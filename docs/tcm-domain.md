# 中医知识图谱扩展（TCM Domain）

本文件说明在西医 CDSS 知识引擎之上接入中医领域的设计与使用方式。核心原则：
**共享流程，分域语义**。指南导入、知识包、实体字典、概念词汇、溯源、LLM 抽取
保持共用；图谱语义投影、规则生成、推理分期、图谱展示由领域 Profile 驱动。

## 1. 领域 Profile

代码位置：`src/domains/`

| 文件 | 作用 |
|---|---|
| `base.py` | `DomainProfile` / `GraphProfile` / `InferenceStage` / `RuleGenerator` 契约 |
| `registry.py` | `western` / `tcm` 注册与别名归一化（通用、心血管内科、血液内科 → western） |
| `western/profile.py` | 西医：疾病锚点、诊断→治疗两阶段、节点元数据 |
| `tcm/profile.py` | 中医：证候锚点、辨证→治法→方药→禁忌四阶段、节点元数据 |
| `tcm/graph.py` | 中医图谱语义层：证候锚点与悬空关系清理 |
| `tcm/rule_generation.py` | 四类中医候选规则生成器 |
| `tcm/checks.py` | 中医规则-图谱一致性校验 |

前端通过 `GET /api/profiles` 与 `GET /api/profiles/{domain}` 获取节点颜色、锚点类型、
推理阶段、路径面板和表单字段，不再硬编码领域差异。

## 2. 模板契约

`TCM_GUIDELINE_V1`（`data/templates/extraction_templates.json`）定义：

- 实体类型：中医疾病、证候、症状、舌象、脉象、体征、病机、治法、方剂、中药、禁忌
- 关系：疾病辨证分型、证候主症/舌/脉、证候治法、治法方药、方剂组成、证候禁忌
- 属性：症状角色（主症/次症）、君臣佐使、剂量、病位、病性、禁忌级别
- `capabilities`: `tcm_pattern` / `tcm_treatment` / `tcm_prescription` / `tcm_contraindication`
- `rule_generation`: 每类规则的锚点、条件实体、最小条件数、评分阈值

实体编码采用「本地稳定码 + `standard_code` 映射」。种子数据在
`data/vocab/entities/{syndrome,symptom,tongue,pulse,formula,herb,...}.json`。

## 3. 图谱结构

中医图谱以 `nodes` / `relations` 为唯一语义层，不使用西医的
`evidences/disease_evidence` 投影。证候是推理锚点，观察项直接作为条件节点。

顶层元数据：

```json
{
  "schema_version": 2,
  "domain": "tcm",
  "anchor_types": ["syndrome", "disease"],
  "nodes": { "ENTITY:syndrome:TCM_SYN_QI_YIN_XU": { ... } },
  "relations": { "...": { "relation_code": "SYNDROME_HAS_TONGUE", ... } },
  "syndromes": [{ "code": "TCM_SYN_QI_YIN_XU", "name_cn": "气阴两虚证" }]
}
```

节点与关系都会持久化 `attributes`（如 `HERB_ROLE`、`HERB_DOSE`），供方药生成和展示使用。

## 4. 规则与推理

四类规则：

| 规则类型 | 条件 | 结论 |
|---|---|---|
| `tcm_pattern` | 主症 OR + 次症 OR + 舌/脉 OR | 证候（评分） |
| `tcm_treatment` | `fact: syndrome` | 治法 |
| `tcm_prescription` | `fact: syndrome` | 方剂 + 中药（君臣佐使/剂量） |
| `tcm_contraindication` | `fact: syndrome` | 禁忌/慎用 |

推理按 `DomainProfile.stages` 顺序执行；每阶段结论写入 `facts`，后续 `fact` 条件消费。
证候评分 = 命中条目 / 规则条目，结论带 `score`、`matched_conditions`、`total_conditions`。

## 5. API

| 接口 | 说明 |
|---|---|
| `GET /api/profiles` | 领域展示契约 |
| `GET /api/templates?domain=tcm` | 中医模板 |
| `GET /api/kg/graphs?domain=tcm` | 中医图谱列表（含证候数、节点类型统计） |
| `GET /api/kg/graph?graph_filename=tcm_graph.json` | 图谱 + profile |
| `GET /api/vocab/entities` | 实体字典（含 label/domain/color） |
| `GET /api/rules` | 规则列表（含 domain/anchor_type/rule_type） |
| `POST /api/inference/run` `{patient, domain:"tcm"}` | 中医辨证推理 |
| `GET /api/inference/demo-patient?domain=tcm` | 中医示例病历 |
| `GET /api/kg/neo4j/graph?graph_filename=tcm_graph.json` | Neo4j 视图（按证候为中心） |

## 6. 种子数据与演示

```bash
python tools/_seed_tcm_vocab.py       # 实体字典 + 概念谓词
python tools/_add_tcm_template.py     # TCM_GUIDELINE_V1 模板
python tools/_seed_tcm_graph.py       # 生成 data/kg/tcm_graph.json
python tools/_seed_tcm_rules.py       # 导入并启用辨证规则
python tools/_smoke_tcm_inference.py  # 辨证 → 治法 → 方药 冒烟测试
python tools/_smoke_tcm_api.py        # API 冒烟测试
```

种子内容仅用于演示，生产前需中医师审核术语、证候条件与方药剂量。

## 7. 扩展新领域

1. 在 `src/domains/<domain>/profile.py` 定义锚点、阶段、节点元数据、推理表单。
2. 在 `src/domains/registry.py` 注册。
3. 新增模板（`domain`、`capabilities`、`rule_generation`、`graph_filename`）。
4. 新增实体字典与概念谓词。
5. 如需新条件语义，新增 evaluator 并注册到 `EvaluatorFactory`；图谱投影与规则生成放在该领域的 profile 模块。
6. 前端无需新增页面：`/api/profiles/{domain}` 驱动图谱类型、锚点、路径面板与表单。
