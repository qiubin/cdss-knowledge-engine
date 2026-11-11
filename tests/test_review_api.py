import asyncio
import json
import tempfile
import unittest
from pathlib import Path
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
