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

    def test_same_code_different_name_keeps_existing_node(self):
        # 同类型同码不同名 → 命中既有 id 更新分支：首名优先，不触发同名合并、不追加 alt_codes
        graph = _fresh_graph()
        first = _upsert_semantic_node(graph, "disease", "Q21.0", "室间隔缺损", ["DOC-A_p1_l1-1"])
        second = _upsert_semantic_node(graph, "disease", "Q21.0", "室间隔缺损（修订名）", ["DOC-B_p1_l1-2"])
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(first["name"], "室间隔缺损")
        self.assertNotIn("alt_codes", first.get("attributes", {}))
        self.assertNotIn("_same_name_merges", graph)
        self.assertEqual(first["source_refs"], ["DOC-A_p1_l1-1", "DOC-B_p1_l1-2"])

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
