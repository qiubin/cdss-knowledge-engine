import unittest

from src.models.rule import Rule
from src.clinical_intent import infer_clinical_intent, normalize_clinical_intent


class ClinicalIntentTest(unittest.TestCase):
    def test_infers_key_import_purposes(self):
        self.assertEqual(infer_clinical_intent("diagnosis", "急性心衰初步诊断线索"), "suspected_diagnosis")
        self.assertEqual(infer_clinical_intent("diagnosis", "用于鉴别肺炎与心衰"), "differential_diagnosis")
        self.assertEqual(infer_clinical_intent("treatment", "首选 SGLT2i 治疗"), "treatment_selection")
        self.assertEqual(infer_clinical_intent("monitoring", "出院后复查肾功能"), "follow_up_plan")

    def test_explicit_valid_intent_is_preserved(self):
        rule = {"rule_type": "diagnosis", "clinical_intent": "risk_stratification", "name": "疑似筛查"}
        self.assertEqual(normalize_clinical_intent(rule), "risk_stratification")

    def test_rule_model_backfills_old_rule(self):
        rule = Rule(
            rule_id="R-OLD",
            rule_type="treatment",
            name="治疗推荐",
            disease_code="I50.9",
            conditions={"type": "group", "operator": "AND", "children": []},
            conclusion={"diagnosis": "I50.9"},
        )
        self.assertEqual(rule.clinical_intent, "treatment_recommendation")


if __name__ == "__main__":
    unittest.main()
