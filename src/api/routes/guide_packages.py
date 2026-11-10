"""Guideline knowledge package generation and review workflow."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.models.rule import Rule
from src.domains import TCM, get_profile, normalize_domain
from src.extraction.graph_binding import resolve_template_graph_filename
from src.extraction.concept_mapping import GENERIC_FINDING_BLOCKLIST
from src.vocab.repository import ConceptVocabularyRepository, collect_concept_codes

from .kg import GraphImportRequest, import_graph
from .pdf import _load_doc
from .rules import RuleImportRequest, check_rule_sync, import_rules

router = APIRouter(prefix="/api/guide-packages", tags=["guide-packages"])

ROOT = Path(__file__).parent.parent.parent.parent
PACKAGE_DIR = ROOT / "data" / "guide_packages"
STANDARDS_DIR = ROOT / "data" / "standards"
TEMPLATE_PATH = ROOT / "data" / "templates" / "extraction_templates.json"
PACKAGE_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_STATUS = {"generated", "reviewed", "imported", "ready_for_test", "published", "failed"}
IMPORTABLE_STATUS = set(ALLOWED_STATUS)
IMPORT_MANAGED_STATUS = {"imported", "ready_for_test", "published"}
GRAPH_ENTITY_TYPES = {
    "disease", "lab", "exam", "diagnostic_evidence", "symptom",
    "treatment_plan", "intervention", "medication", "contraindication",
    "monitoring_requirement",
}

concept_vocab = ConceptVocabularyRepository()


class GuidePackageGenerateRequest(BaseModel):
    doc_id: str
    domain: str = ""
    target_disease_code: str = ""
    template_id: str = ""
    status: str = "generated"
    graph_target: Optional[Dict[str, Any]] = None
    reimport_confirmed: bool = False


class GuidePackageUpdateRequest(BaseModel):
    status: Optional[str] = None
    target_disease_code: Optional[str] = None
    graph_target: Optional[Dict[str, Any]] = None
    reimport_confirmed: bool = False


class GuidePackageImportRequest(BaseModel):
    reviewed_only: bool = False
    mode: str = "draft"


class PackageItemReviewRequest(BaseModel):
    items: List[str] = []
    action: str
    reviewer: str = "未署名"
    note: str = ""


PACKAGE_REVIEW_STATUSES = {"draft", "approved", "rejected"}


def _entity_item_id(entity: Dict[str, Any]) -> str:
    code = entity.get("code") or entity.get("concept_code") or entity.get("name")
    return f"entity:{entity.get('entity_type')}:{code}"


def _relation_item_id(relation: Dict[str, Any]) -> str:
    target = relation.get("target_code") or relation.get("target_concept_code") or ""
    return f"relation:{relation.get('source_code')}:{relation.get('relation_code')}:{target}"


def _rule_item_id(rule: Dict[str, Any]) -> str:
    return f"rule:{rule.get('rule_id')}"


def _package_items(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    reviews = package.get("item_review") or {}
    items: List[Dict[str, Any]] = []
    seen = set()
    for entity in package.get("entities") or []:
        item_id = _entity_item_id(entity)
        if item_id in seen:
            continue
        seen.add(item_id)
        review = reviews.get(item_id) or {}
        items.append({
            "item_id": item_id,
            "item_type": "entity",
            "entity_type": entity.get("entity_type", ""),
            "name": entity.get("name", ""),
            "code": entity.get("code") or entity.get("concept_code") or "",
            "chunk_id": entity.get("chunk_id", ""),
            "page": entity.get("page"),
            "line_start": entity.get("line_start"),
            "line_end": entity.get("line_end"),
            "status": review.get("status", "draft"),
            "reviewed_by": review.get("reviewed_by", ""),
            "reviewed_at": review.get("reviewed_at", ""),
            "note": review.get("note", ""),
        })
    for relation in package.get("suggested_relations") or []:
        item_id = _relation_item_id(relation)
        if item_id in seen:
            continue
        seen.add(item_id)
        review = reviews.get(item_id) or {}
        items.append({
            "item_id": item_id,
            "item_type": "relation",
            "relation_code": relation.get("relation_code", ""),
            "source_code": relation.get("source_code", ""),
            "target_code": relation.get("target_code") or relation.get("target_concept_code") or "",
            "chunk_id": relation.get("chunk_id", ""),
            "status": review.get("status", "draft"),
            "reviewed_by": review.get("reviewed_by", ""),
            "reviewed_at": review.get("reviewed_at", ""),
            "note": review.get("note", ""),
        })
    for rule in package.get("suggested_rules") or []:
        item_id = _rule_item_id(rule)
        if item_id in seen:
            continue
        seen.add(item_id)
        review = reviews.get(item_id) or {}
        items.append({
            "item_id": item_id,
            "item_type": "rule",
            "rule_id": rule.get("rule_id", ""),
            "name": rule.get("name", ""),
            "rule_type": rule.get("rule_type", ""),
            "status": review.get("status", "draft"),
            "reviewed_by": review.get("reviewed_by", ""),
            "reviewed_at": review.get("reviewed_at", ""),
            "note": review.get("note", ""),
        })
    return items


def _filter_approved(package: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    reviews = package.get("item_review") or {}
    approved = {
        item_id for item_id, review in reviews.items()
        if review.get("status") == "approved"
    }
    entities = [
        entity for entity in package.get("entities") or []
        if _entity_item_id(entity) in approved
    ]
    approved_entity_ids = {_entity_item_id(entity) for entity in entities}
    relations = [
        relation for relation in package.get("suggested_relations") or []
        if _relation_item_id(relation) in approved
        and _entity_item_id({
            "entity_type": relation.get("source_type"),
            "code": relation.get("source_code"),
        }) in approved_entity_ids
        and _entity_item_id({
            "entity_type": relation.get("target_type"),
            "code": relation.get("target_code") or relation.get("target_concept_code"),
        }) in approved_entity_ids
    ]
    rules = [
        rule for rule in package.get("suggested_rules") or []
        if _rule_item_id(rule) in approved
    ]
    return {"entities": entities, "suggested_relations": relations, "suggested_rules": rules}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _package_path(package_id: str) -> Path:
    if not re.fullmatch(r"PKG-[A-Za-z0-9_.-]+", package_id):
        raise HTTPException(400, "知识包 ID 格式无效")
    return PACKAGE_DIR / f"{package_id}.json"


def _read(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_package(package: Dict[str, Any]) -> None:
    PACKAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = _package_path(package["package_id"])
    package["updated_at"] = _now()
    path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_package(package_id: str) -> Dict[str, Any]:
    path = _package_path(package_id)
    if not path.exists():
        raise HTTPException(404, "知识包未找到")
    return _read(path)


def _load_packages() -> List[Dict[str, Any]]:
    packages = []
    for path in PACKAGE_DIR.glob("*.json"):
        try:
            packages.append(_read(path))
        except Exception:
            continue
    return sorted(packages, key=lambda item: item.get("updated_at", ""), reverse=True)


def _selected_template_id(doc: Dict[str, Any], requested: str, domain: str = "") -> str:
    templates = _load_templates()

    def selected(item_id: str):
        return next((item for item in templates if item.get("template_id") == item_id), None)

    requested_domain = normalize_domain(domain) if domain else ""
    if requested:
        template = selected(requested)
        if template is None:
            raise HTTPException(422, f"解析模板不存在: {requested}")
        if template.get("status") != "active":
            raise HTTPException(422, f"解析模板已停用: {requested}")
        if requested_domain and normalize_domain(template.get("domain")) != requested_domain:
            raise HTTPException(422, f"解析模板不属于 {requested_domain} 领域: {requested}")
        return requested

    configured = str(doc.get("extraction", {}).get("template_id") or "")
    configured_template = selected(configured) if configured else None
    if configured_template and configured_template.get("status") == "active":
        if not requested_domain or normalize_domain(configured_template.get("domain")) == requested_domain:
            return configured

    available = [
        item for item in templates
        if item.get("status") == "active"
        and (not requested_domain or normalize_domain(item.get("domain")) == requested_domain)
    ]
    if requested_domain:
        profile = get_profile(requested_domain)
        preferred = next(
            (item for item in available if item.get("template_id") == profile.default_template_id),
            None,
        )
        if preferred:
            return str(preferred.get("template_id") or "")
    defaults = [item for item in available if item.get("is_default")]
    if defaults:
        return str(defaults[0].get("template_id") or "")
    if available:
        return str(available[0].get("template_id") or "")
    raise HTTPException(422, "文档缺少可用的解析模板，请指定 template_id")


def _load_templates() -> List[Dict[str, Any]]:
    if not TEMPLATE_PATH.exists():
        return []
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def _resolve_graph_filename(template_id: str) -> str:
    """Resolve the isolated graph target from the template contract."""
    template = next(
        (item for item in _load_templates() if item.get("template_id") == template_id),
        {},
    )
    return resolve_template_graph_filename(SimpleNamespace(
        template_id=template_id,
        graph_filename=str(template.get("graph_filename") or ""),
    ))


def _safe_graph_filename(filename: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(filename or "").strip()).strip("._-")
    if not name:
        raise HTTPException(422, "图谱文件名不能为空")
    if not name.endswith(".json"):
        name += ".json"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.json", name) or ".." in name:
        raise HTTPException(422, "图谱文件名无效")
    return name


def _resolve_graph_target(
    request: Optional[Dict[str, Any]],
    domain: str,
    template_id: str,
    doc_id: str,
) -> Dict[str, Any]:
    """Resolve and validate the explicit graph target for a guideline package."""
    raw = request or {}
    mode = str(raw.get("mode") or "").strip().lower()
    if mode not in {"new", "existing", "legacy", ""}:
        raise HTTPException(422, "图谱目标只支持 new、existing 或 legacy")
    requested = str(raw.get("filename") or "").strip()

    if not mode:
        if normalize_domain(domain) == TCM:
            mode = "new"
            requested = f"KG-TCM-{_safe_graph_filename(doc_id)[:-5]}.json"
        else:
            mode = "legacy"
            requested = _resolve_graph_filename(template_id)
        requested = _safe_graph_filename(requested)
    elif mode == "new":
        requested = requested or f"KG-TCM-{_safe_graph_filename(doc_id)[:-5]}.json"
        requested = _safe_graph_filename(requested)
    else:
        if not requested:
            raise HTTPException(422, "追加既有图谱时必须选择图谱文件")
        requested = _safe_graph_filename(requested)

    path = ROOT / "data" / "kg" / requested
    if mode == "existing":
        if not path.exists():
            raise HTTPException(404, f"目标图谱不存在: {requested}")
        try:
            graph = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raise HTTPException(422, f"目标图谱不是有效 JSON: {requested}")
        graph_domain = normalize_domain(graph.get("domain") or "")
        if not graph_domain:
            # Compatibility with files created before explicit domain metadata.
            graph_domain = normalize_domain(_resolve_graph_filename(template_id) == requested and domain or "western")
        if normalize_domain(domain) == TCM and graph_domain != TCM:
            raise HTTPException(422, "中医模板只能追加 domain=tcm 的图谱")
        if normalize_domain(domain) == TCM and int(graph.get("schema_version") or 1) < 2:
            raise HTTPException(422, "目标中医图谱 schema_version 必须大于等于 2")
        if graph.get("source") == "builtin-demo":
            raise HTTPException(422, "内置示例图谱只用于演示，不能作为指南导入目标")
    elif path.exists():
        try:
            graph = json.loads(path.read_text(encoding="utf-8"))
            graph_domain = normalize_domain(graph.get("domain") or "")
        except Exception:
            graph_domain = ""
        if graph_domain and graph_domain != normalize_domain(domain):
            raise HTTPException(422, "新建图谱文件名已被其他领域占用")

    return {"mode": mode, "filename": requested, "domain": normalize_domain(domain)}


def _validate_graph_target_change(package: Dict[str, Any], target: Dict[str, Any]) -> None:
    previous = package.get("graph_target") or {
        "mode": "legacy", "filename": package.get("graph_filename", ""), "domain": package.get("domain", "")
    }
    changed = previous.get("filename") != target.get("filename")
    if changed and (package.get("import_result") or {}).get("success"):
        raise HTTPException(409, "知识包已成功导入；变更目标图谱必须显式确认重新导入")


def _selected_target(
    entities: List[Dict[str, Any]],
    requested: str,
    anchor_types: tuple[str, ...] = ("disease",),
) -> str:
    candidates = [
        item for item in entities
        if item.get("entity_type") in anchor_types and item.get("code")
    ]
    label = "/".join(anchor_types)
    if requested:
        if requested not in {str(item.get("code")) for item in candidates}:
            raise HTTPException(422, f"目标{label}不在抽取结果中: {requested}")
        return requested
    if not candidates:
        raise HTTPException(422, f"抽取结果中没有{label}实体，无法生成知识包")
    priority = {value: index for index, value in enumerate(anchor_types)}
    counts: Dict[str, int] = {}
    for item in candidates:
        code = str(item.get("code"))
        counts[code] = counts.get(code, 0) + 1
    return str(sorted(
        candidates,
        key=lambda item: (
            priority.get(str(item.get("entity_type")), len(priority)),
            -counts.get(str(item.get("code")), 0),
            -float(item.get("confidence") or 0),
        ),
    )[0]["code"])


def _rule_lab_concepts(rules: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "lab_concept" and node.get("lab_code") and node.get("concept_code"):
            result.append({
                "lab_code": str(node["lab_code"]),
                "concept_code": str(node["concept_code"]),
            })
        for child in node.get("children", []) or []:
            walk(child)

    for rule in rules:
        walk(rule.get("conditions"))
    unique = {(item["lab_code"], item["concept_code"]): item for item in result}
    return list(unique.values())


def _enrich_rule_sources(
    rules: List[Dict[str, Any]],
    entities: List[Dict[str, Any]],
    chunks: List[Dict[str, Any]],
    doc_id: str,
    doc_title: str,
    anchor_types: tuple[str, ...] = ("disease",),
) -> None:
    """Repair source fields in legacy extraction results without inventing evidence."""
    entity_by_code = {
        str(item.get("code")): item for item in entities
        if item.get("entity_type") in anchor_types and item.get("code")
    }
    chunk_by_id = {str(item.get("chunk_id")): item for item in chunks if item.get("chunk_id")}
    for rule in rules:
        source = rule.get("source") if isinstance(rule.get("source"), dict) else {}
        entity = entity_by_code.get(str(rule.get("disease_code") or ""))
        chunk = chunk_by_id.get(str(entity.get("chunk_id") if entity else ""))
        if not entity:
            continue
        source.update({
            "chunk_id": str(source.get("chunk_id") or (entity or {}).get("chunk_id", "")),
            "guideline_id": str(source.get("guideline_id") or doc_id),
            "guideline_title": str(source.get("guideline_title") or doc_title),
            "section": str(source.get("section") or (chunk or {}).get("section") or entity.get("quote", "")[:180]),
            "page_start": int(source.get("page_start") or entity.get("page") or (chunk or {}).get("page_start") or 0),
            "page_end": int(source.get("page_end") or entity.get("page") or (chunk or {}).get("page_end") or source.get("page_start") or 0),
            "line_start": int(source.get("line_start") or entity.get("line_start") or (chunk or {}).get("line_start") or 0),
            "line_end": int(source.get("line_end") or entity.get("line_end") or (chunk or {}).get("line_end") or source.get("line_start") or 0),
            "quote": str(source.get("quote") or entity.get("quote") or (chunk or {}).get("text", "")[:220]),
        })
        rule["source"] = source


def _hospital_standard_coverage(pairs: List[Dict[str, str]]) -> Dict[str, Any]:
    hospitals: Dict[str, List[Dict[str, Any]]] = {}
    for path in STANDARDS_DIR.glob("*.json"):
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in items:
            if isinstance(item, dict) and item.get("hospital_id"):
                hospitals.setdefault(str(item["hospital_id"]), []).append(item)

    missing: List[Dict[str, str]] = []
    details: List[Dict[str, Any]] = []
    if not hospitals:
        return {
            "status": "warning",
            "title": "医院检验标准",
            "issues": ["尚未配置任何医院标准；导入后规则只能保存草稿，不能执行"],
            "details": [],
        }
    for hospital_id in sorted(hospitals):
        for pair in pairs:
            matched = [
                item for item in hospitals[hospital_id]
                if item.get("status") == "active"
                and str(item.get("lab_code")) == pair["lab_code"]
                and str(item.get("concept_code")) == pair["concept_code"]
            ]
            label = f"{pair['lab_code']} / {pair['concept_code']}"
            if not matched:
                missing.append({"hospital_id": hospital_id, **pair})
            details.append({
                "hospital_id": hospital_id,
                "requirement": label,
                "coverage": "covered" if matched else "missing",
                "standard_count": len(matched),
            })
    return {
        "code": "hospital_standards",
        "status": "passed" if not missing else "warning",
        "title": "医院检验标准",
        "issues": [
            f"缺少标准: {item['hospital_id']} / {item['lab_code']} / {item['concept_code']}"
            for item in missing
        ],
        "details": details,
    }


def _tcm_checks(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    entities = package.get("entities", [])
    relations = package.get("suggested_relations", [])
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for entity in entities:
        by_type.setdefault(str(entity.get("entity_type") or ""), []).append(entity)

    checks: List[Dict[str, Any]] = []

    # 1. Each syndrome should carry at least one symptom and one tongue/pulse.
    completeness_issues: List[str] = []
    for syndrome in by_type.get("syndrome", []):
        chunk_id = syndrome.get("chunk_id", "")
        symptoms = [
            item for item in by_type.get("symptom", [])
            if item.get("chunk_id") == chunk_id
        ]
        tongue_pulse = [
            item for item in [*by_type.get("tongue", []), *by_type.get("pulse", [])]
            if item.get("chunk_id") == chunk_id
        ]
        name = syndrome.get("name") or syndrome.get("code")
        if not symptoms:
            completeness_issues.append(f"证候 {name} 缺少症状依据")
        if not tongue_pulse:
            completeness_issues.append(f"证候 {name} 缺少舌象或脉象依据")
    checks.append({
        "code": "tcm_syndrome_completeness",
        "status": "passed" if not completeness_issues else "warning",
        "title": "证候构成完整性",
        "issues": completeness_issues,
        "details": [{
            "syndrome_count": len(by_type.get("syndrome", [])),
            "symptom_count": len(by_type.get("symptom", [])),
            "tongue_count": len(by_type.get("tongue", [])),
            "pulse_count": len(by_type.get("pulse", [])),
        }],
    })

    # 2. Every formula should have a composition (herb list).
    herb_links: Dict[str, List[str]] = {}
    for relation in relations:
        if relation.get("relation_code") == "FORMULA_CONTAINS_HERB":
            herb_links.setdefault(str(relation.get("source_code") or ""), []).append(
                str(relation.get("target_code") or "")
            )
    formula_issues: List[str] = []
    for formula in by_type.get("formula", []):
        code = str(formula.get("code") or "")
        if code and not herb_links.get(code):
            formula_issues.append(f"方剂 {formula.get('name') or code} 缺少组成药物")
    checks.append({
        "code": "tcm_formula_composition",
        "status": "passed" if not formula_issues else "warning",
        "title": "方剂组成完整性",
        "issues": formula_issues,
        "details": [{"formula_count": len(by_type.get("formula", [])), "herb_count": len(by_type.get("herb", []))}],
    })

    # 3. Classical incompatibility pairs (十八反/十九畏 subset).
    incompatible = {
        frozenset(("TCM_HERB_GANCAO", "TCM_HERB_GANSUI")),
        frozenset(("TCM_HERB_GANCAO", "TCM_HERB_DAJI")),
        frozenset(("TCM_HERB_GANCAO", "TCM_HERB_HAIZAO")),
        frozenset(("TCM_HERB_GANCAO", "TCM_HERB_YUANHUA")),
        frozenset(("TCM_HERB_WUTOU", "TCM_HERB_BANXIA")),
        frozenset(("TCM_HERB_WUTOU", "TCM_HERB_GUALOU")),
        frozenset(("TCM_HERB_WUTOU", "TCM_HERB_BEIMU")),
        frozenset(("TCM_HERB_WUTOU", "TCM_HERB_BAILIAN")),
        frozenset(("TCM_HERB_WUTOU", "TCM_HERB_BAIJI")),
        frozenset(("TCM_HERB_LILU", "TCM_HERB_RENSHEN")),
        frozenset(("TCM_HERB_LILU", "TCM_HERB_DANSHEN")),
        frozenset(("TCM_HERB_LILU", "TCM_HERB_XUANSHEN")),
        frozenset(("TCM_HERB_LILU", "TCM_HERB_XIXIN")),
        frozenset(("TCM_HERB_LILU", "TCM_HERB_SHAOYAO")),
    }
    conflict_issues: List[str] = []
    herb_names = {
        str(item.get("code")): str(item.get("name") or item.get("code"))
        for item in entities if item.get("entity_type") == "herb"
    }
    for formula_code, herb_codes in herb_links.items():
        unique = sorted(set(herb_codes))
        for index, first in enumerate(unique):
            for second in unique[index + 1:]:
                if frozenset((first, second)) in incompatible:
                    conflict_issues.append(
                        f"方剂 {formula_code} 含相反/相畏药对 "
                        f"{herb_names.get(first, first)} + {herb_names.get(second, second)}"
                    )
    checks.append({
        "code": "tcm_contraindication_conflicts",
        "status": "passed" if not conflict_issues else "failed",
        "title": "配伍禁忌",
        "issues": conflict_issues,
        "details": [{"formula_count": len(herb_links)}],
    })
    return checks


def _rule_quality_check(rules: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Surface rule-generation quality problems that schema checks miss."""
    issues: List[str] = []
    diagnosis = [
        rule for rule in rules
        if str(rule.get("rule_type") or "diagnosis") == "diagnosis"
    ]
    treatment = [
        rule for rule in rules
        if str(rule.get("rule_type") or "") == "treatment"
    ]
    if not treatment:
        issues.append("未生成治疗规则：本地解析只生成诊断规则，无法输出治疗方案推荐")
    if not any(
        '"lab_concept"' in json.dumps(rule.get("conditions"), ensure_ascii=False)
        or '"exam_finding"' in json.dumps(rule.get("conditions"), ensure_ascii=False)
        for rule in diagnosis
    ):
        issues.append("没有任何诊断规则使用检验或检查条件，客观证据无法参与判定")

    signatures: Dict[str, int] = {}
    for rule in diagnosis:
        signature = json.dumps(rule.get("conditions"), sort_keys=True, ensure_ascii=False)
        signatures[signature] = signatures.get(signature, 0) + 1
    if diagnosis and len(signatures) < max(1, len(diagnosis) // 3):
        duplicate = max(signatures.values())
        issues.append(
            f"规则条件高度重复：{len(diagnosis)} 条诊断规则只有 {len(signatures)} 种条件树，最大重复 {duplicate} 条"
        )

    generic_only = 0
    for rule in diagnosis:
        text = json.dumps(rule.get("conditions"), ensure_ascii=False)
        if '"lab_concept"' in text or '"symptom"' in text:
            continue
        if any(code in text for code in GENERIC_FINDING_BLOCKLIST):
            generic_only += 1
    if generic_only:
        issues.append(f"{generic_only} 条规则只依赖通用病理词，不具备疾病特异性")

    return {
        "code": "rule_quality",
        "status": "passed" if not issues else "warning",
        "title": "规则质量",
        "issues": issues,
        "details": [{
            "diagnosis_count": len(diagnosis),
            "treatment_count": len(treatment),
            "distinct_condition_trees": len(signatures),
            "generic_only_count": generic_only,
        }],
    }


def _build_checks(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    entities: List[Dict[str, Any]] = package.get("entities", [])
    rules: List[Dict[str, Any]] = package.get("suggested_rules", [])
    target = str(package.get("target_disease_code") or "")
    domain = normalize_domain(package.get("domain") or "western")
    profile = get_profile(domain)
    anchor_types = profile.graph.anchor_types
    anchors = {
        str(item.get("code")) for item in entities
        if item.get("entity_type") in anchor_types and item.get("code")
    }
    anchor_label = "证候" if domain == TCM else "疾病"

    checks: List[Dict[str, Any]] = [{
        "code": "target_anchor",
        "status": "passed" if target in anchors else "failed",
        "title": f"目标{anchor_label}",
        "issues": [] if target in anchors else [f"目标{anchor_label}不在抽取实体中"],
        "details": [{"anchor_type": list(anchor_types), "anchor_code": target, "candidates": sorted(anchors)}],
    }]

    # Entity types follow the template contract so custom template types pass
    # pre-check instead of being flagged as unsupported graph entities.
    template = next(
        (item for item in _load_templates() if item.get("template_id") == package.get("template_id")),
        None,
    )
    allowed_types = {
        str(item.get("entity_type"))
        for item in (template or {}).get("entity_types", [])
    } or set(profile.graph.allowed_entity_types)
    type_counts = {
        entity_type: len([item for item in entities if item.get("entity_type") == entity_type])
        for entity_type in sorted(allowed_types)
    }
    invalid_entities = [
        {"index": index, "entity_type": item.get("entity_type", ""), "name": item.get("name", "")}
        for index, item in enumerate(entities)
        if not (item.get("code") or item.get("concept_code"))
    ]
    open_entities = [
        item for item in entities
        if (item.get("code") or item.get("concept_code"))
        and item.get("entity_type") not in allowed_types
    ]
    checks.append({
        "code": "graph_entities",
        "status": "passed" if not invalid_entities else "failed",
        "title": "图谱实体预检",
        "issues": [f"第 {item['index'] + 1} 条实体缺少编码" for item in invalid_entities],
        "details": [type_counts, {"open_import_count": len(open_entities)}],
    })
    if open_entities:
        names = sorted({str(item.get("entity_type")) for item in open_entities})
        checks.append({
            "code": "open_entities",
            "status": "warning",
            "title": "模板外实体开放导入",
            "issues": [
                f"{len(open_entities)} 条模板外实体（类型：{'、'.join(names)}）将以草稿节点导入，"
                "请在图谱审核中确认；确认后可通过「加入实体词典」沉淀到共享词库"
            ],
            "details": [{"open_types": names, "count": len(open_entities)}],
        })

    entity_concept_codes = []
    for item in entities:
        if item.get("entity_type") == "concept":
            entity_concept_codes.append(str(item.get("concept_code")))
        elif item.get("entity_type") == "diagnostic_evidence" and ":" in str(item.get("code", "")):
            entity_concept_codes.append(str(item.get("code")).split(":", 1)[1])
    used_concepts = sorted(set(entity_concept_codes + collect_concept_codes(rules)))
    unknown = [code for code in used_concepts if code and concept_vocab.find(code) is None]
    checks.append({
        "code": "concept_vocabulary",
        "status": "passed" if not unknown else "failed",
        "title": "概念词汇覆盖",
        "issues": [f"概念词汇未定义: {code}" for code in unknown],
        "details": [{"concept_codes": used_concepts}],
    })

    rule_errors: List[str] = []
    for index, rule in enumerate(rules):
        try:
            Rule(**rule)
        except Exception as exc:
            rule_errors.append(f"第 {index + 1} 条候选规则无效: {str(exc)}")
        source = rule.get("source") or {}
        if not source.get("guideline_id") or source.get("page_start") is None or source.get("line_start") is None:
            rule_errors.append(f"候选规则缺少完整溯源: {rule.get('rule_id', '')}")
    checks.append({
        "code": "candidate_rules",
        "status": "passed" if rules and not rule_errors else "failed" if rule_errors else "warning",
        "title": "候选规则完整性",
        "issues": rule_errors,
        "details": [{"rule_count": len(rules), "draft_target": True}],
    })

    if "hospital_standards" in profile.package_checks:
        checks.append(_hospital_standard_coverage(_rule_lab_concepts(rules)))
    if domain != TCM:
        checks.append(_rule_quality_check(rules))
    if domain == TCM:
        checks.extend(_tcm_checks(package))
    return checks


def _package_summary(package: Dict[str, Any]) -> Dict[str, Any]:
    entities = package.get("entities", [])
    return {
        "package_id": package.get("package_id", ""),
        "doc_id": package.get("doc_id", ""),
        "doc_title": package.get("doc_title", ""),
        "domain": normalize_domain(package.get("domain") or "western"),
        "template_id": package.get("template_id", ""),
        "graph_filename": package.get("graph_filename", ""),
        "graph_target": package.get("graph_target", {}),
        "target_disease_code": package.get("target_disease_code", ""),
        "target_anchor_type": package.get("target_anchor_type", ""),
        "status": package.get("status", "generated"),
        "entity_count": len(entities),
        "disease_count": len([item for item in entities if item.get("entity_type") == "disease"]),
        "syndrome_count": len([item for item in entities if item.get("entity_type") == "syndrome"]),
        "relation_count": len(package.get("suggested_relations", [])),
        "rule_count": len(package.get("suggested_rules", [])),
        "imported": bool(
            (package.get("import_result") or {}).get("success")
        ),
        "imported_at": (package.get("import_result") or {}).get("imported_at", ""),
        "check_summary": {
            "passed": len([item for item in package.get("checks", []) if item.get("status") == "passed"]),
            "warning": len([item for item in package.get("checks", []) if item.get("status") == "warning"]),
            "failed": len([item for item in package.get("checks", []) if item.get("status") == "failed"]),
        },
        "created_at": package.get("created_at", ""),
        "updated_at": package.get("updated_at", ""),
    }


@router.post("/generate")
async def generate_package(request: GuidePackageGenerateRequest):
    doc = _load_doc(request.doc_id)
    if not doc:
        raise HTTPException(404, "解析文档未找到")
    if request.status not in {"generated", "reviewed"}:
        raise HTTPException(422, "生成状态只支持 generated 或 reviewed")

    entities: List[Dict[str, Any]] = doc.get("entities", [])
    template_id = _selected_template_id(doc, request.template_id, request.domain)
    template_meta = next(
        (item for item in _load_templates() if item.get("template_id") == template_id),
        {},
    )
    domain = normalize_domain(
        request.domain
        or doc.get("extraction", {}).get("domain")
        or template_meta.get("domain")
    )
    profile = get_profile(domain)
    graph_target = _resolve_graph_target(
        request.graph_target, domain, template_id, request.doc_id
    )
    anchor_types = profile.graph.anchor_types
    target = _selected_target(entities, request.target_disease_code, anchor_types)
    target_entity = next(
        (item for item in entities if str(item.get("code")) == target),
        {},
    )
    package_id = f"PKG-{request.doc_id}"
    created_at = _now()
    existing_path = _package_path(package_id)
    if existing_path.exists():
        existing = _read(existing_path)
        created_at = existing.get("created_at", created_at)
        try:
            _validate_graph_target_change(existing, graph_target)
        except HTTPException as exc:
            if not request.reimport_confirmed:
                raise exc
        if (existing.get("import_result") or {}).get("success"):
            package_id = f"PKG-{request.doc_id}-{uuid.uuid4().hex[:8].upper()}"
            existing_path = _package_path(package_id)

    package = {
        "package_id": package_id,
        "doc_id": doc["doc_id"],
        "doc_title": doc["doc_title"],
        "domain": domain,
        "template_id": template_id,
        "graph_filename": graph_target["filename"],
        "graph_target": graph_target,
        "target_disease_code": target,
        "target_anchor_type": str(target_entity.get("entity_type") or ""),
        "status": request.status,
        "entities": entities,
        "chunks": doc.get("parsed", {}).get("chunks", []),
        "suggested_relations": doc.get("suggested_relations", []),
        "suggested_rules": doc.get("suggested_rules", []),
        "extraction": doc.get("extraction", {}),
        "checks": [],
        "import_result": None,
        "created_at": created_at,
        "updated_at": created_at,
    }
    _enrich_rule_sources(
        package["suggested_rules"], package["entities"], package["chunks"],
        package["doc_id"], package["doc_title"], anchor_types,
    )
    _enrich_rule_sources(
        package["suggested_rules"], package["entities"], package["chunks"],
        package["doc_id"], package["doc_title"],
    )
    package["checks"] = _build_checks(package)
    _save_package(package)
    return {"package": package, "summary": _package_summary(package)}


@router.get("")
async def list_packages(status: Optional[str] = None, domain: Optional[str] = None):
    packages = _load_packages()
    if status:
        packages = [item for item in packages if item.get("status") == status]
    if domain:
        normalized = normalize_domain(domain)
        packages = [
            item for item in packages
            if normalize_domain(item.get("domain") or "western") == normalized
        ]
    return [_package_summary(item) for item in packages]


@router.get("/{package_id}")
async def get_package(package_id: str, include_content: bool = True):
    package = _load_package(package_id)
    if include_content:
        return package
    return _package_summary(package)


@router.put("/{package_id}")
async def update_package(package_id: str, request: GuidePackageUpdateRequest):
    package = _load_package(package_id)
    profile = get_profile(package.get("domain"))
    if request.graph_target is not None:
        graph_target = _resolve_graph_target(
            request.graph_target,
            str(package.get("domain") or "western"),
            str(package.get("template_id") or ""),
            str(package.get("doc_id") or package_id),
        )
        if graph_target["filename"] != package.get("graph_filename") and (package.get("import_result") or {}).get("success"):
            if not request.reimport_confirmed:
                raise HTTPException(409, "知识包已成功导入；变更目标图谱必须显式确认重新导入")
        package["graph_target"] = graph_target
        package["graph_filename"] = graph_target["filename"]
    if request.target_disease_code is not None:
        package["target_disease_code"] = _selected_target(
            package.get("entities", []),
            request.target_disease_code,
            profile.graph.anchor_types,
        )
        package["target_anchor_type"] = next(
            (str(item.get("entity_type")) for item in package.get("entities", [])
             if str(item.get("code")) == package["target_disease_code"]),
            "",
        )
    if request.status is not None:
        if request.status not in ALLOWED_STATUS:
            raise HTTPException(422, "无效的知识包状态")
        if (
            request.status in IMPORT_MANAGED_STATUS
            and not (package.get("import_result") or {}).get("success")
        ):
            raise HTTPException(
                422,
                "知识包尚未完成导入，不能手动标记为已导入；请先执行导入图谱和草稿规则",
            )
        package["status"] = request.status
        package["checks"] = _build_checks(package)
    _save_package(package)
    return package


@router.get("/{package_id}/items")
async def list_package_items(
    package_id: str,
    status: Optional[str] = None,
    item_type: Optional[str] = None,
    q: Optional[str] = None,
):
    package = _load_package(package_id)
    items = _package_items(package)
    if status:
        items = [item for item in items if item["status"] == status]
    if item_type:
        items = [item for item in items if item["item_type"] == item_type]
    query = (q or "").strip().lower()
    if query:
        items = [
            item for item in items
            if query in f"{item.get('name', '')} {item.get('code', '')} {item.get('item_id', '')}".lower()
        ]
    summary = {
        "total": len(items),
        "draft": sum(1 for item in items if item["status"] == "draft"),
        "approved": sum(1 for item in items if item["status"] == "approved"),
        "rejected": sum(1 for item in items if item["status"] == "rejected"),
    }
    return {"package_id": package_id, "summary": summary, "items": items}


@router.post("/{package_id}/items/review")
async def review_package_items(package_id: str, request: PackageItemReviewRequest):
    if request.action not in {"approve", "reject", "restore"}:
        raise HTTPException(422, "无效的预审动作")
    package = _load_package(package_id)
    known = {item["item_id"] for item in _package_items(package)}
    unknown = [item_id for item_id in request.items if item_id not in known]
    if unknown:
        raise HTTPException(422, "未知条目: " + ", ".join(unknown[:10]))
    status = {"approve": "approved", "reject": "rejected", "restore": "draft"}[request.action]
    now = _now()
    reviews = package.setdefault("item_review", {})
    for item_id in request.items:
        reviews[item_id] = {
            "status": status,
            "reviewed_by": request.reviewer or "未署名",
            "reviewed_at": now,
            "note": request.note,
        }
    _save_package(package)
    return {"package_id": package_id, "updated": len(request.items), "items": _package_items(package)}


@router.post("/{package_id}/import")
async def import_package(package_id: str, request: GuidePackageImportRequest):
    package = _load_package(package_id)
    if request.reviewed_only and package.get("status") not in {"reviewed"}:
        raise HTTPException(422, "该知识包尚未标记为 reviewed")
    if package.get("status") not in IMPORTABLE_STATUS:
        raise HTTPException(422, f"当前状态不允许导入: {package.get('status')}")
    if request.mode not in {"draft", "approved_only"}:
        raise HTTPException(422, "无效的导入模式")

    entities = package.get("entities", [])
    relations = package.get("suggested_relations", [])
    candidate_rules = package.get("suggested_rules", [])
    if request.mode == "approved_only":
        filtered = _filter_approved(package)
        entities = filtered["entities"]
        relations = filtered["suggested_relations"]
        candidate_rules = filtered["suggested_rules"]
        if not entities:
            raise HTTPException(422, "没有预审通过的实体，无法导入")
        target = package.get("target_disease_code")
        approved_codes = {item.get("code") for item in entities}
        if target not in approved_codes:
            raise HTTPException(422, "目标疾病/证候未通过预审，无法导入")

    graph_request = GraphImportRequest(
        doc_id=package["doc_id"],
        doc_title=package["doc_title"],
        domain=package.get("domain", ""),
        target_disease_code=package["target_disease_code"],
        entities=entities,
        chunks=package.get("chunks", []),
        suggested_relations=relations,
        template_id=package.get("template_id", ""),
        graph_filename=package.get("graph_filename", ""),
        candidate_rules=candidate_rules,
    )
    try:
        graph_result = await import_graph(graph_request)
    except HTTPException as exc:
        package["status"] = "failed"
        package["import_result"] = {
            "imported_at": _now(),
            "success": False,
            "graph": None,
            "rules": None,
            "error": str(exc.detail),
        }
        _save_package(package)
        raise

    rule_result = await import_rules(RuleImportRequest(
        rules=candidate_rules,
        graph_filename=package.get("graph_filename", ""),
    ))
    rule_ids = {item.get("rule_id") for item in candidate_rules}
    all_rules = json.loads((ROOT / "data" / "rules" / "managed_rules.json").read_text(encoding="utf-8"))
    package_rules = [item for item in all_rules if item.get("rule_id") in rule_ids]
    try:
        sync_result = check_rule_sync(package_rules)
    except HTTPException as exc:
        sync_result = {"total": 0, "consistent_count": 0, "inconsistent_count": 0, "rules": [], "error": str(exc.detail)}

    package["checks"] = _build_checks(package)
    package["import_result"] = {
        "imported_at": _now(),
        "success": not rule_result.get("errors"),
        "mode": request.mode,
        "graph": graph_result,
        "rules": {**rule_result, "sync_check": sync_result},
        "error": "",
    }
    if not rule_result.get("errors") and sync_result.get("inconsistent_count", 1) == 0:
        package["status"] = "ready_for_test"
    else:
        package["status"] = "imported"

    # 自动生成一份阳性测试病历，供「病历测试」页即时验证（失败不阻断导入）。
    test_case = None
    try:
        from src.review.test_case_generator import generate_case_for_package
        test_case = generate_case_for_package(package)
    except Exception as exc:
        test_case = {"error": str(exc)[:200]}

    package["checks"] = _build_checks(package)
    package["import_result"] = {
        "imported_at": _now(),
        "success": not rule_result.get("errors"),
        "graph": graph_result,
        "rules": {**rule_result, "sync_check": sync_result},
        "test_case": test_case,
        "error": "",
    }
    _save_package(package)
    return {
        "package_id": package_id,
        "status": package["status"],
        "graph": graph_result,
        "rules": {**rule_result, "sync_check": sync_result},
        "test_case": test_case,
        "checks": package["checks"],
        "remaining_action": "请在规则管理中人工审核并逐条启用",
    }


@router.post("/{package_id}/generate-test-case")
async def generate_test_case(package_id: str):
    """为知识包（重新）生成一份阳性测试病历，写入病历测试的病例列表。

    门槛只要求包内有候选规则：重新生成知识包会清空 import_result，
    但已入库的规则条件仍然有效，可以继续生成/更新测试病例。
    """
    package = _load_package(package_id)
    if not (package.get("suggested_rules") or []):
        raise HTTPException(422, "该知识包没有候选规则，无法生成测试病历")
    from src.review.test_case_generator import generate_case_for_package

    try:
        result = generate_case_for_package(package)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "package_id": package_id,
        "case": result["case"],
        "patient_file": result["patient_file"],
        "rule_id": result["rule_id"],
        "summary": result["summary"],
        "hint": "已写入「病历测试」的病例列表，选择该病例即可验证推理；规则需审核启用后才会出现在疑似诊断中。",
    }
