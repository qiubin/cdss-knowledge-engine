# 审核台关系审核升级：独立页签、类型筛选、可疑预检、按病分组、批量统计

日期：2026-11-12
状态：已批准（对话确认完整方案一次做，①~⑤ 全量）

## 背景与问题

关系的审核后端能力已完整（五态状态机、单条/批量审核接口、端点未通过不许通过、
节点退回→关系待复核、关系拒绝→规则阻断，均有测试覆盖），但前端审核台把节点与
关系混在「待审图谱」一张表里，作为审核工作流存在四个痛点：

1. **混排**：关系夹在 421 个节点中间不显眼（当前图谱 989 条关系，105 条待审），
   关系审核无法作为独立任务推进；
2. **筛不了**：无「关系类型」筛选；「实体类型」筛选按 `item.node_type` 匹配，
   关系的 node_type 恒为 "relation"，一旦选择实体类型关系全部消失；
3. **不成体系**：审关系的自然单位是"围绕一个主体疾病/证候核对它的知识网"，
   现有列表按名称排序，做不到按病走；
4. **没有预检**：方向接反、无溯源、自环、重复连线等脏边要人工逐条发现。
   真实数据已存在此类边：`DISEASE_HAS_DIAGNOSTIC_EVIDENCE`（疾病→疾病）、
   `DISEASE_HAS_TREATMENT_PLAN` 指向 medication/intervention、
   `TREATMENT_PLAN_HAS_MONITORING` 以 disease 为源。

## 目标

1. 关系审核有专属页签与进度可见；
2. 按关系类型、按端点实体类型筛选，配合全选批量清一类关系；
3. 系统自动预检可疑关系并打徽章，支持「只看可疑」；
4. 按疾病/证候分组的知识卡片视图，贴近医学审核习惯；
5. 批量护栏提示与「关系类型 × 状态」概览。

## 非目标

- 不改审核状态机、级联规则（stale 传导）、规则阻断逻辑——已存在且测试覆盖；
- 不新增"手工造关系/改关系属性"能力（造关系走导入链路，审核台只做
  通过/拒绝/失效/恢复）;
- 不改数据结构，无迁移（relation 的 review_* 字段已存在）；
- 不做跨图谱的关系一致性比对。

## A. 「关系审核」独立页签

- `renderReviewCenter` tabs 新增 `["relations", "关系审核"]`（排在「待审图谱」后）：
  queue 以 `item_type=graph_relation` 拉取，状态过滤 draft/stale（与现 graph 页签口径一致）；
- 「待审图谱」页签改为仅 graph_node（draft/stale）——关系不再混排；
- 「已通过 / 待复核 / 孤儿条目」页签保持节点+关系混合（回看场景），关系行继续用
  `relationSemHtml` 语义渲染；
- 「关系审核」页签按钮带待办数徽标（draft+stale，取自 `queue.summary.graph_relations`）。

## B. 关系类型与端点类型筛选

- 后端 `ReviewService.list_items` 增参 `relation_code`：relation 条目按 code 精确匹配；
  `build_queue` 透传；`/api/review/queue` 与 `/api/kg/review/items` 路由加同名 query 参数；
- `node_type` 语义升级：relation 条目改为「源或目标端点类型匹配」
  （source/target 节点的 node_type 命中即保留）；node 条目仍匹配自身；
- 前端「关系审核」页签筛选栏：新增「关系类型」下拉（选项从当前 items 的
  relation_code 收集，标签用 RELATION_LABELS，回退 relation_name，按条数降序）；
  「实体类型」下拉选项改从 relation 两端类型收集；两筛选可叠加，配合全选批量；
- 节点页签不显示「关系类型」下拉。

## C. 可疑关系预检

新增 `src/review/precheck.py`：

- `load_endpoint_type_map()`：读 `data/templates/extraction_templates.json`，合并全部
  模板的 relations → `{relation_code: {source: [...], target: [...]}}`（同名 code 各模板
  类型取并集）；加载失败返回空映射并记 warning（降级：不标方向可疑，不阻断）；
- `relation_flags(relation, source_node, target_node, endpoint_type_map, duplicate_pairs)`
  返回 flag 列表，每项 `{code, message}`：
  - `endpoint_inactive`：源或目标节点状态 ≠ active；
  - `no_source_refs`：`source_refs` 为空（说不出指南出处）；
  - `direction_suspect`：映射中有该 code 且实际端点类型不符，message 给出
    "预期 源类型→目标类型，实际 源→目标"（类型用中文名）；
  - `self_loop`：source == target；
  - `duplicate_pair`：同 (source, target) 对存在 ≥2 种 relation_code，该对全部边标记；
- `ReviewService.list_items` 对 relation 条目附加 `flags` 字段（node 条目为空数组），
  queue 透传；
- 前端：关系行显示 ⚠ 徽章（title=各 flag message）；「关系审核」页签加「只看可疑」
  开关（客户端过滤 flags 非空）并显示可疑计数；徽章在所有含关系行的页签可见。

## D. 按疾病/证候分组（知识卡片）

`kg_review.py` 新增两个只读端点：

- `GET /api/kg/relations/groups?graph_filename=X[&subject_type=disease|syndrome|all]`
  （默认 disease+syndrome）：subjects = 作为某关系源节点、node_type 命中的节点，
  每项 `{node_id, name, code, node_type, review_status, out_relation_count,
  pending_count(draft+stale), suspect_count(flags 非空数)}`，按 pending 降序、名称升序；
- `GET /api/kg/relations/groups/{subject_node_id}?graph_filename=X`：
  - 直接出边按 relation_code 分组为 sections，每 section
    `{relation_code, relation_name, total, status_counts, items:[关系条目+flags+端点摘要]}`；
  - 目标端为「容器」节点（treatment_plan / care_pathway / order_set / lab / exam）的边，
    额外附该容器的出边为嵌套小节（一层展开，小节标题含容器名，如"其他肥厚型心肌病
    治疗方案 · 含药品"、"肌钙蛋白 · 形成证据"），容器自身的边也保留在原 section；
    不在任何疾病卡片可达的关系（如无疾病挂靠的检验）仍可经「关系审核」页签审核；
  - 前端该页签按单图谱展示：取「图谱」筛选值，未选则回退当前全局图谱并在页头提示；
- 前端新页签 `["bydisease", "按疾病分组"]`（排在「关系审核」后）：
  - 左列 subject 卡（名称+码+类型徽标+待办数+⚠ 数），带搜索框过滤；
  - 右侧知识卡片：每 section 一张——标题（关系类型中文标签 + 计数）+「整组通过 /
    整组拒绝」+ 条目行（语义行 + 状态 + 单条按钮 + ⚠ 徽章）；嵌套小节折叠展示；
    整组拒绝/失效复用原因码弹窗（与审核台批量拒绝一致）；
  - 审核动作走既有 review_relation / bulk 接口；成功后局部刷新当前卡片与左列计数；
  - 批量通过沿用端点拦截 confirm。

## E. 批量护栏与统计

- 批量通过前的端点拦截 confirm 保留（现实现已含跳过数说明）；
- 「关系审核」页签顶部加可折叠「关系类型 × 状态」概览：客户端从当前 items 聚合
  （行=关系类型中文标签，列=草稿/待复核/已通过/已拒绝/已失效）；queue 截断时
  复用 truncated 提示"超出展示上限，请用筛选缩小范围"。

## 错误处理

- endpoint_type_map 加载失败 → 空映射降级（仅少标 direction_suspect）；
- groups 详情端点 subject 不存在 → 404；无出边的节点不进 subjects；
- 关系不存在/状态迁移非法等沿用 ReviewError 既有处理；
- 「按疾病分组」页签的审核失败按条 toast，不中断整卡操作。

## 测试

- pytest（新增 `tests/test_relation_review_upgrade.py`，复用 `tests/fixtures/kg/`）：
  - list_items：relation_code 过滤；node_type 对 relation 的端点匹配（源命中/目标命中/
    两端都不命中排除）、对 node 的原语义不变；
  - flags：无溯源/自环/方向可疑（用真实脏边形态）/重复对/端点未激活各一例；
    模板映射加载与损坏模板降级；
  - groups：subjects 过滤与排序计数；详情 sections 分组、容器嵌套一层展开、
    容器自身边不丢；subject 不存在 404；
  - queue 透传 relation_code 参数。
- JS 渲染手工验证（与既有审核台惯例一致）。
- 手动 e2e：真实图谱走操作剧本——只看可疑 → 类型批量 → 按病分组卡片审 →
  概览核对剩余。

## 影响面

- 修改：`src/review/service.py`（list_items 过滤与 flags）、`src/review/queue.py`（透传）、
  `src/api/routes/kg_review.py`（query 参数 + groups 端点）、
  `src/api/static/app.js`（页签/筛选/徽章/卡片/概览）、`src/api/static/style.css`
- 新增：`src/review/precheck.py`、`tests/test_relation_review_upgrade.py`
- 不变：审核动作链路、状态机、级联与规则阻断、导入流程、数据结构、其他页面
