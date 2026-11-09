"""Entity vocabulary specialty/disease classification tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.vocab.entity_classification import classify_entries
from src.vocab.entity_vocabulary import EntityVocabularyRepository


class EntityClassificationTest(unittest.TestCase):
    def test_disease_entries_get_default_specialty_and_disease_tag(self):
        entries = [item.model_dump() for item in EntityVocabularyRepository("disease").list()]
        classified = {item["code"]: item for item in classify_entries("disease", entries)}

        self.assertIn("心血管内科", classified["I21.0"]["specialties"])
        self.assertIn("血液肿瘤", classified["C81"]["specialties"])
        self.assertTrue(classified["I21.0"]["diseases"])
        self.assertEqual(classified["I21.0"]["classification_source"], "derived")

    def test_manual_classification_overrides_derived_values(self):
        entries = [{
            "code": "CUSTOM_ENTITY",
            "name": "自定义实体",
            "specialty": "测试专科",
            "disease_tags": ["测试疾病"],
        }]
        item = classify_entries("lab", entries)[0]

        self.assertEqual(item["specialties"], ["测试专科"])
        self.assertEqual(item["diseases"], ["测试疾病"])
        self.assertEqual(item["classification_source"], "manual")

    def test_unlinked_entity_has_unclassified_filter_values(self):
        item = classify_entries("lab", [{"code": "NO_GRAPH_LINK", "name": "无关联检验"}])[0]

        self.assertEqual(item["specialties"], ["未分类"])
        self.assertEqual(item["diseases"], ["未分类"])
        self.assertEqual(item["classification_source"], "default")


if __name__ == "__main__":
    unittest.main()
