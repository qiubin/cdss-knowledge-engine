"""Universal guideline → knowledge graph + rules pipeline.

One upload drives the whole flow: LLM extraction, automatic terminology
resolution (standard code or LOCAL code), graph import, rule generation,
treatment plans and optional automatic activation.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..llm import LLMError, chat_json
from .concept_mapping import CARDIAC_FINDINGS
from ..models.rule import Rule
from ..terminology.labels import default_labels
from .terminology_resolver import (
    UNIVERSAL_TYPES,
    UniversalTermResolver,
    normalize_type,
)

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


def _clamp(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _chunk_groups(chunks: List[Dict[str, Any]], max_chars: int, max_chunks: int) -> List[List[Dict[str, Any]]]:
    selected = chunks[:max_chunks]
    groups: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    size = 0
    for chunk in selected:
        length = len(chunk.get("text", ""))
        if current and size + length > max_chars:
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
        }
        for chunk in group
    ]
    system = (
        "你是临床指南知识工程引擎。目标是把任意专科指南转换成可执行的临床知识图谱与规则。"
        "使用开放临床本体，不受预置疾病/症状词典限制。"
        "必须只输出一个 JSON 对象，包含 entities、relations、rules、treatment_plans。"
        "实体类型只能是：disease, symptom, sign, lab, exam, diagnostic_evidence, pathology, "
        "biomarker, molecular_test, stage, histology, risk_factor, medication, intervention, "
        "treatment_plan, contraindication, monitoring_requirement, "
        "order_set, order_item, care_pathway, pathway_phase。"
        "每个实体必须有 name、entity_type、chunk_id、page、line_start、line_end、quote、confidence。"
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
        "disease_name、disease_code、conditions、conclusion、source、confidence。"
        "condition 类型只能是：group, lab_concept, exam_finding, symptom, vital, demographic, "
        "past_history, medication, observation, attribute, evidence, fact。"
        "lab_concept 使用 lab_code + concept_code（ELEVATED/DECREASED/ABOVE_ULN_1/POSITIVE/NEGATIVE）；"
        "exam_finding 使用 exam_code + finding_code；symptom 使用 symptom_code；"
        "attribute 使用 attribute_code + operator + attribute_value（组织学、分期、分级、分子标志物）。"
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
        chunk = chunk_map.get(str(source.get("chunk_id") or "")) or (chunks[0] if chunks else {})
        source = {
            "guideline_id": doc_id,
            "guideline_title": doc_title,
            "chunk_id": str(source.get("chunk_id") or chunk.get("chunk_id", "")),
            "section": str(source.get("section") or chunk.get("section", "")),
            "page_start": source.get("page_start") or chunk.get("page_start"),
            "page_end": source.get("page_end") or chunk.get("page_end"),
            "line_start": source.get("line_start") or chunk.get("line_start"),
            "line_end": source.get("line_end") or chunk.get("line_end"),
            "quote": str(source.get("quote") or chunk.get("text", ""))[:220],
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
                "treatment_plan_id": f"TP-{doc_id}-{disease_code}",
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
        status = "active" if auto_activate and confidence >= DEFAULT_ACTIVE_CONFIDENCE else "draft"
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
            "priority": int(raw.get("priority") or (50 if rule_type == "diagnosis" else 40)),
            "status": status,
            "graph_filename": graph_filename,
            "conditions": conditions,
            "condition_text": labels.summarize(conditions),
            "conclusion": conclusion,
            "source": source,
            "tags": list(dict.fromkeys([*(raw.get("tags") or []), "auto-generated", doc_id])),
        }
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
    auto_import_disease: bool = True,
    controller: Any = None,
) -> Dict[str, Any]:
    """Run the complete universal auto ingestion pipeline."""
    root = Path(data_root) if data_root else Path(__file__).resolve().parents[2] / "data"
    resolver = UniversalTermResolver(auto_import_disease=auto_import_disease)
    settings = dict(config.get("llm", {}))
    call = llm_call or chat_json
    groups = _chunk_groups(
        chunks,
        int(settings.get("max_chunk_chars", 12000)),
        int(settings.get("max_chunks", 80)),
    )
    raw_entities: List[Dict[str, Any]] = []
    raw_relations: List[Dict[str, Any]] = []
    raw_rules: List[Dict[str, Any]] = []
    raw_plans: List[Dict[str, Any]] = []
    warnings: List[str] = []
    succeeded = 0

    def merge_payload(payload: Dict[str, Any]) -> None:
        raw_entities.extend(payload.get("entities") or [])
        raw_relations.extend(payload.get("relations") or [])
        raw_rules.extend(payload.get("rules") or [])
        raw_plans.extend(payload.get("treatment_plans") or [])

    async def call_group(chunk_group: List[Dict[str, Any]]) -> Dict[str, Any]:
        return await call(settings, _auto_prompt(chunk_group, doc_title))

    total_groups = len(groups)
    for index, group in enumerate(groups):
        if controller is not None:
            await controller.checkpoint(
                stage=f"自动抽取（第 {index + 1}/{total_groups} 组）",
                done=index, total=total_groups,
            )
        try:
            payload = await call_group(group)
        except Exception as error:
            message = str(error)
            split_payloads: List[Dict[str, Any]] = []
            if len(group) > 1 and any(
                token in message for token in ("JSON", "截断", "Expecting")
            ):
                # Long groups can exceed the model output budget and return
                # truncated JSON.  Retry each chunk separately so one oversized
                # segment does not discard the whole group.
                for chunk in group:
                    try:
                        split_payloads.append(await call_group([chunk]))
                    except Exception:
                        continue
            if split_payloads:
                failed = len(group) - len(split_payloads)
                warnings.append(
                    f"片段组 {group[0]['chunk_id']} 输出过大或格式错误，"
                    f"已按单片段重试成功 {len(split_payloads)}/{len(group)} 个"
                    + (f"，仍失败 {failed} 个" if failed else "")
                )
                for split_payload in split_payloads:
                    merge_payload(split_payload)
                succeeded += 1
                continue
            warnings.append(f"片段 {group[0]['chunk_id']} LLM 失败: {message[:180]}")
            continue
        merge_payload(payload)
        succeeded += 1
    if not succeeded:
        raise LLMError("大模型没有成功解析任何片段，请检查地址/API Key/模型配置")

    if controller is not None:
        await controller.checkpoint(stage="术语映射与图谱整理")

    chunk_map = {chunk["chunk_id"]: chunk for chunk in chunks}
    entities: List[Dict[str, Any]] = []
    seen = set()
    for raw in raw_entities:
        if not isinstance(raw, dict):
            continue
        chunk = chunk_map.get(str(raw.get("chunk_id") or ""))
        if chunk is None:
            chunk = chunks[0] if chunks else {}
        if not chunk:
            continue
        entity_type = normalize_type(str(raw.get("entity_type") or ""))
        if entity_type not in UNIVERSAL_TYPES:
            continue
        name = str(raw.get("name") or raw.get("term") or "").strip()
        if not name:
            continue
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
            "quote": str(raw.get("quote") or chunk.get("text", ""))[:220],
            "confidence": max(0.0, min(1.0, entity_confidence)),
        })

    by_name, by_code = _entity_map(entities)
    relations: List[Dict[str, Any]] = []
    seen_rel = set()
    for raw in raw_relations:
        if not isinstance(raw, dict):
            continue
        chunk_id = str(raw.get("chunk_id") or "")
        chunk = chunk_map.get(chunk_id) or (chunks[0] if chunks else {})
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
            "quote": str(raw.get("quote") or chunk.get("text", ""))[:220],
            "confidence": 0.7,
        })

    graph_filename = graph_filename or f"{DEFAULT_AUTO_GRAPH_PREFIX}{doc_id}.json"
    graph_filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", graph_filename)
    if not graph_filename.endswith(".json"):
        graph_filename += ".json"

    # Attach plan data from the LLM plan list to matching treatment rules.
    plan_by_disease = {
        str(plan.get("disease_name") or ""): plan
        for plan in raw_plans if isinstance(plan, dict)
    }
    for raw in raw_rules:
        if not isinstance(raw, dict) or RULE_TYPE_ALIASES.get(str(raw.get("rule_type") or ""), raw.get("rule_type")) != "treatment":
            continue
        disease = str(raw.get("disease_name") or "")
        plan = plan_by_disease.get(disease)
        if plan:
            conclusion = raw.setdefault("conclusion", {})
            conclusion.setdefault("treatment_plan_id", plan.get("plan_id"))
            conclusion.setdefault("treatment_name", plan.get("name"))
            conclusion.setdefault("treatment_items", plan.get("items") or [])

    rules, rule_warnings = _normalize_rules(
        raw_rules, entities, chunks, resolver, doc_id, doc_title,
        graph_filename, auto_activate,
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
            "name": f"自动生成: {disease_name or disease_code}治疗方案",
            "disease_code": disease_code,
            "disease_name": disease_name or disease_code,
            "priority": 40,
            "status": "active" if auto_activate else "draft",
            "graph_filename": graph_filename,
            "conditions": {"type": "group", "operator": "AND", "children": []},
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
                "quote": str(plan.get("quote") or "")[:220],
            },
            "tags": ["auto-generated", "treatment", doc_id],
        })

    diseases = [entity for entity in entities if entity.get("entity_type") == "disease"]
    if not diseases:
        resolved = resolver.resolve("disease", doc_title, doc_id=doc_id)
        diseases = [{**resolved, "chunk_id": chunks[0]["chunk_id"] if chunks else "", "page": 1, "line_start": 1, "line_end": 1, "quote": doc_title, "confidence": 0.5}]
        entities.append(diseases[0])
    target_code = max(
        diseases,
        key=lambda item: sum(1 for e in diseases if e.get("code") == item.get("code")),
    ).get("code", "")

    # Import graph and rules.
    if controller is not None:
        controller.finalizing = True
        await controller.checkpoint(stage="写入图谱与规则（不可中断）")
    from ..api.routes.kg import GraphImportRequest, import_graph, normalize_graph_filename

    graph_filename = normalize_graph_filename(graph_filename)
    graph_path = root / "kg" / graph_filename
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(json.dumps({
        "schema_version": 1, "domain": "western", "anchor_types": ["disease"],
        "diseases": [], "evidences": [], "disease_evidence": [], "sources": [],
        "nodes": [], "relations": [],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

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
    ))

    rules_path = root / "rules" / "managed_rules.json"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        all_rules = json.loads(rules_path.read_text(encoding="utf-8")) if rules_path.exists() else []
    except (OSError, json.JSONDecodeError):
        all_rules = []
    kept = [rule for rule in all_rules if not str(rule.get("rule_id") or "").startswith(f"{doc_id}-")]
    kept.extend(rules)
    rules_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from ..review.service import sync_blocked_rules_for_graph
        sync_blocked_rules_for_graph(
            graph_filename,
            graph_dir=root / "kg",
            rules_path=root / "rules" / "managed_rules.json",
        )
    except Exception:
        pass

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
            "imported": len(rules),
            "replaced": 0,
            "errors": [],
            "sync_check": sync_result,
        },
        "error": "",
    }
    metadata = {
        "mode": "auto",
        "provider": settings.get("provider", "openai_compatible"),
        "model": settings.get("model"),
        "base_url": settings.get("base_url"),
        "groups_sent": len(groups),
        "groups_succeeded": succeeded,
        "terms_total": len(resolver.resolved_terms),
        "local_terms": len([item for item in resolver.resolved_terms if item.get("standard_system") == "LOCAL"]),
        "created_at": created_at,
    }
    package = _package_record(
        doc_id, doc_title, graph_filename, entities, relations, rules, chunks,
        target_code, metadata, package_dir=root / "guide_packages",
        import_result=import_result,
    )
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
