"""In-memory import job lifecycle: start/pause/resume/stop for guide imports.

Jobs die with the process (no persistence by design); stopping means clean
discard — pipelines only persist at the very end, guarded by
``ImportController.finalizing``.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

QUEUED = "queued"; RUNNING = "running"; PAUSING = "pausing"; PAUSED = "paused"
SUCCEEDED = "succeeded"; FAILED = "failed"; STOPPED = "stopped"
_TERMINAL = {SUCCEEDED, FAILED, STOPPED}
MAX_LOGS = 200
MAX_JOBS = 50


class ImportStopped(Exception):
    """Raised at a checkpoint after stop was requested."""


@dataclass
class JobControl:
    pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    stop_requested: bool = False


@dataclass
class ImportJob:
    job_id: str
    mode: str
    doc_title: str
    status: str = QUEUED
    stage: str = "排队中"
    progress: Dict[str, int] = field(default_factory=lambda: {"done": 0, "total": 0})
    logs: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    control: JobControl = field(default_factory=JobControl)
    task: Optional[asyncio.Task] = None

    def log(self, message: str, level: str = "info") -> None:
        self.logs.append({"time": time.strftime("%H:%M:%S"), "message": message, "level": level})
        if len(self.logs) > MAX_LOGS:
            del self.logs[: len(self.logs) - MAX_LOGS]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id, "mode": self.mode, "doc_title": self.doc_title,
            "status": self.status, "stage": self.stage, "progress": self.progress,
            "logs": self.logs[-30:], "result": self.result, "error": self.error,
            "created_at": self.created_at, "finished_at": self.finished_at,
        }


class ImportController:
    """Cooperative checkpoint injected into pipeline loops."""

    def __init__(self, job: ImportJob):
        self.job = job
        self.finalizing = False

    def log(self, message: str, level: str = "info") -> None:
        self.job.log(message, level)

    async def checkpoint(self, stage: str = "", done: int = 0, total: int = 0) -> None:
        if stage:
            self.job.stage = stage
        if total:
            self.job.progress = {"done": done, "total": total}
        if self.job.control.stop_requested and not self.finalizing:
            raise ImportStopped()
        if not self.job.control.pause_event.is_set():
            self.job.status = PAUSED
            self.job.log(f"已暂停：{stage or self.job.stage}")
            await self.job.control.pause_event.wait()
            self.job.status = RUNNING
            self.job.log("已继续")


class ImportJobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, ImportJob] = {}

    def _prune(self) -> None:
        finished = [j for j in self._jobs.values() if getattr(j, "status", None) in _TERMINAL]
        finished.sort(key=lambda j: j.finished_at or 0)
        while len(self._jobs) > MAX_JOBS:
            if finished:
                oldest = finished.pop(0)
                self._jobs.pop(oldest.job_id, None)
            else:
                self._jobs.pop(next(iter(self._jobs)), None)

    def submit(
        self,
        mode: str,
        doc_title: str,
        runner: Callable[[ImportController], Awaitable[Dict[str, Any]]],
        on_finish: Optional[Callable[[ImportJob], None]] = None,
    ) -> ImportJob:
        """on_finish（可选）在 finally 中收到终态 job，供调用方做日志等留痕。"""
        job = ImportJob(job_id=f"JOB-{uuid.uuid4().hex[:8]}", mode=mode, doc_title=doc_title)
        job.control.pause_event.set()
        self._jobs[job.job_id] = job
        self._prune()
        controller = ImportController(job)

        async def _wrapped() -> None:
            job.status = RUNNING
            job.log(f"任务开始：{doc_title}")
            try:
                job.result = await runner(controller)
                job.status = SUCCEEDED
                job.stage = "完成"
            except ImportStopped:
                job.status = STOPPED
                job.stage = "已停止（内容已丢弃）"
                job.log("任务被用户停止，未写入任何数据", "warn")
            except asyncio.CancelledError:
                if job.control.stop_requested:
                    job.status = STOPPED
                    job.stage = "已停止（内容已丢弃）"
                else:
                    job.status = FAILED
                    job.error = "任务被取消"
                raise
            except Exception as error:  # noqa: BLE001 - job boundary
                job.status = FAILED
                job.error = str(error)[:300]
                job.log(f"任务失败：{job.error}", "error")
            finally:
                job.finished_at = time.time()
                if on_finish is not None:
                    try:
                        on_finish(job)
                    except Exception as hook_error:  # noqa: BLE001 - 留痕失败不拖垮生命周期
                        job.log(f"收尾回调失败：{hook_error}", "warn")
                self._prune()

        job.task = asyncio.get_event_loop().create_task(_wrapped())
        return job

    def get(self, job_id: str) -> Optional[ImportJob]:
        return self._jobs.get(job_id)

    def list_jobs(self) -> List[ImportJob]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    async def pause(self, job_id: str) -> ImportJob:
        job = self._require(job_id)
        if job.status != RUNNING:
            raise ValueError(f"任务当前状态 {job.status} 不可暂停")
        job.status = PAUSING
        job.control.pause_event.clear()
        job.log("收到暂停请求，将在当前分组完成后暂停")
        return job

    async def resume(self, job_id: str) -> ImportJob:
        job = self._require(job_id)
        if job.status not in {PAUSING, PAUSED}:
            raise ValueError(f"任务当前状态 {job.status} 不可继续")
        job.control.pause_event.set()
        job.status = RUNNING
        return job

    async def stop(self, job_id: str) -> ImportJob:
        job = self._require(job_id)
        if job.status in _TERMINAL:
            raise ValueError(f"任务已结束（{job.status}）")
        job.control.stop_requested = True
        job.control.pause_event.set()  # 唤醒暂停中的任务以便其收到停止
        job.task.cancel()
        for _ in range(200):  # 等 cancel 落地（finalizing 时任务会先跑完）
            if job.status in _TERMINAL:
                break
            await asyncio.sleep(0.02)
        return job

    def _require(self, job_id: str) -> ImportJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise ValueError("任务不存在")
        return job


manager = ImportJobManager()
