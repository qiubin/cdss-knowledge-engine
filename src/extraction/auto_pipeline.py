"""Universal guideline → knowledge graph + rules pipeline.

One upload drives the whole flow: LLM extraction, automatic terminology
resolution (standard code or LOCAL code), graph import, rule generation,
treatment plans and optional automatic activation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
import re
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional

from ..llm import LLMError, chat_json
from .concept_mapping import CARDIAC_FINDINGS
from ..models.rule import Rule
from ..clinical_intent import normalize_clinical_intent
from ..review.quality import coverage_report, locate_single_page_quote, normalized
from ..terminology.labels import default_labels
from .terminology_resolver import (
    UNIVERSAL_TYPES,
    UniversalTermResolver,
    normalize_type,
)
from ..terminology.repository import TerminologyCandidateRepository

AUTO_TEMPLATE_ID = "UNIVERSAL_GUIDELINE_V1"
DEFAULT_AUTO_GRAPH_PREFIX = "KG-AUTO-"
DEFAULT_ACTIVE_CONFIDENCE = 0.75

CONDITION_TYPES = {
    "group", "evidence", "lab_concept", "exam_finding", "symptom",
    "vital", "demographic", "past_history", "medication", "observation",
    "fact", "attribute",
}

RULE_TYPE_ALIASES = {
    "诊断": "diagnosis", "diagnosis_rule": "diagnosis",
    "治疗": "treatment", "treatment_rule": "treatment",
    "禁忌": "contraindication", "contraindication_rule": "contraindication",
    "监测": "monitoring", "monitoring_rule": "monitoring",
}


class _CompilerError(LLMError):
    """The source statements were extracted, but compilation failed."""


def _can_split(error: Exception) -> bool:
    return isinstance(error, json.JSONDecodeError) or any(
        token in str(error) for token in ("JSON", "截断", "Expecting", "超时", "timeout", "Timeout")
    )


def _clamp(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _chunk_groups(chunks: List[Dict[str, Any]], max_chars: int, max_chunks: int) -> List[List[Dict[str, Any]]]:
    max_chunks_per_group = max(1, int(max_chunks))
    groups: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    size = 0
    for chunk in chunks:
        length = len(chunk.get("text", ""))
        if current and (
            size + length > max_chars
            or len(current) >= max_chunks_per_group
        ):
            groups.append(current)
            current, size = [], 0
        current.append(chunk)
        size += length
    if current:
        groups.append(current)
    return groups


def _auto_prompt(group: List[Dict[str, Any]], doc_title: str) -> List[Dict[str, str]]:
    payload = [
        {
            "chunk_id": chunk["chunk_id"],
            "section": chunk.get("section", ""),
            "page_start": chunk.get("page_start"),
            "page_end": chunk.get("page_end"),
            "line_start": chunk.get("line_start"),
            "line_end": chunk.get("line_end"),
            "text": chunk.get("text", ""),
            "tables": chunk.get("tables") or [],
        }
        for chunk in group
    ]
    system = (
        "你是临床指南知识工程引擎。目标是把任意专科指南转换成可执行的临床知识图谱与规则。"
        "使用开放临床本体，不受预置疾病/症状词典限制。"
        "必须只输出一个 JSON 对象，包含 entities、relations、rules、treatment_plans、standards。"
        "实体类型只能是：disease, symptom, sign, lab, exam, diagnostic_evidence, pathology, "
        "biomarker, molecular_test, stage, histology, risk_factor, medication, intervention, "
        "treatment_plan, contraindication, monitoring_requirement, "
        "order_set, order_item, care_pathway, pathway_phase。"
        "每个实体必须有 name、entity_type、chunk_id、page、line_start、line_end、quote、confidence。"
        "疾病实体的 quote 必须逐字引用对应片段，且包含病名或该病的原文别名。"
        "不从文档标题字段推断疾病。"
        "实体 name 必须是完整临床术语（中文全称），缩写或标准码只写入 code 字段，"
        "不得用 CTN、MYO、SYM_FATIGUE 这类缩写代替完整名称。"
        "编码 code 只有在确定时填写；不确定时填 null，不得编造标准编码。"
        "疾病编码优先使用 ICD-10；药品可用 ATC；检验可用 LOINC；无法确定时留空。"
        "关系使用 source_name/source_type/target_name/target_type/relation_code/chunk_id；"
        "医嘱与路径关系码固定为：TREATMENT_PLAN_MATERIALIZED_AS（治疗方案→医嘱套餐）、"
        "ORDER_SET_INCLUDES_ITEM（医嘱套餐→医嘱条目）、"
        "DISEASE_HAS_CARE_PATHWAY（疾病→诊疗路径）、CARE_PATHWAY_HAS_PHASE（诊疗路径→路径阶段）、"
        "PHASE_USES_ORDER_SET（路径阶段→医嘱套餐）。"
        "规则使用 Rule 结构：rule_id、rule_type(diagnosis/treatment/contraindication/monitoring)、"
        "clinical_intent(suspected_diagnosis/diagnosis_confirmation/differential_diagnosis/"
        "diagnosis_exclusion/risk_stratification/severity_assessment/treatment_recommendation/"
        "treatment_selection/contraindication_check/dose_adjustment/monitoring_plan/follow_up_plan)、"
        "disease_name、disease_code、conditions、conclusion、source、confidence。"
        "用途必须按指南原意选择：疑似/初筛/诊断线索用 suspected_diagnosis，明确诊断标准用 diagnosis_confirmation，"
        "治疗推荐用 treatment_recommendation，首选/替代/升级换药用 treatment_selection，且不得把疑似诊断标成确诊。"
        "condition 类型只能是：group, lab_concept, exam_finding, symptom, vital, demographic, "
        "past_history, medication, observation, attribute, evidence, fact。"
        "lab_concept 使用 lab_code + concept_code（ELEVATED/DECREASED/ABOVE_ULN_1/POSITIVE/NEGATIVE）；"
        "exam_finding 使用 exam_code + finding_code；symptom 使用 symptom_code；"
        "attribute 使用 attribute_code + operator + attribute_value（组织学、分期、分级、分子标志物）。"
        "西医诊疗建议须在clinical_context.western_recommendation输出结构：schema_version=1、"
        "diagnosis_stage(suspected/confirmed/independent)、clinical_question、setting、combination(single/alternatives/combined/sequential)、"
        "selection_group、scope_confirmed=false、actions数组。每个action包含code/name、kind(medication/intervention/exam/lab/differential/monitoring/referral/care)、"
        "verb(start/continue/adjust/switch/add/stop/avoid/assess/order/monitor)、conditions条件树、contraindications条件树数组、"
        "required_data[{path:患者字段路径,label:资料名称,purpose:selection或dose,expected_value:如原文要求核实则指定}]、"
        "regimen{dose,route,frequency,course,titration}、monitoring字符串数组、source完整原文定位。"
        "仅录入逐字有据的用法，不得以常识补剂量或药品；类别不自动替换成具体药物。鉴别、检查和手术评估单独成条。"
        "急症处理可独立于疾病确诊，但须有明确触发条件。替代方案不能合成联合，疗效不足升级须写前序治疗条件。"
        "治疗规则必须给出 treatment_plan_id、treatment_name、treatment_items（code/name/entity_type）；"
        "若该章节描述了医嘱套餐，conclusion 需附 order_set_ids（同章节 order_set 实体的 code）。"
        "指南中的医嘱套餐/医嘱组合抽为 order_set 实体，其包含的单条医嘱（药品、检验、检查、"
        "治疗操作、护理、宣教、随访）抽为 order_item 实体；"
        "order_item 用 attributes.order_class 标注类别（药品/检验/检查/治疗/手术/护理/宣教/随访），"
        "用 attributes.required 标注 required/optional，剂量与频次写入 attributes.dose_rule（保留指南原文数值）。"
        "指南中按病程划分的阶段（如急诊期、急性期、恢复期、维持期、随访期）抽为 pathway_phase 实体，"
        "用 attributes.sequence 标注顺序（从 1 开始），attributes.entry 写进入条件，"
        "attributes.exit 写转段/结束条件；全周期路径本身抽为 care_pathway 实体。"
        "每条实体和规则必须带原文页码、行号和引文；不得遗漏溯源。"
        "先识别每条完整临床陈述，再从该陈述展开实体、关系与规则。"
        "区分背景、致病因素、诊断依据、治疗建议、禁忌和监测；同段出现不等于推荐关系。"
        "每条规则必须携带clinical_context对象，字段为population（适用人群）、subtype（分型）、"
        "phase（阶段）、exceptions（例外列表）、recommendation_strength（推荐等级）、"
        "evidence_grade（证据等级）、missing_information（缺失信息列表）。"
        "未知内容留空并列入missing_information，不得推测剂量、阈值、且或逻辑和禁忌。"
        "原文限定条件必须写入conditions，不能只写在说明中。"
        "quote必须逐字来自所标页行，保留完整推荐及限定语；缺少出处时留空，不得回填其他段落。"
        "指南明确给出检查或检验数值阈值时，另输出standards数组，每项包含item_type(exam/lab)、"
        "exam_code/finding_code或lab_code、项目及发现名称、interpretation_type=numeric、operator、"
        "threshold_value、threshold_upper(如有)、threshold_unit、context_age_min/max、context_gender、method、"
        "clinical_context(人群、例外和适用条件)、source(chunk_id、page_start/end、line_start/end、quote)。"
        "禁止从常识补造阈值、单位或适用人群；缺失内容留空待审核。"
        "关联规则叶节点同时携带parameter.interpretation数值标准，以便绑定同一审核流程。"
        "规则条件优先使用同一章节内的证据，不要把无关章节的检查/症状拼进同一规则。"
    )
    user = {
        "doc_title": doc_title,
        "output_schema": {
            "entities": [{
                "entity_type": "disease", "name": "肺癌", "code": None,
                "aliases": ["非小细胞肺癌"], "standard_system": "ICD-10",
                "attributes": {}, "chunk_id": "c1", "page": 1,
                "line_start": 1, "line_end": 2, "quote": "原文", "confidence": 0.9,
            }],
            "relations": [{
                "source_name": "肺癌", "source_type": "disease",
                "target_name": "胸部CT", "target_type": "exam",
                "relation_code": "DISEASE_HAS_EXAM", "chunk_id": "c1",
                "page": 1, "line_start": 1, "line_end": 2, "quote": "原文", "confidence": 0.8,
            }],
            "rules": [{
                "rule_id": "R1", "rule_type": "diagnosis", "disease_name": "肺癌",
                "conditions": {"type": "group", "operator": "AND", "children": []},
                "conclusion": {"diagnosis": "肺癌", "confidence": "medium", "message": "..."},
                "source": {"chunk_id": "c1", "page_start": 1, "line_start": 1, "line_end": 2, "quote": "..."},
                "confidence": 0.85,
            }],
            "standards": [{
                "item_type": "exam", "exam_code": "已映射的检查编码", "finding_code": "测量指标编码",
                "exam_name": "检查项目名称", "finding_name": "测量指标名称", "interpretation_type": "numeric",
                "operator": ">=", "threshold_value": None, "threshold_unit": "原文单位",
                "context_age_min": None, "context_age_max": None, "context_gender": None,
                "clinical_context": {"population": "原文人群", "exceptions": [], "missing_information": []},
                "source": {"chunk_id": "c1", "page_start": 1, "line_start": 1, "line_end": 2, "quote": "包含阈值及限定条件的逐字原文"},
            }],
            "treatment_plans": [{
                "plan_id": "TP-1", "name": "肺癌一线治疗方案", "disease_name": "肺癌",
                "items": [{"code": None, "name": "培美曲塞", "entity_type": "medication"}],
                "chunk_id": "c1", "page": 1, "line_start": 1, "line_end": 2, "quote": "原文",
            }],
            "order_sets_example": {
                "entities": [
                    {"entity_type": "order_set", "name": "STEMI再灌注医嘱套餐", "code": None,
                     "attributes": {"priority": "preferred"}, "chunk_id": "c2",
                     "page": 3, "line_start": 10, "line_end": 20, "quote": "原文", "confidence": 0.9},
                    {"entity_type": "order_item", "name": "阿司匹林负荷剂量", "code": None,
                     "attributes": {"order_class": "药品", "required": "required",
                                    "dose_rule": "300mg 嚼服"}, "chunk_id": "c2",
                     "page": 3, "line_start": 12, "line_end": 13, "quote": "原文", "confidence": 0.9},
                    {"entity_type": "pathway_phase", "name": "STEMI急性期", "code": None,
                     "attributes": {"sequence": 2, "entry": "确诊STEMI",
                                    "exit": "再灌注完成"}, "chunk_id": "c2",
                     "page": 3, "line_start": 8, "line_end": 24, "quote": "原文", "confidence": 0.85},
                ],
                "relations": [
                    {"source_name": "STEMI再灌注医嘱套餐", "source_type": "order_set",
                     "target_name": "阿司匹林负荷剂量", "target_type": "order_item",
                     "relation_code": "ORDER_SET_INCLUDES_ITEM", "chunk_id": "c2"},
                    {"source_name": "STEMI急性期", "source_type": "pathway_phase",
                     "target_name": "STEMI再灌注医嘱套餐", "target_type": "order_set",
                     "relation_code": "PHASE_USES_ORDER_SET", "chunk_id": "c2"},
                ],
            },
        },
        "chunks": payload,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def _statement_prompt(group, doc_title):
    return [
        {"role": "system", "content": (
            "阶段一：抽取完整临床陈述和正文中的明确病名，不生成编码、图谱或可执行规则。"
            "输出JSON对象，包含 statements 和 disease_mentions 两个数组。"
            "disease_mentions 每项含 name 和 source（chunk_id/page_start/line_start/line_end/quote）。"
            "即使没有临床陈述也要检查正文中的病名。中医病名与证候须区分，证候不列为疾病。"
            "仅使用正文中的逐字病名及原句，不从文件名、文档标题字段或章节名推断。"
            "每条陈述字段：statement_id、disease_name、scene、population、subtype、phase、"
            "criteria（原文判断逻辑）、recommendation、exceptions（列表）、recommendation_strength、"
            "evidence_grade、missing_information（列表）、source（chunk_id/page_start/page_end/line_start/line_end/quote）。"
            "区分背景病因、诊断标准、治疗推荐、禁忌与监测；不能将导致疾病的治疗误标为推荐治疗。"
            "一条陈述只对应一个适用人群与临床目的，保留否定、例外、阈值、时间和限定语。"
            "scene使用suspected_diagnosis/diagnosis_confirmation/treatment_recommendation/treatment_selection/"
            "contraindication_check/monitoring_plan等临床用途。quote逐字引用原文，不补造医学判断，未知留空并列待补信息。"
            "没有临床建议的背景段可以返回空列表。")},
        {"role": "user", "content": json.dumps({"doc_title": doc_title, "chunks": group}, ensure_ascii=False)},
    ]


def _entity_map(entities: List[Dict[str, Any]]) -> tuple[Dict[str, str], Dict[str, str]]:
    by_name: Dict[str, str] = {}
    by_code: Dict[str, str] = {}
    for entity in entities:
        code = str(entity.get("code") or "")
        name = str(entity.get("name") or "")
        if code:
            by_code[code] = code
            if name:
                by_name.setdefault(name, code)
            for alias in entity.get("aliases") or []:
                by_name.setdefault(str(alias), code)
    return by_name, by_code


def _resolve_code(
    name: str,
    entity_type: str,
    by_name: Dict[str, str],
    by_code: Dict[str, str],
    explicit: str = "",
) -> str:
    if explicit and explicit in by_code:
        return explicit
    if explicit and by_name.get(explicit):
        return by_name[explicit]
    if name and by_name.get(name):
        return by_name[name]
    return explicit or ""


def _normalize_condition(
    condition: Any,
    by_name: Dict[str, str],
    by_code: Dict[str, str],
) -> Any:
    if not isinstance(condition, dict):
        return condition
    children = condition.get("children")
    if isinstance(children, list):
        condition = {**condition, "children": [
            _normalize_condition(child, by_name, by_code) for child in children
        ]}
    condition_type = str(condition.get("type") or "")
    if condition_type == "lab_concept":
        lab = str(condition.get("lab_code") or "")
        condition["lab_code"] = _resolve_code(lab, "lab", by_name, by_code, lab)
        concept = str(condition.get("concept_code") or "")
        concept_map = {
            "升高": "ELEVATED", "增高": "ELEVATED", "上升": "ELEVATED",
            "超过正常上限": "ABOVE_ULN_1", "高于正常上限": "ABOVE_ULN_1",
            "降低": "DECREASED", "下降": "DECREASED", "减低": "DECREASED",
            "阳性": "POSITIVE", "阴性": "NEGATIVE",
        }
        condition["concept_code"] = concept_map.get(concept, concept)
    elif condition_type == "exam_finding":
        exam = str(condition.get("exam_code") or "")
        condition["exam_code"] = _resolve_code(exam, "exam", by_name, by_code, exam)
    elif condition_type == "symptom":
        symptom = str(condition.get("symptom_code") or "")
        condition["symptom_code"] = _resolve_code(symptom, "symptom", by_name, by_code, symptom)
    elif condition_type == "observation":
        code = str(condition.get("code") or "")
        condition["code"] = _resolve_code(code, str(condition.get("observation_type") or ""), by_name, by_code, code)
    return condition


def _normalize_rules(
    raw_rules: List[Any],
    entities: List[Dict[str, Any]],
    chunks: List[Dict[str, Any]],
    resolver: UniversalTermResolver,
    doc_id: str,
    doc_title: str,
    graph_filename: str,
    auto_activate: bool,
) -> tuple[List[Dict[str, Any]], List[str]]:
    by_name, by_code = _entity_map(entities)
    chunk_map = {chunk["chunk_id"]: chunk for chunk in chunks}
    labels = default_labels()
    rules: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for index, raw in enumerate(raw_rules or [], start=1):
        if not isinstance(raw, dict):
            continue
        rule_type = RULE_TYPE_ALIASES.get(str(raw.get("rule_type") or ""), str(raw.get("rule_type") or "diagnosis"))
        if rule_type not in {"diagnosis", "treatment", "contraindication", "monitoring"}:
            rule_type = "diagnosis"
        disease_name = str(raw.get("disease_name") or raw.get("anchor_name") or "")
        disease_code = _resolve_code(
            disease_name, "disease", by_name, by_code,
            str(raw.get("disease_code") or raw.get("anchor_code") or ""),
        )
        if not disease_code:
            resolved = resolver.resolve("disease", disease_name or doc_title, doc_id=doc_id)
            disease_code = resolved["code"]
            disease_name = resolved["name"]
            entities.append({
                **resolved,
                "chunk_id": chunks[0]["chunk_id"] if chunks else "",
                "page": chunks[0].get("page_start", 1) if chunks else 1,
                "line_start": chunks[0].get("line_start", 1) if chunks else 1,
                "line_end": chunks[0].get("line_end", 1) if chunks else 1,
                "quote": disease_name,
                "confidence": 0.7,
            })
            by_name[disease_name] = disease_code
            by_code[disease_code] = disease_code

        conditions = raw.get("conditions")
        if not isinstance(conditions, dict):
            conditions = {"type": "group", "operator": "AND", "children": []}
        conditions = _normalize_condition(conditions, by_name, by_code)
        labels.annotate(conditions)
        source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
        chunk = chunk_map.get(str(source.get("chunk_id") or "")) or {}
        source = {
            "guideline_id": doc_id,
            "guideline_title": doc_title,
            "chunk_id": str(source.get("chunk_id") or chunk.get("chunk_id", "")),
            "section": str(source.get("section") or chunk.get("section", "")),
            "page_start": source.get("page_start") or chunk.get("page_start"),
            "page_end": source.get("page_end") or chunk.get("page_end"),
            "line_start": source.get("line_start") or chunk.get("line_start"),
            "line_end": source.get("line_end") or chunk.get("line_end"),
            "quote": str(source.get("quote") or ""),
        }
        conclusion = raw.get("conclusion") if isinstance(raw.get("conclusion"), dict) else {}
        conclusion = {
            **conclusion,
            "diagnosis": str(conclusion.get("diagnosis") or disease_code),
            "confidence": str(conclusion.get("confidence") or "medium"),
            "message": str(conclusion.get("message") or raw.get("message") or ""),
        }
        if rule_type == "treatment" and not conclusion.get("treatment_plan_id"):
            conclusion.update({
                "conclusion_type": "treatment",
                "treatment_plan_id": f"TP-{doc_id}-{disease_code}-{index:03d}",
                "treatment_name": str(conclusion.get("treatment_name") or f"{disease_name or disease_code}治疗方案"),
                "treatment_items": list(conclusion.get("treatment_items") or []),
            })
        confidence = 0.75
        for value in (raw.get("confidence"), conclusion.get("confidence")):
            try:
                confidence = float(value)
                break
            except (TypeError, ValueError):
                continue
        status = "draft"  # Model confidence never authorizes publication.
        rule = {
            **raw,
            "rule_id": str(raw.get("rule_id") or f"{doc_id}-AUTO-{index:03d}"),
            "version": 1,
            "domain": "western",
            "anchor_type": "disease",
            "rule_type": rule_type,
            "name": str(raw.get("name") or f"自动生成: {disease_name or disease_code}{rule_type}规则"),
            "disease_code": disease_code,
            "disease_name": disease_name or disease_code,
            "priority": _clamp(
                raw.get("priority"), 0, 100,
                50 if rule_type == "diagnosis" else 40,
            ),
            "status": status,
            "graph_filename": graph_filename,
            "conditions": conditions,
            "clinical_context": raw.get("clinical_context") if isinstance(raw.get("clinical_context"), dict) else {
                "missing_information": ["适用人群、分型和阶段尚未结构化，请对照原文核对"]},
            "condition_text": labels.summarize(conditions),
            "conclusion": conclusion,
            "source": source,
            "tags": list(dict.fromkeys([*(raw.get("tags") or []), "auto-generated", doc_id])),
        }
        recommendation = rule["clinical_context"].get("western_recommendation")
        if recommendation:
            recommendation["scope_confirmed"] = False
            for action in recommendation.get("actions") or []:
                action.setdefault("source", source)
            conclusion["treatment_items"] = [{**action, "entity_type": action.get("kind")} for action in recommendation.get("actions") or []]
            rule["diagnosis_prerequisite"] = recommendation.get("diagnosis_stage") if recommendation.get("diagnosis_stage") in {"confirmed", "suspected"} else None
        rule["clinical_intent"] = normalize_clinical_intent(rule)
        try:
            Rule(**rule)
            rules.append(rule)
        except Exception as error:
            warnings.append(f"候选规则校验失败: {str(error)[:140]}")
    return rules, warnings


def _package_record(
    doc_id: str,
    doc_title: str,
    graph_filename: str,
    entities: List[Dict[str, Any]],
    relations: List[Dict[str, Any]],
    rules: List[Dict[str, Any]],
    chunks: List[Dict[str, Any]],
    target_code: str,
    metadata: Dict[str, Any],
    package_dir: Optional[Path] = None,
    import_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from ..api.routes.guide_packages import _build_checks

    package = {
        "package_id": f"PKG-{doc_id}",
        "doc_id": doc_id,
        "doc_title": doc_title,
        "domain": "western",
        "template_id": AUTO_TEMPLATE_ID,
        "graph_filename": graph_filename,
        "target_disease_code": target_code,
        "target_anchor_type": "disease",
        "status": "ready_for_test",
        "entities": entities,
        "chunks": chunks,
        "suggested_relations": relations,
        "suggested_rules": rules,
        "extraction": metadata,
        "checks": [],
        "import_result": import_result,
        "created_at": metadata.get("created_at", ""),
        "updated_at": metadata.get("created_at", ""),
    }
    package["checks"] = _build_checks(package)
    if any(item.get("status") == "failed" for item in package["checks"]):
        # Auto mode imports immediately; a failed check means "imported but
        # needs correction", not "not imported yet".
        package["status"] = "imported" if import_result else "generated"
    sync = ((import_result or {}).get("rules") or {}).get("sync_check") or {}
    if import_result and sync.get("inconsistent_count"):
        package["status"] = "imported"
    if package_dir is not None:
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / f"{package['package_id']}.json").write_text(
            json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        from ..api.routes.guide_packages import _save_package
        _save_package(package)
    return package


async def run_auto_pipeline(
    chunks: List[Dict[str, Any]],
    doc_id: str,
    doc_title: str,
    config: Dict[str, Any],
    *,
    graph_filename: str = "",
    auto_activate: bool = False,
    llm_call: Optional[Callable[..., Any]] = None,
    data_root: Optional[Path] = None,
    auto_import_disease: bool = False,
    controller: Any = None,
    require_import_quality: bool = False,
) -> Dict[str, Any]:
    """Run the complete universal auto ingestion pipeline."""
    root = Path(data_root) if data_root else Path(__file__).resolve().parents[2] / "data"
    resolver = UniversalTermResolver(
        auto_import_disease=auto_import_disease,
        candidate_repository=TerminologyCandidateRepository(
            root / "terminology" / "candidates.json"
        ),
    )
    settings = dict(config.get("llm", {}))
    started = perf_counter()
    # 编译批默认 4 条/批（曾为 2）：S2 调用数与陈述总数成正比，批越小调用越多，
    # 6MB 指南 270+ 次串行 S2 调用是解析耗时数小时的主因之一。
    batch_size = _clamp(settings.get("western_compile_batch_size"), 1, 8, 4)
    batch_chars = _clamp(settings.get("western_compile_batch_chars"), 1, 24000, 8000)
    stages = {
        stage: dict(calls=0, cache_hits=0, request_seconds=0.0, retry_wait_seconds=0.0,
                    input_tokens=0, output_tokens=0, usage_responses=0)
        for stage in ("extraction", "compilation")
    }
    performance = {"model_stages": stages, "compile_batch_size": batch_size,
                   "compile_batch_chars": batch_chars}

    async def request_model(settings, messages, metrics):
        if llm_call is not None:
            return await llm_call(settings, messages)
        payload, meta = await chat_json(settings, messages, with_meta=True)
        usage = meta.get("usage")
        if isinstance(usage, dict):
            metrics["usage_responses"] += 1
            for name, alias in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens")):
                amount = usage.get(name, usage.get(alias))
                if isinstance(amount, int):
                    metrics[name] += amount
        return payload
    checkpoint_dir = root / "extraction" / "checkpoints" / hashlib.sha256(doc_id.encode()).hexdigest()

    fatal_error = None
    compiled_keys = ("entities", "relations", "rules", "treatment_plans", "standards")

    def valid_response(payload, stage):
        if not isinstance(payload, dict):
            return False
        if stage == "extraction":
            return isinstance(payload.get("statements"), list) or isinstance(payload.get("entities"), list)
        return isinstance(payload.get("entities"), list) and all(
            isinstance(payload.get(key) or [], list) for key in compiled_keys
        )

    async def call(settings, messages, stage):
        nonlocal fatal_error
        metrics = stages[stage]
        if fatal_error is not None:
            raise fatal_error
        # Cache each completed stage, so a compiler timeout does not discard
        # the clinical statements, and retries can resume after a restart.
        identity = {key: settings.get(key) for key in (
            "provider", "api_format", "base_url", "model", "temperature", "max_output_tokens")}
        key = hashlib.sha256(json.dumps([identity, messages], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        path = checkpoint_dir / f"{key}.json"
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if valid_response(cached, stage):
                    metrics["cache_hits"] += 1
                    return cached
            except (ValueError, OSError):
                pass
        for attempt in range(4):
            try:
                metrics["calls"] += 1
                request_started = perf_counter()
                try:
                    payload = await asyncio.wait_for(request_model(settings, messages, metrics),
                        timeout=float(settings.get("timeout_seconds", 120)))
                except asyncio.TimeoutError as error:
                    raise LLMError("模型阶段请求总耗时超时，请拆分片段重试") from error
                finally:
                    metrics["request_seconds"] += perf_counter() - request_started
                break
            except LLMError as error:
                if "HTTP 429" not in str(error):
                    raise
                if attempt == 3:
                    fatal_error = LLMError("模型服务持续限流（HTTP 429），已停止发送后续片段；成功阶段已保存，请稍后重试。" + str(error))
                    raise fatal_error from error
                delay = min(60, 15 * 2 ** attempt)
                if controller is not None:
                    controller.log(f"模型服务限流，等待 {delay} 秒后重试；成功阶段已保存", "warn")
                    await controller.checkpoint(stage="模型服务限流，等待重试")
                waiting = perf_counter()
                try:
                    await asyncio.sleep(delay)
                finally:
                    metrics["retry_wait_seconds"] += perf_counter() - waiting
        if valid_response(payload, stage):
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            temporary = checkpoint_dir / f"{uuid.uuid4().hex}.tmp"
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        elif stage == "compilation":
            raise LLMError("阶段二 JSON 缺少 entities 数组或包含无效字段")
        return payload

    from .chunk_limits import bounded_chunks
    from .tail_sections import split_clinical_chunks
    # 片段大小硬顶 6000（曾为 1200）：1200 字会把 10 万字指南切成上百个片段组，
    # 组数 × 每组 1 次阶段一调用是调用数爆炸的根源；阶段二按陈述数分批与片段
    # 大小无关，且超长输出由 _can_split 拆分重试兜底。
    max_chars = _clamp(settings.get("max_chunk_chars"), 800, 6000, 6000)
    performance["chunk_chars"] = max_chars
    # Bibliography is not a source of guideline clinical recommendations.
    clinical, excluded = split_clinical_chunks(chunks)
    chunks[:] = bounded_chunks(clinical, max_chars) + excluded
    excluded_chunks = {chunk["chunk_id"] for chunk in excluded}
    groups = _chunk_groups([chunk for chunk in chunks if chunk["chunk_id"] not in excluded_chunks], max_chars, 1)
    raw_entities: List[Dict[str, Any]] = []
    raw_relations: List[Dict[str, Any]] = []
    raw_rules: List[Dict[str, Any]] = []
    raw_plans: List[Dict[str, Any]] = []
    raw_standards: List[Dict[str, Any]] = []
    warnings: List[str] = []
    if auto_activate:
        warnings.append("自动启用参数仅为兼容保留；所有候选仍须人工审核后发布")
    succeeded = 0
    completed_chunks = set()
    scheduled_chunks = {chunk["chunk_id"] for group in groups for chunk in group}
    statement_records = []
    disease_mentions = []

    def merge_payload(payload: Dict[str, Any]) -> None:
        raw_entities.extend(payload.get("entities") or [])
        raw_relations.extend(payload.get("relations") or [])
        raw_rules.extend(payload.get("rules") or [])
        raw_plans.extend(payload.get("treatment_plans") or [])
        raw_standards.extend(payload.get("standards") or [])

    async def call_group(chunk_group: List[Dict[str, Any]]) -> Dict[str, Any]:
        extracted = await call(settings, _statement_prompt(chunk_group, doc_title), "extraction")
        chunk_ids = {chunk["chunk_id"] for chunk in chunk_group}
        statements = extracted.get("statements")
        if not isinstance(statements, list):
            if not isinstance(extracted.get("entities"), list):
                raise LLMError("阶段一 JSON 缺少 statements 数组")
            # Retain older provider output as review-only candidates, never
            # silently certify it as a successfully structured statement.
            warnings.append(f"片段 {chunk_group[0]['chunk_id']} 未返回临床陈述结构，仅保留待审核候选")
            for rule in extracted.get("rules") or []:
                context = rule.setdefault("clinical_context", {})
                context.setdefault("missing_information", []).append("临床陈述结构未生成，须重新核对人群、逻辑与建议")
            return extracted
        def record_disease_mentions():
            mentions = extracted.get("disease_mentions")
            for item in [*(mentions if isinstance(mentions, list) else []), *statements]:
                if not isinstance(item, dict):
                    continue
                source = item.get("source")
                if not isinstance(source, dict) or source.get("chunk_id") not in chunk_ids:
                    continue
                disease_mentions.append({
                    **source,
                    "entity_type": "disease",
                    "name": item.get("disease_name") or item.get("name"),
                    "page": source.get("page_start"),
                })

        if not statements:
            record_disease_mentions()
            return {}
        indexed = {}
        for index, statement in enumerate(statements):
            if not isinstance(statement, dict):
                continue
            statement = dict(statement)
            statement["statement_id"] = f"{doc_id}:{chunk_group[0]['chunk_id']}:S{index + 1}"
            indexed[statement["statement_id"]] = statement
        # A small source table can yield dozens of statements. Bound the
        # compiler output by statements as well as by source characters.
        records = list(indexed.values())
        compiled = {key: [] for key in compiled_keys}

        async def compile_batch(start, end):
            messages = _auto_prompt(chunk_group, doc_title)
            payload = json.loads(messages[1]["content"])
            payload["clinical_statements"] = records[start:end]
            payload["instruction"] = "阶段二：仅从这些临床陈述生成实体、关系、规则。每条规则必须携带clinical_statement_id，条件和建议不能超出该陈述；保留限定条件，不自行猜测组合逻辑。"
            messages[1]["content"] = json.dumps(payload, ensure_ascii=False)
            if controller is not None:
                await controller.checkpoint(stage=f"临床陈述编译（{end}/{len(records)}）")
            try:
                batch = await call(settings, messages, "compilation")
            except (LLMError, ValueError) as error:
                if error is fatal_error:
                    raise
                if end - start > 1 and _can_split(error):
                    middle = (start + end) // 2
                    await compile_batch(start, middle)
                    await compile_batch(middle, end)
                    return
                raise _CompilerError(str(error)) from error
            for key in compiled:
                compiled[key].extend(batch.get(key) or [])

        start = 0
        while start < len(records):
            end = start + 1
            size = len(json.dumps(records[start], ensure_ascii=False))
            while end < min(start + batch_size, len(records)):
                next_size = len(json.dumps(records[end], ensure_ascii=False)) + 2
                if size + next_size > batch_chars:
                    break
                size += next_size
                end += 1
            await compile_batch(start, end)
            start = end
        for rule in compiled.get("rules") or []:
            statement = indexed.get(rule.get("clinical_statement_id"))
            if not statement:
                rule.setdefault("clinical_context", {}).setdefault("missing_information", []).append("规则未绑定有效临床陈述，请重新核对")
                continue
            western = (rule.get("clinical_context") or {}).get("western_recommendation")
            rule["clinical_context"] = {key: statement.get(key) for key in (
                "population", "subtype", "phase", "exceptions", "recommendation_strength", "evidence_grade", "missing_information")}
            if western:
                western["scope_confirmed"] = False
                rule["clinical_context"]["western_recommendation"] = western
            rule["source"] = statement.get("source") or {}
            if rule["source"].get("chunk_id") not in chunk_ids:
                rule["clinical_context"]["missing_information"] = [*(rule["clinical_context"].get("missing_information") or []), "陈述原文不在当前片段中"]
            rule["clinical_intent"] = statement.get("scene") or rule.get("clinical_intent")
            treatment_scenes = {"treatment_recommendation", "treatment_selection", "dose_adjustment"}
            kind = RULE_TYPE_ALIASES.get(str(rule.get("rule_type") or ""), rule.get("rule_type"))
            if (kind == "treatment" and statement.get("scene") not in treatment_scenes) or (
                    kind == "diagnosis" and statement.get("scene") in treatment_scenes):
                rule["clinical_context"]["missing_information"] = [*(rule["clinical_context"].get("missing_information") or []), "临床陈述场景与规则类型不一致"]
            cited = next((chunk for chunk in chunk_group if chunk["chunk_id"] == rule["source"].get("chunk_id")), {})
            if not normalized(rule["source"].get("quote")) or normalized(rule["source"].get("quote")) not in normalized(cited.get("text")):
                rule["clinical_context"]["missing_information"] = [*(rule["clinical_context"].get("missing_information") or []), "临床陈述引文不匹配当前片段"]
        statement_records.extend(indexed.values())
        record_disease_mentions()
        return compiled

    total_groups = len(groups)
    async def process_group(index, group):
        nonlocal succeeded
        if controller is not None:
            await controller.checkpoint(
                stage=f"自动抽取（第 {index + 1}/{total_groups} 组）",
                done=index, total=total_groups,
            )
        try:
            payload = await call_group(group)
        except Exception as error:
            message = str(error)
            if "模型服务持续限流" in message:
                raise
            split_payloads: List[Dict[str, Any]] = []
            if not isinstance(error, _CompilerError) and _can_split(error):
                # Long groups can exceed the model output budget and return
                # truncated JSON.  Retry each chunk separately so one oversized
                # segment does not discard the whole group.
                retry_chunks = group
                if len(group) == 1 and len(group[0].get("text", "")) > 600:
                    retry_chunks = bounded_chunks(group, max(400, len(group[0]["text"]) // 2))
                    parent = group[0]["chunk_id"]
                    position = next(i for i, chunk in enumerate(chunks) if chunk["chunk_id"] == parent)
                    chunks[position:position + 1] = retry_chunks
                    scheduled_chunks.discard(parent)
                    scheduled_chunks.update(chunk["chunk_id"] for chunk in retry_chunks)
                for chunk in retry_chunks:
                    try:
                        split_payloads.append(await call_group([chunk]))
                        completed_chunks.add(chunk["chunk_id"])
                    except Exception as split_error:
                        if "模型服务持续限流" in str(split_error):
                            raise
                        warnings.append(f"片段 {chunk['chunk_id']} 单独重试失败: {str(split_error)[:180]}")
                        continue
            if split_payloads:
                failed = len(retry_chunks) - len(split_payloads)
                warnings.append(
                    f"片段组 {group[0]['chunk_id']} 请求失败或输出异常，"
                    f"已按单片段重试成功 {len(split_payloads)}/{len(retry_chunks)} 个"
                    + (f"，仍失败 {failed} 个" if failed else "")
                )
                for split_payload in split_payloads:
                    merge_payload(split_payload)
                succeeded += 1
                return
            warnings.append(f"片段 {group[0]['chunk_id']} LLM 失败: {message[:180]}")
            return
        merge_payload(payload)
        completed_chunks.update(chunk["chunk_id"] for chunk in group)
        succeeded += 1

    # 并发默认 4（曾为 1）：片段组之间无依赖，串行时每次调用 40-60 秒的
    # 供应商时延直接累加，6MB 指南需 400+ 次调用即 4 小时以上。
    concurrency = max(1, min(8, int(settings.get("extraction_concurrency", 4))))
    performance["concurrency"] = concurrency
    semaphore = asyncio.Semaphore(concurrency)
    async def bounded(index, group):
        async with semaphore:
            await process_group(index, group)
    model_started = perf_counter()
    tasks = [asyncio.create_task(bounded(index, group)) for index, group in enumerate(groups)]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        performance["model_wall_seconds"] = perf_counter() - model_started
        if controller is not None:
            controller.log("西医模型阶段指标 " + json.dumps(performance, ensure_ascii=False))
    coverage = coverage_report(chunks, completed_chunks, scheduled_chunks, excluded_chunks)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    report = checkpoint_dir / "coverage.json"
    temporary = report.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps({"doc_id": doc_id, "coverage": coverage, "warnings": warnings, "performance": performance}, ensure_ascii=False), encoding="utf-8")
    temporary.replace(report)
    coverage_summary = (
        f"片段共 {coverage['total']} 个，模型响应 {coverage['processed']} 个，"
        f"失败 {coverage['failed']} 个，未安排 {coverage['not_scheduled']} 个"
    )
    if not succeeded:
        detail = f"。{warnings[-1]}" if warnings else ""
        raise LLMError(f"大模型没有成功解析任何片段。{coverage_summary}{detail}")

    if controller is not None:
        await controller.checkpoint(stage="术语映射与图谱整理")
    mapping_started = perf_counter()

    chunk_map = {chunk["chunk_id"]: chunk for chunk in chunks}
    entities: List[Dict[str, Any]] = []
    seen = set()
    disease_names_by_chunk = set()
    disease_candidate_count = 0
    for index, raw in enumerate([*raw_entities, *disease_mentions]):
        if not isinstance(raw, dict):
            continue
        entity_type = normalize_type(str(raw.get("entity_type") or ""))
        if entity_type == "disease":
            disease_candidate_count += 1
        source = raw.get("source")
        if isinstance(source, dict):
            raw = {**source, **raw}
        chunk = chunk_map.get(str(raw.get("chunk_id") or ""))
        if chunk is None:
            warnings.append("实体引用了未知片段，已跳过该候选，不能用首段补造出处")
            continue
        if not chunk:
            continue
        if entity_type not in UNIVERSAL_TYPES:
            continue
        name = str(raw.get("name") or raw.get("term") or "").strip()
        if not name:
            continue
        disease_key = (chunk["chunk_id"], normalized(name))
        if index >= len(raw_entities) and disease_key in disease_names_by_chunk:
            continue
        entity_quote = str(raw.get("quote") or "")
        if entity_type == "disease":
            # The quote must literally mention the disease (by name or any
            # alias) before the entity may anchor graph relations. A chapter
            # describing a complication (e.g. 心肌肥厚 inside the 线粒体疾病
            # section) must not silently bind the guideline's target disease
            # just because the model named it.
            mentions = [
                name,
                *[str(alias) for alias in (raw.get("aliases") or [])],
            ]
            if not any(
                len(str(mention).strip()) >= 2
                and normalized(mention) in normalized(entity_quote)
                for mention in mentions
            ):
                warnings.append(
                    f"疾病候选「{name}」的引文未出现该病名或别名，已跳过，防止错绑：{entity_quote[:60]}"
                )
                continue
            location = locate_single_page_quote(chunk, entity_quote, raw.get("page") or raw.get("page_start"),
                                         raw.get("line_start"))
            if location is None:
                warnings.append(f"疾病候选「{name}」的引文无法定位到片段 {chunk['chunk_id']} 的唯一页行，已跳过")
                continue
            disease_names_by_chunk.add(disease_key)
        resolved = resolver.resolve(
            entity_type,
            name,
            code=str(raw.get("code") or raw.get("standard_code") or ""),
            aliases=list(raw.get("aliases") or []),
            standard_system=str(raw.get("standard_system") or ""),
            doc_id=doc_id,
            attributes=dict(raw.get("attributes") or {}),
        )
        key = (entity_type, resolved["code"], chunk["chunk_id"])
        if key in seen:
            continue
        seen.add(key)
        page = _clamp(raw.get("page"), chunk.get("page_start", 1), chunk.get("page_end", 1), chunk.get("page_start", 1))
        line_start = _clamp(raw.get("line_start"), chunk.get("line_start", 1), chunk.get("line_end", 1), chunk.get("line_start", 1))
        line_end = _clamp(raw.get("line_end"), line_start, chunk.get("line_end", line_start), line_start)
        if entity_type == "disease":
            page, line_start, line_end = location
        try:
            entity_confidence = float(raw.get("confidence") or 0.7)
        except (TypeError, ValueError):
            entity_confidence = 0.7
        entities.append({
            **resolved,
            "chunk_id": chunk["chunk_id"],
            "page": page,
            "line_start": line_start,
            "line_end": line_end,
            "quote": str(raw.get("quote") or ""),
            "confidence": max(0.0, min(1.0, entity_confidence)),
        })

    by_name, by_code = _entity_map(entities)
    relations: List[Dict[str, Any]] = []
    seen_rel = set()
    for raw in raw_relations:
        if not isinstance(raw, dict):
            continue
        chunk_id = str(raw.get("chunk_id") or "")
        chunk = chunk_map.get(chunk_id) or {}
        if not chunk:
            warnings.append("关系引用了未知片段，已跳过该候选，不能用首段补造出处")
            continue
        source_type = normalize_type(str(raw.get("source_type") or raw.get("source_entity_type") or ""))
        target_type = normalize_type(str(raw.get("target_type") or raw.get("target_entity_type") or ""))
        source_code = _resolve_code(
            str(raw.get("source_name") or ""), source_type, by_name, by_code,
            str(raw.get("source_code") or ""),
        )
        target_code = _resolve_code(
            str(raw.get("target_name") or ""), target_type, by_name, by_code,
            str(raw.get("target_code") or ""),
        )
        if not source_code or not target_code:
            continue
        relation_code = str(raw.get("relation_code") or "")
        if not relation_code:
            if source_type == "disease":
                relation_code = f"DISEASE_HAS_{target_type.upper()}"
            elif source_type == "treatment_plan":
                relation_code = f"TREATMENT_PLAN_INCLUDES_{target_type.upper()}"
            else:
                relation_code = "RELATED_TO"
        key = (source_code, relation_code, target_code, chunk_id)
        if key in seen_rel:
            continue
        seen_rel.add(key)
        relations.append({
            "relation_code": relation_code,
            "relation_name": str(raw.get("relation_name") or relation_code),
            "source_type": source_type,
            "source_entity_type": source_type,
            "source_code": source_code,
            "target_type": target_type,
            "target_entity_type": target_type,
            "target_code": target_code,
            "target_concept_code": "",
            "chunk_id": chunk_id,
            "page": _clamp(raw.get("page"), chunk.get("page_start", 1), chunk.get("page_end", 1), chunk.get("page_start", 1)),
            "line_start": _clamp(raw.get("line_start"), chunk.get("line_start", 1), chunk.get("line_end", 1), chunk.get("line_start", 1)),
            "line_end": _clamp(raw.get("line_end"), raw.get("line_start") or 1, chunk.get("line_end", 1), chunk.get("line_start", 1)),
            "quote": str(raw.get("quote") or ""),
            "confidence": 0.7,
        })

    graph_filename = graph_filename or f"{DEFAULT_AUTO_GRAPH_PREFIX}{doc_id}.json"
    graph_filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", graph_filename)
    if not graph_filename.endswith(".json"):
        graph_filename += ".json"

    # Attach plan data from the LLM plan list to matching treatment rules.
    for raw in raw_rules:
        if not isinstance(raw, dict) or RULE_TYPE_ALIASES.get(str(raw.get("rule_type") or ""), raw.get("rule_type")) != "treatment":
            continue
        disease = str(raw.get("disease_name") or "")
        candidates = [plan for plan in raw_plans if isinstance(plan, dict)
                      and plan.get("disease_name") == disease
                      and ((raw.get("clinical_statement_id") and plan.get("clinical_statement_id") == raw["clinical_statement_id"])
                           or (not raw.get("clinical_statement_id") and plan.get("chunk_id")
                               and plan.get("chunk_id") == (raw.get("source") or {}).get("chunk_id")))]
        plan = candidates[0] if len(candidates) == 1 else None
        if plan:
            conclusion = raw.setdefault("conclusion", {})
            conclusion.setdefault("treatment_plan_id", plan.get("plan_id"))
            conclusion.setdefault("treatment_name", plan.get("name"))
            conclusion.setdefault("treatment_items", plan.get("items") or [])

    rules, rule_warnings = _normalize_rules(
        raw_rules, entities, chunks, resolver, doc_id, doc_title,
        graph_filename, False,
    )
    warnings.extend(rule_warnings)

    # Ensure each treatment plan has a treatment rule.
    existing_plan_ids = {
        str(rule.get("conclusion", {}).get("treatment_plan_id") or "")
        for rule in rules
    }
    for plan in raw_plans:
        if not isinstance(plan, dict):
            continue
        plan_id = str(plan.get("plan_id") or "")
        disease_name = str(plan.get("disease_name") or "")
        if not plan_id or plan_id in existing_plan_ids:
            continue
        disease_code = _resolve_code(disease_name, "disease", by_name, by_code, "")
        if not disease_code:
            continue
        rules.append({
            "rule_id": f"{doc_id}-TREAT-{len(rules)+1:03d}",
            "version": 1,
            "domain": "western",
            "anchor_type": "disease",
            "rule_type": "treatment",
            "clinical_intent": "treatment_recommendation",
            "name": f"自动生成: {disease_name or disease_code}治疗方案",
            "disease_code": disease_code,
            "disease_name": disease_name or disease_code,
            "priority": 40,
            "status": "draft",
            "graph_filename": graph_filename,
            "conditions": {"type": "group", "operator": "AND", "children": []},
            "clinical_statement_id": str(plan.get("clinical_statement_id") or ""),
            "clinical_context": {"missing_information": ["此方案尚未绑定完整适用条件和结构化诊疗推荐，请对照完整原文核对"]},
            "conclusion": {
                "diagnosis": disease_code,
                "confidence": "medium",
                "conclusion_type": "treatment",
                "treatment_plan_id": plan_id,
                "treatment_name": str(plan.get("name") or plan_id),
                "treatment_items": list(plan.get("items") or []),
                "message": "大模型从指南抽取的治疗方案",
            },
            "source": {
                "guideline_id": doc_id,
                "guideline_title": doc_title,
                "chunk_id": str(plan.get("chunk_id") or (chunks[0]["chunk_id"] if chunks else "")),
                "section": "",
                "page_start": plan.get("page") or 1,
                "page_end": plan.get("page") or 1,
                "line_start": plan.get("line_start") or 1,
                "line_end": plan.get("line_end") or 1,
                "quote": str(plan.get("quote") or ""),
            },
            "tags": ["auto-generated", "treatment", doc_id],
        })

    diseases = [entity for entity in entities if entity.get("entity_type") == "disease"]
    if not diseases:
        detail = f"。最近的抽取告警：{warnings[-1]}" if warnings else ""
        raise LLMError(f"未抽取到有原文定位的疾病实体。{coverage_summary}，"
                       f"模型返回疾病候选 {disease_candidate_count} 个{detail}")
    target_code = max(
        diseases,
        key=lambda item: sum(1 for e in diseases if e.get("code") == item.get("code")),
    ).get("code", "")

    if require_import_quality and not coverage["complete"]:
        raise LLMError(
            f"解析覆盖不完整，已停止自动入库：{coverage_summary}。请重试失败片段。"
            + "；".join([warning for warning in warnings if "LLM 失败" in warning or "单独重试失败" in warning][-3:])
        )
    if require_import_quality and not relations:
        raise LLMError("未生成任何实体关系，已停止自动入库；请检查领域选择、模板和抽取告警")

    # Import graph and rules.
    performance["mapping_seconds"] = perf_counter() - mapping_started
    if controller is not None:
        controller.finalizing = True
        await controller.checkpoint(stage="写入图谱与待审诊疗依据（不可中断）")
    from ..api.routes.kg import GraphImportRequest, import_graph, normalize_graph_filename

    graph_filename = normalize_graph_filename(graph_filename)
    graph_path = root / "kg" / graph_filename
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    if not graph_path.exists():
        graph_path.write_text(json.dumps({
            "schema_version": 1, "domain": "western", "anchor_types": ["disease"],
            "diseases": [], "evidences": [], "disease_evidence": [], "sources": [],
            "nodes": [], "relations": [],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    graph_started = perf_counter()
    graph_result = await import_graph(GraphImportRequest(
        doc_id=doc_id,
        doc_title=doc_title,
        domain="western",
        target_disease_code=target_code,
        entities=entities,
        chunks=chunks,
        suggested_relations=relations,
        template_id=AUTO_TEMPLATE_ID,
        graph_filename=graph_filename,
        candidate_rules=rules,
        candidate_standards=raw_standards,
    ))

    performance["graph_import_seconds"] = perf_counter() - graph_started

    # Rules are deferred until clinical-basis review; existing rules are preserved.
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        from ..api.routes.rules import check_rule_sync
        sync_result = check_rule_sync(rules)
    except Exception as error:
        sync_result = {
            "total": len(rules),
            "consistent_count": 0,
            "inconsistent_count": 0,
            "rules": [],
            "error": str(error)[:180],
        }
    import_result = {
        "imported_at": created_at,
        "success": True,
        "graph": graph_result,
        "rules": {
            "imported": 0,
            "pending_basis_count": len(rules),
            "replaced": 0,
            "errors": [],
            "sync_check": sync_result,
        },
        "error": "",
    }
    if not coverage["complete"]:
        warnings.append(f"解析覆盖不完整：共 {coverage['total']} 个片段，已处理 {coverage['processed']}，失败 {coverage['failed']}，未安排 {coverage['not_scheduled']}；请查看覆盖报告")
    metadata = {
        "coverage": coverage,
        "performance": performance,
        "clinical_statements": statement_records,
        "clinical_statement_count": len(statement_records),
        "mode": "auto",
        "provider": settings.get("provider", "openai_compatible"),
        "model": settings.get("model"),
        "base_url": settings.get("base_url"),
        "groups_sent": len(groups),
        "groups_succeeded": succeeded,
        "terms_total": len(resolver.resolved_terms),
        "local_terms": len([item for item in resolver.resolved_terms if item.get("standard_system") == "LOCAL"]),
        "warnings": list(warnings),
        "created_at": created_at,
    }
    package_started = perf_counter()
    package = _package_record(
        doc_id, doc_title, graph_filename, entities, relations, rules, chunks,
        target_code, metadata, package_dir=root / "guide_packages",
        import_result=import_result,
    )
    performance["package_seconds"] = perf_counter() - package_started
    performance["pipeline_seconds"] = perf_counter() - started
    if controller is not None:
        controller.log("西医解析耗时 " + json.dumps({
            key: round(value, 3) for key, value in performance.items() if key.endswith("_seconds")
        }))
    return {
        "doc_id": doc_id,
        "doc_title": doc_title,
        "graph_filename": graph_filename,
        "target_disease_code": target_code,
        "entities": entities,
        "relations": relations,
        "rules": rules,
        "graph_result": graph_result,
        "package_id": package["package_id"],
        "checks": package["checks"],
        "terms": resolver.resolved_terms,
        "warnings": warnings,
        "metadata": metadata,
    }
