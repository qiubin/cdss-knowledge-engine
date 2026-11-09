"""Tests for code + full-term labels written into generated artifacts."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes.kg import _node_display_name, _semantic_node_name
from src.terminology.labels import default_labels


class TerminologyLabelsTest(unittest.TestCase):
    def test_entity_term_pairs_name_with_code(self):
        labels = default_labels()
        self.assertEqual(labels.term("lab", "CTN"), "肌钙蛋白（CTN）")
        self.assertEqual(labels.term("symptom", "SYM_FATIGUE"), "乏力（SYM_FATIGUE）")
        self.assertIn("超正常上限", labels.concept("ABOVE_ULN_1"))

    def test_evidence_name_rebuilds_missing_dictionary_entries(self):
        labels = default_labels()
        self.assertEqual(
            labels.evidence_name("RENAL_FUNCTION:ABNORMAL"), "肾功能异常"
        )
        self.assertEqual(labels.evidence_name("BNP:ABNORMAL"), "B型钠尿肽异常")
        self.assertEqual(labels.evidence_name("ESR:DECREASED"), "血沉降低")

    def test_semantic_node_name_uses_composed_evidence_name(self):
        self.assertEqual(
            _semantic_node_name("diagnostic_evidence", "RENAL_FUNCTION:ABNORMAL"),
            "肾功能异常",
        )
        self.assertEqual(
            _node_display_name("diagnostic_evidence", "CTN:ABOVE_ULN_1", "肌钙蛋白超正常上限"),
            "肌钙蛋白超正常上限（CTN:ABOVE_ULN_1）",
        )
        self.assertEqual(
            _node_display_name("lab", "CTN", "肌钙蛋白"),
            "肌钙蛋白（CTN）",
        )


if __name__ == "__main__":
    unittest.main()
