# 实体治理（同名合并/存量合并工具/关联条目）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 导入时同类型同名实体自动合并；审核台提供「疑似重复」检测与一键合并（关系重挂+规则改写+备份+审计）；节点详情展示关联条目并支持跳转。

**Architecture:** A 改 `_upsert_semantic_node` 的查找逻辑（name 兜底匹配，first-wins 复用）；B 在 kg.py 增加纯函数 `find_duplicate_groups(graph)` 与 `merge_duplicate_nodes(...)`（内存完成全部变换后一次写回，图/规则双备份）；C 扩展 `ReviewService.node_detail` 的 relations 附带邻居摘要，前端渲染分组与跳转。

**Tech Stack:** FastAPI + 现有 JSON 图谱/规则存储 + vanilla JS 单文件前端 + pytest/node:test。

**Spec:** docs/superpowers/specs/2026-11-12-entity-governance-design.md

## Global Constraints

- 合并不得静默丢关系：重挂只换端点 id，语义相同的关系合并且 source_refs 并集，关系总数只减于"完全相同三元组去重"。
- 所有破坏性写盘前先 `.bak-YYYYMMDD-merge` 备份（shutil.copy2）。
- 规则条件改写针对**任意以 `_code` 结尾的字符串字段**（含 `disease_code`），递归条件树；每条被改写的规则先 `record_snapshot(rule, "update", ...)` 再改。
- 名称规范化 = 去首尾空白 + 连续空白折叠单空格（`" ".join(name.split())`）；空名不参与同名匹配。
- 既有测试基线：270 pytest + 39 JS 全绿（HEAD 2858d03）；全套 pytest 会写脏 `data/rules/managed_rules.json`，提交前 `git status` 检查、脏则 `git restore`。
- JS 测试命令：`node --test 'tests/js/*.test.js'`（不要用目录形式）。
- 前端改动提交时同步升 `src/api/static/index.html` 的 `app.js?v=` 缓存版本号。
- 提交信息中文 feat/fix 前缀，直接 main，每任务至少一个 commit。

---

### Task 1: 导入时同名自动合并

**Files:**
- Modify: `src/api/routes/kg.py`（`_upsert_semantic_node` 244-291 行；`_save_graph` 150-162 行；`import_graph` 内两处 `_save_graph(graph, graph_filename)` 调用前）
- Test: `tests/test_same_name_merge.py`（新建）

**Interfaces:**
- Consumes: `_upsert_semantic_node(graph, node_type, code, name="", source_refs=None, attributes=None) -> node dict`（已存在）；`_upsert_semantic_relation(graph, source_node_id, relation_code, target_node_id, relation_names: Dict, source_refs=None, attributes=None)`（已存在）；graph 为 `_load_graph` 归一化后的形态（nodes=dict keyed by id）。新建分支（257-275）与更新分支（276-291）之间的结构：`node = graph["nodes"].get(node_id)` 后 `if node is None:` 新建、`else:` 更新（更新分支 290 行已有 refs 并集）。
- Produces: `_normalize_entity_name(name) -> str`；`_upsert_semantic_node` 行为变更（同名复用 + 计数 `graph["_same_name_merges"]`）。Task 2/3 的 `find_duplicate_groups` 复用 `_normalize_entity_name`。

- [ ] **Step 1: 写失败测试**

```python
"""Import-time same-name merge: 同类型同名不同码 → 复用既有节点。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes.kg import _load_graph, _save_graph, _upsert_semantic_node, _upsert_semantic_relation


def _fresh_graph():
    return {"domain": "western", "schema_version": 1, "nodes": {}, "relations": {},
            "diseases": [], "evidences": [], "disease_evidence": [], "sources": []}


class SameNameMergeTest(unittest.TestCase):
    def test_same_name_different_code_merges_into_existing(self):
        graph = _fresh_graph()
        first = _upsert_semantic_node(graph, "disease", "Q21.0", "室间隔缺损", ["DOC-X_p1_l1-5"])
        second = _upsert_semantic_node(graph, "disease", "CAND-VSD", "室间隔缺损", ["DOC-X_p2_l1-9"])
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(len([n for n in graph["nodes"].values() if n["node_type"] == "disease"]), 1)
        self.assertIn("CAND-VSD", first["attributes"]["alt_codes"])
        self.assertEqual(first["source_refs"], ["DOC-X_p1_l1-5", "DOC-X_p2_l1-9"])

    def test_name_normalization_ignores_whitespace(self):
        graph = _fresh_graph()
        _upsert_semantic_node(graph, "medication", "M1", "地高辛", [])
        merged = _upsert_semantic_node(graph, "medication", "M2", "  地高辛  ", ["DOC-Y_p1_l1-2"])
        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(merged["source_refs"], ["DOC-Y_p1_l1-2"])

    def test_same_code_still_merges_by_id_and_empty_name_creates_by_code(self):
        graph = _fresh_graph()
        a = _upsert_semantic_node(graph, "symptom", "SYM_1", "胸痛", [])
        b = _upsert_semantic_node(graph, "symptom", "SYM_1", "胸痛", ["DOC-Z_p1_l1-2"])
        self.assertEqual(a["id"], b["id"])
        # 空 name 不参与同名匹配：不同码 + 空名 → 各自建节点
        _upsert_semantic_node(graph, "lab", "LAB_A", "", [])
        _upsert_semantic_node(graph, "lab", "LAB_B", "", [])
        self.assertEqual(len([n for n in graph["nodes"].values() if n["node_type"] == "lab"]), 2)

    def test_relations_attach_to_merged_node(self):
        graph = _fresh_graph()
        disease = _upsert_semantic_node(graph, "disease", "R57.2", "感染性休克", [])
        other = _upsert_semantic_node(graph, "disease", "I21.0", "急性心梗", [])
        # 第二次以候选码 upsert 同名 → 返回同一节点
        again = _upsert_semantic_node(graph, "disease", "CAND-SS", "感染性休克", ["DOC-W_p1_l1-3"])
        _upsert_semantic_relation(graph, other["id"], "DISEASE_HAS_SYMPTOM", again["id"], {}, ["DOC-W_p1_l1-3"])
        self.assertEqual(len(graph["relations"]), 1)
        (src, code, tgt), _ = list(graph["relations"].items())[0]
        self.assertEqual(tgt, disease["id"])

    def test_merge_counter_tracked_and_not_persisted(self):
        graph = _fresh_graph()
        _upsert_semantic_node(graph, "disease", "R57.2", "感染性休克", [])
        _upsert_semantic_node(graph, "disease", "CAND-SS", "感染性休克", [])
        _upsert_semantic_node(graph, "disease", "CAND-SS2", "感染性休克", [])
        self.assertEqual(graph["_same_name_merges"], 2)
        with tempfile.TemporaryDirectory() as tmp:
            _save_graph(graph, "merge-count.json", Path(tmp))
            saved = json.loads((Path(tmp) / "merge-count.json").read_text(encoding="utf-8"))
            self.assertNotIn("_same_name_merges", saved)


if __name__ == "__main__":
    unittest.main()
```

（`_upsert_semantic_relation` 签名已核对：`(graph, source_node_id, relation_code, target_node_id, relation_names, source_refs=None, attributes=None)`，`relation_names` 传 `{}`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_same_name_merge.py -v`
Expected: FAIL——`test_same_name_different_code_merges_into_existing` 断言两节点 id 相等失败（现有实现建了两个节点）。

- [ ] **Step 3: 最小实现**

在 kg.py `_semantic_node_id`（178 行）后加：

```python
def _normalize_entity_name(name: str) -> str:
    return " ".join(str(name or "").split())
```

`_upsert_semantic_node` 中，`node = graph["nodes"].get(node_id)`（256 行）与 `if node is None:`（257 行）之间插入同名查找；新建/更新两个分支保持原样不动（更新分支 290 行已做 refs 并集）：

```python
    node = graph["nodes"].get(node_id)
    if node is None and _normalize_entity_name(str(name or "")):
        normalized = _normalize_entity_name(str(name))
        for existing in graph["nodes"].values():
            if (
                existing.get("node_type") == node_type
                and _normalize_entity_name(str(existing.get("name") or "")) == normalized
            ):
                node = existing
                incoming_code = str(code)
                # 后续更新分支用 code 重算 display_name，先回绑节点自身编码防止候选码污染
                code = str(node.get("code") or code)
                if incoming_code and incoming_code != code:
                    alts = node.setdefault("attributes", {}).setdefault("alt_codes", [])
                    if incoming_code not in alts:
                        alts.append(incoming_code)
                graph["_same_name_merges"] = graph.get("_same_name_merges", 0) + 1
                break
    if node is None:
        # …原新建分支不变…
    else:
        # …原更新分支不变…
```

`_save_graph`（150-162 行）在 `payload = dict(data)` 后加一行，保证计数键不落盘：

```python
    payload = dict(data)
    payload.pop("_same_name_merges", None)
```

`import_graph` 内两处 `_save_graph(graph, graph_filename)` 调用前（TCM 分支约 966 行、西文分支约 1200 行，grep 定位）各加：

```python
    same_name_merges = graph.pop("_same_name_merges", 0)
    if same_name_merges:
        warnings.append(f"同名实体自动合并 {same_name_merges} 处候选码")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_same_name_merge.py tests/test_review_api.py tests/test_review_queue.py -v`
Expected: 全 PASS（后两个文件确认无回归）。

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/kg.py tests/test_same_name_merge.py
git commit -m "feat: 导入时同类型同名实体自动合并（alt_codes 留痕+溯源并集）"
```

---

### Task 2: 疑似重复检测

**Files:**
- Modify: `src/api/routes/kg.py`（新增 `find_duplicate_groups` + `GET /duplicates` 路由）
- Test: `tests/test_merge_duplicates.py`（新建，本任务只写检测部分）

**Interfaces:**
- Consumes: `_load_graph(filename, kg_dir=None)`、`_normalize_entity_name`（Task 1）。
- Produces:
  - `_is_standard_code(code: str) -> bool`
  - `find_duplicate_groups(graph: Dict) -> List[Dict]`，元素 `{node_type, name, nodes: [{node_id, code, is_standard_code, review_status, source_ref_count, relation_count}], recommended_survivor: str}`
  - `GET /api/kg/duplicates?graph_filename=X` → `{"groups": [...]}`（Task 3 前端与 Task 5 使用）

- [ ] **Step 1: 写失败测试**

```python
"""疑似重复检测：同类型同名分组 + 标准码识别 + 推荐保留节点。"""
import sys, unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes.kg import find_duplicate_groups, _is_standard_code


def _node(node_id, code, name, node_type="disease", status="draft", refs=None):
    return {"id": node_id, "node_type": node_type, "code": code, "name": name,
            "review_status": status, "source_refs": refs or [], "attributes": {}}


class DetectDuplicatesTest(unittest.TestCase):
    def _graph(self, *nodes, relations=()):
        return {"nodes": {n["id"]: n for n in nodes},
                "relations": {k: {"review_status": "draft"} for k in relations}}

    def test_standard_code_pattern(self):
        self.assertTrue(_is_standard_code("Q21.0"))
        self.assertTrue(_is_standard_code("I21.0"))
        self.assertFalse(_is_standard_code("CAND-VSD"))
        self.assertFalse(_is_standard_code("SYM_CHEST_PAIN"))
        self.assertFalse(_is_standard_code(""))

    def test_groups_same_type_same_name_and_recommends_standard_code(self):
        graph = self._graph(
            _node("ENTITY:disease:CAND-VSD", "CAND-VSD", "室间隔缺损", refs=["a"]),
            _node("ENTITY:disease:Q21.0", "Q21.0", "室间隔缺损", status="active"),
            _node("ENTITY:disease:I21.0", "I21.0", "急性心梗"),
            relations=(("ENTITY:disease:Q21.0", "DISEASE_HAS_SYMPTOM", "ENTITY:symptom:S1"),),
        )
        groups = find_duplicate_groups(graph)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["name"], "室间隔缺损")
        self.assertEqual(group["recommended_survivor"], "ENTITY:disease:Q21.0")
        by_id = {n["node_id"]: n for n in group["nodes"]}
        self.assertTrue(by_id["ENTITY:disease:Q21.0"]["is_standard_code"])
        self.assertFalse(by_id["ENTITY:disease:CAND-VSD"]["is_standard_code"])
        self.assertEqual(by_id["ENTITY:disease:Q21.0"]["relation_count"], 1)
        self.assertEqual(by_id["ENTITY:disease:CAND-VSD"]["source_ref_count"], 1)

    def test_no_duplicates_returns_empty_and_ignores_empty_names(self):
        graph = self._graph(
            _node("ENTITY:disease:I21.0", "I21.0", "急性心梗"),
            _node("ENTITY:disease:X", "X", ""),  # 空名不参与
            _node("ENTITY:symptom:SYM_1", "SYM_1", "胸痛"),
        )
        self.assertEqual(find_duplicate_groups(graph), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_merge_duplicates.py -v`
Expected: FAIL，ImportError（`find_duplicate_groups` 不存在）。

- [ ] **Step 3: 实现**

kg.py 新增（放在 `clear_graph_and_rules` 附近）：

```python
import re as _re

def _is_standard_code(code: str) -> bool:
    return bool(_re.fullmatch(r"[A-Z]\d{2}(?:\.\d+)?", str(code or "")))


def find_duplicate_groups(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    relation_counts: Dict[str, int] = {}
    for source_id, _, target_id in graph.get("relations", {}):
        relation_counts[source_id] = relation_counts.get(source_id, 0) + 1
        relation_counts[target_id] = relation_counts.get(target_id, 0) + 1
    grouped: Dict[tuple, List[Dict[str, Any]]] = {}
    for node in graph.get("nodes", {}).values():
        name = _normalize_entity_name(str(node.get("name") or ""))
        if not name:
            continue
        grouped.setdefault((str(node.get("node_type") or ""), name), []).append(node)
    groups: List[Dict[str, Any]] = []
    for (node_type, name), nodes in grouped.items():
        if len(nodes) < 2:
            continue
        def _rank(n: Dict[str, Any]):
            code = str(n.get("code") or "")
            return (
                0 if _is_standard_code(code) else 1,
                0 if n.get("review_status") == "active" else 1,
                -relation_counts.get(n["id"], 0),
                code,
            )
        ordered = sorted(nodes, key=_rank)
        groups.append({
            "node_type": node_type,
            "name": name,
            "nodes": [{
                "node_id": n["id"],
                "code": str(n.get("code") or ""),
                "is_standard_code": _is_standard_code(str(n.get("code") or "")),
                "review_status": str(n.get("review_status") or "draft"),
                "source_ref_count": len(n.get("source_refs") or []),
                "relation_count": relation_counts.get(n["id"], 0),
            } for n in ordered],
            "recommended_survivor": ordered[0]["id"],
        })
    groups.sort(key=lambda g: (-len(g["nodes"]), g["node_type"], g["name"]))
    return groups
```

路由（挂 kg router，风格照 `clear_graph_and_rules` 的 POST /clear）：

```python
@router.get("/duplicates")
async def list_duplicates(graph_filename: str):
    graph = _load_graph(graph_filename)
    return {"groups": find_duplicate_groups(graph)}
```

注意 kg router 中 `List/Dict/Any` 已导入（核对）；`import re` 若顶部已有则直接用 `re.fullmatch`，不要重复导入。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_merge_duplicates.py tests/test_same_name_merge.py -v`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/kg.py tests/test_merge_duplicates.py
git commit -m "feat: 疑似重复检测端点（同类型同名分组/标准码识别/推荐保留）"
```

---

### Task 3: 合并端点（关系重挂 + 规则改写 + 备份 + 审计）

**Files:**
- Modify: `src/api/routes/kg.py`（`merge_duplicate_nodes` + `MergeNodesRequest` + `POST /merge-nodes`）
- Test: `tests/test_merge_duplicates.py`（追加 MergeTest 类）

**Interfaces:**
- Consumes: `_load_graph/_save_graph(filename, kg_dir)`、`_load_all_rules/_save_all_rules(path)`、`record_snapshot(rule, action, actor="", directory=None)`（src/rules/history.py:24）、`append_audit(record, path=None)`（src/review/audit.py:16）、`find_duplicate_groups`（Task 2）。
- Produces:
  - `MergeNodesRequest(BaseModel)`: `graph_filename: str`, `survivor_node_id: str`, `absorbed_node_ids: List[str]`
  - `merge_duplicate_nodes(request, kg_dir=None, rules_path=None, snapshot_dir=None, audit_path=None) -> Dict`，返回 `{"survivor_node_id", "absorbed": [{node_id, code}], "merged_relations": int, "deduped_relations": int, "updated_rules": [rule_id]}`
  - `POST /api/kg/merge-nodes`

- [ ] **Step 1: 写失败测试（追加到 tests/test_merge_duplicates.py）**

```python
import json
import tempfile
from src.api.routes.kg import merge_duplicate_nodes
from src.api.routes.rules import _load_all_rules


class MergeDuplicatesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.kg_dir = self.root / "kg"; self.kg_dir.mkdir()
        self.rules_path = self.root / "managed_rules.json"
        self.snapshot_dir = self.root / "history"
        self.audit_path = self.root / "review_log.jsonl"
        graph = {
            "domain": "western", "schema_version": 1,
            "diseases": [], "evidences": [], "disease_evidence": [], "sources": [],
            "nodes": {
                "ENTITY:disease:Q21.0": _node("ENTITY:disease:Q21.0", "Q21.0", "室间隔缺损", status="active", refs=["r1"]),
                "ENTITY:disease:CAND-VSD": _node("ENTITY:disease:CAND-VSD", "CAND-VSD", "室间隔缺损", refs=["r2"]),
                "ENTITY:symptom:SYM_1": _node("ENTITY:symptom:SYM_1", "SYM_1", "胸痛", node_type="symptom"),
            },
            "relations": {
                ("ENTITY:disease:CAND-VSD", "DISEASE_HAS_SYMPTOM", "ENTITY:symptom:SYM_1"): {"review_status": "draft", "source_refs": ["r2"]},
                ("ENTITY:disease:Q21.0", "DISEASE_HAS_SYMPTOM", "ENTITY:symptom:SYM_1"): {"review_status": "active", "source_refs": ["r1"]},
            },
        }
        (self.kg_dir / "test_graph.json").write_text(json.dumps({
            **{k: v for k, v in graph.items() if k != "relations"},
            "relations": [
                {"source_node_id": s, "relation_code": c, "target_node_id": t, "review_status": r["review_status"], "source_refs": r["source_refs"]}
                for (s, c, t), r in graph["relations"].items()
            ],
        }, ensure_ascii=False), encoding="utf-8")
        self.rules_path.write_text(json.dumps([{
            "rule_id": "R_VSD", "name": "室缺规则", "graph_filename": "test_graph.json",
            "disease_code": "CAND-VSD", "status": "draft",
            "conditions": {"type": "group", "operator": "AND", "children": [
                {"type": "symptom", "symptom_code": "SYM_1"},
                {"type": "group", "operator": "OR", "children": [
                    {"type": "exam", "exam_code": "CAND-VSD"}]}]},
            "conclusion": {"diagnosis": "CAND-VSD"},
        }], ensure_ascii=False), encoding="utf-8")

    def _request(self, survivor="ENTITY:disease:Q21.0", absorbed=None):
        from src.api.routes.kg import MergeNodesRequest
        return MergeNodesRequest(graph_filename="test_graph.json", survivor_node_id=survivor,
                                 absorbed_node_ids=absorbed or ["ENTITY:disease:CAND-VSD"])

    def test_merge_reattaches_dedupes_and_rewrites_rules(self):
        result = merge_duplicate_nodes(self._request(), kg_dir=self.kg_dir, rules_path=self.rules_path,
                                       snapshot_dir=self.snapshot_dir, audit_path=self.audit_path)
        self.assertEqual(result["deduped_relations"], 1)
        self.assertEqual(result["merged_relations"], 1)
        self.assertEqual(result["updated_rules"], ["R_VSD"])
        graph = json.loads((self.kg_dir / "test_graph.json").read_text())
        ids = {n["id"] for n in graph["nodes"]}
        self.assertNotIn("ENTITY:disease:CAND-VSD", ids)
        survivor = next(n for n in graph["nodes"] if n["id"] == "ENTITY:disease:Q21.0")
        self.assertEqual(survivor["source_refs"], ["r1", "r2"])
        self.assertEqual(survivor["attributes"]["alt_codes"], ["CAND-VSD"])
        self.assertEqual(len(graph["relations"]), 1)  # 同三元组去重合并
        merged = graph["relations"][0]
        self.assertEqual(merged["review_status"], "active")  # 状态取更高
        self.assertEqual(set(merged["source_refs"]), {"r1", "r2"})
        rule = _load_all_rules(self.rules_path)[0]
        self.assertEqual(rule["disease_code"], "Q21.0")
        self.assertEqual(rule["conditions"]["children"][1]["children"][0]["exam_code"], "Q21.0")
        self.assertEqual(rule["conclusion"]["diagnosis"], "Q21.0")
        # 快照与审计与备份
        self.assertTrue(list(self.snapshot_dir.glob("R_VSD.jsonl")))
        self.assertIn("merge", self.audit_path.read_text())
        self.assertTrue(list(self.kg_dir.glob("test_graph.json.bak-*-merge")))
        self.assertTrue(list(self.rules_path.parent.glob("managed_rules.json.bak-*")))

    def test_merge_rejects_overlap_and_missing(self):
        with self.assertRaises(Exception):
            merge_duplicate_nodes(self._request(absorbed=["ENTITY:disease:Q21.0"]),
                                  kg_dir=self.kg_dir, rules_path=self.rules_path)
        with self.assertRaises(Exception):
            merge_duplicate_nodes(self._request(absorbed=["ENTITY:disease:NOPE"]),
                                  kg_dir=self.kg_dir, rules_path=self.rules_path)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_merge_duplicates.py -v`
Expected: MergeDuplicatesTest FAIL（`merge_duplicate_nodes` ImportError）。

- [ ] **Step 3: 实现**

kg.py 新增（错误用 `HTTPException`，与文件内既有端点一致；函数顶部导入遵循文件既有惯例——kg.py:1342 有 `from .rules import ...` 的函数内导入先例）：

```python
class MergeNodesRequest(BaseModel):
    graph_filename: str
    survivor_node_id: str
    absorbed_node_ids: List[str]


_STATUS_RANK = {"active": 3, "draft": 2}


def _rewrite_codes(value, mapping: Dict[str, str]) -> bool:
    changed = False
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                isinstance(item, str)
                and (key.endswith("_code") or key == "diagnosis")
                and item in mapping
            ):
                value[key] = mapping[item]
                changed = True
            elif isinstance(item, (dict, list)):
                changed = _rewrite_codes(item, mapping) or changed
    elif isinstance(value, list):
        for item in value:
            changed = _rewrite_codes(item, mapping) or changed
    return changed


def merge_duplicate_nodes(
    request: MergeNodesRequest,
    kg_dir: Optional[Path] = None,
    rules_path: Optional[Path] = None,
    snapshot_dir: Optional[Path] = None,
    audit_path: Optional[Path] = None,
) -> Dict[str, Any]:
    import copy
    from .rules import MANAGED_RULES_PATH, _load_all_rules, _save_all_rules
    from src.rules.history import record_snapshot
    from src.review.audit import append_audit

    graph_filename = normalize_graph_filename(request.graph_filename)
    graph = _load_graph(graph_filename, kg_dir)
    nodes = graph["nodes"]
    survivor = nodes.get(request.survivor_node_id)
    if survivor is None:
        raise HTTPException(404, "保留节点不存在")
    absorbed = []
    for node_id in request.absorbed_node_ids:
        if node_id == request.survivor_node_id:
            raise HTTPException(422, "保留节点不能同时出现在被吸收列表中")
        node = nodes.get(node_id)
        if node is None:
            raise HTTPException(404, f"被吸收节点不存在：{node_id}")
        absorbed.append(node)
    if not absorbed:
        raise HTTPException(422, "被吸收节点列表为空")
    absorbed_ids = {n["id"] for n in absorbed}

    stamp = datetime.now().strftime("%Y%m%d")
    graph_path = (Path(kg_dir) if kg_dir is not None else KG_DIR) / graph_filename
    if graph_path.exists():
        shutil.copy2(graph_path, graph_path.with_name(f"{graph_path.name}.bak-{stamp}-merge"))

    rules = _load_all_rules(rules_path)
    rules_file = Path(rules_path) if rules_path is not None else MANAGED_RULES_PATH
    if rules_file.exists() and any(
        normalize_graph_filename(r.get("graph_filename") or "") == graph_filename for r in rules
    ):
        shutil.copy2(rules_file, rules_file.with_name(f"{rules_file.name}.bak-{stamp}-merge"))

    merged_relations = 0
    deduped_relations = 0
    new_relations: Dict[tuple, Dict[str, Any]] = {}
    for (source_id, relation_code, target_id), relation in list(graph["relations"].items()):
        new_source = survivor["id"] if source_id in absorbed_ids else source_id
        new_target = survivor["id"] if target_id in absorbed_ids else target_id
        new_key = (new_source, relation_code, new_target)
        if new_key == (source_id, relation_code, target_id):
            new_relations[new_key] = relation
            continue
        merged_relations += 1
        existing = new_relations.get(new_key)
        if existing is None:
            new_relations[new_key] = {**relation, "source_node_id": new_source, "target_node_id": new_target}
        else:
            deduped_relations += 1
            existing["source_refs"] = list(dict.fromkeys([
                *(existing.get("source_refs") or []), *(relation.get("source_refs") or []),
            ]))
            if _STATUS_RANK.get(relation.get("review_status"), 1) > _STATUS_RANK.get(existing.get("review_status"), 1):
                existing["review_status"] = relation.get("review_status")
    graph["relations"] = new_relations

    survivor["source_refs"] = list(dict.fromkeys([
        *(survivor.get("source_refs") or []), *[r for n in absorbed for r in (n.get("source_refs") or [])],
    ]))
    survivor.setdefault("attributes", {}).setdefault("alt_codes", [])
    for node in absorbed:
        for code in [str(node.get("code") or ""), *(node.get("attributes", {}).get("alt_codes") or [])]:
            if code and code != survivor.get("code") and code not in survivor["attributes"]["alt_codes"]:
                survivor["attributes"]["alt_codes"].append(code)
    for node_id in absorbed_ids:
        nodes.pop(node_id, None)

    code_mapping = {str(n.get("code") or ""): str(survivor.get("code") or "") for n in absorbed}
    code_mapping.pop("", None)
    updated_rules = []
    for rule in rules:
        if normalize_graph_filename(rule.get("graph_filename") or "") != graph_filename:
            continue
        original = copy.deepcopy(rule)
        if _rewrite_codes(rule, code_mapping):
            record_snapshot(original, "update", actor=f"merge:{survivor['id']}", directory=snapshot_dir)
            updated_rules.append(str(rule.get("rule_id") or ""))
    _save_all_rules(rules, rules_path)

    append_audit({
        "ts": datetime.now(timezone.utc).isoformat(),
        "entity_type": "graph_node",
        "entity_id": survivor["id"],
        "action": "merge",
        "before_status": survivor.get("review_status"),
        "after_status": survivor.get("review_status"),
        "reviewer": "合并工具",
        "note": "吸收同类型同名节点：" + "、".join(f"{n['id']}({n.get('code')})" for n in absorbed),
    }, path=audit_path)

    _save_graph(graph, graph_filename, kg_dir)
    return {
        "survivor_node_id": survivor["id"],
        "absorbed": [{"node_id": n["id"], "code": str(n.get("code") or "")} for n in absorbed],
        "merged_relations": merged_relations,
        "deduped_relations": deduped_relations,
        "updated_rules": updated_rules,
    }


@router.post("/merge-nodes")
async def merge_nodes_endpoint(request: MergeNodesRequest):
    return merge_duplicate_nodes(request)
```

实现注意（以文件现状为准，冲突时按现状调整）：
- `graph["relations"]` 在 `_load_graph` 后是 dict（键为三元组 tuple），`_save_graph` 负责序列化回列表——重挂时**同时更新条目内的 `source_node_id`/`target_node_id` 字段**（示例已含）。
- 快照记录的是改写**前**的深拷贝原件，且只记真正被改写的规则（规格要求）；`record_snapshot(rule, action, actor="", directory=None)`（src/rules/history.py:24）在 `directory` 下写 `{rule_id}.jsonl`（自动 mkdir parents），测试 glob `R_VSD.jsonl` 与此一致。
- `append_audit(record, path=None)`（src/review/audit.py:16）自动补 `ts`（UTC ISO）字段，其余字段原样落盘；test_review_api 已断言 `note`/`reviewer` 字段名。
- `datetime/timezone/shutil` 顶部若缺则补导入。
- `_rewrite_codes` 对以 `_code` 结尾的字符串叶子字段（含顶层 `disease_code`）及 `conclusion.diagnosis` 生效，递归 dict/list；测试对两者均有断言。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_merge_duplicates.py tests/test_same_name_merge.py tests/test_review_api.py -v`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/kg.py tests/test_merge_duplicates.py
git commit -m "feat: 节点合并端点（关系重挂去重/规则条件改写/双备份/审计留痕）"
```

---

### Task 4: 前端——疑似重复页签 + 关联条目

**Files:**
- Modify: `src/review/service.py`（`node_detail` 的 relations 附带邻居摘要）
- Modify: `src/api/static/app.js`（审核台 tab + 分组卡 + 详情关联区）
- Modify: `src/api/static/index.html`（升 `app.js?v=20261112entitygov`）
- Test: `tests/test_review_api.py`（追加邻居摘要断言）

**Interfaces:**
- Consumes: `GET /api/kg/duplicates`（Task 2）、`POST /api/kg/merge-nodes`（Task 3）、`node_detail` 的 `relations`（扩展）；前端已存在的助手（勿新造）：`relationLabel(item)`（读 `item.relation_code/relation_name`，app.js:2920）、`miniStatus(status)`（2931）、`reviewBadge(status)`（119）、`typeName`、`esc`、`toast`、`api(path, { json })`。
- Produces: `node_detail` 返回的 relations 元素变为 `{"key": "src|code|tgt", "relation": {...}, "source": {node_id,name,code,node_type,status}, "target": {...}}`（形状镜像 service.py `relation_detail` 的 `endpoint_summary`，259-268 行，加 `node_type`）；前端 `relatedItemsHtml(detail, item)` 与「疑似重复」tab（`state.reviewTab === "dupes"`）。

- [ ] **Step 1: 写失败测试（追加到 tests/test_review_api.py）**

```python
    def test_node_detail_relations_include_neighbor_summary(self):
        response = self.request(
            "GET",
            "/api/kg/nodes/ENTITY%3Adisease%3AI21.0?graph_filename=API-TEST.json",
        )
        self.assertEqual(response.status_code, 200)
        relations = response.json()["relations"]
        self.assertTrue(relations)
        first = relations[0]
        self.assertIn("source", first)
        self.assertIn("target", first)
        self.assertEqual(first["source"]["name"], "测试病")
        self.assertEqual(first["target"]["node_type"], "symptom")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_review_api.py::ReviewApiTest::test_node_detail_relations_include_neighbor_summary -v`
Expected: FAIL，`KeyError: 'source'`。

- [ ] **Step 3: 后端实现**

`src/review/service.py::node_detail`（236-240 行）替换 relations 构造——`_status` 已是该模块函数（`endpoint_summary` 267 行在用）：

```python
        def neighbor_summary(node_id: str) -> Dict[str, Any]:
            node = graph["nodes"].get(node_id)
            if node is None:
                return {"node_id": node_id, "name": node_id, "code": "", "node_type": "", "status": "missing"}
            return {
                "node_id": node_id,
                "name": str(node.get("name") or node_id),
                "code": str(node.get("code") or node.get("concept_code") or ""),
                "node_type": str(node.get("node_type") or ""),
                "status": _status(node),
            }

        relations = [
            {
                "key": "|".join(key),
                "relation": relation,
                "source": neighbor_summary(key[0]),
                "target": neighbor_summary(key[2]),
            }
            for key, relation in graph["relations"].items()
            if node_id in (key[0], key[2])
        ]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_review_api.py -v`
Expected: 全 PASS。

- [ ] **Step 5: 前端——疑似重复页签**

app.js `renderReviewCenter`（3370 行起）四处改动 + 一个新函数：

1. **数据获取**：Promise.all 块（3384-3393 行）之后加（tab 为 dupes 且已选图谱才请求，带徽标数）：

```javascript
    const dupesData = tab === "dupes" && filters.graph
      ? await api(`/api/kg/duplicates?graph_filename=${encodeURIComponent(filters.graph)}`).catch(() => ({ groups: [] }))
      : { groups: [] };
```

2. **tabs 数组**（3417-3421 行）在 `["orphan", "孤儿条目"],` 与 `["audit", "审核记录"],` 之间插入：

```javascript
      ["dupes", `疑似重复${(tab === "dupes" && dupesData.groups?.length) ? ` ${dupesData.groups.length}` : ""}`],
```

3. **dupes 分支**：照抄 audit 分支（3432-3447 行）的早返回模式，紧跟其后插入。注意 `bindReviewFilter` 是 renderReviewCenter 尾部（3534 行）才定义的闭包，此处**不可复用**，自己绑：

```javascript
    if (tab === "dupes") {
      const graphFilename = filters.graph;
      const groups = dupesData.groups || [];
      const dupeCards = !graphFilename
        ? '<div class="empty">请先在下方选择目的图谱</div>'
        : (groups.map((group) => `
          <div class="dupe-group">
            <div class="dupe-head">
              <span class="type-chip">${esc(typeName[group.node_type] || group.node_type)}</span>
              <strong>${esc(group.name)}</strong>
              <span class="muted">${group.nodes.length} 个同名节点</span>
            </div>
            <div class="dupe-nodes">${group.nodes.map((n) => `
              <label class="dupe-node">
                <input type="radio" name="dupe-group" value="${esc(n.node_id)}" ${n.node_id === group.recommended_survivor ? "checked" : ""}>
                <span class="mono">${esc(n.code)}</span>
                ${n.is_standard_code ? '<span class="badge active">标准码</span>' : '<span class="badge">候选码</span>'}
                ${reviewBadge(n.review_status)}
                <span class="muted">关系 ${n.relation_count} · 引用 ${n.source_ref_count}</span>
              </label>`).join("")}</div>
            <div class="actions"><button class="btn sm primary" data-dupe-merge>合并为一</button></div>
          </div>`).join("") || '<div class="empty">未检测到同类型同名重复</div>');
      content.innerHTML = `<div class="review-tabs">${tabHtml}</div>
        <div class="review-list" style="max-width:none">
          <div class="review-toolbar compact review-filterbar">
            <select id="reviewFilterGraph" title="按目的图谱筛选">
              <option value="">请选择图谱</option>
              ${(state.reviewGraphOptions || []).map((g) => `<option value="${esc(g.graph_filename)}" ${filters.graph === g.graph_filename ? "selected" : ""}>${esc(g.graph_filename)}</option>`).join("")}
            </select>
            <span class="muted">同类型同名节点分组；默认勾选推荐保留节点（标准码优先），「合并为一」吸收其余节点并自动改写规则引用（合并前自动备份）。</span>
          </div>
          ${dupeCards}
        </div>`;
      bindReviewTabs();
      $("#reviewFilterGraph")?.addEventListener("change", (event) => {
        state.reviewFilters.graph = event.target.value;
        renderReviewCenter();
      });
      bindDupeMerge(graphFilename);
      return;
    }
```

4. **新函数 `bindDupeMerge`**：放在 `bindReviewTabs`（3362-3368 行）之后（模块作用域，2 空格缩进）：

```javascript
  function bindDupeMerge(graphFilename) {
    if (!graphFilename) return;
    content.querySelectorAll("[data-dupe-merge]").forEach((button) => button.onclick = async () => {
      const groupEl = button.closest(".dupe-group");
      const checked = groupEl.querySelector("input[type=radio]:checked");
      if (!checked) { toast("请选择保留节点", true); return; }
      const survivor = checked.value;
      const labels = [...groupEl.querySelectorAll(".dupe-node")].map((el) => ({
        id: el.querySelector("input[type=radio]").value,
        code: el.querySelector(".mono").textContent,
      }));
      const survivorCode = labels.find((l) => l.id === survivor).code;
      const absorbed = labels.filter((l) => l.id !== survivor);
      if (!confirm(`保留 ${survivorCode}，吸收：${absorbed.map((l) => l.code).join("、")}？\n关联关系将重挂到保留节点，引用被吸收编码的规则会自动改写（合并前自动备份）。`)) return;
      button.disabled = true;
      try {
        const result = await api("/api/kg/merge-nodes", { json: {
          graph_filename: graphFilename, survivor_node_id: survivor,
          absorbed_node_ids: absorbed.map((l) => l.id),
        }});
        toast(`合并完成：重挂关系 ${result.merged_relations}、去重 ${result.deduped_relations}、改写规则 ${result.updated_rules.length} 条`);
        renderReviewCenter();
      } catch (error) { toast(error.message, true); button.disabled = false; }
    });
  }
```

- [ ] **Step 6: 前端——关联条目**

1. **新函数 `relatedItemsHtml(detail, item)`**：放在 `nodeDetailHtml`（3210 行）之前（模块作用域、2 空格缩进；关系行与邻居均可点击，超过 20 条折叠为「显示全部」）：

```javascript
  function relatedItemsHtml(detail, item) {
    const relations = detail.relations || [];
    if (!relations.length) {
      return '<div class="detail-section"><h4>关联条目</h4><div class="muted">暂无关联条目</div></div>';
    }
    const row = (entry, asSource) => {
      const neighbor = asSource ? entry.target : entry.source;
      const label = relationLabel({ relation_code: entry.relation.relation_code, relation_name: entry.relation.relation_name });
      return `<div class="related-row">
        <button class="related-rel" data-related-relation="${esc(entry.key)}"
          data-relation-code="${esc(entry.relation.relation_code)}"
          data-source-node="${esc(entry.source.node_id)}"
          data-target-node="${esc(entry.target.node_id)}"
          data-relation-status="${esc(entry.relation.review_status || "")}"
          title="查看关系详情">${esc(asSource ? label : `← ${label}`)}</button>
        <button class="related-node" data-related-node="${esc(neighbor.node_id)}">
          <span class="type-chip">${esc(typeName[neighbor.node_type] || neighbor.node_type || "-")}</span>
          <strong>${esc(neighbor.name)}</strong>${miniStatus(neighbor.status)}
        </button>
      </div>`;
    };
    const asSource = relations.filter((e) => e.source.node_id === item.node_id);
    const asTarget = relations.filter((e) => e.target.node_id === item.node_id);
    const section = (title, list, flag) => {
      if (!list.length) return "";
      const visible = list.slice(0, 20).map((e) => row(e, flag)).join("");
      const extra = list.slice(20);
      const hidden = extra.length
        ? `<div class="related-extra" hidden>${extra.map((e) => row(e, flag)).join("")}</div>
           <div class="related-row"><button class="btn sm" data-related-expand>显示全部 ${list.length} 条</button></div>`
        : "";
      return `<div class="detail-section"><h4>${title}（${list.length}）</h4>
        <div class="related-list">${visible}${hidden}</div>
      </div>`;
    };
    return section("作为源", asSource, true) + section("作为目标", asTarget, false);
  }
```

2. **nodeDetailHtml**（3210 行）：在 `const refsHtml = ...` 一行后加 `const relatedHtml = relatedItemsHtml(detail, item);`，模板里 `${refsHtml ? ... }` 那行之后插入一行 `${relatedHtml}`。

3. **loadReviewDetail**（3297 行）：设置 `mount.innerHTML` 后、既有 `data-ref` 溯源 chip 绑定附近，追加三组绑定：

```javascript
      mount.querySelectorAll("[data-related-node]").forEach((btn) => btn.onclick = () => {
        loadReviewDetail({ item_type: "graph_node", node_id: btn.dataset.relatedNode, graph_filename: item.graph_filename, status: "" });
      });
      mount.querySelectorAll("[data-related-relation]").forEach((btn) => btn.onclick = () => {
        loadReviewDetail({
          item_type: "graph_relation",
          item_id: btn.dataset.relatedRelation,
          graph_filename: item.graph_filename,
          relation_code: btn.dataset.relationCode,
          source_node_id: btn.dataset.sourceNode,
          target_node_id: btn.dataset.targetNode,
          status: btn.dataset.relationStatus || "",
        });
      });
      mount.querySelectorAll("[data-related-expand]").forEach((btn) => btn.onclick = () => {
        const extra = btn.closest(".related-list")?.querySelector(".related-extra");
        if (extra) { extra.hidden = false; btn.closest(".related-row").remove(); }
      });
```

（`item` 在 loadReviewDetail 作用域可用；`relationDetailHtml` 所需字段即 pseudo item 传入的这些。）

**规格偏差记录**：spec B-前端 要求 confirm 里列「受影响规则数」，检测端点不返回规则引用数，预合并无从得知——confirm 列被吸收的码，改写规则数在合并后 toast 中报告。

4. style.css 追加：

```css
/* 疑似重复分组与关联条目 */
.dupe-group { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; }
.dupe-head { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
.dupe-nodes { display: grid; gap: 6px; margin-bottom: 8px; }
.dupe-node { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.related-list { display: grid; gap: 4px; }
.related-row { display: flex; gap: 8px; align-items: center; font-size: 13px; }
.related-rel { color: var(--muted); min-width: 110px; background: none; border: none; cursor: pointer; padding: 2px 4px; text-align: left; }
.related-rel:hover { color: var(--fg); text-decoration: underline; }
.related-node { display: inline-flex; gap: 6px; align-items: center; background: none; border: none; cursor: pointer; padding: 2px 4px; border-radius: 4px; }
.related-node:hover { background: var(--accent-soft); }
```

- [ ] **Step 7: 验证与提交**

先升缓存版本：index.html 中 `app.js?v=20261112sourceref` → `app.js?v=20261112entitygov`。

```bash
node --check src/api/static/app.js && node --test 'tests/js/*.test.js'
python3 -m pytest tests/test_review_api.py tests/test_merge_duplicates.py -q
```
Expected: JS 全绿、pytest 全绿。手工：重启服务，浏览器验证疑似重复页签（选图谱后出分组、默认勾选、合并弹确认）与节点详情关联条目跳转。

```bash
git add src/review/service.py src/api/static/app.js src/api/static/style.css src/api/static/index.html tests/test_review_api.py
git commit -m "feat: 审核台疑似重复页签与节点关联条目展示"
```

---

### Task 5: 端到端验证（真实图谱）

**Files:** 无代码改动；验证产物不入库。

**Interfaces:**
- Consumes: Task 2/3/4 的端点与页面。

- [ ] **Step 1: 重启服务并跑检测**

```bash
./start-api.sh   # 后台
curl -s "http://127.0.0.1:8001/api/kg/duplicates?graph_filename=KG-GENERIC_GUIDELINE_V1.json" | python3 -m json.tool | head -50
```
Expected: 返回 20+ 组（感染性休克/室间隔缺损/地高辛…）。

- [ ] **Step 2: 合并一组真实重复**

对「室间隔缺损」组（Q21.0 与 CAND-VSD）POST `/api/kg/merge-nodes`（survivor=Q21.0 节点）。验证：
- 响应 `updated_rules` 含 `RULE-CHD-ASD-*`（此前引用 CAND 码的 3 条规则）；
- `data/kg/KG-GENERIC_GUIDELINE_V1.json.bak-*-merge` 存在；
- `GET /api/kg/duplicates` 中该组消失；
- `GET /api/review/queue?graph_filename=...` 正常、被吸收节点不再出现；
- `managed_rules.json` 中 `grep -c "CAND-VSD"` 归零。

- [ ] **Step 3: 浏览器手工验证**

疑似重复页签（含合并按钮）、节点详情「作为源/作为目标」关联跳转、溯源上下文仍正常。无浏览器则标注"待人工"。

- [ ] **Step 4: 不提交验证产物**

`git status` 确认除预期的图谱/规则/审计数据文件外无代码改动；`.bak-*` 文件与数据变更**不提交**（真实数据文件的合并结果是否入库由用户决定，默认保留本地）。
