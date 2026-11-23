"""导入历史删除：JOB-/DOC- 两类标识均能整组删除（含旧留痕无 job_id 的兼容）。

背景：早期上传链路（pdf 上传、一键自动）写留痕时没有 job_id 字段，
前端删除时曾把 doc_id-ts 组合键当任务号传给后端，被 JOB- 正则拒绝，
报“导入任务标识无效”。现在删除接口同时接受 JOB- 与 DOC- 标识。

测试通过 patch 模块全局把导入历史与上传目录指到临时目录，完全隔离，
不读写项目 data/ 下的真实数据。
"""
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


class ImportLogDeleteTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        tmp = Path(tempfile.mkdtemp(prefix="import-log-delete-"))
        self.log_path = tmp / "imports" / "import_log.jsonl"
        self.upload_dir = tmp / "uploads"
        self.upload_dir.mkdir(parents=True)
        self.doc_store: dict = {}
        patcher = mock.patch.multiple(
            pdf_routes,
            IMPORT_LOG_PATH=self.log_path,
            UPLOAD_DIR=self.upload_dir,
            _doc_store=self.doc_store,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_rows(self, rows):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _read_rows(self):
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def _seed_docs(self, *doc_ids):
        for doc_id in doc_ids:
            (self.upload_dir / f"{doc_id}.pdf").write_bytes(b"%PDF-1.4")
            self.doc_store[doc_id] = {"doc_id": doc_id}

    def test_delete_by_job_id(self):
        self._write_rows([
            {"job_id": "JOB-A", "doc_id": "DOC-A1", "doc_title": "任务流", "status": "started", "mode": "auto", "ts": "2026-11-23T09:00:00"},
            {"job_id": "JOB-A", "doc_id": "DOC-A1", "doc_title": "任务流", "status": "success", "mode": "auto", "ts": "2026-11-23T09:01:00"},
            {"job_id": "JOB-B", "doc_id": "DOC-B1", "doc_title": "其它", "status": "success", "mode": "llm", "ts": "2026-11-23T09:02:00"},
        ])
        self._seed_docs("DOC-A1", "DOC-B1")
        response = self.client.delete("/api/pdf/import-logs/JOB-A")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["removed_entries"], 2)
        self.assertEqual(response.json()["deleted_doc_ids"], ["DOC-A1"])
        self.assertEqual([row["job_id"] for row in self._read_rows()], ["JOB-B"])
        self.assertNotIn("DOC-A1", self.doc_store)
        self.assertFalse((self.upload_dir / "DOC-A1.pdf").exists())

    def test_delete_legacy_by_doc_id(self):
        """旧留痕没有 job_id：按 DOC- 号整组删除——这正是此前报“导入任务标识无效”的场景。"""
        self._write_rows([
            {"doc_id": "DOC-L1", "doc_title": "旧上传", "status": "failed", "mode": "local", "stage": "parse", "error": "解析失败", "ts": "2026-11-01T08:00:00"},
            {"doc_id": "DOC-L1", "doc_title": "旧上传", "status": "success", "mode": "local", "ts": "2026-11-01T08:03:00"},
            {"doc_id": "DOC-L2", "doc_title": "另一条旧上传", "status": "success", "mode": "llm", "ts": "2026-11-02T08:00:00"},
        ])
        self._seed_docs("DOC-L1", "DOC-L2")
        response = self.client.delete("/api/pdf/import-logs/DOC-L1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["removed_entries"], 2)
        self.assertEqual(response.json()["deleted_doc_ids"], ["DOC-L1"])
        self.assertEqual([row["doc_id"] for row in self._read_rows()], ["DOC-L2"])
        self.assertNotIn("DOC-L1", self.doc_store)
        self.assertFalse((self.upload_dir / "DOC-L1.pdf").exists())

    def test_delete_rejects_bad_key_and_missing_404(self):
        self._write_rows([{"doc_id": "DOC-L2", "status": "success", "mode": "llm", "ts": "t"}])
        self.assertEqual(self.client.delete("/api/pdf/import-logs/not-a-key").status_code, 422)
        self.assertEqual(self.client.delete("/api/pdf/import-logs/DOC-NOPE").status_code, 404)


if __name__ == "__main__":
    unittest.main()
