# 审核台关系审核升级实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让关系（边）在审核台成为一等公民：独立页签、关系类型/端点类型筛选、可疑预检徽章、按疾病分组知识卡片、批量护栏与类型×状态概览。

**Architecture:** 后端在 `ReviewService.list_items` 上扩展筛选参数并附加预检 flags（新模块 `src/review/precheck.py`，端点类型权威来源是 `data/templates/extraction_templates.json`），新增 groups 两个只读端点支撑按病分组；前端 `renderReviewCenter` 拆分页签并新增知识卡片视图。数据结构零改动。

**Tech Stack:** FastAPI + 原生 JS（无框架）+ pytest/unittest。前端无自动化 DOM 测试（仓库惯例：JS 渲染手工验证，pytest 覆盖服务层）。

**Spec:** `docs/superpowers/specs/2026-11-12-relation-review-design.md`

## Global Constraints

- UI 文案一律中文；状态词沿用现有词汇：草稿/已通过/已拒绝/已失效/待复核/被阻断。
- 不改数据结构（不迁移、不改 KG JSON schema）、不改审核状态机（`src/review/status.py`）、不改级联与规则阻断逻辑。
- 预检只读，绝不因预检失败阻断审核动作（降级为少标 flag）。
- 测试风格：unittest + `tempfile.TemporaryDirectory`，照 `tests/test_graph_review_service.py` 惯例。
- 提交信息格式：`feat: …` / `test: …` / `chore: …` 中文描述（照仓库近期提交风格）。
- `app.js` 通过 `index.html` 的 `?v=` 缓存版本号加载（当前 `?v=20261112sourceref`），前端改动落地后必须升级版本号（Task 9 统一执行）。
- 服务端列表每类条目上限 300（`build_queue` 的 limit），前端概览受此限制时复用 `truncated` 提示。

---

### Task 1: 预检模块——端点类型映射加载

**Files:**
- Create: `src/review/precheck.py`
- Test: `tests/test_relation_review_upgrade.py`

**Interfaces:**
- Consumes: `TEMPLATE_PATH`（`src/api/routes/kg.py:30`，值为 `KG_DIR.parent / "templates" / "extraction_templates.json"`）。
- Produces: `load_endpoint_type_map(path: Optional[Path] = None) -> Dict[str, Dict[str, List[str]]]`，形如 `{"DISEASE_HAS_SYMPTOM": {"source": ["disease"], "target": ["symptom"]}}`；模板文件损坏/缺失时返回 `{}` 并打 warning 日志。Task 2、3、5 依赖此函数。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_relation_review_upgrade.py`：

```python
import json
import tempfile
import unittest
from pathlib import Path

from src.review.precheck import load_endpoint_type_map


class EndpointTypeMapTest(unittest.TestCase):
    def test_merges_templates_per_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "templates.json"
            path.write_text(json.dumps([
                {"template_id": "T1", "relations": [
                    {"relation_code": "DISEASE_HAS_SYMPTOM", "name": "症见",
                     "source_entity_type": "disease", "target_entity_type": "symptom"},
                ]},
                {"template_id": "T2", "relations": [
                    {"relation_code": "DISEASE_HAS_SYMPTOM", "name": "症见",
                     "source_entity_type": "disease", "target_entity_type": "symptom"},
                    {"relation_code": "SYNDROME_HAS_SYMPTOM", "name": "症见",
                     "source_entity_type": "syndrome", "target_entity_type": "symptom"},
                ]},
            ], ensure_ascii=False), encoding="utf-8")
            mapping = load_endpoint_type_map(path)
        self.assertEqual(mapping["DISEASE_HAS_SYMPTOM"], {"source": ["disease"], "target": ["symptom"]})
        self.assertEqual(mapping["SYNDROME_HAS_SYMPTOM"], {"source": ["syndrome"], "target": ["symptom"]})

    def test_corrupt_file_degrades_to_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(load_endpoint_type_map(path), {})

    def test_real_templates_cover_key_codes(self):
        mapping = load_endpoint_type_map()
        for code in ("DISEASE_HAS_SYMPTOM", "DISEASE_HAS_DIAGNOSTIC_EVIDENCE",
                     "TREATMENT_PLAN_INCLUDES_MEDICATION", "SYNDROME_HAS_TONGUE"):
            self.assertIn(code, mapping)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_relation_review_upgrade.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'src.review.precheck'`）

- [ ] **Step 3: 最小实现**

创建 `src/review/precheck.py`：

```python
"""Relation preflight checks: endpoint direction, provenance and duplication.

预检只读、可降级：模板加载失败返回空映射，仅导致少标方向可疑，不阻断任何审核动作。
端点类型的权威来源是 data/templates/extraction_templates.json 中各模板的
relations[].source_entity_type / target_entity_type。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.api.routes.kg import TEMPLATE_PATH

logger = logging.getLogger(__name__)

# 预检提示文案里的类型中文名（前端 typeName 的服务端对应版本）。
NODE_TYPE_LABELS = {
    "disease": "疾病", "symptom": "症状", "sign": "体征", "lab": "检验", "exam": "检查",
    "diagnostic_evidence": "诊断依据", "treatment_plan": "治疗方案", "medication": "药品",
    "intervention": "干预操作", "contraindication": "禁忌", "monitoring_requirement": "监测要求",
    "syndrome": "证候", "tongue": "舌象", "pulse": "脉象", "formula": "方剂", "herb": "中药",
    "treatment_method": "治法", "care_pathway": "临床路径", "pathway_phase": "路径阶段",
    "order_set": "医嘱组", "order_item": "医嘱项", "care_guidance": "调护",
    "risk_factor": "危险因素", "stage": "分期", "pathology": "病理", "histology": "组织学",
    "biomarker": "生物标志物", "molecular_test": "分子检测", "syndrome_element": "证素",
    "pathogenesis": "病机", "acupoint": "穴位", "meridian": "经络", "western_disease": "西医疾病",
}


def type_label(node_type: str) -> str:
    return NODE_TYPE_LABELS.get(str(node_type or ""), str(node_type or "未知"))


def load_endpoint_type_map(path: Optional[Path] = None) -> Dict[str, Dict[str, List[str]]]:
    """合并全部模板的关系端点类型定义；失败降级为空映射。"""
    target = Path(path) if path is not None else TEMPLATE_PATH
    try:
        templates = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("加载抽取模板失败，关系方向预检降级: %s", target)
        return {}
    merged: Dict[str, Dict[str, List[str]]] = {}
    for template in templates or []:
        for relation in template.get("relations") or []:
            code = str(relation.get("relation_code") or "")
            if not code:
                continue
            entry = merged.setdefault(code, {"source": [], "target": []})
            for side, field in (("source", "source_entity_type"), ("target", "target_entity_type")):
                value = str(relation.get(field) or "")
                if value and value not in entry[side]:
                    entry[side].append(value)
    return merged
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_relation_review_upgrade.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add src/review/precheck.py tests/test_relation_review_upgrade.py
git commit -m "feat: 关系预检模块——模板端点类型映射加载（可降级）"
```

---

### Task 2: 预检模块——关系 flags 计算

**Files:**
- Modify: `src/review/precheck.py`
- Test: `tests/test_relation_review_upgrade.py`

**Interfaces:**
- Consumes: Task 1 的 `load_endpoint_type_map`。
- Produces:
  - `relation_flags(relation: Dict, source_node: Optional[Dict], target_node: Optional[Dict], endpoint_type_map: Dict, duplicate_pairs: Set[Tuple[str, str]]) -> List[Dict[str, str]]`，每项 `{"code": ..., "message": ...}`；code ∈ `endpoint_inactive | no_source_refs | direction_suspect | self_loop | duplicate_pair`。
  - `duplicate_relation_pairs(relations: Iterable[Dict]) -> Set[Tuple[str, str]]`：同 (source,target) 出现 ≥2 种 relation_code 的对。
  Task 3、5 依赖这两个函数；前端依赖 flags 的 `code`/`message` 字段名。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_relation_review_upgrade.py`：

```python
from src.review.precheck import duplicate_relation_pairs, relation_flags


def _rel(source, target, code="DISEASE_HAS_SYMPTOM", status="draft", refs=("DOC-x_p1_l1-5",)):
    return {
        "source_node_id": source, "relation_code": code, "target_node_id": target,
        "review_status": status, "source_refs": list(refs),
    }


def _node(node_type, status="active"):
    return {"node_type": node_type, "review_status": status}


class RelationFlagsTest(unittest.TestCase):
    def setUp(self):
        self.map = {
            "DISEASE_HAS_SYMPTOM": {"source": ["disease"], "target": ["symptom"]},
            "DISEASE_HAS_DIAGNOSTIC_EVIDENCE": {"source": ["disease"], "target": ["diagnostic_evidence"]},
        }

    def test_clean_relation_has_no_flags(self):
        flags = relation_flags(
            _rel("D1", "S1", status="active"), _node("disease"), _node("symptom"),
            self.map, set(),
        )
        self.assertEqual(flags, [])

    def test_inactive_endpoint_flagged(self):
        flags = relation_flags(
            _rel("D1", "S2"), _node("disease"), _node("symptom", status="draft"), self.map, set(),
        )
        self.assertEqual([f["code"] for f in flags], ["endpoint_inactive"])

    def test_missing_source_refs_flagged(self):
        flags = relation_flags(
            _rel("D1", "S1", refs=()), _node("disease"), _node("symptom"), self.map, set(),
        )
        self.assertEqual([f["code"] for f in flags], ["no_source_refs"])

    def test_direction_suspect_with_expected_types_in_message(self):
        flags = relation_flags(
            _rel("S1", "D1"), _node("symptom"), _node("disease"), self.map, set(),
        )
        self.assertEqual([f["code"] for f in flags], ["direction_suspect"])
        self.assertIn("症状", flags[0]["message"])
        self.assertIn("疾病", flags[0]["message"])

    def test_direction_check_degrades_on_empty_map(self):
        flags = relation_flags(_rel("S1", "D1"), _node("symptom"), _node("disease"), {}, set())
        self.assertEqual(flags, [])

    def test_self_loop_and_duplicate_pair(self):
        pairs = {("D1", "D1")}
        flags = relation_flags(
            _rel("D1", "D1", code="DISEASE_HAS_DIAGNOSTIC_EVIDENCE"),
            _node("disease"), _node("disease"), self.map, pairs,
        )
        codes = [f["code"] for f in flags]
        self.assertIn("self_loop", codes)
        self.assertIn("duplicate_pair", codes)
        self.assertIn("direction_suspect", codes)

    def test_duplicate_pairs_detection(self):
        relations = [
            _rel("L1", "E1", code="LAB_HAS_DIAGNOSTIC_EVIDENCE"),
            _rel("L1", "E1", code="EXAM_HAS_DIAGNOSTIC_EVIDENCE"),
            _rel("D1", "S1", code="DISEASE_HAS_SYMPTOM"),
        ]
        self.assertEqual(duplicate_relation_pairs(relations), {("L1", "E1")})

    def test_missing_endpoint_node_counts_as_inactive(self):
        flags = relation_flags(_rel("D1", "GONE"), _node("disease"), None, self.map, set())
        self.assertEqual([f["code"] for f in flags], ["endpoint_inactive"])
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_relation_review_upgrade.py -v`
Expected: FAIL（`ImportError: cannot import name 'relation_flags'`）

- [ ] **Step 3: 实现**

在 `src/review/precheck.py` 追加（`typing` 行扩为 `from typing import Any, Dict, Iterable, List, Optional, Set, Tuple`）：

```python
def duplicate_relation_pairs(relations: Iterable[Dict[str, Any]]) -> Set[Tuple[str, str]]:
    """同 (source, target) 被多种 relation_code 连接的对——疑似重复连线。"""
    pair_codes: Dict[Tuple[str, str], Set[str]] = {}
    for relation in relations:
        pair = (str(relation.get("source_node_id") or ""), str(relation.get("target_node_id") or ""))
        pair_codes.setdefault(pair, set()).add(str(relation.get("relation_code") or ""))
    return {pair for pair, codes in pair_codes.items() if len(codes) > 1}


def relation_flags(
    relation: Dict[str, Any],
    source_node: Optional[Dict[str, Any]],
    target_node: Optional[Dict[str, Any]],
    endpoint_type_map: Dict[str, Dict[str, List[str]]],
    duplicate_pairs: Set[Tuple[str, str]],
) -> List[Dict[str, str]]:
    flags: List[Dict[str, str]] = []

    def _status(item: Optional[Dict[str, Any]]) -> str:
        return str((item or {}).get("review_status") or "draft")

    if _status(source_node) != "active" or _status(target_node) != "active":
        flags.append({"code": "endpoint_inactive", "message": "端点节点未全部「已通过」"})
    if not (relation.get("source_refs") or []):
        flags.append({"code": "no_source_refs", "message": "无指南溯源引用"})
    source_type = str((source_node or {}).get("node_type") or "")
    target_type = str((target_node or {}).get("node_type") or "")
    code = str(relation.get("relation_code") or "")
    expected = endpoint_type_map.get(code)
    if expected and expected.get("source") and expected.get("target"):
        if source_type not in expected["source"] or target_type not in expected["target"]:
            flags.append({
                "code": "direction_suspect",
                "message": (
                    f"预期 {type_label(expected['source'][0])}→{type_label(expected['target'][0])}，"
                    f"实际 {type_label(source_type)}→{type_label(target_type)}"
                ),
            })
    source_id = str(relation.get("source_node_id") or "")
    target_id = str(relation.get("target_node_id") or "")
    if source_id and source_id == target_id:
        flags.append({"code": "self_loop", "message": "源与目标是同一节点"})
    if (source_id, target_id) in duplicate_pairs:
        flags.append({"code": "duplicate_pair", "message": "同一对节点被多种关系类型重复连接"})
    return flags
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_relation_review_upgrade.py -v`
Expected: 11 passed

- [ ] **Step 5: 提交**

```bash
git add src/review/precheck.py tests/test_relation_review_upgrade.py
git commit -m "feat: 关系预检 flags（端点未通过/无溯源/方向可疑/自环/重复连线）"
```

---

### Task 3: list_items——关系筛选与 flags 落地

**Files:**
- Modify: `src/review/service.py`（`list_items`，约 149-228 行）
- Test: `tests/test_relation_review_upgrade.py`

**Interfaces:**
- Consumes: Task 2 的 `relation_flags` / `duplicate_relation_pairs` / `load_endpoint_type_map`。
- Produces:
  - `ReviewService.list_items(graph_filename, status=None, item_type=None, node_type=None, source_doc=None, referenced_by_rule=None, q=None, relation_code=None)`——新增第 8 个关键字参数。
  - relation 条目新增字段：`source_node_type: str`、`target_node_type: str`、`flags: List[Dict[str, str]]`（node 条目不加）。
  - `node_type` 筛选新语义：relation 条目按「源或目标端点类型」命中；node 条目仍按自身类型（行为不变）。
  - 私有方法 `ReviewService._relation_item(graph, key, relation, relation_counts, endpoint_type_map, duplicate_pairs) -> Dict`——relation 条目统一构造器，Task 5 的 groups 复用。
  Task 4、5 依赖这些签名。

- [ ] **Step 1: 写失败测试**

追加（fixture 照 `tests/test_graph_review_service.py` 的 setUp 惯例）：

```python
from src.review.errors import ReviewError
from src.review.service import ReviewService


GRAPH2 = {
    "schema_version": 1, "domain": "western", "anchor_types": ["disease"],
    "diseases": [{"code": "I42.2", "name_cn": "测试病"}],
    "evidences": [], "disease_evidence": [], "sources": [],
    "nodes": [
        {"id": "ENTITY:disease:D1", "node_type": "disease", "code": "I42.2", "name": "测试病", "review_status": "active"},
        {"id": "ENTITY:symptom:S1", "node_type": "symptom", "code": "SYM_1", "name": "症状一", "review_status": "active"},
        {"id": "ENTITY:symptom:S2", "node_type": "symptom", "code": "SYM_2", "name": "症状二", "review_status": "draft"},
        {"id": "ENTITY:lab:L1", "node_type": "lab", "code": "LAB_1", "name": "检验一", "review_status": "active"},
        {"id": "ENTITY:diagnostic_evidence:E1", "node_type": "diagnostic_evidence", "code": "LAB_1:CON_1", "name": "证据一", "review_status": "active"},
        {"id": "ENTITY:treatment_plan:TP1", "node_type": "treatment_plan", "code": "TP1", "name": "测试病治疗方案", "review_status": "active"},
        {"id": "ENTITY:medication:M1", "node_type": "medication", "code": "MED_1", "name": "药品一", "review_status": "active"},
    ],
    "relations": [
        {"source_node_id": "ENTITY:disease:D1", "relation_code": "DISEASE_HAS_SYMPTOM", "target_node_id": "ENTITY:symptom:S1", "relation_name": "疾病有症状", "review_status": "active", "source_refs": ["DOC-x_p1_l1-5"]},
        {"source_node_id": "ENTITY:disease:D1", "relation_code": "DISEASE_HAS_SYMPTOM", "target_node_id": "ENTITY:symptom:S2", "relation_name": "疾病有症状", "review_status": "draft", "source_refs": []},
        {"source_node_id": "ENTITY:symptom:S1", "relation_code": "DISEASE_HAS_SYMPTOM", "target_node_id": "ENTITY:disease:D1", "relation_name": "疾病有症状", "review_status": "draft", "source_refs": ["DOC-x_p1_l1-5"]},
        {"source_node_id": "ENTITY:disease:D1", "relation_code": "DISEASE_HAS_DIAGNOSTIC_EVIDENCE", "target_node_id": "ENTITY:disease:D1", "relation_name": "构成诊断依据", "review_status": "draft", "source_refs": ["DOC-x_p1_l1-5"]},
        {"source_node_id": "ENTITY:lab:L1", "relation_code": "LAB_HAS_DIAGNOSTIC_EVIDENCE", "target_node_id": "ENTITY:diagnostic_evidence:E1", "relation_name": "检验形成证据", "review_status": "draft", "source_refs": ["DOC-x_p1_l1-5"]},
        {"source_node_id": "ENTITY:lab:L1", "relation_code": "EXAM_HAS_DIAGNOSTIC_EVIDENCE", "target_node_id": "ENTITY:diagnostic_evidence:E1", "relation_name": "检查形成证据", "review_status": "draft", "source_refs": ["DOC-x_p1_l1-5"]},
        {"source_node_id": "ENTITY:disease:D1", "relation_code": "DISEASE_HAS_TREATMENT_PLAN", "target_node_id": "ENTITY:treatment_plan:TP1", "relation_name": "疾病有治疗方案", "review_status": "active", "source_refs": ["DOC-x_p1_l1-5"]},
        {"source_node_id": "ENTITY:treatment_plan:TP1", "relation_code": "TREATMENT_PLAN_INCLUDES_MEDICATION", "target_node_id": "ENTITY:medication:M1", "relation_name": "方案含药品", "review_status": "draft", "source_refs": ["DOC-x_p1_l1-5"]},
    ],
}


class RelationListItemsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.graph_dir = root / "kg"
        self.graph_dir.mkdir()
        self.rules_path = root / "rules" / "managed_rules.json"
        self.rules_path.parent.mkdir()
        self.audit_path = root / "review" / "review_log.jsonl"
        (self.graph_dir / "REVIEW-TEST2.json").write_text(
            json.dumps(GRAPH2, ensure_ascii=False), encoding="utf-8"
        )
        self.rules_path.write_text("[]", encoding="utf-8")
        self.service = ReviewService(self.graph_dir, self.rules_path, self.audit_path)

    def _rel_items(self, **kwargs):
        return self.service.list_items("REVIEW-TEST2.json", item_type="relation", **kwargs)

    def test_relation_code_filter(self):
        items = self._rel_items(relation_code="DISEASE_HAS_SYMPTOM")
        self.assertEqual(len(items), 3)
        self.assertTrue(all(item["relation_code"] == "DISEASE_HAS_SYMPTOM" for item in items))

    def test_node_type_matches_relation_endpoints(self):
        codes = {item["item_id"] for item in self._rel_items(node_type="symptom")}
        self.assertEqual(codes, {
            "ENTITY:disease:D1|DISEASE_HAS_SYMPTOM|ENTITY:symptom:S1",
            "ENTITY:disease:D1|DISEASE_HAS_SYMPTOM|ENTITY:symptom:S2",
            "ENTITY:symptom:S1|DISEASE_HAS_SYMPTOM|ENTITY:disease:D1",
        })

    def test_node_type_filter_for_nodes_unchanged(self):
        items = self.service.list_items("REVIEW-TEST2.json", item_type="node", node_type="symptom")
        self.assertEqual({item["node_id"] for item in items}, {"ENTITY:symptom:S1", "ENTITY:symptom:S2"})

    def test_relation_items_carry_endpoint_types_and_flags(self):
        items = {item["item_id"]: item for item in self._rel_items()}
        suspect = items["ENTITY:symptom:S1|DISEASE_HAS_SYMPTOM|ENTITY:disease:D1"]
        self.assertEqual(suspect["source_node_type"], "symptom")
        self.assertEqual([f["code"] for f in suspect["flags"]], ["direction_suspect"])
        dirty = items["ENTITY:disease:D1|DISEASE_HAS_SYMPTOM|ENTITY:symptom:S2"]
        self.assertEqual(sorted(f["code"] for f in dirty["flags"]), ["endpoint_inactive", "no_source_refs"])
        loop = items["ENTITY:disease:D1|DISEASE_HAS_DIAGNOSTIC_EVIDENCE|ENTITY:disease:D1"]
        self.assertIn("self_loop", [f["code"] for f in loop["flags"]])
        pair_codes = [f["code"] for f in items["ENTITY:lab:L1|LAB_HAS_DIAGNOSTIC_EVIDENCE|ENTITY:diagnostic_evidence:E1"]["flags"]]
        self.assertIn("duplicate_pair", pair_codes)

    def test_direction_flags_use_real_templates(self):
        # 真实模板里 DISEASE_HAS_SYMPTOM 的端点是 disease→symptom；
        # 本 fixture 的干净边不应被标记方向可疑。
        clean = {item["item_id"]: item for item in self._rel_items()}
        self.assertEqual(
            [f["code"] for f in clean["ENTITY:disease:D1|DISEASE_HAS_SYMPTOM|ENTITY:symptom:S1"]["flags"]],
            [],
        )
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_relation_review_upgrade.py -v`
Expected: FAIL（`TypeError: list_items() got an unexpected keyword argument 'relation_code'`）

- [ ] **Step 3: 实现**

`src/review/service.py` 顶部 import 区追加：

```python
from .precheck import (
    duplicate_relation_pairs,
    load_endpoint_type_map,
    relation_flags,
)
```

`list_items` 签名（149 行）加参：

```python
    def list_items(
        self,
        graph_filename: str,
        status: Optional[str] = None,
        item_type: Optional[str] = None,
        node_type: Optional[str] = None,
        source_doc: Optional[str] = None,
        referenced_by_rule: Optional[bool] = None,
        q: Optional[str] = None,
        relation_code: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
```

relation 分支（180-205 行）整体替换为（用统一构造器，附加端点类型与 flags；端点类型映射和重复对只在需要时计算）：

```python
        endpoint_type_map: Dict[str, Dict[str, List[str]]] = {}
        duplicate_pairs: set = set()
        if item_type in (None, "", "relation"):
            endpoint_type_map = load_endpoint_type_map()
            duplicate_pairs = duplicate_relation_pairs(graph["relations"].values())
        if item_type in (None, "", "node"):
            for node_id, node in graph["nodes"].items():
                items.append({
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
                    "orphaned_from": list(node.get("orphaned_from") or []),
                    "orphaned": bool(node.get("orphaned_from")),
                })
        if item_type in (None, "", "relation"):
            for key, relation in graph["relations"].items():
                items.append(self._relation_item(
                    graph, key, relation, relation_counts, endpoint_type_map, duplicate_pairs,
                ))
```

在 `list_items` 上方新增统一构造器方法：

```python
    def _relation_item(
        self,
        graph: Dict[str, Any],
        key: Tuple[str, str, str],
        relation: Dict[str, Any],
        relation_counts: Counter,
        endpoint_type_map: Dict[str, Dict[str, List[str]]],
        duplicate_pairs: set,
    ) -> Dict[str, Any]:
        source_node = graph["nodes"].get(key[0]) or {}
        target_node = graph["nodes"].get(key[2]) or {}
        return {
            "item_type": "relation",
            "item_id": _relation_id(key),
            "source_node_id": key[0],
            "relation_code": key[1],
            "target_node_id": key[2],
            "node_type": "relation",
            "name": str(relation.get("relation_name") or key[1]),
            "status": _status(relation),
            "source_name": str(source_node.get("name") or key[0]),
            "source_code": str(source_node.get("code") or source_node.get("concept_code") or ""),
            "source_status": _status(source_node) if source_node else "missing",
            "source_node_type": str(source_node.get("node_type") or ""),
            "target_name": str(target_node.get("name") or key[2]),
            "target_code": str(target_node.get("code") or target_node.get("concept_code") or ""),
            "target_status": _status(target_node) if target_node else "missing",
            "target_node_type": str(target_node.get("node_type") or ""),
            "source_refs": list(relation.get("source_refs") or []),
            "dependent_rule_count": int(relation_counts.get(_relation_id(key), 0)),
            "updated_at": str(relation.get("reviewed_at") or ""),
            "orphaned_from": list(relation.get("orphaned_from") or []),
            "orphaned": bool(relation.get("orphaned_from")),
            "flags": relation_flags(relation, source_node, target_node, endpoint_type_map, duplicate_pairs),
        }
```

筛选块（206 行起）中两处替换——`node_type` 行替换为端点匹配语义，并在其后新增 `relation_code` 行：

```python
            if node_type:
                endpoint_types = (item.get("source_node_type"), item.get("target_node_type"))
                if item.get("node_type") != node_type and node_type not in endpoint_types:
                    continue
            if relation_code and item.get("relation_code") != relation_code:
                continue
```

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `pytest tests/test_relation_review_upgrade.py tests/test_graph_review_service.py -v`
Expected: 全部 PASS（旧用例证明 node 筛选语义未变）

Run: `pytest tests/ -x -q`
Expected: 全量 PASS。若有既有用例因 relation 条目新增字段/筛选语义失败，逐个核对语义后修测试或实现——不得为迁就旧断言弱化新语义，发现行为冲突时停下来在提交信息中说明。

- [ ] **Step 5: 提交**

```bash
git add src/review/service.py tests/test_relation_review_upgrade.py
git commit -m "feat: 审核条目接口支持关系类型筛选、端点类型匹配与预检 flags"
```

---

### Task 4: queue 与路由透传 relation_code

**Files:**
- Modify: `src/review/queue.py:32-78`（`build_queue`）
- Modify: `src/api/routes/kg_review.py:69-87`（`review_items`）、`187-209`（`review_queue`）
- Test: `tests/test_relation_review_upgrade.py`

**Interfaces:**
- Consumes: Task 3 的 `list_items(..., relation_code=...)`。
- Produces:
  - `build_queue(..., relation_code: Optional[str] = None)`——graph 分支透传。
  - `GET /api/kg/review/items?...&relation_code=` 与 `GET /api/review/queue?...&relation_code=` 查询参数。
  Task 6 前端依赖 `relation_code` 查询参数。

- [ ] **Step 1: 写失败测试**

追加：

```python
from src.review.queue import build_queue


class QueueRelationCodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.graph_dir = root / "kg"
        self.graph_dir.mkdir()
        self.rules_path = root / "rules" / "managed_rules.json"
        self.rules_path.parent.mkdir()
        self.audit_path = root / "review" / "review_log.jsonl"
        (self.graph_dir / "REVIEW-TEST2.json").write_text(
            json.dumps(GRAPH2, ensure_ascii=False), encoding="utf-8"
        )
        self.rules_path.write_text("[]", encoding="utf-8")
        self.service = ReviewService(self.graph_dir, self.rules_path, self.audit_path)

    def test_build_queue_filters_relation_code(self):
        queue = build_queue(
            service=self.service, graph_files=["REVIEW-TEST2.json"],
            item_type="graph_relation", relation_code="DISEASE_HAS_SYMPTOM",
        )
        self.assertTrue(queue["items"])
        self.assertTrue(all(item["relation_code"] == "DISEASE_HAS_SYMPTOM" for item in queue["items"]))

    def test_build_queue_summary_counts_relations(self):
        queue = build_queue(service=self.service, graph_files=["REVIEW-TEST2.json"], limit=300)
        summary = queue["summary"]
        self.assertEqual(summary["graph_relations"].get("draft"), 5)
        self.assertEqual(summary["graph_relations"].get("active"), 3)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_relation_review_upgrade.py::QueueRelationCodeTest -v`
Expected: FAIL（`build_queue() got an unexpected keyword argument 'relation_code'`）

- [ ] **Step 3: 实现**

`src/review/queue.py`：`build_queue` 签名在 `q` 后加 `relation_code: Optional[str] = None`；graph 分支的 `service.list_items(...)` 调用追加 `relation_code=relation_code`。

`src/api/routes/kg_review.py`：`review_items`（69 行）加 `relation_code: Optional[str] = None` 参数并传给 `service.list_items`；`review_queue`（187 行）同样加参并传给 `build_queue`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_relation_review_upgrade.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/review/queue.py src/api/routes/kg_review.py tests/test_relation_review_upgrade.py
git commit -m "feat: 审核队列与路由透传 relation_code 筛选参数"
```

---

### Task 5: 按病分组——groups 服务方法与端点

**Files:**
- Modify: `src/review/service.py`（新增常量与两个方法）
- Modify: `src/api/routes/kg_review.py`（新增两个 GET 路由，必须放在 `/relations/{relation_id}` 路由**之前**，与现有 `/relations/impact` 相邻）
- Test: `tests/test_relation_review_upgrade.py`

**Interfaces:**
- Consumes: Task 3 的 `_relation_item`、precheck 三函数、`ReviewError`、`_status` / `normalize_graph_filename`（service.py 现有导入）。
- Produces:
  - 模块常量 `CONTAINER_NODE_TYPES = ("treatment_plan", "care_pathway", "order_set", "lab", "exam")`。
  - `ReviewService.relation_groups(graph_filename, subject_type="disease") -> {"graph_filename", "subjects": [{node_id, name, display_name, code, node_type, review_status, out_relation_count, pending_count, suspect_count}]}`；`subject_type` ∈ `disease`（默认，含 disease+syndrome）| `syndrome` | `all`；排序 pending 降序、名称升序。
  - `ReviewService.relation_group_detail(graph_filename, subject_node_id) -> {"graph_filename", "subject": {...同上无计数}, "totals": {total, pending, suspect}, "sections": [...]}`；section = `{section_key, relation_code, relation_name, total, status_counts, items, containers}`，item = `_relation_item` 字段 + `graph_filename`；container = `{container_node_id, container_name, display_name, node_type, sections}`，嵌套 section 的 `section_key` = `"{container_node_id}:{relation_code}"`（全局唯一，前端靠它定位整组条目）。仅一层容器展开；容器自身出边同时保留在其所属直接 section。
  - `GET /api/kg/relations/groups?graph_filename=&subject_type=` 与 `GET /api/kg/relations/groups/{subject_node_id}?graph_filename=`（subject 不存在 → ReviewError 404）。
  Task 8 前端依赖以上响应结构。

- [ ] **Step 1: 写失败测试**

追加：

```python
class RelationGroupsTest(unittest.TestCase):
    def setUp(self):
        # 与 RelationListItemsTest 相同的 fixture 搭建
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.graph_dir = root / "kg"
        self.graph_dir.mkdir()
        self.rules_path = root / "rules" / "managed_rules.json"
        self.rules_path.parent.mkdir()
        self.audit_path = root / "review" / "review_log.jsonl"
        (self.graph_dir / "REVIEW-TEST2.json").write_text(
            json.dumps(GRAPH2, ensure_ascii=False), encoding="utf-8"
        )
        self.rules_path.write_text("[]", encoding="utf-8")
        self.service = ReviewService(self.graph_dir, self.rules_path, self.audit_path)

    def test_groups_default_subjects_are_disease_and_syndrome(self):
        groups = self.service.relation_groups("REVIEW-TEST2.json")
        self.assertEqual([s["node_id"] for s in groups["subjects"]], ["ENTITY:disease:D1"])
        subject = groups["subjects"][0]
        self.assertEqual(subject["out_relation_count"], 4)   # S1, S2, self-loop, TP1
        self.assertEqual(subject["pending_count"], 2)        # S2 draft, self-loop draft
        self.assertEqual(subject["suspect_count"], 2)        # S2 端点+无溯源, self-loop 自环+方向

    def test_groups_subject_type_all_includes_lab(self):
        groups = self.service.relation_groups("REVIEW-TEST2.json", subject_type="all")
        ids = {s["node_id"] for s in groups["subjects"]}
        self.assertIn("ENTITY:lab:L1", ids)

    def test_group_detail_sections_and_container_nesting(self):
        detail = self.service.relation_group_detail("REVIEW-TEST2.json", "ENTITY:disease:D1")
        by_code = {section["relation_code"]: section for section in detail["sections"]}
        self.assertEqual(by_code["DISEASE_HAS_SYMPTOM"]["total"], 2)
        self.assertEqual(by_code["DISEASE_HAS_DIAGNOSTIC_EVIDENCE"]["total"], 1)
        tp = by_code["DISEASE_HAS_TREATMENT_PLAN"]
        self.assertEqual(tp["total"], 1)                       # 容器自身这条边保留
        self.assertEqual(tp["containers"][0]["container_node_id"], "ENTITY:treatment_plan:TP1")
        nested = tp["containers"][0]["sections"]
        self.assertEqual([s["relation_code"] for s in nested], ["TREATMENT_PLAN_INCLUDES_MEDICATION"])
        self.assertEqual(nested[0]["section_key"], "ENTITY:treatment_plan:TP1:TREATMENT_PLAN_INCLUDES_MEDICATION")
        self.assertEqual(nested[0]["items"][0]["graph_filename"], "REVIEW-TEST2.json")
        self.assertEqual(detail["totals"]["total"], 5)

    def test_group_detail_unknown_subject_404(self):
        with self.assertRaises(ReviewError) as ctx:
            self.service.relation_group_detail("REVIEW-TEST2.json", "ENTITY:disease:NOPE")
        self.assertEqual(ctx.exception.status_code, 404)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_relation_review_upgrade.py::RelationGroupsTest -v`
Expected: FAIL（`AttributeError: 'ReviewService' object has no attribute 'relation_groups'`）

- [ ] **Step 3: 实现**

`src/review/service.py` 模块级常量（放在 `_relation_key` 附近）：

```python
# 按病分组视图中，目标端为这些类型时展开其出边作为嵌套小节（仅一层）。
CONTAINER_NODE_TYPES: Tuple[str, ...] = ("treatment_plan", "care_pathway", "order_set", "lab", "exam")
```

`ReviewService` 新增三个方法（放在 `list_items` 与 `node_detail` 之间）：

```python
    def _section_summary(
        self, section_key: str, relation_code: str, relation_name: str,
        items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "section_key": section_key,
            "relation_code": relation_code,
            "relation_name": relation_name,
            "total": len(items),
            "status_counts": dict(Counter(item["status"] for item in items)),
            "items": items,
        }

    def relation_groups(self, graph_filename: str, subject_type: str = "disease") -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        if subject_type == "syndrome":
            allowed: Optional[Tuple[str, ...]] = ("syndrome",)
        elif subject_type == "all":
            allowed = None
        else:
            allowed = ("disease", "syndrome")
        endpoint_type_map = load_endpoint_type_map()
        duplicate_pairs = duplicate_relation_pairs(graph["relations"].values())
        stats: Dict[str, Counter] = {}
        for key, relation in graph["relations"].items():
            source = graph["nodes"].get(key[0])
            if source is None:
                continue
            source_type = str(source.get("node_type") or "")
            if allowed is not None and source_type not in allowed:
                continue
            entry = stats.setdefault(key[0], Counter())
            entry["total"] += 1
            flags = relation_flags(
                relation, source, graph["nodes"].get(key[2]) or {},
                endpoint_type_map, duplicate_pairs,
            )
            if _status(relation) in ("draft", "stale"):
                entry["pending"] += 1
            if flags:
                entry["suspect"] += 1
        subjects = []
        for node_id, entry in stats.items():
            node = graph["nodes"][node_id]
            subjects.append({
                "node_id": node_id,
                "name": str(node.get("name") or node_id),
                "display_name": str(node.get("display_name") or node.get("name") or node_id),
                "code": str(node.get("code") or node.get("concept_code") or ""),
                "node_type": str(node.get("node_type") or ""),
                "review_status": _status(node),
                "out_relation_count": int(entry["total"]),
                "pending_count": int(entry["pending"]),
                "suspect_count": int(entry["suspect"]),
            })
        subjects.sort(key=lambda item: (-item["pending_count"], item["name"]))
        return {"graph_filename": filename, "subjects": subjects}

    def relation_group_detail(self, graph_filename: str, subject_node_id: str) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        subject = graph["nodes"].get(subject_node_id)
        if subject is None:
            raise ReviewError("节点不存在", 404)
        endpoint_type_map = load_endpoint_type_map()
        duplicate_pairs = duplicate_relation_pairs(graph["relations"].values())
        _, relation_counts, _ = self._dependency_index(filename)

        def item_for(key: Tuple[str, str, str], relation: Dict[str, Any]) -> Dict[str, Any]:
            return {
                **self._relation_item(
                    graph, key, relation, relation_counts, endpoint_type_map, duplicate_pairs,
                ),
                "graph_filename": filename,
            }

        sections: Dict[str, Dict[str, Any]] = {}
        container_ids_by_section: Dict[str, List[str]] = {}
        container_items: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for key, relation in graph["relations"].items():
            if key[0] != subject_node_id:
                continue
            entry = item_for(key, relation)
            section = sections.setdefault(
                key[1], self._section_summary(key[1], key[1], entry["name"], []),
            )
            section["items"].append(entry)
            target = graph["nodes"].get(key[2]) or {}
            if str(target.get("node_type") or "") not in CONTAINER_NODE_TYPES:
                continue
            ids = container_ids_by_section.setdefault(key[1], [])
            if key[2] not in ids:
                ids.append(key[2])
            for ckey, crelation in graph["relations"].items():
                if ckey[0] != key[2]:
                    continue
                container_items.setdefault((key[2], ckey[1]), []).append(item_for(ckey, crelation))
        ordered_keys = sorted(sections, key=lambda c: (-len(sections[c]["items"]), c))
        sections_out = []
        for code in ordered_keys:
            section = sections[code]
            containers = []
            for container_id in container_ids_by_section.get(code, []):
                node = graph["nodes"].get(container_id) or {}
                nested = []
                for (cid, ccode), items in container_items.items():
                    if cid != container_id:
                        continue
                    nested.append(self._section_summary(
                        f"{cid}:{ccode}", ccode,
                        items[0]["name"] if items else ccode, items,
                    ))
                nested.sort(key=lambda item: (-item["total"], item["relation_code"]))
                containers.append({
                    "container_node_id": container_id,
                    "container_name": str(node.get("name") or container_id),
                    "display_name": str(node.get("display_name") or node.get("name") or container_id),
                    "node_type": str(node.get("node_type") or ""),
                    "sections": nested,
                })
            section["containers"] = containers
            sections_out.append(section)
        totals: Counter = Counter()
        for section in sections_out:
            totals.update(section["status_counts"])
        suspect = sum(
            1 for section in sections_out for item in section["items"] if item["flags"]
        )
        return {
            "graph_filename": filename,
            "subject": {
                "node_id": subject_node_id,
                "name": str(subject.get("name") or subject_node_id),
                "display_name": str(subject.get("display_name") or subject.get("name") or subject_node_id),
                "code": str(subject.get("code") or subject.get("concept_code") or ""),
                "node_type": str(subject.get("node_type") or ""),
                "review_status": _status(subject),
            },
            "totals": {
                "total": sum(totals.values()),
                "pending": int(totals.get("draft", 0) + totals.get("stale", 0)),
                "suspect": suspect,
            },
            "sections": sections_out,
        }
```

`src/api/routes/kg_review.py`——在 `relation_impact`（140-149 行）之后、`relation_detail`（152 行）之前插入：

```python
@router.get("/relations/groups")
async def relation_groups(graph_filename: str, subject_type: str = "disease"):
    return service.relation_groups(graph_filename, subject_type=subject_type)


@router.get("/relations/groups/{subject_node_id}")
async def relation_group_detail(subject_node_id: str, graph_filename: str):
    return service.relation_group_detail(graph_filename, subject_node_id)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_relation_review_upgrade.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/review/service.py src/api/routes/kg_review.py tests/test_relation_review_upgrade.py
git commit -m "feat: 按病分组接口——主体列表与知识卡片 sections（含一层容器展开）"
```

---

### Task 6: 前端——「关系审核」独立页签与筛选

**Files:**
- Modify: `src/api/static/app.js`（状态区 ~49-54 行、`renderReviewCenter` 3370-3578 行）

**Interfaces:**
- Consumes: Task 4 的 `relation_code` 查询参数；Task 3 的 relation 条目字段 `source_node_type` / `target_node_type`；现有 `RELATION_LABELS` / `typeName` / `esc` / `$` / `api`。
- Produces（Task 7、8 复用）：
  - 模块常量 `REVIEW_TABS`（从 `renderReviewCenter` 内提取到 `REVIEW_TYPE_LABELS` 之后）。
  - state 字段：`reviewFilters.relationCode`、`reviewRelationCodeOptions: []`。
  - state 字段 `reviewSuspectOnly`（Task 7 使用，本任务不渲染）。

本任务无 pytest；每步用浏览器手工验证（`bash start-api.sh` 起服务后开审核台）。

- [ ] **Step 1: 状态与常量**

`reviewFilters` 初始化（51 行）改为：

```js
    reviewFilters: { graph: "", sourceDoc: "", nodeType: "", relationCode: "" },
```

紧邻加一行：

```js
    reviewRelationCodeOptions: [],
```

在 `REVIEW_TYPE_LABELS`（2900 行）后新增模块常量（`renderReviewCenter` 内的旧 `tabs` 数组删除，改引此常量）：

```js
  const REVIEW_TABS = [
    ["graph", "待审图谱"], ["relations", "关系审核"], ["rules", "待审规则"], ["blocked", "被阻断"],
    ["stale", "待复核"], ["active", "已通过"], ["orphan", "孤儿条目"],
    ["audit", "审核记录"],
  ];
```

- [ ] **Step 2: 页签队列与客户端过滤**

`renderReviewCenter` 内（3375-3377 行）`queueItemType` 替换为：

```js
    const queueItemType = tab === "rules" || tab === "blocked"
      ? "rule"
      : tab === "relations"
        ? "graph_relation"
        : tab === "graph"
          ? "graph_node"
          : (["stale", "orphan"].includes(tab) ? "graph" : "");
```

`filterParams`（3378-3382 行）末尾追加一项：

```js
      filters.relationCode ? `&relation_code=${encodeURIComponent(filters.relationCode)}` : "",
```

客户端过滤（3400-3407 行）graph 行替换、新增 relations 行：

```js
      if (tab === "graph") return item.item_type === "graph_node" && ["draft", "stale"].includes(item.status);
      if (tab === "relations") return item.item_type === "graph_relation" && ["draft", "stale"].includes(item.status);
```

页签渲染（3417-3422 行）替换为带待办徽标的版本：

```js
    const relationPending = Number(queue.summary?.graph_relations?.draft || 0)
      + Number(queue.summary?.graph_relations?.stale || 0);
    const tabHtml = REVIEW_TABS.map(([key, label]) => `<button class="review-tab ${tab === key ? "active" : ""}" data-review-tab="${key}">${label}${key === "relations" && relationPending ? `（${relationPending}）` : ""}</button>`).join("");
```

- [ ] **Step 3: 筛选选项与筛选栏**

nodeType 选项块（3395-3399 行）替换为（relations 页签按端点类型收集，并新增关系类型选项收集）：

```js
    if (!filters.nodeType) {
      const types = new Set();
      allItems.forEach((item) => {
        if (item.item_type === "graph_relation") {
          if (item.source_node_type) types.add(item.source_node_type);
          if (item.target_node_type) types.add(item.target_node_type);
        } else if (item.item_type === "graph_node" && item.node_type) {
          types.add(item.node_type);
        }
      });
      state.reviewNodeTypeOptions = [...types].sort();
    }
    if (!filters.relationCode) {
      const counts = new Map();
      allItems.filter((item) => item.item_type === "graph_relation").forEach((item) => {
        const code = item.relation_code || "";
        if (!code) return;
        const entry = counts.get(code) || { value: code, label: RELATION_LABELS[code] || item.name || code, count: 0 };
        entry.count += 1;
        counts.set(code, entry);
      });
      state.reviewRelationCodeOptions = [...counts.values()].sort((a, b) => b.count - a.count);
    }
```

筛选栏（3483-3497 行）在「全部类型」select 之后插入（仅 relations 页签渲染）：

```js
            ${tab === "relations" ? `<select id="reviewFilterRelationCode" title="按关系语义类型筛选（配合全选可批量审核同一语义的关系）">
              <option value="">全部关系类型</option>
              ${(state.reviewRelationCodeOptions || []).map((option) => `<option value="${esc(option.value)}" ${filters.relationCode === option.value ? "selected" : ""}>${esc(option.label)}（${option.count}）</option>`).join("")}
            </select>` : ""}
```

事件绑定区（3540-3542 行）追加：

```js
    bindReviewFilter("#reviewFilterRelationCode", "relationCode");
```

「清除筛选」重置（3543-3546 行）改为：

```js
    $("#reviewFilterClear")?.addEventListener("click", () => {
      state.reviewFilters = { graph: "", sourceDoc: "", nodeType: "", relationCode: "" };
      renderReviewCenter();
    });
```

- [ ] **Step 4: 手工验证**

Run: `bash start-api.sh`（按其输出打开页面）
验证清单：
1. 「关系审核」页签出现且只列关系行（语义行 `甲 ─标签→ 乙`），页签带待办数（当前数据应为 105 待审：draft 105 + stale 0，以「待审图谱」页签不再含关系为准）；
2. 「待审图谱」页签只剩节点；
3. 关系类型下拉选「构成诊断依据」→ 列表只剩该类；全选 → 批量通过可用；
4. 实体类型下拉选「疾病」→ 关系列表显示两端含疾病实体的边（不再整体消失）；
5. 「清除筛选」同时清掉关系类型。

- [ ] **Step 5: 提交**

```bash
git add src/api/static/app.js
git commit -m "feat: 审核台新增关系审核独立页签与关系类型/端点类型筛选"
```

---

### Task 7: 前端——可疑徽章、只看可疑、类型×状态概览

**Files:**
- Modify: `src/api/static/app.js`（`RELATION_LABELS` 附近、`renderReviewCenter`、rows 渲染）
- Modify: `src/api/static/style.css`（文件末尾追加）

**Interfaces:**
- Consumes: Task 3 relation 条目的 `flags: [{code, message}]`；现有 `REVIEW_LABELS`（状态中文）、`table()` 帮助函数。
- Produces（Task 8 复用）：模块常量 `FLAG_LABELS`、`flagChipsHtml(item)`、`relationMatrixHtml(items)`、state 字段 `reviewSuspectOnly`。

- [ ] **Step 1: 常量与帮助函数**

`FIELD_LABELS`（2910 行）后新增：

```js
  const FLAG_LABELS = {
    endpoint_inactive: "端点未通过", no_source_refs: "无溯源",
    direction_suspect: "方向可疑", self_loop: "自环", duplicate_pair: "重复连线",
  };
  function flagChipsHtml(item) {
    return (item.flags || []).map((flag) => `<span class="flag-chip" title="${esc(flag.message)}">⚠ ${esc(FLAG_LABELS[flag.code] || flag.code)}</span>`).join(" ");
  }
  function relationMatrixHtml(items) {
    const relItems = items.filter((item) => item.item_type === "graph_relation");
    if (!relItems.length) return "";
    const statuses = ["draft", "stale", "active", "rejected", "retired"];
    const byCode = new Map();
    relItems.forEach((item) => {
      const code = item.relation_code || "-";
      if (!byCode.has(code)) byCode.set(code, Object.fromEntries(statuses.map((s) => [s, 0])));
      byCode.get(code)[item.status] = (byCode.get(code)[item.status] || 0) + 1;
    });
    const rows = [...byCode.entries()].map(([code, counts]) =>
      `<tr><td>${esc(RELATION_LABELS[code] || code)}</td>${statuses.map((s) => `<td class="num ${counts[s] ? "" : "zero"}">${counts[s] || "·"}</td>`).join("")}</tr>`).join("");
    return `<details class="relation-matrix"><summary>关系类型 × 状态概览（当前列表 ${relItems.length} 条）</summary>
      ${table(["关系类型", ...statuses.map((s) => REVIEW_LABELS[s] || s)], rows)}</details>`;
  }
```

state 区（51 行附近）加：

```js
    reviewSuspectOnly: false,
```

- [ ] **Step 2: 列表行渲染徽章 + 可疑过滤**

rows.map 中（3465 行 `${item.orphaned_from...}` 行之前）插入：

```js
          ${item.flags?.length ? `<div class="flag-line">${flagChipsHtml(item)}</div>` : ""}
```

items 链式过滤（3408 行 `.filter((item) => {` 内、query 判断之前）插入：

```js
      if (tab === "relations" && state.reviewSuspectOnly && !(item.flags || []).length) return false;
```

- [ ] **Step 3: 只看可疑开关与概览挂载**

筛选栏（Task 6 插入的 relation select 之后）追加：

```js
            ${tab === "relations" ? `<label class="suspect-toggle" title="只显示系统预检标记了可疑项的关系">
              <input type="checkbox" id="reviewSuspectOnly" ${state.reviewSuspectOnly ? "checked" : ""}> 只看可疑（${allItems.filter((item) => item.item_type === "graph_relation" && (item.flags || []).length).length}）
            </label>` : ""}
```

content.innerHTML 模板中 `${truncated}` 之后、`<div class="review-layout">` 之前插入：

```js
      ${tab === "relations" ? relationMatrixHtml(items) : ""}
```

事件绑定区追加：

```js
    $("#reviewSuspectOnly")?.addEventListener("change", (event) => {
      state.reviewSuspectOnly = event.target.checked;
      renderReviewCenter();
    });
```

- [ ] **Step 4: 样式**

`style.css` 末尾追加：

```css
/* 关系审核：预检徽章 / 只看可疑 / 类型×状态概览 */
.flag-line { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 2px; }
.flag-chip { font-size: 11px; background: var(--warn-soft); color: var(--warn); border-radius: 4px; padding: 1px 6px; cursor: help; }
.suspect-toggle { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; white-space: nowrap; }
.relation-matrix { margin-bottom: 8px; }
.relation-matrix summary { cursor: pointer; font-size: 12px; color: var(--muted); }
.relation-matrix td.num { text-align: right; font-variant-numeric: tabular-nums; }
.relation-matrix td.zero { color: var(--muted); }
```

- [ ] **Step 5: 手工验证**

刷新页面（注意强刷或等 Task 9 的版本号升级）：
1. 「关系审核」列表中无溯源关系显示「⚠ 无溯源」徽章，悬停有说明；方向可疑的边（真实图谱中的"疾病→疾病"诊断依据边应命中）显示「⚠ 方向可疑」；
2. 「只看可疑（N）」勾选后列表只剩带徽章条目，计数 N 与列表一致；
3. 展开「关系类型 × 状态概览」，行列数字与列表一致；矩阵在非 relations 页签不出现。

- [ ] **Step 6: 提交**

```bash
git add src/api/static/app.js src/api/static/style.css
git commit -m "feat: 关系可疑预检徽章、只看可疑开关与关系类型×状态概览"
```

---

### Task 8: 前端——按疾病分组知识卡片

**Files:**
- Modify: `src/api/static/app.js`（`REVIEW_TABS` 加页签、`renderReviewCenter` 早分支、新增分组视图函数组）
- Modify: `src/api/static/style.css`（末尾追加）

**Interfaces:**
- Consumes: Task 5 的 groups 两个端点及响应结构（`section_key` 唯一定位整组、item 含 `graph_filename`）；Task 6 的 `REVIEW_TABS`；Task 7 的 `flagChipsHtml`；现有 `detailHeadHtml` / `relationSemHtml` / `relationLabel` / `reviewBadge` / `openReasonDialog`（multi 路径走 `/api/review/bulk`）/ `relationEndpointBlocked` / `itemPayload` / `submitReviewAction` / `reviewReviewer` / `ACTION_DONE_LABELS`。
- Produces: 页签 `bydisease`；state 字段 `byDisease: {subject, search}`、`byDiseaseItems: Map`、`byDiseaseSections: []`；函数 `renderByDisease` / `loadByDiseaseCard` / `subjectCardHtml` / `diseaseSectionHtml` / `nestedSectionHtml` / `diseaseRelationRowHtml` / `collectSectionItems` / `bindByDiseaseCardActions`。

- [ ] **Step 1: 页签注册与早分支**

`REVIEW_TABS` 中 `["relations", "关系审核"],` 后插入 `["bydisease", "按疾病分组"],`。

`renderReviewCenter` 函数体第一行 `const tab = state.reviewTab || "graph";` 之后插入：

```js
    if (tab === "bydisease") { await renderByDisease(); return; }
```

- [ ] **Step 2: 分组视图函数组**

在 `renderReviewCenter` 之后新增（整块一次写入）：

```js
  // ==== 按疾病分组：知识卡片视图（单图谱）====
  async function renderByDisease() {
    const tab = state.reviewTab || "graph";
    const filters = state.reviewFilters || {};
    const graphName = filters.graph || state.graphFilename || "";
    state.byDisease = state.byDisease || { subject: "", search: "" };
    const tabHtml = REVIEW_TABS.map(([key, label]) => `<button class="review-tab ${tab === key ? "active" : ""}" data-review-tab="${key}">${label}</button>`).join("");
    if (!graphName) {
      content.innerHTML = `<div class="review-tabs">${tabHtml}</div><div class="empty">按疾病分组视图按单图谱展示，请先在筛选中选择目的图谱。</div>`;
      bindReviewTabs();
      return;
    }
    let groups;
    try {
      groups = await api(`/api/kg/relations/groups?graph_filename=${encodeURIComponent(graphName)}&subject_type=disease`);
    } catch (error) {
      content.innerHTML = `<div class="review-tabs">${tabHtml}</div><div class="empty">加载分组失败：${esc(error.message)}</div>`;
      bindReviewTabs();
      return;
    }
    const search = (state.byDisease.search || "").trim().toLowerCase();
    const subjects = (groups.subjects || []).filter((s) => !search || `${s.name} ${s.code}`.toLowerCase().includes(search));
    const hint = filters.graph ? "" : `<div class="muted trunc-note">按疾病分组视图按单图谱展示，当前：${esc(graphName)}</div>`;
    content.innerHTML = `<div class="review-tabs">${tabHtml}</div>${hint}
      <div class="by-disease-layout">
        <div class="subject-list">
          <input id="byDiseaseSearch" type="search" value="${esc(state.byDisease.search || "")}" placeholder="搜索疾病 / 证候 / 编码…">
          <div class="subject-list-scroll">
            ${subjects.map(subjectCardHtml).join("") || '<div class="empty">没有匹配的主体节点</div>'}
          </div>
        </div>
        <div class="review-detail" id="byDiseaseCard">
          <div class="empty">在左侧选择疾病 / 证候，核对它的知识网<br><span class="muted">症见、检验、检查、诊断依据、用药会按关系类型分组展示</span></div>
        </div>
      </div>`;
    bindReviewTabs();
    let searchTimer = null;
    $("#byDiseaseSearch").oninput = (event) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        state.byDisease.search = event.target.value;
        renderByDisease();
      }, 250);
    };
    content.querySelectorAll(".subject-card").forEach((card) => card.onclick = () => loadByDiseaseCard(card.dataset.subject));
    if (state.byDisease.subject && subjects.some((s) => s.node_id === state.byDisease.subject)) {
      await loadByDiseaseCard(state.byDisease.subject);
    } else {
      state.byDisease.subject = "";
    }
  }

  function subjectCardHtml(s) {
    return `<div class="subject-card ${state.byDisease?.subject === s.node_id ? "selected" : ""}" data-subject="${esc(s.node_id)}">
      <strong>${esc(s.display_name || s.name)}</strong>
      <div class="sub-meta">
        <span>${esc(typeName[s.node_type] || s.node_type || "-")}</span>
        ${miniStatus(s.review_status)}
        <span>出边 ${s.out_relation_count}</span>
        ${s.pending_count ? `<span class="pending-chip">待审 ${s.pending_count}</span>` : '<span class="muted">已清</span>'}
        ${s.suspect_count ? `<span class="pending-chip warn">⚠ ${s.suspect_count}</span>` : ""}
      </div>
    </div>`;
  }

  async function loadByDiseaseCard(nodeId) {
    const filters = state.reviewFilters || {};
    const graphName = filters.graph || state.graphFilename || "";
    state.byDisease.subject = nodeId;
    content.querySelectorAll(".subject-card").forEach((card) => card.classList.toggle("selected", card.dataset.subject === nodeId));
    const mount = $("#byDiseaseCard");
    if (!mount) return;
    mount.innerHTML = '<div class="empty">加载知识卡片…</div>';
    let detail;
    try {
      detail = await api(`/api/kg/relations/groups/${encodeURIComponent(nodeId)}?graph_filename=${encodeURIComponent(graphName)}`);
    } catch (error) {
      mount.innerHTML = `<div class="empty">加载失败：${esc(error.message)}</div>`;
      return;
    }
    const sections = detail.sections || [];
    state.byDiseaseSections = sections;
    state.byDiseaseItems = new Map();
    sections.forEach((section) => {
      (section.items || []).forEach((item) => state.byDiseaseItems.set(item.item_id, item));
      (section.containers || []).forEach((container) => (container.sections || []).forEach((nested) => (nested.items || []).forEach((item) => state.byDiseaseItems.set(item.item_id, item))));
    });
    const subject = detail.subject || {};
    mount.innerHTML = `
      ${detailHeadHtml({ item_type: "graph_node", node_id: nodeId, name: subject.display_name || subject.name || nodeId, code: subject.code || "", status: subject.review_status || "draft", graph_filename: detail.graph_filename || graphName, node_type: subject.node_type || "" })}
      <div class="detail-sub mono muted">出边 ${detail.totals?.total ?? 0} 条 · 待审 ${detail.totals?.pending ?? 0} · 可疑 ${detail.totals?.suspect ?? 0}</div>
      ${sections.map((section) => diseaseSectionHtml(section)).join("") || '<div class="empty">该节点没有出边关系</div>'}`;
    bindByDiseaseCardActions(mount);
  }

  function diseaseSectionHtml(section) {
    const relItems = section.items || [];
    const pending = relItems.filter((item) => ["draft", "stale"].includes(item.status));
    const containers = (section.containers || []).map((container) => `
      <details class="container-block">
        <summary>${esc(container.display_name || container.container_name)} · ${container.sections.length} 组</summary>
        ${(container.sections || []).map((nested) => nestedSectionHtml(container, nested)).join("")}
      </details>`).join("");
    return `<div class="kg-section">
      <div class="kg-section-head">
        <h4>${esc(relationLabel(section))}</h4>
        <span class="mono muted">${esc(section.relation_code)}</span>
        <span class="muted">${section.total} 条 · 待审 ${pending.length}</span>
        <div class="actions">
          ${pending.length ? `<button class="btn sm primary" data-section-action="approve" data-section-key="${esc(section.section_key)}">整组通过</button>
          <button class="btn sm danger" data-section-action="reject" data-section-key="${esc(section.section_key)}">整组拒绝</button>` : '<span class="muted">无待审</span>'}
        </div>
      </div>
      <table><tbody>${relItems.map((item) => diseaseRelationRowHtml(item)).join("")}</tbody></table>
      ${containers}
    </div>`;
  }

  function nestedSectionHtml(container, nested) {
    const pending = (nested.items || []).filter((item) => ["draft", "stale"].includes(item.status));
    if (!pending.length && !(nested.items || []).length) return "";
    return `<div class="kg-section nested">
      <div class="kg-section-head">
        <h4>${esc(container.display_name || container.container_name)} · ${esc(relationLabel(nested))}</h4>
        <span class="mono muted">${esc(nested.relation_code)}</span>
        <span class="muted">${nested.total} 条 · 待审 ${pending.length}</span>
        <div class="actions">
          ${pending.length ? `<button class="btn sm primary" data-section-action="approve" data-section-key="${esc(nested.section_key)}">整组通过</button>
          <button class="btn sm danger" data-section-action="reject" data-section-key="${esc(nested.section_key)}">整组拒绝</button>` : ""}
        </div>
      </div>
      <table><tbody>${(nested.items || []).map((item) => diseaseRelationRowHtml(item)).join("")}</tbody></table>
    </div>`;
  }

  function diseaseRelationRowHtml(item) {
    const actions = [];
    if (["draft", "stale"].includes(item.status)) {
      actions.push(`<button class="btn sm primary" data-rel-action="approve" data-rel-id="${esc(item.item_id)}">通过</button>`);
      actions.push(`<button class="btn sm danger" data-rel-action="reject" data-rel-id="${esc(item.item_id)}">拒绝</button>`);
    } else if (item.status === "active") {
      actions.push(`<button class="btn sm" data-rel-action="retire" data-rel-id="${esc(item.item_id)}">失效</button>`);
    } else {
      actions.push(`<button class="btn sm" data-rel-action="restore" data-rel-id="${esc(item.item_id)}">恢复</button>`);
    }
    return `<tr>
      <td>${relationSemHtml(item)}</td>
      <td>${reviewBadge(item.status)}</td>
      <td>${flagChipsHtml(item)}</td>
      <td class="nowrap"><div class="actions">${actions.join(" ")}</div></td>
    </tr>`;
  }

  function collectSectionItems(sectionKey) {
    for (const section of state.byDiseaseSections || []) {
      if (section.section_key === sectionKey) {
        const items = [...(section.items || [])];
        (section.containers || []).forEach((container) => (container.sections || []).forEach((nested) => items.push(...(nested.items || []))));
        return items;
      }
      for (const container of section.containers || []) {
        for (const nested of container.sections || []) {
          if (nested.section_key === sectionKey) return [...(nested.items || [])];
        }
      }
    }
    return [];
  }

  function bindByDiseaseCardActions(mount) {
    const refresh = async () => {
      await renderByDisease();
    };
    mount.onclick = async (event) => {
      const single = event.target.closest("[data-rel-action]");
      if (single) {
        const item = state.byDiseaseItems?.get(single.dataset.relId);
        if (!item) return;
        const action = single.dataset.relAction;
        if (action === "reject" || action === "retire") { openReasonDialog(item, action); return; }
        try {
          if (action === "approve" && relationEndpointBlocked(item)) {
            toast("该关系的端点节点尚未全部「已通过」，请先审核两端节点", true);
            return;
          }
          await submitReviewAction(item, action, "");
          toast(`${ACTION_DONE_LABELS[action] || "已处理"}：${reviewItemTitle(item)}`);
          await refresh();
        } catch (error) { toast(error.message, true); }
        return;
      }
      const sectionBtn = event.target.closest("[data-section-action]");
      if (sectionBtn) {
        const action = sectionBtn.dataset.sectionAction;
        let pending = collectSectionItems(sectionBtn.dataset.sectionKey)
          .filter((item) => ["draft", "stale"].includes(item.status));
        if (!pending.length) return;
        if (action === "reject") { openReasonDialog({ multi: pending }, "reject"); return; }
        const blocked = pending.filter(relationEndpointBlocked);
        if (blocked.length) {
          const rest = pending.length - blocked.length;
          const proceed = confirm(
            `${blocked.length} 条关系的端点节点还不是「已通过」，直接提交会被拒绝。` +
            (rest ? `\n\n确定后仅提交其余 ${rest} 条；` : "\n\n当前没有可提交条目；") + "取消则不做操作。"
          );
          if (!proceed) return;
          pending = pending.filter((item) => !relationEndpointBlocked(item));
          if (!pending.length) { toast("没有可提交的条目，请先审核相关端点节点", true); return; }
        }
        try {
          const result = await api("/api/review/bulk", { json: {
            items: pending.map(itemPayload),
            action,
            reviewer: reviewReviewer(),
            note: "",
          }});
          toast(`整组完成：成功 ${result.succeeded}，失败 ${result.failed}`, result.failed > 0);
          await refresh();
        } catch (error) { toast(error.message, true); }
      }
    };
  }
```

要点：`openReasonDialog` 成功后回调 `renderReviewCenter()`，bydisease 分支会走 `renderByDisease()`，其中保留 `state.byDisease.subject` 自动重载卡片——拒绝/失效流程的界面刷新因此免处理。

- [ ] **Step 3: 样式**

`style.css` 末尾追加：

```css
/* 按疾病分组：知识卡片 */
.by-disease-layout { display: grid; grid-template-columns: 300px 1fr; gap: 12px; align-items: start; }
.subject-list { display: flex; flex-direction: column; gap: 8px; }
.subject-list-scroll { display: flex; flex-direction: column; gap: 6px; max-height: calc(100vh - 320px); overflow: auto; }
.subject-card { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; cursor: pointer; background: #fff; }
.subject-card.selected { border-color: var(--accent); background: var(--accent-soft); }
.subject-card .sub-meta { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 4px; font-size: 12px; color: var(--muted); }
.pending-chip { font-size: 11px; background: var(--warn-soft); color: var(--warn); border-radius: 999px; padding: 1px 8px; }
.pending-chip.warn { background: var(--danger-soft); color: var(--danger); }
.kg-section { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; background: #fff; }
.kg-section.nested { margin: 8px 0 0; }
.kg-section-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
.kg-section-head h4 { margin: 0; }
.container-block { margin-top: 8px; }
.container-block > summary { cursor: pointer; font-size: 12px; color: var(--accent-dark); }
@media (max-width: 960px) { .by-disease-layout { grid-template-columns: 1fr; } }
```

- [ ] **Step 4: 手工验证**

1. 「按疾病分组」页签：左列列出疾病/证候（按待审数降序），搜索"肥厚"可过滤；
2. 选「其他肥厚型心肌病」→ 卡片按 sections 分组：症见 / 需开展检验 / 构成诊断依据 / 疾病有治疗方案；展开治疗方案容器 → 见「XXX治疗方案 · 方案含药品」嵌套组；
3. 单条「通过」→ 行状态变化、左列待审计数减少；
4. 整组通过（含端点未通过的组）→ 弹确认并只提交可用条目；
5. 整组拒绝 → 原因码弹窗，选择原因后组内条目变为已拒绝；卡片与左列计数同步刷新；
6. 未选图谱筛选时显示"按单图谱展示，当前：…"提示。

- [ ] **Step 5: 提交**

```bash
git add src/api/static/app.js src/api/static/style.css
git commit -m "feat: 按疾病分组知识卡片视图（语义分组、容器展开、整组与单条审核）"
```

---

### Task 9: 缓存版本号升级 + 全量回归 + 手动 e2e

**Files:**
- Modify: `src/api/static/index.html:84`

- [ ] **Step 1: 升级 app.js 缓存版本号**

`index.html` 84 行：

```html
  <script src="/static/app.js?v=20261112relationrev"></script>
```

- [ ] **Step 2: 全量 pytest**

Run: `pytest tests/ -q`
Expected: 全部 PASS；如有失败逐个修复后重跑。

- [ ] **Step 3: 手动 e2e（按规格操作剧本）**

`bash start-api.sh` 起服务，强刷页面后：
1. 审核台 → 「关系审核」：待办徽标 ≈105；「只看可疑」能过滤出无溯源/方向可疑边（真实图谱应命中 DISEASE_HAS_DIAGNOSTIC_EVIDENCE 的疾病→疾病边）；
2. 拒绝一条方向可疑边（原因码「关系方向错误」）→ 若有规则依赖，规则页签「被阻断」出现对应规则；
3. 关系类型批量通过一组；「按疾病分组」打开肥厚型心肌病卡片逐组核对；
4. 「已通过」页签关系行仍显示语义行与徽章（回看场景未回归）。

- [ ] **Step 4: 提交**

```bash
git add src/api/static/index.html
git commit -m "chore: 升级 app.js 缓存版本号（关系审核升级上线）"
```

---

## Self-Review 记录

- 规格覆盖：A→Task 6；B→Task 3/4/6；C→Task 1/2/3/7；D→Task 5/8；E→Task 7（矩阵）+ Task 8（整组通过护栏，沿用既有批量确认模式）。无遗漏。
- 类型一致性：`section_key` 服务端（Task 5）与前端 `data-section-key`（Task 8）一致；flags 形状 `{code, message}` 三处一致；`relation_groups`/`relation_group_detail` 响应字段与前端读取一致。
- 已知取舍：groups 详情对每个容器边全表扫描容器出边（989 关系×~120 容器，最坏 ~10 万次内存遍历，可接受）；真实图谱数据在 `KG-GENERIC_GUIDELINE_V1.json`（421 节点/989 关系），tcm/stemi/lymphoma 当前为空图谱，e2e 以主图谱为准。
