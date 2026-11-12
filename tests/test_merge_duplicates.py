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
