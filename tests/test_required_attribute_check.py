"""P1-3: 模板必填属性缺失检查（字典默认属性合并后再判缺失）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.extraction.models import (
    ExtractionTemplate,
    TemplateAttribute,
    TemplateEntityEntry,
    TemplateEntityType,
    TemplateRelation,
)
from src.extraction.service import _validate_llm_payload


def make_template(required: bool = True) -> ExtractionTemplate:
    return ExtractionTemplate(
        template_id="TEST-REQ",
        name="必填属性测试",
        domain="western",
        entity_types=[
            TemplateEntityType(entity_type="disease", label="疾病", entries=[
                TemplateEntityEntry(code="I10", name="高血压"),
            ]),
            TemplateEntityType(entity_type="medication", label="药物", entries=[
                TemplateEntityEntry(code="M-ASA", name="阿司匹林"),
            ]),
        ],
        relations=[],
        attributes=[
            TemplateAttribute(
                attribute_code="HERB_DOSE",
                name="剂量用法",
                owner_entity_type="medication",
                data_type="string",
                required=required,
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
        "text": "阿司匹林",
    }]


def payload(attributes=None, entity_type="medication", code="M-ASA"):
    entity = {
        "entity_type": entity_type, "name": "阿司匹林", "code": code,
        "chunk_id": "c1", "page": 1, "line_start": 5, "line_end": 6,
    }
    if attributes is not None:
        entity["attributes"] = attributes
    return {
        "entities": [entity],
        "suggested_relations": [],
        "suggested_rules": [],
        "suggested_treatment_plans": [],
        "suggested_treatment_rules": [],
        "suggested_contraindications": [],
    }


class RequiredAttributeCheckTest(unittest.TestCase):
    def test_missing_required_attribute_warns(self):
        result = _validate_llm_payload(
            payload(attributes={}), make_chunks(), make_template(), "DOC", "测试指南"
        )
        self.assertTrue(
            any(w.startswith("缺少必填属性 HERB_DOSE（剂量用法）") for w in result[3])
        )

    def test_llm_attribute_satisfies_required(self):
        result = _validate_llm_payload(
            payload(attributes={"HERB_DOSE": "300mg"}), make_chunks(), make_template(), "DOC", "测试指南"
        )
        self.assertFalse(any("缺少必填属性" in w for w in result[3]))

    def test_dictionary_default_satisfies_required(self):
        template = make_template()
        # 字典默认属性合并进 attributes 后即可满足 required，不再告警
        template.entity_types[1].entries[0].attributes = {"HERB_DOSE": "300mg"}
        result = _validate_llm_payload(
            payload(attributes={}), make_chunks(), template, "DOC", "测试指南"
        )
        self.assertFalse(any("缺少必填属性" in w for w in result[3]))

    def test_template_without_required_is_silent(self):
        result = _validate_llm_payload(
            payload(attributes={}), make_chunks(), make_template(required=False), "DOC", "测试指南"
        )
        self.assertFalse(any("缺少必填属性" in w for w in result[3]))

    def test_other_entity_type_not_affected(self):
        result = _validate_llm_payload(
            payload(attributes={}, entity_type="disease", code="I10"),
            make_chunks(), make_template(), "DOC", "测试指南",
        )
        self.assertFalse(any("缺少必填属性" in w for w in result[3]))


if __name__ == "__main__":
    unittest.main()
