# tests/test_drl_compiler.py
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.engine.drools.compiler import (
    compile_rule,
    compile_rule_set,
    walk_leaves,
)
from src.models.rule import Rule, RuleCondition, RuleConclusion


def _rule(rule_id, conditions, priority=50):
    return Rule(
        rule_id=rule_id, disease_code="X", disease_name="X", priority=priority,
        conditions=RuleCondition(**conditions),
        conclusion=RuleConclusion(diagnosis="X"),
    )


def test_compiles_and_group_of_or_and_leaf():
    rule = _rule("T1", {
        "type": "group", "operator": "AND", "children": [
            {"type": "group", "operator": "OR", "children": [
                {"type": "lab_concept", "lab_code": "CTN", "concept_code": "ELEVATED"},
                {"type": "lab_concept", "lab_code": "CTN", "concept_code": "ABOVE_ULN_1"},
            ]},
            {"type": "symptom", "symptom_code": "SYM1"},
        ],
    })
    compiled = compile_rule(rule)
    assert compiled is not None
    assert [c.type for c in compiled.leaves] == ["lab_concept", "lab_concept", "symptom"]
    assert 'rule "T1"' in compiled.drl
    assert "salience 50" in compiled.drl
    assert '( Key( ruleId == "T1", code == "a0" ) or Key( ruleId == "T1", code == "a1" ) )' in compiled.drl
    assert 'Key( ruleId == "T1", code == "a2" )' in compiled.drl
    assert 'matched.add("T1");' in compiled.drl


def test_compiles_not_group():
    rule = _rule("T2", {
        "type": "group", "operator": "NOT", "children": [
            {"type": "symptom", "symptom_code": "S1"},
        ],
    })
    compiled = compile_rule(rule)
    assert compiled is not None
    assert 'not ( Key( ruleId == "T2", code == "a0" ) )' in compiled.drl


def test_single_leaf_rule():
    rule = _rule("T3", {"type": "lab_concept", "lab_code": "MYO", "concept_code": "ELEVATED"})
    compiled = compile_rule(rule)
    assert compiled is not None
    assert compiled.drl.strip().startswith('rule "T3"')


def test_unsupported_types_return_none():
    assert compile_rule(_rule("W1", {"type": "weighted_group", "children": [
        {"type": "observation", "observation_type": "tongue", "code": "T1"}]})) is None
    assert compile_rule(_rule("W2", {"type": "fact", "fact_type": "syndrome", "code": "S1"})) is None
    assert compile_rule(_rule("W3", {"type": "evidence", "evidence_id": "EV"})) is None
    assert compile_rule(_rule("W4", {"type": "group", "operator": "AND", "children": []})) is None


def test_rule_set_includes_header():
    compiled = compile_rule(_rule("T3", {"type": "symptom", "symptom_code": "S"}))
    drl = compile_rule_set([compiled])
    assert drl.startswith("package cdss;")
    assert "global java.util.List matched;" in drl
    assert "import dev.cdss.sidecar.Key" in drl


def test_walk_order_matches_lhs_ids():
    rule = _rule("T5", {
        "type": "group", "operator": "AND", "children": [
            {"type": "symptom", "symptom_code": "A"},
            {"type": "symptom", "symptom_code": "B"},
            {"type": "symptom", "symptom_code": "C"},
        ],
    })
    leaves = walk_leaves(rule.conditions)
    assert [leaf.symptom_code for leaf in leaves] == ["A", "B", "C"]
    compiled = compile_rule(rule)
    assert compiled.leaves == leaves
