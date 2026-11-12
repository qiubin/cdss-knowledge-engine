# 导入任务控制（开始/暂停/停止）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 指南导入三条入口（模板本地/大模型/一键自动）改造为可开始、暂停、停止的内存后台任务，前端两步式启动 + 真实进度轮询。

**Architecture:** 进程内 `ImportJobManager`（`{job_id → ImportJob}` 注册表 + asyncio.Task）承载任务生命周期；`ImportController.checkpoint()` 注入两个 LLM 分组循环（`controller=None` 零行为变化）；新路由 `/api/import/jobs` 管理任务；前端导入页改两步式并轮询真实进度。现有同步接口 `POST /api/pdf/upload`、`POST /api/auto/import` 保留不动。

**Tech Stack:** Python 3 / FastAPI / asyncio（现有栈，零新依赖）；前端 vanilla JS + node:test。

**Spec:** `docs/superpowers/specs/2026-11-12-import-job-control-design.md`

## Global Constraints

- 不引入新依赖；任务状态只存内存，服务重启即清空（spec 非目标）。
- 停止 = 干净丢弃：不写图谱/规则/doc_store；已上传 PDF 文件保留。
- `extract_llm` / `run_auto_pipeline` 的 `controller` 参数默认 `None`，None 时行为与现状完全一致（既有 240 个 pytest 必须全绿）。
- 现有同步接口与既有测试不改契约。
- 提交信息中文 feat/fix 前缀；测试命令 `python3 -m pytest <file> -v`、`node --test <file>`。
- spec 中"写库段 asyncio.shield"的落地方式：pipeline 写库阶段置 `controller.finalizing=True`，checkpoint 在 finalizing 时忽略停止标志（同步文件写入本身无 await 点、不可中断，语义等价且更简单）。

---

### Task 1: 任务控制器与任务管理器

**Files:**
- Create: `src/api/import_job_manager.py`
- Test: `tests/test_import_job_manager.py`

**Interfaces:**
- Produces:
  - `class ImportStopped(Exception)` — 停止时 checkpoint 抛出
  - `class ImportController` — `__init__(job: "ImportJob")`；`async checkpoint(stage: str = "", done: int = 0, total: int = 0) -> None`；`finalizing: bool`；`stop_requested: bool`；`log(message: str) -> None`
  - `class ImportJob` — dataclass：`job_id, mode, doc_title, status, stage, progress(done,total), logs, result, error, created_at, finished_at`
  - `class ImportJobManager` — `submit(mode: str, doc_title: str, runner: Callable[[ImportController], Awaitable[dict]]) -> ImportJob`；`get(job_id) -> ImportJob | None`；`list_jobs() -> list[ImportJob]`；`pause(job_id) -> ImportJob`；`resume(job_id) -> ImportJob`；`stop(job_id) -> ImportJob`（非法迁移抛 `ValueError("...")`，路由层转 409）；模块级单例 `manager = ImportJobManager()`
  - 状态常量：`QUEUED/RUNNING/PAUSING/PAUSED/SUCCEEDED/FAILED/STOPPED`

- [ ] **Step 1: 写失败测试**

```python
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
```

注意 `ImportJob` 需要可变控制属性：`job.control`（含 `pause_event: asyncio.Event`、`stop_requested: bool`）与 `job.task: asyncio.Task`。为让测试直接构造，`ImportJob` 用普通 dataclass（非 frozen），`control` 由 `submit` 创建。

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_import_job_manager.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'src.api.import_job_manager'`

- [ ] **Step 3: 实现 `src/api/import_job_manager.py`**

```python
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
        finished = [j for j in self._jobs.values() if j.status in _TERMINAL]
        finished.sort(key=lambda j: j.finished_at or 0)
        while len(self._jobs) > MAX_JOBS and finished:
            oldest = finished.pop(0)
            self._jobs.pop(oldest.job_id, None)

    def submit(
        self,
        mode: str,
        doc_title: str,
        runner: Callable[[ImportController], Awaitable[Dict[str, Any]]],
    ) -> ImportJob:
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
```

实现时按测试微调（如 `submit` 里 `asyncio.get_event_loop()` 在 3.9 IsolatedAsyncioTestCase 内可用；若报警告改用 `asyncio.get_running_loop()`）。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_import_job_manager.py -v`
Expected: 全部 PASS（8 个用例）

- [ ] **Step 5: 提交**

```bash
git add src/api/import_job_manager.py tests/test_import_job_manager.py
git commit -m "feat: 导入任务管理器与控制器（状态机+检查点暂停/停止）"
```

---

### Task 2: Pipeline 检查点注入

**Files:**
- Modify: `src/extraction/service.py:995`（`extract_llm` 签名与分组循环）
- Modify: `src/extraction/auto_pipeline.py:407`（`run_auto_pipeline` 签名、分组循环、写库段）
- Test: `tests/test_pipeline_checkpoints.py`

**Interfaces:**
- Consumes: Task 1 的 `ImportController`、`ImportStopped`
- Produces:
  - `extract_llm(chunks, template, doc_id, doc_title, config, controller=None)` — `controller` 为 `src.api.import_job_manager.ImportController`（用 `TYPE_CHECKING` 前置导入类型，运行时鸭子类型）
  - `run_auto_pipeline(chunks, doc_id, doc_title, config, *, graph_filename="", auto_activate=False, llm_call=None, data_root=None, auto_import_disease=True, controller=None)` — 同上
  - `extract()` 不改签名（本地模式不注入；llm 模式由调用方直接调 `extract_llm` 或经 `extract` 透传——**采用透传**：`extract(mode, chunks, template, doc_id, doc_title, config, controller=None)`）

- [ ] **Step 1: 写失败测试**

```python
"""Pipeline checkpoint injection: progress, pause-between-groups, stop-discard."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.import_job_manager import ImportController, ImportJob, ImportStopped
from src.extraction.service import extract_llm


def _controller() -> ImportController:
    job = ImportJob(job_id="JOB-T", mode="template-llm", doc_title="t")
    job.control.pause_event.set()
    return ImportController(job)


def _chunks(n: int) -> List[Dict[str, Any]]:
    return [{"chunk_id": f"C{i}", "text": "内容" * 50, "page_start": 1,
             "page_end": 1, "line_start": 1, "line_end": 2} for i in range(n)]


class ExtractLlmCheckpointTest(unittest.IsolatedAsyncioTestCase):
    async def test_progress_reported_per_group(self):
        controller = _controller()
        calls: List[Dict[str, int]] = []

        async def fake_llm(settings, prompt):
            await asyncio.sleep(0)
            return {"entities": [], "relations": [], "rules": []}

        original = sys.modules["src.extraction.service"].chat_json
        sys.modules["src.extraction.service"].chat_json = fake_llm
        self.addCleanup(setattr, sys.modules["src.extraction.service"], "chat_json", original)

        real_checkpoint = controller.checkpoint
        async def spy_checkpoint(stage="", done=0, total=0):
            calls.append({"done": done, "total": total})
            await real_checkpoint(stage=stage, done=done, total=total)
        controller.checkpoint = spy_checkpoint  # type: ignore[assignment]

        from src.extraction.repository import ExtractionTemplateRepository
        template = ExtractionTemplateRepository().get("GENERIC_GUIDELINE_V1")
        result = await extract_llm(
            _chunks(2), template, "DOC-T", "t",
            {"llm": {"api_key_env": "TEST_KEY", "max_chunks": 80, "max_chunk_chars": 100}},
            controller=controller,
        )
        self.assertEqual(result.entities, [])
        self.assertTrue(calls)
        self.assertEqual(calls[-1]["total"], len(calls))  # done 递增到 total

    async def test_stop_between_groups_raises(self):
        controller = _controller()

        async def fake_llm(settings, prompt):
            controller.job.control.stop_requested = True  # 第一组完成后请求停止
            return {"entities": [], "relations": [], "rules": []}

        original = sys.modules["src.extraction.service"].chat_json
        sys.modules["src.extraction.service"].chat_json = fake_llm
        self.addCleanup(setattr, sys.modules["src.extraction.service"], "chat_json", original)

        from src.extraction.repository import ExtractionTemplateRepository
        template = ExtractionTemplateRepository().get("GENERIC_GUIDELINE_V1")
        with self.assertRaises(ImportStopped):
            await extract_llm(
                _chunks(2), template, "DOC-T", "t",
                {"llm": {"api_key_env": "TEST_KEY", "max_chunk_chars": 100}},
                controller=controller,
            )

    async def test_controller_none_keeps_working(self):
        from src.extraction.repository import ExtractionTemplateRepository
        template = ExtractionTemplateRepository().get("GENERIC_GUIDELINE_V1")

        async def fake_llm(settings, prompt):
            return {"entities": [], "relations": [], "rules": []}
        original = sys.modules["src.extraction.service"].chat_json
        sys.modules["src.extraction.service"].chat_json = fake_llm
        self.addCleanup(setattr, sys.modules["src.extraction.service"], "chat_json", original)
        result = await extract_llm(
            _chunks(1), template, "DOC-T", "t",
            {"llm": {"api_key_env": "TEST_KEY"}}, controller=None,
        )
        self.assertEqual(result.entities, [])


if __name__ == "__main__":
    unittest.main()
```

（`run_auto_pipeline` 的检查点测试同构：注入 `llm_call` 假函数 + `controller`，断言进度与停止；写在该文件 `AutoPipelineCheckpointTest` 中，data_root 指向 tempdir。）

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_pipeline_checkpoints.py -v`
Expected: FAIL `TypeError: extract_llm() got an unexpected keyword argument 'controller'`

- [ ] **Step 3: 实现**

`src/extraction/service.py::extract_llm`（约 995 行）：

```python
async def extract_llm(
    chunks: List[Dict[str, Any]],
    template: ExtractionTemplate,
    doc_id: str,
    doc_title: str,
    config: Dict[str, Any],
    controller: Any = None,
) -> ProviderResult:
```

分组循环（约 1055 行 `for group in groups:`）改为：

```python
    total_groups = len(groups)
    for index, group in enumerate(groups):
        if controller is not None:
            await controller.checkpoint(
                stage=f"大模型抽取（第 {index + 1}/{total_groups} 组）",
                done=index, total=total_groups,
            )
        try:
            ...
```

`extract()`（约 1155 行）透传：

```python
async def extract(
    mode, chunks, template, doc_id, doc_title, config, controller: Any = None,
) -> ProviderResult:
    if mode == "llm":
        return await extract_llm(chunks, template, doc_id, doc_title, config, controller=controller)
    if mode == "local":
        return extract_local(chunks, template, doc_id, doc_title)
    raise ValueError(f"不支持的解析方式: {mode}")
```

`src/extraction/auto_pipeline.py::run_auto_pipeline`：签名加 `controller: Any = None`；分组循环（445 行）同样加 `enumerate` + checkpoint（stage=`自动抽取（第 i/N 组）`）；循环后、术语解析前 `if controller: await controller.checkpoint(stage="术语映射与图谱整理")`；写库段（约 666 行 `from ..api.routes.kg import ...` 之前）：

```python
    if controller is not None:
        controller.finalizing = True
        await controller.checkpoint(stage="写入图谱与规则（不可中断）")
```

注意 `finalizing=True` 必须在最后一批可中断操作之后、`import_graph` 调用之前设置，且之后不再把 `finalizing` 复位。

- [ ] **Step 4: 运行确认通过 + 既有测试回归**

Run: `python3 -m pytest tests/test_pipeline_checkpoints.py tests/test_auto_pipeline.py tests/test_llm_validation.py -v`
Expected: 新测试 PASS；既有测试 PASS（controller=None 路径零影响）

- [ ] **Step 5: 提交**

```bash
git add src/extraction/service.py src/extraction/auto_pipeline.py tests/test_pipeline_checkpoints.py
git commit -m "feat: 抽取管线注入任务检查点（进度上报/组间暂停/停止丢弃）"
```

---

### Task 3: 任务路由与作业执行体

**Files:**
- Create: `src/api/routes/import_jobs.py`
- Modify: `src/api/main.py:36`（include_router）
- Test: `tests/test_import_jobs_api.py`

**Interfaces:**
- Consumes: Task 1 `manager/ImportController`；Task 2 `extract_llm/run_auto_pipeline(controller=...)`；`src.api.routes.pdf` 的 `UPLOAD_DIR/parse_pdf/_persist_doc/_doc_store/_append_import_log`、`resolve_upload_graph_filename`；`src.extraction.repository.ExtractionConfigRepository/ExtractionTemplateRepository`
- Produces:
  - `POST /api/import/jobs`（multipart：`file`、`mode`∈{`template-local`,`template-llm`,`auto`}、`template_id?`、`graph_filename?`、`domain?`、`auto_activate?`）→ `201 {job_id, status}`（422 参数错误）
  - `GET /api/import/jobs` → `{jobs: [ImportJob.to_dict()...]}`
  - `GET /api/import/jobs/{job_id}` → `ImportJob.to_dict()`（404）
  - `POST /api/import/jobs/{job_id}/pause|resume|stop` → `to_dict()`（409 ValueError / 404）

- [ ] **Step 1: 写失败测试**（用 FastAPI TestClient + monkeypatch 假 runner，聚焦路由层；PDF 用 `data/uploads/` 真实小文件或 bytes 构造的 multipart——校验只看 `.pdf` 后缀，用 `io.BytesIO(b"%PDF-1.4 test")` + 文件名 `t.pdf`）

```python
"""Import job API: create->poll->pause->resume->stop over HTTP."""
from __future__ import annotations

import asyncio
import io
import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes import import_jobs as routes


class ImportJobsApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self._original_submit = routes.manager.submit
        self.addCleanup(setattr, routes.manager, "submit", self._original_submit)

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


if __name__ == "__main__":
    unittest.main()
```

（另加一个不 stub 的端到端用例：`mode=template-local` + 真 PDF bytes，轮询到 `succeeded`，断言 `result.entity_count` 字段存在——`parse_pdf` 对 `%PDF-1.4` 最小文件可能产出 0 页，此时断言 `result` 摘要结构而非数量。）

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_import_jobs_api.py -v`
Expected: FAIL `ModuleNotFoundError` / 路由 404

- [ ] **Step 3: 实现 `src/api/routes/import_jobs.py`**

```python
"""Async import jobs: start/pause/resume/stop over guide ingestion."""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.api.import_job_manager import ImportController, ImportJob, manager
from src.extraction.auto_pipeline import run_auto_pipeline
from src.extraction.graph_binding import resolve_upload_graph_filename
from src.extraction.repository import ExtractionConfigRepository
from .pdf import extraction_templates

router = APIRouter(prefix="/api/import", tags=["import-jobs"])

UPLOAD_DIR_PDF = Path(__file__).resolve().parents[3] / "data" / "uploads"
_VALID_MODES = {"template-local", "template-llm", "auto"}


async def _run_template_job(
    controller: ImportController, *, file_path: Path, doc_id: str, doc_title: str,
    mode: str, template_id: str, graph_name: str,
) -> Dict[str, Any]:
    """模板模式作业体：镜像 pdf.upload_pdf 的解析-抽取-落库链路。

    ``graph_name`` 已在 create 路由经 ``resolve_upload_graph_filename`` 校验。
    """
    from .pdf import (  # deferred: 保持本仓库函数内导入风格
        _doc_store, _persist_doc, extraction_templates, parse_pdf,
    )
    from src.extraction.service import extract

    config = ExtractionConfigRepository().load()
    parsed = parse_pdf(file_path, doc_id)
    await controller.checkpoint(stage="模板抽取", done=0, total=1)
    template = extraction_templates.get(template_id)
    provider = await extract(
        "llm" if mode == "template-llm" else "local",
        parsed["chunks"], template, doc_id, doc_title, config, controller=controller,
    )
    # —— 以下为写库段（不可中断）——
    controller.finalizing = True
    await controller.checkpoint(stage="保存解析结果")
    doc_record = {
        "doc_id": doc_id, "doc_title": doc_title, "file_path": str(file_path),
        "parsed": parsed, "entities": provider.entities,
        "suggested_relations": provider.suggested_relations,
        "suggested_rules": provider.suggested_rules,
        "extraction": {
            "mode": "llm" if mode == "template-llm" else "local",
            "domain": template.domain, "template_id": template.template_id,
            "template_name": template.name, "graph_filename": graph_name,
            "warnings": provider.warnings, "metadata": provider.metadata,
        },
    }
    _doc_store[doc_id] = doc_record
    _persist_doc(doc_record)
    return {
        "doc_id": doc_id, "doc_title": doc_title, "graph_filename": graph_name,
        "total_pages": parsed["total_pages"],
        "entity_count": len(provider.entities),
        "relation_count": len(provider.suggested_relations),
        "rule_count": len(provider.suggested_rules),
        "warnings": provider.warnings,
    }


async def _run_auto_job(
    controller: ImportController, *, file_path: Path, doc_id: str, doc_title: str,
    graph_filename: str, auto_activate: bool,
) -> Dict[str, Any]:
    """一键自动作业体：镜像 auto_import.auto_import 的 pipeline 调用。"""
    from .pdf import parse_pdf
    parsed = parse_pdf(file_path, doc_id)
    result = await run_auto_pipeline(
        parsed["chunks"], doc_id, doc_title,
        ExtractionConfigRepository().load(),
        graph_filename=graph_filename, auto_activate=auto_activate,
        controller=controller,
    )
    return result  # run_auto_pipeline 的返回 dict 即结果摘要


@router.post("/jobs", status_code=201)
async def create_import_job(
    file: UploadFile = File(...),
    mode: str = Form("template-local"),
    template_id: str = Form(""),
    graph_filename: str = Form(""),
    domain: str = Form(""),
    auto_activate: str = Form("false"),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持 PDF 文件")
    if mode not in _VALID_MODES:
        raise HTTPException(422, f"mode 必须是 {'/'.join(sorted(_VALID_MODES))}")
    if mode in {"template-local", "template-llm"} and not template_id:
        raise HTTPException(422, "模板模式必须提供 template_id")
    resolved_graph = ""
    if mode in {"template-local", "template-llm"}:
        template = extraction_templates.get(template_id)
        if template is None:
            raise HTTPException(422, f"解析模板不存在: {template_id}")
        try:
            resolved_graph = resolve_upload_graph_filename(template, graph_filename or "")
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    doc_title = re.sub(r"\.pdf$", "", file.filename, flags=re.IGNORECASE)
    doc_id = f"DOC-{uuid.uuid4().hex[:8]}"
    UPLOAD_DIR_PDF.mkdir(parents=True, exist_ok=True)
    file_path = UPLOAD_DIR_PDF / f"{doc_id}.pdf"
    file_path.write_bytes(await file.read())

    def runner(controller: ImportController):
        if mode == "auto":
            return _run_auto_job(
                controller, file_path=file_path, doc_id=doc_id, doc_title=doc_title,
                graph_filename=graph_filename,
                auto_activate=auto_activate.lower() in {"1", "true", "yes"},
            )
        return _run_template_job(
            controller, file_path=file_path, doc_id=doc_id, doc_title=doc_title,
            mode=mode, template_id=template_id, graph_name=resolved_graph,
        )

    job = manager.submit(mode, doc_title, runner)
    _log_job_created(job, doc_id)
    return {"job_id": job.job_id, "status": job.status, "doc_id": doc_id}


def _log_job_created(job: ImportJob, doc_id: str) -> None:
    from .pdf import _append_import_log
    _append_import_log({
        "doc_id": doc_id, "doc_title": job.doc_title, "status": "started",
        "mode": job.mode.replace("template-", ""), "job_id": job.job_id,
    })


@router.get("/jobs")
async def list_import_jobs():
    return {"jobs": [job.to_dict() for job in manager.list_jobs()]}


@router.get("/jobs/{job_id}")
async def get_import_job(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在（服务可能已重启）")
    return job.to_dict()


def _dispatch(job_id: str, action: str) -> ImportJob:
    """pause/resume 为同步状态迁移；错误转 404/409。"""
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在（服务可能已重启）")
    try:
        return manager.pause(job_id) if action == "pause" else manager.resume(job_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/jobs/{job_id}/pause")
async def pause_import_job(job_id: str):
    return _dispatch(job_id, "pause").to_dict()


@router.post("/jobs/{job_id}/resume")
async def resume_import_job(job_id: str):
    return _dispatch(job_id, "resume").to_dict()


@router.post("/jobs/{job_id}/stop")
async def stop_import_job(job_id: str):
    if manager.get(job_id) is None:
        raise HTTPException(404, "任务不存在（服务可能已重启）")
    try:
        job = await manager.stop(job_id)  # 等待 cancel 落地（finalizing 时先跑完）
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return job.to_dict()
```

**实现提示（执行者注意）**：
- `src/api/main.py` 在 `app.include_router(auto_import.router)` 之后加 `from src.api.routes import import_jobs` 与 `app.include_router(import_jobs.router)`（跟随现有 import 风格）。
- `manager.pause/resume` 为同步方法、`manager.stop` 为 async——路由层分别处理（如上）。
- import-log 的 `status` 新值 `started`：前端历史表（Task 4）识别；`_append_import_log` 本身不限枚举。
- 测试里 `routes.ImportJob` 直接构造后放入 `manager._jobs`，绕过 submit——断言只针对路由层序列化与状态码。

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `python3 -m pytest tests/test_import_jobs_api.py tests/test_import_job_manager.py -v && python3 -m pytest tests/ -q`
Expected: 新旧全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/api/routes/import_jobs.py src/api/main.py tests/test_import_jobs_api.py
git commit -m "feat: 导入任务 API（创建/轮询/暂停/继续/停止）"
```

---

### Task 4: 前端两步式与真实进度

**Files:**
- Modify: `src/api/static/app.js`（renderImport 面板、uploadFile→prepareUpload/startJob、autoUploadFile、startImportJob/finishImportJob/renderImportJob、导入历史徽标）
- Test: `tests/js/import_job_panel.test.js`

**Interfaces:**
- Consumes: Task 3 全部 HTTP 端点
- Produces（app.js 内部函数，供测试断言）:
  - `jobControlButtons(status)` → `{pause: bool, resume: bool, stop: bool, start: bool}` 
  - `jobProgressText(job)` → `"抽取（3/10 组）· 进行中"` 形式字符串
  - `state.pendingUpload`（{file, mode, templateId, graphFilename}）与 `state.importJob`

- [ ] **Step 1: 写失败测试** `tests/js/import_job_panel.test.js`

```javascript
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(
  path.join(__dirname, "../../src/api/static/app.js"), "utf8"
);
// 提取纯函数：app.js 内以 "function jobControlButtons" / "function jobProgressText" 定义
function extract(name) {
  const match = source.match(new RegExp(`function ${name}\\([^)]*\\)\\s*\\{`));
  return match;
}

test("jobControlButtons 映射各状态按钮", () => {
  assert.ok(extract("jobControlButtons"), "缺少 jobControlButtons");
  const fn = new Function("status", `
    ${source.match(/function jobControlButtons[\s\S]*?\n  }/)[0]}
    return jobControlButtons(status);
  `);
  assert.deepStrictEqual(fn("running"), { pause: true, resume: false, stop: true, start: false });
  assert.deepStrictEqual(fn("pausing"), { pause: false, resume: true, stop: true, start: false });
  assert.deepStrictEqual(fn("paused"), { pause: false, resume: true, stop: true, start: false });
  assert.deepStrictEqual(fn("queued"), { pause: true, resume: false, stop: true, start: false });
  assert.deepStrictEqual(fn("succeeded"), { pause: false, resume: false, stop: false, start: false });
  assert.deepStrictEqual(fn("stopped"), { pause: false, resume: false, stop: false, start: true });
  assert.deepStrictEqual(fn("failed"), { pause: false, resume: false, stop: false, start: true });
});

test("jobProgressText 输出阶段与分组进度", () => {
  assert.ok(extract("jobProgressText"), "缺少 jobProgressText");
  const fn = new Function("job", `
    ${source.match(/function jobProgressText[\s\S]*?\n  }/)[0]}
    return jobProgressText(job);
  `);
  assert.match(fn({ status: "running", stage: "大模型抽取（第 3/10 组）", progress: { done: 2, total: 10 } }), /3\/10/);
  assert.match(fn({ status: "paused", stage: "任意", progress: { done: 0, total: 0 } }), /已暂停/);
});
```

- [ ] **Step 2: 运行确认失败**

Run: `node --test tests/js/import_job_panel.test.js`
Expected: FAIL `缺少 jobControlButtons`

- [ ] **Step 3: 实现（要点）**

在 app.js 中（`templateGraphFilename` 辅助函数附近）加两个纯函数（保持两空格缩进、挂在 renderApp 作用域内，正则可提取）：

```javascript
  function jobControlButtons(status) {
    const map = {
      queued: { pause: true, resume: false, stop: true, start: false },
      running: { pause: true, resume: false, stop: true, start: false },
      pausing: { pause: false, resume: true, stop: true, start: false },
      paused: { pause: false, resume: true, stop: true, start: false },
      succeeded: { pause: false, resume: false, stop: false, start: false },
      failed: { pause: false, resume: false, stop: false, start: true },
      stopped: { pause: false, resume: false, stop: false, start: true },
    };
    return map[status] || map.succeeded;
  }

  function jobProgressText(job) {
    const { done = 0, total = 0 } = job.progress || {};
    const suffix = job.status === "paused" ? " · 已暂停" : job.status === "pausing" ? " · 暂停中" : "";
    return total ? `${job.stage || "处理中"}（${done}/${total} 组）${suffix}` : `${job.stage || "处理中"}${suffix}`;
  }
```

面板与流程改造（`renderImport` / `uploadFile` / `autoUploadFile` / `startImportJob`）：
1. 选文件后不调上传接口：`state.pendingUpload = { file, kind: "template" | "auto", mode: $("#uploadMode").value, templateId: $("#uploadTemplate").value, graphFilename: 当前目的图谱选择 }`，渲染"待导入"卡片（文件名 + 模式/模板/目的图谱摘要 + 「开始」按钮 `#jobStartBtn`）。
2. `#jobStartBtn.onclick = startImportJob`：构建 FormData（file、mode 映射 `template-local`/`template-llm`/`auto`、template_id、graph_filename、auto_activate），`POST /api/import/jobs` 得 `{job_id}`，启动轮询。
3. 轮询 `pollImportJob(jobId)`：`setInterval` 1500ms `GET /api/import/jobs/{id}` → `state.importJob = data` → `renderImportJob()`；终态停止轮询；404 时提示"服务已重启，任务终止"。
4. `renderImportJob` 重写：真实 `job.stage` + `done/total` 进度条（复用现有进度条 DOM 结构）+ `job.logs` 尾部 10 条 + 按钮区用 `jobControlButtons(job.status)` 渲染（暂停→`POST pause`、继续→`POST resume`、停止→`POST stop` 后立即刷新一次）+ `jobProgressText`；删除原 `startImportJob` 里的假进度 `setInterval`/里程碑数组。
5. 成功终态：模板模式把 `job.result` 适配进 `state.doc` 并 `renderImportResult()`（result 摘要缺 chunks 等字段——结果区仅展示摘要统计与 `doc_id`，点"查看详情"跳 `GET /api/pdf/{doc_id}/result`）；自动模式复用现有 `state.autoResult` 横幅结构。
6. 导入历史表：`log.status === "started" ? "已开始" : log.status === "stopped" ? '<span class="badge">已停止</span>'`（沿用现有三元链）。
7. 一键自动入口 `autoUploadFile` 同样改两步式（待导入卡片 kind=auto）。

- [ ] **Step 4: 运行确认通过 + JS 回归**

Run: `node --check src/api/static/app.js && node --test tests/js/`
Expected: 新测试 PASS，原有 33 个 JS 测试 PASS

- [ ] **Step 5: 提交**

```bash
git add src/api/static/app.js tests/js/import_job_panel.test.js
git commit -m "feat: 导入页两步式启动与任务控制（真实进度轮询+暂停/停止按钮）"
```

---

### Task 5: 端到端验证

**Files:** 无新文件（验证任务）

- [ ] **Step 1: 重启服务**

```bash
# 停掉现有 ./start-api.sh 后台任务，重新启动
./start-api.sh  # 后台运行
```

- [ ] **Step 2: curl 走通本地模式任务全生命周期**

```bash
PDF=$(ls -S data/uploads/*.pdf | tail -1)
# 创建（template-local 走全程只需数秒）
curl -s -X POST http://127.0.0.1:8001/api/import/jobs \
  -F "file=@$PDF" -F "mode=template-local" -F "template_id=GENERIC_GUIDELINE_V1" \
  -F "graph_filename=KG-E2E-JOBTEST"
# → 记下 job_id
curl -s http://127.0.0.1:8001/api/import/jobs/{job_id}   # 轮询至 succeeded
curl -s -X POST http://127.0.0.1:8001/api/import/jobs/{job_id}/stop   # 已终态 → 409（预期）
```

Expected: `succeeded` 时 `result.graph_filename == "KG-E2E-JOBTEST.json"`、`entity_count > 0`；`data/kg/KG-E2E-JOBTEST.json` 不应被创建（模板上传只进审核区，不写图谱——验证这一点）。

- [ ] **Step 3: 验证停止丢弃**

再建一个同模式任务，立即 `stop`，确认：状态 `stopped`、`result == null`、`GET /api/pdf/documents` 列表**不含**该 doc_id、`data/rules/managed_rules.json` 无变化（`git status` 干净）。

- [ ] **Step 4: 浏览器手动验证**

打开 http://127.0.0.1:8001 导入页：选 PDF → 待导入卡片出现 → 开始 → 进度/日志实时刷新（本地模式秒级完成也走同一 UI）；再以 `mode=auto` 重复观察暂停/继续按钮。截图留档 `artifacts-import-job.png`。

- [ ] **Step 5: 收尾提交**

```bash
git add -A && git commit -m "chore: 导入任务控制端到端验证收尾"  # 如有截图/小修
git push origin main
```

验证产生的临时任务/文档按系统设计留在导入历史；如污染规则库（auto 模式直接写库），用图谱页危险操作面板清除 `KG-E2E-JOBTEST` 类产物。
