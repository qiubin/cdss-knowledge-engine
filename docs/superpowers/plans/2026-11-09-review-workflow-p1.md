# 审核工作流 P1 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 P0 的逐条审核基础上，增加统一审核台、批量审核、知识包预审与 `approved_only` 导入、孤儿条目提示。

**Architecture:** 新增 `src/review/queue.py` 聚合图谱/规则/知识包待办；在 `/api/review` 下新增队列和统一批量审核接口；知识包增加 `item_review` 预审数据和条目级 API；重新导入时通过 `source_refs` + `source_doc_ids` 计算孤儿条目；前端新增「审核台」页面，并增强知识包页的条目预审与导入模式。

**Tech Stack:** Python 3.9+、FastAPI、Pydantic v2、JSON 文件存储、原生 JS、unittest、httpx ASGITransport、Playwright（临时浏览器验证）。

**Spec:** `docs/review-workflow-design.md`（P1 章节）

## Global Constraints

- 图谱状态仍为 `draft / active / rejected / retired / stale`；规则仍为 `draft / testing / active / blocked / rejected / retired`。
- 知识包预审状态使用 `draft / approved / rejected`，与图谱/规则状态分离。
- `approved_only` 导入只导入预审通过的知识包条目；未预审条目视为 `draft`，不导入。
- 批量操作允许部分成功，逐条返回结果；每条成功动作写审计。
- 不自动恢复 `blocked` 规则；批量恢复图谱条目后仍需人工重新审核规则。
- 审核人 P0/P1 仍使用字符串姓名，默认 `未署名`。
- 不修改 P0 已实现的状态机与依赖门禁语义。
- 当前环境没有 pytest，新测试使用 `unittest`。
- 不提交、不推送；工作区已有未提交改动，保持原样。

---

### Task 1: 统一审核队列与批量审核 API

**Files:**
- Create: `src/review/queue.py`
- Modify: `src/api/routes/rules.py`（提取 `apply_rule_review`）
- Modify: `src/api/routes/kg_review.py`（新增 `/api/review/queue`、`/api/review/bulk`）
- Test: `tests/test_review_queue.py`

**Interfaces:**
- Produces:
  - `build_queue(service=None, packages=None, graph_files=None, domain=None, graph_filename=None, status=None, item_type=None, q=None, limit=300) -> dict`
  - `rules.apply_rule_review(rule_id, action, reviewer, note="") -> dict`
  - `GET /api/review/queue`
  - `POST /api/review/bulk`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_review_queue.py`：

```python
import json
import tempfile
import unittest
from pathlib import Path

from src.review.queue import build_queue
from src.review.service import ReviewService


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        graph_dir = root / "kg"
        graph_dir.mkdir()
        rules_path = root / "rules" / "managed_rules.json"
        rules_path.parent.mkdir()
        audit_path = root / "review" / "review_log.jsonl"
        (graph_dir / "QUEUE.json").write_text(json.dumps({
            "schema_version": 1,
            "domain": "western",
            "anchor_types": ["disease"],
            "diseases": [{"code": "I21.0", "name_cn": "测试病"}],
            "evidences": [], "disease_evidence": [], "sources": [],
            "nodes": [
                {"id": "ENTITY:disease:I21.0", "node_type": "disease", "code": "I21.0", "name": "测试病", "review_status": "active"},
                {"id": "ENTITY:symptom:SYM_DRAFT", "node_type": "symptom", "code": "SYM_DRAFT", "name": "待审症状", "review_status": "draft"},
            ],
            "relations": [],
        }, ensure_ascii=False), encoding="utf-8")
        rules_path.write_text(json.dumps([{
            "rule_id": "R1", "name": "测试规则", "graph_filename": "QUEUE.json",
            "disease_code": "I21.0", "rule_type": "diagnosis", "status": "draft",
            "conditions": {"type": "group", "operator": "AND", "children": [{"type": "symptom", "symptom_code": "SYM_DRAFT"}]},
            "conclusion": {"diagnosis": "I21.0"},
        }], ensure_ascii=False), encoding="utf-8")
        self.service = ReviewService(graph_dir, rules_path, audit_path)

    def test_queue_contains_graph_and_rule_items(self):
        queue = build_queue(self.service, packages=[], graph_files=["QUEUE.json"])
        item_types = {item["item_type"] for item in queue["items"]}
        self.assertIn("graph_node", item_types)
        self.assertIn("rule", item_types)
        self.assertEqual(queue["summary"]["total"], len(queue["items"]))

    def test_queue_filters_status_and_search(self):
        queue = build_queue(self.service, packages=[], graph_files=["QUEUE.json"], status="draft", q="待审")
        self.assertTrue(queue["items"])
        self.assertTrue(all(item["status"] == "draft" for item in queue["items"]))

    def test_queue_includes_dependency_summary_for_rules(self):
        queue = build_queue(self.service, packages=[], graph_files=["QUEUE.json"])
        rule = next(item for item in queue["items"] if item["item_type"] == "rule")
        self.assertEqual(rule["dependency_summary"]["total"], 3)
        self.assertTrue(rule["dependency_summary"]["blocked"])
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python3 -m unittest tests.test_review_queue -v`  
Expected: FAIL，`ModuleNotFoundError: src.review.queue`

- [ ] **Step 3: 实现 queue.py**

创建 `src/review/queue.py`：

```python
"""Unified review queue across graph items, rules and knowledge packages."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.api.routes.kg import normalize_graph_filename
from src.api.routes.rules import _load_all_rules, check_rule_sync
from src.domains import normalize_domain
from src.review.service import ReviewService

PACKAGE_STATUSES = {"generated", "reviewed", "imported", "ready_for_test", "published", "failed"}


def _package_items(packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = []
    for package in packages:
        items.append({
            "item_type": "package",
            "item_id": package.get("package_id", ""),
            "package_id": package.get("package_id", ""),
            "doc_title": package.get("doc_title", ""),
            "status": package.get("status", ""),
            "domain": normalize_domain(package.get("domain") or "western"),
            "graph_filename": package.get("graph_filename", ""),
            "entity_count": len(package.get("entities") or []),
            "relation_count": len(package.get("suggested_relations") or []),
            "rule_count": len(package.get("suggested_rules") or []),
            "check_summary": package.get("check_summary") or {},
        })
    return items


def build_queue(
    service: Optional[ReviewService] = None,
    packages: Optional[List[Dict[str, Any]]] = None,
    graph_files: Optional[List[str]] = None,
    domain: Optional[str] = None,
    graph_filename: Optional[str] = None,
    status: Optional[str] = None,
    item_type: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 300,
) -> Dict[str, Any]:
    service = service or ReviewService()
    if graph_files is None:
        graph_files = sorted(path.name for path in service.graph_dir.glob("*.json"))
    if graph_filename:
        graph_files = [normalize_graph_filename(graph_filename)]
    target_domain = normalize_domain(domain) if domain else ""
    query = str(q or "").strip().lower()

    items: List[Dict[str, Any]] = []
    for filename in graph_files:
        graph = service._load_graph(filename)
        graph_domain = normalize_domain(graph.get("domain") or "western")
        if target_domain and graph_domain != target_domain:
            continue
        if item_type in (None, "", "graph_node", "graph_relation"):
            wanted = "node" if item_type == "graph_node" else "relation" if item_type == "graph_relation" else None
            for item in service.list_items(filename, status=status, item_type=wanted, q=q):
                items.append({
                    **item,
                    "item_type": "graph_node" if item["item_type"] == "node" else "graph_relation",
                    "graph_filename": filename,
                    "domain": graph_domain,
                })

    rules = _load_all_rules()
    sync = check_rule_sync(rules, graph_loader=service._load_graph)
    by_id = {item["rule_id"]: item for item in sync["rules"]}
    for rule in rules:
        rule_domain = normalize_domain(rule.get("domain") or "western")
        if target_domain and rule_domain != target_domain:
            continue
        if graph_filename and normalize_graph_filename(rule.get("graph_filename") or "stemi_graph.json") != normalize_graph_filename(graph_filename):
            continue
        result = by_id.get(rule.get("rule_id"), {})
        refs = result.get("references") or []
        statuses = [ref.get("status") for ref in refs]
        dependency_summary = {
            "total": len(refs),
            "active": sum(value == "active" for value in statuses),
            "blocked": result.get("dependency_status") != "passed",
        }
        if status and rule.get("status") != status:
            continue
        if item_type and item_type != "rule":
            continue
        text = f"{rule.get('rule_id', '')} {rule.get('name', '')}".lower()
        if query and query not in text:
            continue
        items.append({
            "item_type": "rule",
            "item_id": rule.get("rule_id", ""),
            "rule_id": rule.get("rule_id", ""),
            "name": rule.get("name", ""),
            "status": rule.get("status", ""),
            "domain": rule_domain,
            "graph_filename": rule.get("graph_filename", ""),
            "dependency_summary": dependency_summary,
            "issues": result.get("issues", []),
        })

    if packages is not None and (item_type in (None, "", "package")):
        for package in _package_items(packages):
            if status and package["status"] != status:
                continue
            if target_domain and package["domain"] != target_domain:
                continue
            if query and query not in f"{package.get('package_id', '')} {package.get('doc_title', '')}".lower():
                continue
            items.append(package)

    order = {"graph_node": 0, "graph_relation": 1, "rule": 2, "package": 3}
    status_order = {"draft": 0, "stale": 1, "blocked": 2, "rejected": 3, "active": 4, "retired": 5}
    items.sort(key=lambda item: (
        status_order.get(item.get("status"), 9),
        order.get(item.get("item_type"), 9),
        item.get("graph_filename", ""),
        item.get("name", ""),
    ))
    limited = items[:max(1, int(limit))]
    summary = {
        "graph_nodes": dict(Counter(item["status"] for item in items if item["item_type"] == "graph_node")),
        "graph_relations": dict(Counter(item["status"] for item in items if item["item_type"] == "graph_relation")),
        "rules": dict(Counter(item["status"] for item in items if item["item_type"] == "rule")),
        "packages": dict(Counter(item["status"] for item in items if item["item_type"] == "package")),
        "total": len(items),
    }
    return {"summary": summary, "items": limited}
```

- [ ] **Step 4: 提取 apply_rule_review**

在 `src/api/routes/rules.py` 中新增：

```python
def apply_rule_review(rule_id: str, action: str, reviewer: str, note: str = "") -> Dict[str, Any]:
    rules = _load_all_rules()
    for rule in rules:
        if rule.get("rule_id") != rule_id:
            continue
        current = str(rule.get("status") or "draft")
        try:
            target = next_rule_status(current, action)
        except StatusTransitionError as exc:
            raise HTTPException(422, str(exc)) from exc
        if action == "approve":
            result = _dependency_result(rule)
            if result.get("semantic_status") != "consistent":
                issues = result.get("issues") or ["图谱语义不一致"]
                raise HTTPException(422, "规则与知识图谱语义不一致: " + "; ".join(issues))
            blockers = _activation_blockers(result)
            if blockers:
                raise HTTPException(422, {
                    "error": "规则依赖未通过审核",
                    "dependencies": blockers,
                })
        now = datetime.now(timezone.utc).isoformat()
        rule["status"] = target
        rule["reviewed_by"] = reviewer or "未署名"
        rule["reviewed_at"] = now
        rule["review_note"] = note
        rule["updated_at"] = now
        _save_all_rules(rules)
        append_audit({
            "action": action,
            "entity_type": "rule",
            "entity_id": rule_id,
            "graph_filename": rule.get("graph_filename", ""),
            "before_status": current,
            "after_status": target,
            "reviewer": reviewer or "未署名",
            "note": note,
            "affected_rules": [],
        }, REVIEW_AUDIT_PATH)
        return rule
    raise HTTPException(404, "规则未找到")
```

把 `review_rule` 端点改为：

```python
@router.post("/{rule_id}/review")
async def review_rule(rule_id: str, request: RuleReviewRequest):
    return apply_rule_review(rule_id, request.action, request.reviewer, request.note)
```

- [ ] **Step 5: 增加队列与统一批量接口**

在 `src/api/routes/kg_review.py` 中：

```python
from src.review.queue import build_queue
from src.api.routes import rules as rules_route

class BulkReviewItem(BaseModel):
    item_type: str
    graph_filename: str = ""
    node_id: str = ""
    source_node_id: str = ""
    relation_code: str = ""
    target_node_id: str = ""
    rule_id: str = ""


class UnifiedBulkRequest(BaseModel):
    items: List[BulkReviewItem] = Field(default_factory=list)
    action: str
    reviewer: str = "未署名"
    note: str = ""


@audit_router.get("/queue")
async def review_queue(
    domain: Optional[str] = None,
    graph_filename: Optional[str] = None,
    status: Optional[str] = None,
    item_type: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 300,
):
    return build_queue(
        service=service, packages=[],
        domain=domain, graph_filename=graph_filename,
        status=status, item_type=item_type, q=q, limit=limit,
    )


@audit_router.post("/bulk")
async def review_bulk_unified(request: UnifiedBulkRequest):
    graph_items = []
    rule_items = []
    for item in request.items:
        if item.item_type == "rule":
            rule_items.append(item)
        else:
            graph_items.append(item)
    results = []
    grouped = {}
    for item in graph_items:
        grouped.setdefault(item.graph_filename, []).append(item)
    for filename, items in grouped.items():
        payload = [
            {
                "item_type": "relation" if item.item_type == "graph_relation" else "node",
                "node_id": item.node_id,
                "source_node_id": item.source_node_id,
                "relation_code": item.relation_code,
                "target_node_id": item.target_node_id,
            }
            for item in items
        ]
        result = service.bulk_review(filename, payload, request.action, request.reviewer, request.note)
        results.extend(result["results"])
    for item in rule_items:
        try:
            result = rules_route.apply_rule_review(item.rule_id, request.action, request.reviewer, request.note)
            results.append({"item": item.model_dump(), "ok": True, "result": result})
        except Exception as exc:
            detail = getattr(exc, "detail", str(exc))
            status_code = getattr(exc, "status_code", 422)
            results.append({"item": item.model_dump(), "ok": False, "error": detail, "status_code": status_code})
    return {
        "action": request.action,
        "total": len(request.items),
        "succeeded": sum(1 for item in results if item["ok"]),
        "failed": sum(1 for item in results if not item["ok"]),
        "results": results,
    }
```

> `build_queue` 的 `packages=[]` 让队列只返回图谱和规则；知识包预审在知识包页处理。需要知识包待办时传 `_load_packages()`。

- [ ] **Step 6: 增加 API 测试**

在 `tests/test_review_queue.py` 增加：

```python
import asyncio
import httpx
import src.api.routes.kg_review as kg_review
from src.api.main import app


class QueueApiTest(unittest.TestCase):
    def test_queue_endpoint(self):
        async def go():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get("/api/review/queue?graph_filename=QUEUE.json&limit=10")
        response = asyncio.run(go())
        self.assertEqual(response.status_code, 200)
        self.assertIn("items", response.json())
```

> 该测试使用真实数据目录；若 `QUEUE.json` 不存在则返回空队列，仍应 200。为稳定起见，setUp 中已创建临时 service，但 API 默认 service 指向真实目录。可在测试中把 `kg_review.service` 换成 `self.service` 并调用。保留简单 smoke 即可。

- [ ] **Step 7: 运行测试**

Run: `python3 -m unittest tests.test_review_queue tests.test_rule_review_gate -v`  
Expected: PASS

---

### Task 2: 前端「审核台」页面

**Files:**
- Modify: `src/api/static/index.html`（导航）
- Modify: `src/api/static/app.js`（titles、render 路由、`renderReviewCenter`）
- Modify: `src/api/static/style.css`

**Interfaces:**
- Consumes: `GET /api/review/queue`、`GET /api/review/audit`、`POST /api/review/bulk`
- Produces：审核台页面、批量选择与操作、审核记录

- [ ] **Step 1: 增加导航和路由**

`index.html` 在「规则管理」后增加：

```html
<button class="nav-item" data-route="review">审核台</button>
```

`app.js` 的 `titles` 增加：

```js
    review: ["审核台", "跨图谱与规则的待办、阻断、待复核与审核记录"],
```

`render()` 路由增加：

```js
      else if (state.route === "review") await renderReviewCenter();
```

- [ ] **Step 2: 实现审核台**

在 `renderRules` 前新增：

```js
  async function renderReviewCenter() {
    const reviewer = localStorage.getItem("cdssReviewer") || "";
    const tab = state.reviewTab || "graph";
    const [queue, audit] = await Promise.all([
      api(`/api/review/queue?limit=300`),
      tab === "audit" ? api("/api/review/audit?limit=100") : Promise.resolve({ items: [] }),
    ]);
    const items = (queue.items || []).filter((item) => {
      if (tab === "graph") return ["graph_node", "graph_relation"].includes(item.item_type) && ["draft", "stale"].includes(item.status);
      if (tab === "rules") return item.item_type === "rule" && ["draft", "testing"].includes(item.status);
      if (tab === "blocked") return item.item_type === "rule" && item.status === "blocked";
      if (tab === "stale") return item.status === "stale";
      if (tab === "orphan") return Boolean(item.orphaned_from && item.orphaned_from.length);
      return false;
    });
    const tabs = [
      ["graph", "待审图谱"], ["rules", "待审规则"], ["blocked", "被阻断"],
      ["stale", "待复核"], ["orphan", "孤儿条目"], ["audit", "审核记录"],
    ];
    const tabHtml = tabs.map(([key, label]) => `<button class="review-tab ${tab === key ? "active" : ""}" data-review-tab="${key}">${label}</button>`).join("");
    const rows = items.map((item, index) => {
      const label = item.item_type === "rule"
        ? `${item.rule_id} · ${item.name}`
        : item.item_type === "graph_relation"
          ? `${item.source_node_id} -${item.relation_code}-> ${item.target_node_id}`
          : `${item.name}（${item.code || item.node_id}）`;
      const typeLabel = { graph_node: "节点", graph_relation: "关系", rule: "规则" }[item.item_type] || item.item_type;
      return `<tr>
        <td><input type="checkbox" data-review-select="${index}" ${state.reviewSelection?.has(index) ? "checked" : ""}></td>
        <td>${esc(typeLabel)}</td>
        <td>${esc(label)}</td>
        <td>${reviewBadge(item.status)}</td>
        <td class="mono muted">${esc(item.graph_filename || "-")}</td>
        <td>${esc(String(item.dependent_rule_count ?? item.dependency_summary?.total ?? "-"))}</td>
      </tr>`;
    });
    const auditRows = (audit.items || []).map((row) => `<tr>
      <td class="nowrap">${esc(formatTime(row.ts))}</td>
      <td>${esc(row.entity_type)}</td>
      <td class="mono">${esc(row.entity_id)}</td>
      <td>${esc(row.action)}</td>
      <td>${evtBadge(row.before_status)} → ${evtBadge(row.after_status)}</td>
      <td>${esc(row.reviewer || "-")}</td>
      <td>${esc(row.note || "-")}</td>
    </tr>`);
    content.innerHTML = `
      <div class="review-tabs">${tabHtml}</div>
      ${panel("待办列表", tab === "audit"
        ? table(["时间", "类型", "条目", "动作", "状态变化", "审核人", "意见"], auditRows)
        : `
          <div class="review-toolbar">
            <div class="field"><label>审核人</label><input id="reviewCenterReviewer" value="${esc(reviewer)}" placeholder="姓名"></div>
            <span class="muted">当前 ${items.length} 条；批量操作会逐条返回结果。</span>
            <div class="actions">
              <button class="btn sm primary" data-review-bulk="approve">批量通过</button>
              <button class="btn sm danger" data-review-bulk="reject">批量拒绝</button>
              <button class="btn sm" data-review-bulk="retire">批量失效</button>
              <button class="btn sm" data-review-bulk="restore">批量恢复</button>
            </div>
          </div>
          ${table(["", "类型", "条目", "状态", "图谱", "依赖规则数"], rows)}
        `, "", "审核台汇总图谱、规则和知识包的待办；批量操作按条目逐条执行并写审计")}
    `;
    $("#reviewCenterReviewer").onchange = (event) => localStorage.setItem("cdssReviewer", event.target.value.trim());
    content.querySelectorAll("[data-review-tab]").forEach((button) => button.onclick = () => {
      state.reviewTab = button.dataset.reviewTab;
      state.reviewSelection = new Set();
      renderReviewCenter();
    });
    const selectedIndexes = () => [...content.querySelectorAll("[data-review-select]:checked")].map((input) => Number(input.dataset.reviewSelect));
    content.querySelectorAll("[data-review-bulk]").forEach((button) => button.onclick = async () => {
      const indexes = selectedIndexes();
      if (!indexes.length) { toast("请先勾选条目", true); return; }
      const action = button.dataset.reviewBulk;
      const selected = indexes.map((index) => items[index]);
      if (action === "reject" || action === "retire") {
        const ruleCount = selected.reduce((sum, item) => sum + (item.dependent_rule_count || item.dependency_summary?.total || 0), 0);
        if (!confirm(`将处理 ${selected.length} 条，可能影响 ${ruleCount} 条规则。确认继续？`)) return;
      }
      const note = prompt("审核意见（可选）") || "";
      const payload = selected.map((item) => ({
        item_type: item.item_type,
        graph_filename: item.graph_filename || "",
        node_id: item.node_id || "",
        source_node_id: item.source_node_id || "",
        relation_code: item.relation_code || "",
        target_node_id: item.target_node_id || "",
        rule_id: item.rule_id || "",
      }));
      try {
        const result = await api("/api/review/bulk", { json: {
          items: payload, action,
          reviewer: (($("#reviewCenterReviewer")?.value || "").trim()) || "未署名",
          note,
        }});
        toast(`批量完成：成功 ${result.succeeded}，失败 ${result.failed}`, result.failed > 0);
        state.reviewSelection = new Set();
        renderReviewCenter();
      } catch (error) { toast(error.message, true); }
    });
  }

  function evtBadge(value) {
    return value ? reviewBadge(value) : '<span class="muted">-</span>';
  }
```

- [ ] **Step 3: 增加样式**

`style.css` 追加：

```css
.review-tabs { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.review-tab { border: 1px solid var(--line); background: #fff; border-radius: 999px; padding: 6px 14px; cursor: pointer; }
.review-tab.active { background: var(--accent-soft); border-color: var(--accent); color: var(--accent-dark); }
.review-toolbar { display: flex; align-items: end; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
```

- [ ] **Step 4: 浏览器验证**

- 导航出现「审核台」。
- 页签切换正常。
- 勾选图谱条目并批量通过，状态变化、审计记录增加。
- 审核记录页签可看到动作。

---

### Task 3: 知识包条目预审与 approved_only 导入

**Files:**
- Modify: `src/api/routes/guide_packages.py`
- Test: `tests/test_package_review.py`

**Interfaces:**
- Produces:
  - `GET /api/guide-packages/{package_id}/items`
  - `POST /api/guide-packages/{package_id}/items/review`
  - `POST /api/guide-packages/{package_id}/import {mode:"draft"|"approved_only"}`
  - `_package_items(package)`, `_filter_approved(package)`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_package_review.py`：

```python
import unittest

from src.api.routes.guide_packages import _filter_approved, _package_items


def package():
    return {
        "package_id": "PKG-TEST",
        "doc_id": "DOC-TEST",
        "doc_title": "测试指南",
        "domain": "western",
        "target_disease_code": "I21.0",
        "target_anchor_type": "disease",
        "entities": [
            {"entity_type": "disease", "name": "测试病", "code": "I21.0", "chunk_id": "c1"},
            {"entity_type": "symptom", "name": "症状", "code": "SYM", "chunk_id": "c1"},
        ],
        "suggested_relations": [
            {"relation_code": "DISEASE_HAS_SYMPTOM", "source_code": "I21.0", "target_code": "SYM", "source_type": "disease", "target_type": "symptom", "chunk_id": "c1"},
        ],
        "suggested_rules": [
            {"rule_id": "R1", "name": "规则", "rule_type": "diagnosis"},
        ],
        "item_review": {},
    }


class PackageReviewTest(unittest.TestCase):
    def test_package_items_default_to_draft(self):
        items = _package_items(package())
        self.assertEqual(len(items), 4)
        self.assertTrue(all(item["status"] == "draft" for item in items))

    def test_filter_approved_keeps_only_approved_items(self):
        pkg = package()
        pkg["item_review"] = {
            "entity:disease:I21.0": {"status": "approved"},
            "entity:symptom:SYM": {"status": "approved"},
            "relation:I21.0:DISEASE_HAS_SYMPTOM:SYM": {"status": "approved"},
            "rule:R1": {"status": "approved"},
        }
        filtered = _filter_approved(pkg)
        self.assertEqual(len(filtered["entities"]), 2)
        self.assertEqual(len(filtered["suggested_relations"]), 1)
        self.assertEqual(len(filtered["suggested_rules"]), 1)

    def test_filter_approved_excludes_unapproved(self):
        pkg = package()
        pkg["item_review"] = {"entity:disease:I21.0": {"status": "approved"}}
        filtered = _filter_approved(pkg)
        self.assertEqual(len(filtered["entities"]), 1)
        self.assertEqual(filtered["suggested_relations"], [])
        self.assertEqual(filtered["suggested_rules"], [])
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python3 -m unittest tests.test_package_review -v`  
Expected: FAIL，`_package_items` 不存在

- [ ] **Step 3: 实现条目模型与过滤**

在 `guide_packages.py` 中新增：

```python
PACKAGE_REVIEW_STATUSES = {"draft", "approved", "rejected"}


def _entity_item_id(entity: Dict[str, Any]) -> str:
    code = entity.get("code") or entity.get("concept_code") or entity.get("name")
    return f"entity:{entity.get('entity_type')}:{code}"


def _relation_item_id(relation: Dict[str, Any]) -> str:
    target = relation.get("target_code") or relation.get("target_concept_code") or ""
    return f"relation:{relation.get('source_code')}:{relation.get('relation_code')}:{target}"


def _rule_item_id(rule: Dict[str, Any]) -> str:
    return f"rule:{rule.get('rule_id')}"


def _package_items(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    reviews = package.get("item_review") or {}
    items = []
    seen = set()
    for entity in package.get("entities") or []:
        item_id = _entity_item_id(entity)
        if item_id in seen:
            continue
        seen.add(item_id)
        review = reviews.get(item_id) or {}
        items.append({
            "item_id": item_id,
            "item_type": "entity",
            "entity_type": entity.get("entity_type", ""),
            "name": entity.get("name", ""),
            "code": entity.get("code") or entity.get("concept_code") or "",
            "chunk_id": entity.get("chunk_id", ""),
            "page": entity.get("page"),
            "line_start": entity.get("line_start"),
            "line_end": entity.get("line_end"),
            "status": review.get("status", "draft"),
            "reviewed_by": review.get("reviewed_by", ""),
            "reviewed_at": review.get("reviewed_at", ""),
            "note": review.get("note", ""),
        })
    for relation in package.get("suggested_relations") or []:
        item_id = _relation_item_id(relation)
        if item_id in seen:
            continue
        seen.add(item_id)
        review = reviews.get(item_id) or {}
        items.append({
            "item_id": item_id,
            "item_type": "relation",
            "relation_code": relation.get("relation_code", ""),
            "source_code": relation.get("source_code", ""),
            "target_code": relation.get("target_code") or relation.get("target_concept_code") or "",
            "chunk_id": relation.get("chunk_id", ""),
            "status": review.get("status", "draft"),
            "reviewed_by": review.get("reviewed_by", ""),
            "reviewed_at": review.get("reviewed_at", ""),
            "note": review.get("note", ""),
        })
    for rule in package.get("suggested_rules") or []:
        item_id = _rule_item_id(rule)
        if item_id in seen:
            continue
        seen.add(item_id)
        review = reviews.get(item_id) or {}
        items.append({
            "item_id": item_id,
            "item_type": "rule",
            "rule_id": rule.get("rule_id", ""),
            "name": rule.get("name", ""),
            "rule_type": rule.get("rule_type", ""),
            "status": review.get("status", "draft"),
            "reviewed_by": review.get("reviewed_by", ""),
            "reviewed_at": review.get("reviewed_at", ""),
            "note": review.get("note", ""),
        })
    return items


def _filter_approved(package: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    reviews = package.get("item_review") or {}
    approved = {item_id for item_id, review in reviews.items() if review.get("status") == "approved"}
    entities = [
        entity for entity in package.get("entities") or []
        if _entity_item_id(entity) in approved
    ]
    approved_entity_ids = {_entity_item_id(entity) for entity in entities}
    relations = [
        relation for relation in package.get("suggested_relations") or []
        if _relation_item_id(relation) in approved
        and _entity_item_id({"entity_type": relation.get("source_type"), "code": relation.get("source_code")}) in approved_entity_ids
        and _entity_item_id({"entity_type": relation.get("target_type"), "code": relation.get("target_code") or relation.get("target_concept_code")}) in approved_entity_ids
    ]
    rules = [
        rule for rule in package.get("suggested_rules") or []
        if _rule_item_id(rule) in approved
    ]
    return {"entities": entities, "suggested_relations": relations, "suggested_rules": rules}
```

- [ ] **Step 4: 增加条目审核 API**

在 `guide_packages.py` 增加请求模型：

```python
class PackageItemReviewRequest(BaseModel):
    items: List[str] = []
    action: str
    reviewer: str = "未署名"
    note: str = ""
```

新增接口（放在 `@router.post("/{package_id}/import")` 之前）：

```python
@router.get("/{package_id}/items")
async def list_package_items(
    package_id: str,
    status: Optional[str] = None,
    item_type: Optional[str] = None,
    q: Optional[str] = None,
):
    package = _load_package(package_id)
    items = _package_items(package)
    if status:
        items = [item for item in items if item["status"] == status]
    if item_type:
        items = [item for item in items if item["item_type"] == item_type]
    query = (q or "").strip().lower()
    if query:
        items = [
            item for item in items
            if query in f"{item.get('name', '')} {item.get('code', '')} {item.get('item_id', '')}".lower()
        ]
    summary = {
        "total": len(items),
        "draft": sum(1 for item in items if item["status"] == "draft"),
        "approved": sum(1 for item in items if item["status"] == "approved"),
        "rejected": sum(1 for item in items if item["status"] == "rejected"),
    }
    return {"package_id": package_id, "summary": summary, "items": items}


@router.post("/{package_id}/items/review")
async def review_package_items(package_id: str, request: PackageItemReviewRequest):
    if request.action not in {"approve", "reject", "restore"}:
        raise HTTPException(422, "无效的预审动作")
    package = _load_package(package_id)
    known = {item["item_id"] for item in _package_items(package)}
    unknown = [item_id for item_id in request.items if item_id not in known]
    if unknown:
        raise HTTPException(422, "未知条目: " + ", ".join(unknown[:10]))
    status = {"approve": "approved", "reject": "rejected", "restore": "draft"}[request.action]
    now = _now()
    reviews = package.setdefault("item_review", {})
    for item_id in request.items:
        reviews[item_id] = {
            "status": status,
            "reviewed_by": request.reviewer or "未署名",
            "reviewed_at": now,
            "note": request.note,
        }
    _save_package(package)
    return {"package_id": package_id, "updated": len(request.items), "items": _package_items(package)}
```

- [ ] **Step 5: 增加导入模式**

修改 `GuidePackageImportRequest`：

```python
class GuidePackageImportRequest(BaseModel):
    reviewed_only: bool = False
    mode: str = "draft"
```

修改 `import_package`：

```python
    if request.mode not in {"draft", "approved_only"}:
        raise HTTPException(422, "无效的导入模式")
    entities = package.get("entities", [])
    relations = package.get("suggested_relations", [])
    rules = package.get("suggested_rules", [])
    if request.mode == "approved_only":
        filtered = _filter_approved(package)
        entities = filtered["entities"]
        relations = filtered["suggested_relations"]
        rules = filtered["suggested_rules"]
        if not entities:
            raise HTTPException(422, "没有预审通过的实体，无法导入")
        target = package.get("target_disease_code")
        approved_codes = {item.get("code") for item in entities}
        if target not in approved_codes:
            raise HTTPException(422, "目标疾病/证候未通过预审")
```

然后把 `GraphImportRequest` 和 `RuleImportRequest` 中的 `entities`、`suggested_relations`、`candidate_rules`、`rules` 改为过滤后的变量，并在 `import_result` 中记录 `"mode": request.mode`。

- [ ] **Step 6: 运行测试**

Run: `python3 -m unittest tests.test_package_review -v`  
Expected: PASS（3 tests）

---

### Task 4: 知识包页预审 UI 与导入模式

**Files:**
- Modify: `src/api/static/app.js`（`renderGuidePackages`、`importGuidePackage`）
- Modify: `src/api/static/style.css`

**Interfaces:**
- Consumes: Task 3 的条目 API 和导入模式
- Produces：包内预审表格、批量通过、导入模式选择

- [ ] **Step 1: 加载包内条目**

在 `renderGuidePackages` 中选中 package 后：

```js
    let packageItems = null;
    if (selected) {
      try { packageItems = await api(`/api/guide-packages/${selected.package_id}/items`); }
      catch (_) { packageItems = null; }
    }
```

- [ ] **Step 2: 渲染预审面板**

在「候选内容」面板后增加：

```js
        ${packageItems ? panel("包内预审", `
          <div class="review-toolbar">
            <div class="field"><label>审核人</label><input id="packageReviewer" value="${esc(localStorage.getItem("cdssReviewer") || "")}" placeholder="姓名"></div>
            <div class="field"><label>筛选</label><select id="packageItemStatus">
              <option value="">全部</option>
              <option value="draft">待预审</option>
              <option value="approved">已通过</option>
              <option value="rejected">已拒绝</option>
            </select></div>
            <input id="packageItemSearch" type="search" placeholder="搜索名称/编码…">
            <button id="packageBatchApprove" class="btn sm primary">批量通过当前筛选</button>
            <span class="muted">通过 ${packageItems.summary.approved} / 待审 ${packageItems.summary.draft} / 拒绝 ${packageItems.summary.rejected}</span>
          </div>
          <div id="packageItemTable"></div>
        `, "", "只有预审通过的条目才会在“仅导入已通过项”模式下入库") : ""}
```

- [ ] **Step 3: 渲染条目表格与操作**

在绑定 package 按钮的位置增加：

```js
    const renderPackageItemTable = () => {
      const mount = $("#packageItemTable");
      if (!mount || !packageItems) return;
      const status = $("#packageItemStatus")?.value || "";
      const query = ($("#packageItemSearch")?.value || "").trim().toLowerCase();
      const items = (packageItems.items || []).filter((item) => {
        if (status && item.status !== status) return false;
        return !query || `${item.name || ""} ${item.code || ""} ${item.item_id}`.toLowerCase().includes(query);
      }).slice(0, 200);
      mount.innerHTML = table(["", "类型", "条目", "状态", "操作"], items.map((item, index) => `
        <tr>
          <td><input type="checkbox" data-package-select="${index}"></td>
          <td>${esc(item.item_type)}${item.entity_type ? ` / ${esc(item.entity_type)}` : ""}</td>
          <td><strong>${esc(item.name || item.rule_id || item.item_id)}</strong><div class="mono muted">${esc(item.code || item.item_id)}</div></td>
          <td>${reviewBadge(item.status)}</td>
          <td><div class="actions">
            <button class="btn sm primary" data-package-item="${esc(item.item_id)}" data-package-action="approve">通过</button>
            <button class="btn sm danger" data-package-item="${esc(item.item_id)}" data-package-action="reject">拒绝</button>
            <button class="btn sm" data-package-item="${esc(item.item_id)}" data-package-action="restore">恢复</button>
          </div></td>
        </tr>`));
      mount.querySelectorAll("[data-package-item]").forEach((button) => button.onclick = async () => {
        try {
          await api(`/api/guide-packages/${selected.package_id}/items/review`, { json: {
            items: [button.dataset.packageItem],
            action: button.dataset.packageAction,
            reviewer: ($("#packageReviewer")?.value || "").trim() || "未署名",
          }});
          localStorage.setItem("cdssReviewer", ($("#packageReviewer")?.value || "").trim());
          toast("预审状态已更新");
          renderGuidePackages();
        } catch (error) { toast(error.message, true); }
      });
    };
    renderPackageItemTable();
    $("#packageItemStatus")?.addEventListener("change", renderPackageItemTable);
    $("#packageItemSearch")?.addEventListener("input", renderPackageItemTable);
    $("#packageBatchApprove")?.addEventListener("click", async () => {
      const status = $("#packageItemStatus")?.value || "";
      const query = ($("#packageItemSearch")?.value || "").trim().toLowerCase();
      const ids = (packageItems.items || []).filter((item) => {
        if (status && item.status !== status) return false;
        return !query || `${item.name || ""} ${item.code || ""} ${item.item_id}`.toLowerCase().includes(query);
      }).map((item) => item.item_id);
      if (!ids.length) { toast("没有可预审的条目", true); return; }
      try {
        await api(`/api/guide-packages/${selected.package_id}/items/review`, { json: {
          items: ids, action: "approve",
          reviewer: ($("#packageReviewer")?.value || "").trim() || "未署名",
        }});
        toast(`已通过 ${ids.length} 条`);
        renderGuidePackages();
      } catch (error) { toast(error.message, true); }
    });
```

- [ ] **Step 4: 导入模式**

在「下一步」面板增加模式选择：

```html
<div class="field"><label>导入模式</label><select id="packageImportMode">
  <option value="draft">全部导入为草稿</option>
  <option value="approved_only">仅导入已通过项</option>
</select></div>
```

修改 `importGuidePackage`：

```js
  async function importGuidePackage(packageId, force = false) {
    const mode = $("#packageImportMode")?.value || "draft";
    ...
    const result = await api(`/api/guide-packages/${packageId}/import`, {
      json: { reviewed_only: false, mode },
    });
    ...
  }
```

- [ ] **Step 5: 浏览器验证**

- 知识包页出现「包内预审」。
- 通过/拒绝/恢复单条生效。
- 批量通过按筛选生效。
- 导入模式选择「仅导入已通过项」时，未预审条目不入库（用测试包验证，不操作正式知识包）。

---

### Task 5: 孤儿条目检测

**Files:**
- Modify: `src/review/metadata.py`
- Modify: `src/api/routes/kg.py`（传入当前 chunk 集合）
- Modify: `src/review/service.py`（list_items 返回 orphaned）
- Modify: `src/review/queue.py`（孤儿筛选）
- Test: `tests/test_review_orphans.py`

**Interfaces:**
- Produces:
  - `derive_doc_ids(source_refs) -> list[str]`
  - `apply_import_review_metadata(..., current_chunk_ids=None)`
  - 节点/关系字段 `orphaned_from: list[str]`
  - 队列条目 `orphaned: bool`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_review_orphans.py`：

```python
import unittest

from src.review.metadata import apply_import_review_metadata, derive_doc_ids, snapshot_hashes


class OrphanTest(unittest.TestCase):
    def test_derive_doc_ids_from_chunk_refs(self):
        self.assertEqual(
            derive_doc_ids(["DOC-ABC_p1_l1-10", "DOC-ABC_p2_l3-9", "DOC-XYZ_p1_l1-2"]),
            ["DOC-ABC", "DOC-XYZ"],
        )

    def test_item_not_in_current_import_becomes_orphaned(self):
        graph = {
            "nodes": [{
                "id": "N1", "node_type": "symptom", "code": "S1", "name": "症状",
                "review_status": "active", "source_refs": ["DOC-1_p1_l1-10"],
                "source_doc_ids": ["DOC-1"],
            }],
            "relations": [],
        }
        previous = snapshot_hashes(graph)
        apply_import_review_metadata(
            graph, previous, "DOC-1", current_chunk_ids={"DOC-1_p9_l1-2"}
        )
        self.assertIn("DOC-1", graph["nodes"][0]["orphaned_from"])

    def test_item_seen_in_current_import_is_not_orphaned(self):
        graph = {
            "nodes": [{
                "id": "N1", "node_type": "symptom", "code": "S1", "name": "症状",
                "review_status": "active", "source_refs": ["DOC-1_p1_l1-10"],
                "source_doc_ids": ["DOC-1"],
            }],
            "relations": [],
        }
        previous = snapshot_hashes(graph)
        apply_import_review_metadata(
            graph, previous, "DOC-1", current_chunk_ids={"DOC-1_p1_l1-10"}
        )
        self.assertNotIn("DOC-1", graph["nodes"][0].get("orphaned_from", []))
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python3 -m unittest tests.test_review_orphans -v`  
Expected: FAIL，`derive_doc_ids` 不存在

- [ ] **Step 3: 实现 orphan 逻辑**

在 `metadata.py` 增加：

```python
import re

_DOC_RE = re.compile(r"^(DOC-[A-Za-z0-9]+)")


def derive_doc_ids(source_refs) -> List[str]:
    docs = []
    for ref in source_refs or []:
        match = _DOC_RE.match(str(ref))
        if match and match.group(1) not in docs:
            docs.append(match.group(1))
    return docs
```

修改 `ensure_metadata`：为节点/关系补 `source_doc_ids` 和 `orphaned_from`：

```python
        node.setdefault("source_doc_ids", derive_doc_ids(node.get("source_refs")))
        node.setdefault("orphaned_from", [])
```

关系同理。

修改 `apply_import_review_metadata` 签名：

```python
def apply_import_review_metadata(..., current_chunk_ids: Optional[Set[str]] = None) -> None:
```

在每个节点/关系处理完后调用：

```python
def _update_orphan(item: Dict[str, Any], doc_id: str, current_chunk_ids: Optional[Set[str]]) -> None:
    if current_chunk_ids is None:
        return
    refs = set(item.get("source_refs") or [])
    seen = bool(refs & current_chunk_ids)
    orphaned = set(item.get("orphaned_from") or [])
    if seen:
        orphaned.discard(doc_id)
    elif doc_id in set(item.get("source_doc_ids") or []) or doc_id in derive_doc_ids(item.get("source_refs")):
        orphaned.add(doc_id)
    item["orphaned_from"] = sorted(orphaned)
```

在节点循环和关系循环末尾调用 `_update_orphan(node, doc_id, current_chunk_ids)`。

- [ ] **Step 4: 传入当前 chunk**

在 `import_graph` 的两次 `apply_import_review_metadata` 调用中增加：

```python
apply_import_review_metadata(
    graph, previous_hashes, request.doc_id,
    current_chunk_ids={chunk.get("chunk_id") for chunk in request.chunks},
)
```

- [ ] **Step 5: 队列与列表返回 orphaned**

在 `ReviewService.list_items` 的节点和关系字典中增加：

```python
"orphaned_from": list(node.get("orphaned_from") or []),
"orphaned": bool(node.get("orphaned_from")),
```

关系同理。`queue.build_queue` 已透传 `**item`，因此自动带上。

- [ ] **Step 6: 运行测试**

Run: `python3 -m unittest tests.test_review_orphans tests.test_review_metadata tests.test_review_queue -v`  
Expected: PASS

---

### Task 6: 端到端验证与文档

**Files:**
- Modify: `README.md`
- Test: 全量 unittest + 浏览器 E2E

- [ ] **Step 1: 运行迁移（补 source_doc_ids/orphaned_from）**

```bash
PYTHONPATH=. python3 tools/migrate_review_status.py --dry-run
PYTHONPATH=. python3 tools/migrate_review_status.py
```

`ensure_metadata` 会为现有图谱补齐 `source_doc_ids` 和 `orphaned_from`。

- [ ] **Step 2: 全量测试**

```bash
python3 -m unittest discover -s tests -t . 2>&1 | tail -5
python3 -m unittest tests.test_review_queue tests.test_package_review tests.test_review_orphans -v
```

Expected: 新增测试通过；既有 6 个 TestClient 错误保持。

- [ ] **Step 3: 浏览器 E2E**

- 审核台：页签、批量选择、批量通过/失效、审核记录。
- 知识包页：包内预审、单条通过/拒绝、批量通过、导入模式选择。
- 使用临时测试图谱/测试知识包，完成后删除，避免污染正式数据。

- [ ] **Step 4: 更新 README**

在「知识审核」章节补充：

```markdown
### 审核台（P1）

- 导航「审核台」汇总待审图谱、待审规则、被阻断规则、待复核、孤儿条目和审核记录。
- 支持批量通过/拒绝/失效/恢复；批量操作逐条返回结果并写审计。

### 知识包预审（P1）

- 知识包页提供「包内预审」，可逐条通过/拒绝实体、关系和规则。
- 导入模式支持「全部导入为草稿」和「仅导入已通过项」。
- 未预审条目不会在 `approved_only` 模式下入库。

### 孤儿条目

- 重新导入时，本次未再出现但此前由同一指南贡献的条目会标记 `orphaned_from`，在审核台「孤儿条目」页签中处理，不自动删除。
```

- [ ] **Step 5: 不提交**

保持工作区未提交状态，汇报改动与测试结果。

---

## 自检

### Spec 覆盖（P1 章节）

| 设计 P1 项 | 对应任务 |
|---|---|
| 统一审核台 | Task 1、Task 2 |
| 批量操作和影响面预览 | Task 1、Task 2 |
| 关系级联和批量失效 | Task 1、Task 2（复用 P0 级联） |
| 知识包预审、approved_only | Task 3、Task 4 |
| 孤儿条目提示 | Task 5 |
| 重新导入 stale/版本 | P0 已实现，Task 5 补孤儿 |
| 审核记录 | Task 2 |

### 类型与命名

- 队列条目：`item_type = graph_node | graph_relation | rule | package`
- 知识包预审状态：`draft / approved / rejected`
- 孤儿字段：`orphaned_from: list[str]`、`orphaned: bool`
- 批量接口：`POST /api/review/bulk`

### 已知边界

- 知识包预审不进入统一批量接口；在知识包页单独处理。
- 孤儿判定基于 `source_refs` 与本次导入 chunk 集合，近似但保守；不会自动删除条目。
- 审核台一次最多展示 300 条，超出需用筛选/搜索。
