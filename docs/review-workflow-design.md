# 知识图谱与规则审核工作流设计

- 日期：2026-11-09
- 状态：P0 / P1 已实施（2026-11-09）；P2 未实施
- 范围：知识包 → 知识图谱 → 规则库的分层审核、失效传播与审计
- 目标读者：产品、医学知识专员、开发

## 1. 背景与目标

当前指南导入链路已经具备「PDF → 模板/LLM 抽取 → 知识包预检 → 导入图谱和草稿规则」的能力，但审核只停留在知识包级：

- 知识包状态 `reviewed` 只是包级标记，不改变包内实体、关系或规则的状态。
- 图谱节点和关系创建时统一写入 `review_status: "draft"`，但没有任何接口或界面可以修改。
- 规则已有 `draft / active / retired` 生命周期，但启用检查只看图谱里「有没有」对应节点和关系，不看这些条目是否已审核。
- 节点失效不会影响依赖它的规则；重新导入还可能静默修改已审核内容。

本设计要解决的问题：

1. 图谱节点、关系可以逐条审核、拒绝、失效、恢复。
2. 规则与图谱建立明确的依赖关系；规则启用必须通过依赖门禁。
3. 图谱条目失效时，依赖它的已启用规则自动阻断。
4. 重新导入导致已审核内容变化时，进入待复核，而不是静默覆盖。
5. 所有审核动作可审计，记录审核人、时间、原因和影响范围。

## 2. 范围与非目标

### 2.1 范围内

- 知识包批量准入与批量导入为草稿。
- 图谱节点、关系的逐条审核与批量操作。
- 规则逐条审核、依赖检查、启用门禁、阻断与恢复。
- 节点失效的影响面预览与规则级联阻断。
- 重新导入的内容变化检测（`stale`）。
- 审核审计日志。
- 统一审核台页面（P1）。

### 2.2 非目标

- P0 不引入登录、RBAC、双人复核；审核人使用界面填写的姓名并写入审计。
- 不自动判断医学内容是否正确；系统只提供证据、依赖和一致性检查，最终由审核人决定。
- 不提供物理删除；图谱条目使用 `retired` 保留历史，规则保留现有删除能力但仅限管理员操作。
- P0 不处理规则与规则之间的依赖（如中医 `fact` 条件），只处理规则对图谱条目的依赖。
- 不内嵌 PDF 批注；审核页展示页码、行号和引文，PDF 原文定位留给后续增强。

## 3. 现状与差距

| 层 | 现有实现 | 差距 |
|---|---|---|
| 知识包 | `generated → reviewed → imported → ready_for_test → published`；有结构、概念、标准、质量预检 | `reviewed` 不落到条目；无法逐条标记；导入只有整包一种方式 |
| 图谱 | 节点/关系已有 `review_status`；导入默认 `draft` | 全部条目固定为 `draft`；无审核接口；无状态筛选；无失效能力 |
| 规则 | `draft / testing / active / retired`；有启用、停用、删除 | 启用只检查图谱存在性；不检查审核状态；无 `blocked` 状态 |
| 联动 | `sync-check` 检查图谱语义链 | 不检查状态；节点失效不阻断规则；无影响面分析 |
| 审计 | 无 | 无审核人、时间、原因、前后状态 |

关键代码位置：

- 节点创建固定 `draft`：`src/api/routes/kg.py` 的 `_upsert_semantic_node`
- 关系创建固定 `draft`：`src/api/routes/kg.py` 的 `_upsert_semantic_relation`
- 图谱页只展示状态：`src/api/static/app.js` 的语义节点/关系表格
- 规则启用门禁：`src/api/routes/rules.py` 的 `activate_rule`、`activate_consistent`
- 语义一致性检查：`src/api/routes/rules.py` 的 `GraphSemanticChecker`、`check_rule_sync`

## 4. 状态模型

### 4.1 图谱节点 / 关系

| 状态 | 含义 | 可作为规则依赖 | 可用于推理 |
|---|---|---|---|
| `draft` | 新导入或恢复后待审核 | 否 | 否 |
| `active` | 审核通过 | 是 | 是 |
| `rejected` | 审核不通过，内容错误 | 否 | 否 |
| `retired` | 曾经生效，现已失效/停用 | 否 | 否 |
| `stale` | 已通过，但重新导入后内容变化，待复核 | 否 | 否 |

状态迁移：

```
draft --通过--> active
draft --拒绝--> rejected
active --失效--> retired
active --重导入内容变化--> stale
stale --复核通过--> active
stale --拒绝--> rejected
stale --失效--> retired
rejected --恢复--> draft
retired --恢复--> draft
```

约束：

- 关系只有在源节点和目标节点都已是 `active` 时才能被批准为 `active`。
- 任一端点变为 `rejected`、`retired` 或 `stale` 时，该关系自动变为 `stale`。
- 审核动作必须带 `reviewer`，可选 `note`。

### 4.2 规则

在现有状态基础上增加两个：

| 状态 | 含义 |
|---|---|
| `blocked` | 依赖的图谱节点/关系不再可用，被系统自动阻断 |
| `rejected` | 审核不通过 |

规则状态迁移：

```
draft --审核通过且门禁通过--> active
draft --拒绝--> rejected
testing --审核通过且门禁通过--> active
active --人工停用--> retired
active --依赖失效--> blocked
blocked --依赖恢复且重新审核--> active
rejected --恢复--> draft
retired --恢复--> draft
```

推理引擎只执行 `status == "active"` 的规则；`blocked`、`rejected`、`retired`、`draft` 均不参与推理。现有行为不变。

### 4.3 知识包

保留现有状态，但明确语义：

| 状态 | 含义 |
|---|---|
| `generated` | 已生成，等待批量准入 |
| `reviewed` | 包级准入通过，允许导入；**不代表图谱条目已审核** |
| `imported` | 已批量导入为草稿 |
| `ready_for_test` | 图谱和规则审核完成，可测试 |
| `published` | 可生产使用 |
| `failed` | 导入失败 |

包级批量通过只是「允许入库」的门槛，不改变条目的最终审核状态。

## 5. 总体流程

```
指南 PDF
  ↓
解析 / 模板 / LLM 抽取
  ↓
知识包生成 + 自动预检
  ↓
【第一段：包级批量准入】
  核对目标锚点、候选实体/关系/规则、预检问题
  标记 reviewed
  批量导入为草稿
      ├─ 图谱节点/关系 → draft
      └─ 规则 → draft
  ↓
【第二段：条目级终审】
  ├─ 图谱页：节点逐条 通过 / 拒绝 / 失效 / 恢复
  ├─ 图谱页：关系逐条 通过 / 拒绝 / 失效 / 恢复
  ├─ 规则页：查看依赖 → 通过启用 / 拒绝 / 停用
  └─ 审核台：跨条目待办、阻断项、待复核、影响面
  ↓
【规则启用门禁】
  ├─ 规则结构合法、概念词汇存在
  ├─ 图谱语义链一致
  ├─ 所有依赖节点/关系为 active
  └─ 质量检查通过（可选）
  ↓
推理引擎只加载 active 规则 + active 图谱语义
  ↓
后续重导入 / 人工修改
  ├─ 内容未变：保留审核状态
  ├─ 内容变化：已审核条目 → stale
  └─ 节点失效：依赖规则 → blocked
```

## 6. 分层审核职责

| 层级 | 审什么 | 粒度 | 主要操作 |
|---|---|---|---|
| 知识包 | 目标锚点、预检问题、抽取质量、候选范围 | 包级 | 批量通过、批量导入为草稿 |
| 知识图谱 | 节点语义、关系方向、溯源证据、属性 | 条目级 | 通过、拒绝、失效、恢复、批量操作 |
| 规则库 | 条件逻辑、依赖完整性、质量标准 | 条目级 | 通过启用、拒绝、停用、阻断后重审 |
| 审核台 | 跨图谱/规则的待办与阻断 | 混合 | 筛选、批量操作、影响面预览 |

知识包不承担最终审核职责，原因：

- 知识包是导入时的快照，导入后图谱会被多个指南合并、人工编辑。
- 重新导入时只有图谱层能判断「内容是否变化」。
- 规则最终依赖的是图谱语义，不是知识包文件。

## 7. 图谱审核设计

### 7.1 审核对象

- 语义节点：`nodes[]`，包括 disease、lab、exam、symptom、diagnostic_evidence、treatment_plan、medication、intervention、contraindication、monitoring_requirement，以及中医的 syndrome、tongue、pulse、formula、herb 等。
- 语义关系：`relations[]`，以 `(source_node_id, relation_code, target_node_id)` 唯一标识。

### 7.2 节点审核操作

| 操作 | 允许的当前状态 | 结果 | 副作用 |
|---|---|---|---|
| 通过 | `draft`、`stale` | `active` | 无 |
| 拒绝 | `draft`、`stale` | `rejected` | 关联关系 → `stale`；依赖规则 → `blocked` |
| 失效 | `active` | `retired` | 关联关系 → `stale`；依赖规则 → `blocked` |
| 恢复 | `rejected`、`retired` | `draft` | 关联关系保持 `stale`；依赖规则保持 `blocked` |

### 7.3 关系审核操作

| 操作 | 允许的当前状态 | 前置条件 | 结果 |
|---|---|---|---|
| 通过 | `draft`、`stale` | 两端节点均为 `active` | `active` |
| 拒绝 | `draft`、`stale` | 无 | `rejected` |
| 失效 | `active` | 无 | `retired` |
| 恢复 | `rejected`、`retired` | 无 | `draft` |

关系失效不会额外级联节点；但依赖该关系的规则会进入 `blocked`。

### 7.4 批量操作

- 支持勾选多条节点或关系，执行「批量通过 / 批量拒绝 / 批量失效」。
- 批量通过关系时，逐条检查两端节点状态；不满足的条目进入失败列表，其余成功。
- 批量失效前必须展示影响面：
  - 将失效的节点/关系数量
  - 受影响的关系数量
  - 受影响规则数量（按 active / draft / blocked 分组）
- 批量操作返回逐条结果，允许部分成功；每条都写审计。

### 7.5 节点详情

审核侧栏展示：

- 基本信息：类型、编码、名称、属性、所属图谱
- 指南溯源：所有 `source_refs` 对应的文档、章节、页码、行号、引文
- 关联关系：入边和出边，及对方节点状态
- 依赖规则：规则 ID、名称、状态、所属图谱
- 审核历史：时间、审核人、动作、原因、前后状态

### 7.6 图谱可视化

- 节点按状态区分：`draft` 虚线边框、`active` 实线、`rejected/retired` 灰色、`stale` 橙色。
- 增加开关：「只看待审核」「隐藏已失效」。
- 点击节点打开审核侧栏，可直接通过、拒绝、失效。

## 8. 规则审核与联动

### 8.1 依赖提取

扩展现有 `GraphSemanticChecker`，除了返回 `issues`，还返回 `references`：

```
references: [
  { "kind": "node", "node_id": "ENTITY:symptom:SYM_CHEST_PAIN" },
  { "kind": "relation", "source_node_id": "...", "relation_code": "...", "target_node_id": "..." }
]
```

依赖映射：

| 规则条件/结论 | 图谱依赖 |
|---|---|
| 疾病锚点 | disease 节点 |
| `lab_concept` | lab 节点、diagnostic_evidence 节点、`DISEASE_HAS_LAB`、`LAB_HAS_DIAGNOSTIC_EVIDENCE`、`DISEASE_HAS_DIAGNOSTIC_EVIDENCE` |
| `exam_finding` | exam 节点、diagnostic_evidence 节点、`DISEASE_HAS_EXAM`、`EXAM_HAS_DIAGNOSTIC_EVIDENCE`、`DISEASE_HAS_DIAGNOSTIC_EVIDENCE` |
| `symptom` | symptom 节点、`DISEASE_HAS_SYMPTOM` |
| `observation`（中医） | 对应 observation 节点、证候相关关系 |
| `fact`（证候/治法） | 前一阶段证候/治法节点；P0 只校验节点状态，不展开规则-规则依赖 |
| 治疗结论 | treatment_plan、medication/intervention 节点及关系 |
| 中医方药结论 | formula、herb 节点及关系 |

### 8.2 规则启用门禁

`activate_rule` 和 `activate_consistent` 按以下顺序校验：

1. 规则结构合法，概念词汇存在。
2. 图谱语义链一致（现有 `check_rule_sync`）。
3. **所有依赖节点/关系均为 `active`**。
4. 质量检查通过（`activate_consistent` 的 `quality_only` 选项）。
5. 写入审核人、时间、原因，状态改为 `active`。

任一项失败返回 422，响应中包含逐项问题：

```json
{
  "error": "规则依赖未通过审核",
  "dependencies": [
    {"node_id": "ENTITY:symptom:SYM_CHEST_PAIN", "status": "draft"},
    {"relation": "DISEASE_HAS_SYMPTOM", "status": "stale"}
  ]
}
```

### 8.3 失效传播

节点变为 `rejected` / `retired` / `stale`，或关系变为 `rejected` / `retired` / `stale` 时：

1. 计算依赖它的规则集合。
2. 对 `active` 规则：状态改为 `blocked`，写入 `blocked_by`：

```json
{
  "status": "blocked",
  "blocked_by": [
    {
      "entity_type": "node",
      "entity_id": "ENTITY:symptom:SYM_CHEST_PAIN",
      "reason": "retired",
      "since": "2026-11-09T10:00:00Z"
    }
  ]
}
```

3. 对 `draft` / `testing` 规则：状态不变，但依赖检查会显示问题。
4. 对 `rejected` / `retired` 规则：不变。
5. 写审计日志，记录受影响规则列表。

恢复节点后：

- 节点变为 `draft`。
- 关联关系保持 `stale`，需逐条复核。
- `blocked` 规则不会自动恢复，必须重新审核启用。

### 8.4 规则审核操作

| 操作 | 允许状态 | 结果 |
|---|---|---|
| 通过并启用 | `draft`、`testing`、`blocked` | 门禁通过后 `active`；失败保持原状态并返回问题 |
| 拒绝 | `draft`、`testing` | `rejected` |
| 停用 | `active`、`blocked` | `retired` |
| 恢复 | `rejected`、`retired` | `draft` |

### 8.5 规则页展示

- 新增「图谱依赖」列：`3/3 已审核`、`1 项待审`、`2 项失效`。
- 点击展开依赖清单，每项可跳转到图谱定位。
- `blocked` 规则显示阻断原因和「前往处理」入口。
- 批量启用时同时执行依赖门禁，跳过不满足的规则并返回原因。

## 9. 重新导入与版本

### 9.1 内容指纹

为节点和关系计算 `content_hash`，覆盖审核关心的字段：

- 节点：`node_type`、`code`、`name`、`attributes`、`source_refs`
- 关系：`source_node_id`、`relation_code`、`target_node_id`、`attributes`、`source_refs`

### 9.2 重新导入规则

| 现有状态 | 内容是否变化 | 结果 |
|---|---|---|
| `draft` | 任意 | 保持 `draft`，更新内容 |
| `active` | 未变化 | 保持 `active` |
| `active` | 变化 | `stale` |
| `stale` | 任意 | 保持 `stale`，更新内容 |
| `rejected` | 任意 | 保持 `rejected`，更新内容 |
| `retired` | 任意 | 保持 `retired`，更新内容 |

`stale` 条目不能作为规则依赖；如已有规则依赖它，规则进入 `blocked`。

### 9.3 版本与来源

新增字段：

```json
{
  "content_hash": "sha256:...",
  "version": 2,
  "previous_values": {"name": "旧名称", "review_status": "active"},
  "last_imported_at": "2026-11-09T10:00:00Z",
  "source_doc_ids": ["DOC-d51b97dd"]
}
```

本次导入未再出现的旧条目，P1 阶段按来源文档标记为「待处理」，不自动删除；P0 只记录 `last_imported_at`，不做孤儿判定。

## 10. 数据模型变更

### 10.1 图谱节点

```json
{
  "id": "ENTITY:symptom:SYM_CHEST_PAIN",
  "node_type": "symptom",
  "code": "SYM_CHEST_PAIN",
  "name": "胸痛",
  "display_name": "胸痛（SYM_CHEST_PAIN）",
  "attributes": {},
  "source_refs": ["DOC-xxx_p1_l1-10"],
  "review_status": "draft",
  "review_note": "",
  "reviewed_by": "",
  "reviewed_at": "",
  "content_hash": "sha256:...",
  "version": 1,
  "last_imported_at": "2026-11-09T10:00:00Z",
  "source_doc_ids": ["DOC-xxx"]
}
```

### 10.2 图谱关系

```json
{
  "source_node_id": "ENTITY:disease:I21.0",
  "relation_code": "DISEASE_HAS_SYMPTOM",
  "target_node_id": "ENTITY:symptom:SYM_CHEST_PAIN",
  "relation_name": "疾病有症状",
  "attributes": {},
  "source_refs": ["DOC-xxx_p1_l1-10"],
  "review_status": "draft",
  "review_note": "",
  "reviewed_by": "",
  "reviewed_at": "",
  "content_hash": "sha256:...",
  "version": 1,
  "last_imported_at": "2026-11-09T10:00:00Z",
  "source_doc_ids": ["DOC-xxx"]
}
```

### 10.3 规则

```json
{
  "status": "blocked",
  "review_note": "",
  "reviewed_by": "",
  "reviewed_at": "",
  "blocked_by": [
    {"entity_type": "node", "entity_id": "ENTITY:symptom:SYM_CHEST_PAIN", "reason": "retired", "since": "2026-11-09T10:00:00Z"}
  ]
}
```

### 10.4 审计日志

追加写入 `data/review/review_log.jsonl`（运行时数据，加入 `.gitignore`）：

```json
{
  "ts": "2026-11-09T10:00:00Z",
  "action": "retire",
  "entity_type": "node",
  "entity_id": "ENTITY:symptom:SYM_CHEST_PAIN",
  "graph_filename": "KG-GENERIC_GUIDELINE_V1.json",
  "before_status": "active",
  "after_status": "retired",
  "reviewer": "张医生",
  "note": "指南更新后该症状不再作为诊断依据",
  "affected_rules": ["DOC-xxx-AUTO-001"]
}
```

审计写入与状态保存的一致性：先校验并准备审计记录，状态保存成功后追加审计；若审计追加失败，回滚状态并返回 500。审核动作不允许无审计落库。

## 11. API 设计

### 11.1 图谱审核

```
GET  /api/kg/review/summary?graph_filename=&domain=
  → 节点/关系各状态数量、待审核规则数、被阻断规则数

GET  /api/kg/review/items?graph_filename=&status=&item_type=&node_type=&source_doc=&referenced_by_rule=&page=&page_size=
  → 待审核条目列表，含 dependent_rule_count、source_refs、updated_at

GET  /api/kg/nodes/{node_id}
  → 节点详情、关系、依赖规则、审核历史

POST /api/kg/nodes/{node_id}/review
  { "action": "approve|reject|retire|restore", "note": "", "reviewer": "张医生" }

POST /api/kg/relations/review
  { "source_node_id": "...", "relation_code": "...", "target_node_id": "...",
    "action": "approve|reject|retire|restore", "note": "", "reviewer": "张医生" }

POST /api/kg/review/bulk
  { "items": [{"item_type": "node", "node_id": "..."}, {"item_type": "relation", "source_node_id": "...", "relation_code": "...", "target_node_id": "..."}],
    "action": "approve|reject|retire|restore", "note": "", "reviewer": "张医生" }

GET  /api/kg/nodes/{node_id}/dependents
  → 引用该节点的规则列表

GET  /api/kg/nodes/{node_id}/impact
  → 失效影响面预览：关系数、规则数、按状态分组

GET  /api/kg/relations/impact?source_node_id=&relation_code=&target_node_id=
```

### 11.2 规则审核

```
GET  /api/rules/{rule_id}/graph-dependencies
  → 依赖清单、每项状态、门禁结果

POST /api/rules/{rule_id}/review
  { "action": "approve|reject|retire|restore", "note": "", "reviewer": "张医生" }

GET  /api/rules/sync-check
  → 扩展返回 dependency_status、review_issues
```

`GET /api/rules` 列表响应增加 `dependency_summary`，避免前端逐条请求：

```json
{
  "dependency_summary": {
    "total": 5,
    "active": 3,
    "draft": 1,
    "stale": 1,
    "blocked": true
  }
}
```

### 11.3 审核台

```
GET  /api/review/queue?domain=&graph_filename=&item_type=&status=&q=
GET  /api/review/audit?entity_id=&graph_filename=&limit=
```

### 11.4 知识包

```
POST /api/guide-packages/{package_id}/import
  { "mode": "draft" }
  → 默认全部导入为草稿；返回节点/关系/规则的新增、更新、跳过数量

P1:
GET  /api/guide-packages/{package_id}/items
POST /api/guide-packages/{package_id}/items/review
POST /api/guide-packages/{package_id}/import { "mode": "approved_only" }
```

P0 只实现 `mode: "draft"`；`approved_only` 留给 P1。

## 12. 前端设计

### 12.1 图谱页

- 语义节点、语义关系表格增加状态筛选和行内操作。
- 诊断证据表格只读展示其投影节点的审核状态，不单独审核；审核入口统一走语义节点。
- 增加多选列和批量操作栏。
- 节点点击打开审核侧栏；侧栏包含溯源、关联关系、依赖规则、审核历史。
- 图谱可视化增加状态样式和筛选开关。
- 状态徽章统一颜色：
  - `draft` 灰色
  - `active` 绿色
  - `rejected` 红色
  - `retired` 深灰
  - `stale` 橙色

### 12.2 规则页

- 增加「图谱依赖」列和依赖展开面板。
- `blocked` 规则置顶或提供筛选。
- 启用按钮失败时展示依赖问题清单。
- 批量启用结果展示每条规则的跳过原因。

### 12.3 审核台（P1）

- 导航新增「审核台」。
- 页签：待审图谱、待审规则、被阻断、待复核、审核记录。
- 筛选：领域、图谱、来源指南、条目类型、状态、是否被规则引用。
- 支持批量操作和影响面预览。

### 12.4 知识包页

- 保留现有预检和整包导入。
- 导入结果增加条目状态分布，并提供「前往审核台」入口。
- 明确提示：「导入为草稿，不会自动启用」。
- P1 增加包内预审和「仅导入已通过项」。

## 13. 迁移与兼容

现有数据迁移策略：

1. 所有现有节点/关系保持 `review_status: "draft"`，进入审核台。
2. 为当前 `active` 规则计算图谱依赖，并将被这些规则引用的节点/关系一次性置为 `active`，审计记录标记 `action: "migration_bootstrap"`、`reviewer: "system"`。
3. 其余条目保持 `draft`，等待人工审核。
4. 规则状态保持不变；迁移后 `active` 规则仍可推理。
5. 新增字段均为可选，旧数据缺少字段时按默认值处理：
   - `review_status` 缺失 → `draft`
   - `blocked_by` 缺失 → `[]`
   - `content_hash` 缺失 → 首次写入时计算

迁移脚本必须具备幂等性：重复执行不会重复写入审计记录，也不会把人工审核过的条目改回 `active`。

## 14. 错误处理与边界

- 非法状态迁移返回 422，包含当前状态和允许的动作。
- 关系通过时端点未审核返回 422，列出未满足的端点。
- 规则启用门禁失败返回 422，返回逐项依赖状态。
- 批量操作允许部分成功，返回每条结果；成功的条目正常审计。
- 并发修改：P0 使用 `updated_at` 最后写入者生效；P1 增加 `expected_version` 乐观锁。
- 审计写入失败时回滚状态并返回 500。
- 图谱文件损坏或缺失时，审核接口返回 503，不修改任何状态。

## 15. 测试策略

### 15.1 后端单元测试

- 节点/关系状态迁移的允许与拒绝。
- 关系通过要求两端节点 active。
- 依赖提取覆盖 lab、exam、symptom、observation、treatment、TCM 方药。
- 规则启用门禁：依赖 draft/stale/retired 时拒绝。
- 节点失效级联：关系变 stale、active 规则变 blocked。
- 恢复流程：节点回 draft，规则保持 blocked。
- 重新导入：内容不变保留状态，内容变化 active → stale。
- 审计记录字段完整，失败回滚。

### 15.2 API 测试

- 单条审核、批量审核、部分成功。
- 影响面预览数量与级联结果一致。
- 规则列表包含 dependency_summary。
- 迁移脚本幂等。

### 15.3 前端浏览器测试

- 图谱页筛选 draft、逐条通过、批量失效。
- 节点详情显示依赖规则。
- 规则页显示 blocked 原因，门禁失败提示。
- 审核台筛选和批量操作。
- 图谱可视化状态样式。

## 16. 分阶段实施

### P0：最小可用审核闭环

- 图谱节点/关系审核 API 和审计。
- 图谱页状态筛选、逐条操作、节点详情。
- 依赖提取、规则启用门禁、`blocked` 状态。
- 失效影响面预览和级联阻断。
- 数据迁移脚本。
- 后端单元测试和浏览器回归。

验收标准：

- 任意图谱条目可以从 draft 改为 active/rejected/retired。
- 规则依赖未审核时无法启用。
- 节点失效后依赖的 active 规则立即变为 blocked。
- 所有审核动作可在审计日志中查询。

### P1：效率与变更安全

- 统一审核台。
- 批量操作和影响面预览。
- 关系级联和批量失效。
- 重新导入 `stale` 检测与版本字段。
- 知识包预审、`approved_only` 导入模式。
- 孤儿条目提示。

### P2：生产治理

- 登录与 RBAC。
- 双人复核。
- 审核统计、抽样审核、导出。
- 规则-规则依赖（中医 `fact` 链）。
- 审核 SLA 和超时提醒。

## 17. 已确认决策

| 决策 | 选择 |
|---|---|
| 审核主流程 | 两段式：知识包批量准入 + 导入草稿 + 图谱/规则逐条终审 + 审核台 |
| 节点/关系失效 | 依赖的已启用规则自动 `blocked`；恢复后需人工重新启用 |
| 重导入内容变化 | 已审核条目标记 `stale`，不能作为规则依赖 |
| 审核人身份 | P0 使用姓名 + 审计日志；P1/P2 再接登录与 RBAC |

## 18. 关键假设

- 审核是临床治理动作，宁可阻断也不允许未审核条目参与推理。
- 知识包只是导入快照，图谱和规则是最终事实来源。
- 当前系统为内网单团队使用，P0 不引入登录不会造成权限风险。
- 图谱条目按 `graph_filename` 隔离；同一节点编码在不同图谱中分别审核。
- 现有 `active` 规则在迁移时通过一次性 bootstrap 保持可用，避免上线即停摆。
