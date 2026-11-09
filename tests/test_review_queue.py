import json
import tempfile
import unittest
from pathlib import Path

from src.review.queue import build_queue
from src.review.service import ReviewService


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        graph_dir = root / "kg"
        graph_dir.mkdir()
        rules_path = root / "rules" / "managed_rules.json"
        rules_path.parent.mkdir()
        audit_path = root / "review" / "review_log.jsonl"
        (graph_dir / "QUEUE.json").write_text(json.dumps({
            "schema_version": 1,
            "domain": "western",
            "anchor_types": ["disease"],
            "diseases": [{"code": "I21.0", "name_cn": "测试病"}],
            "evidences": [],
            "disease_evidence": [],
            "sources": [],
            "nodes": [
                {"id": "ENTITY:disease:I21.0", "node_type": "disease", "code": "I21.0", "name": "测试病", "review_status": "active"},
                {"id": "ENTITY:symptom:SYM_DRAFT", "node_type": "symptom", "code": "SYM_DRAFT", "name": "待审症状", "review_status": "draft"},
            ],
            "relations": [],
        }, ensure_ascii=False), encoding="utf-8")
        rules_path.write_text(json.dumps([{
            "rule_id": "R1",
            "name": "测试规则",
            "graph_filename": "QUEUE.json",
            "disease_code": "I21.0",
            "rule_type": "diagnosis",
            "status": "draft",
            "conditions": {
                "type": "group",
                "operator": "AND",
                "children": [{"type": "symptom", "symptom_code": "SYM_DRAFT"}],
            },
            "conclusion": {"diagnosis": "I21.0"},
        }], ensure_ascii=False), encoding="utf-8")
        self.service = ReviewService(graph_dir, rules_path, audit_path)

    def test_queue_contains_graph_and_rule_items(self):
        queue = build_queue(self.service, packages=[], graph_files=["QUEUE.json"])
        item_types = {item["item_type"] for item in queue["items"]}
        self.assertIn("graph_node", item_types)
        self.assertIn("rule", item_types)
        self.assertEqual(queue["summary"]["total"], len(queue["items"]))

    def test_queue_filters_status_and_search(self):
        queue = build_queue(
            self.service, packages=[], graph_files=["QUEUE.json"],
            status="draft", q="待审",
        )
        self.assertTrue(queue["items"])
        self.assertTrue(all(item["status"] == "draft" for item in queue["items"]))

    def test_queue_includes_dependency_summary_for_rules(self):
        queue = build_queue(self.service, packages=[], graph_files=["QUEUE.json"])
        rule = next(item for item in queue["items"] if item["item_type"] == "rule")
        self.assertEqual(rule["dependency_summary"]["total"], 3)
        self.assertTrue(rule["dependency_summary"]["blocked"])
