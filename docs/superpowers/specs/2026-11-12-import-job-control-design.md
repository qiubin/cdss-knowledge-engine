# 导入任务控制（开始/暂停/停止）设计

日期：2026-11-12
状态：已批准（对话确认四项决策：三个入口全覆盖、两步式开始、干净丢弃、内存态）

## 目标

指南导入的三条入口（模板模式上传、大模型解析、一键自动生成）从同步阻塞 HTTP 请求改造为**可控制的内存后台任务**：用户选文件后点「开始」启动；运行中可「暂停/继续」「停止」；停止即干净丢弃（不写库、不产生草稿）。

## 非目标

- 跨服务重启续跑：重启即任务终止，标记 `stopped`，重新点开始即可。
- 停止后保留已抽取部分：两个 pipeline 都是最后才写库，停止语义就是全量丢弃。
- 任务队列中间件（Celery/RQ）：单机单用户工具，进程内 asyncio 注册表足够。
- 修改现有同步接口契约：`POST /api/pdf/upload`、`POST /api/auto/import` 保留原样（既有测试与调用方不受影响），前端全面切换到新任务接口。

## 架构

### 任务管理器（新模块 `src/api/import_job_manager.py`）

- 模块级单例 `ImportJobManager`：内存注册表 `{job_id → ImportJob}`。
- `ImportJob`（dataclass）：`job_id`、`mode`（`template-local` / `template-llm` / `auto`）、`status`、`stage`（当前阶段中文描述）、`progress`（`{done, total}` 分组进度）、`logs`（时间戳+消息环形列表，上限 200 条）、`result`（成功时的摘要 dict）、`error`、`doc_id`、`created_at/finished_at`、`task: asyncio.Task`、控制标志（`pause_event: asyncio.Event`、`stop_requested: bool`）。
- 状态机：`queued → running → (pausing → paused ⇄ running) → succeeded | failed | stopped`（`pausing` 是暂停请求已接受、等待下一个检查点生效的瞬态）。非法迁移（如暂停已完成任务）返回 409。
- 控制器 `ImportController`（每个任务一个，传入 pipeline）：
  - `await checkpoint(stage=None, done=None, total=None)`：先更新进度/阶段 → 若 `stop_requested` 抛 `ImportStopped` → 若未 set `pause_event` 则 `await` 等待（恢复后继续）。
  - 停止 = 设 `stop_requested` + `task.cancel()`：飞行中的 httpx LLM 调用被即时中断；**例外**——pipeline 已进入最终写库阶段（毫秒级窗口），取消在 `asyncio.shield` 保护下等待写库完成后按 `succeeded` 处理，UI 如实显示。
- 任务清理：保留最近 50 个终态任务供前端回看，超出丢弃最旧。

### Pipeline 注入（检查点）

两处 LLM 分组循环注入 `controller=None` 可选参数（None = 完全零行为变化，既有调用与测试不受影响）：

- `src/extraction/service.py::extract_llm`：`for group in groups:` 循环体开头 `await controller.checkpoint(...)`，进度 `done=i/total=len(groups)`。
- `src/extraction/auto_pipeline.py::run_auto_pipeline`：同上；pipeline 各阶段（抽取分组循环 → 术语映射 → 图谱写入 → 规则生成 → 方案生成）间也调 `checkpoint` 更新 `stage`。写库段（图谱/规则落盘）用 `asyncio.shield` 包裹。
- `extract_local`（本地模板解析，无 LLM、秒级完成）不注入检查点；`parse_pdf`（CPU 解析）同理——本地模式的任务生命周期短到不需要控制。

### API（新路由 `src/api/routes/import_jobs.py`，挂 `/api/import`）

| 方法 | 路径 | 行为 |
|---|---|---|
| POST | `/api/import/jobs` | multipart：`file` + `mode`（`template-local`/`template-llm`/`auto`）+ `template_id`/`graph_filename`/`domain`/`auto_activate`（按模式取用）。校验参数后创建任务并立即返回 `{job_id, status: "queued"}`（约 1 秒内完成文件落盘与任务创建；`parse_pdf` 在任务内执行） |
| GET | `/api/import/jobs` | 任务列表（最近 50，新在前） |
| GET | `/api/import/jobs/{id}` | 单任务：状态、阶段、进度、日志尾、结果摘要 |
| POST | `/api/import/jobs/{id}/pause` | `running → paused`（在下一检查点生效，状态先标 `pausing` 再落 `paused`） |
| POST | `/api/import/jobs/{id}/resume` | `paused → running` |
| POST | `/api/import/jobs/{id}/stop` | 设停止标志 + cancel；返回最终状态（`stopped` 或收尾完成的 `succeeded`） |

- 成功结果摘要：模板模式 = `ExtractionResult` 关键字段（doc_id、页数、实体/关系/规则数、graph_filename）；自动模式 = 现有 auto 结果字段（含 package_id、active_rule_count）。
- 导入日志（import-log）：start/pause/resume/stop(丢弃)/success/failed 均留痕，`mode` 沿用现有值（`local`/`llm`/`auto`），停止状态记 `status: "stopped"`。
- 同一时刻允许多个任务并行（不同指南）；前端导入面板一次只展示当前任务。

### 前端（`src/api/static/app.js` 导入页）

- **两步式**：选文件/拖拽后不再立即上传，而是出现「待导入」卡片：文件名 + 模式/模板/目的图谱摘要 + 「开始」按钮；一键自动入口同样改造。
- 任务面板改为**真实轮询**（`GET /jobs/{id}`，1.5s 间隔）：进度条 = `done/total` 分组真实值 + 当前阶段文案 + 实时日志尾（替换现有假进度定时器）。
- 按钮组随状态切换：`queued/running → 暂停+停止`；`pausing/paused → 继续+停止`；终态显示结果（复用现有 `renderImportResult` / auto 结果横幅）。
- 导入历史表新增 `stopped` 灰色徽标。

## 错误处理

- 任务内任何异常 → `failed` + error 摘要 + import-log 留痕；不抛到路由层。
- `ImportStopped` → `stopped`；已写盘的 PDF 文件保留（可重新开始），doc 记录不落库（清理半成品 doc_store 条目）。
- 停止请求对已终态任务返回 409。
- 服务重启：注册表清空；前端轮询 404 时回落到列表页并提示「服务已重启，任务终止」。

## 测试

- pytest（`tests/test_import_job_manager.py`）：
  - 状态机迁移合法性（暂停/继续/停止/409）
  - `ImportController.checkpoint`：停止标志抛 `ImportStopped`；暂停时阻塞直到 resume；`controller=None` 路径零影响
  - 注入假 LLM（`llm_call` 参数）的 pipeline 级测试：分组进度上报、暂停发生在组间、停止后无任何写库产物
  - 路由级：创建任务立即返回 job_id、dry 状态查询、pause/resume/stop 序列
- JS（`tests/js/import_job_panel.test.js`，node:test）：状态→按钮组映射、进度渲染格式。
- 手动 e2e：真实 PDF + 本地模式全流程；LLM 模式（如可用）暂停/停止验证。

## 影响面

- 新文件：`src/api/import_job_manager.py`、`src/api/routes/import_jobs.py`、两个测试文件
- 修改：`src/extraction/service.py`（extract_llm 加参+检查点）、`src/extraction/auto_pipeline.py`（同）、`src/api/main.py`（注册路由）、`src/api/static/app.js`（导入页两步式+任务面板）、`src/api/routes/pdf.py`（import-log 增加 stopped 状态支持，若需要）
- 不变：同步上传/自动导入接口、审核与入库链路、知识包流程
