"""Import job API: create->poll->pause->resume->stop over HTTP."""
from __future__ import annotations

import asyncio
import io
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient

from src.api.main import app
from src.api.import_job_manager import ImportJob
from src.api.routes import import_jobs as routes
from src.api.routes import pdf as pdf_routes
from src.api.routes.pdf import IMPORT_LOG_PATH, UPLOAD_DIR, _doc_store


def _cleanup_upload(doc_id: str) -> None:
    (UPLOAD_DIR / f"{doc_id}.pdf").unlink(missing_ok=True)
    (UPLOAD_DIR / f"{doc_id}.parsed.json").unlink(missing_ok=True)


def _stub_submit():
    """submit 换成不真正跑 pipeline 的版本，验证路由参数与状态码。"""
    captured = {}
    def fake_submit(mode, doc_title, runner, on_finish=None):
        captured.update({"mode": mode, "doc_title": doc_title})
        job = routes.ImportJob(job_id="JOB-STUB", mode=mode, doc_title=doc_title)
        job.status = "running"
        routes.manager._jobs["JOB-STUB"] = job
        return job
    routes.manager.submit = fake_submit
    return captured


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

    def test_create_job_returns_job_id(self):
        captured = _stub_submit()
        response = self.client.post(
            "/api/import/jobs",
            files={"file": ("测试.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            data={"mode": "template-local", "template_id": "GENERIC_GUIDELINE_V1"},
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["job_id"], "JOB-STUB")
        self.assertEqual(captured["mode"], "template-local")
        _cleanup_upload(body["doc_id"])

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
        pdf_bytes = _tiny_guide_pdf()
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
        # 回归路径：作业落库的 doc_record 必须带 uploaded_at，否则 documents 列表 KeyError→500
        documents = client.get("/api/pdf/documents")
        self.assertEqual(documents.status_code, 200)
        self.assertIn(doc_id, [d["doc_id"] for d in documents.json()])
        self.assertTrue(documents.json()[0]["uploaded_at"])
        routes.manager._jobs.pop(job_id, None)
        _doc_store.pop(doc_id, None)
        _cleanup_upload(doc_id)


def _tiny_guide_pdf() -> bytes:
    """最小可解析指南 PDF（与 upload 链路一致的 fitz 生成）。"""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hypertension Diagnosis and Treatment Guideline")
    page.insert_text((72, 92), "Section 1 Diagnosis Criteria")
    page.insert_text((72, 112), "Blood pressure measurement is required for diagnosis.")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


class ImportJobLogTrailTest(unittest.TestCase):
    """任务全生命周期导入日志留痕：started/终态/paused/resumed 均落痕。

    打桩 ``src.api.routes.pdf._append_import_log``（路由侧均为函数内导入，
    打桩源头即可拦截），既收集断言数据又不污染 data/imports。
    """

    def setUp(self):
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        self.client = TestClient(app)
        self.calls = []
        patcher = mock.patch(
            "src.api.routes.pdf._append_import_log",
            new=lambda entry, path=None: self.calls.append(dict(entry)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self._original_submit = routes.manager.submit
        self.addCleanup(setattr, routes.manager, "submit", self._original_submit)
        routes._JOB_DOC_IDS.clear()  # JOB-STUB 等固定 id 复用，避免串档
        self.addCleanup(routes._JOB_DOC_IDS.clear)
        routes.manager._jobs.clear()
        self.addCleanup(routes.manager._jobs.clear)

    def _entries(self, job_id):
        return [c for c in self.calls if c.get("job_id") == job_id]

    def test_terminal_status_mappings(self):
        """on_finish 三条终态映射单元级断言：success/failed/stopped。"""
        job = ImportJob(job_id="JOB-OK", mode="template-local", doc_title="指南A")
        job.status = "succeeded"
        job.result = {
            "doc_id": "DOC-OK", "entity_count": 3, "relation_count": 2,
            "rule_count": 1, "total_pages": 7, "warnings": [],
        }
        routes._log_job_finished(job)
        entry = self.calls[-1]
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["mode"], "local")  # 模式映射同建任务处
        self.assertEqual(entry["doc_id"], "DOC-OK")
        self.assertEqual(entry["entity_count"], 3)
        self.assertEqual(entry["relation_count"], 2)
        self.assertEqual(entry["rule_count"], 1)
        self.assertEqual(entry["pages"], 7)

        job = ImportJob(job_id="JOB-AUTO", mode="auto", doc_title="指南B")
        job.status = "succeeded"
        job.result = {"doc_id": "DOC-AUTO"}  # run_auto_pipeline 返回含 doc_id
        routes._log_job_finished(job)
        entry = self.calls[-1]
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["mode"], "auto")
        self.assertNotIn("entity_count", entry)  # result 没有计数字段则不带

        job = ImportJob(job_id="JOB-BAD", mode="template-llm", doc_title="指南C")
        job.status = "failed"
        job.error = "x" * 500
        routes._log_job_finished(job)
        entry = self.calls[-1]
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(entry["mode"], "llm")
        self.assertEqual(len(entry["error"]), 300)

        job = ImportJob(job_id="JOB-STOP", mode="auto", doc_title="指南D")
        job.status = "stopped"  # 无 result：doc_id 回退空串
        routes._log_job_finished(job)
        entry = self.calls[-1]
        self.assertEqual(entry["status"], "stopped")
        self.assertEqual(entry["doc_id"], "")

    def test_pause_resume_writes_log_entries(self):
        """pause/resume 路由成功路径各写一条；404 不写。"""
        _stub_submit()
        created = self.client.post(
            "/api/import/jobs",
            files={"file": ("t.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            data={"mode": "template-local", "template_id": "GENERIC_GUIDELINE_V1"},
        )
        doc_id = created.json()["doc_id"]
        self.assertEqual(
            [c["status"] for c in self._entries("JOB-STUB")], ["started"]
        )
        self.assertEqual(self.client.post("/api/import/jobs/JOB-STUB/pause").status_code, 200)
        self.assertEqual(self.client.post("/api/import/jobs/JOB-STUB/resume").status_code, 200)
        statuses = [c["status"] for c in self._entries("JOB-STUB")]
        self.assertEqual(statuses, ["started", "paused", "resumed"])
        paused = self._entries("JOB-STUB")[1]
        self.assertEqual(paused["doc_id"], doc_id)
        self.assertEqual(paused["mode"], "local")
        before = len(self.calls)
        self.assertEqual(self.client.post("/api/import/jobs/JOB-X/pause").status_code, 404)
        self.assertEqual(len(self.calls), before)  # 404 路径不留痕
        _cleanup_upload(doc_id)

    def test_end_to_end_success_writes_started_and_success(self):
        """真 PDF 跑到 succeeded 后，导入日志里该 job_id 有 started 与 success 两条。"""
        pdf_bytes = _tiny_guide_pdf()
        with TestClient(app) as client:
            created = client.post(
                "/api/import/jobs",
                files={"file": ("log-trail.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
                data={"mode": "template-local", "template_id": "GENERIC_GUIDELINE_V1"},
            )
            self.assertEqual(created.status_code, 201)
            job_id = created.json()["job_id"]
            doc_id = created.json()["doc_id"]
            deadline = time.time() + 30
            status = ""
            while time.time() < deadline:
                status = client.get(f"/api/import/jobs/{job_id}").json()["status"]
                if status in {"succeeded", "failed", "stopped"}:
                    break
                time.sleep(0.1)
        self.assertEqual(status, "succeeded")
        entries = self._entries(job_id)
        self.assertEqual([c["status"] for c in entries], ["started", "success"])
        self.assertEqual(entries[-1]["doc_id"], doc_id)
        self.assertIn("entity_count", entries[-1])
        self.assertIn("pages", entries[-1])
        routes.manager._jobs.pop(job_id, None)
        _doc_store.pop(doc_id, None)
        _cleanup_upload(doc_id)


if __name__ == "__main__":
    unittest.main()
