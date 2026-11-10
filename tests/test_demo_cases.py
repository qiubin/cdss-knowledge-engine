"""Demo patient case switching tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import httpx

from src.api.main import app
from src.models.patient import PatientSnapshot


class DemoCasesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_cases_include_stemi_and_fulminant_myocarditis(self):
        response = await self.client.get("/api/inference/demo-patients?domain=western")
        cases = response.json()
        self.assertGreaterEqual(len(cases), 4)
        categories = {item.get("category") for item in cases}
        self.assertIn("STEMI", categories)
        self.assertIn("暴发性心肌炎", categories)

    async def test_each_case_loads_and_runs(self):
        response = await self.client.get("/api/inference/demo-patients?domain=western")
        cases = response.json()
        for case in cases:
            patient = (await self.client.get(
                f"/api/inference/demo-patient?case_id={case['case_id']}"
            )).json()
            self.assertTrue(patient.get("patient_id"))
            # Extra presentation fields must not break PatientSnapshot.
            PatientSnapshot(**patient)
            response = await self.client.post(
                "/api/inference/run",
                json={"patient": patient, "domain": "western"},
            )
            self.assertEqual(response.status_code, 200, response.text[:300])

    async def test_fulminant_myocarditis_gets_diagnosis_and_treatment(self):
        patient = (await self.client.get(
            "/api/inference/demo-patient?case_id=fulminant-myocarditis-adult"
        )).json()
        result = (await self.client.post(
            "/api/inference/run",
            json={"patient": patient, "domain": "western"},
        )).json()
        self.assertGreater(result["conclusion_count"], 0)
        self.assertTrue(result["diagnosis_conclusions"])
        self.assertTrue(result["treatment_conclusions"])
        self.assertGreater(result["diagnostics"]["active_diagnosis_rules"], 0)

    async def test_evidence_labels_include_full_terms(self):
        patient = (await self.client.get(
            "/api/inference/demo-patient?case_id=fulminant-myocarditis-adult"
        )).json()
        result = (await self.client.post(
            "/api/inference/run",
            json={"patient": patient, "domain": "western"},
        )).json()
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

    async def test_stemi_anterior_matches_and_empty_case_explains_why(self):
        patient = (await self.client.get(
            "/api/inference/demo-patient?case_id=stemi-anterior"
        )).json()
        result = (await self.client.post(
            "/api/inference/run",
            json={"patient": patient, "domain": "western"},
        )).json()
        self.assertGreater(result["conclusion_count"], 0)
        self.assertTrue(result["diagnosis_conclusions"])

        unmatched = {
            "patient_id": "DEMO-NO-MATCH",
            "hospital_id": "XM_FIRST",
        }
        result = (await self.client.post(
            "/api/inference/run",
            json={"patient": unmatched, "domain": "western"},
        )).json()
        self.assertEqual(result["conclusion_count"], 0)
        self.assertTrue(result["diagnostics"]["reasons"])

    async def test_tcm_demo_case_assertions_pass(self):
        patient = (await self.client.get(
            "/api/inference/demo-patient?case_id=TCM-CASE-001"
        )).json()
        response = await self.client.post(
            "/api/inference/test-case",
            json={
                "patient": patient,
                "domain": "tcm",
                "case_id": "TCM-CASE-001",
                "expected": {
                    "top_syndrome": "TCM_SYN_QI_YIN_XU",
                    "min_score": 0.8,
                    "must_include_formula": "TCM_FORMULA_SHENGMAI_SAN",
                    "must_not_trigger": ["TCM_SYN_TAN_ZHUO_BI_ZU"],
                },
                "include_draft_patterns": True,
            },
        )
        self.assertEqual(response.status_code, 200, response.text[:300])
        result = response.json()
        self.assertTrue(result["passed"])
        self.assertTrue(
            all(item["passed"] for item in result["checks"])
        )
        self.assertEqual(result["graph_files"], ["KG-TCM-DEMO.json"])


if __name__ == "__main__":
    unittest.main()
