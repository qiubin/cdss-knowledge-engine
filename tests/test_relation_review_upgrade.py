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
        # GRAPH2 共 8 条关系：6 draft / 2 active（与 fixture 实际一致）。
        queue = build_queue(service=self.service, graph_files=["REVIEW-TEST2.json"], limit=300)
        summary = queue["summary"]
        self.assertEqual(summary["graph_relations"].get("draft"), 6)
        self.assertEqual(summary["graph_relations"].get("active"), 2)


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
        self.assertEqual(detail["totals"]["total"], 4)

    def test_group_detail_unknown_subject_404(self):
        with self.assertRaises(ReviewError) as ctx:
            self.service.relation_group_detail("REVIEW-TEST2.json", "ENTITY:disease:NOPE")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_group_detail_same_container_two_codes_no_duplicate_nested_items(self):
        # 脏数据场景：同一主体经两种关系码指向同一容器——容器出边只收集一次；
        # 同时容器自身同码多目标出边（一个方案含多种药品）必须全量保留。
        graph = json.loads(json.dumps(GRAPH2))
        graph["nodes"].append({
            "id": "ENTITY:medication:M2", "node_type": "medication", "code": "MED_2",
            "name": "药品二", "review_status": "active",
        })
        graph["relations"].append({
            "source_node_id": "ENTITY:disease:D1", "relation_code": "DISEASE_HAS_ORDER_SET",
            "target_node_id": "ENTITY:treatment_plan:TP1", "relation_name": "疾病有医嘱组",
            "review_status": "active", "source_refs": ["DOC-x_p1_l1-5"],
        })
        graph["relations"].append({
            "source_node_id": "ENTITY:treatment_plan:TP1",
            "relation_code": "TREATMENT_PLAN_INCLUDES_MEDICATION",
            "target_node_id": "ENTITY:medication:M2", "relation_name": "方案含药品",
            "review_status": "active", "source_refs": ["DOC-x_p1_l1-5"],
        })
        (self.graph_dir / "REVIEW-TEST3.json").write_text(
            json.dumps(graph, ensure_ascii=False), encoding="utf-8"
        )
        detail = self.service.relation_group_detail("REVIEW-TEST3.json", "ENTITY:disease:D1")
        tp_containers = [
            container for section in detail["sections"]
            for container in section["containers"]
            if container["container_node_id"] == "ENTITY:treatment_plan:TP1"
        ]
        self.assertEqual(len(tp_containers), 2)  # 两个直接 section 各自展开 TP1
        nested_keys = set()
        for container in tp_containers:
            nested = container["sections"]
            self.assertEqual([s["relation_code"] for s in nested], ["TREATMENT_PLAN_INCLUDES_MEDICATION"])
            # M1、M2 两条都在（同码多目标不丢边），且不因双码重复扫描翻倍
            self.assertEqual(len(nested[0]["items"]), 2)
            self.assertEqual(
                {item["target_node_id"] for item in nested[0]["items"]},
                {"ENTITY:medication:M1", "ENTITY:medication:M2"},
            )
            nested_keys.add(nested[0]["section_key"])
        # 嵌套 section_key 全局唯一，两个容器展开引用同一组条目
        self.assertEqual(
            nested_keys, {"ENTITY:treatment_plan:TP1:TREATMENT_PLAN_INCLUDES_MEDICATION"}
        )
