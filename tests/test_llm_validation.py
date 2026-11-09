"""Validation behavior for LLM extraction payloads."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.extraction.models import (
    ExtractionTemplate,
    TemplateEntityEntry,
    TemplateEntityType,
    TemplateRelation,
)
from src.extraction.service import _merge_warnings, _validate_llm_payload


def make_template() -> ExtractionTemplate:
    return ExtractionTemplate(
        template_id="TEST",
        name="测试",
        domain="western",
        entity_types=[
            TemplateEntityType(entity_type="disease", label="疾病", entries=[
                TemplateEntityEntry(code="I10", name="高血压"),
            ]),
            TemplateEntityType(entity_type="lab", label="检验", entries=[
                TemplateEntityEntry(code="L1", name="血脂"),
            ]),
            TemplateEntityType(entity_type="medication", label="药物"),
        ],
        relations=[
            TemplateRelation(
                relation_code="DISEASE_HAS_LAB",
                name="疾病有检验",
                source_entity_type="disease",
                target_entity_type="lab",
            ),
        ],
    )


def make_chunks() -> list:
    return [{
        "chunk_id": "c1",
        "section": "",
        "page_start": 1,
        "page_end": 1,
        "line_start": 5,
        "line_end": 10,
        "text": "高血压",
    }]


class PayloadValidationTest(unittest.TestCase):
    def test_treatment_rule_without_conditions_uses_empty_group(self):
        payload = {
            "entities": [{
                "entity_type": "disease", "name": "高血压", "code": "I10",
                "chunk_id": "c1", "page": 1, "line_start": 5, "line_end": 6,
            }],
            "suggested_relations": [],
            "suggested_rules": [],
            "suggested_treatment_plans": [],
            "suggested_treatment_rules": [{
                "rule_id": "TX-1",
                "disease_code": "I10",
                # The model sometimes omits conditions/conclusion entirely.
                "source": {"chunk_id": "c1"},
            }],
            "suggested_contraindications": [],
        }
        result = _validate_llm_payload(payload, make_chunks(), make_template(), "DOC", "测试指南")
        treatment_rules = result[5]
        self.assertEqual(len(treatment_rules), 1)
        self.assertEqual(treatment_rules[0]["conditions"]["type"], "group")
        self.assertEqual(treatment_rules[0]["conditions"]["children"], [])
        self.assertEqual(treatment_rules[0]["conclusion"]["diagnosis"], "I10")
        self.assertFalse(any("校验失败" in warning for warning in result[3]))

    def test_repetitive_warnings_are_aggregated(self):
        payload = {
            "entities": [{
                "entity_type": "disease", "name": "高血压", "code": "I10",
                # Outside the chunk range: triggers a position correction.
                "chunk_id": "c1", "page": 1, "line_start": 1, "line_end": 2,
            }],
            "suggested_relations": [
                {
                    "relation_code": "DISEASE_HAS_LAB",
                    "source_type": "disease", "source_code": "未知疾病甲",
                    "target_type": "lab", "target_code": "未知检验甲",
                    "chunk_id": "c1",
                },
                {
                    "relation_code": "DISEASE_HAS_LAB",
                    "source_type": "disease", "source_code": "未知疾病乙",
                    "target_type": "lab", "target_code": "未知检验乙",
                    "chunk_id": "c1",
                },
            ],
            "suggested_rules": [],
            "suggested_treatment_plans": [],
            "suggested_treatment_rules": [],
            "suggested_contraindications": [],
        }
        warnings = _validate_llm_payload(
            payload, make_chunks(), make_template(), "DOC", "测试指南"
        )[3]
        joined = "\n".join(warnings)
        self.assertIn("已校正实体溯源位置", joined)
        self.assertIn("已忽略 2 条引用未知实体的关系（DISEASE_HAS_LAB×2）", joined)
        self.assertFalse(any(warning.startswith("已校正实体 高血压") for warning in warnings))

    def test_relation_accepts_template_style_endpoint_keys(self):
        payload = {
            "entities": [
                {
                    "entity_type": "disease", "name": "高血压", "code": "I10",
                    "chunk_id": "c1", "page": 1, "line_start": 5, "line_end": 6,
                },
                {
                    "entity_type": "lab", "name": "血脂", "code": "L1",
                    "chunk_id": "c1", "page": 1, "line_start": 5, "line_end": 6,
                },
            ],
            "suggested_relations": [{
                "relation_code": "DISEASE_HAS_LAB",
                "source_entity_type": "disease",
                "source_entity_code": "I10",
                "target_entity_type": "lab",
                "target_entity_code": "L1",
                "chunk_id": "c1",
            }],
            "suggested_rules": [],
            "suggested_treatment_plans": [],
            "suggested_treatment_rules": [],
            "suggested_contraindications": [],
        }
        result = _validate_llm_payload(payload, make_chunks(), make_template(), "DOC", "测试指南")
        self.assertEqual(len(result[2]), 1)
        self.assertEqual(result[2][0]["source_code"], "I10")
        self.assertEqual(result[2][0]["target_code"], "L1")

    def test_unknown_relation_endpoint_is_materialized(self):
        payload = {
            "entities": [{
                "entity_type": "disease", "name": "高血压", "code": "I10",
                "chunk_id": "c1", "page": 1, "line_start": 5, "line_end": 6,
            }],
            "suggested_relations": [{
                "relation_code": "DISEASE_HAS_LAB",
                "source_entity_type": "disease", "source_code": "I10",
                "target_entity_type": "lab", "target_code": "L9",
                "chunk_id": "c1",
            }],
            "suggested_rules": [],
            "suggested_treatment_plans": [],
            "suggested_treatment_rules": [],
            "suggested_contraindications": [],
        }
        entities, _, relations, warnings, *_ = _validate_llm_payload(
            payload, make_chunks(), make_template(), "DOC", "测试指南"
        )
        self.assertTrue(any(item["entity_type"] == "lab" and item["code"] == "L9" for item in entities))
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0]["target_code"], "L9")
        self.assertTrue(any("已根据关系补全 1 个未抽取实体" in item for item in warnings))

    def test_unknown_chunk_ids_fall_back_to_page_and_line(self):
        payload = {
            "entities": [
                {
                    "entity_type": "disease", "name": "高血压", "code": "I10",
                    "chunk_id": "c1", "page": 1, "line_start": 5, "line_end": 6,
                },
                {
                    "entity_type": "lab", "name": "血脂", "code": "L1",
                    "chunk_id": "c1", "page": 1, "line_start": 5, "line_end": 6,
                },
            ],
            "suggested_relations": [{
                "relation_code": "DISEASE_HAS_LAB",
                "source_type": "disease", "source_code": "I10",
                "target_type": "lab", "target_code": "L1",
                "chunk_id": "STALE-CHUNK", "page": 1, "line_start": 5,
            }],
            "suggested_rules": [],
            "suggested_treatment_plans": [{
                "treatment_plan_code": "TP-1",
                "name": "测试方案",
                "chunk_id": "STALE-CHUNK", "page": 1, "line_start": 5,
            }],
            "suggested_treatment_rules": [],
            "suggested_contraindications": [],
        }
        result = _validate_llm_payload(payload, make_chunks(), make_template(), "DOC", "测试指南")
        self.assertEqual(len(result[2]), 1)
        self.assertEqual(result[2][0]["chunk_id"], "c1")
        self.assertEqual(len(result[4]), 1)
        self.assertEqual(result[4][0]["chunk_id"], "c1")
        self.assertTrue(any("已校正" in item and "片段归属" in item for item in result[3]))

    def test_merge_warnings_collapses_repeated_group_summaries(self):
        merged = _merge_warnings([
            "已忽略 2 条引用未知实体的关系（DISEASE_HAS_LAB×2）",
            "已忽略 3 条引用未知实体的关系（DISEASE_HAS_EXAM×1、DISEASE_HAS_LAB×2）",
            "已校正实体溯源位置（行号 4 处），已对齐到所属片段，不影响入库",
            "已校正实体溯源位置（行号 6 处），已对齐到所属片段，不影响入库",
            "已忽略 1 个缺少编码的治疗方案",
            "已忽略 2 个缺少编码的治疗方案",
        ])
        joined = "\n".join(merged)
        self.assertIn("已忽略 5 条引用未知实体的关系（DISEASE_HAS_EXAM×1、DISEASE_HAS_LAB×4）", joined)
        self.assertIn("已校正实体溯源位置（行号 10 处）", joined)
        self.assertIn("已忽略 3 个缺少编码的治疗方案", joined)
        self.assertEqual(len([item for item in merged if item.startswith("已忽略") and "引用未知实体" in item]), 1)


if __name__ == "__main__":
    unittest.main()
