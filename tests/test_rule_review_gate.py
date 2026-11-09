import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from src.api.routes import rules as rules_module


class RuleGateTest(unittest.TestCase):
    def setUp(self):
        self.graph = {
            "domain": "western",
            "diseases": [{"code": "I21.0", "name_cn": "测试病"}],
            "nodes": [
                {"id": "ENTITY:disease:I21.0", "node_type": "disease", "code": "I21.0", "name": "测试病", "review_status": "active"},
                {"id": "ENTITY:symptom:SYM_TEST", "node_type": "symptom", "code": "SYM_TEST", "name": "测试症状", "review_status": "draft"},
            ],
            "relations": [
                {"source_node_id": "ENTITY:disease:I21.0", "relation_code": "DISEASE_HAS_SYMPTOM", "target_node_id": "ENTITY:symptom:SYM_TEST", "review_status": "draft"},
            ],
            "evidences": [],
            "disease_evidence": [],
        }
        self.rule = {
            "rule_id": "R1",
            "graph_filename": "GATE.json",
            "disease_code": "I21.0",
            "rule_type": "diagnosis",
            "status": "draft",
            "conditions": {
                "type": "group",
                "operator": "AND",
                "children": [{"type": "symptom", "symptom_code": "SYM_TEST"}],
            },
            "conclusion": {"diagnosis": "I21.0"},
        }

    def test_dependency_gate_blocks_draft_symptom(self):
        result = rules_module.check_rule_sync(
            [self.rule], graph_loader=lambda name: self.graph
        )["rules"][0]
        self.assertEqual(result["dependency_status"], "blocked")
        blockers = rules_module._activation_blockers(result)
        self.assertTrue(blockers)

    def test_dependency_gate_passes_when_all_active(self):
        for node in self.graph["nodes"]:
            node["review_status"] = "active"
        self.graph["relations"][0]["review_status"] = "active"
        result = rules_module.check_rule_sync(
            [self.rule], graph_loader=lambda name: self.graph
        )["rules"][0]
        self.assertEqual(result["dependency_status"], "passed")
        self.assertEqual(rules_module._activation_blockers(result), [])


class RuleReviewEndpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.rules = [{
            "rule_id": "R1",
            "graph_filename": "GATE.json",
            "disease_code": "I21.0",
            "rule_type": "diagnosis",
            "status": "draft",
            "conditions": {"type": "group", "operator": "AND", "children": []},
            "conclusion": {"diagnosis": "I21.0"},
        }]
        self.audit_path = root / "review_log.jsonl"
        self.original_load = rules_module._load_all_rules
        self.original_save = rules_module._save_all_rules
        self.original_gate = rules_module._dependency_result
        self.original_audit = rules_module.REVIEW_AUDIT_PATH
        rules_module._load_all_rules = lambda path=None: self.rules
        rules_module._save_all_rules = lambda rules, path=None: None
        rules_module.REVIEW_AUDIT_PATH = self.audit_path
        self.addCleanup(setattr, rules_module, "_load_all_rules", self.original_load)
        self.addCleanup(setattr, rules_module, "_save_all_rules", self.original_save)
        self.addCleanup(setattr, rules_module, "_dependency_result", self.original_gate)
        self.addCleanup(setattr, rules_module, "REVIEW_AUDIT_PATH", self.original_audit)

    def set_gate(self, passed):
        rules_module._dependency_result = lambda rule, graph_loader=None: {
            "rule_id": rule["rule_id"],
            "semantic_status": "consistent",
            "issues": [],
            "references": [] if passed else [
                {"kind": "node", "node_id": "ENTITY:symptom:X", "status": "draft"}
            ],
            "dependency_status": "passed" if passed else "blocked",
            "review_issues": [] if passed else ["节点未审核"],
        }

    def test_review_rejects_rule_with_unapproved_dependency(self):
        self.set_gate(False)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(rules_module.review_rule(
                "R1", rules_module.RuleReviewRequest(action="approve", reviewer="张医生")
            ))
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(self.rules[0]["status"], "draft")

    def test_review_activates_rule_when_dependencies_pass(self):
        self.set_gate(True)
        result = asyncio.run(rules_module.review_rule(
            "R1", rules_module.RuleReviewRequest(action="approve", reviewer="张医生", note="确认")
        ))
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["reviewed_by"], "张医生")
        self.assertTrue(self.audit_path.exists())
        self.assertIn("张医生", self.audit_path.read_text(encoding="utf-8"))
