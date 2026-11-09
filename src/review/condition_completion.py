"""Complete "no concept condition" rules from graph concepts.

When a rule is semantically inconsistent because it carries no usable concept
conditions (empty condition group, symptom without ``symptom_code``, evidence
without ``evidence_id``), this module rebuilds those parts from the anchor
disease's graph evidence chain (disease → lab/exam/symptom → diagnostic
evidence).  Candidate conditions are probe-checked against the semantic
checker, so only conditions that fully resolve in the graph are used.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else list((value or {}).values())


def _evidence_to_condition(evidence: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    dtype = str(evidence.get("data_type") or "")
    if dtype == "lab" and evidence.get("lab_code") and evidence.get("concept_code"):
        return {
            "type": "lab_concept",
            "lab_code": str(evidence["lab_code"]),
            "concept_code": str(evidence["concept_code"]),
        }
    if dtype == "exam_finding" and evidence.get("finding_code"):
        condition: Dict[str, Any] = {
            "type": "exam_finding",
            "finding_code": str(evidence["finding_code"]),
        }
        if evidence.get("exam_code"):
            condition["exam_code"] = str(evidence["exam_code"])
        return condition
    if dtype == "symptom" and evidence.get("symptom_code"):
        return {"type": "symptom", "symptom_code": str(evidence["symptom_code"])}
    return None


def collect_disease_evidence_conditions(
    graph: Dict[str, Any],
    disease_code: str,
    vocab=None,
) -> Dict[str, Any]:
    """Collect probe-verified conditions from the disease's evidence chain."""
    from src.api.routes.rules import GraphSemanticChecker
    from src.vocab.repository import ConceptVocabularyRepository

    vocab = vocab or ConceptVocabularyRepository()
    links = [
        link for link in graph.get("disease_evidence") or []
        if str(link.get("disease_code") or "") == str(disease_code)
    ]
    evidence_index = {
        str(item.get("id")): item for item in _as_list(graph.get("evidences"))
        if isinstance(item, dict) and item.get("id")
    }
    links.sort(key=lambda link: (
        not bool(link.get("required")),
        -(float(link.get("weight") or 0)),
    ))

    symptoms: List[Dict[str, Any]] = []
    objective: List[Dict[str, Any]] = []
    used: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    seen = set()

    for link in links:
        evidence = evidence_index.get(str(link.get("evidence_id") or ""))
        if evidence is None:
            continue
        candidate = _evidence_to_condition(evidence)
        if candidate is None:
            skipped.append({
                "evidence_id": evidence.get("id"),
                "reason": "证据类型不支持条件映射",
            })
            continue
        key = json.dumps(candidate, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        probe_rule = {
            "rule_id": "probe",
            "graph_filename": graph.get("graph_filename", ""),
            "disease_code": disease_code,
            "rule_type": "diagnosis",
            "conditions": {"type": "group", "operator": "OR", "children": [candidate]},
        }
        result = GraphSemanticChecker(graph).check_rule(probe_rule)
        if result.get("semantic_status") != "consistent":
            skipped.append({
                "evidence_id": evidence.get("id"),
                "reason": "; ".join((result.get("issues") or ["图谱语义不一致"])[:2]),
            })
            continue
        seen.add(key)
        bucket = symptoms if candidate["type"] == "symptom" else objective
        bucket.append(candidate)
        used.append({
            "evidence_id": evidence.get("id"),
            "condition": candidate,
            "required": bool(link.get("required")),
            "weight": link.get("weight"),
        })
    return {
        "symptoms": symptoms,
        "objective": objective,
        "used": used,
        "skipped": skipped,
    }


def build_condition_tree(collected: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Symptoms OR objective findings, then AND between the two groups."""
    symptoms = collected.get("symptoms") or []
    objective = collected.get("objective") or []
    if symptoms and objective:
        return {
            "type": "group",
            "operator": "AND",
            "children": [
                {"type": "group", "operator": "OR", "children": list(symptoms)},
                {"type": "group", "operator": "OR", "children": list(objective)},
            ],
        }
    if symptoms:
        return {"type": "group", "operator": "OR", "children": list(symptoms)}
    if objective:
        return {"type": "group", "operator": "OR", "children": list(objective)}
    return None


def _leaf_is_incomplete(node: Dict[str, Any]) -> bool:
    node_type = str(node.get("type") or "")
    if node_type == "symptom" and not node.get("symptom_code"):
        return True
    if node_type == "evidence" and not node.get("evidence_id"):
        return True
    return False


def _transform(node: Any, collected: Dict[str, Any]) -> Any:
    if not isinstance(node, dict):
        return node
    generated = build_condition_tree(collected)
    if str(node.get("type") or "") == "group":
        children = node.get("children") or []
        if not children:
            return generated if generated else node
        new_children = []
        changed = False
        for child in children:
            new_child = _transform(child, collected)
            if new_child is not child:
                changed = True
            if isinstance(new_child, dict):
                new_children.append(new_child)
        if changed:
            return {**node, "children": new_children}
        return node
    if _leaf_is_incomplete(node):
        node_type = str(node.get("type") or "")
        if node_type == "symptom":
            symptoms = collected.get("symptoms") or []
            if len(symptoms) == 1:
                return {**node, "symptom_code": symptoms[0]["symptom_code"]}
            if symptoms:
                return {"type": "group", "operator": "OR", "children": list(symptoms)}
            return node
        if node_type == "evidence":
            objective = collected.get("objective") or []
            if objective:
                return {"type": "group", "operator": "OR", "children": list(objective)}
            return node
    return node


def complete_rule_conditions(
    rule: Dict[str, Any],
    graph: Dict[str, Any],
    vocab=None,
) -> Dict[str, Any]:
    """Fill in concept conditions for rules that have none.

    Returns ``{"changed": bool, "conditions": ..., "used": [...],
    "skipped": [...], "reason": str}``.
    """
    disease_code = str(rule.get("disease_code") or "")
    if not disease_code:
        return {"changed": False, "reason": "规则缺少目标疾病/证候编码"}

    collected = collect_disease_evidence_conditions(graph, disease_code, vocab)
    if not (collected["symptoms"] or collected["objective"]):
        return {
            "changed": False,
            "reason": f"图谱中疾病 {disease_code} 没有可映射的诊断证据条件",
            "skipped": collected["skipped"],
        }

    conditions = rule.get("conditions")
    new_conditions = _transform(conditions, collected)
    if new_conditions is None:
        new_conditions = conditions
    if new_conditions == conditions:
        return {
            "changed": False,
            "reason": "规则条件无需补全（不完整叶子的图谱证据不足以生成条件）",
            "skipped": collected["skipped"],
        }
    return {
        "changed": True,
        "conditions": new_conditions,
        "used": collected["used"],
        "skipped": collected["skipped"],
    }
