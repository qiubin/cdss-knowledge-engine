# 图谱与规则审核台改造：专业化显示与清晰呈现

## Context

当前审核中心（`renderReviewCenter`，app.js L2674-2818）只是一张纯列表：看不到被审条目的内容，关系条目只显示关系名（看不出"谁→谁"），状态是英文原文（draft/stale/blocked），详情要么没有要么藏在编辑弹窗里，"待复核"条目看不出改了什么。本次改造聚焦两点：**条目显示专业化**（让医学知识专员一眼看懂每条是什么、什么状态、为什么被阻断）与**主从详情面板**（点选即见全貌）。不做审核人署名/身份流程，不做原文溯源（用户明确要求暂缓）。

## 现状关键事实（已实测）

- 前端单文件 IIFE：`src/api/static/app.js`（3781 行），整页 `innerHTML` 重绘；唯一模态 `#editorDialog`（index.html L68-76，全站复用）；toast（L97-102）；全站无 keydown 处理。
- `src/review/service.py`：
  - `list_items`（L149-219）节点条目含 name/code/node_type/status/dependent_rule_count；关系条目只有 `source_node_id/target_node_id` + relation_name，**不带端点名称与状态**；
  - `node_detail`（L221-237）返回 node/relations/dependents/audit，可直接喂详情面板；
  - `review_relation`（L432-492）approve 前置校验在 L453-464（两端节点须 active，否则 422）；
  - 无 relation_detail。
- `src/api/routes/kg_review.py`：Pydantic `ReviewActionRequest{action, reviewer="未署名", note=""}`（L19-22）等；POST `/api/review/bulk`（L173-215）逐条返回成功/失败。
- 状态机 `src/review/status.py`：图谱 draft/active/rejected/retired/stale；规则 draft/testing/active/blocked/rejected/retired。
- 规则侧已有接口：`GET /api/rules/{rule_id}`（rules.py L917）、`/{rule_id}/history`（L1068）、`/{rule_id}/graph-dependencies`（L1060）。
- 图谱侧无内容快照：`metadata.py` 只存 `content_hash + version`（`apply_import_review_metadata` L127-186，内容变化 active→stale 在 L140-141）。
- 审计：append-only `data/review/review_log.jsonl`；`node_detail` 已带 audit。

## 改动方案

### 1. 后端（为专业显示补数据）

**`src/review/service.py`**
- `list_items` 关系条目增加：`source_name`、`target_name`、`source_code`、`target_code`、`source_status`、`target_status`（同循环从 `graph["nodes"]` 取）→ 列表直接渲染"阿司匹林 —[抗血小板治疗]→ 急性心肌梗死"。
- 新增 `relation_detail(graph_filename, relation_id)`：返回 relation + 两端节点完整摘要 + dependents + audit（复用 `node_dependents`、`audit_records`）。

**`src/api/routes/kg_review.py`**
- 新增 `GET /api/kg/relations/{relation_id}`（query: graph_filename）→ relation_detail。
- 新增 `GET /api/kg/entities/{entity_id}/history`（query: graph_filename）→ 内容快照列表（供 stale diff）。
- 请求模型增加可选 `reason: str = ""`；路由把 note 组合为 `f"[{reason}] {note}"`（非空时）→ 审计 schema 不动，拒绝意见可统计。

**`src/review/metadata.py`**
- `apply_import_review_metadata` 内容变化分支（L140-141）覆写前，把旧内容 append 到 `data/review/entity_history/{graph_filename}/{entity_id}.jsonl`（仿 `src/rules/history.py` 的 seq/timestamp/content 模式）。快照仅存 diff 相关字段：name/code/node_type/attributes/source_refs；关系存 relation_name/attributes/source_refs。

### 2. 前端 `app.js` — 主从布局 + 专业化条目显示

**列表列改造（左栏）**
- 状态中文化徽章：draft=待审（蓝）、active=已通过（绿）、stale=待复核（橙）、blocked=被阻断（红）、rejected=已拒绝（灰红）、retired=已失效（灰）、testing=测试中（青）——统一一个 `STATUS_LABEL` 映射，图谱/规则共用。
- 节点条目：类型中文标签（疾病/症状/药物/检查/手术…由 node_type 映射）+ 名称 + 编码（等宽小字）。
- 关系条目：语义式单行"源名 —[关系名]→ 目标名"，两端节点状态异常（非 active）时在行内显示"等待端点通过"小标。
- 保留现有 7 个页签、搜索、批量勾选；新增列：依赖规则数、更新时间。

**详情面板（右栏，点选/J/K 即加载）**
- 节点详情：基本信息字段表（名称/编码/类型/定义/属性 key-value 表格化）、关联关系列表（语义式 + 各自状态徽章）、依赖规则列表（规则名 + 状态，被阻断标红）、历史审核记录（时间线）。
- 关系详情：顶部大字"源节点 —[关系名]→ 目标节点"，两端节点卡片（名称/编码/状态，可点击跳转该节点详情）、依赖规则、审核记录；端点非 active 时顶部黄条说明"该关系当前不可通过：XXX 尚未通过审核"。
- 规则详情：组合现有三个接口——规则定义结构化展示（条件→建议、参数）、依赖的图谱条目及状态、修改历史。
- stale 条目：详情面板顶部渲染字段级新旧对比（名称/编码/属性逐字段 old→new，source_refs 数量变化）；无快照时显示"无历史快照（旧数据导入）"。
- 面板顶部操作区：按状态显示可用动作按钮（通过/拒绝/失效/恢复，语义对齐 status.py 转移表）。

**操作交互**
- 拒绝统一走 `#editorDialog`：预置原因码 chips（编码错误 / 证据不足 / 关系方向错误 / 重复条目 / 超出指南范围 / 内容过期 / 其他）+ 备注 textarea，替代现有 `prompt()`；单条与批量共用；提交带 `reason`。
- 批量预检：提交 bulk 前对 relation 项用列表数据本地预检端点状态，弹框提示"共 N 条，预计 M 条因端点未通过而失败"，可选【仅提交可通过项】【取消】；后端逐条结果照常展示。
- 键盘导航（聚焦 input/textarea/dialog 时跳过）：`J/K` 上下选中并联动详情、`X` 勾选、`Enter` 跳到下一条待审；工具栏显示快捷键提示。
- 进度总览：头部按 summary 渲染 待审/已通过/被阻断/待复核 计数与比例条。

### 3. 样式 `src/api/static/style.css`

新增：`.review-layout`（master-detail grid）、`.review-detail`、`.relation-sem`（语义式关系行）、`.reason-chips`、`.diff-old/.diff-new`、`.kbd`、`.progress-bar`、`.endpoint-card`、`.attr-table`；沿用现有 `.panel/.btn/.badge/.table-wrap` 变量。

### 4. 测试

- `tests/test_review_api.py`：relation detail 端点、关系条目端点名称/状态字段、reason 组合进审计 note、entity history 端点。
- `tests/test_review_metadata.py`：内容变化时写快照、未变化不写。
- 手动：`./start-api.sh` 后浏览器走通（选中→详情→语义式关系显示→stale diff→拒绝原因框→批量预检）。

## 验收标准

1. 不点开任何弹窗，仅凭列表即可分辨每条的类型/状态/关系方向/阻塞原因。
2. 任意条目一次点选看到完整详情（内容、关联、依赖规则、历史）。
3. 关系不可通过时，行内与详情面板均提前可见原因，并可一键跳到阻塞节点。
4. stale 条目直接看到字段级改动。
5. 拒绝必选原因码，审计 note 带 `[原因码]`。
6. `python3 -m pytest -q` 全绿。

## 明确不做

审核人署名/身份流程、原文对照/溯源、PDF 渲染、领取/指派协作、审计筛选导出。
