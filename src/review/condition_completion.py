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


def collect_reviewed_treatment_links(graph, specification):
    """Read reviewed treatment content without inventing eligibility criteria."""
    nodes = {item.get("id"): item for item in _as_list(graph.get("nodes"))}
    relations = _as_list(graph.get("relations"))
    disease_code = specification.get("disease_code")
    anchors = {key for key, item in nodes.items() if item.get("node_type") == "disease"
               and item.get("code") == disease_code}
    def approved(edge):
        return (edge.get("review_status") == "active"
                and nodes.get(edge.get("source_node_id"), {}).get("review_status") == "active"
                and nodes.get(edge.get("target_node_id"), {}).get("review_status") == "active")
    plan_code = (specification.get("conclusion") or {}).get("treatment_plan_id")
    plans, investigations, items = [], [], []
    for edge in relations:
        if edge.get("source_node_id") not in anchors or not approved(edge):
            continue
        node = nodes[edge["target_node_id"]]
        if edge.get("relation_code") == "DISEASE_HAS_TREATMENT_PLAN":
            plans.append(node)
        elif edge.get("relation_code") in {"DISEASE_HAS_EXAM", "DISEASE_HAS_LAB"}:
            investigations.append({"code": node.get("code"), "name": node.get("name"),
                                   "entity_type": node.get("node_type"), "source_refs": node.get("source_refs") or []})
    selected = {node["id"] for node in plans if node.get("code") == plan_code}
    for edge in relations:
        if edge.get("source_node_id") not in selected or not approved(edge):
            continue
        if edge.get("relation_code") not in {"TREATMENT_PLAN_INCLUDES_MEDICATION", "TREATMENT_PLAN_INCLUDES_INTERVENTION"}:
            continue
        node = nodes[edge["target_node_id"]]
        item = {"code": node.get("code"), "name": node.get("name"), "entity_type": node.get("node_type")}
        if item not in items:
            items.append(item)
    return {"plans": [{"code": node.get("code"), "name": node.get("name")} for node in plans],
            "items": items, "investigations": investigations}


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
    approved_only: bool = False,
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
    # Older imports contain reviewed graph edges without the flat evidence map.
    nodes = {item.get("id"): item for item in _as_list(graph.get("nodes"))}
    anchors = {key for key, item in nodes.items()
               if item.get("node_type") == "disease" and str(item.get("code")) == disease_code}
    for relation in _as_list(graph.get("relations")):
        if relation.get("source_node_id") not in anchors:
            continue
        target = nodes.get(relation.get("target_node_id"), {})
        code = str(target.get("code") or "")
        relation_code = relation.get("relation_code")
        evidence = None
        if relation_code == "DISEASE_HAS_SYMPTOM" and code:
            evidence = {"data_type": "symptom", "symptom_code": code}
        elif relation_code == "DISEASE_HAS_DIAGNOSTIC_EVIDENCE" and code:
            lab_code, separator, concept = code.partition(":")
            if separator and any(n.get("node_type") == "lab" and n.get("code") == lab_code for n in nodes.values()):
                evidence = {"data_type": "lab", "lab_code": lab_code, "concept_code": concept}
            else:
                evidence = {"data_type": "exam_finding", "finding_code": code}
        if evidence:
            evidence_id = "graph:" + str(target.get("id"))
            evidence_index[evidence_id] = {**evidence, "id": evidence_id}
            links.append({"disease_code": disease_code, "evidence_id": evidence_id})
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
        if result.get("semantic_status") != "consistent" or (
            approved_only and result.get("dependency_status") != "passed"
        ):
            skipped.append({
                "evidence_id": evidence.get("id"),
                "reason": (
                    "关联实体或关系尚未全部审核通过"
                    if approved_only and result.get("dependency_status") != "passed"
                    else "; ".join((result.get("issues") or ["图谱语义不一致"])[:2])
                ),
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


def normalize_condition_logic(node: Any) -> tuple[Any, bool]:
    """Repair legacy condition groups without inventing ambiguous logic.

    ``ANY`` is the historical spelling of OR. A one-child group has no real
    Boolean decision and is collapsed. Multi-child groups with no operator are
    deliberately left for a short reviewer choice in the UI.
    """
    if not isinstance(node, dict):
        return node, False
    changed = False
    normalized = dict(node)
    children = []
    for child in node.get("children") or []:
        fixed, child_changed = normalize_condition_logic(child)
        children.append(fixed)
        changed = changed or child_changed
    if "children" in node:
        normalized["children"] = children
    required = []
    for child in node.get("required_children") or []:
        fixed, child_changed = normalize_condition_logic(child)
        required.append(fixed)
        changed = changed or child_changed
    if "required_children" in node:
        normalized["required_children"] = required
    if normalized.get("type") != "group":
        return normalized, changed
    if normalized.get("operator") == "ANY":
        normalized["operator"] = "OR"
        changed = True
    if normalized.get("operator") not in {"AND", "OR", "NOT"} and len(children) == 1:
        return children[0], True
    return normalized, changed


def unresolved_logic_questions(node: Any, path: str = "conditions") -> List[Dict[str, Any]]:
    """Describe only Boolean choices that require a clinical reviewer."""
    if not isinstance(node, dict):
        return []
    questions: List[Dict[str, Any]] = []
    children = node.get("children") or []
    if (
        node.get("type") == "group"
        and node.get("operator") not in {"AND", "OR", "NOT"}
        and len(children) >= 2
    ):
        questions.append({
            "path": path,
            "child_count": len(children),
            "children": children,
        })
    for index, child in enumerate(children):
        questions.extend(unresolved_logic_questions(child, f"{path}.children[{index}]"))
    return questions


def complete_rule_conditions(
    rule: Dict[str, Any],
    graph: Dict[str, Any],
    vocab=None,
    approved_only: bool = False,
) -> Dict[str, Any]:
    """Fill in concept conditions for rules that have none.

    Returns ``{"changed": bool, "conditions": ..., "used": [...],
    "skipped": [...], "reason": str}``.
    """
    disease_code = str(rule.get("disease_code") or "")
    if not disease_code:
        return {"changed": False, "reason": "规则缺少目标疾病/证候编码"}

    collected = collect_disease_evidence_conditions(
        graph, disease_code, vocab, approved_only=approved_only
    )
    if not (collected["symptoms"] or collected["objective"]):
        # No new graph evidence must not lock an already-authored basis out of
        # the editor. We can still repair harmless legacy syntax and ask only
        # for genuinely ambiguous Boolean choices.
        conditions, normalized = normalize_condition_logic(rule.get("conditions"))
        logic_questions = unresolved_logic_questions(conditions)
        has_conditions = bool(conditions) and not (
            isinstance(conditions, dict)
            and conditions.get("type") == "group"
            and not (conditions.get("children") or [])
        )
        return {
            "changed": normalized,
            "conditions": conditions,
            "reason": f"图谱中疾病 {disease_code} 没有可映射的诊断证据条件",
            "skipped": collected["skipped"],
            "logic_questions": logic_questions,
            "logic_normalized": normalized,
            "ready": has_conditions,
        }

    conditions = rule.get("conditions")
    new_conditions = _transform(conditions, collected)
    if new_conditions is None:
        new_conditions = conditions
    new_conditions, normalized = normalize_condition_logic(new_conditions)
    logic_questions = unresolved_logic_questions(new_conditions)
    if approved_only and new_conditions:
        # Keep authored logic intact; append new candidates in a separate group
        # for explicit human confirmation rather than losing reviewed edges.
        def contains(node, candidate):
            if not isinstance(node, dict):
                return False
            if all(node.get(key) == value for key, value in candidate.items()):
                return True
            return any(contains(child, candidate) for field in ("children", "required_children")
                       for child in node.get(field) or [])
        missing = []
        for candidate in [*collected["symptoms"], *collected["objective"]]:
            if not contains(new_conditions, candidate):
                missing.append(candidate)
        # An association supplies a candidate, never a new mandatory clause.
        # Preserve the authored Boolean tree until the reviewer chooses logic.
    if new_conditions == conditions:
        return {
            "changed": False,
            "reason": "规则条件无需补全（不完整叶子的图谱证据不足以生成条件）",
            "skipped": collected["skipped"],
            "candidates": missing if approved_only else [],
            "logic_questions": logic_questions,
            "ready": bool(new_conditions) and not (
                isinstance(new_conditions, dict)
                and new_conditions.get("type") == "group"
                and not (new_conditions.get("children") or [])
            ),
        }
    return {
        "changed": True,
        "conditions": new_conditions,
        "used": collected["used"],
        "skipped": collected["skipped"],
        "candidates": missing if approved_only and new_conditions else [],
        "logic_questions": logic_questions,
        "logic_normalized": normalized,
        "ready": bool(new_conditions) and not (
            isinstance(new_conditions, dict)
            and new_conditions.get("type") == "group"
            and not (new_conditions.get("children") or [])
        ),
    }
