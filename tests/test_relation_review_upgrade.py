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
