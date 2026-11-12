# 实体治理：同名自动合并、存量合并工具、关联条目展示

日期：2026-11-12
状态：已批准（对话确认三项全部执行）

## 背景与问题

真实图谱（KG-GENERIC_GUIDELINE_V1.json，421 节点）存在 20+ 组同类型同名重复节点
（感染性休克 ×2、地高辛 ×5、利尿剂 ×5…）。根因：入库按「类型+编码」判重
（`_upsert_semantic_node` 以 `ENTITY:{type}:{code}` 为 key），而大模型为同名实体
铸出不同候选码（如"室间隔缺损"分别得到 `Q21.0` 与 `CAND-VSD`），同名不同码即成
两个节点；导入链路没有任何按名称合并的逻辑。

同时，审核人在审核台看不到节点的关联邻居（后端 `node_detail` 已返回该节点全部
关系，前端未渲染），无法借上下文判断条目合理性。

## 目标

1. **增量止血**：导入时同图谱同类型同名实体自动合并为一个节点。
2. **存量清理**：审核台提供"疑似重复"页签，检测同类型同名分组，一键合并
   （关系重挂、规则条件改写、自动备份、审计留痕）。
3. **审核上下文**：节点详情面板展示「关联条目」（作为源/作为目标的全部关系与
   邻居），可点击跳转审核。

## 非目标

- 疾病分类学层级（ICD 父子树）：词库为扁平 23 条，图谱无层级关系类型，留待
  引入层级词库的独立工作。
- 跨图谱合并、别名合并（"霍奇金淋巴瘤" vs "霍奇金病"）：仅做精确同名（规范化
  空白后）合并，别名归一留给术语映射层。
- 合并的自动执行：存量合并必须人工确认，不做后台自动合并。

## A. 导入时同名自动合并

修改 `src/api/routes/kg.py::_upsert_semantic_node`：

- 新建节点前，先在 `graph["nodes"]` 中查找 `node_type` 相同且 `name` 规范化相等
  （去首尾空白、连续空白折叠为单空格）的既有节点；命中则**复用该节点**：
  - 新 code 与既有 code 不同 → 新 code 追加进 `node["attributes"]["alt_codes"]`
    （去重、保持插入序）；
  - `source_refs` 并集去重；
  - 不改既有节点的 `code` / `id` / `review_status`（first-wins，改 id 需重挂关系，
    代价留给存量合并工具按需处理）。
- 未命中（且 code 不同）才走原新建逻辑；code 相同仍按原 key 命中，行为不变。
- 关系创建复用 `_upsert_semantic_node` 返回的 id，自动挂到保留节点，无需额外改动。
- 导入 warnings 中追加一行合并统计（如"同名合并 N 组"），让审核人可见。

## B. 存量合并工具（审核台「疑似重复」）

### 检测

`GET /api/kg/duplicates?graph_filename=X`：

- 遍历节点，按 `(node_type, normalized name)` 分组，≥2 的组返回：
  `{node_type, name, nodes: [{node_id, code, is_standard_code, review_status, source_ref_count, relation_count}]}`。
- `is_standard_code`：code 匹配 `^[A-Z]\d{2}(\.\d+)?$`（ICD 风格）或来自
  standard_system 词库；`CAND-*` 等自铸码为 false。
- 每组附推荐保留节点（`recommended_survivor`）：优先标准码 > review_status=active
  > 关系数多 > code 字典序。

### 合并

`POST /api/kg/merge-nodes`，body `{graph_filename, survivor_node_id, absorbed_node_ids}`：

1. **备份**：`{graph}.json.bak-YYYYMMDD-merge`（shutil.copy2，同清库备份机制）。
2. **图合并**：
   - absorbed 节点的关系全部重挂到 survivor：三元组相同（源/码/目标）的关系统并
     （source_refs 并集、review_status 取"更高"：active > draft > 其余），否则仅改
     端点 id；关系总数不得减少（重挂只换 id，不删除语义不同的关系）；
   - survivor `source_refs` 并集；`attributes.alt_codes` 吸收各 absorbed 的 code
     与既有 alt_codes；
   - 删除 absorbed 节点。
3. **规则改写**：规则条件引用的是**码**（`disease_code` 与条件树里的
   `symptom_code`/`lab_code`/`exam_code`/`medication_code` 等 `*_code` 叶子字段）。
   遍历 `managed_rules.json` 中该图谱的规则，把 absorbed code 全量替换为
   survivor code（递归条件树）；每条被改写的规则 `record_snapshot(rule, "update")`
   留版本快照；规则文件先做 `.bak` 备份。
4. **审计**：每组合并写一条 review_log（entity_id=survivor，action="merge"，
   note 记录 absorbed id/code 清单），依赖既有 audit 管道。
5. 幂等与校验：survivor 不得在 absorbed 中（422）；节点不存在 404；graph_filename
   归一化校验复用 `normalize_graph_filename`。

### 前端

审核台 tabs 新增「疑似重复」（显示分组数徽标）：

- 每组一张卡：类型 + 名称 + 候选节点列表（码、标准/候选徽标、状态、关系数、
  引用数），默认勾选推荐保留节点，其余为待吸收；
- 「合并」按钮 → confirm（列出将被吸收的码与受影响规则数）→ 调用 merge →
  toast + 刷新；
- 空态："未检测到同类型同名重复"。

## C. 节点详情「关联条目」

`nodeDetailHtml` 渲染 `detail.relations`（接口已返回）：

- 分两组：作为源 / 作为目标；每行：关系名（中文标签）+ 邻居（名称 + 类型徽标 +
  mini 状态）；
- 点击邻居节点 → 构造 `{item_type:"graph_node", node_id, graph_filename}` 直接调
  `loadReviewDetail`（不要求邻居在当前列表中）；点击关系行 → 打开关系详情；
- 超过 20 条折叠为"显示全部 N 条"。

## 错误处理

- A：合并逻辑不得抛出（导入路径已有多重兜底，名称匹配失败即走原逻辑）。
- B：merge 任一步失败 → 返回错误、不落盘半成品（先备份、内存中完成全部变换、
  一次写回）；检测接口对无重复图谱返回空数组。
- C：relations 为空显示"暂无关联条目"。

## 测试

- pytest（A）：同名不同码 → 单节点 + alt_codes + source_refs 并集；关系挂到保留
  节点；同名规范化（首尾空白/连续空白）；同名但码相同 → 原行为不变；不同名同码
  → 原行为不变。
- pytest（B）：检测分组正确（含 is_standard_code 与推荐 survivor）；合并后 absorbed
  消失、关系统并/重挂、规则条件码被改写且快照记录、备份文件存在、审计留痕；
  survivor ∈ absorbed → 422；不存在 → 404。
- JS：疑似重复卡片渲染与推荐勾选为 DOM 逻辑，手工验证；C 的分组渲染手工验证。
- 手动 e2e：真实图谱跑检测 → 合并一组真实重复（如"室间隔缺损"）→ 验证规则
  引用与健康页推理不受影响。

## 影响面

- 修改：`src/api/routes/kg.py`（A：upsert；B：两个端点）、
  `src/api/static/app.js`（疑似重复页签 + 关联条目区）、`src/api/static/style.css`
- 规则改写复用 `src/api/routes/rules.py` 的快照与加载工具
- 新增测试：`tests/test_same_name_merge.py`（或并入既有 import 测试文件）、
  `tests/test_merge_duplicates.py`
- 不变：导入 API 契约、审核动作链路、推理引擎
