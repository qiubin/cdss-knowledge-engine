# CDSS Knowledge Engine

可扩展的临床决策支持知识引擎。基于临床指南知识图谱 + 规则引擎，支持多医院标准配置，
并通过领域 Profile 同时支撑西医诊断与中医辨证两条知识生产线。

完整的中医扩展设计、数据结构、API 和种子数据说明见 [docs/tcm-domain.md](docs/tcm-domain.md)。

## 架构

```
患者数据 (LIS/EMR/HIS)
    |
    v
术语映射 (医院本地码 -> 标准码)
    |
    v
推理引擎 (规则条件树评估，按领域阶段执行)
    |--- 知识图谱 (西医 DiagnosticEvidence / 中医证候-症状-舌脉-方药)
    |--- 概念词汇表 (ELEVATED/DYNAMIC_RISE/PRESENT/...)
    |--- 标准配置 (按医院管理阈值；中医证候评分在规则内)
    |
    v
诊断/证候结论 + 证据链 + 指南溯源
```

### 领域扩展

| 领域 | 锚点 | 推理阶段 | 图谱语义 |
|---|---|---|---|
| `western` | 疾病 | 诊断 → 治疗 | 疾病 → 检验/检查/症状 → 诊断证据 |
| `tcm` | 证候 | 辨证 → 治法 → 方药 → 禁忌 | 病/证候 → 症状/舌象/脉象 → 治法 → 方剂 → 中药 |

共享层：PDF 解析、模板契约、实体字典、概念词汇、知识包、指南溯源、LLM 抽取。
分域层：图谱投影、候选规则生成、规则语义校验、推理分期、图谱展示。

领域展示契约由 `GET /api/profiles/{domain}` 下发，前端图谱类型、锚点、路径面板和
推理表单均按 profile 渲染，不再硬编码西医假设。

## 快速开始

```bash
pip install -r requirements.txt
python3 scripts/run_demo.py
```

首次使用大模型抽取前，复制 `data/extraction/config.example.json` 为
`data/extraction/config.json` 并填写 API Key，或在「解析模板」页配置（会写入同一文件）。
`config.json` 含凭证，已加入 `.gitignore`，请勿提交。

中医演示数据（词典、模板、图谱、规则、患者）：

```bash
python tools/_seed_tcm_vocab.py
python tools/_seed_tcm_graph.py
python tools/_seed_tcm_rules.py
python tools/_smoke_tcm_inference.py
```

## 运行测试

```bash
python3 -m pytest -q
node --test tests/js/graph_view_fallback.test.js
python3 -m unittest tests.test_review_status tests.test_rule_dependencies tests.test_graph_review_service tests.test_review_metadata tests.test_review_api tests.test_rule_review_gate tests.test_review_queue tests.test_package_review tests.test_review_orphans -v
```

测试覆盖西医静态阈值、ULN、动态趋势、端到端诊断，以及中医模板抽取、证候发现、
图谱导入、辨证/治法/方药分期推理、知识包预检和领域 Profile。

启动服务后，可执行前端全流程浏览器回归。`CDSS_TEST_PDF` 指向待上传的指南 PDF：

```bash
PYTHONPATH=. python3 -B -m uvicorn src.api.main:app --host 127.0.0.1 --port 8001

NODE_PATH=/path/to/node_modules \
  /path/to/node scripts/browser_test.js
```

## 核心设计

### 分层解耦

| 层 | 存什么 | 谁维护 | 扩展方式 |
|---|-------|-------|---------|
| 概念词汇表 | 有限枚举概念编码 | 系统管理员 | 修改 `data/vocab/concepts.json` |
| 知识图谱 | DiagnosticEvidence + Source 溯源 | 医学知识专员 + LLM 抽取 | 修改 `data/kg/*.json` |
| 标准配置 | 每医院 x 每检验 x 每概念 -> 数值 | 医院检验科 | 修改 `data/standards/*.json` |
| 规则 | 引用 evidence_id，不写数值 | 医学知识专员 + 审核 | 修改 `data/rules/*.json` |
| 评估器 | 判断概念是否匹配 | 开发者 | 继承 BaseEvaluator + 注册 |

### 扩展新病种

1. 在 `data/kg/` 中添加疾病节点 + DiagnosticEvidence + Source 溯源
2. 在 `data/rules/` 中添加引用这些 evidence_id 的规则
3. 在 `data/standards/` 中为每个医院配置涉及的检验阈值

不需要修改任何代码。

### 扩展新评估器

```python
from src.engine.evaluators.base import BaseEvaluator
from src.engine.evaluators.factory import EvaluatorFactory

class MyCustomEvaluator(BaseEvaluator):
    def evaluate(self, patient, standard, parameter=None):
        # 自定义逻辑
        ...

EvaluatorFactory.register("my_custom", MyCustomEvaluator)
```

然后在概念词汇表中将某个概念的 `evaluator_type` 设为 `"my_custom"`。

### 扩展新医院

1. 在 `data/standards/` 中新建 `{hospital_id}.json`，配置该医院的检验参考范围
2. 在术语映射表中配置该医院的本地码到标准码映射

## 项目结构

```
src/
  models/          # Pydantic 领域模型 (患者/规则/结论/标准)
  kg/              # 知识图谱客户端 (JSON版, 可替换为Neo4j)
  standards/       # 标准配置仓库 (按医院管理)
  rules/           # 规则仓库 (JSON文件)
  engine/
    evaluators/    # 可插拔概念评估器
    inference_engine.py  # 推理引擎主逻辑
  terminology/     # 术语映射器
data/
  vocab/           # 概念词汇表
  kg/              # 知识图谱种子数据 (STEMI)
  standards/       # 各医院标准配置
  rules/           # 临床规则
  patients/        # 演示患者数据
scripts/
  run_demo.py      # 端到端演示
tests/
  test_engine.py   # 单元测试 + 集成测试
```

## 指南导入与规则质量

指南导入走完整链路：PDF 分块 → 模板抽取 → 知识包预检 → 图谱/规则入库 → 规则启用 → 推理。

### 本地规则生成 v2

- 按疾病在章节中的位置切分局部窗口，不再把同一 chunk 内所有实体交叉组合。
- 从检验实体和邻近文本推断 `ELEVATED` / `ABOVE_ULN_1` / `DYNAMIC_RISE` 等概念，生成 `lab_concept` 条件。
- 丢弃 `HEMORRHAGE`、`NODULE` 等通用病理词，避免它们成为疾病证据。
- 从同章节的治疗方案、治疗措施、药物生成 `treatment` 规则。
- 只对词典内的 ICD-10 疾病生成规则；`AUTO-` 自动发现疾病保留在图谱供审核，但不生成规则。
- `GENERIC_GUIDELINE_V1` 已关闭疾病自动发现；新病种请先加入 `data/vocab/entities/disease.json`（ICD-10）。

### 规则质量与启用

- 知识包预检新增 `rule_quality`：检查是否使用检验概念、是否生成治疗规则、条件树是否高度重复、是否只依赖通用病理词。
- 规则默认入库为 `draft`。规则管理页提供「批量启用一致规则」，只启用图谱语义一致且通过质量检查的规则。
- 推理接口返回 `diagnostics`：启用/草稿的诊断与治疗规则数、图谱文件、无结论原因。
- 长篇指南建议使用「大模型解析」；本地模式会给出噪声提示。

### 模拟病历

「病历测试」支持切换 5 份内置病例：2 份 STEMI、3 份急性/暴发性心肌炎。病例清单位于
`data/patients/cases.json`，可用 `python tools/_seed_demo_cases.py` 重新生成。

```bash
# 用现有已上传文档重建知识包/图谱/规则（不重新上传 PDF）
python tools/_rebuild_myocarditis_asset.py
```

### 通用自动流水线（LLM）

「指南导入」页提供「一键自动生成」：

- 在「解析模板」页配置 **Provider / 服务地址 / API Key / 模型**；
- 智谱 Coding Plan 预设：
  - 国内：`https://open.bigmodel.cn/api/coding/paas/v4`
  - 国际：`https://api.z.ai/api/coding/paas/v4`
  - 模型示例：`glm-5.3-flash`（以套餐实际支持为准）
  - 必须使用 Coding Plan 地址，不要用普通 `/api/paas/v4`
- 支持自定义 Host 头（反向代理部署时使用）；
- 上传 PDF 后自动执行：LLM 开放术语发现 → ICD-10/参考库映射（无标准码时生成 `LOCAL-` 稳定码）→ 图谱导入 → 诊断/治疗/禁忌/监测规则 → 结构化治疗方案；
- 不要求预先补疾病/症状/检查词典；
- 规则统一以 `draft` 入库，审核通过后才启用；
- 每个指南使用独立的 `KG-AUTO-<doc_id>.json` 图谱，避免不同指南互相污染；
- 接口：`POST /api/auto/import`，配置：`GET/PUT /api/extraction/config`，连接测试：`POST /api/extraction/test-llm`。

## 图谱可视化

知识图谱页优先从 Neo4j 读取邻域子图；未启动 Neo4j 或认证失败时，自动回退到
`data/kg/*.json` 的本地子图，页面标记为「本地 JSON」，无需任何额外服务。
Neo4j 可用时，首次打开图谱会自动同步当前 `graph_filename` 的节点。

可选环境变量：`NEO4J_URI`（默认 `bolt://localhost:7687`）、`NEO4J_USER`、
`NEO4J_PASSWORD`；点击工具栏「刷新」会重新探测 Neo4j。

## 知识审核

指南导入后，图谱节点/关系和规则默认都是草稿，需要逐条审核：

- **知识包**：批量准入并导入为草稿，不自动启用。
- **知识图谱**：节点/关系可按 `draft / active / rejected / retired / stale` 审核，支持通过、拒绝、失效、恢复。
- **规则**：启用前要求所有图谱依赖为 `active`；依赖失效时规则自动变为 `blocked`，恢复依赖后不会自动重新启用。
- **审核人**：图谱页填写审核人姓名，所有动作写入 `data/review/review_log.jsonl`（运行时数据）。

### 审核台

- 导航「审核台」汇总待审图谱、待审规则、被阻断规则、待复核、已通过、孤儿条目和审核记录。
- 支持搜索、批量通过/拒绝/失效/恢复；批量操作逐条执行、逐条返回结果并写审计。

### 知识包预审

- 知识包页提供「包内预审」，可逐条通过/拒绝实体、关系和规则，支持按筛选批量通过。
- 导入模式支持「全部导入为草稿」和「仅导入已通过项」；未预审条目在后者中不入库。

### 孤儿条目

- 重新导入时，本次未再出现但此前由同一指南贡献的条目会标记 `orphaned_from`，
  在审核台「孤儿条目」页签中处理，不自动删除。

### 规则条件补全（无概念条件）

- 规则与图谱语义不一致的常见原因是「无概念条件」：条件组为空、症状条件缺
  `symptom_code`、证据条件缺 `evidence_id`。
- 规则管理页提供「补全条件 / 批量补全条件」：从锚点疾病在图谱中的诊断证据链
  （疾病 → 检验/检查/症状 → 诊断证据）自动生成概念条件（`lab_concept` /
  `exam_finding` / `symptom`），生成前逐条做图谱语义探查，只有能完整解析的
  证据才会成为条件；已一致的规则不会被改动。
- 补全后的条件写入草稿并标记 `condition_source: graph_completion`，审核人可在
  编辑器中继续调整；启用仍需通过依赖审核门禁。

首次启用审核工作流时执行迁移，为现有已启用规则 bootstrap 依赖：

```bash
PYTHONPATH=. python3 tools/migrate_review_status.py --dry-run
PYTHONPATH=. python3 tools/migrate_review_status.py
```

完整设计见 [docs/review-workflow-design.md](docs/review-workflow-design.md)。

## 指南溯源

每条知识通过 `Source` 节点关联到指南 PDF 的具体位置：

```
溯源: 急性ST段抬高型心肌梗死诊断和治疗指南 p2 l15-40
原文: "典型的STEMI早期心电图表现为ST段弓背向上抬高..."
```

`bbox` 坐标可用于前端高亮定位 PDF 原文。

