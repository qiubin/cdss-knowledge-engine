import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

import src.review.service as review_service_module
from src.api.routes import rules as rules_module
from src.review.service import ReviewService


GRAPH = {
    "schema_version": 1,
    "domain": "western",
    "anchor_types": ["disease"],
    "diseases": [{"code": "I21.0", "name_cn": "测试病"}],
    "evidences": [],
    "disease_evidence": [],
    "sources": [],
    "nodes": [
        {"id": "ENTITY:disease:I21.0", "node_type": "disease", "code": "I21.0", "name": "测试病", "review_status": "active"},
        {"id": "ENTITY:symptom:SYM_TEST", "node_type": "symptom", "code": "SYM_TEST", "name": "测试症状", "review_status": "active"},
    ],
    "relations": [
        {
            "source_node_id": "ENTITY:disease:I21.0",
            "relation_code": "DISEASE_HAS_SYMPTOM",
            "target_node_id": "ENTITY:symptom:SYM_TEST",
            "review_status": "stale",
        }
    ],
}


def make_rule(rule_id="R1", status="blocked", symptom_code="SYM_TEST"):
    return {
        "rule_id": rule_id,
        "name": "测试规则",
        "graph_filename": "DEPS.json",
        "disease_code": "I21.0",
        "rule_type": "diagnosis",
        "status": status,
        "conditions": {
            "type": "group",
            "operator": "AND",
            "children": [{"type": "symptom", "symptom_code": symptom_code}],
        },
        "conclusion": {"diagnosis": "I21.0"},
    }


class ApproveDependenciesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        graph_dir = root / "kg"
        graph_dir.mkdir()
        rules_path = root / "rules" / "managed_rules.json"
        rules_path.parent.mkdir()
        (graph_dir / "DEPS.json").write_text(json.dumps(GRAPH, ensure_ascii=False), encoding="utf-8")
        self.rules_path = rules_path
        self.rules_path.write_text(json.dumps([make_rule()], ensure_ascii=False), encoding="utf-8")
        self.service = ReviewService(graph_dir, rules_path, root / "review" / "review_log.jsonl")

        self.original_service = review_service_module.ReviewService
        self.original_load = rules_module._load_all_rules
        self.original_save = rules_module._save_all_rules
        self.original_gate = rules_module._dependency_result
        review_service_module.ReviewService = lambda **kwargs: self.service
        rules_module._load_all_rules = lambda path=None: json.loads(self.rules_path.read_text(encoding="utf-8")) if self.rules_path.exists() else []
        rules_module._save_all_rules = lambda rules, path=None: self.rules_path.write_text(
            json.dumps(rules, ensure_ascii=False), encoding="utf-8"
        )
        rules_module._dependency_result = lambda rule, graph_loader=None: rules_module.check_rule_sync(
            [rule], graph_loader=self.service._load_graph
        )["rules"][0]
        self.addCleanup(setattr, review_service_module, "ReviewService", self.original_service)
        self.addCleanup(setattr, rules_module, "_load_all_rules", self.original_load)
        self.addCleanup(setattr, rules_module, "_save_all_rules", self.original_save)
        self.addCleanup(setattr, rules_module, "_dependency_result", self.original_gate)

    def test_blocked_rule_deps_approved_and_rule_reactivated(self):
        result = asyncio.run(rules_module.approve_rule_dependencies(
            "R1", rules_module.RuleApproveDependenciesRequest(reviewer="张医生")
        ))
        self.assertGreaterEqual(result["approved_relations"], 1)
        self.assertEqual(result["dependency_status"], "passed")
        saved = json.loads(self.rules_path.read_text(encoding="utf-8"))[0]
        self.assertEqual(saved["status"], "active")

    def test_draft_rule_deps_approved_but_stays_draft(self):
        self.rules_path.write_text(
            json.dumps([make_rule(status="draft")], ensure_ascii=False), encoding="utf-8"
        )
        result = asyncio.run(rules_module.approve_rule_dependencies(
            "R1", rules_module.RuleApproveDependenciesRequest(reviewer="张医生")
        ))
        self.assertEqual(result["dependency_status"], "passed")
        saved = json.loads(self.rules_path.read_text(encoding="utf-8"))[0]
        self.assertEqual(saved["status"], "draft")

    def test_missing_dependency_reported_and_rule_stays_blocked(self):
        rule = make_rule()
        rule["conditions"] = {
            "type": "group",
            "operator": "AND",
            "children": [
                {"type": "symptom", "symptom_code": "SYM_TEST"},
                {"type": "symptom", "symptom_code": "SYM_MISSING"},
            ],
        }
        self.rules_path.write_text(json.dumps([rule], ensure_ascii=False), encoding="utf-8")
        result = asyncio.run(rules_module.approve_rule_dependencies(
            "R1", rules_module.RuleApproveDependenciesRequest(reviewer="张医生")
        ))
        self.assertTrue(result["missing"])
        saved = json.loads(self.rules_path.read_text(encoding="utf-8"))[0]
        self.assertEqual(saved["status"], "blocked")
