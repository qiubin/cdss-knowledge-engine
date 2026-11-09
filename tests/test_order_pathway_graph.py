#!/usr/bin/env python3
"""Tests for the order-set / care-pathway graph layer.

Covers the guideline → order-set extraction renovation:
1. Domain profile exposes the new entity types, node metadata and pathway panel.
2. Western extraction templates carry the order/pathway contract.
3. Terminology resolver accepts order/pathway aliases and generates local codes.
4. Rule generation attaches order_set_ids to treatment conclusions.
5. Rule/Conclusion models and graph projection materialize order sets.
6. KG import turns order/pathway entities + relations into semantic nodes.
"""
import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes import kg as kg_routes
from src.domains import get_profile
from src.domains.western.rule_generation import generate_western_rules
from src.extraction.auto_pipeline import _auto_prompt
from src.extraction.models import (
    ExtractionTemplate,
    TemplateEntityEntry,
    TemplateEntityType,
)
from src.extraction.repository import ExtractionTemplateRepository
from src.extraction.terminology_resolver import local_code, normalize_type
from src.models.conclusion import Conclusion
from src.models.rule import Rule, RuleConclusion

ORDER_TYPES = {"order_set", "order_item", "care_pathway", "pathway_phase"}
ORDER_RELATIONS = {
    "TREATMENT_PLAN_MATERIALIZED_AS",
    "ORDER_SET_INCLUDES_ITEM",
    "DISEASE_HAS_CARE_PATHWAY",
    "CARE_PATHWAY_HAS_PHASE",
    "PHASE_USES_ORDER_SET",
}


def make_template() -> ExtractionTemplate:
    return ExtractionTemplate(
        template_id="TEST_ORDER_V1",
        name="医嘱套餐规则生成测试",
        domain="western",
        graph_filename="TEST_ORDER_GRAPH.json",
        entity_types=[
            TemplateEntityType(entity_type="disease", label="疾病", entries=[
                TemplateEntityEntry(code="I21.0", name="急性心肌梗死", aliases=["急性心肌梗死"]),
            ]),
            TemplateEntityType(entity_type="symptom", label="症状"),
            TemplateEntityType(entity_type="lab", label="检验"),
            TemplateEntityType(entity_type="concept", label="概念"),
            TemplateEntityType(entity_type="diagnostic_evidence", label="证据"),
            TemplateEntityType(entity_type="medication", label="药物"),
            TemplateEntityType(entity_type="order_set", label="医嘱套餐"),
            TemplateEntityType(entity_type="order_item", label="医嘱条目"),
            TemplateEntityType(entity_type="care_pathway", label="诊疗路径"),
            TemplateEntityType(entity_type="pathway_phase", label="路径阶段"),
        ],
    )


def make_chunk() -> dict:
    return {
        "chunk_id": "c1",
        "section": "治疗",
        "page_start": 1,
        "page_end": 1,
        "line_start": 1,
        "line_end": 30,
        "text": "急性心肌梗死\n肌钙蛋白明显升高\n胸痛\nSTEMI再灌注医嘱套餐\n阿司匹林负荷剂量",
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


class OrderPathwayProfileTest(unittest.TestCase):
    def test_western_profile_supports_order_pathway_types(self):
        profile = get_profile("western")
        allowed = set(profile.graph.allowed_entity_types)
        self.assertTrue(ORDER_TYPES.issubset(allowed))
        for node_type in ORDER_TYPES:
            self.assertIn(node_type, profile.node_meta)
            self.assertTrue(profile.node_meta[node_type].get("label"))
        specs = " ".join(panel.get("spec", "") for panel in profile.path_panels)
        self.assertIn("care_pathway", specs)
        self.assertIn("order_set", specs)

    def test_western_templates_carry_order_pathway_contract(self):
        repo = ExtractionTemplateRepository()
        for template_id in (
            "GENERIC_GUIDELINE_V1",
            "CARDIO_DIAGNOSIS_V1",
            "LYMPHOMA_GUIDELINE_V1",
            "UNIVERSAL_GUIDELINE_V1",
        ):
            template = repo.get(template_id, allow_retired=True)
            self.assertTrue(
                ORDER_TYPES.issubset(set(template.allowed_entity_types)),
                f"{template_id} missing order/pathway entity types",
            )
            codes = {relation.relation_code for relation in template.relations}
            self.assertTrue(
                ORDER_RELATIONS.issubset(codes),
                f"{template_id} missing order/pathway relations",
            )


class OrderTerminologyTest(unittest.TestCase):
    def test_normalize_type_aliases(self):
        self.assertEqual(normalize_type("医嘱套餐"), "order_set")
        self.assertEqual(normalize_type("医嘱条目"), "order_item")
        self.assertEqual(normalize_type("诊疗路径"), "care_pathway")
        self.assertEqual(normalize_type("路径阶段"), "pathway_phase")

    def test_local_codes_use_order_prefixes(self):
        self.assertTrue(local_code("order_set", "STEMI再灌注医嘱套餐").startswith("LOCAL-OS-"))
        self.assertTrue(local_code("order_item", "阿司匹林负荷剂量").startswith("LOCAL-OI-"))
        self.assertTrue(local_code("care_pathway", "STEMI全程路径").startswith("LOCAL-CP-"))
        self.assertTrue(local_code("pathway_phase", "急性期").startswith("LOCAL-PP-"))


class OrderRuleGenerationTest(unittest.TestCase):
    def test_treatment_rule_carries_order_set_ids(self):
        entities = [
            entity("disease", "I21.0", "急性心肌梗死", 10),
            entity("lab", "CTN", "肌钙蛋白", 12, "肌钙蛋白明显升高"),
            entity("symptom", "SYM_CHEST_PAIN", "胸痛", 13),
            entity("order_set", "OS-REPERFUSION", "STEMI再灌注医嘱套餐", 15, "STEMI再灌注医嘱套餐"),
        ]
        rules = generate_western_rules(
            entities, [make_chunk()], make_template(), "DOC-OS", "测试指南"
        )
        types = {rule["rule_type"] for rule in rules}
        self.assertEqual(types, {"diagnosis", "treatment"})
        for rule in rules:
            Rule(**rule)

        treatment = next(rule for rule in rules if rule["rule_type"] == "treatment")
        self.assertEqual(treatment["disease_code"], "I21.0")
        self.assertEqual(
            treatment["conclusion"]["order_set_ids"], ["OS-REPERFUSION"],
        )
        self.assertIn("医嘱套餐", treatment["conclusion"]["message"])

    def test_conclusion_models_accept_order_set_ids(self):
        conclusion = RuleConclusion(diagnosis="I21.0", order_set_ids=["OS-1"])
        self.assertEqual(conclusion.order_set_ids, ["OS-1"])
        output = Conclusion(
            rule_id="R1", disease_code="I21.0", order_set_ids=["OS-1"],
        )
        self.assertEqual(output.order_set_ids, ["OS-1"])

    def test_auto_prompt_requests_order_pathway_extraction(self):
        messages = _auto_prompt([make_chunk()], "测试指南")
        system = messages[0]["content"]
        for token in (
            "order_set", "order_item", "care_pathway", "pathway_phase",
            "ORDER_SET_INCLUDES_ITEM", "PHASE_USES_ORDER_SET",
        ):
            self.assertIn(token, system)


class OrderGraphProjectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._original_kg_dir = kg_routes.KG_DIR
        kg_routes.KG_DIR = Path(self.tmp.name)
        # import_graph reads the concept vocabulary relative to KG_DIR.
        vocab_dir = Path(self.tmp.name).parent / "vocab"
        vocab_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(
            project_root / "data" / "vocab" / "concepts.json",
            vocab_dir / "concepts.json",
        )

    def tearDown(self):
        kg_routes.KG_DIR = self._original_kg_dir
        self.tmp.cleanup()

    def _graph(self):
        return {"domain": "western", "nodes": {}, "relations": {}}

    def test_treatment_plan_semantics_project_order_set(self):
        graph = self._graph()
        rule = {
            "rule_type": "treatment",
            "disease_code": "I21.0",
            "disease_name": "急性心肌梗死",
            "source": {"chunk_id": "c1"},
            "conclusion": {
                "treatment_plan_id": "TP-DOC-OS-I21.0",
                "treatment_name": "急性心肌梗死治疗方案",
                "treatment_items": [],
                "order_set_ids": ["OS-REPERFUSION"],
            },
        }
        relation_names = kg_routes._relation_names()
        kg_routes._ensure_treatment_plan_semantics(graph, rule, relation_names)
        self.assertIn("ENTITY:order_set:OS-REPERFUSION", graph["nodes"])
        relation = graph["relations"].get((
            "ENTITY:treatment_plan:TP-DOC-OS-I21.0",
            "TREATMENT_PLAN_MATERIALIZED_AS",
            "ENTITY:order_set:OS-REPERFUSION",
        ))
        self.assertIsNotNone(relation)
        self.assertEqual(
            relation["relation_name"], "治疗方案物化为医嘱套餐",
        )
        self.assertEqual(relation["review_status"], "draft")

    def test_order_entities_import_as_semantic_nodes(self):
        template = make_template()
        original_resolver = kg_routes._resolve_import_template
        kg_routes._resolve_import_template = lambda template_id: template
        try:
            request = kg_routes.GraphImportRequest(
                doc_id="DOC-OS2",
                doc_title="测试指南",
                target_disease_code="I21.0",
                template_id="TEST_ORDER_V1",
                graph_filename="test_order_graph.json",
                entities=[
                    {"entity_type": "disease", "code": "I21.0", "name": "急性心肌梗死", "chunk_id": "c1"},
                    {"entity_type": "order_set", "code": "OS-REPERFUSION", "name": "STEMI再灌注医嘱套餐", "chunk_id": "c1",
                     "attributes": {"ORDER_SET_PRIORITY": "preferred"}},
                    {"entity_type": "order_item", "code": "OI-ASPIRIN", "name": "阿司匹林负荷剂量", "chunk_id": "c1",
                     "attributes": {"ORDER_CLASS": "药品", "ORDER_REQUIRED": "required"}},
                    {"entity_type": "care_pathway", "code": "CP-STEMI", "name": "STEMI全程诊疗路径", "chunk_id": "c1"},
                    {"entity_type": "pathway_phase", "code": "PP-ACUTE", "name": "急性期", "chunk_id": "c1",
                     "attributes": {"PHASE_SEQUENCE": 2}},
                ],
                suggested_relations=[
                    {"relation_code": "ORDER_SET_INCLUDES_ITEM", "source_entity_type": "order_set",
                     "source_code": "OS-REPERFUSION", "target_entity_type": "order_item",
                     "target_code": "OI-ASPIRIN", "chunk_id": "c1"},
                    {"relation_code": "CARE_PATHWAY_HAS_PHASE", "source_entity_type": "care_pathway",
                     "source_code": "CP-STEMI", "target_entity_type": "pathway_phase",
                     "target_code": "PP-ACUTE", "chunk_id": "c1"},
                    {"relation_code": "PHASE_USES_ORDER_SET", "source_entity_type": "pathway_phase",
                     "source_code": "PP-ACUTE", "target_entity_type": "order_set",
                     "target_code": "OS-REPERFUSION", "chunk_id": "c1"},
                ],
                chunks=[make_chunk()],
            )
            result = asyncio.run(kg_routes.import_graph(request))
        finally:
            kg_routes._resolve_import_template = original_resolver
        graph = kg_routes._load_graph("test_order_graph.json")
        node_ids = set(graph["nodes"].keys())
        for expected in (
            "ENTITY:order_set:OS-REPERFUSION",
            "ENTITY:order_item:OI-ASPIRIN",
            "ENTITY:care_pathway:CP-STEMI",
            "ENTITY:pathway_phase:PP-ACUTE",
        ):
            self.assertIn(expected, node_ids)
        keys = set(graph["relations"].keys())
        self.assertIn(
            ("ENTITY:order_set:OS-REPERFUSION", "ORDER_SET_INCLUDES_ITEM", "ENTITY:order_item:OI-ASPIRIN"),
            keys,
        )
        self.assertIn(
            ("ENTITY:care_pathway:CP-STEMI", "CARE_PATHWAY_HAS_PHASE", "ENTITY:pathway_phase:PP-ACUTE"),
            keys,
        )
        self.assertIn(
            ("ENTITY:pathway_phase:PP-ACUTE", "PHASE_USES_ORDER_SET", "ENTITY:order_set:OS-REPERFUSION"),
            keys,
        )
        self.assertEqual(graph["nodes"]["ENTITY:order_set:OS-REPERFUSION"]["attributes"].get("ORDER_SET_PRIORITY"), "preferred")
        self.assertEqual(result["warnings"], [])


if __name__ == "__main__":
    unittest.main()
