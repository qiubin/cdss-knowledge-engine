"""Upload destination-graph selection: override must beat template binding."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.extraction.graph_binding import resolve_upload_graph_filename
from src.extraction.repository import ExtractionTemplateRepository


class ResolveUploadGraphFilenameTest(unittest.TestCase):
    def setUp(self):
        self.template = ExtractionTemplateRepository().get("GENERIC_GUIDELINE_V1")

    def test_empty_requested_keeps_template_binding(self):
        self.assertEqual(
            resolve_upload_graph_filename(self.template, ""),
            "KG-GENERIC_GUIDELINE_V1.json",
        )

    def test_requested_existing_graph_overrides_binding(self):
        self.assertEqual(
            resolve_upload_graph_filename(self.template, "stemi_graph.json"),
            "stemi_graph.json",
        )

    def test_requested_new_name_appends_json_suffix(self):
        self.assertEqual(
            resolve_upload_graph_filename(self.template, "KG-RESPIRATORY_V1"),
            "KG-RESPIRATORY_V1.json",
        )

    def test_requested_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_upload_graph_filename(self.template, "../rules/managed_rules.json")

    def test_requested_illegal_characters_are_rejected(self):
        with self.assertRaises(ValueError):
            resolve_upload_graph_filename(self.template, "kg/evil.json")


if __name__ == "__main__":
    unittest.main()
