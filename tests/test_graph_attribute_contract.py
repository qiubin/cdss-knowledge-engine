"""P2-6: 图谱导入节点属性契约校验（编码 / 类型 / 枚举值）。"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import src.api.routes.guide_packages as gp
import src.api.routes.kg as kg_routes
from src.api.routes.kg import GraphImportRequest, import_graph
from src.extraction.models import (
    ExtractionTemplate,
    TemplateAttribute,
    TemplateEntityEntry,
    TemplateEntityType,
)
from src.extraction.service import template_attribute_violations


def make_template() -> ExtractionTemplate:
    return ExtractionTemplate(
        template_id="TEST-CONTRACT",
        name="属性契约测试",
        domain="western",
        entity_types=[
            TemplateEntityType(entity_type="disease", label="疾病", entries=[
                TemplateEntityEntry(code="I10", name="高血压"),
            ]),
            TemplateEntityType(entity_type="lab", label="检验", entries=[
                TemplateEntityEntry(code="L1", name="血脂"),
            ]),
        ],
        relations=[],
        attributes=[
            TemplateAttribute(attribute_code="LAB_VALUE", name="检验值", owner_entity_type="lab", data_type="number"),
            TemplateAttribute(attribute_code="LAB_CONFIRMED", name="是否确认", owner_entity_type="lab", data_type="boolean"),
            TemplateAttribute(attribute_code="SPECIMEN_KIND", name="标本类型", owner_entity_type="lab", data_type="enum", allowed_values=["血", "尿"]),
            TemplateAttribute(attribute_code="DISEASE_NOTE", name="疾病备注", owner_entity_type="disease", data_type="string"),
        ],
    )


class TemplateAttributeViolationsTest(unittest.TestCase):
    def test_valid_attributes_pass(self):
        violations = template_attribute_violations(
            {"LAB_VALUE": 3.5, "LAB_CONFIRMED": True, "SPECIMEN_KIND": "血"},
            "lab", make_template(),
        )
        self.assertEqual(violations, [])

    def test_unknown_attribute_code(self):
        violations = template_attribute_violations({"MYSTERY": "x"}, "lab", make_template())
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["attribute_code"], "MYSTERY")
        self.assertEqual(violations[0]["key"], "attributes.MYSTERY")

    def test_owner_entity_type_mismatch(self):
        violations = template_attribute_violations({"DISEASE_NOTE": "x"}, "lab", make_template())
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["key"], "attributes.DISEASE_NOTE")

    def test_number_type_violation(self):
        violations = template_attribute_violations({"LAB_VALUE": "abc"}, "lab", make_template())
        self.assertEqual(violations[0]["issue"], "类型应为数值")

    def test_boolean_type_violation(self):
        violations = template_attribute_violations({"LAB_CONFIRMED": "yes"}, "lab", make_template())
        self.assertEqual(violations[0]["issue"], "类型应为布尔")

    def test_enum_value_violation(self):
        violations = template_attribute_violations({"SPECIMEN_KIND": "痰"}, "lab", make_template())
        self.assertEqual(violations[0]["issue"], "取值不在模板允许值内")

    def test_type_without_contract_is_unconstrained(self):
        # 模板未给 disease 定义任何属性契约 → 该类型属性不校验
        template = make_template()
        template.attributes = [
            attr for attr in template.attributes if attr.owner_entity_type != "disease"
        ]
        violations = template_attribute_violations({"ANYTHING": "x"}, "disease", template)
        self.assertEqual(violations, [])


class ImportAttributeContractTest(unittest.TestCase):
    """知识包导入主路径：guide_packages.import_package → kg.import_graph。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.original_kg_dir = kg_routes.KG_DIR
        kg_routes.KG_DIR = root / "kg"
        kg_routes.KG_DIR.mkdir(parents=True, exist_ok=True)
        # 语义层校验需要概念词汇表；复制真实词表到临时目录
        vocab_dir = root / "vocab"
        vocab_dir.mkdir()
        (vocab_dir / "concepts.json").write_text(
            (Path(__file__).parent.parent / "data" / "vocab" / "concepts.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.addCleanup(setattr, kg_routes, "KG_DIR", self.original_kg_dir)

    def _request(self, attributes):
        return GraphImportRequest(
            doc_id="DOC-CONTRACT",
            doc_title="属性契约导入测试",
            domain="western",
            target_disease_code="I21.0",
            template_id="GENERIC_GUIDELINE_V1",
            graph_filename="CONTRACT-TEST.json",
            entities=[
                {"entity_type": "disease", "name": "契约测试病", "code": "I21.0",
                 "chunk_id": "c1", "page": 1, "line_start": 1, "line_end": 2,
                 "quote": "q", "confidence": 0.9, "attributes": attributes},
            ],
            chunks=[{"chunk_id": "c1", "section": "s", "page_start": 1, "page_end": 1,
                     "line_start": 1, "line_end": 6, "text": "t"}],
            suggested_relations=[],
            candidate_rules=[],
        )

    def _load_node(self):
        graph = json.loads((kg_routes.KG_DIR / "CONTRACT-TEST.json").read_text(encoding="utf-8"))
        return {node["id"]: node for node in graph["nodes"]}["ENTITY:disease:I21.0"]

    def test_conforming_attributes_import_cleanly(self):
        result = asyncio.run(import_graph(self._request({"DISEASE_ICD_CODE": "I21.0"})))
        node = self._load_node()
        self.assertNotIn("attribute_violations", node)
        self.assertFalse(any("不在模板属性定义" in w for w in result["warnings"]))

    def test_foreign_attribute_kept_and_marked(self):
        result = asyncio.run(import_graph(self._request({"DISEASE_ICD_CODE": "I21.0", "MYSTERY": "x"})))
        node = self._load_node()
        # 不丢弃：违规值保留在节点属性中，交由审核人确认或修正
        self.assertEqual(node["attributes"]["MYSTERY"], "x")
        self.assertEqual(node["attributes"]["DISEASE_ICD_CODE"], "I21.0")
        # 标记待复核：节点级 metadata，key 形如 attributes.<CODE>
        self.assertEqual(node["attribute_violations"][0]["attribute_code"], "MYSTERY")
        self.assertEqual(node["attribute_violations"][0]["key"], "attributes.MYSTERY")
        self.assertTrue(any("属性 MYSTERY" in w and "待复核" in w for w in result["warnings"]))


class PackagePrecheckContractTest(unittest.TestCase):
    def _package(self, attributes):
        return {
            "package_id": "PKG-CONTRACT",
            "doc_id": "DOC-CONTRACT",
            "doc_title": "属性契约预检测试",
            "domain": "western",
            "template_id": "GENERIC_GUIDELINE_V1",
            "target_disease_code": "I21.0",
            "entities": [
                {"entity_type": "disease", "name": "契约测试病", "code": "I21.0",
                 "chunk_id": "c1", "attributes": attributes},
            ],
            "suggested_relations": [],
            "suggested_rules": [],
        }

    def test_precheck_flags_attribute_violations(self):
        checks = {
            item["code"]: item
            for item in gp._build_checks(self._package({"MYSTERY": "x", "DISEASE_ICD_CODE": "I21.0"}))
        }
        check = checks["attribute_contract"]
        self.assertEqual(check["status"], "warning")
        self.assertTrue(any("属性 MYSTERY" in issue for issue in check["issues"]))
        self.assertEqual(check["details"][0]["entity"], "契约测试病")
        self.assertEqual(check["details"][0]["key"], "attributes.MYSTERY")

    def test_precheck_passes_without_violations(self):
        checks = {
            item["code"]: item
            for item in gp._build_checks(self._package({"DISEASE_ICD_CODE": "I21.0"}))
        }
        self.assertEqual(checks["attribute_contract"]["status"], "passed")
        self.assertEqual(checks["attribute_contract"]["issues"], [])


if __name__ == "__main__":
    unittest.main()
