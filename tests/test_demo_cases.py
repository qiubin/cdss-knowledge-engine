"""Demo patient case switching tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient

from src.api.main import app
from src.models.patient import PatientSnapshot


class DemoCasesTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_cases_include_stemi_and_fulminant_myocarditis(self):
        cases = self.client.get("/api/inference/demo-patients?domain=western").json()
        self.assertGreaterEqual(len(cases), 4)
        categories = {item.get("category") for item in cases}
        self.assertIn("STEMI", categories)
        self.assertIn("暴发性心肌炎", categories)

    def test_each_case_loads_and_runs(self):
        cases = self.client.get("/api/inference/demo-patients?domain=western").json()
        for case in cases:
            patient = self.client.get(
                f"/api/inference/demo-patient?case_id={case['case_id']}"
            ).json()
            self.assertTrue(patient.get("patient_id"))
            # Extra presentation fields must not break PatientSnapshot.
            PatientSnapshot(**patient)
            response = self.client.post(
                "/api/inference/run",
                json={"patient": patient, "domain": "western"},
            )
            self.assertEqual(response.status_code, 200, response.text[:300])

    def test_fulminant_myocarditis_gets_diagnosis_and_treatment(self):
        patient = self.client.get(
            "/api/inference/demo-patient?case_id=fulminant-myocarditis-adult"
        ).json()
        result = self.client.post(
            "/api/inference/run",
            json={"patient": patient, "domain": "western"},
        ).json()
        self.assertGreater(result["conclusion_count"], 0)
        self.assertTrue(result["diagnosis_conclusions"])
        self.assertTrue(result["treatment_conclusions"])
        self.assertGreater(result["diagnostics"]["active_diagnosis_rules"], 0)

    def test_evidence_labels_include_full_terms(self):
        patient = self.client.get(
            "/api/inference/demo-patient?case_id=fulminant-myocarditis-adult"
        ).json()
        result = self.client.post(
            "/api/inference/run",
            json={"patient": patient, "domain": "western"},
        ).json()
        evidence = [
            item
            for conclusion in result["diagnosis_conclusions"]
            for item in conclusion["evidence"]
        ]
        descriptions = [item.get("description") or "" for item in evidence]
        values = [item.get("patient_value_display") or "" for item in evidence]
        standards = [item.get("standard_used") or "" for item in evidence]
        self.assertTrue(any("肌钙蛋白（CTN）" in text for text in descriptions))
        self.assertTrue(any("超" in text and "正常上限" in text for text in descriptions))
        self.assertTrue(any("肌红蛋白（MYO）" in text for text in values))
        self.assertTrue(any("正常上限（ULN）" in text for text in standards))

    def test_unmatched_case_explains_why(self):
        patient = self.client.get(
            "/api/inference/demo-patient?case_id=stemi-anterior"
        ).json()
        result = self.client.post(
            "/api/inference/run",
            json={"patient": patient, "domain": "western"},
        ).json()
        self.assertEqual(result["conclusion_count"], 0)
        self.assertTrue(result["diagnostics"]["reasons"])


if __name__ == "__main__":
    unittest.main()
