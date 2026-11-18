"""GET /api/pdf/documents 必须容忍旧格式文档缺失 entities 等字段（回归：KeyError 500）。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes import pdf as pdf_routes


DOC_ID = "TEST-DOC-NO-ENTITIES"
PARSE_ONLY_DOC_ID = "TEST-DOC-PARSE-ONLY"


class PdfDocumentsRouteTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # 隔离运行时状态：内存 store 清空并在测试后还原
        self._store_backup = dict(pdf_routes._doc_store)
        pdf_routes._doc_store.clear()
        self.addCleanup(self._restore_store)
        # 上传目录指到临时目录，避免依赖真实 data/uploads 内容
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        patcher = mock.patch.object(pdf_routes, "UPLOAD_DIR", Path(self._tmpdir.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        # 写入一份缺 entities 字段的旧格式文档（线上 500 的触发形态）
        legacy = {
            "doc_id": DOC_ID,
            "doc_title": "旧格式文档（无 entities 字段）",
            "parsed": {"total_pages": 3, "total_lines": 42},
            "uploaded_at": "2026-11-18T00:00:00",
        }
        (Path(self._tmpdir.name) / f"{DOC_ID}.parsed.json").write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
        )

    def _restore_store(self):
        pdf_routes._doc_store.clear()
        pdf_routes._doc_store.update(self._store_backup)

    def test_list_documents_tolerates_missing_entities(self):
        resp = self.client.get("/api/pdf/documents")
        self.assertEqual(resp.status_code, 200)
        docs = {d["doc_id"]: d for d in resp.json()}
        self.assertIn(DOC_ID, docs)
        self.assertEqual(docs[DOC_ID]["entity_count"], 0)
        self.assertEqual(docs[DOC_ID]["total_pages"], 3)

    def _write_parse_only_doc(self):
        """只完成解析、未跑实体抽取的文档（无 entities/suggested_rules/extraction）。"""
        parse_only = {
            "doc_id": PARSE_ONLY_DOC_ID,
            "doc_title": "仅解析文档",
            "parsed": {
                "total_pages": 5,
                "total_lines": 120,
                "chunks": [],
            },
            "uploaded_at": "2026-11-18T01:00:00",
        }
        (Path(self._tmpdir.name) / f"{PARSE_ONLY_DOC_ID}.parsed.json").write_text(
            json.dumps(parse_only, ensure_ascii=False), encoding="utf-8"
        )

    def test_document_result_tolerates_parse_only_doc(self):
        self._write_parse_only_doc()
        resp = self.client.get(f"/api/pdf/{PARSE_ONLY_DOC_ID}/result")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["entities"], [])
        self.assertEqual(payload["suggested_rules"], [])
        self.assertEqual(payload["total_pages"], 5)

    def test_entities_endpoint_tolerates_parse_only_doc(self):
        self._write_parse_only_doc()
        resp = self.client.get(f"/api/pdf/{PARSE_ONLY_DOC_ID}/entities")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["entities"], [])


if __name__ == "__main__":
    unittest.main()
