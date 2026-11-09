"""Tests for the v2 western rule generator."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.domains.western.rule_generation import generate_western_rules
from src.extraction.models import (
    ExtractionTemplate,
    TemplateEntityEntry,
    TemplateEntityType,
)
from src.models.rule import Rule


def make_template() -> ExtractionTemplate:
    return ExtractionTemplate(
        template_id="TEST_V2",
        name="规则生成测试",
        domain="western",
        graph_filename="TEST_GRAPH.json",
        entity_types=[
            TemplateEntityType(entity_type="disease", label="疾病", entries=[
                TemplateEntityEntry(code="I40.9", name="急性心肌炎", aliases=["急性心肌炎"]),
            ]),
            TemplateEntityType(entity_type="symptom", label="症状"),
            TemplateEntityType(entity_type="lab", label="检验"),
            TemplateEntityType(entity_type="concept", label="概念"),
            TemplateEntityType(entity_type="diagnostic_evidence", label="证据"),
            TemplateEntityType(entity_type="medication", label="药物"),
        ],
    )


def make_chunk() -> dict:
    return {
        "chunk_id": "c1",
        "section": "诊断",
        "page_start": 1,
        "page_end": 1,
        "line_start": 1,
        "line_end": 30,
        "text": "急性心肌炎\n肌钙蛋白明显升高\n胸痛\n出血\n机械通气",
    }


def entity(entity_type, code, name, line, quote="", concept_code="", chunk_id="c1"):
    return {
        "entity_type": entity_type,
        "code": code,
        "name": name,
        "concept_code": concept_code,
        "chunk_id": chunk_id,
        "line_start": line,
        "line_end": line,
        "page": 1,
        "quote": quote,
    }


class WesternRuleGeneratorV2Test(unittest.TestCase):
    def test_generates_lab_condition_and_treatment_rule(self):
        entities = [
            entity("disease", "I40.9", "急性心肌炎", 10),
            entity("lab", "CTN", "肌钙蛋白", 12, "肌钙蛋白明显升高"),
            entity("symptom", "SYM_CHEST_PAIN", "胸痛", 13),
            entity("diagnostic_evidence", "HEMORRHAGE", "出血", 14),
            entity("medication", "MECHANICAL_VENTILATION", "机械通气", 15),
        ]
        rules = generate_western_rules(
            entities, [make_chunk()], make_template(), "DOC-V2", "测试指南"
        )
        types = {rule["rule_type"] for rule in rules}
        self.assertEqual(types, {"diagnosis", "treatment"})
        for rule in rules:
            Rule(**rule)

        diagnosis = next(rule for rule in rules if rule["rule_type"] == "diagnosis")
        text = __import__("json").dumps(diagnosis["conditions"], ensure_ascii=False)
        self.assertIn('"lab_code": "CTN"', text)
        self.assertIn('"concept_code": "ABOVE_ULN_1"', text)
        self.assertNotIn("HEMORRHAGE", text)
        # Generated conditions carry both the full term and the code.
        self.assertIn("肌钙蛋白（CTN）", diagnosis["condition_text"])
        self.assertIn("超正常上限（ABOVE_ULN_1）", diagnosis["condition_text"])
        lab_condition = diagnosis["conditions"]["children"][0]["children"][0]
        self.assertIn("肌钙蛋白（CTN）", lab_condition["label"])

        treatment = next(rule for rule in rules if rule["rule_type"] == "treatment")
        self.assertEqual(treatment["disease_code"], "I40.9")
        self.assertTrue(treatment["conclusion"]["treatment_plan_id"])
        self.assertTrue(treatment["conclusion"]["treatment_name"])
        item_names = {item["name"] for item in treatment["conclusion"]["treatment_items"]}
        self.assertIn("机械通气", item_names)

    def test_auto_discovered_diseases_do_not_generate_rules(self):
        entities = [
            entity("disease", "AUTO-X", "自动发现疾病", 10),
            entity("lab", "CTN", "肌钙蛋白", 12, "肌钙蛋白升高"),
        ]
        rules = generate_western_rules(
            entities, [make_chunk()], make_template(), "DOC-V2"
        )
        self.assertEqual(rules, [])


if __name__ == "__main__":
    unittest.main()
