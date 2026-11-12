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
