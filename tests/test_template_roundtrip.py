#!/usr/bin/env python3
"""Template JSON round-trip fidelity tests.

Guards against silent field loss: Pydantic models drop unknown keys on load,
so any field present in data/templates/extraction_templates.json must also be
declared on the model, or the next template save wipes it from disk (this
happened to entity_types[].extraction_hint / description).
"""
import json
import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.extraction.models import ExtractionTemplate, TemplateEntityType

TEMPLATES_PATH = project_root / "data" / "templates" / "extraction_templates.json"


def flat_keys(obj, prefix=""):
    keys = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            keys.add(prefix + key)
            keys |= flat_keys(value, f"{prefix}{key}.")
    elif isinstance(obj, list):
        for value in obj:
            keys |= flat_keys(value, f"{prefix}[].")
    return keys


class TemplateRoundTripTest(unittest.TestCase):
    def test_entity_type_keeps_hint_and_description(self):
        item = {
            "entity_type": "order_set",
            "label": "医嘱套餐",
            "discovery": True,
            "vocabulary": True,
            "domain": "western",
            "color": "#fbd38d",
            "icon": "clipboard-list",
            "extraction_hint": "指南中成组的医嘱推荐",
            "description": "覆盖成组开立的医嘱",
        }
        dumped = TemplateEntityType(**item).model_dump()
        for key in ("extraction_hint", "description", "icon", "color", "domain"):
            self.assertEqual(dumped[key], item[key])

    def test_real_template_file_survives_round_trip(self):
        if not TEMPLATES_PATH.exists():
            self.skipTest("template data file not present")
        templates = json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))
        self.assertTrue(templates)
        for raw in templates:
            with self.subTest(template_id=raw.get("template_id")):
                dumped = ExtractionTemplate(**raw).model_dump()
                lost = flat_keys(raw) - flat_keys(dumped)
                self.assertEqual(lost, set(), f"keys lost on load/dump: {sorted(lost)}")


if __name__ == "__main__":
    unittest.main()
