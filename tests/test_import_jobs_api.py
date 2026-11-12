"""Import job API: create->poll->pause->resume->stop over HTTP."""
from __future__ import annotations

import asyncio
import io
import sys
import time
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes import import_jobs as routes
from src.api.routes.pdf import IMPORT_LOG_PATH, UPLOAD_DIR


class ImportJobsApiTest(unittest.TestCase):
    def setUp(self):
        # 全量回归时，前置测试的 asyncio.run 会清掉主线程当前事件循环；
        # ImportJob 默认构造 asyncio.Event 需要 get_event_loop 可用，这里兜底补一个。
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        self.client = TestClient(app)
        self._original_submit = routes.manager.submit
        self.addCleanup(setattr, routes.manager, "submit", self._original_submit)
        # 导入历史是运行时数据：备份/还原，保持 data/ 原样
        self._log_backup = IMPORT_LOG_PATH.read_bytes() if IMPORT_LOG_PATH.exists() else None
        self.addCleanup(self._restore_log)

    def _restore_log(self):
        if self._log_backup is None:
            IMPORT_LOG_PATH.unlink(missing_ok=True)
        else:
            IMPORT_LOG_PATH.write_bytes(self._log_backup)

    def _cleanup_upload(self, doc_id: str) -> None:
        (UPLOAD_DIR / f"{doc_id}.pdf").unlink(missing_ok=True)
        (UPLOAD_DIR / f"{doc_id}.parsed.json").unlink(missing_ok=True)

    def _stub_submit(self):
        """submit 换成不真正跑 pipeline 的版本，验证路由参数与状态码。"""
        captured = {}
        def fake_submit(mode, doc_title, runner):
            captured.update({"mode": mode, "doc_title": doc_title})
            job = routes.ImportJob(job_id="JOB-STUB", mode=mode, doc_title=doc_title)
            job.status = "running"
            routes.manager._jobs["JOB-STUB"] = job
            return job
        routes.manager.submit = fake_submit
        return captured

    def test_create_job_returns_job_id(self):
        captured = self._stub_submit()
        response = self.client.post(
            "/api/import/jobs",
            files={"file": ("测试.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            data={"mode": "template-local", "template_id": "GENERIC_GUIDELINE_V1"},
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["job_id"], "JOB-STUB")
        self.assertEqual(captured["mode"], "template-local")
        self._cleanup_upload(body["doc_id"])

    def test_create_job_rejects_bad_mode(self):
        response = self.client.post(
            "/api/import/jobs",
            files={"file": ("t.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            data={"mode": "nonsense"},
        )
        self.assertEqual(response.status_code, 422)

    def test_unknown_job_404_and_terminal_pause_409(self):
        self.assertEqual(self.client.post("/api/import/jobs/JOB-X/pause").status_code, 404)
        job = routes.ImportJob(job_id="JOB-DONE", mode="auto", doc_title="t")
        job.status = "succeeded"
        routes.manager._jobs["JOB-DONE"] = job
        self.assertEqual(self.client.post("/api/import/jobs/JOB-DONE/pause").status_code, 409)
        routes.manager._jobs.pop("JOB-DONE", None)

    def test_list_and_detail(self):
        job = routes.ImportJob(job_id="JOB-L", mode="auto", doc_title="t")
        routes.manager._jobs["JOB-L"] = job
        self.assertEqual(self.client.get("/api/import/jobs").json()["jobs"][0]["job_id"], "JOB-L")
        self.assertEqual(self.client.get("/api/import/jobs/JOB-L").json()["job_id"], "JOB-L")
        routes.manager._jobs.pop("JOB-L", None)

    def test_end_to_end_template_local_succeeds(self):
        """真 PDF 全链路（不 stub submit）：解析→本地抽取→落库，轮询到 succeeded。"""
        import fitz

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Hypertension Diagnosis and Treatment Guideline")
        page.insert_text((72, 92), "Section 1 Diagnosis Criteria")
        page.insert_text((72, 112), "Blood pressure measurement is required for diagnosis.")
        pdf_bytes = doc.tobytes()
        doc.close()

        with TestClient(app) as client:  # portal 常驻：后台任务在轮询请求间持续推进
            created = client.post(
                "/api/import/jobs",
                files={"file": ("e2e-guide.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
                data={"mode": "template-local", "template_id": "GENERIC_GUIDELINE_V1"},
            )
            self.assertEqual(created.status_code, 201)
            job_id = created.json()["job_id"]
            doc_id = created.json()["doc_id"]
            deadline = time.time() + 30
            body = {}
            while time.time() < deadline:
                body = client.get(f"/api/import/jobs/{job_id}").json()
                if body["status"] in {"succeeded", "failed", "stopped"}:
                    break
                time.sleep(0.1)
        self.assertEqual(body["status"], "succeeded", body.get("error"))
        self.assertIn("entity_count", body["result"] or {})
        self.assertEqual(body["result"]["doc_id"], doc_id)
        routes.manager._jobs.pop(job_id, None)
        self._cleanup_upload(doc_id)


if __name__ == "__main__":
    unittest.main()
