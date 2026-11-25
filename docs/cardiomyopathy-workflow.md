# 心肌病全病程工作台

## 入口与启动

启动现有 FastAPI 服务，在侧栏「验证运行 → 心肌病全病程」进入，直接地址为 `/static/cardiomyopathy.html`。
现有审核台继续负责临床知识审核；新增页面负责病程、风险、随访、家系和本地推荐反馈。

```bash
python3 -m uvicorn src.api.main:app --host 127.0.0.1 --port 8001
```

运行记录默认保存在 `data/workflows/cardiomyopathy.sqlite3`，不纳入 Git。
可用 `CDSS_WORKFLOW_DB` 指定其他数据库。业务记录和对应审计在同一 SQLite 事务中保存。
此页面沿用项目的本地开发服务访问方式；操作人和医院参数不等于身份认证。接入医院前须由实际认证网关绑定医院、患者权限和角色，不能直接把本地接口开放给临床用户。

## 场景使用

1. 在疾病审核台核对实体、关系和指南出处。
2. 在疑似、确诊、鉴别、排除、风险、严重程度、治疗、用药、禁忌、监测或随访场景整理依据。
3. 可填写场景契约：触发事件、必需病历属性、建议动作和复评事件。API 还支持年龄范围和排除属性。
4. 契约存入 `clinical_context.scenario_contract`。推理输入的 `clinical_attributes.trigger_event` 须与触发事件一致；缺必需属性返回未知并记录问题。未配置契约的旧规则沿用原行为。
5. 依据审核后生成草稿规则，再经现有规则门禁启用。
6. 新整理的检查条件采用就诊时点最近一次结果；同一时点冲突、多次检查缺时间、只有未来结果时不直接判定。旧规则仍保持原选择方式。

风险、鉴别、排除、禁忌和监测结果不再发布为疾病诊断 fact，避免它们被当作确诊前提。推理响应新增 `assessment_conclusions`；`diagnosis_conclusions` 只保留疑似、确诊及中医证候结论，完整结果仍在 `conclusions`。

检查标准会核对年龄和方法；可在检查条件 parameter 中设置 `max_age_hours`，超过时间窗或缺时间返回未知。单位不一致不会隐式换算。

## 场景覆盖与病例验证

`GET /api/review/scenarios/coverage?graph_filename=...&disease_code=...` 分别统计原文、结构化、审核、可执行与病例已验证数量，返回依据 ID 明细。

`POST /api/cardiomyopathy/case-validations?actor=...` 接受 case_id、graph_filename、basis_id、patient、expected_rule_ids、forbidden_rule_ids。
服务以显式测试模式运行规则并保存断言结果，不发布规则。必须包含当前依据生成的规则作为预期命中项。依据或规则内容改变后，原验证记录不再计入当前版本覆盖。

病例测试通过只说明对应断言成立，不能替代临床专家审核。生产知识库的六类真实 HCM 验收病例仍需由专家确认预期；自动化测试中的测试分值与疾病编码不能用作临床知识。

## 风险评估

- PUT/GET `/api/cardiomyopathy/risk-models`：模型版本维护。
- POST `/api/cardiomyopathy/risk-assessments`：指定 model_id、version 和患者。
- 当前计算器支持 `linear_points`，按显式 terms 系数累加并匹配 bands；没有预置临床 HCM 猝死风险公式。
- 模型必须有来源；active 必须记录 reviewer 和 reviewed_at；同一已发布版本不可覆盖。
- 缺变量、不适用人群、非有限数值不会得到风险结论；每次评估保存模型版本、输入和来源。
- 特定疾病的非线性公式、变量定义及模型校准需要专门实现与验证，不能把它们硬套成线性模型。

## 病程、随访及家系

- PUT/GET `/patient-snapshots`：按医院、患者和就诊时间记录快照，查询支持 at 截止时间。
- PUT/GET `/follow-ups`：任务、计划时间、完成时间与依据；完成必须记录时间。
- POST `/follow-ups/reassess`：按到期或复评事件返回待复评任务和缺失属性。当前由调用方触发，不包含后台定时消息发送。
- PUT/GET `/family-members`：亲缘、先证者、家属患者编号、遗传状态、变异分类、解释版本及授权记录。
- POST `/family-screening-tasks`：医生明确指定成员及筛查内容和日期后，转为家属随访任务；VUS 不自动触发筛查结论。
- 儿童、妊娠、运动等专门分支可由场景契约和原有条件树表达；对应临床依据仍须独立审核，成人标准不会直接用于儿童。

## 医嘱推荐和反馈

- PUT/GET `/order-mappings`：院内条目映射，active 须记录核对人。
- POST `/recommendations`：复用现有生产推理门禁，只消费已发布规则；展开已审医嘱组及已审条目关系。
- GET `/recommendations`：按医院及患者查询。
- POST `/recommendations/{id}/feedback`：accepted/rejected 反馈。
- GET `/metrics`：推荐数、处理数及已处理推荐中的采纳率。
- GET `/audit`：本地运行审计摘要。

无映射、未审医嘱组条目、命中禁忌、条件未定或推荐超过24小时会限制采纳。采纳时重新核对映射，映射变化需重新生成建议。
本地 accepted 是医生采纳草稿记录，不代表 HIS 医嘱开立成功；尚未接通真实 CPOE、剂量参数化、药品相互作用库和执行回执。当前安全核查依赖已维护的规则与患者信息，不能视为完整处方审核。

## 验证

```bash
python3 -m pytest -q tests/test_cardiomyopathy_workflow.py tests/test_scenarios.py tests/test_hcm_standards.py tests/test_static_threshold_missing.py tests/test_engine.py
node --check src/api/static/cardiomyopathy.js
node --check src/api/static/scenario-workbench.js
```

浏览器回归须连接隔离测试服务；脚本会写入 TEST-H 医院下的测试患者记录。

```bash
CDSS_WORKFLOW_DB=/tmp/cdss-workflow-test.sqlite3 python3 -m uvicorn src.api.main:app --host 127.0.0.1 --port 8019
CDSS_BASE_URL=http://127.0.0.1:8019 node scripts/cardiomyopathy_browser_test.cjs
```

Node 需可加载 playwright，且已安装 Chrome。浏览器脚本覆盖页面加载、病程保存查询、随访创建完成和模型空状态。
