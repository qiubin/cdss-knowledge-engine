"""Import job manager: lifecycle state machine + controller checkpoints."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.import_job_manager import (
    ImportController, ImportJobManager, ImportStopped,
)


class ControllerCheckpointTest(unittest.IsolatedAsyncioTestCase):
    async def test_checkpoint_updates_stage_and_progress(self):
        mgr = ImportJobManager()
        job = mgr.submit("template-llm", "t", lambda c: asyncio.sleep(10, result={}))
        controller = ImportController(job)
        await controller.checkpoint(stage="抽取", done=2, total=5)
        self.assertEqual(job.stage, "抽取")
        self.assertEqual(job.progress, {"done": 2, "total": 5})
        await mgr.stop(job.job_id)

    async def test_stop_requested_raises_import_stopped(self):
        mgr = ImportJobManager()
        job = mgr.submit("template-llm", "t", lambda c: asyncio.sleep(10, result={}))
        controller = ImportController(job)
        job.control.stop_requested = True
        with self.assertRaises(ImportStopped):
            await controller.checkpoint()
        await mgr.stop(job.job_id)

    async def test_finalizing_ignores_stop(self):
        mgr = ImportJobManager()
        job = mgr.submit("template-llm", "t", lambda c: asyncio.sleep(10, result={}))
        controller = ImportController(job)
        controller.finalizing = True
        job.control.stop_requested = True
        await controller.checkpoint()  # 不抛
        await mgr.stop(job.job_id)

    async def test_pause_blocks_until_resume(self):
        mgr = ImportJobManager()
        job = mgr.submit("template-llm", "t", lambda c: asyncio.sleep(10, result={}))
        controller = ImportController(job)
        job.control.pause_event.clear()  # 暂停
        checker = asyncio.create_task(controller.checkpoint())
        await asyncio.sleep(0.05)
        self.assertFalse(checker.done())
        self.assertEqual(job.status, "paused")
        job.control.pause_event.set()  # 恢复
        await asyncio.wait_for(checker, 1)
        self.assertEqual(job.status, "running")
        await mgr.stop(job.job_id)


class ManagerLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_submit_runs_runner_to_success(self):
        mgr = ImportJobManager()
        async def runner(controller):
            await controller.checkpoint(stage="一", done=0, total=1)
            return {"doc_id": "D1", "entity_count": 3}
        job = mgr.submit("template-local", "指南A", runner)
        for _ in range(100):
            if job.status in {"succeeded", "failed"}: break
            await asyncio.sleep(0.02)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.result["doc_id"], "D1")
        self.assertIsNotNone(job.finished_at)

    async def test_runner_exception_marks_failed(self):
        mgr = ImportJobManager()
        async def runner(controller):
            raise RuntimeError("炸了")
        job = mgr.submit("auto", "指南B", runner)
        for _ in range(100):
            if job.status in {"succeeded", "failed"}: break
            await asyncio.sleep(0.02)
        self.assertEqual(job.status, "failed")
        self.assertIn("炸了", job.error)

    async def test_import_stopped_marks_stopped_and_discards(self):
        mgr = ImportJobManager()
        async def runner(controller):
            await controller.checkpoint()
            raise ImportStopped()
        job = mgr.submit("template-llm", "指南C", runner)
        for _ in range(100):
            if job.status in {"succeeded", "failed", "stopped"}: break
            await asyncio.sleep(0.02)
        self.assertEqual(job.status, "stopped")
        self.assertIsNone(job.result)

    async def test_pause_resume_stop_transitions_and_409(self):
        mgr = ImportJobManager()
        started = asyncio.Event()
        async def runner(controller):
            started.set()
            await asyncio.sleep(30, result={})
        job = mgr.submit("template-llm", "t", runner)
        await started.wait()
        await mgr.pause(job.job_id)   # running -> pausing（cancel 挂起 sleep）
        await mgr.resume(job.job_id)  # -> running
        await mgr.stop(job.job_id)    # -> stopped
        with self.assertRaises(ValueError):
            await mgr.pause(job.job_id)  # 已终态 -> ValueError（路由转 409）
        for _ in range(100):
            if job.status == "stopped": break
            await asyncio.sleep(0.02)
        self.assertEqual(job.status, "stopped")

    def test_list_jobs_keeps_most_recent_50(self):
        mgr = ImportJobManager()
        for i in range(55):
            mgr._jobs[f"J{i:02d}"] = "x"
        mgr._prune()
        self.assertEqual(len(mgr._jobs), 50)


if __name__ == "__main__":
    unittest.main()
