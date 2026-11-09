"""Knowledge graph CRUD API."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.extraction.repository import ExtractionTemplateRepository
from src.extraction.service import relation_pairs_for_chunk
from src.extraction.graph_binding import resolve_template_graph_filename
from src.extraction.concept_mapping import (
    STANDARD_BACKED_LABS,
    infer_lab_concept,
    nearest_concept,
    normalize_lab_concept,
)
from src.domains import TCM, get_profile, normalize_domain
from src.domains.tcm.graph import ensure_tcm_semantic_layer
from src.kg.schema import ConceptVocabulary
from src.terminology.labels import default_labels
from src.vocab.entity_vocabulary import EntityVocabularyRepository, find_shared_entry, resolve_template

router = APIRouter(prefix="/api/kg", tags=["kg"])

KG_DIR = Path(__file__).parent.parent.parent.parent / "data" / "kg"
TEMPLATE_PATH = KG_DIR.parent / "templates" / "extraction_templates.json"
VOCAB_PATH = KG_DIR.parent / "vocab" / "concepts.json"
DEFAULT_GRAPH_FILENAME = "stemi_graph.json"

# Fallback for imports that carry no resolvable template; kept in sync with
# the historical graph contract.
LEGACY_ENTITY_TYPES = {"disease", "lab", "exam", "diagnostic_evidence", "symptom"}
# Old semantic types stay valid only as rule-condition vocabulary.  They are
# folded into diagnostic_evidence when rendered as graph nodes.
LEGACY_SEMANTIC_NODE_TYPES = {"concept", "exam_finding"}
LEGACY_SEMANTIC_RELATIONS = {
    "DISEASE_HAS_FINDING",
    "LAB_HAS_CONCEPT",
    "EXAM_HAS_FINDING",
    "FINDING_HAS_CONCEPT",
    "SYMPTOM_HAS_CONCEPT",
}

DEFAULT_RELATION_NAMES = {
    "DISEASE_HAS_LAB": "疾病关联检验",
    "DISEASE_HAS_EXAM": "疾病关联检查",
    "DISEASE_HAS_SYMPTOM": "疾病有症状",
    "DISEASE_HAS_DIAGNOSTIC_EVIDENCE": "疾病有诊断证据",
    "LAB_HAS_DIAGNOSTIC_EVIDENCE": "检验形成诊断证据",
    "EXAM_HAS_DIAGNOSTIC_EVIDENCE": "检查形成诊断证据",
    "DISEASE_HAS_TREATMENT_PLAN": "疾病有治疗方案",
    "TREATMENT_PLAN_INCLUDES_INTERVENTION": "治疗方案包含治疗措施",
    "TREATMENT_PLAN_INCLUDES_MEDICATION": "治疗方案包含药物",
    "TREATMENT_PLAN_HAS_CONTRAINDICATION": "治疗方案有禁忌",
    "TREATMENT_PLAN_HAS_MONITORING": "治疗方案有监测要求",
}

def normalize_graph_filename(filename: str = DEFAULT_GRAPH_FILENAME) -> str:
    """Keep graph assets in data/kg and reject path traversal."""
    name = (filename or DEFAULT_GRAPH_FILENAME).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.json", name) or ".." in name:
        raise HTTPException(422, "图谱文件名无效")
    return name


def _resolve_graph_domain(graph: Dict[str, Any], filename: str) -> str:
    """Resolve a graph's domain before the semantic layer is derived.

    A brand-new TCM graph has no ``domain`` field yet.  Infer it from the
    template or rules bound to the same graph filename so the western
    diagnostic-evidence projection never runs over TCM rules.
    """
    explicit = graph.get("domain")
    if explicit:
        return normalize_domain(explicit)
    if TEMPLATE_PATH.exists():
        try:
            templates = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            templates = []
        for template in templates:
            if str(template.get("graph_filename") or "") == filename:
                return normalize_domain(template.get("domain"))
    rules_path = KG_DIR.parent / "rules" / "managed_rules.json"
    if rules_path.exists():
        try:
            rules = json.loads(rules_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rules = []
        for rule in rules:
            if str(rule.get("graph_filename") or "") == filename:
                return normalize_domain(rule.get("domain"))
    return "western"


def _load_graph(filename: str = DEFAULT_GRAPH_FILENAME) -> Dict[str, Any]:
    path = KG_DIR / normalize_graph_filename(filename)
    graph: Dict[str, Any]
    if path.exists():
        graph = json.loads(path.read_text(encoding="utf-8"))
    else:
        graph = {"diseases": [], "evidences": [], "disease_evidence": [], "sources": []}
    if isinstance(graph.get("nodes"), list):
        graph["nodes"] = {item.get("id"): item for item in graph["nodes"] if item.get("id")}
    if isinstance(graph.get("relations"), list):
        graph["relations"] = {
            (item.get("source_node_id"), item.get("relation_code"), item.get("target_node_id")): item
            for item in graph["relations"]
            if item.get("source_node_id") and item.get("target_node_id")
        }
    graph["domain"] = _resolve_graph_domain(graph, normalize_graph_filename(filename))
    graph.setdefault("schema_version", 1)
    profile = get_profile(graph.get("domain"))
    graph.setdefault("anchor_types", list(profile.graph.anchor_types))
    _ensure_semantic_layer(graph, normalize_graph_filename(filename))
    # Legacy graphs may predate display_name and may still carry raw evidence
    # codes as names.  Backfill on read so review screens show 全称（编码）
    # without requiring a full regeneration.
    for node in graph["nodes"].values():
        if not isinstance(node, dict) or not node.get("node_type"):
            continue
        code = str(node.get("code") or "")
        name = str(node.get("name") or "")
        if name == code and node.get("node_type") == "diagnostic_evidence":
            composed = default_labels().evidence_name(code)
            if composed:
                node["name"] = name = composed
        # Always recompute: earlier imports may have stored the raw code as the
        # display name before the dictionary name was available.
        node["display_name"] = _node_display_name(
            str(node["node_type"]), code, name
        )
    return graph


def _save_graph(data: Dict[str, Any], filename: str = DEFAULT_GRAPH_FILENAME):
    path = KG_DIR / normalize_graph_filename(filename)
    payload = dict(data)
    if isinstance(data.get("nodes"), dict):
        payload["nodes"] = list(data["nodes"].values())
    if isinstance(data.get("relations"), dict):
        payload["relations"] = list(data["relations"].values())
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class GraphImportRequest(BaseModel):
    doc_id: str
    doc_title: str
    domain: str = ""
    target_disease_code: str = ""
    entities: List[Dict[str, Any]]
    chunks: List[Dict[str, Any]] = []
    suggested_relations: List[Dict[str, Any]] = []
    template_id: str = ""
    graph_filename: str = ""
    candidate_rules: List[Dict[str, Any]] = []


def _semantic_node_id(node_type: str, code: str) -> str:
    return f"ENTITY:{node_type}:{code}"


def _entity_semantic_code(entity: Dict[str, Any]) -> str:
    return str(entity.get("code") or entity.get("concept_code") or "")


def _semantic_node_name(node_type: str, code: str, fallback: str = "") -> str:
    if fallback:
        return fallback
    if TEMPLATE_PATH.exists():
        templates = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
        for template in templates:
            for item in template.get("entity_types", []):
                if item.get("entity_type") != node_type:
                    continue
                for entry in item.get("entries", []):
                    if entry.get("code") == code:
                        return str(entry.get("name") or code)
    # Templates migrated to shared dictionaries keep no local entries, so the
    # shared vocabulary is the primary name source.
    shared = find_shared_entry(node_type, code)
    if shared:
        return shared.name or code
    if node_type == "diagnostic_evidence":
        # Generated evidence codes (LAB:CONCEPT) may not have a curated
        # dictionary entry yet; compose the full term instead of showing the
        # raw code as the node name.
        composed = default_labels().evidence_name(code)
        if composed:
            return composed
    return code


def _node_display_name(node_type: str, code: str, name: str = "") -> str:
    """Pair the full term with its code for review and visualization."""
    code = str(code or "").strip()
    name = str(name or "").strip()
    if not name:
        labels = default_labels()
        name = (
            labels.evidence_name(code)
            if node_type == "diagnostic_evidence"
            else labels.name(node_type, code)
        )
    if name and code and name != code:
        return f"{name}（{code}）"
    return name or code


def _relation_names(template_id: str = "") -> Dict[str, str]:
    names = dict(DEFAULT_RELATION_NAMES)
    if TEMPLATE_PATH.exists():
        templates = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
        ordered = sorted(
            templates,
            key=lambda item: (item.get("template_id") != template_id, item.get("template_id", "")),
        )
        for template in ordered:
            for relation in template.get("relations", []):
                if relation.get("relation_code") and relation.get("name"):
                    names[str(relation["relation_code"])] = str(relation["name"])
    return names


def _upsert_semantic_node(
    graph: Dict[str, Any],
    node_type: str,
    code: str,
    name: str = "",
    source_refs: Optional[List[str]] = None,
    attributes: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not code:
        raise ValueError("语义节点缺少编码")
    node_id = _semantic_node_id(node_type, code)
    refs = [ref for ref in (source_refs or []) if ref]
    node = graph["nodes"].get(node_id)
    if node is None:
        node_name = _semantic_node_name(node_type, code, name)
        node = {
            "id": node_id,
            "node_type": node_type,
            "code": code,
            "concept_code": (
                code.split(":", 1)[1]
                if node_type == "diagnostic_evidence" and ":" in code
                else ""
            ),
            "name": node_name,
            # Reviewers see the full term together with the code: 肌钙蛋白（CTN）.
            "display_name": _node_display_name(node_type, code, node_name),
            "attributes": dict(attributes or {}),
            "source_refs": list(dict.fromkeys(refs)),
            "review_status": "draft",
        }
        graph["nodes"][node_id] = node
    else:
        if name and (not node.get("name") or node.get("name") == code):
            node["name"] = name
            node["display_name"] = _node_display_name(node_type, code, name)
        if not node.get("display_name"):
            node["display_name"] = _node_display_name(
                node_type, code, str(node.get("name") or "")
            )
        elif node["display_name"] == code and node.get("name"):
            node["display_name"] = _node_display_name(
                node_type, code, str(node.get("name") or "")
            )
        if attributes:
            node.setdefault("attributes", {}).update(attributes)
        node["source_refs"] = list(dict.fromkeys([*(node.get("source_refs") or []), *refs]))
    return node


def _upsert_semantic_relation(
    graph: Dict[str, Any],
    source_node_id: str,
    relation_code: str,
    target_node_id: str,
    relation_names: Dict[str, str],
    source_refs: Optional[List[str]] = None,
    attributes: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    key = (source_node_id, relation_code, target_node_id)
    relation = graph["relations"].get(key)
    refs = [ref for ref in (source_refs or []) if ref]
    if relation is None:
        relation = {
            "source_node_id": source_node_id,
            "relation_code": relation_code,
            "relation_name": relation_names.get(relation_code, relation_code),
            "target_node_id": target_node_id,
            "attributes": dict(attributes or {}),
            "source_refs": list(dict.fromkeys(refs)),
            "review_status": "draft",
        }
        graph["relations"][key] = relation
    else:
        if attributes:
            relation.setdefault("attributes", {}).update(attributes)
        relation["source_refs"] = list(dict.fromkeys([*(relation.get("source_refs") or []), *refs]))
    return relation


def _evidence_semantic_code(evidence: Dict[str, Any]) -> str:
    """Return the unified node code for one evidence row."""
    data_type = str(evidence.get("data_type") or "")
    if data_type == "lab" and evidence.get("lab_code") and evidence.get("concept_code"):
        return f"{evidence['lab_code']}:{evidence['concept_code']}"
    if data_type == "exam_finding" and evidence.get("finding_code"):
        return str(evidence["finding_code"])
    return ""


def _managed_rules_for(filename: str) -> List[Dict[str, Any]]:
    """Load managed rules belonging to one graph without coupling to RULES_DIR."""
    path = KG_DIR.parent / "rules" / "managed_rules.json"
    if not path.exists():
        return []
    try:
        rules = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [
        rule for rule in rules
        if isinstance(rule, dict)
        and str(rule.get("graph_filename") or DEFAULT_GRAPH_FILENAME) == filename
    ]


def _repair_symptom_evidence(graph: Dict[str, Any]) -> Dict[str, int]:
    """Repair historical symptom rows that actually contain exam findings.

    The repair is idempotent and only changes the flat evidence record before
    semantic nodes are derived; the original graph remains untouched until the
    caller explicitly saves it.
    """
    repaired = 0
    skipped = 0
    exam_entries = EntityVocabularyRepository("exam").list()
    name_to_entry = {
        name: entry
        for entry in exam_entries
        for name in ([entry.name, *entry.aliases] if entry.aliases else [entry.name])
        if name
    }
    for evidence in graph.get("evidences", []):
        if evidence.get("data_type") != "symptom" or evidence.get("symptom_code"):
            continue
        matched = name_to_entry.get(str(evidence.get("description") or ""))
        if not matched:
            candidates = [
                part for part in str(evidence.get("id") or "").split("-")[1:-1] if part
            ]
            for length in range(len(candidates), 0, -1):
                code = "-".join(candidates[:length])
                found = EntityVocabularyRepository("exam").find(code)
                if found:
                    matched = found
                    break
        if not matched:
            skipped += 1
            continue
        evidence["data_type"] = "exam"
        evidence["exam_code"] = matched.code
        evidence["symptom_code"] = None
        evidence["finding_code"] = None
        repaired += 1
    return {"repaired": repaired, "skipped": skipped}


def _cleanup_legacy_semantics(graph: Dict[str, Any]) -> None:
    """Remove obsolete concept/finding nodes and every edge left dangling."""
    removed_node_ids = {
        item.get("id")
        for item in graph.get("nodes", {}).values()
        if item.get("node_type") in LEGACY_SEMANTIC_NODE_TYPES
    }
    for node_id in removed_node_ids:
        graph["nodes"].pop(node_id, None)
    for key, relation in list(graph.get("relations", {}).items()):
        source = relation.get("source_node_id")
        target = relation.get("target_node_id")
        obsolete_relation = relation.get("relation_code") in LEGACY_SEMANTIC_RELATIONS
        dangling = source not in graph["nodes"] or target not in graph["nodes"]
        if obsolete_relation or dangling:
            graph["relations"].pop(key, None)


def _ensure_evidence_semantics(
    graph: Dict[str, Any],
    evidence: Dict[str, Any],
    relation_names: Dict[str, str],
) -> None:
    refs = list(evidence.get("source_refs") or [])
    evidence_type = evidence.get("data_type")
    if evidence_type == "exam" and evidence.get("exam_code"):
        _upsert_semantic_node(graph, "exam", evidence["exam_code"], source_refs=refs)
        return

    if evidence_type == "lab" and evidence.get("lab_code") and evidence.get("concept_code"):
        lab_id = _upsert_semantic_node(graph, "lab", evidence["lab_code"], source_refs=refs)["id"]
        code = _evidence_semantic_code(evidence)
        evidence_id = _upsert_semantic_node(
            graph, "diagnostic_evidence", code, source_refs=refs
        )["id"]
        _upsert_semantic_relation(
            graph, lab_id, "LAB_HAS_DIAGNOSTIC_EVIDENCE", evidence_id, relation_names, refs
        )
        return

    if evidence_type == "exam_finding" and evidence.get("finding_code"):
        code = str(evidence["finding_code"])
        evidence_node_id = _upsert_semantic_node(
            graph, "diagnostic_evidence", code, source_refs=refs
        )["id"]
        exam_code = str(evidence.get("exam_code") or "")
        if exam_code:
            exam_id = _upsert_semantic_node(graph, "exam", exam_code, source_refs=refs)["id"]
            _upsert_semantic_relation(
                graph, exam_id, "EXAM_HAS_DIAGNOSTIC_EVIDENCE", evidence_node_id, relation_names, refs
            )
        return

    if evidence_type == "symptom" and evidence.get("symptom_code"):
        _upsert_semantic_node(graph, "symptom", evidence["symptom_code"], source_refs=refs)


def _ensure_semantic_layer(graph: Dict[str, Any], graph_filename: str = "") -> None:
    """Derive the unified semantic layer for one graph.

    Western graphs project lab/exam/symptom rows into diagnostic-evidence
    nodes.  TCM graphs already carry syndrome/observation nodes from extraction
    and intentionally skip that projection.
    """
    if normalize_domain(graph.get("domain")) == TCM:
        ensure_tcm_semantic_layer(graph, graph_filename)
        return

    graph.setdefault("nodes", {})
    graph.setdefault("relations", {})
    relation_names = _relation_names()
    repair_stats = _repair_symptom_evidence(graph)
    graph["_repair_stats"] = repair_stats
    _cleanup_legacy_semantics(graph)

    for disease in graph.get("diseases", []):
        code = disease.get("code")
        if code:
            _upsert_semantic_node(graph, "disease", str(code), str(disease.get("name_cn") or code))

    _ensure_rule_semantics(
        graph,
        _managed_rules_for(normalize_graph_filename(graph_filename or DEFAULT_GRAPH_FILENAME)),
        relation_names,
    )

    for evidence in graph.get("evidences", []):
        _ensure_evidence_semantics(graph, evidence, relation_names)

    evidence_index = {item.get("id"): item for item in graph.get("evidences", [])}
    for link in graph.get("disease_evidence", []):
        evidence = evidence_index.get(link.get("evidence_id"))
        disease_code = str(link.get("disease_code") or "")
        if not evidence or not disease_code:
            continue
        refs = list(evidence.get("source_refs") or [])
        disease_id = _upsert_semantic_node(graph, "disease", disease_code)["id"]
        evidence_type = evidence.get("data_type")
        if evidence_type == "lab" and evidence.get("lab_code") and evidence.get("concept_code"):
            lab_id = _upsert_semantic_node(graph, "lab", evidence["lab_code"], source_refs=refs)["id"]
            evidence_id = _upsert_semantic_node(
                graph, "diagnostic_evidence", _evidence_semantic_code(evidence), source_refs=refs
            )["id"]
            _upsert_semantic_relation(graph, disease_id, "DISEASE_HAS_LAB", lab_id, relation_names, refs)
            _upsert_semantic_relation(
                graph, lab_id, "LAB_HAS_DIAGNOSTIC_EVIDENCE", evidence_id, relation_names, refs
            )
            _upsert_semantic_relation(
                graph, disease_id, "DISEASE_HAS_DIAGNOSTIC_EVIDENCE", evidence_id, relation_names, refs
            )
        elif evidence_type == "exam_finding" and evidence.get("finding_code"):
            evidence_id = _upsert_semantic_node(
                graph, "diagnostic_evidence", str(evidence["finding_code"]), source_refs=refs
            )["id"]
            _upsert_semantic_relation(
                graph, disease_id, "DISEASE_HAS_DIAGNOSTIC_EVIDENCE", evidence_id, relation_names, refs
            )
            exam_code = str(evidence.get("exam_code") or "")
            if exam_code:
                exam_id = _upsert_semantic_node(graph, "exam", exam_code, source_refs=refs)["id"]
                _upsert_semantic_relation(
                    graph, disease_id, "DISEASE_HAS_EXAM", exam_id, relation_names, refs
                )
                _upsert_semantic_relation(
                    graph, exam_id, "EXAM_HAS_DIAGNOSTIC_EVIDENCE", evidence_id, relation_names, refs
                )
        elif evidence_type == "exam" and evidence.get("exam_code"):
            exam_id = _upsert_semantic_node(graph, "exam", evidence["exam_code"], source_refs=refs)["id"]
            _upsert_semantic_relation(
                graph, disease_id, "DISEASE_HAS_EXAM", exam_id, relation_names, refs
            )
        elif evidence_type == "symptom" and evidence.get("symptom_code"):
            symptom_id = _upsert_semantic_node(
                graph, "symptom", str(evidence["symptom_code"]), source_refs=refs
            )["id"]
            _upsert_semantic_relation(
                graph, disease_id, "DISEASE_HAS_SYMPTOM", symptom_id, relation_names, refs
            )

def _ensure_rule_disease(graph: Dict[str, Any], rule: Dict[str, Any]) -> str:
    code = str(rule.get("disease_code") or "")
    if not code:
        return ""
    if not any(str(item.get("code")) == code for item in graph.get("diseases", [])):
        graph.setdefault("diseases", []).append({
            "code": code,
            "name_cn": str(rule.get("disease_name") or code),
            "name_en": "",
        })
    _upsert_semantic_node(graph, "disease", code, str(rule.get("disease_name") or code))
    return code


def _ensure_treatment_plan_semantics(
    graph: Dict[str, Any],
    rule: Dict[str, Any],
    relation_names: Dict[str, str],
) -> None:
    """Project a local treatment rule into disease → plan → items nodes."""
    if str(rule.get("rule_type") or "") != "treatment":
        return
    conclusion = rule.get("conclusion") if isinstance(rule.get("conclusion"), dict) else {}
    plan_code = str(conclusion.get("treatment_plan_id") or "")
    disease_code = str(rule.get("disease_code") or "")
    if not plan_code or not disease_code:
        return
    source = rule.get("source") if isinstance(rule.get("source"), dict) else {}
    refs = [str(source.get("chunk_id"))] if source.get("chunk_id") else []
    disease_id = _semantic_node_id("disease", disease_code)
    plan_id = _upsert_semantic_node(
        graph, "treatment_plan", plan_code,
        str(conclusion.get("treatment_name") or plan_code), refs,
    )["id"]
    _upsert_semantic_relation(
        graph, disease_id, "DISEASE_HAS_TREATMENT_PLAN", plan_id,
        relation_names, refs,
    )
    for item in conclusion.get("treatment_items") or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "")
        if not code:
            continue
        item_type = str(item.get("entity_type") or "medication")
        if item_type not in {"medication", "intervention"}:
            item_type = "medication"
        item_id = _upsert_semantic_node(
            graph, item_type, code, str(item.get("name") or code), refs,
        )["id"]
        relation_code = (
            "TREATMENT_PLAN_INCLUDES_MEDICATION"
            if item_type == "medication"
            else "TREATMENT_PLAN_INCLUDES_INTERVENTION"
        )
        _upsert_semantic_relation(
            graph, plan_id, relation_code, item_id, relation_names, refs,
        )


def _ensure_rule_semantics(
    graph: Dict[str, Any],
    rules: List[Dict[str, Any]],
    relation_names: Dict[str, str],
) -> int:
    """Fill the disease/entity/evidence paths required by candidate rules.

    Rule-condition structures intentionally remain unchanged; only their graph
    projection is migrated to the unified diagnostic-evidence node.
    """
    graph.setdefault("nodes", {})
    graph.setdefault("relations", {})
    relation_count_before = len(graph["relations"])
    evidence_index = {str(item.get("id")): item for item in graph.get("evidences", [])}
    link_keys = {
        (str(item.get("disease_code")), str(item.get("evidence_id")))
        for item in graph.get("disease_evidence", [])
    }

    def refs_for(rule: Dict[str, Any]) -> List[str]:
        source = rule.get("source") if isinstance(rule.get("source"), dict) else {}
        return [str(source.get("chunk_id"))] if source.get("chunk_id") else []

    def walk(rule: Dict[str, Any], condition: Any) -> None:
        if not isinstance(condition, dict):
            return
        if condition.get("type") == "group":
            for child in condition.get("children") or []:
                walk(rule, child)
            return

        disease_code = str(rule.get("disease_code") or "")
        refs = refs_for(rule)
        disease_id = _semantic_node_id("disease", disease_code)
        condition_type = str(condition.get("type") or "")

        if condition_type == "lab_concept" and condition.get("lab_code") and condition.get("concept_code"):
            lab_id = _upsert_semantic_node(
                graph, "lab", str(condition["lab_code"]), source_refs=refs
            )["id"]
            evidence_code = f"{condition['lab_code']}:{condition['concept_code']}"
            evidence_id = _upsert_semantic_node(
                graph, "diagnostic_evidence", evidence_code, source_refs=refs
            )["id"]
            _upsert_semantic_relation(
                graph, disease_id, "DISEASE_HAS_LAB", lab_id, relation_names, refs
            )
            _upsert_semantic_relation(
                graph, lab_id, "LAB_HAS_DIAGNOSTIC_EVIDENCE", evidence_id, relation_names, refs
            )
            _upsert_semantic_relation(
                graph, disease_id, "DISEASE_HAS_DIAGNOSTIC_EVIDENCE", evidence_id, relation_names, refs
            )
            return

        if condition_type == "exam_finding" and condition.get("finding_code"):
            evidence_id = _upsert_semantic_node(
                graph, "diagnostic_evidence", str(condition["finding_code"]), source_refs=refs
            )["id"]
            _upsert_semantic_relation(
                graph, disease_id, "DISEASE_HAS_DIAGNOSTIC_EVIDENCE", evidence_id, relation_names, refs
            )
            exam_code = str(condition.get("exam_code") or "")
            if exam_code:
                exam_id = _upsert_semantic_node(graph, "exam", exam_code, source_refs=refs)["id"]
                _upsert_semantic_relation(
                    graph, disease_id, "DISEASE_HAS_EXAM", exam_id, relation_names, refs
                )
                _upsert_semantic_relation(
                    graph, exam_id, "EXAM_HAS_DIAGNOSTIC_EVIDENCE", evidence_id, relation_names, refs
                )
            return

        if condition_type == "symptom" and condition.get("symptom_code"):
            symptom_id = _upsert_semantic_node(
                graph, "symptom", str(condition["symptom_code"]), source_refs=refs
            )["id"]
            _upsert_semantic_relation(
                graph, disease_id, "DISEASE_HAS_SYMPTOM", symptom_id, relation_names, refs
            )
            return

        if condition_type == "evidence":
            evidence = evidence_index.get(str(condition.get("evidence_id") or ""))
            if not evidence or not disease_code:
                return
            key = (disease_code, str(evidence.get("id")))
            if key not in link_keys:
                graph.setdefault("disease_evidence", []).append({
                    "disease_code": disease_code,
                    "evidence_id": evidence.get("id"),
                    "weight": 0.5,
                    "evidence_level": "待评估",
                    "required": False,
                })
                link_keys.add(key)
            _ensure_evidence_semantics(graph, evidence, relation_names)

    for rule in rules:
        if not _ensure_rule_disease(graph, rule):
            continue
        walk(rule, rule.get("conditions"))
        _ensure_treatment_plan_semantics(graph, rule, relation_names)
    return max(0, len(graph["relations"]) - relation_count_before)

def _resolve_import_template(template_id: str):
    """Resolved extraction template driving this import, or None if unavailable."""
    repository = ExtractionTemplateRepository()
    try:
        template = repository.get(template_id, allow_retired=True) if template_id else repository.get()
    except HTTPException:
        return None
    return resolve_template(template)


@router.post("/import")
async def import_graph(request: GraphImportRequest):
    """Import reviewed entities from an uploaded guideline into the JSON graph."""
    template = _resolve_import_template(request.template_id)
    requested_graph = str(request.graph_filename or "").strip()
    if not requested_graph and template is not None:
        requested_graph = resolve_template_graph_filename(template)
    if not requested_graph:
        requested_graph = get_profile(
            request.domain or "western"
        ).graph.legacy_graph_filename
    graph_filename = normalize_graph_filename(requested_graph)
    graph = _load_graph(graph_filename)
    graph.setdefault("diseases", [])
    graph.setdefault("evidences", [])
    graph.setdefault("disease_evidence", [])
    graph.setdefault("sources", [])
    if normalize_domain(graph.get("domain")) == TCM:
        graph.setdefault("syndromes", [])
    initial_nodes = len(graph["nodes"])
    initial_relations = len(graph["relations"])

    warnings: List[str] = []
    domain = normalize_domain(
        request.domain or graph.get("domain") or (template.domain if template else "")
    )
    profile = get_profile(domain)
    graph["domain"] = domain
    if domain == TCM:
        graph["schema_version"] = max(2, int(graph.get("schema_version") or 1))
    else:
        graph.setdefault("schema_version", 1)
    graph["anchor_types"] = list(profile.graph.anchor_types)
    if domain == TCM:
        graph.setdefault("syndromes", [])
    # Entity types come from the template contract so custom types defined in
    # a template flow into the graph instead of being silently dropped.
    fallback_types = (
        set(profile.graph.allowed_entity_types)
        if domain == TCM
        else LEGACY_ENTITY_TYPES
    )
    allowed_types = set(template.allowed_entity_types) if template else fallback_types

    disease_index = {d.get("code"): d for d in graph["diseases"]}
    syndrome_index = {s.get("code"): s for s in graph.get("syndromes", [])}
    evidence_index = {e.get("id"): e for e in graph["evidences"]}
    source_index = {s.get("chunk_id"): s for s in graph["sources"]}
    link_index = {
        (r.get("disease_code"), r.get("evidence_id"))
        for r in graph["disease_evidence"]
    }
    chunk_index = {c.get("chunk_id"): c for c in request.chunks}
    selected_diseases = [e for e in request.entities if e.get("entity_type") == "disease"]
    selected_syndromes = [e for e in request.entities if e.get("entity_type") == "syndrome"]
    target_code = request.target_disease_code or (
        selected_diseases[0].get("code", "") if selected_diseases
        else selected_syndromes[0].get("code", "") if selected_syndromes
        else ""
    )

    added_diseases = 0
    added_syndromes = 0
    added_evidence = 0
    added_sources = 0
    added_links = 0

    for entity in selected_diseases:
        code = entity.get("code", "")
        if not code or code in disease_index:
            continue
        disease = {
            "code": code,
            "name_cn": entity.get("name", code),
            "name_en": "",
        }
        graph["diseases"].append(disease)
        disease_index[code] = disease
        added_diseases += 1

    for entity in selected_syndromes:
        code = entity.get("code", "")
        if not code or code in syndrome_index:
            continue
        syndrome = {
            "code": code,
            "name_cn": entity.get("name", code),
        }
        graph.setdefault("syndromes", []).append(syndrome)
        syndrome_index[code] = syndrome
        added_syndromes += 1

    if target_code and target_code not in disease_index and target_code not in syndrome_index:
        raise HTTPException(400, "目标疾病或证候尚未抽取或不存在")

    def make_source(entity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        nonlocal added_sources
        chunk_id = entity.get("chunk_id", "")
        chunk = chunk_index.get(chunk_id)
        if not chunk_id or chunk_id in source_index:
            return None
        if chunk is None:
            chunk = {
                "chunk_id": chunk_id,
                "section": "已抽取片段",
                "page_start": entity.get("page", 0),
                "page_end": entity.get("page", 0),
                "line_start": entity.get("line_start", 0),
                "line_end": entity.get("line_end", 0),
                "quote": entity.get("quote", ""),
            }
        source = {
            "chunk_id": chunk_id,
            "doc_id": request.doc_id,
            "doc_title": request.doc_title,
            "section": chunk.get("section", ""),
            "page_start": chunk.get("page_start", entity.get("page", 0)),
            "page_end": chunk.get("page_end", entity.get("page", 0)),
            "line_start": chunk.get("line_start", entity.get("line_start", 0)),
            "line_end": chunk.get("line_end", entity.get("line_end", 0)),
            "quote": chunk.get("quote", entity.get("quote", "")),
        }
        graph["sources"].append(source)
        source_index[chunk_id] = source
        added_sources += 1
        return source

    concepts_in_chunk: Dict[str, List[Dict[str, Any]]] = {}
    evidence_in_chunk: Dict[str, List[Dict[str, Any]]] = {}
    exams_in_chunk: Dict[str, List[Dict[str, Any]]] = {}
    for entity in request.entities:
        entity_type = entity.get("entity_type", "")
        chunk_id = entity.get("chunk_id", "")
        if entity_type == "concept" and entity.get("concept_code"):
            concepts_in_chunk.setdefault(chunk_id, []).append(entity)
        elif entity_type == "diagnostic_evidence" and _entity_semantic_code(entity):
            evidence_in_chunk.setdefault(chunk_id, []).append(entity)
        elif entity_type == "exam" and entity.get("code"):
            exams_in_chunk.setdefault(chunk_id, []).append(entity)

    relation_names = _relation_names(request.template_id)
    skip_semantic_types = LEGACY_SEMANTIC_NODE_TYPES | {"diagnostic_evidence"}
    for entity in request.entities:
        code = _entity_semantic_code(entity)
        if not code:
            continue
        node_type = entity.get("entity_type", "")
        # Unified evidence is projected from flat evidence rows below, and old
        # concept/finding types remain rule vocabulary rather than graph nodes.
        if node_type in skip_semantic_types:
            continue
        if node_type not in allowed_types:
            warnings.append(f"已忽略模板外实体类型 {node_type}（{entity.get('name', '')}）")
            continue
        _upsert_semantic_node(
            graph, node_type, code, entity.get("name", ""),
            [entity.get("chunk_id", "")],
            attributes=entity.get("attributes") or {},
        )

    for relation in request.suggested_relations:
        relation_code = str(relation.get("relation_code") or "")
        source_type = relation.get("source_entity_type") or relation.get("source_type") or ""
        relation_target_code = relation.get("target_code") or relation.get("target_concept_code") or ""
        if not relation_code or not source_type or not relation_target_code:
            continue
        source_id = _upsert_semantic_node(
            graph, source_type, relation.get("source_code", ""),
            source_refs=[relation.get("chunk_id", "")],
        )["id"]
        target_id = _upsert_semantic_node(
            graph, relation.get("target_entity_type") or relation.get("target_type") or "",
            relation_target_code,
            source_refs=[relation.get("chunk_id", "")],
        )["id"]
        _upsert_semantic_relation(
            graph, source_id, relation_code, target_id, relation_names,
            [relation.get("chunk_id", "")],
            attributes=relation.get("attributes") or {},
        )

    entity_by_chunk: Dict[str, List[Dict[str, Any]]] = {}
    for entity in request.entities:
        if _entity_semantic_code(entity):
            entity_by_chunk.setdefault(entity.get("chunk_id", ""), []).append(entity)

    for chunk_id, values in entity_by_chunk.items():
        # Custom template relations still import normally; only the built-in
        # diagnostic-evidence layer is derived separately below.
        if template is None:
            continue
        for definition, source, target in relation_pairs_for_chunk(values, template, target_code):
            relation_source_code = _entity_semantic_code(source)
            relation_target_code = _entity_semantic_code(target)
            if not relation_source_code or not relation_target_code:
                continue
            source_id = _upsert_semantic_node(
                graph, source.get("entity_type", ""), relation_source_code,
                source.get("name", ""), [chunk_id],
            )["id"]
            target_id = _upsert_semantic_node(
                graph, target.get("entity_type", ""), relation_target_code,
                target.get("name", ""), [chunk_id],
            )["id"]
            _upsert_semantic_relation(graph, source_id, definition.relation_code, target_id, relation_names, [chunk_id])

    if domain == TCM:
        # TCM graphs are node/relation native.  Create provenance rows for every
        # chunk that contributed an entity or relation, then stop before the
        # western diagnostic-evidence projection.
        for entity in request.entities:
            make_source(entity)
        for relation in request.suggested_relations:
            chunk_id = str(relation.get("chunk_id") or "")
            if chunk_id:
                make_source({"chunk_id": chunk_id})
        for rule in request.candidate_rules:
            source = rule.get("source") or {}
            chunk_id = str(source.get("chunk_id") or "")
            if chunk_id:
                make_source({
                    "chunk_id": chunk_id,
                    "page": source.get("page_start"),
                    "line_start": source.get("line_start"),
                    "line_end": source.get("line_end"),
                    "quote": source.get("quote", ""),
                })
        _ensure_semantic_layer(graph, graph_filename)
        _save_graph(graph, graph_filename)
        return {
            "doc_id": request.doc_id,
            "domain": domain,
            "diseases_added": added_diseases,
            "syndromes_added": added_syndromes,
            "evidences_added": 0,
            "sources_added": added_sources,
            "links_added": 0,
            "nodes_added": max(0, len(graph["nodes"]) - initial_nodes),
            "relations_added": max(0, len(graph["relations"]) - initial_relations),
            "rule_semantics_added": 0,
            "graph_filename": graph_filename,
            "unknown_concepts": [],
            "warnings": sorted(set(warnings)),
            "graph_stats": {
                "diseases": len(graph["diseases"]),
                "syndromes": len(graph.get("syndromes", [])),
                "evidences": len(graph["evidences"]),
                "sources": len(graph["sources"]),
                "disease_evidence_links": len(graph["disease_evidence"]),
                "semantic_nodes": len(graph["nodes"]),
                "semantic_relations": len(graph["relations"]),
            },
        }

    evidence_specs: Dict[str, Dict[str, Any]] = {}

    def add_evidence_spec(spec: Dict[str, Any]) -> None:
        evidence_id = str(spec["id"])
        existing = evidence_specs.get(evidence_id)
        if not existing:
            evidence_specs[evidence_id] = spec
            return
        existing["source_refs"] = list(dict.fromkeys([
            *(existing.get("source_refs") or []), *(spec.get("source_refs") or []),
        ]))
        if not existing.get("exam_code") and spec.get("exam_code"):
            existing["exam_code"] = spec.get("exam_code")
        if not existing.get("description") and spec.get("description"):
            existing["description"] = spec.get("description")

    for chunk_id, entities in evidence_in_chunk.items():
        same_chunk_exams = exams_in_chunk.get(chunk_id, [])
        for entity in entities:
            code = _entity_semantic_code(entity)
            if ":" in code:
                lab_code, concept_code = code.split(":", 1)
                add_evidence_spec({
                    "id": f"EV-{request.doc_id}-{lab_code}-{concept_code}",
                    "description": entity.get("name", code),
                    "data_type": "lab",
                    "lab_code": lab_code,
                    "concept_code": concept_code,
                    "exam_code": None,
                    "finding_code": None,
                    "symptom_code": None,
                    "source_refs": [chunk_id],
                })
            else:
                add_evidence_spec({
                    "id": f"EV-{request.doc_id}-{code}-PRESENT",
                    "description": entity.get("name", code),
                    "data_type": "exam_finding",
                    "lab_code": None,
                    "concept_code": "PRESENT",
                    "exam_code": same_chunk_exams[0].get("code") if same_chunk_exams else None,
                    "finding_code": code,
                    "symptom_code": None,
                    "source_refs": [chunk_id],
                })

    for entity in request.entities:
        entity_type = entity.get("entity_type")
        chunk_id = entity.get("chunk_id", "")
        code = str(entity.get("code") or "")
        if entity_type == "lab" and code:
            lab_prefix = f"{code}:"
            concept_codes = [
                str(item.get("code", "")).split(":", 1)[1]
                for item in evidence_in_chunk.get(chunk_id, [])
                if str(item.get("code", "")).startswith(lab_prefix)
            ]
            if not concept_codes:
                nearby = nearest_concept(
                    concepts_in_chunk.get(chunk_id, []),
                    int(entity.get("line_start") or 0),
                    max_distance=4,
                )
                concept_codes = [nearby] if nearby else []
            if not concept_codes:
                inferred = infer_lab_concept(
                    code, str(entity.get("quote") or entity.get("name") or "")
                )
                concept_codes = [inferred] if inferred else []
            context = str(entity.get("quote") or entity.get("name") or "")
            concept_codes = [
                normalize_lab_concept(code, item, context)
                for item in concept_codes if item
            ]
            concept_codes = list(dict.fromkeys([item for item in concept_codes if item]))
            if not concept_codes:
                if code in STANDARD_BACKED_LABS:
                    # Do not invent ABNORMAL evidence for standard-backed labs:
                    # it has no hospital threshold and would be unevaluable.
                    continue
                concept_codes = ["ABNORMAL"]
            for concept_code in concept_codes:
                add_evidence_spec({
                    "id": f"EV-{request.doc_id}-{code}-{concept_code}",
                    "description": entity.get("name", code),
                    "data_type": "lab",
                    "lab_code": code,
                    "concept_code": concept_code,
                    "exam_code": None,
                    "finding_code": None,
                    "symptom_code": None,
                    "source_refs": [chunk_id],
                })
        elif entity_type == "exam" and code:
            add_evidence_spec({
                "id": f"EV-{request.doc_id}-{code}-PRESENT",
                "description": entity.get("name", code),
                "data_type": "exam",
                "lab_code": None,
                "concept_code": "PRESENT",
                "exam_code": code,
                "finding_code": None,
                "symptom_code": None,
                "source_refs": [chunk_id],
            })
        elif entity_type == "symptom" and code:
            add_evidence_spec({
                "id": f"EV-{request.doc_id}-{code}-PRESENT",
                "description": entity.get("name", code),
                "data_type": "symptom",
                "lab_code": None,
                "concept_code": "PRESENT",
                "exam_code": None,
                "finding_code": None,
                "symptom_code": code,
                "source_refs": [chunk_id],
            })

    for spec in evidence_specs.values():
        evidence_id = str(spec["id"])
        if evidence_id in evidence_index:
            evidence = evidence_index[evidence_id]
            evidence["source_refs"] = list(dict.fromkeys([
                *(evidence.get("source_refs") or []), *(spec.get("source_refs") or []),
            ]))
            # Newer unified imports can recover the parent exam for an old
            # finding-only evidence without changing its stable ID.
            if not evidence.get("exam_code") and spec.get("exam_code"):
                evidence["exam_code"] = spec.get("exam_code")
        else:
            evidence = {
                "id": evidence_id,
                "description": spec.get("description", ""),
                "data_type": spec.get("data_type", ""),
                "lab_code": spec.get("lab_code"),
                "concept_code": spec.get("concept_code"),
                "exam_code": spec.get("exam_code"),
                "finding_code": spec.get("finding_code"),
                "symptom_code": spec.get("symptom_code"),
                "parameter": {},
                "source_refs": list(dict.fromkeys(spec.get("source_refs") or [])),
                "review_status": "draft",
            }
            graph["evidences"].append(evidence)
            evidence_index[evidence_id] = evidence
            added_evidence += 1

        chunk_ids = list(dict.fromkeys(evidence.get("source_refs") or []))
        if chunk_ids and chunk_ids[-1] not in source_index:
            make_source({"chunk_id": chunk_ids[-1]})
        for chunk_id in chunk_ids:
            chunk_entities = entity_by_chunk.get(chunk_id, [])
            chunk_disease_codes = [
                str(item.get("code", ""))
                for item in chunk_entities
                if item.get("entity_type") == "disease" and item.get("code")
            ]
            linked_disease_codes = list(dict.fromkeys(chunk_disease_codes)) if len(chunk_disease_codes) == 1 else []
            for linked_disease_code in linked_disease_codes:
                if (linked_disease_code, evidence_id) in link_index:
                    continue
                graph["disease_evidence"].append({
                    "disease_code": linked_disease_code,
                    "evidence_id": evidence_id,
                    "weight": 0.5,
                    "evidence_level": "待评估",
                    "required": False,
                })
                link_index.add((linked_disease_code, evidence_id))
                added_links += 1
        _ensure_evidence_semantics(graph, evidence, relation_names)

    rule_semantics_added = _ensure_rule_semantics(
        graph,
        request.candidate_rules,
        relation_names,
    )
    _ensure_semantic_layer(graph, graph_filename)

    _save_graph(graph, graph_filename)
    vocabulary = ConceptVocabulary()
    vocabulary.load(str(KG_DIR.parent / "vocab" / "concepts.json"))
    unknown_concepts = sorted({
        entity.get("concept_code", "")
        for entity in request.entities
        if entity.get("entity_type") == "concept"
        and entity.get("concept_code")
        and not vocabulary.exists(entity.get("concept_code", ""))
    })

    return {
        "doc_id": request.doc_id,
        "diseases_added": added_diseases,
        "evidences_added": added_evidence,
        "sources_added": added_sources,
        "links_added": added_links,
        "nodes_added": max(0, len(graph["nodes"]) - initial_nodes),
        "relations_added": max(0, len(graph["relations"]) - initial_relations),
        "rule_semantics_added": rule_semantics_added,
        "graph_filename": graph_filename,
        "unknown_concepts": unknown_concepts,
        "warnings": sorted(set(warnings)),
        "graph_stats": {
            "diseases": len(graph["diseases"]),
            "evidences": len(graph["evidences"]),
            "sources": len(graph["sources"]),
            "disease_evidence_links": len(graph["disease_evidence"]),
            "semantic_nodes": len(graph["nodes"]),
            "semantic_relations": len(graph["relations"]),
        },
    }


@router.get("/graphs")
async def list_graphs(domain: Optional[str] = None):
    graphs = []
    for path in sorted(KG_DIR.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            graph_domain = normalize_domain(item.get("domain"))
            if domain and normalize_domain(domain) != graph_domain:
                continue
            nodes = item.get("nodes", [])
            relations = item.get("relations", [])
            node_list = nodes if isinstance(nodes, list) else list((nodes or {}).values())
            node_type_counts: Dict[str, int] = {}
            for node in node_list:
                if isinstance(node, dict) and node.get("node_type"):
                    node_type_counts[node["node_type"]] = node_type_counts.get(node["node_type"], 0) + 1
            profile = get_profile(graph_domain)
            graphs.append({
                "graph_filename": path.name,
                "domain": graph_domain,
                "domain_label": profile.label,
                "schema_version": item.get("schema_version", 1),
                "anchor_types": item.get("anchor_types", list(profile.graph.anchor_types)),
                "diseases": len(item.get("diseases", [])),
                "syndromes": len(item.get("syndromes", [])),
                "evidences": len(item.get("evidences", [])),
                "sources": len(item.get("sources", [])),
                "disease_evidence_links": len(item.get("disease_evidence", [])),
                "semantic_nodes": len(nodes) if isinstance(nodes, list) else len(nodes or {}),
                "semantic_relations": len(relations) if isinstance(relations, list) else len(relations or {}),
                "node_type_counts": node_type_counts,
                "updated_at": path.stat().st_mtime,
            })
        except Exception:
            continue
    return graphs


@router.get("/graph")
async def get_graph(graph_filename: str = DEFAULT_GRAPH_FILENAME):
    graph = _load_graph(graph_filename)
    graph["nodes"] = list(graph.get("nodes", {}).values())
    graph["relations"] = list(graph.get("relations", {}).values())
    graph["graph_filename"] = normalize_graph_filename(graph_filename)
    graph["profile"] = get_profile(graph.get("domain")).to_dict()
    return graph


@router.get("/diseases")
async def list_diseases(graph_filename: str = DEFAULT_GRAPH_FILENAME):
    return _load_graph(graph_filename).get("diseases", [])


@router.get("/evidences")
async def list_evidences(graph_filename: str = DEFAULT_GRAPH_FILENAME):
    return _load_graph(graph_filename).get("evidences", [])


@router.get("/sources")
async def list_sources(graph_filename: str = DEFAULT_GRAPH_FILENAME):
    return _load_graph(graph_filename).get("sources", [])


@router.get("/stats")
async def get_stats(graph_filename: str = DEFAULT_GRAPH_FILENAME):
    g = _load_graph(graph_filename)
    normalized_filename = normalize_graph_filename(graph_filename)
    rules_dir = KG_DIR.parent / "rules"
    managed_rules = rules_dir / "managed_rules.json"
    rules = []
    if managed_rules.exists():
        rules = json.loads(managed_rules.read_text(encoding="utf-8"))
    elif rules_dir.exists():
        for f in rules_dir.glob("*.json"):
            items = json.loads(f.read_text(encoding="utf-8"))
            rules.extend(items if isinstance(items, list) else [items])
    rule_count = sum(
        (item.get("graph_filename") or DEFAULT_GRAPH_FILENAME) == normalized_filename
        for item in rules
        if isinstance(item, dict)
    )
    return {
        "graph_filename": normalized_filename,
        "domain": normalize_domain(g.get("domain")),
        "diseases": len(g.get("diseases", [])),
        "syndromes": len(g.get("syndromes", [])),
        "evidences": len(g.get("evidences", [])),
        "sources": len(g.get("sources", [])),
        "disease_evidence_links": len(g.get("disease_evidence", [])),
        "semantic_nodes": len(g.get("nodes", {})),
        "semantic_relations": len(g.get("relations", {})),
        "rules": rule_count,
    }

