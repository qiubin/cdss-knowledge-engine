"""疑似重复检测：同类型同名分组 + 标准码识别 + 推荐保留节点。"""
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi import HTTPException

from src.api.routes.kg import find_duplicate_groups, _is_standard_code


def _node(node_id, code, name, node_type="disease", status="draft", refs=None):
    return {"id": node_id, "node_type": node_type, "code": code, "name": name,
            "review_status": status, "source_refs": refs or [], "attributes": {}}


class DetectDuplicatesTest(unittest.TestCase):
    def _graph(self, *nodes, relations=()):
        return {"nodes": {n["id"]: n for n in nodes},
                "relations": {k: {"review_status": "draft"} for k in relations}}

    def test_standard_code_pattern(self):
        self.assertTrue(_is_standard_code("disease", "Q21.0"))
        self.assertTrue(_is_standard_code("disease", "I21.0"))
        self.assertFalse(_is_standard_code("disease", "CAND-VSD"))
        # 既非 ICD 正则、也未被共享词库登记 → 非标准码
        self.assertFalse(_is_standard_code("symptom", "TOTALLY-BOGUS"))
        self.assertFalse(_is_standard_code("disease", ""))

    def test_standard_code_accepts_vocabulary_backed_entry(self):
        # ICD 正则不命中、但共享词库登记了该编码 → 视为标准码（未来非 ICD 标准码场景）
        with unittest.mock.patch("src.api.routes.kg.find_shared_entry") as lookup:
            lookup.return_value = object()
            self.assertTrue(_is_standard_code("medication", "MED-001"))
            lookup.assert_called_once_with("medication", "MED-001")
        # 不打桩时正则路径照常生效
        self.assertTrue(_is_standard_code("disease", "Q21.0"))
        self.assertFalse(_is_standard_code("medication", "MED-001"))

    def test_recommended_survivor_prefers_vocabulary_backed_code(self):
        graph = self._graph(
            _node("ENTITY:medication:CAND-MED", "CAND-MED", "地高辛", node_type="medication", refs=["a"]),
            _node("ENTITY:medication:MED-001", "MED-001", "地高辛", node_type="medication", status="active"),
        )
        with unittest.mock.patch(
            "src.api.routes.kg.find_shared_entry",
            side_effect=lambda entity_type, code, directory=None: object() if code == "MED-001" else None,
        ):
            groups = find_duplicate_groups(graph)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["recommended_survivor"], "ENTITY:medication:MED-001")
        by_id = {n["node_id"]: n for n in groups[0]["nodes"]}
        self.assertTrue(by_id["ENTITY:medication:MED-001"]["is_standard_code"])
        self.assertFalse(by_id["ENTITY:medication:CAND-MED"]["is_standard_code"])

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

    def test_merge_rejects_empty_survivor_code_before_writes(self):
        # 保留节点缺编码：被吸收码会被改写为 ""（数据损坏）→ 必须在落盘前 422 拒绝
        graph_path = self.kg_dir / "test_graph.json"
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        survivor = next(n for n in graph["nodes"].values() if n["id"] == "ENTITY:disease:Q21.0")
        survivor["code"] = ""
        graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(HTTPException) as ctx:
            merge_duplicate_nodes(self._request(), kg_dir=self.kg_dir, rules_path=self.rules_path,
                                  snapshot_dir=self.snapshot_dir, audit_path=self.audit_path)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail, "保留节点缺少标准编码，无法改写规则引用")
        # 任何备份与改写都未发生
        self.assertFalse(list(self.kg_dir.glob("*.bak-*-merge")))
        self.assertFalse(list(self.rules_path.parent.glob("managed_rules.json.bak-*")))
        rule = json.loads(self.rules_path.read_text(encoding="utf-8"))[0]
        self.assertEqual(rule["disease_code"], "CAND-VSD")
        graph_after = json.loads(graph_path.read_text(encoding="utf-8"))
        self.assertIn("ENTITY:disease:CAND-VSD", graph_after["nodes"])

    def test_merge_discards_stale_condition_text(self):
        # 改写后的规则若保留旧 condition_text（描述被吸收码）会误导审核 → 落盘前弃置，重载时重建
        rules = json.loads(self.rules_path.read_text(encoding="utf-8"))
        rules[0]["condition_text"] = "存在检查 CAND-VSD 或症状 胸痛"
        self.rules_path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        merge_duplicate_nodes(self._request(), kg_dir=self.kg_dir, rules_path=self.rules_path,
                              snapshot_dir=self.snapshot_dir, audit_path=self.audit_path)
        saved = json.loads(self.rules_path.read_text(encoding="utf-8"))
        self.assertNotIn("condition_text", saved[0])  # 陈旧文本已弃置
        reloaded = _load_all_rules(self.rules_path)[0]
        self.assertNotIn("CAND-VSD", reloaded.get("condition_text") or "")
        self.assertTrue(reloaded.get("condition_text"))  # 重载后按新条件树重新生成


if __name__ == "__main__":
    unittest.main()
