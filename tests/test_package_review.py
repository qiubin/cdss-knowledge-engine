import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

import src.api.routes.guide_packages as gp
from src.api.main import app
from src.api.routes.guide_packages import _filter_approved, _package_items


def package():
    return {
        "package_id": "PKG-TEST",
        "doc_id": "DOC-TEST",
        "doc_title": "测试指南",
        "domain": "western",
        "target_disease_code": "I21.0",
        "target_anchor_type": "disease",
        "entities": [
            {"entity_type": "disease", "name": "测试病", "code": "I21.0", "chunk_id": "c1"},
            {"entity_type": "symptom", "name": "症状", "code": "SYM", "chunk_id": "c1"},
        ],
        "suggested_relations": [
            {
                "relation_code": "DISEASE_HAS_SYMPTOM",
                "source_code": "I21.0",
                "target_code": "SYM",
                "source_type": "disease",
                "target_type": "symptom",
                "chunk_id": "c1",
            },
        ],
        "suggested_rules": [
            {"rule_id": "R1", "name": "规则", "rule_type": "diagnosis"},
        ],
        "item_review": {},
    }


class PackageReviewTest(unittest.TestCase):
    def test_package_items_default_to_draft(self):
        items = _package_items(package())
        self.assertEqual(len(items), 4)
        self.assertTrue(all(item["status"] == "draft" for item in items))

    def test_filter_approved_keeps_only_approved_items(self):
        pkg = package()
        pkg["item_review"] = {
            "entity:disease:I21.0": {"status": "approved"},
            "entity:symptom:SYM": {"status": "approved"},
            "relation:I21.0:DISEASE_HAS_SYMPTOM:SYM": {"status": "approved"},
            "rule:R1": {"status": "approved"},
        }
        filtered = _filter_approved(pkg)
        self.assertEqual(len(filtered["entities"]), 2)
        self.assertEqual(len(filtered["suggested_relations"]), 1)
        self.assertEqual(len(filtered["suggested_rules"]), 1)

    def test_filter_approved_excludes_unapproved(self):
        pkg = package()
        pkg["item_review"] = {"entity:disease:I21.0": {"status": "approved"}}
        filtered = _filter_approved(pkg)
        self.assertEqual(len(filtered["entities"]), 1)
        self.assertEqual(filtered["suggested_relations"], [])
        self.assertEqual(filtered["suggested_rules"], [])


def run(coro):
    return asyncio.run(coro)


class PackageReviewApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.package_dir = Path(self.tmp.name) / "guide_packages"
        self.package_dir.mkdir()
        (self.package_dir / "PKG-TEST.json").write_text(
            json.dumps(package(), ensure_ascii=False), encoding="utf-8"
        )
        self.original = gp.PACKAGE_DIR
        gp.PACKAGE_DIR = self.package_dir
        self.addCleanup(setattr, gp, "PACKAGE_DIR", self.original)

    def request(self, method, path, **kwargs):
        async def go():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, path, **kwargs)
        return run(go())

    def test_list_package_items(self):
        response = self.request("GET", "/api/guide-packages/PKG-TEST/items")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["total"], 4)

    def test_review_package_item(self):
        response = self.request("POST", "/api/guide-packages/PKG-TEST/items/review", json={
            "items": ["entity:disease:I21.0"],
            "action": "approve",
            "reviewer": "张医生",
        })
        self.assertEqual(response.status_code, 200)
        items = {item["item_id"]: item for item in response.json()["items"]}
        self.assertEqual(items["entity:disease:I21.0"]["status"], "approved")

    def test_unknown_item_rejected(self):
        response = self.request("POST", "/api/guide-packages/PKG-TEST/items/review", json={
            "items": ["entity:unknown:X"], "action": "approve", "reviewer": "张医生",
        })
        self.assertEqual(response.status_code, 422)
