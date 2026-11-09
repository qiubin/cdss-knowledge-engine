import unittest

from src.api.routes.rules import GraphSemanticChecker, check_rule_sync
from src.domains.tcm.checks import TcmGraphSemanticChecker


def western_graph(node_status="active"):
    return {
        "domain": "western",
        "diseases": [{"code": "I21.0", "name_cn": "测试病"}],
        "nodes": [
            {"id": "ENTITY:disease:I21.0", "node_type": "disease", "code": "I21.0", "name": "测试病", "review_status": node_status},
            {"id": "ENTITY:lab:CTN", "node_type": "lab", "code": "CTN", "name": "肌钙蛋白", "review_status": node_status},
            {"id": "ENTITY:diagnostic_evidence:CTN:ELEVATED", "node_type": "diagnostic_evidence", "code": "CTN:ELEVATED", "name": "肌钙蛋白升高", "review_status": node_status},
        ],
        "relations": [
            {"source_node_id": "ENTITY:disease:I21.0", "relation_code": "DISEASE_HAS_LAB", "target_node_id": "ENTITY:lab:CTN", "review_status": node_status},
            {"source_node_id": "ENTITY:lab:CTN", "relation_code": "LAB_HAS_DIAGNOSTIC_EVIDENCE", "target_node_id": "ENTITY:diagnostic_evidence:CTN:ELEVATED", "review_status": node_status},
            {"source_node_id": "ENTITY:disease:I21.0", "relation_code": "DISEASE_HAS_DIAGNOSTIC_EVIDENCE", "target_node_id": "ENTITY:diagnostic_evidence:CTN:ELEVATED", "review_status": node_status},
        ],
        "evidences": [],
        "disease_evidence": [],
    }


def lab_rule():
    return {
        "rule_id": "R1",
        "graph_filename": "TEST.json",
        "disease_code": "I21.0",
        "disease_name": "测试病",
        "rule_type": "diagnosis",
        "status": "draft",
        "conditions": {
            "type": "group",
            "operator": "AND",
            "children": [{"type": "lab_concept", "lab_code": "CTN", "concept_code": "ELEVATED"}],
        },
        "conclusion": {"diagnosis": "I21.0"},
    }


class WesternDependencyTest(unittest.TestCase):
    def test_lab_rule_reports_nodes_and_relations(self):
        result = GraphSemanticChecker(western_graph("active")).check_rule(lab_rule())
        self.assertEqual(result["dependency_status"], "passed")
        node_ids = {ref["node_id"] for ref in result["references"] if ref["kind"] == "node"}
        self.assertIn("ENTITY:disease:I21.0", node_ids)
        self.assertIn("ENTITY:lab:CTN", node_ids)
        self.assertIn("ENTITY:diagnostic_evidence:CTN:ELEVATED", node_ids)
        relation_codes = {ref["relation_code"] for ref in result["references"] if ref["kind"] == "relation"}
        self.assertIn("DISEASE_HAS_LAB", relation_codes)
        self.assertIn("LAB_HAS_DIAGNOSTIC_EVIDENCE", relation_codes)

    def test_draft_dependency_blocks_rule(self):
        result = GraphSemanticChecker(western_graph("draft")).check_rule(lab_rule())
        self.assertEqual(result["dependency_status"], "blocked")
        self.assertTrue(result["review_issues"])

    def test_sync_includes_dependency_counts(self):
        result = check_rule_sync([lab_rule()], graph_loader=lambda filename: western_graph("draft"))
        self.assertEqual(result["dependency_passed_count"], 0)
        self.assertEqual(result["dependency_blocked_count"], 1)


def tcm_graph():
    return {
        "domain": "tcm",
        "syndromes": [{"code": "SYN_QI_YIN_XU", "name_cn": "气阴两虚证"}],
        "nodes": [
            {"id": "ENTITY:syndrome:SYN_QI_YIN_XU", "node_type": "syndrome", "code": "SYN_QI_YIN_XU", "name": "气阴两虚证", "review_status": "active"},
            {"id": "ENTITY:symptom:SYM_FATIGUE", "node_type": "symptom", "code": "SYM_FATIGUE", "name": "乏力", "review_status": "active"},
        ],
        "relations": [],
        "diseases": [],
    }


def tcm_rule():
    return {
        "rule_id": "T1",
        "graph_filename": "TCM.json",
        "domain": "tcm",
        "anchor_type": "syndrome",
        "disease_code": "SYN_QI_YIN_XU",
        "rule_type": "tcm_pattern",
        "status": "draft",
        "conditions": {
            "type": "group",
            "operator": "OR",
            "children": [{"type": "observation", "observation_type": "symptom", "code": "SYM_FATIGUE"}],
        },
        "conclusion": {"diagnosis": "SYN_QI_YIN_XU"},
    }


class TcmDependencyTest(unittest.TestCase):
    def test_observation_rule_reports_syndrome_and_symptom(self):
        result = TcmGraphSemanticChecker(tcm_graph()).check_rule(tcm_rule())
        node_ids = {ref["node_id"] for ref in result["references"] if ref["kind"] == "node"}
        self.assertIn("ENTITY:syndrome:SYN_QI_YIN_XU", node_ids)
        self.assertIn("ENTITY:symptom:SYM_FATIGUE", node_ids)
        self.assertEqual(result["dependency_status"], "passed")
