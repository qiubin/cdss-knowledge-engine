import json
import tempfile
import unittest
from pathlib import Path

from src.api.routes.rules import GraphSemanticChecker, check_rule_sync
from src.review.condition_completion import (
    build_condition_tree,
    collect_disease_evidence_conditions,
    complete_rule_conditions,
    normalize_condition_logic,
    unresolved_logic_questions,
)


def graph_with_evidence():
    return {
        "domain": "western",
        "anchor_types": ["disease"],
        "diseases": [{"code": "I21.0", "name_cn": "测试病"}],
        "nodes": [
            {"id": "ENTITY:disease:I21.0", "node_type": "disease", "code": "I21.0", "name": "测试病", "review_status": "active"},
            {"id": "ENTITY:lab:CTN", "node_type": "lab", "code": "CTN", "name": "肌钙蛋白", "review_status": "active"},
            {"id": "ENTITY:diagnostic_evidence:CTN:ELEVATED", "node_type": "diagnostic_evidence", "code": "CTN:ELEVATED", "name": "肌钙蛋白升高", "review_status": "active"},
            {"id": "ENTITY:symptom:SYM_TEST", "node_type": "symptom", "code": "SYM_TEST", "name": "测试症状", "review_status": "active"},
            {"id": "ENTITY:diagnostic_evidence:FIND_TEST", "node_type": "diagnostic_evidence", "code": "FIND_TEST", "name": "测试发现", "review_status": "active"},
        ],
        "relations": [
            {"source_node_id": "ENTITY:disease:I21.0", "relation_code": "DISEASE_HAS_LAB", "target_node_id": "ENTITY:lab:CTN", "review_status": "active"},
            {"source_node_id": "ENTITY:lab:CTN", "relation_code": "LAB_HAS_DIAGNOSTIC_EVIDENCE", "target_node_id": "ENTITY:diagnostic_evidence:CTN:ELEVATED", "review_status": "active"},
            {"source_node_id": "ENTITY:disease:I21.0", "relation_code": "DISEASE_HAS_DIAGNOSTIC_EVIDENCE", "target_node_id": "ENTITY:diagnostic_evidence:CTN:ELEVATED", "review_status": "active"},
            {"source_node_id": "ENTITY:disease:I21.0", "relation_code": "DISEASE_HAS_SYMPTOM", "target_node_id": "ENTITY:symptom:SYM_TEST", "review_status": "active"},
            {"source_node_id": "ENTITY:disease:I21.0", "relation_code": "DISEASE_HAS_DIAGNOSTIC_EVIDENCE", "target_node_id": "ENTITY:diagnostic_evidence:FIND_TEST", "review_status": "active"},
        ],
        "evidences": [
            {"id": "EV-1", "data_type": "lab", "lab_code": "CTN", "concept_code": "ELEVATED", "description": "肌钙蛋白升高"},
            {"id": "EV-2", "data_type": "symptom", "symptom_code": "SYM_TEST", "description": "测试症状"},
            {"id": "EV-3", "data_type": "exam_finding", "finding_code": "FIND_TEST", "description": "测试发现"},
            {"id": "EV-4", "data_type": "lab", "lab_code": "CTN", "concept_code": "NOT_IN_VOCAB", "description": "词表外概念"},
        ],
        "disease_evidence": [
            {"disease_code": "I21.0", "evidence_id": "EV-1", "weight": 0.9, "required": True},
            {"disease_code": "I21.0", "evidence_id": "EV-2", "weight": 0.5, "required": False},
            {"disease_code": "I21.0", "evidence_id": "EV-3", "weight": 0.5, "required": False},
            {"disease_code": "I21.0", "evidence_id": "EV-4", "weight": 0.5, "required": False},
        ],
        "sources": [],
    }


def empty_rule():
    return {
        "rule_id": "R1",
        "graph_filename": "COMPLETE.json",
        "disease_code": "I21.0",
        "rule_type": "diagnosis",
        "status": "draft",
        "conditions": {"type": "group", "operator": "AND", "children": []},
        "conclusion": {"diagnosis": "I21.0"},
    }


class CollectTest(unittest.TestCase):
    def test_reviewed_edges_work_without_legacy_evidence_map(self):
        graph = graph_with_evidence()
        graph["evidences"] = []
        graph["disease_evidence"] = []
        collected = collect_disease_evidence_conditions(graph, "I21.0", approved_only=True)
        self.assertTrue(collected["symptoms"])
        self.assertTrue(collected["objective"])

    def test_collect_maps_lab_symptom_and_finding(self):
        collected = collect_disease_evidence_conditions(graph_with_evidence(), "I21.0")
        types = sorted(c["type"] for c in collected["symptoms"] + collected["objective"])
        self.assertEqual(types, ["exam_finding", "lab_concept", "symptom"])
        lab = next(c for c in collected["objective"] if c["type"] == "lab_concept")
        self.assertEqual(lab["lab_code"], "CTN")
        self.assertEqual(lab["concept_code"], "ELEVATED")

    def test_collect_skips_vocabulary_unknown_concept(self):
        collected = collect_disease_evidence_conditions(graph_with_evidence(), "I21.0")
        skipped_reasons = " ".join(item["reason"] for item in collected["skipped"])
        self.assertIn("NOT_IN_VOCAB", skipped_reasons)

    def test_approved_only_excludes_condition_with_draft_relation(self):
        graph = graph_with_evidence()
        symptom_relation = next(
            item for item in graph["relations"]
            if item["relation_code"] == "DISEASE_HAS_SYMPTOM"
        )
        symptom_relation["review_status"] = "draft"
        collected = collect_disease_evidence_conditions(
            graph, "I21.0", approved_only=True
        )
        self.assertFalse(any(item["type"] == "symptom" for item in collected["symptoms"]))
        self.assertIn("尚未全部审核通过", " ".join(item["reason"] for item in collected["skipped"]))


class BuildTreeTest(unittest.TestCase):
    def test_builds_and_tree_with_or_branches(self):
        collected = collect_disease_evidence_conditions(graph_with_evidence(), "I21.0")
        tree = build_condition_tree(collected)
        self.assertEqual(tree["operator"], "AND")
        child_ops = sorted(child["operator"] for child in tree["children"])
        self.assertEqual(child_ops, ["OR", "OR"])


class CompleteRuleTest(unittest.TestCase):
    def test_legacy_any_and_single_child_groups_are_normalized(self):
        fixed, changed = normalize_condition_logic({
            "type": "group", "operator": "ANY", "children": [
                {"type": "symptom", "symptom_code": "A"},
                {"type": "symptom", "symptom_code": "B"},
            ],
        })
        self.assertTrue(changed)
        self.assertEqual(fixed["operator"], "OR")
        single, changed = normalize_condition_logic({
            "type": "group", "operator": None,
            "children": [{"type": "symptom", "symptom_code": "A"}],
        })
        self.assertTrue(changed)
        self.assertEqual(single["type"], "symptom")

    def test_ambiguous_multi_condition_group_becomes_reviewer_question(self):
        conditions = {
            "type": "group", "operator": None, "children": [
                {"type": "symptom", "symptom_code": "A"},
                {"type": "symptom", "symptom_code": "B"},
            ],
        }
        questions = unresolved_logic_questions(conditions)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["path"], "conditions")
        self.assertEqual(questions[0]["child_count"], 2)

    def test_reviewed_associations_do_not_add_mandatory_clause(self):
        rule = empty_rule()
        rule["conditions"] = {"type": "symptom", "symptom_code": "SYM_TEST"}
        result = complete_rule_conditions(rule, graph_with_evidence(), approved_only=True)
        self.assertFalse(result["changed"])
        self.assertTrue(result["candidates"])
        self.assertEqual(rule["conditions"], {"type": "symptom", "symptom_code": "SYM_TEST"})

    def test_empty_group_replaced_and_becomes_consistent(self):
        rule = empty_rule()
        result = complete_rule_conditions(rule, graph_with_evidence())
        self.assertTrue(result["changed"])
        checker = GraphSemanticChecker(graph_with_evidence())
        probe = {**rule, "conditions": result["conditions"]}
        self.assertEqual(checker.check_rule(probe)["semantic_status"], "consistent")

    def test_symptom_missing_code_gets_filled(self):
        rule = empty_rule()
        rule["conditions"] = {
            "type": "group", "operator": "AND",
            "children": [{"type": "symptom"}],
        }
        result = complete_rule_conditions(rule, graph_with_evidence())
        self.assertTrue(result["changed"])
        symptom = result["conditions"]["children"][0]["children"][0] \
            if result["conditions"].get("children") and result["conditions"]["children"][0].get("type") == "group" \
            else result["conditions"]["children"][0]
        self.assertEqual(symptom.get("symptom_code"), "SYM_TEST")

    def test_evidence_missing_id_replaced(self):
        rule = empty_rule()
        rule["conditions"] = {"type": "evidence"}
        result = complete_rule_conditions(rule, graph_with_evidence())
        self.assertTrue(result["changed"])
        text = json.dumps(result["conditions"])
        self.assertNotIn('"type": "evidence"', text)

    def test_rule_without_graph_evidence_reports_reason(self):
        rule = empty_rule()
        rule["disease_code"] = "I99.9"
        result = complete_rule_conditions(rule, graph_with_evidence())
        self.assertFalse(result["changed"])
        self.assertTrue(result.get("reason"))
        self.assertFalse(result["ready"])

    def test_existing_conditions_remain_editable_without_new_graph_evidence(self):
        rule = empty_rule()
        rule["disease_code"] = "I99.9"
        rule["conditions"] = {
            "type": "group", "operator": None,
            "children": [
                {"type": "symptom", "symptom_code": "A"},
                {"type": "symptom", "symptom_code": "B"},
            ],
        }
        result = complete_rule_conditions(rule, graph_with_evidence())
        self.assertTrue(result["ready"])
        self.assertEqual(len(result["logic_questions"]), 1)

    def test_legacy_logic_is_repaired_without_new_graph_evidence(self):
        rule = empty_rule()
        rule["disease_code"] = "I99.9"
        rule["conditions"] = {
            "type": "group", "operator": "ANY",
            "children": [
                {"type": "symptom", "symptom_code": "A"},
                {"type": "symptom", "symptom_code": "B"},
            ],
        }
        result = complete_rule_conditions(rule, graph_with_evidence())
        self.assertTrue(result["changed"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["conditions"]["operator"], "OR")

    def test_existing_incomplete_leaf_replaced_and_consistent(self):
        rule = empty_rule()
        rule["conditions"] = {
            "type": "group", "operator": "AND",
            "children": [
                {"type": "symptom"},
                {"type": "evidence"},
            ],
        }
        result = complete_rule_conditions(rule, graph_with_evidence())
        self.assertTrue(result["changed"])
        probe = {**rule, "conditions": result["conditions"]}
        sync = check_rule_sync([probe], graph_loader=lambda name: graph_with_evidence())
        self.assertEqual(sync["rules"][0]["semantic_status"], "consistent")
