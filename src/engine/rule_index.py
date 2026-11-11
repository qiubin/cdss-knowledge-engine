# src/engine/rule_index.py
"""Conservative rule-pruning index.

Before evaluating a rule's condition tree, the index proves whether the rule
can possibly match the data the patient carries.  Pruning is *sound*: a rule
is dropped only when every path through its tree demands data the patient
does not have.  Anything uncertain (evidence leaves resolved inside per-rule
graphs, NOT groups, missing codes) counts as satisfiable so semantics never
change — this is pure pre-filtering ahead of the full evaluation.
"""
from __future__ import annotations

from typing import List, Optional, Set

from ..models.patient import PatientSnapshot
from ..models.rule import Rule, RuleCondition


def patient_keys(patient: PatientSnapshot) -> Set[str]:
    keys: Set[str] = set()
    for lab in patient.labs:
        if lab.lab_code:
            keys.add(f"lab:{lab.lab_code}")
    for symptom in patient.symptoms:
        if symptom.present and symptom.symptom_code:
            keys.add(f"symptom:{symptom.symptom_code}")
            # The observation evaluators fall back to patient.get_symptom for
            # observation leaves whose type is "symptom", so a present symptom
            # must satisfy that key shape too (adding keys is strictly
            # conservative — it can only keep more rules).
            keys.add(f"observation:symptom:{symptom.symptom_code}")
    for finding in patient.exam_findings:
        if finding.present and finding.exam_code and finding.finding_code:
            keys.add(f"finding:{finding.exam_code}:{finding.finding_code}")
    for observation in patient.observations:
        if observation.present and observation.code:
            keys.add(
                f"observation:{observation.observation_type or 'symptom'}:{observation.code}"
            )
    for vital in patient.vitals:
        if vital.vital_code:
            keys.add(f"vital:{vital.vital_code}")
    for code in patient.past_history or []:
        keys.add(f"past_history:{code}")
    for code in patient.current_medications or []:
        keys.add(f"medication:{code}")
    for code in (patient.clinical_attributes or {}):
        keys.add(f"attribute:{code}")
    return keys


def _leaf_key(condition: RuleCondition) -> Optional[str]:
    ctype = condition.type
    if ctype == "lab_concept" and condition.lab_code:
        return f"lab:{condition.lab_code}"
    if ctype == "exam_finding" and condition.exam_code and condition.finding_code:
        return f"finding:{condition.exam_code}:{condition.finding_code}"
    if ctype == "symptom" and condition.symptom_code:
        return f"symptom:{condition.symptom_code}"
    if ctype == "vital" and condition.vital_code:
        return f"vital:{condition.vital_code}"
    if ctype in {"past_history", "medication"} and condition.code:
        return f"{ctype}:{condition.code}"
    if ctype == "observation" and condition.code:
        if str(condition.concept_code or "").upper() == "ABSENT":
            # ABSENT 期望：数据缺失本身即匹配（matched = not observed），不可剪
            return None
        return f"observation:{condition.observation_type or 'symptom'}:{condition.code}"
    if ctype == "attribute" and condition.attribute_code:
        return f"attribute:{condition.attribute_code}"
    # evidence（按规则各自的图谱解析）、未知类型、缺编码 → 不可判定
    return None


def satisfiable(condition: RuleCondition, keys: Set[str]) -> bool:
    """True when the subtree can possibly match given the patient keys."""
    if condition.type in {"group", "weighted_group"}:
        op = (condition.operator or "AND").upper()
        children = list(condition.children or [])
        if condition.type == "weighted_group":
            # 评估器只以 required_children 作为硬条件：children 中带
            # required=True 的子项既不进 required_specs 也不进 main_children，
            # 完全不参与求值，故不可据此剪枝。
            hard = list(condition.required_children or [])
            return all(satisfiable(child, keys) for child in hard)
        if op == "AND":
            return all(satisfiable(child, keys) for child in children)
        if op == "OR":
            if not children:
                return True
            return any(satisfiable(child, keys) for child in children)
        return True  # NOT：数据缺失本身就是一种成立结果
    key = _leaf_key(condition)
    if key is None:
        return True
    return key in keys


class RuleIndex:
    """Pre-filters rules against a patient before full tree evaluation."""

    def __init__(self, rules: List[Rule]):
        self._rules = list(rules)

    def satisfiable_rule(self, rule: Rule, keys: Set[str]) -> bool:
        return satisfiable(rule.conditions, keys)
