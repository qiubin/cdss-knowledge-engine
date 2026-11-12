"""Import job manager: lifecycle state machine + controller checkpoints."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.import_job_manager import (
    ImportController, ImportJob, ImportJobManager, ImportStopped,
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

    async def test_on_finish_called_with_terminal_job(self):
        """submit 的 on_finish 在 finally 里收到终态 job（成功与失败路径）。"""
        mgr = ImportJobManager()
        seen = []
        async def ok(controller):
            return {"doc_id": "D1"}
        job = mgr.submit("auto", "A", ok, on_finish=seen.append)
        for _ in range(100):
            if job.status in {"succeeded", "failed", "stopped"}: break
            await asyncio.sleep(0.02)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0], job)
        self.assertEqual(seen[0].status, "succeeded")

        async def boom(controller):
            raise RuntimeError("终态回调")
        job2 = mgr.submit("auto", "B", boom, on_finish=seen.append)
        for _ in range(100):
            if job2.status in {"succeeded", "failed", "stopped"}: break
            await asyncio.sleep(0.02)
        self.assertEqual(job2.status, "failed")
        self.assertEqual(len(seen), 2)
        self.assertIs(seen[1], job2)
        self.assertEqual(seen[1].status, "failed")

    async def test_stop_before_task_first_step_synthesizes_terminal(self):
        """stop 先于任务体首步落地：协程从未执行也要合成终态，不留僵尸 queued。

        submit 与 stop 之间不 await 任何东西 → cancel 在任务首步前生效，
        任务体的 status/finally 全部未跑；stop 必须就地合成 STOPPED 终态、
        补 finished_at、写日志并触发 on_finish。
        """
        mgr = ImportJobManager()
        seen = []

        async def runner(controller):
            await asyncio.sleep(30, result={})

        job = mgr.submit("template-llm", "t", runner, on_finish=seen.append)
        stopped = await mgr.stop(job.job_id)
        self.assertIs(stopped, job)
        self.assertEqual(job.status, "stopped")
        self.assertIsNotNone(job.finished_at)
        self.assertTrue(job.task.done())
        self.assertTrue(any("停止" in entry["message"] for entry in job.logs))
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0], job)

    def test_prune_only_evicts_terminal_jobs(self):
        """_prune 兜底分支只逐出终态条目；无非终态可逐时允许暂时超 50。"""
        mgr = ImportJobManager()
        # 51 个非终态（running/paused/字符串占位）：一个都不能被逐出
        mgr._jobs["R00"] = ImportJob(job_id="R00", mode="auto", doc_title="t")
        mgr._jobs["R00"].status = "running"
        mgr._jobs["P00"] = ImportJob(job_id="P00", mode="auto", doc_title="t")
        mgr._jobs["P00"].status = "paused"
        for i in range(49):
            mgr._jobs[f"Q{i:02d}"] = "x"  # 字符串占位（getattr status 兜底 → 非终态）
        mgr._prune()
        self.assertIn("R00", mgr._jobs)
        self.assertIn("P00", mgr._jobs)
        self.assertEqual(len(mgr._jobs), 51)  # 允许暂时超过 MAX_JOBS

        # 终态充足时：只逐出最旧终态，非终态与较新终态保留
        mgr2 = ImportJobManager()
        for i in range(45):
            mgr2._jobs[f"Q{i:02d}"] = "x"
        for i in range(10):
            done = ImportJob(job_id=f"D{i}", mode="auto", doc_title="t")
            done.status = "succeeded"
            done.finished_at = float(i)
            mgr2._jobs[f"D{i}"] = done
        mgr2._prune()  # 55 → 50：逐出 D0..D4
        self.assertEqual(len(mgr2._jobs), 50)
        self.assertNotIn("D0", mgr2._jobs)
        self.assertNotIn("D4", mgr2._jobs)
        self.assertIn("D5", mgr2._jobs)  # 较新终态保留
        self.assertIn("D9", mgr2._jobs)
        self.assertEqual(
            len([k for k in mgr2._jobs if k.startswith("Q")]), 45
        )  # 非终态全部保留

    def test_list_jobs_keeps_most_recent_50(self):
        """终态条目为主时仍维持 50 上限：最旧终态逐出、最新 50 个保留。"""
        mgr = ImportJobManager()
        for i in range(55):
            done = ImportJob(job_id=f"J{i:02d}", mode="auto", doc_title="t")
            done.status = "succeeded"
            done.finished_at = float(i)
            mgr._jobs[f"J{i:02d}"] = done
        mgr._prune()
        self.assertEqual(len(mgr._jobs), 50)
        self.assertNotIn("J00", mgr._jobs)  # 最旧被逐
        self.assertNotIn("J04", mgr._jobs)
        self.assertIn("J05", mgr._jobs)
        self.assertIn("J54", mgr._jobs)     # 最新保留


if __name__ == "__main__":
    unittest.main()
