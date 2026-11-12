# 基于 Drools 的可视化条件树编辑器 实施计划

## Context

双规则引擎（native 默认 + Drools sidecar）后端已落地：`src/engine/drools/compiler.py` 能把 JSON 条件树编译为 DRL（支持嵌套 AND/OR/NOT 组 + 9 种叶子类型），推理时可自动回退原生。但编辑侧没有可视化能力：

- 「编辑规则」（app.js `openRuleEditor` L3385）条件是**裸 JSON textarea**，医学知识专员无法使用；
- 「规则向导」（`openRuleWizard` L3449）只支持平铺行、行间强制 AND，表达力远低于 DRL 编译器能力。

实际规则数据（data/rules/managed_rules.json）节点分布：group 92、lab_concept 91、exam_finding 54、symptom 26、evidence 24、observation 19、fact 2、weighted_group 2。其中 evidence/fact/weighted_group 不可被 Drools 编译（推理时原生回退）。

**用户已确认的决策：**
1. 可视化编辑器**替换编辑器里的 JSON textarea**，保留「JSON 模式」切换给高级用户；规则向导保留不动。
2. **全类型支持**：evidence/fact/weighted_group 只读展示 + 标注「仅原生引擎」；可编译类型可编辑字段；Drools 可编译性实时徽章 + DRL 预览。

## 改动文件

| 文件 | 动作 |
|---|---|
| `src/engine/drools/compiler.py` | 新增 `describe_uncompilable()` |
| `src/api/routes/engine.py` | 新增 `POST /api/engine/drl-preview` |
| `src/api/static/condition-tree-editor.js` | **新建** 纯函数模块 `window.CdssConditionTree` |
| `src/api/static/app.js` | 改造 `openRuleEditor`（L3385-3447 区域） |
| `src/api/static/index.html` | 在 app.js 之前插入新模块 script 标签（按现有写法） |
| `src/api/static/style.css` | 末尾追加条件树样式 |
| `tests/js/condition_tree_editor.test.js` | **新建**（node --test，vm 加载 pattern） |
| `tests/test_engine_drl_preview.py` | **新建**（pytest，仿 test_engine_api.py 直调路由函数） |

## 任务 1：后端 DRL 预览

**compiler.py** 新增纯函数：
```python
def describe_uncompilable(condition: RuleCondition, path: str = "root") -> Optional[str]
```
递归镜像 `walk_leaves` 语义：返回首个不可编译原因（如 `"root.children[1]: type=evidence 仅原生引擎可执行"`、`"root: group children 为空"`），可编译返回 None。

**engine.py** 新增：
```python
class DrlPreviewPayload(BaseModel):
    rule_id: str = "PREVIEW"
    priority: int = 50
    conditions: Dict[str, Any]

@router.post("/drl-preview")
def drl_preview(payload) -> {"compilable": bool, "drl": str|None, "reason": str|None, "leaf_count": int}
```
- `RuleCondition(**payload.conditions)` 构造失败 → HTTPException 422；
- 构造临时 `Rule(rule_id=..., disease_code="PREVIEW", priority=..., conditions=..., conclusion=RuleConclusion(diagnosis="PREVIEW"))`，走 `compile_rule`（与推理 `_stage_match` 完全同路径，无缓存）。

## 任务 2：前端纯函数模块 condition-tree-editor.js

IIFE + `"use strict"`，导出 `window.CdssConditionTree`，**无 DOM/fetch**（字典选项由 app.js 渲染时传入）。测试用 fs+vm 加载（照抄 tests/js/rule_wizard.test.js 的 pattern）。

- 常量：`SUPPORTED_LEAF_TYPES`（镜像 compiler.py 9 种）、`READONLY_TYPES = ["evidence","fact","weighted_group"]`。
- `sanitizeTree(raw)`：深拷贝，拒绝 `__proto__/constructor/prototype` 键（抛错），剥 null/undefined 字段，未知 type 抛错。
- `isCompilable(tree) -> bool`：group 需 children 非空且全部子可编译；evidence/fact/weighted_group 恒 false。
- `compilabilityReason(tree) -> string|null`：与后端 `describe_uncompilable` 同语义（路径 `children[i]` 拼接），前端本地即时徽章用。
- `countLeaves(tree)`。
- **不可变 CRUD**（path 为子索引数组 `[0,2,1]`，全部返回新树）：
  - `updateNode(tree, path, patch)`（operator 或叶子字段）
  - `addChild(tree, path, child)` / `addGroup(tree, path)`（目标为叶子时自动包裹 AND 组）
  - `addSibling(tree, path, sibling)`（根时把根包进新 AND 组）
  - `removeNode(tree, path)`（父组剩 1 子则上提折叠；删根返回空 AND 组）
  - `moveNode(tree, path, delta)`（越界返回原树）
- `LEAF_FIELDS` 描述表（供 app.js 泛化渲染），每种可编辑叶子的字段清单 + widget 类型（dict/enum/number/text/bool）+ 字典名（lab/exam/symptom/lab_concept/exam_finding）+ enum 候选（observation_type: symptom/tongue/pulse/sign/constitution；gender；vital 比较符）。observation 的 `concept_code:"PRESENT"` 只读展示，weight/required 可编辑。
- `jsonToTree(text)`（parse+sanitize）、`treeToJson(tree)`（2 空格缩进）、`treeToText(tree)`（中文摘要，风格对齐 conditionText L2437：label 优先、组用 AND/OR/NOT 连接）。

## 任务 3：app.js openRuleEditor 改造

- L3404 条件 textarea 替换为：
  ```html
  <div class="field wide"><label>条件树 <span id="drlBadge"></span>
    <button type="button" id="treeModeToggle" class="btn sm">JSON 模式</button></label>
    <div id="conditionTreeHost" class="condition-tree"></div>
    <textarea name="conditions" class="code" hidden></textarea>
    <div id="drlPreview" hidden><pre></pre></div>
  </div>
  ```
  **隐藏 textarea 是唯一数据源**，保存路径（L3413 JSON.parse + resolveRuleConceptCodes 概念校验）零改动。
- `loadEditorDict()`：Promise.all 六个 vocab 接口（复用 wizard L3451-3459 pattern：disease/syndrome/lab/symptom/exam/exam_finding）+ state.vocab 过滤 lab_concept；模块级缓存。
- `renderConditionTree(tree, dict)`：递归嵌套卡片；组头 = operator 三态切换 + 添加子条件/子组/上移/下移/删除（按钮带 `data-path="0-2-1"`）；叶子按 `LEAF_FIELDS` 渲染下拉/输入（dict 下拉显示「名称（编码）」）；evidence/fact/weighted_group 渲染只读灰卡 + 「仅原生引擎」徽章（weighted_group 展示 scoring/threshold/required_children 数）。
- `bindConditionTreeEvents(host)`：事件委托（click/change）→ data-path 解析 → 模块 CRUD → 重渲 → `textarea.value = treeToJson(tree)` → `scheduleDrlPreview()`。
- `scheduleDrlPreview()`：内联 setTimeout 400ms 防抖（先 clearTimeout，项目无现成防抖 util）；POST `/api/engine/drl-preview`；徽章先用本地 `isCompilable/compilabilityReason` 即时渲染，接口返回后刷新 DRL 文本与 leaf_count；`dialog.open === false` 时跳过；失败置灰「预览失败」，不阻塞保存。
- **模式切换**：树→JSON 直接填 textarea 显示；JSON→树调 `jsonToTree`，失败 `toast(错误, true)` 且**停留 JSON 模式**（按钮文案同步）；合法才切换。编辑器打开即树模式 + 首次预览。
- observation 保存时若无 concept_code 自动补 `"PRESENT"`（对齐 wizard L40）。
- **dialog 纪律**：只读场景维持 `save.hidden = true` + `dialog.onclose` 恢复的既有模式；本次编辑器本身可保存，不涉及，但新增按钮需 `type="button"`（form method=dialog）。

## 任务 4：CSS（style.css 末尾追加）

`.condition-tree` 容器、`.ct-node` 卡片（border/radius/浅底，对齐 .endpoint-card 风格）、`.ct-children { margin-left:16px; border-left:2px solid var(--line); padding-left:10px }` 缩进线、`.ct-head` flex 工具条、`.ct-readonly` 灰底、`.drl-preview pre`（深底等宽 max-height 200px overflow auto）、可编译/不可编译徽章复用现有 ok/danger 配色。

## 任务 5：测试

**tests/js/condition_tree_editor.test.js**（fs+vm，plain() 归一化跨 realm）：
- sanitize 剥 null、拒绝 `__proto__`
- isCompilable：lab 组 true；evidence/fact/weighted_group/空组 false
- CRUD：addChild、removeNode 折叠上提、addSibling 包根、moveNode 越界不变
- jsonToTree→treeToJson→jsonToTree roundtrip（含 observation PRESENT 节点）
- compilabilityReason 路径正确

**tests/test_engine_drl_preview.py**（直调路由函数）：
- 可编译 lab 树 → drl 含 `rule "PREVIEW"`、leaf_count > 0
- weighted_group → compilable=False 且 reason 含 weighted_group
- 空组 → False；缺 type → 422

## 任务 6：验证

```bash
node --test tests/js/condition_tree_editor.test.js tests/js/rule_wizard.test.js
python3 -m pytest tests/test_engine_drl_preview.py tests/test_engine_api.py -q
python3 -m pytest -q   # 全量回归
```

手动浏览器（服务 http://127.0.0.1:8001，重启后端生效）：
1. 规则页「编辑」任一托管规则 → 树卡片按 managed_rules.json 渲染正确（含 evidence 只读卡 + 徽章）
2. 切换 AND/OR/NOT → 徽章实时变化；DRL `<pre>` 与 compile_rule 输出一致（NOT 组含 `not ( ... )`）
3. 「JSON 模式」改坏 JSON 再切回 → toast 且停留 JSON 模式；改合法后切回树正常
4. 保存 → 列表 conditionText 正常、草稿无门禁；启用门禁不受影响

## 边界情况

- 根空组：占位卡 + 添加按钮，编译性 false（原因「空条件组」）
- 根为单叶子时 addSibling 包根
- 深嵌套：缩进线 + data-path 定位
- 叶子包组时保留原字段
- JSON 非法时切树模式：不丢用户输入，停留 JSON 模式

## 明确不改

保存门禁（draft 照旧、active 语义/依赖校验照旧）、规则向导 `openRuleWizard`、`conditionText` 列表渲染、推理引擎与 `/api/rules` 后端。
