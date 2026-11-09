# 审核工作流 P0 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让知识图谱节点/关系可以逐条审核、拒绝、失效、恢复，并让规则启用受图谱审核状态约束；条目失效时自动阻断依赖规则，所有动作写入审计日志。

**Architecture:** 新增 `src/review/` 领域模块承载状态机、审计、内容指纹和审核服务；图谱审核 HTTP 接口放在独立路由 `src/api/routes/kg_review.py`；规则侧扩展 `GraphSemanticChecker` 返回依赖引用，并在 `activate_rule` / `activate_consistent` 中加入依赖门禁；前端在图谱页和规则页增加审核操作与状态展示。

**Tech Stack:** Python 3.9+、FastAPI、Pydantic v2、JSON 文件存储、原生 JS 前端、unittest、httpx ASGITransport。

**Spec:** `docs/review-workflow-design.md`

## Global Constraints

- 图谱状态只能是 `draft / active / rejected / retired / stale`；规则状态只能是 `draft / testing / active / blocked / rejected / retired`。
- 只有 `active` 的图谱节点/关系可以作为规则依赖；只有 `active` 规则参与推理。
- 关系变为 `active` 前，两端节点必须都是 `active`。
- 节点/关系变为 `rejected / retired / stale` 时，依赖它的 `active` 规则自动变 `blocked`；`blocked` 规则不得自动恢复。
- 审核人 P0 只接受字符串姓名，默认 `未署名`；不引入登录/RBAC。
- 审计日志固定写入 `data/review/review_log.jsonl`；审核动作落库失败时必须回滚。
- 当前环境没有 pytest，所有新测试使用 `unittest`，运行命令为 `python3 -m unittest <module> -v`。
- 不修改现有推理语义：推理引擎仍只加载 `status == "active"` 的规则。
- 不推送远程仓库；提交步骤仅作为本地检查点。

---

### Task 1: 状态机、错误类型与审计日志

**Files:**
- Create: `src/review/__init__.py`
- Create: `src/review/status.py`
- Create: `src/review/errors.py`
- Create: `src/review/audit.py`
- Test: `tests/test_review_status.py`

**Interfaces:**
- Produces:
  - `next_graph_status(current: str, action: str) -> str`
  - `next_rule_status(current: str, action: str) -> str`
  - `StatusTransitionError(ValueError)`
  - `ReviewError(Exception)`，属性 `detail: str`、`status_code: int`
  - `append_audit(record: dict, path: Path = AUDIT_PATH) -> None`
  - `load_audit(path=AUDIT_PATH, entity_id=None, graph_filename=None, limit=200) -> list[dict]`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_review_status.py`：

```python
import json
import tempfile
import unittest
from pathlib import Path

from src.review.audit import append_audit, load_audit
from src.review.status import (
    StatusTransitionError,
    next_graph_status,
    next_rule_status,
)


class GraphStatusTest(unittest.TestCase):
    def test_draft_approve_becomes_active(self):
        self.assertEqual(next_graph_status("draft", "approve"), "active")

    def test_active_retire_becomes_retired(self):
        self.assertEqual(next_graph_status("active", "retire"), "retired")

    def test_active_cannot_be_approved_again(self):
        with self.assertRaises(StatusTransitionError):
            next_graph_status("active", "approve")

    def test_stale_approve_becomes_active(self):
        self.assertEqual(next_graph_status("stale", "approve"), "active")

    def test_retired_restore_becomes_draft(self):
        self.assertEqual(next_graph_status("retired", "restore"), "draft")


class RuleStatusTest(unittest.TestCase):
    def test_blocked_rule_can_be_approved(self):
        self.assertEqual(next_rule_status("blocked", "approve"), "active")

    def test_active_rule_cannot_be_approved_again(self):
        with self.assertRaises(StatusTransitionError):
            next_rule_status("active", "approve")


class AuditTest(unittest.TestCase):
    def test_append_and_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review" / "review_log.jsonl"
            append_audit({
                "action": "approve",
                "entity_type": "node",
                "entity_id": "ENTITY:symptom:SYM_TEST",
                "graph_filename": "TEST.json",
                "before_status": "draft",
                "after_status": "active",
                "reviewer": "张医生",
            }, path)
            append_audit({
                "action": "retire",
                "entity_type": "node",
                "entity_id": "ENTITY:symptom:OTHER",
                "graph_filename": "TEST.json",
                "before_status": "active",
                "after_status": "retired",
                "reviewer": "李医生",
            }, path)
            rows = load_audit(path=path, entity_id="ENTITY:symptom:SYM_TEST")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["reviewer"], "张医生")
            self.assertIn("ts", rows[0])
            all_rows = load_audit(path=path)
            self.assertEqual(len(all_rows), 2)
```

- [ ] **Step 2: 运行测试，确认导入失败**

Run: `python3 -m unittest tests.test_review_status -v`  
Expected: FAIL / ImportError: No module named 'src.review'

- [ ] **Step 3: 实现最小代码**

创建 `src/review/__init__.py`：

```python
"""Knowledge review workflow."""
```

创建 `src/review/status.py`：

```python
"""Review status vocabularies and transition rules."""
from __future__ import annotations

from typing import Dict, Tuple

GRAPH_STATUSES = ("draft", "active", "rejected", "retired", "stale")
GRAPH_ACTIONS = ("approve", "reject", "retire", "restore")

GRAPH_TRANSITIONS: Dict[Tuple[str, str], str] = {
    ("draft", "approve"): "active",
    ("draft", "reject"): "rejected",
    ("stale", "approve"): "active",
    ("stale", "reject"): "rejected",
    ("stale", "retire"): "retired",
    ("active", "retire"): "retired",
    ("rejected", "restore"): "draft",
    ("retired", "restore"): "draft",
}

RULE_STATUSES = ("draft", "testing", "active", "blocked", "rejected", "retired")
RULE_ACTIONS = ("approve", "reject", "retire", "restore")

RULE_TRANSITIONS: Dict[Tuple[str, str], str] = {
    ("draft", "approve"): "active",
    ("draft", "reject"): "rejected",
    ("testing", "approve"): "active",
    ("testing", "reject"): "rejected",
    ("blocked", "approve"): "active",
    ("blocked", "reject"): "rejected",
    ("blocked", "retire"): "retired",
    ("active", "retire"): "retired",
    ("active", "block"): "blocked",
    ("rejected", "restore"): "draft",
    ("retired", "restore"): "draft",
}


class StatusTransitionError(ValueError):
    """Raised when an action is not allowed for the current status."""


def next_graph_status(current: str, action: str) -> str:
    key = (str(current or "draft"), str(action or ""))
    if key not in GRAPH_TRANSITIONS:
        raise StatusTransitionError(
            f"图谱条目状态 {key[0]!r} 不支持动作 {key[1]!r}"
        )
    return GRAPH_TRANSITIONS[key]


def next_rule_status(current: str, action: str) -> str:
    key = (str(current or "draft"), str(action or ""))
    if key not in RULE_TRANSITIONS:
        raise StatusTransitionError(
            f"规则状态 {key[0]!r} 不支持动作 {key[1]!r}"
        )
    return RULE_TRANSITIONS[key]


def graph_status_usable(status: str) -> bool:
    return str(status or "draft") == "active"
```

创建 `src/review/errors.py`：

```python
"""Review domain errors."""


class ReviewError(Exception):
    def __init__(self, detail: str, status_code: int = 422):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
```

创建 `src/review/audit.py`：

```python
"""Append-only review audit log."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

AUDIT_PATH = Path(__file__).resolve().parents[2] / "data" / "review" / "review_log.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_audit(record: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = Path(path) if path is not None else AUDIT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(record)
    payload.setdefault("ts", _now())
    line = json.dumps(payload, ensure_ascii=False, default=str)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()


def load_audit(
    path: Optional[Path] = None,
    entity_id: str = "",
    graph_filename: str = "",
    limit: int = 200,
) -> List[Dict[str, Any]]:
    target = Path(path) if path is not None else AUDIT_PATH
    if not target.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entity_id and item.get("entity_id") != entity_id:
                continue
            if graph_filename and item.get("graph_filename") != graph_filename:
                continue
            rows.append(item)
    return rows[-max(1, int(limit)):][::-1]
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python3 -m unittest tests.test_review_status -v`  
Expected: PASS（12 tests）

- [ ] **Step 5: 本地提交**

```bash
git add src/review tests/test_review_status.py
git commit -m "feat: add review status machine and audit log"
```

---

### Task 2: 依赖引用与依赖状态

**Files:**
- Modify: `src/api/routes/rules.py:76-430`
- Modify: `src/domains/tcm/checks.py:11-103`
- Test: `tests/test_rule_dependencies.py`

**Interfaces:**
- Produces:
  - `GraphSemanticChecker.check_rule(rule)["references"]: list[dict]`
  - 每个 reference：`{"kind": "node"|"relation", "node_id"?, "source_node_id"?, "relation_code"?, "target_node_id"?, "status": "draft"|"active"|"rejected"|"retired"|"stale"|"missing"}`
  - `check_rule(...)["dependency_status"]: "passed"|"blocked"`
  - `check_rule(...)["review_issues"]: list[str]`
  - `check_rule_sync(rules, graph_loader=None) -> dict`，增加 `dependency_passed_count`、`dependency_blocked_count`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_rule_dependencies.py`：

```python
import unittest

from src.api.routes.rules import GraphSemanticChecker, check_rule_sync
from src.domains.tcm.checks import TcmGraphSemanticChecker


def western_graph(node_status="active"):
    return {
        "domain": "western",
        "diseases": [{"code": "I21.0", "name_cn": "测试病"}],
        "nodes": [
            {"id": "ENTITY:disease:I21.0", "node_type": "disease", "code": "I21.0", "name": "测试病", "review_status": node_status},
            {"id": "ENTITY:lab:CTN", "node_type": "lab", "code": "CTN", "name": "肌钙蛋白", "review_status": node_status},
            {"id": "ENTITY:diagnostic_evidence:CTN:ELEVATED", "node_type": "diagnostic_evidence", "code": "CTN:ELEVATED", "name": "肌钙蛋白升高", "review_status": node_status},
        ],
        "relations": [
            {"source_node_id": "ENTITY:disease:I21.0", "relation_code": "DISEASE_HAS_LAB", "target_node_id": "ENTITY:lab:CTN", "review_status": node_status},
            {"source_node_id": "ENTITY:lab:CTN", "relation_code": "LAB_HAS_DIAGNOSTIC_EVIDENCE", "target_node_id": "ENTITY:diagnostic_evidence:CTN:ELEVATED", "review_status": node_status},
            {"source_node_id": "ENTITY:disease:I21.0", "relation_code": "DISEASE_HAS_DIAGNOSTIC_EVIDENCE", "target_node_id": "ENTITY:diagnostic_evidence:CTN:ELEVATED", "review_status": node_status},
        ],
        "evidences": [],
        "disease_evidence": [],
    }


def lab_rule():
    return {
        "rule_id": "R1",
        "graph_filename": "TEST.json",
        "disease_code": "I21.0",
        "disease_name": "测试病",
        "rule_type": "diagnosis",
        "status": "draft",
        "conditions": {
            "type": "group",
            "operator": "AND",
            "children": [{"type": "lab_concept", "lab_code": "CTN", "concept_code": "ELEVATED"}],
        },
        "conclusion": {"diagnosis": "I21.0"},
    }


class WesternDependencyTest(unittest.TestCase):
    def test_lab_rule_reports_nodes_and_relations(self):
        result = GraphSemanticChecker(western_graph("active")).check_rule(lab_rule())
        self.assertEqual(result["dependency_status"], "passed")
        node_ids = {ref["node_id"] for ref in result["references"] if ref["kind"] == "node"}
        self.assertIn("ENTITY:disease:I21.0", node_ids)
        self.assertIn("ENTITY:lab:CTN", node_ids)
        self.assertIn("ENTITY:diagnostic_evidence:CTN:ELEVATED", node_ids)
        relation_codes = {ref["relation_code"] for ref in result["references"] if ref["kind"] == "relation"}
        self.assertIn("DISEASE_HAS_LAB", relation_codes)
        self.assertIn("LAB_HAS_DIAGNOSTIC_EVIDENCE", relation_codes)

    def test_draft_dependency_blocks_rule(self):
        result = GraphSemanticChecker(western_graph("draft")).check_rule(lab_rule())
        self.assertEqual(result["dependency_status"], "blocked")
        self.assertTrue(result["review_issues"])

    def test_sync_includes_dependency_counts(self):
        result = check_rule_sync([lab_rule()], graph_loader=lambda filename: western_graph("draft"))
        self.assertEqual(result["dependency_passed_count"], 0)
        self.assertEqual(result["dependency_blocked_count"], 1)


def tcm_graph():
    return {
        "domain": "tcm",
        "syndromes": [{"code": "SYN_QI_YIN_XU", "name_cn": "气阴两虚证"}],
        "nodes": [
            {"id": "ENTITY:syndrome:SYN_QI_YIN_XU", "node_type": "syndrome", "code": "SYN_QI_YIN_XU", "name": "气阴两虚证", "review_status": "active"},
            {"id": "ENTITY:symptom:SYM_FATIGUE", "node_type": "symptom", "code": "SYM_FATIGUE", "name": "乏力", "review_status": "active"},
        ],
        "relations": [],
        "diseases": [],
    }


def tcm_rule():
    return {
        "rule_id": "T1",
        "graph_filename": "TCM.json",
        "domain": "tcm",
        "anchor_type": "syndrome",
        "disease_code": "SYN_QI_YIN_XU",
        "rule_type": "tcm_pattern",
        "status": "draft",
        "conditions": {
            "type": "group",
            "operator": "OR",
            "children": [{"type": "observation", "observation_type": "symptom", "code": "SYM_FATIGUE"}],
        },
        "conclusion": {"diagnosis": "SYN_QI_YIN_XU"},
    }


class TcmDependencyTest(unittest.TestCase):
    def test_observation_rule_reports_syndrome_and_symptom(self):
        result = TcmGraphSemanticChecker(tcm_graph()).check_rule(tcm_rule())
        node_ids = {ref["node_id"] for ref in result["references"] if ref["kind"] == "node"}
        self.assertIn("ENTITY:syndrome:SYN_QI_YIN_XU", node_ids)
        self.assertIn("ENTITY:symptom:SYM_FATIGUE", node_ids)
        self.assertEqual(result["dependency_status"], "passed")
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python3 -m unittest tests.test_rule_dependencies -v`  
Expected: FAIL，`KeyError: 'dependency_status'` 或 `'references'`

- [ ] **Step 3: 扩展西方医学检查器**

在 `src/api/routes/rules.py` 的 `GraphSemanticChecker.__init__` 中增加：

```python
        self.nodes = {
            str(item.get("id")): item for item in self._as_list(graph.get("nodes"))
            if isinstance(item, dict) and item.get("id")
        }
        self.relations = {
            (item.get("source_node_id"), item.get("relation_code"), item.get("target_node_id")): item
            for item in self._as_list(graph.get("relations"))
            if isinstance(item, dict)
        }
        self._references = []
```

把 `_add_node_issue` 改为：

```python
    def _record_node(self, node_id: str) -> None:
        node = self.nodes.get(node_id)
        self._references.append({
            "kind": "node",
            "node_id": node_id,
            "status": str((node or {}).get("review_status") or "draft") if node else "missing",
        })

    def _record_relation(self, source: Any, relation_code: str, target: Any) -> None:
        key = (source, relation_code, target)
        relation = self.relations.get(key)
        self._references.append({
            "kind": "relation",
            "source_node_id": source,
            "relation_code": relation_code,
            "target_node_id": target,
            "status": str((relation or {}).get("review_status") or "draft") if relation else "missing",
        })

    def _add_node_issue(self, issues: List[str], node_type: str, code: Any) -> bool:
        node_id = self._node_id(node_type, code)
        self._record_node(node_id)
        if node_id not in self.node_ids:
            issues.append(f"图谱缺少{node_type}节点 {code}")
            return False
        return True
```

把 `_add_relation_issue` 改为：

```python
    def _add_relation_issue(
        self, issues: List[str], source: Any, relation_code: str, target: Any
    ) -> None:
        self._record_relation(source, relation_code, target)
        if (source, relation_code, target) not in self.relation_keys:
            issues.append(f"图谱缺少关系 {relation_code}: {source} -> {target}")
```

在 `check_rule` 开头重置引用，并在返回前汇总：

```python
    def check_rule(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        issues: List[str] = []
        concepts: List[str] = []
        self._references = []
        disease_code = str(rule.get("disease_code") or "")

        if not disease_code:
            issues.append("目标疾病编码为空")
        else:
            self._add_node_issue(issues, "disease", disease_code)
        ...
        references = self._unique_references()
        review_issues = [
            self._reference_issue(ref) for ref in references
            if ref.get("status") != "active"
        ]
        return {
            ...,
            "references": references,
            "dependency_status": "passed" if references and not review_issues else "blocked",
            "review_issues": review_issues,
        }
```

新增方法：

```python
    def _unique_references(self) -> List[Dict[str, Any]]:
        seen = {}
        for ref in self._references:
            key = (
                ref.get("kind"),
                ref.get("node_id"),
                ref.get("source_node_id"),
                ref.get("relation_code"),
                ref.get("target_node_id"),
            )
            seen[key] = ref
        return list(seen.values())

    def _reference_issue(self, ref: Dict[str, Any]) -> str:
        if ref["kind"] == "node":
            return f"节点 {ref['node_id']} 当前状态为 {ref['status']}，需要 active"
        return (
            f"关系 {ref['source_node_id']} -{ref['relation_code']}-> "
            f"{ref['target_node_id']} 当前状态为 {ref['status']}，需要 active"
        )
```

> 注意：原 `check_rule` 中 `elif disease_code not in self.diseases: issues.append(...)` 分支改为调用 `self._add_node_issue(...)`，避免重复与漏记引用。

在 `check_rule_sync` 中增加 `graph_loader` 参数：

```python
def check_rule_sync(rules: List[Dict[str, Any]], graph_loader=None) -> Dict[str, Any]:
    loader = graph_loader or _load_graph
    ...
        graph = loader(filename)
    ...
    dependency_passed = sum(item.get("dependency_status") == "passed" for item in results)
    return {
        ...,
        "dependency_passed_count": dependency_passed,
        "dependency_blocked_count": len(results) - dependency_passed,
    }
```

- [ ] **Step 4: 扩展中医检查器**

在 `src/domains/tcm/checks.py` 中：

- `__init__` 增加 `self.nodes`（id -> node）和 `self._references = []`。
- `_require_node` 记录引用：

```python
    def _require_node(self, issues: List[str], node_type: str, code: str) -> bool:
        if not code:
            issues.append(f"缺少{node_type}编码")
            return False
        node_id = _node_id(node_type, code)
        node = self.nodes.get(node_id)
        self._references.append({
            "kind": "node",
            "node_id": node_id,
            "status": str((node or {}).get("review_status") or "draft") if node else "missing",
        })
        if node_id not in self.node_ids:
            issues.append(f"图谱缺少{node_type}节点 {code}")
            return False
        return True
```

- `check_rule` 开头 `self._references = []`；证候/疾病锚点改为调用 `_require_node`；结论中的治法/方剂/中药已通过 `_require_node` 自动记录。
- 返回前汇总：

```python
        references = self._unique_references()
        review_issues = [
            f"节点 {ref['node_id']} 当前状态为 {ref['status']}，需要 active"
            for ref in references if ref.get("status") != "active"
        ]
        return {
            ...,
            "references": references,
            "dependency_status": "passed" if references and not review_issues else "blocked",
            "review_issues": review_issues,
        }
```

新增 `_unique_references`，逻辑同西方检查器（只处理 node）。

- [ ] **Step 5: 运行测试，确认通过**

Run: `python3 -m unittest tests.test_rule_dependencies tests.test_tcm_domain -v`  
Expected: PASS（新增 4 tests；TCM 原有测试不回归）

- [ ] **Step 6: 本地提交**

```bash
git add src/api/routes/rules.py src/domains/tcm/checks.py tests/test_rule_dependencies.py
git commit -m "feat: report graph dependency references for rules"
```

---

### Task 3: ReviewService（图谱审核、影响面、级联阻断）

**Files:**
- Modify: `src/api/routes/kg.py:100-150`（`_load_graph` / `_save_graph` 支持 `kg_dir`）
- Modify: `src/api/routes/rules.py:40-73`（`_load_all_rules` / `_save_all_rules` 支持 `path`）
- Create: `src/review/service.py`
- Test: `tests/test_graph_review_service.py`

**Interfaces:**
- Produces:
  - `ReviewService(graph_dir=None, rules_path=None, audit_path=None)`
  - `summary(graph_filename) -> dict`
  - `list_items(graph_filename, status=None, item_type=None, node_type=None, source_doc=None, referenced_by_rule=None, q=None) -> list[dict]`
  - `node_detail(graph_filename, node_id) -> dict`
  - `review_node(graph_filename, node_id, action, reviewer, note="") -> dict`
  - `review_relation(graph_filename, source_node_id, relation_code, target_node_id, action, reviewer, note="") -> dict`
  - `bulk_review(graph_filename, items, action, reviewer, note="") -> dict`
  - `impact_for_node(graph_filename, node_id) -> dict`
  - `impact_for_relation(graph_filename, source_node_id, relation_code, target_node_id) -> dict`
  - `node_dependents(graph_filename, node_id) -> dict`
  - `sync_blocked_rules_for_graph(graph_filename, **kwargs) -> list[str]`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_graph_review_service.py`：

```python
import json
import tempfile
import unittest
from pathlib import Path

from src.review.errors import ReviewError
from src.review.service import ReviewService


GRAPH = {
    "schema_version": 1,
    "domain": "western",
    "anchor_types": ["disease"],
    "diseases": [{"code": "I21.0", "name_cn": "测试病"}],
    "evidences": [],
    "disease_evidence": [],
    "sources": [],
    "nodes": [
        {"id": "ENTITY:disease:I21.0", "node_type": "disease", "code": "I21.0", "name": "测试病", "review_status": "active"},
        {"id": "ENTITY:symptom:SYM_TEST", "node_type": "symptom", "code": "SYM_TEST", "name": "测试症状", "review_status": "active"},
        {"id": "ENTITY:symptom:SYM_DRAFT", "node_type": "symptom", "code": "SYM_DRAFT", "name": "待审症状", "review_status": "draft"},
    ],
    "relations": [
        {
            "source_node_id": "ENTITY:disease:I21.0",
            "relation_code": "DISEASE_HAS_SYMPTOM",
            "target_node_id": "ENTITY:symptom:SYM_TEST",
            "relation_name": "疾病有症状",
            "review_status": "active",
        }
    ],
}

RULE = {
    "rule_id": "R1",
    "name": "测试规则",
    "graph_filename": "REVIEW-TEST.json",
    "disease_code": "I21.0",
    "disease_name": "测试病",
    "rule_type": "diagnosis",
    "status": "active",
    "conditions": {
        "type": "group",
        "operator": "AND",
        "children": [{"type": "symptom", "symptom_code": "SYM_TEST"}],
    },
    "conclusion": {"diagnosis": "I21.0", "confidence": "high"},
}


class ReviewServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.graph_dir = root / "kg"
        self.graph_dir.mkdir()
        self.rules_path = root / "rules" / "managed_rules.json"
        self.rules_path.parent.mkdir()
        self.audit_path = root / "review" / "review_log.jsonl"
        (self.graph_dir / "REVIEW-TEST.json").write_text(
            json.dumps(GRAPH, ensure_ascii=False), encoding="utf-8"
        )
        self.rules_path.write_text(json.dumps([RULE], ensure_ascii=False), encoding="utf-8")
        self.service = ReviewService(self.graph_dir, self.rules_path, self.audit_path)

    def test_summary_counts_statuses(self):
        summary = self.service.summary("REVIEW-TEST.json")
        self.assertEqual(summary["nodes"]["active"], 2)
        self.assertEqual(summary["nodes"]["draft"], 1)
        self.assertEqual(summary["relations"]["active"], 1)

    def test_approve_draft_node_writes_audit(self):
        result = self.service.review_node(
            "REVIEW-TEST.json", "ENTITY:symptom:SYM_DRAFT", "approve", "张医生", "确认"
        )
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["item"]["reviewed_by"], "张医生")
        audit = self.service.audit_records(entity_id="ENTITY:symptom:SYM_DRAFT")
        self.assertEqual(audit[0]["action"], "approve")

    def test_relation_approval_requires_active_endpoints(self):
        with self.assertRaises(ReviewError) as ctx:
            self.service.review_relation(
                "REVIEW-TEST.json",
                "ENTITY:disease:I21.0", "DISEASE_HAS_SYMPTOM", "ENTITY:symptom:SYM_DRAFT",
                "approve", "张医生",
            )
        self.assertEqual(ctx.exception.status_code, 422)

    def test_retiring_node_blocks_dependent_active_rule(self):
        result = self.service.review_node(
            "REVIEW-TEST.json", "ENTITY:symptom:SYM_TEST", "retire", "张医生", "指南更新"
        )
        self.assertIn("R1", result["affected_rules"])
        rules = json.loads(self.rules_path.read_text(encoding="utf-8"))
        self.assertEqual(rules[0]["status"], "blocked")
        self.assertEqual(rules[0]["blocked_by"][0]["reason"], "stale")

    def test_impact_counts_rules(self):
        impact = self.service.impact_for_node("REVIEW-TEST.json", "ENTITY:symptom:SYM_TEST")
        self.assertEqual(impact["incident_relations"], 1)
        self.assertEqual(impact["rules"]["total"], 1)
        self.assertEqual(impact["rules"]["active"], 1)

    def test_list_items_filters_status(self):
        items = self.service.list_items("REVIEW-TEST.json", status="draft")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["node_id"], "ENTITY:symptom:SYM_DRAFT")
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python3 -m unittest tests.test_graph_review_service -v`  
Expected: FAIL，`ModuleNotFoundError: src.review.service`

- [ ] **Step 3: 让图谱和规则存储支持注入路径**

修改 `src/api/routes/kg.py`：

```python
def _load_graph(
    filename: str = DEFAULT_GRAPH_FILENAME,
    kg_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    base = Path(kg_dir) if kg_dir is not None else KG_DIR
    path = base / normalize_graph_filename(filename)
    ...

def _save_graph(
    data: Dict[str, Any],
    filename: str = DEFAULT_GRAPH_FILENAME,
    kg_dir: Optional[Path] = None,
):
    base = Path(kg_dir) if kg_dir is not None else KG_DIR
    path = base / normalize_graph_filename(filename)
    ...
```

修改 `src/api/routes/rules.py`：

```python
def _load_all_rules(path: Optional[Path] = None) -> List[Dict]:
    target = Path(path) if path is not None else MANAGED_RULES_PATH
    if target.exists():
        rules = json.loads(target.read_text(encoding="utf-8"))
    else:
        rules = []
        for f in target.parent.glob("*.json"):
            ...
    ...

def _save_all_rules(rules: List[Dict], path: Optional[Path] = None):
    target = Path(path) if path is not None else MANAGED_RULES_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **Step 4: 实现 ReviewService**

创建 `src/review/service.py`：

```python
"""Graph review operations: status transitions, impact, cascade and audit."""
from __future__ import annotations

import copy
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.api.routes.kg import (
    DEFAULT_GRAPH_FILENAME,
    KG_DIR,
    _load_graph,
    _save_graph,
    normalize_graph_filename,
)
from src.api.routes.rules import (
    MANAGED_RULES_PATH,
    _load_all_rules,
    _save_all_rules,
    check_rule_sync,
)
from .audit import AUDIT_PATH, append_audit, load_audit
from .errors import ReviewError
from .status import StatusTransitionError, next_graph_status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relation_key(relation: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(relation.get("source_node_id") or ""),
        str(relation.get("relation_code") or ""),
        str(relation.get("target_node_id") or ""),
    )


def _relation_id(key: Tuple[str, str, str]) -> str:
    return "|".join(key)


def _status(item: Dict[str, Any]) -> str:
    return str(item.get("review_status") or "draft")


class ReviewService:
    def __init__(
        self,
        graph_dir: Optional[Path] = None,
        rules_path: Optional[Path] = None,
        audit_path: Optional[Path] = None,
    ):
        self.graph_dir = Path(graph_dir) if graph_dir is not None else KG_DIR
        self.rules_path = Path(rules_path) if rules_path is not None else MANAGED_RULES_PATH
        self.audit_path = Path(audit_path) if audit_path is not None else AUDIT_PATH

    # ---- storage ----
    def _load_graph(self, filename: str) -> Dict[str, Any]:
        return _load_graph(filename, kg_dir=self.graph_dir)

    def _save_graph(self, graph: Dict[str, Any], filename: str) -> None:
        _save_graph(graph, filename, kg_dir=self.graph_dir)

    def _load_rules(self) -> List[Dict[str, Any]]:
        return _load_all_rules(self.rules_path)

    def _save_rules(self, rules: List[Dict[str, Any]]) -> None:
        _save_all_rules(rules, self.rules_path)

    def _rules_for_graph(
        self, rules: List[Dict[str, Any]], graph_filename: str
    ) -> List[Dict[str, Any]]:
        result = []
        for rule in rules:
            try:
                filename = normalize_graph_filename(
                    rule.get("graph_filename") or DEFAULT_GRAPH_FILENAME
                )
            except Exception:
                continue
            if filename == graph_filename:
                result.append(rule)
        return result

    def _sync(self, rules: List[Dict[str, Any]]) -> Dict[str, Any]:
        return check_rule_sync(rules, graph_loader=self._load_graph)

    # ---- audit ----
    def audit_records(self, **filters) -> List[Dict[str, Any]]:
        return load_audit(path=self.audit_path, **filters)

    def _persist(
        self,
        filename: str,
        previous_graph: Dict[str, Any],
        graph: Dict[str, Any],
        previous_rules: List[Dict[str, Any]],
        rules: List[Dict[str, Any]],
        audit_records: List[Dict[str, Any]],
    ) -> None:
        try:
            self._save_graph(graph, filename)
            self._save_rules(rules)
            for record in audit_records:
                append_audit(record, self.audit_path)
        except Exception as exc:
            self._save_graph(previous_graph, filename)
            self._save_rules(previous_rules)
            raise ReviewError(f"审核保存失败: {exc}", 500) from exc

    # ---- summaries / listings ----
    def summary(self, graph_filename: str) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        rules = self._rules_for_graph(self._load_rules(), filename)
        return {
            "graph_filename": filename,
            "nodes": dict(Counter(_status(item) for item in graph["nodes"].values())),
            "relations": dict(Counter(_status(item) for item in graph["relations"].values())),
            "rules": dict(Counter(str(item.get("status") or "draft") for item in rules)),
            "blocked_rules": sum(1 for item in rules if item.get("status") == "blocked"),
        }

    def _dependency_index(
        self, filename: str
    ) -> Tuple[Counter, Counter, Dict[str, Dict[str, Any]]]:
        rules = self._rules_for_graph(self._load_rules(), filename)
        sync = self._sync(rules)
        node_counts: Counter = Counter()
        relation_counts: Counter = Counter()
        by_rule: Dict[str, Dict[str, Any]] = {}
        for result in sync["rules"]:
            by_rule[result["rule_id"]] = result
            for ref in result.get("references") or []:
                if ref.get("kind") == "node":
                    node_counts[ref.get("node_id")] += 1
                else:
                    relation_counts[
                        _relation_id((
                            ref.get("source_node_id"),
                            ref.get("relation_code"),
                            ref.get("target_node_id"),
                        ))
                    ] += 1
        return node_counts, relation_counts, by_rule

    def list_items(
        self,
        graph_filename: str,
        status: Optional[str] = None,
        item_type: Optional[str] = None,
        node_type: Optional[str] = None,
        source_doc: Optional[str] = None,
        referenced_by_rule: Optional[bool] = None,
        q: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        node_counts, relation_counts, _ = self._dependency_index(filename)
        query = str(q or "").strip().lower()
        items: List[Dict[str, Any]] = []
        if item_type in (None, "", "node"):
            for node_id, node in graph["nodes"].items():
                item = {
                    "item_type": "node",
                    "item_id": node_id,
                    "node_id": node_id,
                    "node_type": str(node.get("node_type") or ""),
                    "code": str(node.get("code") or node.get("concept_code") or ""),
                    "name": str(node.get("name") or ""),
                    "status": _status(node),
                    "source_refs": list(node.get("source_refs") or []),
                    "dependent_rule_count": int(node_counts.get(node_id, 0)),
                    "updated_at": str(node.get("reviewed_at") or ""),
                }
                items.append(item)
        if item_type in (None, "", "relation"):
            for key, relation in graph["relations"].items():
                item = {
                    "item_type": "relation",
                    "item_id": _relation_id(key),
                    "source_node_id": key[0],
                    "relation_code": key[1],
                    "target_node_id": key[2],
                    "node_type": "relation",
                    "name": str(relation.get("relation_name") or key[1]),
                    "status": _status(relation),
                    "source_refs": list(relation.get("source_refs") or []),
                    "dependent_rule_count": int(relation_counts.get(_relation_id(key), 0)),
                    "updated_at": str(relation.get("reviewed_at") or ""),
                }
                items.append(item)
        filtered = []
        for item in items:
            if status and item["status"] != status:
                continue
            if node_type and item.get("node_type") != node_type:
                continue
            if source_doc and not any(str(ref).startswith(source_doc) for ref in item["source_refs"]):
                continue
            if referenced_by_rule is True and item["dependent_rule_count"] <= 0:
                continue
            if referenced_by_rule is False and item["dependent_rule_count"] > 0:
                continue
            if query and query not in " ".join([
                item.get("name", ""), item.get("code", ""), item.get("item_id", ""),
            ]).lower():
                continue
            filtered.append(item)
        return sorted(filtered, key=lambda item: (item["item_type"], item.get("node_type", ""), item.get("name", "")))

    def node_detail(self, graph_filename: str, node_id: str) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        node = graph["nodes"].get(node_id)
        if node is None:
            raise ReviewError("节点不存在", 404)
        relations = [
            {"key": key, "relation": relation}
            for key, relation in graph["relations"].items()
            if node_id in (key[0], key[2])
        ]
        return {
            "node": node,
            "relations": relations,
            "dependents": self.node_dependents(filename, node_id),
            "audit": self.audit_records(entity_id=node_id),
        }

    # ---- impact ----
    def _rule_items_for_reference(
        self, filename: str, predicate
    ) -> Dict[str, Any]:
        rules = self._rules_for_graph(self._load_rules(), filename)
        sync = self._sync(rules)
        dependent = []
        for result in sync["rules"]:
            if any(predicate(ref) for ref in result.get("references") or []):
                dependent.append({
                    "rule_id": result["rule_id"],
                    "name": result.get("name", ""),
                    "status": result.get("status", ""),
                    "dependency_status": result.get("dependency_status", ""),
                    "issues": result.get("issues", []),
                })
        counts = Counter(item["status"] for item in dependent)
        return {
            "total": len(dependent),
            "active": int(counts.get("active", 0)),
            "draft": int(counts.get("draft", 0)),
            "testing": int(counts.get("testing", 0)),
            "blocked": int(counts.get("blocked", 0)),
            "rejected": int(counts.get("rejected", 0)),
            "retired": int(counts.get("retired", 0)),
            "items": dependent,
        }

    def impact_for_node(self, graph_filename: str, node_id: str) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        node = graph["nodes"].get(node_id)
        if node is None:
            raise ReviewError("节点不存在", 404)
        incident = [
            relation for relation in graph["relations"].values()
            if node_id in (relation.get("source_node_id"), relation.get("target_node_id"))
        ]
        return {
            "node_id": node_id,
            "name": node.get("name", ""),
            "status": _status(node),
            "incident_relations": len(incident),
            "rules": self._rule_items_for_reference(
                filename, lambda ref: ref.get("kind") == "node" and ref.get("node_id") == node_id
            ),
        }

    def impact_for_relation(
        self,
        graph_filename: str,
        source_node_id: str,
        relation_code: str,
        target_node_id: str,
    ) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        key = (source_node_id, relation_code, target_node_id)
        relation = graph["relations"].get(key)
        if relation is None:
            raise ReviewError("关系不存在", 404)
        return {
            "relation_id": _relation_id(key),
            "status": _status(relation),
            "rules": self._rule_items_for_reference(
                filename,
                lambda ref: (
                    ref.get("kind") == "relation"
                    and ref.get("source_node_id") == source_node_id
                    and ref.get("relation_code") == relation_code
                    and ref.get("target_node_id") == target_node_id
                ),
            ),
        }

    def node_dependents(self, graph_filename: str, node_id: str) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        rules = self._rules_for_graph(self._load_rules(), filename)
        sync = self._sync(rules)
        items = []
        for result in sync["rules"]:
            refs = [
                ref for ref in result.get("references") or []
                if ref.get("kind") == "node" and ref.get("node_id") == node_id
            ]
            if refs:
                items.append({
                    "rule_id": result["rule_id"],
                    "name": result.get("name", ""),
                    "status": result.get("status", ""),
                    "dependency_status": result.get("dependency_status", ""),
                    "issues": result.get("issues", []),
                })
        return {"node_id": node_id, "rules": items}

    # ---- status changes ----
    def _blocked_by(self, references: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        now = _now()
        result = []
        for ref in references:
            if ref.get("status") == "active":
                continue
            if ref.get("kind") == "node":
                entity_id = str(ref.get("node_id") or "")
                entity_type = "node"
            else:
                entity_id = _relation_id((
                    ref.get("source_node_id"),
                    ref.get("relation_code"),
                    ref.get("target_node_id"),
                ))
                entity_type = "relation"
            result.append({
                "entity_type": entity_type,
                "entity_id": entity_id,
                "reason": str(ref.get("status") or "missing"),
                "since": now,
            })
        return result

    def _sync_blocked_rules(
        self, rules: List[Dict[str, Any]], graph_filename: str
    ) -> List[str]:
        graph_rules = self._rules_for_graph(rules, graph_filename)
        sync = self._sync(graph_rules)
        by_id = {item["rule_id"]: item for item in sync["rules"]}
        changed = []
        for rule in graph_rules:
            result = by_id.get(rule.get("rule_id"), {})
            if result.get("dependency_status") == "passed":
                continue
            if rule.get("status") != "active":
                continue
            rule["status"] = "blocked"
            rule["blocked_by"] = self._blocked_by(result.get("references") or [])
            rule["updated_at"] = _now()
            changed.append(rule["rule_id"])
        return changed

    def review_node(
        self,
        graph_filename: str,
        node_id: str,
        action: str,
        reviewer: str,
        note: str = "",
    ) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        node = graph["nodes"].get(node_id)
        if node is None:
            raise ReviewError("节点不存在", 404)
        current = _status(node)
        try:
            target = next_graph_status(current, action)
        except StatusTransitionError as exc:
            raise ReviewError(str(exc), 422) from exc

        previous_graph = copy.deepcopy(graph)
        previous_rules = self._load_rules()
        rules = copy.deepcopy(previous_rules)
        now = _now()
        node.update({
            "review_status": target,
            "reviewed_by": reviewer or "未署名",
            "reviewed_at": now,
            "review_note": note,
        })
        if target != "active":
            for relation in graph["relations"].values():
                if node_id in (relation.get("source_node_id"), relation.get("target_node_id")):
                    if _status(relation) not in {"rejected", "retired"}:
                        relation["review_status"] = "stale"
                        relation["reviewed_at"] = now
        affected = self._sync_blocked_rules(rules, filename)
        audit = [{
            "action": action,
            "entity_type": "node",
            "entity_id": node_id,
            "graph_filename": filename,
            "before_status": current,
            "after_status": target,
            "reviewer": reviewer or "未署名",
            "note": note,
            "affected_rules": affected,
        }]
        self._persist(filename, previous_graph, graph, previous_rules, rules, audit)
        return {"item": node, "status": target, "affected_rules": affected}

    def review_relation(
        self,
        graph_filename: str,
        source_node_id: str,
        relation_code: str,
        target_node_id: str,
        action: str,
        reviewer: str,
        note: str = "",
    ) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        key = (source_node_id, relation_code, target_node_id)
        relation = graph["relations"].get(key)
        if relation is None:
            raise ReviewError("关系不存在", 404)
        current = _status(relation)
        try:
            target = next_graph_status(current, action)
        except StatusTransitionError as exc:
            raise ReviewError(str(exc), 422) from exc
        if action == "approve":
            problems = []
            for endpoint in (source_node_id, target_node_id):
                node = graph["nodes"].get(endpoint)
                if node is None or _status(node) != "active":
                    problems.append(f"{endpoint} 状态为 {(_status(node) if node else 'missing')}")
            if problems:
                raise ReviewError(
                    "关系两端节点必须先通过审核: " + "; ".join(problems), 422
                )

        previous_graph = copy.deepcopy(graph)
        previous_rules = self._load_rules()
        rules = copy.deepcopy(previous_rules)
        now = _now()
        relation.update({
            "review_status": target,
            "reviewed_by": reviewer or "未署名",
            "reviewed_at": now,
            "review_note": note,
        })
        affected = self._sync_blocked_rules(rules, filename)
        audit = [{
            "action": action,
            "entity_type": "relation",
            "entity_id": _relation_id(key),
            "graph_filename": filename,
            "before_status": current,
            "after_status": target,
            "reviewer": reviewer or "未署名",
            "note": note,
            "affected_rules": affected,
        }]
        self._persist(filename, previous_graph, graph, previous_rules, rules, audit)
        return {"item": relation, "status": target, "affected_rules": affected}

    def bulk_review(
        self,
        graph_filename: str,
        items: List[Dict[str, Any]],
        action: str,
        reviewer: str,
        note: str = "",
    ) -> Dict[str, Any]:
        results = []
        for item in items:
            try:
                if item.get("item_type") == "relation":
                    result = self.review_relation(
                        graph_filename,
                        str(item.get("source_node_id") or ""),
                        str(item.get("relation_code") or ""),
                        str(item.get("target_node_id") or ""),
                        action, reviewer, note,
                    )
                else:
                    result = self.review_node(
                        graph_filename, str(item.get("node_id") or ""),
                        action, reviewer, note,
                    )
                results.append({"item": item, "ok": True, "result": result})
            except ReviewError as exc:
                results.append({
                    "item": item, "ok": False,
                    "error": exc.detail, "status_code": exc.status_code,
                })
        return {
            "action": action,
            "total": len(items),
            "succeeded": sum(1 for item in results if item["ok"]),
            "failed": sum(1 for item in results if not item["ok"]),
            "results": results,
        }


def sync_blocked_rules_for_graph(
    graph_filename: str,
    **service_kwargs,
) -> List[str]:
    """Block active rules whose graph dependencies are no longer usable."""
    service = ReviewService(**service_kwargs)
    filename = normalize_graph_filename(graph_filename)
    rules = service._load_rules()
    changed = service._sync_blocked_rules(rules, filename)
    if changed:
        service._save_rules(rules)
        for rule_id in changed:
            append_audit({
                "action": "auto_block",
                "entity_type": "rule",
                "entity_id": rule_id,
                "graph_filename": filename,
                "before_status": "active",
                "after_status": "blocked",
                "reviewer": "system",
                "note": "图谱依赖不再可用",
            }, service.audit_path)
    return changed
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `python3 -m unittest tests.test_graph_review_service -v`  
Expected: PASS（6 tests）

- [ ] **Step 6: 本地提交**

```bash
git add src/review/service.py src/api/routes/kg.py src/api/routes/rules.py tests/test_graph_review_service.py
git commit -m "feat: add graph review service with impact and cascade"
```

---

### Task 4: 重新导入的内容指纹、stale 检测与迁移脚本

**Files:**
- Create: `src/review/metadata.py`
- Modify: `src/api/routes/kg.py:691-1130`（导入前后调用元数据）
- Create: `tools/migrate_review_status.py`
- Test: `tests/test_review_metadata.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces:
  - `node_content_hash(node) -> str`
  - `relation_content_hash(relation) -> str`
  - `snapshot_hashes(graph) -> dict`
  - `ensure_metadata(graph, now=None) -> None`
  - `apply_import_review_metadata(graph, previous_hashes, doc_id, now=None) -> None`
  - `migrate(graph_dir, rules_path, audit_path, dry_run=False) -> dict`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_review_metadata.py`：

```python
import json
import tempfile
import unittest
from pathlib import Path

from src.review.metadata import (
    apply_import_review_metadata,
    node_content_hash,
    snapshot_hashes,
)
from tools.migrate_review_status import migrate


class MetadataTest(unittest.TestCase):
    def test_unchanged_active_node_stays_active(self):
        node = {"id": "N1", "node_type": "symptom", "code": "S1", "name": "症状", "review_status": "active"}
        graph = {"nodes": [node], "relations": []}
        previous = snapshot_hashes(graph)
        apply_import_review_metadata(graph, previous, "DOC-1")
        self.assertEqual(graph["nodes"][0]["review_status"], "active")

    def test_changed_active_node_becomes_stale(self):
        graph = {"nodes": [{"id": "N1", "node_type": "symptom", "code": "S1", "name": "旧名", "review_status": "active"}], "relations": []}
        previous = snapshot_hashes(graph)
        graph["nodes"][0]["name"] = "新名"
        apply_import_review_metadata(graph, previous, "DOC-1")
        self.assertEqual(graph["nodes"][0]["review_status"], "stale")
        self.assertEqual(graph["nodes"][0]["version"], 2)
        self.assertIn("DOC-1", graph["nodes"][0]["source_doc_ids"])


class MigrationTest(unittest.TestCase):
    def test_migration_bootstraps_active_rule_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_dir = root / "kg"
            graph_dir.mkdir()
            rules_path = root / "rules" / "managed_rules.json"
            rules_path.parent.mkdir()
            audit_path = root / "review" / "review_log.jsonl"
            graph = {
                "schema_version": 1, "domain": "western", "anchor_types": ["disease"],
                "diseases": [{"code": "I21.0", "name_cn": "测试病"}],
                "evidences": [], "disease_evidence": [], "sources": [],
                "nodes": [
                    {"id": "ENTITY:disease:I21.0", "node_type": "disease", "code": "I21.0", "name": "测试病", "review_status": "draft"},
                    {"id": "ENTITY:symptom:SYM_TEST", "node_type": "symptom", "code": "SYM_TEST", "name": "测试症状", "review_status": "draft"},
                ],
                "relations": [{"source_node_id": "ENTITY:disease:I21.0", "relation_code": "DISEASE_HAS_SYMPTOM", "target_node_id": "ENTITY:symptom:SYM_TEST", "review_status": "draft"}],
            }
            (graph_dir / "MIGRATE.json").write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
            rules_path.write_text(json.dumps([{
                "rule_id": "R1", "graph_filename": "MIGRATE.json", "disease_code": "I21.0",
                "rule_type": "diagnosis", "status": "active",
                "conditions": {"type": "group", "operator": "AND", "children": [{"type": "symptom", "symptom_code": "SYM_TEST"}]},
                "conclusion": {"diagnosis": "I21.0"},
            }], ensure_ascii=False), encoding="utf-8")
            result = migrate(graph_dir, rules_path, audit_path)
            self.assertGreaterEqual(result["bootstrapped_nodes"], 2)
            saved = json.loads((graph_dir / "MIGRATE.json").read_text(encoding="utf-8"))
            statuses = {item["id"]: item["review_status"] for item in saved["nodes"]}
            self.assertEqual(statuses["ENTITY:disease:I21.0"], "active")
            self.assertEqual(statuses["ENTITY:symptom:SYM_TEST"], "active")
            self.assertTrue(audit_path.exists())
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python3 -m unittest tests.test_review_metadata -v`  
Expected: FAIL，模块不存在

- [ ] **Step 3: 实现 metadata.py**

创建 `src/review/metadata.py`：

```python
"""Content fingerprint and review metadata for graph import."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(payload: Dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def node_content_hash(node: Dict[str, Any]) -> str:
    return _hash({
        "node_type": node.get("node_type"),
        "code": node.get("code"),
        "name": node.get("name"),
        "attributes": node.get("attributes") or {},
        "source_refs": sorted(node.get("source_refs") or []),
    })


def relation_content_hash(relation: Dict[str, Any]) -> str:
    return _hash({
        "source_node_id": relation.get("source_node_id"),
        "relation_code": relation.get("relation_code"),
        "target_node_id": relation.get("target_node_id"),
        "attributes": relation.get("attributes") or {},
        "source_refs": sorted(relation.get("source_refs") or []),
    })


def _node_items(graph: Dict[str, Any]):
    nodes = graph.get("nodes") or {}
    return nodes.values() if isinstance(nodes, dict) else nodes


def _relation_items(graph: Dict[str, Any]):
    relations = graph.get("relations") or {}
    if isinstance(relations, dict):
        return list(relations.items())
    return [
        ((item.get("source_node_id"), item.get("relation_code"), item.get("target_node_id")), item)
        for item in relations
    ]


def snapshot_hashes(graph: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    return {
        "nodes": {str(node.get("id")): node_content_hash(node) for node in _node_items(graph)},
        "relations": {
            "|".join(str(part) for part in key): relation_content_hash(item)
            for key, item in _relation_items(graph)
        },
    }


def ensure_metadata(graph: Dict[str, Any], now: Optional[str] = None) -> None:
    now = now or _now()
    for node in _node_items(graph):
        node.setdefault("review_status", "draft")
        node.setdefault("review_note", "")
        node.setdefault("reviewed_by", "")
        node.setdefault("reviewed_at", "")
        node.setdefault("version", 1)
        node["content_hash"] = node_content_hash(node)
        node.setdefault("source_doc_ids", [])
    for key, relation in _relation_items(graph):
        relation.setdefault("review_status", "draft")
        relation.setdefault("review_note", "")
        relation.setdefault("reviewed_by", "")
        relation.setdefault("reviewed_at", "")
        relation.setdefault("version", 1)
        relation["content_hash"] = relation_content_hash(relation)
        relation.setdefault("source_doc_ids", [])


def apply_import_review_metadata(
    graph: Dict[str, Any],
    previous_hashes: Dict[str, Dict[str, str]],
    doc_id: str,
    now: Optional[str] = None,
) -> None:
    now = now or _now()
    for node in _node_items(graph):
        node_id = str(node.get("id"))
        old = previous_hashes.get("nodes", {}).get(node_id)
        new = node_content_hash(node)
        current = str(node.get("review_status") or "draft")
        if old is not None and old != new and current == "active":
            node["review_status"] = "stale"
        if old is None:
            node["version"] = int(node.get("version") or 1)
        elif old != new:
            node["version"] = int(node.get("version") or 1) + 1
        node["content_hash"] = new
        if old is None or old != new:
            node["last_imported_at"] = now
            docs = set(node.get("source_doc_ids") or [])
            docs.add(doc_id)
            node["source_doc_ids"] = sorted(docs)
        node.setdefault("review_status", "draft")
    for key, relation in _relation_items(graph):
        key_text = "|".join(str(part) for part in key)
        old = previous_hashes.get("relations", {}).get(key_text)
        new = relation_content_hash(relation)
        current = str(relation.get("review_status") or "draft")
        if old is not None and old != new and current == "active":
            relation["review_status"] = "stale"
        if old is None:
            relation["version"] = int(relation.get("version") or 1)
        elif old != new:
            relation["version"] = int(relation.get("version") or 1) + 1
        relation["content_hash"] = new
        if old is None or old != new:
            relation["last_imported_at"] = now
            docs = set(relation.get("source_doc_ids") or [])
            docs.add(doc_id)
            relation["source_doc_ids"] = sorted(docs)
        relation.setdefault("review_status", "draft")
```

- [ ] **Step 4: 接入 import_graph**

在 `src/api/routes/kg.py` 的 `import_graph` 中：

1. 在 `graph = _load_graph(graph_filename)` 之后：

```python
    from src.review.metadata import apply_import_review_metadata, snapshot_hashes
    previous_hashes = snapshot_hashes(graph)
```

2. 两处 `_save_graph(graph, graph_filename)` 之前（TCM 分支和西方分支）：

```python
    apply_import_review_metadata(graph, previous_hashes, request.doc_id)
```

3. 两处 `_save_graph(...)` 之后：

```python
    try:
        from src.review.service import sync_blocked_rules_for_graph
        sync_blocked_rules_for_graph(graph_filename)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("同步规则阻断状态失败", exc_info=True)
```

- [ ] **Step 5: 实现迁移脚本**

创建 `tools/migrate_review_status.py`：

```python
#!/usr/bin/env python3
"""Bootstrap review metadata and approve dependencies of active rules once."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from src.api.routes.kg import KG_DIR, _load_graph, _save_graph, normalize_graph_filename
from src.api.routes.rules import MANAGED_RULES_PATH, _load_all_rules, _save_all_rules, check_rule_sync
from src.review.audit import AUDIT_PATH, append_audit
from src.review.metadata import ensure_metadata


def _node_ref_status(graph: Dict[str, Any], node_id: str) -> str:
    for node in graph.get("nodes", []):
        if node.get("id") == node_id:
            return str(node.get("review_status") or "draft")
    return "missing"


def _set_node_status(graph: Dict[str, Any], node_id: str, status: str) -> bool:
    for node in graph.get("nodes", []):
        if node.get("id") == node_id:
            if str(node.get("review_status") or "draft") != status:
                node["review_status"] = status
                return True
            return False
    return False


def _set_relation_status(graph: Dict[str, Any], key, status: str) -> bool:
    for relation in graph.get("relations", []):
        current = (
            relation.get("source_node_id"),
            relation.get("relation_code"),
            relation.get("target_node_id"),
        )
        if current == key:
            if str(relation.get("review_status") or "draft") != status:
                relation["review_status"] = status
                return True
            return False
    return False


def migrate(graph_dir=KG_DIR, rules_path=MANAGED_RULES_PATH, audit_path=AUDIT_PATH, dry_run=False):
    graph_dir = Path(graph_dir)
    rules_path = Path(rules_path)
    rules = _load_all_rules(rules_path)
    stats = {"graphs": 0, "bootstrapped_nodes": 0, "bootstrapped_relations": 0, "blocked_rules": 0}
    for graph_path in sorted(graph_dir.glob("*.json")):
        filename = normalize_graph_filename(graph_path.name)
        graph = _load_graph(filename, kg_dir=graph_dir)
        ensure_metadata(graph)
        graph_rules = [
            rule for rule in rules
            if normalize_graph_filename(rule.get("graph_filename") or "stemi_graph.json") == filename
        ]
        sync = check_rule_sync(graph_rules, graph_loader=lambda name: _load_graph(name, kg_dir=graph_dir))
        for result in sync["rules"]:
            if result.get("semantic_status") != "consistent":
                continue
            if result.get("dependency_status") == "passed":
                continue
            for ref in result.get("references") or []:
                if ref.get("status") == "active":
                    continue
                if ref.get("kind") == "node":
                    if _set_node_status(graph, ref["node_id"], "active"):
                        stats["bootstrapped_nodes"] += 1
                        if not dry_run:
                            append_audit({
                                "action": "migration_bootstrap",
                                "entity_type": "node",
                                "entity_id": ref["node_id"],
                                "graph_filename": filename,
                                "before_status": ref.get("status"),
                                "after_status": "active",
                                "reviewer": "system",
                                "note": "迁移：保持现有已启用规则的依赖可用",
                            }, audit_path)
                else:
                    key = (ref["source_node_id"], ref["relation_code"], ref["target_node_id"])
                    if _set_relation_status(graph, key, "active"):
                        stats["bootstrapped_relations"] += 1
                        if not dry_run:
                            append_audit({
                                "action": "migration_bootstrap",
                                "entity_type": "relation",
                                "entity_id": "|".join(key),
                                "graph_filename": filename,
                                "before_status": ref.get("status"),
                                "after_status": "active",
                                "reviewer": "system",
                                "note": "迁移：保持现有已启用规则的依赖可用",
                            }, audit_path)
        if not dry_run:
            _save_graph(graph, filename, kg_dir=graph_dir)
        stats["graphs"] += 1

    if not dry_run:
        for rule in rules:
            if rule.get("status") != "active":
                continue
            filename = normalize_graph_filename(rule.get("graph_filename") or "stemi_graph.json")
            result = check_rule_sync([rule], graph_loader=lambda name: _load_graph(name, kg_dir=graph_dir))["rules"][0]
            if result.get("dependency_status") != "passed":
                rule["status"] = "blocked"
                rule["blocked_by"] = [
                    {"entity_type": ref.get("kind"), "entity_id": ref.get("node_id") or "|".join(filter(None, [ref.get("source_node_id"), ref.get("relation_code"), ref.get("target_node_id")])), "reason": ref.get("status")}
                    for ref in result.get("references") or [] if ref.get("status") != "active"
                ]
                stats["blocked_rules"] += 1
        _save_all_rules(rules, rules_path)
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-dir", default=str(KG_DIR))
    parser.add_argument("--rules-path", default=str(MANAGED_RULES_PATH))
    parser.add_argument("--audit-path", default=str(AUDIT_PATH))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.graph_dir, args.rules_path, args.audit_path, args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: 更新 .gitignore**

追加：

```
data/review/
```

- [ ] **Step 7: 运行测试，确认通过**

Run: `python3 -m unittest tests.test_review_metadata -v`  
Expected: PASS（3 tests）

- [ ] **Step 8: 本地提交**

```bash
git add src/review/metadata.py src/api/routes/kg.py tools/migrate_review_status.py tests/test_review_metadata.py .gitignore
git commit -m "feat: track review metadata on reimport and add migration"
```

---

### Task 5: 图谱审核 API

**Files:**
- Create: `src/api/routes/kg_review.py`
- Modify: `src/api/main.py:11-33`
- Test: `tests/test_review_api.py`

**Interfaces:**
- Consumes: `ReviewService`, `ReviewError`
- Produces HTTP:
  - `GET /api/kg/review/summary`
  - `GET /api/kg/review/items`
  - `GET /api/kg/nodes/{node_id}`
  - `POST /api/kg/nodes/{node_id}/review`
  - `POST /api/kg/relations/review`
  - `POST /api/kg/review/bulk`
  - `GET /api/kg/nodes/{node_id}/dependents`
  - `GET /api/kg/nodes/{node_id}/impact`
  - `GET /api/kg/relations/impact`
  - `GET /api/review/audit`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_review_api.py`：

```python
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

import src.api.routes.kg_review as kg_review
from src.api.main import app
from src.review.service import ReviewService


def run(coro):
    return asyncio.run(coro)


class ReviewApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        graph_dir = root / "kg"
        graph_dir.mkdir()
        rules_path = root / "rules" / "managed_rules.json"
        rules_path.parent.mkdir()
        audit_path = root / "review" / "review_log.jsonl"
        (graph_dir / "API-TEST.json").write_text(json.dumps({
            "schema_version": 1, "domain": "western", "anchor_types": ["disease"],
            "diseases": [{"code": "I21.0", "name_cn": "测试病"}],
            "evidences": [], "disease_evidence": [], "sources": [],
            "nodes": [
                {"id": "ENTITY:disease:I21.0", "node_type": "disease", "code": "I21.0", "name": "测试病", "review_status": "active"},
                {"id": "ENTITY:symptom:SYM_DRAFT", "node_type": "symptom", "code": "SYM_DRAFT", "name": "待审症状", "review_status": "draft"},
            ],
            "relations": [],
        }, ensure_ascii=False), encoding="utf-8")
        rules_path.write_text("[]", encoding="utf-8")
        self.service = ReviewService(graph_dir, rules_path, audit_path)
        self.original = kg_review.service
        kg_review.service = self.service
        self.addCleanup(setattr, kg_review, "service", self.original)

    def request(self, method, path, **kwargs):
        async def go():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, path, **kwargs)
        return run(go())

    def test_summary(self):
        response = self.request("GET", "/api/kg/review/summary?graph_filename=API-TEST.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["nodes"]["draft"], 1)

    def test_approve_node(self):
        response = self.request(
            "POST", "/api/kg/nodes/ENTITY:symptom:SYM_DRAFT/review",
            json={"action": "approve", "reviewer": "张医生", "note": "确认"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "active")

    def test_invalid_transition_returns_422(self):
        response = self.request(
            "POST", "/api/kg/nodes/ENTITY:disease:I21.0/review",
            json={"action": "approve", "reviewer": "张医生"},
        )
        self.assertEqual(response.status_code, 422)

    def test_audit_endpoint(self):
        self.request(
            "POST", "/api/kg/nodes/ENTITY:symptom:SYM_DRAFT/review",
            json={"action": "approve", "reviewer": "张医生"},
        )
        response = self.request("GET", "/api/review/audit?entity_id=ENTITY:symptom:SYM_DRAFT")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["items"]), 1)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python3 -m unittest tests.test_review_api -v`  
Expected: FAIL，`ModuleNotFoundError: src.api.routes.kg_review`

- [ ] **Step 3: 实现路由**

创建 `src/api/routes/kg_review.py`：

```python
"""Knowledge graph review API."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.review.audit import AUDIT_PATH, load_audit
from src.review.service import ReviewService

router = APIRouter(prefix="/api/kg", tags=["kg-review"])
audit_router = APIRouter(prefix="/api/review", tags=["review-audit"])
service = ReviewService()


class ReviewActionRequest(BaseModel):
    action: str
    reviewer: str = "未署名"
    note: str = ""


class RelationReviewRequest(ReviewActionRequest):
    source_node_id: str
    relation_code: str
    target_node_id: str


class BulkReviewRequest(ReviewActionRequest):
    graph_filename: str
    items: List[dict] = Field(default_factory=list)


@router.get("/review/summary")
async def review_summary(graph_filename: str):
    return service.summary(graph_filename)


@router.get("/review/items")
async def review_items(
    graph_filename: str,
    status: Optional[str] = None,
    item_type: Optional[str] = None,
    node_type: Optional[str] = None,
    source_doc: Optional[str] = None,
    referenced_by_rule: Optional[bool] = None,
    q: Optional[str] = None,
):
    return service.list_items(
        graph_filename, status=status, item_type=item_type,
        node_type=node_type, source_doc=source_doc,
        referenced_by_rule=referenced_by_rule, q=q,
    )


@router.get("/nodes/{node_id}")
async def node_detail(node_id: str, graph_filename: str):
    return service.node_detail(graph_filename, node_id)


@router.post("/nodes/{node_id}/review")
async def review_node(node_id: str, request: ReviewActionRequest, graph_filename: str):
    return service.review_node(
        graph_filename, node_id, request.action, request.reviewer, request.note
    )


@router.post("/relations/review")
async def review_relation(request: RelationReviewRequest, graph_filename: str):
    return service.review_relation(
        graph_filename, request.source_node_id, request.relation_code,
        request.target_node_id, request.action, request.reviewer, request.note,
    )


@router.post("/review/bulk")
async def review_bulk(request: BulkReviewRequest):
    return service.bulk_review(
        request.graph_filename, request.items, request.action,
        request.reviewer, request.note,
    )


@router.get("/nodes/{node_id}/dependents")
async def node_dependents(node_id: str, graph_filename: str):
    return service.node_dependents(graph_filename, node_id)


@router.get("/nodes/{node_id}/impact")
async def node_impact(node_id: str, graph_filename: str):
    return service.impact_for_node(graph_filename, node_id)


@router.get("/relations/impact")
async def relation_impact(
    graph_filename: str,
    source_node_id: str,
    relation_code: str,
    target_node_id: str,
):
    return service.impact_for_relation(
        graph_filename, source_node_id, relation_code, target_node_id
    )


@audit_router.get("/audit")
async def review_audit(
    entity_id: Optional[str] = None,
    graph_filename: Optional[str] = None,
    limit: int = 200,
):
    items = load_audit(
        path=service.audit_path, entity_id=entity_id or "",
        graph_filename=graph_filename or "", limit=limit,
    )
    return {"items": items}
```

- [ ] **Step 4: 注册路由与异常处理器**

修改 `src/api/main.py`：

```python
from fastapi.responses import JSONResponse
from src.review.errors import ReviewError
from .routes import (..., kg_review, ...)

app.include_router(kg_review.router)
app.include_router(kg_review.audit_router)


@app.exception_handler(ReviewError)
async def review_error_handler(request, exc: ReviewError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `python3 -m unittest tests.test_review_api -v`  
Expected: PASS（4 tests）

- [ ] **Step 6: 本地提交**

```bash
git add src/api/routes/kg_review.py src/api/main.py tests/test_review_api.py
git commit -m "feat: expose graph review API"
```

---

### Task 6: 规则审核门禁与接口

**Files:**
- Modify: `src/api/routes/rules.py:428-660`
- Test: `tests/test_rule_review_gate.py`

**Interfaces:**
- Consumes: `next_rule_status`, `StatusTransitionError`, `append_audit`
- Produces:
  - `POST /api/rules/{rule_id}/review`
  - `GET /api/rules/{rule_id}/graph-dependencies`
  - `GET /api/rules` 每条规则增加 `dependency_summary`
  - `activate_rule` / `activate_consistent` 依赖门禁

- [ ] **Step 1: 写失败测试**

创建 `tests/test_rule_review_gate.py`：

```python
import json
import tempfile
import unittest
from pathlib import Path

from src.api.routes import rules as rules_module


class RuleGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.graph = {
            "domain": "western",
            "diseases": [{"code": "I21.0", "name_cn": "测试病"}],
            "nodes": [
                {"id": "ENTITY:disease:I21.0", "node_type": "disease", "code": "I21.0", "name": "测试病", "review_status": "active"},
                {"id": "ENTITY:symptom:SYM_TEST", "node_type": "symptom", "code": "SYM_TEST", "name": "测试症状", "review_status": "draft"},
            ],
            "relations": [
                {"source_node_id": "ENTITY:disease:I21.0", "relation_code": "DISEASE_HAS_SYMPTOM", "target_node_id": "ENTITY:symptom:SYM_TEST", "review_status": "draft"},
            ],
            "evidences": [], "disease_evidence": [],
        }
        self.rule = {
            "rule_id": "R1", "graph_filename": "GATE.json", "disease_code": "I21.0",
            "rule_type": "diagnosis", "status": "draft",
            "conditions": {"type": "group", "operator": "AND", "children": [{"type": "symptom", "symptom_code": "SYM_TEST"}]},
            "conclusion": {"diagnosis": "I21.0"},
        }

    def test_dependency_gate_blocks_draft_symptom(self):
        result = rules_module.check_rule_sync([self.rule], graph_loader=lambda name: self.graph)["rules"][0]
        self.assertEqual(result["dependency_status"], "blocked")
        blockers = rules_module._activation_blockers(result)
        self.assertTrue(blockers)

    def test_dependency_gate_passes_when_all_active(self):
        for node in self.graph["nodes"]:
            node["review_status"] = "active"
        self.graph["relations"][0]["review_status"] = "active"
        result = rules_module.check_rule_sync([self.rule], graph_loader=lambda name: self.graph)["rules"][0]
        self.assertEqual(result["dependency_status"], "passed")
        self.assertEqual(rules_module._activation_blockers(result), [])
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python3 -m unittest tests.test_rule_review_gate -v`  
Expected: FAIL，`_activation_blockers` 不存在

- [ ] **Step 3: 实现门禁与接口**

在 `src/api/routes/rules.py` 顶部增加：

```python
from datetime import datetime, timezone
from src.review.audit import AUDIT_PATH as REVIEW_AUDIT_PATH, append_audit
from src.review.status import StatusTransitionError, next_rule_status
```

新增：

```python
class RuleReviewRequest(BaseModel):
    action: str
    reviewer: str = "未署名"
    note: str = ""


def _activation_blockers(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        ref for ref in result.get("references") or []
        if ref.get("status") != "active"
    ]


def _dependency_result(rule: Dict[str, Any], graph_loader=None) -> Dict[str, Any]:
    return check_rule_sync([rule], graph_loader=graph_loader)["rules"][0]
```

修改 `activate_rule`：

```python
@router.post("/{rule_id}/activate")
async def activate_rule(rule_id: str):
    rules = _load_all_rules()
    for rule in rules:
        if rule.get("rule_id") == rule_id:
            result = _dependency_result(rule)
            blockers = _activation_blockers(result)
            if blockers:
                raise HTTPException(422, {
                    "error": "规则依赖未通过审核",
                    "dependencies": blockers,
                })
            rule["status"] = "active"
            rule["updated_at"] = datetime.now(timezone.utc).isoformat()
            _save_all_rules(rules)
            return rule
    raise HTTPException(404, "规则未找到")
```

修改 `activate_consistent` 候选状态：

```python
        if rule.get("status") not in {"draft", "testing", "blocked"}:
            continue
```

并在 `result.get("semantic_status") != "consistent"` 判断之后增加：

```python
            if result.get("dependency_status") != "passed":
                blockers = _activation_blockers(result)
                skipped.append({
                    "rule_id": rule.get("rule_id"),
                    "reason": "依赖未通过审核: " + "; ".join(
                        f"{item.get('node_id') or item.get('relation_code')}={item.get('status')}"
                        for item in blockers
                    ),
                })
                continue
```

新增接口：

```python
@router.get("/{rule_id}/graph-dependencies")
async def graph_dependencies(rule_id: str):
    for rule in _load_all_rules():
        if rule.get("rule_id") == rule_id:
            return _dependency_result(rule)
    raise HTTPException(404, "规则未找到")


@router.post("/{rule_id}/review")
async def review_rule(rule_id: str, request: RuleReviewRequest):
    rules = _load_all_rules()
    for rule in rules:
        if rule.get("rule_id") != rule_id:
            continue
        current = str(rule.get("status") or "draft")
        try:
            target = next_rule_status(current, request.action)
        except StatusTransitionError as exc:
            raise HTTPException(422, str(exc)) from exc
        if request.action == "approve":
            result = _dependency_result(rule)
            blockers = _activation_blockers(result)
            if blockers:
                raise HTTPException(422, {
                    "error": "规则依赖未通过审核",
                    "dependencies": blockers,
                })
        rule["status"] = target
        rule["reviewed_by"] = request.reviewer or "未署名"
        rule["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        rule["review_note"] = request.note
        rule["updated_at"] = rule["reviewed_at"]
        _save_all_rules(rules)
        append_audit({
            "action": request.action,
            "entity_type": "rule",
            "entity_id": rule_id,
            "graph_filename": rule.get("graph_filename", ""),
            "before_status": current,
            "after_status": target,
            "reviewer": request.reviewer or "未署名",
            "note": request.note,
            "affected_rules": [],
        }, REVIEW_AUDIT_PATH)
        return rule
    raise HTTPException(404, "规则未找到")
```

修改 `list_rules`，在过滤后附加依赖摘要：

```python
    sync = check_rule_sync(rules)
    by_id = {item["rule_id"]: item for item in sync["rules"]}
    for rule in rules:
        result = by_id.get(rule.get("rule_id"), {})
        refs = result.get("references") or []
        statuses = [ref.get("status") for ref in refs]
        rule["dependency_summary"] = {
            "total": len(refs),
            "active": sum(status == "active" for status in statuses),
            "draft": sum(status == "draft" for status in statuses),
            "stale": sum(status == "stale" for status in statuses),
            "rejected": sum(status == "rejected" for status in statuses),
            "retired": sum(status == "retired" for status in statuses),
            "missing": sum(status == "missing" for status in statuses),
            "blocked": result.get("dependency_status") != "passed",
        }
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python3 -m unittest tests.test_rule_review_gate tests.test_rule_dependencies -v`  
Expected: PASS（6 tests）

- [ ] **Step 5: 本地提交**

```bash
git add src/api/routes/rules.py tests/test_rule_review_gate.py
git commit -m "feat: gate rule activation on graph review status"
```

---

### Task 7: 图谱页审核 UI

**Files:**
- Modify: `src/api/static/app.js`（`renderGraph`、`api` 错误消息）
- Modify: `src/api/static/style.css`
- Modify: `src/api/static/index.html`（版本号）

**Interfaces:**
- Consumes: Task 5 的 API
- Produces：图谱页状态筛选、审核动作、节点详情、审核人输入

- [ ] **Step 1: 增加审核状态标签和错误消息**

在 `app.js` 的 `badge` 附近增加：

```js
  const REVIEW_LABELS = {
    draft: "待审核", active: "已通过", rejected: "已拒绝",
    retired: "已失效", stale: "待复核", blocked: "已阻断", testing: "测试中",
  };
  const reviewBadge = (value) => {
    const status = value || "draft";
    return `<span class="badge ${esc(status)}">${esc(REVIEW_LABELS[status] || status)}</span>`;
  };
```

修改 `api()` 的错误提取：

```js
    if (!response.ok) {
      const detail = data && data.detail;
      const message = typeof detail === "string"
        ? detail
        : detail ? JSON.stringify(detail)
        : (data && data.message) || `请求失败 ${response.status}`;
      throw new Error(message);
    }
```

- [ ] **Step 2: 修改图谱页表格**

在 `renderGraph` 中：

- `relationItems` 改为 `semanticRelations.map((relation, index) => ...)`，`values` 增加 `review_status`，HTML 增加状态列与操作列：

```js
      const relationStatus = relation.review_status || "draft";
      const relationActions = [
        (relationStatus === "draft" || relationStatus === "stale")
          ? `<button class="btn sm primary" data-review-relation="${index}" data-review-action="approve">通过</button>
             <button class="btn sm danger" data-review-relation="${index}" data-review-action="reject">拒绝</button>`
          : "",
        relationStatus === "active"
          ? `<button class="btn sm" data-review-relation="${index}" data-review-action="retire">失效</button>`
          : "",
        (relationStatus === "rejected" || relationStatus === "retired")
          ? `<button class="btn sm" data-review-relation="${index}" data-review-action="restore">恢复</button>`
          : "",
      ].join(" ");
```

并把关系行 HTML 改为包含：

```html
<td>${reviewBadge(relationStatus)}</td>
<td><div class="actions">${relationActions}</div></td>
```

表头增加 `"审核状态", "操作"`，`filters` 增加：

```js
        filters: [
          { key: "relation_code", label: "关系", options: relationOptions },
          { key: "review_status", label: "审核状态", options: [
            { value: "draft", label: "待审核" },
            { value: "active", label: "已通过" },
            { value: "rejected", label: "已拒绝" },
            { value: "retired", label: "已失效" },
            { value: "stale", label: "待复核" },
          ] },
        ],
```

- `nodeItems` 增加 `review_status` 值和操作列，逻辑同上：

```js
      const nodeStatus = node.review_status || "draft";
      const nodeActions = [
        (nodeStatus === "draft" || nodeStatus === "stale")
          ? `<button class="btn sm primary" data-review-node="${esc(node.id)}" data-review-action="approve">通过</button>
             <button class="btn sm danger" data-review-node="${esc(node.id)}" data-review-action="reject">拒绝</button>`
          : "",
        nodeStatus === "active"
          ? `<button class="btn sm" data-review-node="${esc(node.id)}" data-review-action="retire">失效</button>`
          : "",
        (nodeStatus === "rejected" || nodeStatus === "retired")
          ? `<button class="btn sm" data-review-node="${esc(node.id)}" data-review-action="restore">恢复</button>`
          : "",
        `<button class="btn sm ghost" data-node-detail="${esc(node.id)}">详情</button>`,
      ].join(" ");
```

表头增加 `"审核状态", "操作"`，`filters` 增加 `review_status`。

- 在「图谱选择」面板增加审核人输入：

```html
<div class="field"><label>审核人</label><input id="reviewerName" value="${esc(localStorage.getItem("cdssReviewer") || "")}" placeholder="姓名"></div>
```

- 在 `renderGraph` 末尾绑定：

```js
    $("#reviewerName").onchange = (event) => {
      localStorage.setItem("cdssReviewer", event.target.value.trim());
    };

    const currentReviewer = () => {
      const value = ($("#reviewerName")?.value || "").trim();
      if (value) localStorage.setItem("cdssReviewer", value);
      return value || "未署名";
    };

    const submitNodeReview = async (nodeId, action) => {
      if (action === "reject" || action === "retire") {
        const impact = await api(`/api/kg/nodes/${encodeURIComponent(nodeId)}/impact?graph_filename=${encodeURIComponent(state.graphFilename)}`);
        const message = `该操作将影响 ${impact.incident_relations} 条关系、${impact.rules.total} 条规则（已启用 ${impact.rules.active} 条）。确认继续？`;
        if (!confirm(message)) return;
      }
      const note = prompt("审核意见（可选）") || "";
      await api(`/api/kg/nodes/${encodeURIComponent(nodeId)}/review?graph_filename=${encodeURIComponent(state.graphFilename)}`, {
        json: { action, reviewer: currentReviewer(), note },
      });
      toast("节点审核状态已更新");
      renderGraph();
    };

    const submitRelationReview = async (index, action) => {
      const relation = semanticRelations[index];
      if (action === "reject" || action === "retire") {
        const params = new URLSearchParams({
          graph_filename: state.graphFilename,
          source_node_id: relation.source_node_id,
          relation_code: relation.relation_code,
          target_node_id: relation.target_node_id,
        });
        const impact = await api(`/api/kg/relations/impact?${params}`);
        if (!confirm(`该操作将影响 ${impact.rules.total} 条规则（已启用 ${impact.rules.active} 条）。确认继续？`)) return;
      }
      const note = prompt("审核意见（可选）") || "";
      await api(`/api/kg/relations/review?graph_filename=${encodeURIComponent(state.graphFilename)}`, {
        json: {
          action, reviewer: currentReviewer(), note,
          source_node_id: relation.source_node_id,
          relation_code: relation.relation_code,
          target_node_id: relation.target_node_id,
        },
      });
      toast("关系审核状态已更新");
      renderGraph();
    };

    const openNodeDetail = async (nodeId) => {
      const detail = await api(`/api/kg/nodes/${encodeURIComponent(nodeId)}?graph_filename=${encodeURIComponent(state.graphFilename)}`);
      const rules = detail.dependents?.rules || [];
      $("#editorBody").innerHTML = `
        <h3>${esc(detail.node.name)} <span class="mono muted">${esc(detail.node.code || detail.node.id)}</span></h3>
        <p>类型：${esc(detail.node.node_type)}　状态：${reviewBadge(detail.node.review_status)}</p>
        <h4>指南溯源</h4>
        <div class="quote">${esc((detail.node.source_refs || []).join("；") || "-")}</div>
        <h4>依赖规则（${rules.length}）</h4>
        ${table(["规则", "状态", "依赖状态"], rules.map((rule) => `<tr><td class="mono">${esc(rule.rule_id)}</td><td>${reviewBadge(rule.status)}</td><td>${esc(rule.dependency_status || "-")}</td></tr>`))}
      `;
      const dialog = $("#editorDialog");
      const save = $("#editorSave");
      save.hidden = true;
      dialog.onclose = () => { save.hidden = false; dialog.onclose = null; };
      $("#editorCancel").onclick = () => dialog.close();
      dialog.showModal();
    };

    content.onclick = async (event) => {
      const nodeButton = event.target.closest("[data-review-node]");
      if (nodeButton) {
        try { await submitNodeReview(nodeButton.dataset.reviewNode, nodeButton.dataset.reviewAction); }
        catch (error) { toast(error.message, true); }
        return;
      }
      const relationButton = event.target.closest("[data-review-relation]");
      if (relationButton) {
        try { await submitRelationReview(Number(relationButton.dataset.reviewRelation), relationButton.dataset.reviewAction); }
        catch (error) { toast(error.message, true); }
        return;
      }
      const detailButton = event.target.closest("[data-node-detail]");
      if (detailButton) {
        try { await openNodeDetail(detailButton.dataset.nodeDetail); }
        catch (error) { toast(error.message, true); }
      }
    };
```

- [ ] **Step 3: 增加 CSS**

在 `style.css` 追加：

```css
.badge.rejected, .badge.blocked { background: var(--danger-soft); color: var(--danger); }
.badge.stale { background: #fff4e5; color: #b25e09; }
```

- [ ] **Step 4: 更新静态资源版本**

`index.html` 中把 `app.js?v=...` 改为：

```html
<script src="/static/app.js?v=20261109review"></script>
```

- [ ] **Step 5: 浏览器验证**

启动服务：

```bash
PYTHONPATH=. python3 -B -m uvicorn src.api.main:app --host 127.0.0.1 --port 8001
```

用 Chrome/Playwright 打开知识图谱页，验证：

- 节点/关系表出现「审核状态」和操作按钮。
- 对 `draft` 节点点击「通过」，状态变为「已通过」。
- 对 `active` 节点点击「失效」，确认影响面后状态变为「已失效」，依赖规则变为「已阻断」（若无依赖则不报错）。
- 点击「详情」显示溯源和依赖规则。

- [ ] **Step 6: 本地提交**

```bash
git add src/api/static/app.js src/api/static/style.css src/api/static/index.html
git commit -m "feat: add graph review actions to UI"
```

---

### Task 8: 规则页依赖展示与审核反馈

**Files:**
- Modify: `src/api/static/app.js`（`renderRules`）
- Modify: `src/api/static/style.css`（如 Task 7 未加）
- Modify: `src/api/static/index.html`（版本号）

**Interfaces:**
- Consumes: `GET /api/rules` 的 `dependency_summary`、`GET /api/rules/{rule_id}/graph-dependencies`
- Produces：规则页依赖列、阻断状态、依赖详情弹窗

- [ ] **Step 1: 修改规则表格**

在 `renderRules` 中，把状态列改为 `reviewBadge(r.status)`，并在「图谱语义」列后增加「图谱依赖」列：

```js
      const dependency = r.dependency_summary || {};
      const dependencyTone = dependency.blocked ? "danger" : (dependency.total && dependency.active === dependency.total ? "active" : "draft");
      const dependencyLabel = dependency.blocked
        ? `${dependency.total - dependency.active} 项未通过`
        : `${dependency.active || 0}/${dependency.total || 0} 已审核`;
      const dependencyCell = dependency.total
        ? `<button class="btn sm ${dependencyTone}" data-rule-deps="${esc(r.rule_id)}">${dependencyLabel}</button>`
        : '<span class="muted">无依赖</span>';
```

表头与行插入该列；行 HTML 中状态用 `reviewBadge(r.status)`。

- [ ] **Step 2: 绑定依赖详情**

在 `renderRules` 末尾：

```js
    content.querySelectorAll("[data-rule-deps]").forEach((button) => {
      button.onclick = async () => {
        try {
          const result = await api(`/api/rules/${encodeURIComponent(button.dataset.ruleDeps)}/graph-dependencies`);
          const refs = result.references || [];
          $("#editorBody").innerHTML = `
            <h3>图谱依赖 <span class="mono muted">${esc(result.rule_id)}</span></h3>
            <p>依赖状态：${result.dependency_status === "passed" ? reviewBadge("active") : reviewBadge("blocked")}</p>
            ${table(["类型", "条目", "状态"], refs.map((ref) => `<tr>
              <td>${ref.kind === "node" ? "节点" : "关系"}</td>
              <td class="mono">${esc(ref.node_id || `${ref.source_node_id} -${ref.relation_code}-> ${ref.target_node_id}`)}</td>
              <td>${reviewBadge(ref.status === "active" ? "active" : "draft")} <span class="muted">${esc(ref.status)}</span></td>
            </tr>`))}
            ${(result.review_issues || []).length ? `<div class="result-banner warning">${result.review_issues.map((issue) => `<div>${esc(issue)}</div>`).join("")}</div>` : ""}
          `;
          const dialog = $("#editorDialog");
          const save = $("#editorSave");
          save.hidden = true;
          dialog.onclose = () => { save.hidden = false; dialog.onclose = null; };
          $("#editorCancel").onclick = () => dialog.close();
          dialog.showModal();
        } catch (error) { toast(error.message, true); }
      };
    });
```

- [ ] **Step 3: 启用失败提示**

规则启用按钮已有 `try/catch` 调用 `api`。确认 `api()` 会把结构化 `detail` 转成字符串；若后端返回 `{error, dependencies}`，toast 会显示 JSON。可接受；P1 再做更友好的逐项展示。

- [ ] **Step 4: 浏览器验证**

- 规则页出现「图谱依赖」列。
- 依赖未通过的规则显示「x 项未通过」。
- 点击依赖列显示节点/关系清单和状态。
- 对依赖未通过的规则点击「启用」，toast 显示具体阻断项，规则保持 `draft`。

- [ ] **Step 5: 本地提交**

```bash
git add src/api/static/app.js src/api/static/style.css src/api/static/index.html
git commit -m "feat: show rule graph dependencies in UI"
```

---

### Task 9: 迁移、端到端验证与文档

**Files:**
- Modify: `README.md`
- Modify: `.gitignore`（若 Task 4 未加）
- Test: 手动浏览器回归 + 单元测试全量

- [ ] **Step 1: 备份当前数据**

```bash
STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p "data/_review_backup_$STAMP"
cp -R data/kg data/rules "data/_review_backup_$STAMP"/
echo "$STAMP"
```

- [ ] **Step 2: 运行迁移脚本（先 dry-run）**

```bash
PYTHONPATH=. python3 tools/migrate_review_status.py --dry-run
```

确认输出中的 `bootstrapped_nodes` / `bootstrapped_relations` / `blocked_rules` 合理后执行：

```bash
PYTHONPATH=. python3 tools/migrate_review_status.py
```

- [ ] **Step 3: 全量测试**

```bash
python3 -m unittest tests.test_review_status tests.test_rule_dependencies tests.test_graph_review_service tests.test_review_metadata tests.test_review_api tests.test_rule_review_gate -v
python3 -m unittest discover -s tests -t . 2>&1 | tail -5
```

Expected: 新增测试全部通过；`unittest discover` 的已知 6 个 `TestClient` 错误是本地 starlette/httpx 版本问题，与本次改动无关。

- [ ] **Step 4: 浏览器全流程验证**

1. 启动 `./start-api.sh`。
2. 「知识图谱」页：筛选待审核节点，通过一个节点，确认状态徽章变为「已通过」。
3. 对已通过节点执行「失效」，确认影响面弹窗；确认后依赖规则在规则页显示「已阻断」。
4. 「规则管理」页：查看依赖列，点击依赖详情，确认节点/关系状态。
5. 对 `blocked` 规则重新审核（依赖恢复后）或保持阻断，确认不会自动恢复。
6. 「审核记录」可通过 `GET /api/review/audit` 查询到上述动作。

- [ ] **Step 5: 更新 README**

在 README 增加「知识审核」章节：

```markdown
## 知识审核

指南导入后，图谱节点/关系和规则默认都是草稿，需要逐条审核：

- 知识包：批量准入并导入为草稿，不自动启用。
- 知识图谱：节点/关系可按 `draft / active / rejected / retired / stale` 审核。
- 规则：启用前要求所有图谱依赖为 `active`；依赖失效时规则自动 `blocked`，不会自动恢复。
- 审核记录：`data/review/review_log.jsonl`（运行时数据）。

首次启用审核工作流时执行迁移，为现有已启用规则 bootstrap 依赖：

```bash
PYTHONPATH=. python3 tools/migrate_review_status.py --dry-run
PYTHONPATH=. python3 tools/migrate_review_status.py
```

完整设计见 [docs/review-workflow-design.md](docs/review-workflow-design.md)。
```

- [ ] **Step 6: 最终检查**

```bash
git status --short
python3 -m unittest tests.test_review_status tests.test_rule_dependencies tests.test_graph_review_service tests.test_review_metadata tests.test_review_api tests.test_rule_review_gate -v
```

确认工作区只包含预期文件；不推送远程仓库。

---

## 自检

### Spec 覆盖

| 设计章节 | 对应任务 |
|---|---|
| 4.1 图谱状态 | Task 1、Task 3 |
| 4.2 规则状态与 blocked | Task 1、Task 3、Task 6 |
| 5 总体流程 | Task 3、Task 5、Task 7、Task 8 |
| 7 图谱审核/影响面 | Task 3、Task 5、Task 7 |
| 8 规则依赖/门禁/级联 | Task 2、Task 3、Task 6、Task 8 |
| 9 重新导入与 stale | Task 4 |
| 10 数据模型与审计 | Task 1、Task 3、Task 4 |
| 11 API | Task 5、Task 6 |
| 12 前端 | Task 7、Task 8 |
| 13 迁移 | Task 4、Task 9 |
| 14 错误处理 | Task 1、Task 3、Task 5 |
| 15 测试 | 每个任务的测试步骤 |
| 16 P0 验收 | Task 9 |

### 类型与命名一致性

- 图谱状态：`draft / active / rejected / retired / stale`
- 规则状态：`draft / testing / active / blocked / rejected / retired`
- 依赖结果字段：`references`、`dependency_status`、`review_issues`
- 依赖摘要字段：`dependency_summary`
- 审计记录：`ts / action / entity_type / entity_id / graph_filename / before_status / after_status / reviewer / note / affected_rules`
- API 路径与前端调用一致。

### 已知边界

- P0 不实现统一审核台页面、批量勾选 UI、登录/RBAC、孤儿条目检测；这些在 P1/P2。
- P0 的批量 API 已实现，但前端只提供逐条操作。
- 现有 `TestClient` 版本问题不修复；新 API 测试使用 httpx ASGITransport。
