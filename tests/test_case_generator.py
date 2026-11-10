import json
import tempfile
import unittest
from pathlib import Path

from src.review.test_case_generator import generate_case_for_package


def make_package(rules):
    return {
        "package_id": "PKG-GEN",
        "doc_id": "DOC-GEN",
        "doc_title": "生成器测试指南",
        "domain": "western",
        "target_disease_code": "K1.1",
        "graph_filename": "stemi_graph.json",
        "entities": [
            {"entity_type": "disease", "name": "生成器测试病", "code": "K1.1"},
        ],
        "suggested_rules": rules,
    }


def lab_rule():
    return {
        "rule_id": "R-LAB",
        "rule_type": "diagnosis",
        "priority": 50,
        "status": "draft",
        "disease_code": "K1.1",
        "conditions": {
            "type": "group", "operator": "AND", "children": [
                {"type": "symptom", "symptom_code": "SYM_CHEST_PAIN"},
                {"type": "lab_concept", "lab_code": "CTN", "concept_code": "ELEVATED"},
            ],
        },
    }


class TestCaseGenerator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patients_dir = Path(self.tmp.name) / "patients"
        self.cases_path = self.patients_dir / "cases.json"

    def test_builds_positive_patient_and_case(self):
        result = generate_case_for_package(
            make_package([lab_rule()]),
            patients_dir=self.patients_dir,
            cases_path=self.cases_path,
        )
        patient = json.loads(
            (self.patients_dir / result["patient_file"]).read_text(encoding="utf-8")
        )
        self.assertTrue(patient["symptoms"])
        self.assertEqual(patient["symptoms"][0]["symptom_code"], "SYM_CHEST_PAIN")
        self.assertTrue(any(lab.get("lab_code") == "CTN" for lab in patient["labs"]))
        ctn = next(lab for lab in patient["labs"] if lab["lab_code"] == "CTN")
        self.assertTrue(
            (ctn.get("value") or 0) > (ctn.get("reference_high") or 0)
            or ctn.get("qualitative"),
            "CTN 应为超限数值或定性阳性",
        )
        cases = json.loads(self.cases_path.read_text(encoding="utf-8"))["cases"]
        self.assertEqual(cases[0]["case_id"], "auto-doc-gen")

    def test_or_group_uses_first_satisfiable_branch(self):
        rule = lab_rule()
        rule["rule_id"] = "R-OR"
        rule["conditions"] = {
            "type": "group", "operator": "OR", "children": [
                {"type": "symptom", "symptom_code": ""},  # 不可满足
                {"type": "symptom", "symptom_code": "SYM_FATIGUE"},
            ],
        }
        result = generate_case_for_package(
            make_package([rule]),
            patients_dir=self.patients_dir,
            cases_path=self.cases_path,
        )
        self.assertEqual(result["rule_id"], "R-OR")
        patient = json.loads(
            (self.patients_dir / result["patient_file"]).read_text(encoding="utf-8")
        )
        self.assertEqual(patient["symptoms"][0]["symptom_code"], "SYM_FATIGUE")

    def test_unsatisfiable_rules_raise_with_reason(self):
        rule = lab_rule()
        rule["rule_id"] = "R-BAD"
        rule["conditions"] = {"type": "symptom", "symptom_code": ""}
        with self.assertRaises(ValueError):
            generate_case_for_package(
                make_package([rule]),
                patients_dir=self.patients_dir,
                cases_path=self.cases_path,
            )


if __name__ == "__main__":
    unittest.main()
