# src/engine/drools/compiler.py
"""Compile native JSON rule condition trees into Drools DRL.

Atoms (leaf conditions) are pre-evaluated by the Python engine; the sidecar
only evaluates the boolean structure.  A Key fact is inserted for every
satisfied atom, so LHS patterns test presence:

    Key( ruleId == "R1", code == "a0" )

NOT groups use ``not`` (absence of the fact = not happened), OR groups use a
nested ``or`` CE.  Condition types that need weighted scoring or per-rule
graph resolution are NOT compilable; the adapter evaluates those natively.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ...models.rule import Rule, RuleCondition

SUPPORTED_LEAF_TYPES = {
    "lab_concept", "exam_finding", "symptom", "vital", "demographic",
    "past_history", "medication", "observation", "attribute",
}

DRL_HEADER = """package cdss;

global java.util.List matched;

declare Key
    ruleId : String
    code : String
end

"""


@dataclass
class CompiledRule:
    rule_id: str
    salience: int
    drl: str
    leaves: List[RuleCondition] = field(default_factory=list)


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def walk_leaves(condition: RuleCondition) -> Optional[List[RuleCondition]]:
    """Pre-order leaf list; None when the tree contains an uncompilable node."""
    ctype = condition.type
    if ctype == "group":
        children = list(condition.children or [])
        if not children:
            return None
        leaves: List[RuleCondition] = []
        for child in children:
            child_leaves = walk_leaves(child)
            if child_leaves is None:
                return None
            leaves.extend(child_leaves)
        return leaves
    if ctype not in SUPPORTED_LEAF_TYPES:
        return None
    return [condition]


def _lhs(condition: RuleCondition, rule_id: str, counter: List[int]) -> Optional[str]:
    ctype = condition.type
    if ctype == "group":
        children = list(condition.children or [])
        if not children:
            return None
        parts = []
        for child in children:
            part = _lhs(child, rule_id, counter)
            if part is None:
                return None
            parts.append(part)
        op = (condition.operator or "AND").upper()
        if op == "AND":
            return "\n    ".join(parts)
        if op == "OR":
            return "( " + " or ".join(parts) + " )"
        if op == "NOT":
            return "not ( " + " or ".join(parts) + " )"
        return None
    if ctype not in SUPPORTED_LEAF_TYPES:
        return None
    index = counter[0]
    counter[0] += 1
    return f'Key( ruleId == "{_escape(rule_id)}", code == "a{index}" )'


def compile_rule(rule: Rule) -> Optional[CompiledRule]:
    leaves = walk_leaves(rule.conditions)
    if not leaves:
        return None
    counter = [0]
    lhs = _lhs(rule.conditions, rule.rule_id, counter)
    if lhs is None:
        return None
    drl = (
        f'rule "{_escape(rule.rule_id)}"\n'
        f"salience {int(rule.priority)}\n"
        "when\n"
        f"    {lhs}\n"
        "then\n"
        f'    matched.add("{_escape(rule.rule_id)}");\n'
        "end"
    )
    return CompiledRule(
        rule_id=rule.rule_id,
        salience=int(rule.priority),
        drl=drl,
        leaves=leaves,
    )


def compile_rule_set(compiled: List[CompiledRule]) -> str:
    return DRL_HEADER + "\n".join(item.drl for item in compiled) + "\n"
