import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import httpx

import src.api.routes.kg_review as kg_review
from src.api.main import app
from src.review.service import ReviewService


def run(coro):
    return asyncio.run(coro)


class ReviewApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        graph_dir = root / "kg"
        graph_dir.mkdir()
        rules_path = root / "rules" / "managed_rules.json"
        rules_path.parent.mkdir()
        audit_path = root / "review" / "review_log.jsonl"
        (graph_dir / "API-TEST.json").write_text(json.dumps({
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
            "relations": [
                {
                    "source_node_id": "ENTITY:disease:I21.0",
                    "relation_code": "DISEASE_HAS_SYMPTOM",
                    "target_node_id": "ENTITY:symptom:SYM_DRAFT",
                    "review_status": "draft",
                }
            ],
        }, ensure_ascii=False), encoding="utf-8")
        rules_path.write_text("[]", encoding="utf-8")
        self.service = ReviewService(graph_dir, rules_path, audit_path)
        self.original = kg_review.service
        kg_review.service = self.service
        self.addCleanup(setattr, kg_review, "service", self.original)

    def request(self, method, path, **kwargs):
        async def go():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, path, **kwargs)
        return run(go())

    def test_summary(self):
        response = self.request("GET", "/api/kg/review/summary?graph_filename=API-TEST.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["nodes"]["draft"], 1)

    def test_approve_node(self):
        response = self.request(
            "POST", "/api/kg/nodes/ENTITY:symptom:SYM_DRAFT/review?graph_filename=API-TEST.json",
            json={"action": "approve", "reviewer": "张医生", "note": "确认"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "active")

    def test_invalid_transition_returns_422(self):
        response = self.request(
            "POST", "/api/kg/nodes/ENTITY:disease:I21.0/review?graph_filename=API-TEST.json",
            json={"action": "approve", "reviewer": "张医生"},
        )
        self.assertEqual(response.status_code, 422)

    def test_audit_endpoint(self):
        self.request(
            "POST", "/api/kg/nodes/ENTITY:symptom:SYM_DRAFT/review?graph_filename=API-TEST.json",
            json={"action": "approve", "reviewer": "张医生"},
        )
        response = self.request("GET", "/api/review/audit?entity_id=ENTITY:symptom:SYM_DRAFT")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["items"]), 1)

    def test_queue_endpoint_uses_service_graphs(self):
        response = self.request("GET", "/api/review/queue?graph_filename=API-TEST.json")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(any(
            item.get("item_type") == "graph_node"
            for item in payload["items"]
        ))

    def test_queue_endpoint_passes_node_type_and_source_doc(self):
        response = self.request(
            "GET", "/api/review/queue?graph_filename=API-TEST.json&node_type=symptom"
        )
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        # node_type 新语义：node 按自身类型命中；relation 按源/目标端点类型命中，
        # 故 symptom 同时命中节点「待审症状」与以症状为端点的 DISEASE_HAS_SYMPTOM 关系。
        self.assertEqual(
            [(i["item_type"], i["name"]) for i in items],
            [("graph_node", "待审症状"), ("graph_relation", "DISEASE_HAS_SYMPTOM")],
        )
        # source_doc 对无 source_refs 的条目应过滤为空
        response = self.request(
            "GET", "/api/review/queue?graph_filename=API-TEST.json&source_doc=DOC-NONE"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [i for i in response.json()["items"] if i["item_type"].startswith("graph_")], []
        )

    def test_queue_endpoint_filters_rule_clinical_intent(self):
        self.service.rules_path.write_text(json.dumps([{
            "rule_id": "R-SUSPECT",
            "name": "疑似心衰筛查",
            "rule_type": "diagnosis",
            "clinical_intent": "suspected_diagnosis",
            "disease_code": "I50.9",
            "status": "draft",
            "conditions": {"type": "group", "operator": "AND", "children": []},
            "conclusion": {"diagnosis": "I50.9"},
        }], ensure_ascii=False), encoding="utf-8")
        response = self.request(
            "GET", "/api/review/queue?clinical_intent=suspected_diagnosis"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["rule_id"] for item in response.json()["items"]], ["R-SUSPECT"])

    def test_unified_bulk_approves_graph_node(self):
        response = self.request("POST", "/api/review/bulk", json={
            "items": [{
                "item_type": "graph_node",
                "graph_filename": "API-TEST.json",
                "node_id": "ENTITY:symptom:SYM_DRAFT",
            }],
            "action": "approve",
            "reviewer": "张医生",
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["succeeded"], 1)
        self.assertEqual(payload["failed"], 0)

    def test_relation_items_expose_endpoint_info(self):
        response = self.request("GET", "/api/review/queue?graph_filename=API-TEST.json&item_type=graph_relation")
        self.assertEqual(response.status_code, 200)
        items = [item for item in response.json()["items"] if item["item_type"] == "graph_relation"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["source_name"], "测试病")
        self.assertEqual(item["source_status"], "active")
        self.assertEqual(item["target_name"], "待审症状")
        self.assertEqual(item["target_status"], "draft")
        self.assertEqual(item["item_id"], "ENTITY:disease:I21.0|DISEASE_HAS_SYMPTOM|ENTITY:symptom:SYM_DRAFT")

    def test_relation_detail_endpoint(self):
        relation_id = "ENTITY:disease:I21.0|DISEASE_HAS_SYMPTOM|ENTITY:symptom:SYM_DRAFT"
        response = self.request(
            "GET",
            f"/api/kg/relations/{quote(relation_id, safe='')}?graph_filename=API-TEST.json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["relation"]["relation_code"], "DISEASE_HAS_SYMPTOM")
        self.assertEqual(payload["source"]["name"], "测试病")
        self.assertEqual(payload["source"]["status"], "active")
        self.assertEqual(payload["target"]["status"], "draft")
        self.assertEqual(payload["rules"]["total"], 0)

    def test_review_reason_is_composed_into_note(self):
        response = self.request(
            "POST", "/api/kg/nodes/ENTITY:symptom:SYM_DRAFT/review?graph_filename=API-TEST.json",
            json={"action": "reject", "reviewer": "张医生", "note": "请核对指南第 3 页", "reason": "证据不足"},
        )
        self.assertEqual(response.status_code, 200)
        audit = self.request("GET", "/api/review/audit?entity_id=ENTITY:symptom:SYM_DRAFT").json()
        self.assertIn("[证据不足]", audit["items"][0]["note"])
        self.assertIn("请核对指南第 3 页", audit["items"][0]["note"])

    def test_node_detail_relations_include_neighbor_summary(self):
        response = self.request(
            "GET",
            "/api/kg/nodes/ENTITY%3Adisease%3AI21.0?graph_filename=API-TEST.json",
        )
        self.assertEqual(response.status_code, 200)
        relations = response.json()["relations"]
        self.assertTrue(relations)
        first = relations[0]
        self.assertIn("source", first)
        self.assertIn("target", first)
        self.assertEqual(first["source"]["name"], "测试病")
        self.assertEqual(first["target"]["node_type"], "symptom")

    def test_update_node_name_endpoint_keeps_stable_code(self):
        response = self.request(
            "PUT",
            "/api/kg/nodes/ENTITY%3Asymptom%3ASYM_DRAFT?graph_filename=API-TEST.json",
            json={"name": "血乳酸阳性", "reviewer": "张医生", "note": "中文化"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["item"]["name"], "血乳酸阳性")
        self.assertEqual(response.json()["item"]["code"], "SYM_DRAFT")
        self.assertEqual(response.json()["status"], "draft")
        audit = self.request("GET", "/api/review/audit?entity_id=ENTITY:symptom:SYM_DRAFT").json()
        self.assertEqual(audit["items"][0]["action"], "edit_name")

    def test_ai_correction_returns_suggestion_without_writing_graph(self):
        original_repo = kg_review.ExtractionConfigRepository
        original_resolve = kg_review.resolve_api_key
        original_chat = kg_review.chat_json

        class FakeRepo:
            def load(self):
                return {"llm": {"model": "test-model"}}

        async def fake_chat(settings, messages, **kwargs):
            return {
                "suggested_name": "血乳酸阳性",
                "reason": "将英文候选名转换为中文",
                "confidence": 0.93,
                "needs_human_review": False,
            }

        kg_review.ExtractionConfigRepository = FakeRepo
        kg_review.resolve_api_key = lambda settings: "test-key"
        kg_review.chat_json = fake_chat
        self.addCleanup(setattr, kg_review, "ExtractionConfigRepository", original_repo)
        self.addCleanup(setattr, kg_review, "resolve_api_key", original_resolve)
        self.addCleanup(setattr, kg_review, "chat_json", original_chat)
        response = self.request(
            "POST",
            "/api/kg/nodes/ENTITY%3Asymptom%3ASYM_DRAFT/ai-correction?graph_filename=API-TEST.json",
            json={"prompt": "改成规范中文名称"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["suggested_name"], "血乳酸阳性")
        detail = self.request(
            "GET", "/api/kg/nodes/ENTITY%3Asymptom%3ASYM_DRAFT?graph_filename=API-TEST.json"
        ).json()
        self.assertEqual(detail["node"]["name"], "待审症状")

    def test_relation_ai_review_requires_source_evidence_and_does_not_mutate(self):
        original_repo = kg_review.ExtractionConfigRepository
        original_resolve = kg_review.resolve_api_key
        original_chat = kg_review.chat_json

        class FakeRepo:
            def load(self):
                return {"llm": {"model": "test-model"}}

        async def fake_chat(settings, messages, **kwargs):
            self.assertIn("DISEASE_HAS_SYMPTOM", messages[1]["content"])
            return {
                "assessment": "approve",
                "summary": "关系语义看起来合理",
                "issues": [],
                "suggested_relation_code": "",
                "suggested_direction": "",
                "suggested_source_name": "",
                "suggested_target_name": "待审症状",
                "reverse_direction": False,
                "suggested_statement": "测试病可见待审症状",
                "reviewer_checklist": ["核对原文"],
                "confidence": 0.8,
            }

        kg_review.ExtractionConfigRepository = FakeRepo
        kg_review.resolve_api_key = lambda settings: "test-key"
        kg_review.chat_json = fake_chat
        self.addCleanup(setattr, kg_review, "ExtractionConfigRepository", original_repo)
        self.addCleanup(setattr, kg_review, "resolve_api_key", original_resolve)
        self.addCleanup(setattr, kg_review, "chat_json", original_chat)
        response = self.request(
            "POST",
            "/api/kg/relation-ai-review?graph_filename=API-TEST.json",
            json={
                "source_node_id": "ENTITY:disease:I21.0",
                "relation_code": "DISEASE_HAS_SYMPTOM",
                "target_node_id": "ENTITY:symptom:SYM_DRAFT",
                "prompt": "重点核对症状是否为阳性表现",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["assessment"], "precheck")
        self.assertEqual(payload["review_mode"], "model_knowledge")
        self.assertFalse(payload["source_verified"])
        self.assertEqual(payload["suggested_target_name"], "待审症状")
        detail = self.request(
            "GET",
            "/api/kg/relations/ENTITY%3Adisease%3AI21.0%7CDISEASE_HAS_SYMPTOM%7CENTITY%3Asymptom%3ASYM_DRAFT?graph_filename=API-TEST.json",
        ).json()
        self.assertEqual(detail["relation"]["review_status"], "draft")

    def test_relation_source_uses_archived_package_when_upload_is_missing(self):
        from src.api.routes import guide_packages, pdf

        package_dir = Path(self.tmp.name) / "guide_packages"
        package_dir.mkdir()
        (package_dir / "PKG-DOC-archive01.json").write_text(json.dumps({
            "doc_id": "DOC-archive01",
            "chunks": [{
                "chunk_id": "DOC-archive01_p3_l21-4",
                "text": "first line\nsecond line",
                "page_numbers": [3, 4],
                "line_numbers": [21, 4],
            }],
        }), encoding="utf-8")
        with patch.object(guide_packages, "PACKAGE_DIR", package_dir), patch.object(pdf, "_load_doc", return_value=None):
            excerpts = kg_review._relation_source_excerpts(["DOC-archive01_p3_l21-4"])
        self.assertEqual(excerpts, [{
            "source_ref": "DOC-archive01_p3_l21-4",
            "text": "first line\nsecond line",
            "origin": "knowledge_package",
        }])

    def test_apply_relation_ai_correction_endpoint(self):
        response = self.request(
            "POST",
            "/api/kg/relation-ai-correction?graph_filename=API-TEST.json",
            json={
                "source_node_id": "ENTITY:disease:I21.0",
                "relation_code": "DISEASE_HAS_SYMPTOM",
                "target_node_id": "ENTITY:symptom:SYM_DRAFT",
                "reviewer": "张医生",
                "note": "采纳 AI 中文名称修正",
                "suggested_target_name": "规范中文症状",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["changes"], ["target_name"])
        detail = self.request(
            "GET", "/api/kg/nodes/ENTITY%3Asymptom%3ASYM_DRAFT?graph_filename=API-TEST.json"
        ).json()
        self.assertEqual(detail["node"]["name"], "规范中文症状")

    def test_entity_history_endpoint(self):
        import src.review.metadata as metadata
        original_dir = metadata.HISTORY_DIR
        metadata.HISTORY_DIR = Path(self.tmp.name)
        self.addCleanup(setattr, metadata, "HISTORY_DIR", original_dir)
        metadata.append_entity_snapshot(
            "API-TEST.json", "node", "ENTITY:symptom:SYM_DRAFT",
            {"node_type": "symptom", "code": "SYM_DRAFT", "name": "旧症状名", "attributes": {}},
            reason="import_change",
        )
        response = self.request(
            "GET",
            f"/api/kg/entities/{quote('ENTITY:symptom:SYM_DRAFT', safe='')}/history"
            "?graph_filename=API-TEST.json&entity_type=node",
        )
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content"]["name"], "旧症状名")
        self.assertEqual(items[0]["reason"], "import_change")
