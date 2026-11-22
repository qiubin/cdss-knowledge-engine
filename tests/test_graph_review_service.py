import json
import tempfile
import unittest
from pathlib import Path

from src.review.errors import ReviewError
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
        {"id": "ENTITY:symptom:SYM_DRAFT", "node_type": "symptom", "code": "SYM_DRAFT", "name": "待审症状", "review_status": "draft"},
    ],
    "relations": [
        {
            "source_node_id": "ENTITY:disease:I21.0",
            "relation_code": "DISEASE_HAS_SYMPTOM",
            "target_node_id": "ENTITY:symptom:SYM_TEST",
            "relation_name": "疾病有症状",
            "review_status": "active",
        },
        {
            "source_node_id": "ENTITY:disease:I21.0",
            "relation_code": "DISEASE_HAS_SYMPTOM",
            "target_node_id": "ENTITY:symptom:SYM_DRAFT",
            "relation_name": "疾病有症状",
            "review_status": "draft",
        }
    ],
}

RULE = {
    "rule_id": "R1",
    "name": "测试规则",
    "graph_filename": "REVIEW-TEST.json",
    "disease_code": "I21.0",
    "disease_name": "测试病",
    "rule_type": "diagnosis",
    "status": "active",
    "conditions": {
        "type": "group",
        "operator": "AND",
        "children": [{"type": "symptom", "symptom_code": "SYM_TEST"}],
    },
    "conclusion": {"diagnosis": "I21.0", "confidence": "high"},
}


class ReviewServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.graph_dir = root / "kg"
        self.graph_dir.mkdir()
        self.rules_path = root / "rules" / "managed_rules.json"
        self.rules_path.parent.mkdir()
        self.audit_path = root / "review" / "review_log.jsonl"
        (self.graph_dir / "REVIEW-TEST.json").write_text(
            json.dumps(GRAPH, ensure_ascii=False), encoding="utf-8"
        )
        self.rules_path.write_text(json.dumps([RULE], ensure_ascii=False), encoding="utf-8")
        self.service = ReviewService(self.graph_dir, self.rules_path, self.audit_path)

    def test_summary_counts_statuses(self):
        summary = self.service.summary("REVIEW-TEST.json")
        self.assertEqual(summary["nodes"]["active"], 2)
        self.assertEqual(summary["nodes"]["draft"], 1)
        self.assertEqual(summary["relations"]["active"], 1)

    def test_approve_draft_node_writes_audit(self):
        result = self.service.review_node(
            "REVIEW-TEST.json", "ENTITY:symptom:SYM_DRAFT", "approve", "张医生", "确认"
        )
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["item"]["reviewed_by"], "张医生")
        audit = self.service.audit_records(entity_id="ENTITY:symptom:SYM_DRAFT")
        self.assertEqual(audit[0]["action"], "approve")

    def test_relation_approval_requires_active_endpoints(self):
        with self.assertRaises(ReviewError) as ctx:
            self.service.review_relation(
                "REVIEW-TEST.json",
                "ENTITY:disease:I21.0", "DISEASE_HAS_SYMPTOM", "ENTITY:symptom:SYM_DRAFT",
                "approve", "张医生",
            )
        self.assertEqual(ctx.exception.status_code, 422)

    def test_tcm_membership_approval_also_approves_pending_endpoints(self):
        filename = "REVIEW-TCM.json"
        disease_id = "ENTITY:disease:TCM_TEST"
        syndrome_id = "ENTITY:syndrome:SYNDROME_TEST"
        other_syndrome_id = "ENTITY:syndrome:SYNDROME_OTHER"
        code = "DISEASE_HAS_SYNDROME"
        graph = {
            "domain": "tcm",
            "nodes": [
                {"id": disease_id, "node_type": "disease", "name": "心衰", "review_status": "draft"},
                {"id": syndrome_id, "node_type": "syndrome", "name": "气虚血瘀证", "review_status": "rejected"},
                {"id": other_syndrome_id, "node_type": "syndrome", "name": "安全性证", "review_status": "draft"},
            ],
            "relations": [{
                "source_node_id": disease_id, "relation_code": code,
                "target_node_id": syndrome_id, "review_status": "draft",
            }, {
                "source_node_id": disease_id, "relation_code": code,
                "target_node_id": other_syndrome_id, "review_status": "draft",
            }],
        }
        path = self.graph_dir / filename
        path.write_text(json.dumps(graph), encoding="utf-8")
        with self.assertRaises(ReviewError):
            self.service.review_relation(filename, disease_id, code, syndrome_id, "approve", "审核员")
        saved = self.service._load_graph(filename)
        self.assertEqual(saved["nodes"][disease_id]["review_status"], "draft")
        self.assertEqual(saved["relations"][(disease_id, code, syndrome_id)]["review_status"], "draft")

        graph["nodes"][1]["review_status"] = "stale"
        path.write_text(json.dumps(graph), encoding="utf-8")
        detail = self.service.relation_group_detail(filename, disease_id)
        self.assertTrue(detail["sections"][0]["items"][0]["approves_endpoints"])
        result = self.service.review_relation(filename, disease_id, code, syndrome_id, "approve", "审核员")
        self.assertEqual(result["status"], "active")
        saved = self.service._load_graph(filename)
        for endpoint in (disease_id, syndrome_id):
            self.assertEqual(saved["nodes"][endpoint]["review_status"], "active")
            audit = self.service.audit_records(entity_id=endpoint)
            self.assertEqual(audit[0]["trigger_relation_id"], f"{disease_id}|{code}|{syndrome_id}")

        result = self.service.review_relation(
            filename, disease_id, code, other_syndrome_id, "reject", "审核员",
        )
        self.assertEqual(result["status"], "rejected")
        saved = self.service._load_graph(filename)
        self.assertEqual(saved["nodes"][disease_id]["review_status"], "active")
        self.assertEqual(saved["nodes"][syndrome_id]["review_status"], "active")
        self.assertEqual(saved["nodes"][other_syndrome_id]["review_status"], "draft")
        self.assertEqual(saved["relations"][(disease_id, code, syndrome_id)]["review_status"], "active")
        self.assertEqual(saved["relations"][(disease_id, code, other_syndrome_id)]["review_status"], "rejected")

    def test_retiring_node_blocks_dependent_active_rule(self):
        result = self.service.review_node(
            "REVIEW-TEST.json", "ENTITY:symptom:SYM_TEST", "retire", "张医生", "指南更新"
        )
        self.assertIn("R1", result["affected_rules"])
        rules = json.loads(self.rules_path.read_text(encoding="utf-8"))
        self.assertEqual(rules[0]["status"], "blocked")
        reasons = {item["reason"] for item in rules[0]["blocked_by"]}
        self.assertIn("draft", reasons)
        self.assertIn("retired", reasons)

    def test_impact_counts_rules(self):
        impact = self.service.impact_for_node("REVIEW-TEST.json", "ENTITY:symptom:SYM_TEST")
        self.assertEqual(impact["incident_relations"], 1)
        self.assertEqual(impact["rules"]["total"], 1)
        self.assertEqual(impact["rules"]["active"], 1)

    def test_update_node_name_preserves_code_and_invalidates_approval(self):
        result = self.service.update_node_name(
            "REVIEW-TEST.json", "ENTITY:symptom:SYM_TEST", "规范中文症状", "张医生", "名称修正"
        )
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["before_name"], "测试症状")
        graph = self.service._load_graph("REVIEW-TEST.json")
        node = graph["nodes"]["ENTITY:symptom:SYM_TEST"]
        self.assertEqual(node["name"], "规范中文症状")
        self.assertTrue(node["display_name"].startswith("规范中文症状"))
        self.assertEqual(node["code"], "SYM_TEST")
        self.assertEqual(node["name_history"][-1]["name"], "测试症状")
        relation = next(
            value for key, value in graph["relations"].items()
            if key[2] == "ENTITY:symptom:SYM_TEST"
        )
        self.assertEqual(relation["review_status"], "draft")
        self.assertIn("R1", result["affected_rules"])
        audit = self.service.audit_records(entity_id="ENTITY:symptom:SYM_TEST")
        self.assertEqual(audit[0]["action"], "edit_name")
        self.assertEqual(audit[0]["before_name"], "测试症状")
        self.assertEqual(audit[0]["after_name"], "规范中文症状")

    def test_apply_relation_correction_atomically_updates_name_and_stales_relation(self):
        result = self.service.apply_relation_correction(
            "REVIEW-TEST.json",
            "ENTITY:disease:I21.0",
            "DISEASE_HAS_SYMPTOM",
            "ENTITY:symptom:SYM_TEST",
            "张医生",
            "采纳 AI 修正",
            suggested_target_name="规范中文症状",
        )
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["changes"], ["target_name"])
        graph = self.service._load_graph("REVIEW-TEST.json")
        self.assertEqual(
            graph["nodes"]["ENTITY:symptom:SYM_TEST"]["name"], "规范中文症状"
        )
        self.assertEqual(
            graph["nodes"]["ENTITY:symptom:SYM_TEST"]["review_status"], "active"
        )
        relation = graph["relations"][
            ("ENTITY:disease:I21.0", "DISEASE_HAS_SYMPTOM", "ENTITY:symptom:SYM_TEST")
        ]
        self.assertEqual(relation["review_status"], "draft")
        self.assertIn("R1", result["affected_rules"])
        audit = self.service.audit_records(
            entity_id="ENTITY:disease:I21.0|DISEASE_HAS_SYMPTOM|ENTITY:symptom:SYM_TEST"
        )
        self.assertEqual(audit[0]["action"], "apply_ai_correction")

    def test_relation_ai_correction_cannot_rename_standard_icd_disease(self):
        with self.assertRaises(ReviewError) as ctx:
            self.service.apply_relation_correction(
                "REVIEW-TEST.json",
                "ENTITY:disease:I21.0",
                "DISEASE_HAS_SYMPTOM",
                "ENTITY:symptom:SYM_TEST",
                "张医生",
                "错误建议",
                suggested_source_name="线粒体疾病",
            )
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("标准名称", str(ctx.exception))
        graph = self.service._load_graph("REVIEW-TEST.json")
        self.assertEqual(graph["nodes"]["ENTITY:disease:I21.0"]["name"], "测试病")

    def test_list_items_filters_status(self):
        items = self.service.list_items("REVIEW-TEST.json", status="draft", item_type="node")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["node_id"], "ENTITY:symptom:SYM_DRAFT")
