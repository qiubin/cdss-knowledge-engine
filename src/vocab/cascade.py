# src/vocab/cascade.py
"""Cascade shared-dictionary changes into stored rules.

Rules reference dictionary codes (disease_code, symptom_code, lab_code, ...).
A rename re-derives readable labels from the *saved* dictionary; a code remap
rewrites references everywhere in the rule; deletion of a referenced code is
blocked by the API layer.  Graph/template impact is reported, never silently
rewritten — graph changes go through the review workflow.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..terminology.labels import TerminologyLabels, default_labels

ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = ROOT / "data" / "rules" / "managed_rules.json"
KG_DIR = ROOT / "data" / "kg"

# rule condition field -> entity dictionary types that field may reference.
# `fact` / `medication` / `observation` 节点通过 `code` 字段引用实体字典，
# 在遍历逻辑里单独处理，不在此表。
CONDITION_FIELDS: Dict[str, Tuple[str, ...]] = {
    "lab_code": ("lab",),
    "symptom_code": ("symptom",),
    "exam_code": ("exam",),
    "finding_code": ("exam_finding",),
}
OBSERVATION_TYPE_TO_ENTITY = {
    "symptom": "symptom", "tongue": "tongue", "pulse": "pulse",
    "sign": "sign", "constitution": "constitution",
}
# fact 节点 fact_type -> 实体字典类型；未列出的 fact_type 直接充当实体类型
FACT_TYPE_TO_ENTITY = {"treatment": "treatment_method"}


def _anchor_entity_type(rule: Dict[str, Any]) -> str:
    return "syndrome" if str(rule.get("anchor_type")) == "syndrome" else "disease"


def collect_rule_codes(rule: Dict[str, Any]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []

    def add(entity_type: str, code: str) -> None:
        if entity_type and code:
            pairs.append((entity_type, str(code)))

    anchor_type = _anchor_entity_type(rule)
    add(anchor_type, str(rule.get("disease_code") or ""))

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if str(node.get("type")) == "observation":
            add(
                OBSERVATION_TYPE_TO_ENTITY.get(str(node.get("observation_type") or "symptom"), ""),
                str(node.get("code") or ""),
            )
        elif str(node.get("type")) == "fact":
            fact_type = str(node.get("fact_type") or "")
            add(FACT_TYPE_TO_ENTITY.get(fact_type, fact_type), str(node.get("code") or ""))
        elif str(node.get("type")) == "medication":
            add("medication", str(node.get("code") or ""))
        for field, types in CONDITION_FIELDS.items():
            value = str(node.get(field) or "")
            if value and types:
                add(types[0], value)
        for child in node.get("children") or []:
            walk(child)
        for child in node.get("required_children") or []:
            walk(child)

    walk(rule.get("conditions"))

    conclusion = rule.get("conclusion") or {}
    for item in conclusion.get("treatment_items") or []:
        if isinstance(item, dict):
            add(str(item.get("entity_type") or ""), str(item.get("code") or ""))
    for herb in conclusion.get("herbs") or []:
        if isinstance(herb, dict):
            add("herb", str(herb.get("code") or ""))
    for field in ("treatment_method_code", "formula_code"):
        entity_type = "treatment_method" if field == "treatment_method_code" else "formula"
        add(entity_type, str(conclusion.get(field) or ""))
    return list(dict.fromkeys(pairs))


def affected_rule_ids(entity_type: str, code: str, rules: List[Dict[str, Any]]) -> List[str]:
    target = (entity_type, str(code))
    return [
        str(rule.get("rule_id") or "")
        for rule in rules
        if target in collect_rule_codes(rule)
    ]


def _load_rules(rules_path: Path) -> List[Dict[str, Any]]:
    if not rules_path.exists():
        return []
    data = json.loads(rules_path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _save_rules(rules_path: Path, rules: List[Dict[str, Any]]) -> None:
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")


def _sync_renamed_label(labels: TerminologyLabels, entity_type: str, code: str, new_name: str) -> None:
    """Reflect a rename in the labels instance before re-annotating.

    The dictionary on disk is saved *before* cascade runs, so a freshly built
    TerminologyLabels would already carry the new name.  ``default_labels()``
    is lru-cached and callers (or tests) may hand in an older snapshot, so the
    renamed entry is written onto the instance to guarantee regenerated labels
    match the rename actually applied.
    """
    if entity_type and code and new_name:
        labels._names.setdefault(entity_type, {})[str(code)] = str(new_name)


def _refresh_labels(rule: Dict[str, Any], labels: TerminologyLabels) -> None:
    labels.annotate(rule.get("conditions"))
    rule["condition_text"] = labels.summarize(rule.get("conditions"))


def apply_name_cascade(
    entity_type: str,
    code: str,
    new_name: str,
    rules_path: Path = RULES_PATH,
    labels: Optional[TerminologyLabels] = None,
) -> Dict[str, Any]:
    """Dictionary entry renamed: re-derive labels and display names."""
    labels = labels or default_labels()
    _sync_renamed_label(labels, entity_type, code, new_name)
    rules = _load_rules(rules_path)
    updated: List[str] = []
    now = datetime.now(timezone.utc).isoformat()
    for rule in rules:
        if (entity_type, str(code)) not in collect_rule_codes(rule):
            continue
        if entity_type == _anchor_entity_type(rule) and str(rule.get("disease_code")) == str(code):
            rule["disease_name"] = new_name
        _refresh_labels(rule, labels)
        rule["updated_at"] = now
        updated.append(str(rule.get("rule_id") or ""))
    if updated:
        _save_rules(rules_path, rules)
    return {"updated": updated}


def apply_code_remap(
    entity_type: str,
    old_code: str,
    new_code: str,
    rules_path: Path = RULES_PATH,
    labels: Optional[TerminologyLabels] = None,
) -> Dict[str, Any]:
    """Dictionary code remapped: rewrite every rule reference to the code."""
    labels = labels or default_labels()
    rules = _load_rules(rules_path)
    updated: List[str] = []
    now = datetime.now(timezone.utc).isoformat()

    for rule in rules:
        changed = False
        if entity_type == _anchor_entity_type(rule) and str(rule.get("disease_code")) == str(old_code):
            rule["disease_code"] = new_code
            changed = True
        conclusion = rule.get("conclusion") or {}
        if entity_type == "disease" and str(conclusion.get("diagnosis") or "") == str(old_code):
            conclusion["diagnosis"] = new_code
            changed = True

        def walk(node: Any) -> None:
            nonlocal changed
            if not isinstance(node, dict):
                return
            if (
                str(node.get("type")) == "observation"
                and OBSERVATION_TYPE_TO_ENTITY.get(str(node.get("observation_type") or "symptom")) == entity_type
                and str(node.get("code") or "") == str(old_code)
            ):
                node["code"] = new_code
                changed = True
            elif str(node.get("type")) == "fact":
                fact_type = str(node.get("fact_type") or "")
                if (
                    FACT_TYPE_TO_ENTITY.get(fact_type, fact_type) == entity_type
                    and str(node.get("code") or "") == str(old_code)
                ):
                    node["code"] = new_code
                    changed = True
            elif str(node.get("type")) == "medication":
                if entity_type == "medication" and str(node.get("code") or "") == str(old_code):
                    node["code"] = new_code
                    changed = True
            for field, types in CONDITION_FIELDS.items():
                if entity_type in types and str(node.get(field) or "") == str(old_code):
                    node[field] = new_code
                    changed = True
            for child in node.get("children") or []:
                walk(child)
            for child in node.get("required_children") or []:
                walk(child)

        walk(rule.get("conditions"))
        for item in conclusion.get("treatment_items") or []:
            if (
                isinstance(item, dict)
                and str(item.get("entity_type") or "") == entity_type
                and str(item.get("code") or "") == str(old_code)
            ):
                item["code"] = new_code
                changed = True
        for herb in conclusion.get("herbs") or []:
            if entity_type == "herb" and isinstance(herb, dict) and str(herb.get("code") or "") == str(old_code):
                herb["code"] = new_code
                changed = True
        if str(conclusion.get("treatment_method_code") or "") == str(old_code) and entity_type == "treatment_method":
            conclusion["treatment_method_code"] = new_code
            changed = True
        if str(conclusion.get("formula_code") or "") == str(old_code) and entity_type == "formula":
            conclusion["formula_code"] = new_code
            changed = True

        if changed:
            _refresh_labels(rule, labels)
            rule["updated_at"] = now
            updated.append(str(rule.get("rule_id") or ""))

    if updated:
        _save_rules(rules_path, rules)
    return {"updated": updated}


def usage_in_graphs(entity_type: str, code: str, kg_dir: Path = KG_DIR) -> List[str]:
    """Graph filenames whose nodes reference this dictionary code."""
    hits: List[str] = []
    if not kg_dir.exists():
        return hits
    for graph_path in sorted(kg_dir.glob("*.json")):
        try:
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        nodes = graph.get("nodes") or []
        if isinstance(nodes, dict):
            nodes = list(nodes.values())
        referenced = any(
            isinstance(node, dict)
            and str(node.get("node_type") or "") == entity_type
            and str(node.get("code") or "") == str(code)
            for node in nodes
        )
        if not referenced and entity_type == "disease":
            diseases = graph.get("diseases") or []
            referenced = any(
                isinstance(item, dict) and str(item.get("code") or "") == str(code)
                for item in diseases
            )
        if referenced:
            hits.append(graph_path.name)
    return hits
