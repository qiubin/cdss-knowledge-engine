import copy
from src.review.rule_checks import condition_structure_issues
from src.api.routes.rules import check_rule_sync
from tests.test_graph_review_service import GRAPH, RULE


def test_empty_diagnosis_group_is_incomplete():
    rule = {**RULE, "conditions": {"type": "group", "operator": "AND", "children": []}}
    assert any("缺少子条件" in item for item in condition_structure_issues(rule))


def test_nested_empty_group_cannot_hide_inside_valid_root():
    rule = copy.deepcopy(RULE)
    rule["conditions"]["children"].append({"type": "group", "operator": "OR", "children": []})
    assert condition_structure_issues(rule)


def test_not_requires_exactly_one_child():
    child = {"type": "symptom", "symptom_code": "SYM_TEST"}
    rule = {**RULE, "conditions": {"type": "group", "operator": "NOT", "children": [child, child]}}
    assert any("只能包含一个" in item for item in condition_structure_issues(rule))


def test_existing_implicit_treatment_gate_is_not_called_missing_conditions():
    rule = {**RULE, "rule_type": "treatment", "conditions": {"type": "group", "operator": "AND", "children": []}}
    assert condition_structure_issues(rule) == []
    rule["conditions"]["operator"] = "OR"
    assert condition_structure_issues(rule)


def test_weighted_score_needs_a_threshold():
    rule = {**RULE, "conditions": {"type": "weighted_group", "children": [{"type": "symptom", "symptom_code": "SYM_TEST"}]}}
    assert any("评分阈值" in item for item in condition_structure_issues(rule))


def test_graph_sync_exposes_structural_blocker_to_single_and_bulk_review():
    rule = copy.deepcopy(RULE)
    rule["conditions"]["operator"] = "NOT"
    rule["conditions"]["children"] *= 2
    result = check_rule_sync([rule], graph_loader=lambda filename: GRAPH)["rules"][0]
    assert result["semantic_status"] == "inconsistent"
    assert any("只能包含一个" in item for item in result["issues"])
