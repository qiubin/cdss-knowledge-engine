"""导入留痕必须携带领域：中医导入记为中医、西医导入记为西医。

回归背景：留痕原本不带 domain 字段，中医导入在历史里与西医混排，
用户看到“最新一次是导入中医，却记录成一个西医指南”。
"""
from __future__ import annotations

import asyncio
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes import import_jobs as routes


def _stub_submit():
    def fake_submit(mode, doc_title, runner, on_finish=None):
        job = routes.ImportJob(job_id="JOB-DOM", mode=mode, doc_title=doc_title)
        job.status = "running"
        routes.manager._jobs["JOB-DOM"] = job
        return job
    routes.manager.submit = fake_submit


class ImportLogDomainTest(unittest.TestCase):
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
        routes._JOB_DOC_IDS.clear()
        self.addCleanup(routes._JOB_DOC_IDS.clear)
        routes._JOB_DOMAINS.clear()
        self.addCleanup(routes._JOB_DOMAINS.clear)
        routes.manager._jobs.clear()
        self.addCleanup(routes.manager._jobs.clear)

    def test_tcm_template_job_logs_tcm_domain(self):
        _stub_submit()
        response = self.client.post(
            "/api/import/jobs",
            files={"file": ("t.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            data={"mode": "auto", "template_id": "TCM_GUIDELINE_V3", "domain": "tcm"},
        )
        self.assertEqual(response.status_code, 201)
        entry = self.calls[-1]
        self.assertEqual(entry["domain"], "tcm", "中医模板导入的留痕必须记为中医")

    def test_western_template_job_logs_western_domain(self):
        _stub_submit()
        response = self.client.post(
            "/api/import/jobs",
            files={"file": ("t.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            data={"mode": "auto", "template_id": "GENERIC_GUIDELINE_V1", "domain": "western"},
        )
        self.assertEqual(response.status_code, 201)
        entry = self.calls[-1]
        self.assertEqual(entry["domain"], "western", "西医模板导入的留痕必须记为西医")

    def test_domain_template_mismatch_rejected_before_llm(self):
        _stub_submit()
        response = self.client.post(
            "/api/import/jobs",
            files={"file": ("t.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            data={"mode": "auto", "template_id": "TCM_GUIDELINE_V3", "domain": "western"},
        )
        self.assertEqual(response.status_code, 422, "领域与模板不一致应在建任务时拒绝，而非任务跑失败")

    def test_success_result_domain_overrides_registry(self):
        job = routes.ImportJob(job_id="JOB-R", mode="auto", doc_title="中医指南")
        job.status = "succeeded"
        job.result = {"doc_id": "DOC-R", "domain": "tcm"}
        routes._log_job_finished(job)
        entry = self.calls[-1]
        self.assertEqual(entry["domain"], "tcm")
        self.assertNotIn("JOB-R", routes._JOB_DOMAINS, "终态后应清理领域登记")

    def test_legacy_entry_without_domain_is_blank(self):
        job = routes.ImportJob(job_id="JOB-N", mode="auto", doc_title="未知领域")
        job.status = "stopped"
        routes._log_job_finished(job)
        self.assertEqual(self.calls[-1]["domain"], "")


if __name__ == "__main__":
    unittest.main()
