"""Hospital standard coverage regression tests."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes.guide_packages import (
    _hospital_standard_coverage,
    _rule_lab_concepts,
)
from src.standards.repository import JsonStandardRepository


class StandardsCoverageTest(unittest.TestCase):
    def test_all_rule_lab_concepts_have_active_hospital_standards(self):
        rules = json.loads(
            (project_root / "data" / "rules" / "managed_rules.json").read_text(encoding="utf-8")
        )
        checks = _hospital_standard_coverage(_rule_lab_concepts(rules))
        self.assertEqual(checks["status"], "passed", checks["issues"])

    def test_standard_rows_are_unique_per_hospital_and_context(self):
        repo = JsonStandardRepository(str(project_root / "data" / "standards"))
        for hospital_id in ("FZ_HOSPITAL", "XM_FIRST"):
            rows = repo.list_for_hospital(hospital_id)
            identities = [
                (row.lab_code, row.concept_code, row.context_gender,
                 row.context_age_min, row.context_age_max)
                for row in rows
            ]
            self.assertEqual(len(identities), len(set(identities)))
            self.assertGreaterEqual(len(rows), 27)

    def test_every_lab_entity_has_active_standards_for_both_hospitals(self):
        lab_entities = json.loads(
            (project_root / "data" / "vocab" / "entities" / "lab.json").read_text(encoding="utf-8")
        )
        entity_codes = {item["code"] for item in lab_entities}
        repo = JsonStandardRepository(str(project_root / "data" / "standards"))
        for hospital_id in ("FZ_HOSPITAL", "XM_FIRST"):
            covered = {
                row.lab_code
                for row in repo.list_for_hospital(hospital_id)
                if row.status == "active"
            }
            self.assertEqual(entity_codes - covered, set(), hospital_id)


if __name__ == "__main__":
    unittest.main()
