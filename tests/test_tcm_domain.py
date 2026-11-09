"""TCM domain tests: template, discovery, graph import, rules, inference.

The seed terminology lives in data/vocab/entities; these tests exercise the
full TCM pipeline without touching the western fixtures.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes import guide_packages, kg as kg_routes
from src.api.routes.vocab import _validate_entry
from src.domains import get_profile, normalize_domain
from src.engine import InferenceEngine
from src.extraction.models import TemplateEntityEntry
from src.extraction.repository import ExtractionTemplateRepository
from src.extraction.service import extract_local
from src.kg.client import JsonGraphClient
from src.kg.schema import ConceptVocabulary
from src.models.patient import PatientSnapshot
from src.models.rule import Rule
from src.rules.repository import JsonRuleRepository
from src.standards.repository import JsonStandardRepository
from src.vocab.entity_vocabulary import resolve_template


def make_chunk(chunk_id: str, text: str, page: int = 1) -> dict:
    lines = text.split("\n")
    return {
        "chunk_id": chunk_id,
        "section": chunk_id,
        "page_start": page,
        "page_end": page,
        "line_start": 1,
        "line_end": len(lines),
        "text": text,
        "page_numbers": [page],
        "line_numbers": list(range(1, len(lines) + 1)),
    }


CHUNKS = [
    make_chunk("c1", "胸痹心痛病\n气阴两虚证：胸闷、气短、乏力、自汗，舌淡红，苔薄白，脉细弱。治法：益气养阴。方药：生脉散（人参10g、麦冬15g、五味子6g）。"),
    make_chunk("c2", "胸痹心痛病\n痰浊闭阻证：胸闷、痰多、肢体沉重，舌淡胖，苔白腻，脉弦滑。治法：通阳泄浊豁痰。方药：瓜蒌薤白半夏汤（瓜蒌15g、薤白12g、半夏9g）。", page=2),
    make_chunk("c3", "胸痹心痛病\n心血瘀阻证：胸痛、心悸、唇甲紫暗，舌紫暗，脉涩。治法：活血化瘀通络。方药：血府逐瘀汤（桃仁12g、红花9g、当归9g、川芎6g、赤芍6g、柴胡3g、枳壳6g、桔梗6g、牛膝9g、甘草6g）。", page=3),
]


def tcm_template():
    return ExtractionTemplateRepository().get("TCM_GUIDELINE_V1")


class DomainProfileTest(unittest.TestCase):
    def test_legacy_domains_resolve_to_western(self):
        for value in ("", None, "通用", "心血管内科", "血液内科"):
            self.assertEqual(normalize_domain(value), "western")
            self.assertEqual(get_profile(value).domain, "western")
        self.assertEqual(normalize_domain("中医"), "tcm")
        self.assertEqual(get_profile("中医").domain, "tcm")

    def test_tcm_stage_order(self):
        profile = get_profile("tcm")
        self.assertEqual(
            [stage.name for stage in profile.stages],
            ["tcm_pattern", "tcm_treatment", "tcm_prescription", "tcm_contraindication"],
        )
        self.assertEqual(profile.graph.anchor_types, ("syndrome", "disease"))


class DiseaseDictionaryTest(unittest.TestCase):
    def _disease_codes(self, template_id: str):
        template = resolve_template(ExtractionTemplateRepository().get(template_id))
        disease_type = next(item for item in template.entity_types if item.entity_type == "disease")
        return {entry.code for entry in disease_type.entries}

    def test_western_template_uses_icd10_diseases(self):
        codes = self._disease_codes("GENERIC_GUIDELINE_V1")
        self.assertIn("I21.0", codes)
        self.assertIn("C83.3", codes)
        self.assertNotIn("TCM_DIS_XIONG_BI_XIN_TONG", codes)

    def test_tcm_template_uses_local_tcm_diseases_without_icd10_leakage(self):
        codes = self._disease_codes("TCM_GUIDELINE_V1")
        self.assertIn("TCM_DIS_XIONG_BI_XIN_TONG", codes)
        self.assertNotIn("I21.0", codes)

    def test_disease_entries_are_icd10_only(self):
        western = _validate_entry("disease", TemplateEntityEntry(
            code="I50.9", name="心力衰竭", standard_system="ICD-10",
        ))
        self.assertEqual(western.domain, "western")
        self.assertEqual(western.standard_system, "ICD-10")
        self.assertEqual(western.standard_code, "I50.9")
        self.assertEqual(western.source, "ICD-10")

        from fastapi import HTTPException
        rejected = [
            TemplateEntityEntry(code="X1", name="非 ICD 编码"),
            TemplateEntityEntry(code="TCM_DIS_X", name="中医病", domain="tcm", standard_system="ICD-11 TM1"),
            TemplateEntityEntry(code="I50.9", name="心力衰竭", standard_code="LOCAL-I50"),
            TemplateEntityEntry(code="I50.9", name="心力衰竭", standard_system="ICD-11 TM1"),
        ]
        for entry in rejected:
            with self.assertRaises(HTTPException):
                _validate_entry("disease", entry)


class TcmTemplateTest(unittest.TestCase):
    def test_template_contract(self):
        template = tcm_template()
        self.assertEqual(template.domain, "tcm")
        self.assertEqual(template.graph_filename, "tcm_graph.json")
        self.assertIn("tcm_pattern", template.capabilities)
        self.assertIn("FORMULA_CONTAINS_HERB", {item.relation_code for item in template.relations})

    def test_local_extraction_and_rule_families(self):
        result = extract_local(CHUNKS, tcm_template(), "DOC-TCM-T", "中医测试指南")
        counts = {}
        for entity in result.entities:
            counts[entity["entity_type"]] = counts.get(entity["entity_type"], 0) + 1
        self.assertEqual(counts.get("syndrome"), 3)
        self.assertGreaterEqual(counts.get("tongue", 0), 4)
        self.assertGreaterEqual(counts.get("pulse", 0), 3)
        self.assertGreaterEqual(counts.get("herb", 0), 10)

        rule_types = {rule["rule_type"] for rule in result.suggested_rules}
        self.assertEqual(
            rule_types,
            {"tcm_pattern", "tcm_treatment", "tcm_prescription"},
        )
        for rule in result.suggested_rules:
            Rule(**rule)
            self.assertEqual(rule["domain"], "tcm")
            self.assertEqual(rule["anchor_type"], "syndrome")

        prescriptions = [rule for rule in result.suggested_rules if rule["rule_type"] == "tcm_prescription"]
        self.assertEqual(len(prescriptions), 3)
        formula_names = {rule["conclusion"]["formula_name"] for rule in prescriptions}
        self.assertIn("生脉散", formula_names)
        shengmai = next(rule for rule in prescriptions if rule["conclusion"]["formula_name"] == "生脉散")
        self.assertEqual(len(shengmai["conclusion"]["herbs"]), 3)
        self.assertEqual(shengmai["conclusion"]["herbs"][0]["role"], "君")

    def test_syndrome_discovery_for_unknown_name(self):
        chunk = make_chunk("c9", "肝郁气滞证：胸闷、善太息，舌淡红，苔薄白，脉弦。")
        result = extract_local([chunk], tcm_template(), "DOC-TCM-DISC", "中医发现测试")
        syndromes = [item for item in result.entities if item["entity_type"] == "syndrome"]
        self.assertTrue(syndromes)
        self.assertEqual(syndromes[0]["name"], "肝郁气滞证")
        self.assertTrue(syndromes[0]["code"].startswith("AUTO-"))


class TcmGraphAndInferenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.kg_dir = self.root / "kg"
        self.kg_dir.mkdir()
        self.rules_dir = self.root / "rules"
        self.rules_dir.mkdir()
        self.standards_dir = self.root / "standards"
        self.standards_dir.mkdir()

        self._original_kg_dir = kg_routes.KG_DIR
        self._original_resolver = kg_routes._resolve_import_template
        kg_routes.KG_DIR = self.kg_dir
        template = tcm_template()
        kg_routes._resolve_import_template = lambda template_id: template

        vocab_dir = self.root / "vocab"
        vocab_dir.mkdir()
        shutil.copy(project_root / "data" / "vocab" / "concepts.json", vocab_dir / "concepts.json")

        self.template = template
        self.result = extract_local(CHUNKS, template, "DOC-TCM-T", "中医测试指南")
        request = kg_routes.GraphImportRequest(
            doc_id="DOC-TCM-T",
            doc_title="中医测试指南",
            domain="tcm",
            target_disease_code="TCM_SYN_QI_YIN_XU",
            entities=self.result.entities,
            chunks=CHUNKS,
            suggested_relations=self.result.suggested_relations,
            template_id="TCM_GUIDELINE_V1",
            graph_filename="tcm_test_graph.json",
            candidate_rules=self.result.suggested_rules,
        )
        self.graph_result = asyncio.run(kg_routes.import_graph(request))

        rules = []
        for raw in self.result.suggested_rules:
            item = Rule(**raw).model_dump()
            item["graph_filename"] = "tcm_test_graph.json"
            if item["rule_type"] == "tcm_pattern":
                item["status"] = "active"
            rules.append(item)
        (self.rules_dir / "managed_rules.json").write_text(
            json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def tearDown(self):
        kg_routes.KG_DIR = self._original_kg_dir
        kg_routes._resolve_import_template = self._original_resolver
        self.tmp.cleanup()

    def test_graph_is_node_native_tcm(self):
        self.assertEqual(self.graph_result["domain"], "tcm")
        self.assertEqual(self.graph_result["syndromes_added"], 3)
        self.assertEqual(self.graph_result["evidences_added"], 0)
        graph = json.loads((self.kg_dir / "tcm_test_graph.json").read_text(encoding="utf-8"))
        self.assertEqual(graph["domain"], "tcm")
        self.assertGreaterEqual(graph["schema_version"], 2)
        node_types = {node["node_type"] for node in graph["nodes"]}
        self.assertTrue({"syndrome", "tongue", "pulse", "formula", "herb"} <= node_types)
        herb = next(node for node in graph["nodes"] if node["node_type"] == "herb")
        self.assertIn("HERB_ROLE", herb["attributes"])
        relations = {relation["relation_code"] for relation in graph["relations"]}
        self.assertIn("SYNDROME_HAS_TONGUE", relations)
        self.assertIn("FORMULA_CONTAINS_HERB", relations)

    def test_staged_tcm_inference(self):
        patient_payload = json.loads(
            (project_root / "data" / "patients" / "demo_tcm_patient.json").read_text(encoding="utf-8")
        )
        patient = PatientSnapshot(**patient_payload)
        vocab = ConceptVocabulary()
        vocab.load(str(project_root / "data" / "vocab" / "concepts.json"))
        kg = JsonGraphClient(str(self.kg_dir / "tcm_test_graph.json"))
        rules = JsonRuleRepository(str(self.rules_dir))
        standards = JsonStandardRepository(str(self.standards_dir))

        def graph_factory(filename: str) -> JsonGraphClient:
            return JsonGraphClient(str(self.kg_dir / filename))

        engine = InferenceEngine(rules, kg, standards, vocab, graph_factory, domain="tcm")
        conclusions = engine.run(patient)
        by_type = {}
        for item in conclusions:
            by_type.setdefault(item.rule_type, []).append(item)

        self.assertEqual(len(by_type.get("tcm_pattern", [])), 1)
        pattern = by_type["tcm_pattern"][0]
        self.assertEqual(pattern.anchor_code, "TCM_SYN_QI_YIN_XU")
        self.assertEqual(pattern.anchor_name, "气阴两虚证")
        self.assertGreater(pattern.score or 0, 0.8)

        treatment = by_type["tcm_treatment"][0]
        self.assertEqual(treatment.treatment_method_code, "TCM_METHOD_YIQI_YANGYIN")
        self.assertIn(pattern.rule_id, treatment.triggered_by)

        prescription = by_type["tcm_prescription"][0]
        self.assertEqual(prescription.formula_name, "生脉散")
        self.assertEqual(len(prescription.herbs), 3)
        self.assertIn(pattern.rule_id, prescription.triggered_by)


class TcmPackageChecksTest(unittest.TestCase):
    def test_target_selection_prefers_syndrome(self):
        entities = [
            {"entity_type": "disease", "code": "TCM_DIS_XIONG_BI_XIN_TONG", "confidence": 0.99},
            {"entity_type": "syndrome", "code": "TCM_SYN_QI_YIN_XU", "confidence": 0.7},
        ]
        self.assertEqual(
            guide_packages._selected_target(entities, "", ("syndrome", "disease")),
            "TCM_SYN_QI_YIN_XU",
        )
        self.assertEqual(
            guide_packages._selected_template_id(
                {"extraction": {"template_id": "TCM_GUIDELINE_V1"}}, "", "tcm"
            ),
            "TCM_GUIDELINE_V1",
        )

    def test_target_selection_prefers_most_frequent_anchor(self):
        entities = [
            {"entity_type": "disease", "code": "I42.1", "confidence": 0.99},
            {"entity_type": "disease", "code": "I42.2", "confidence": 0.7},
            {"entity_type": "disease", "code": "I42.2", "confidence": 0.7},
            {"entity_type": "disease", "code": "I42.2", "confidence": 0.7},
        ]
        self.assertEqual(
            guide_packages._selected_target(entities, "", ("disease",)),
            "I42.2",
        )

    def _package(self, entities, relations):
        return {
            "domain": "tcm",
            "template_id": "TCM_GUIDELINE_V1",
            "target_disease_code": "TCM_SYN_QI_YIN_XU",
            "entities": entities,
            "suggested_relations": relations,
            "suggested_rules": [{
                "rule_id": "R1",
                "domain": "tcm",
                "anchor_type": "syndrome",
                "rule_type": "tcm_pattern",
                "disease_code": "TCM_SYN_QI_YIN_XU",
                "disease_name": "气阴两虚证",
                "conditions": {"type": "observation", "observation_type": "symptom", "code": "SYM_CHEST_TIGHTNESS"},
                "conclusion": {"diagnosis": "TCM_SYN_QI_YIN_XU"},
                "source": {"guideline_id": "D1", "page_start": 1, "line_start": 1},
            }],
        }

    def test_tcm_checks_cover_completeness_and_conflicts(self):
        entities = [
            {"entity_type": "syndrome", "code": "TCM_SYN_QI_YIN_XU", "name": "气阴两虚证", "chunk_id": "c1"},
            {"entity_type": "symptom", "code": "SYM_CHEST_TIGHTNESS", "name": "胸闷", "chunk_id": "c1"},
            {"entity_type": "formula", "code": "TCM_FORMULA_SHENGMAI_SAN", "name": "生脉散", "chunk_id": "c1"},
            {"entity_type": "herb", "code": "TCM_HERB_GANCAO", "name": "甘草", "chunk_id": "c1"},
            {"entity_type": "herb", "code": "TCM_HERB_GANSUI", "name": "甘遂", "chunk_id": "c1"},
        ]
        relations = [
            {"relation_code": "FORMULA_CONTAINS_HERB", "source_code": "TCM_FORMULA_SHENGMAI_SAN", "target_code": "TCM_HERB_GANCAO"},
            {"relation_code": "FORMULA_CONTAINS_HERB", "source_code": "TCM_FORMULA_SHENGMAI_SAN", "target_code": "TCM_HERB_GANSUI"},
        ]
        checks = {item["code"]: item for item in guide_packages._build_checks(self._package(entities, relations))}
        self.assertIn("tcm_syndrome_completeness", checks)
        self.assertIn("tcm_formula_composition", checks)
        self.assertEqual(checks["tcm_contraindication_conflicts"]["status"], "failed")
        self.assertTrue(any("甘草" in issue for issue in checks["tcm_contraindication_conflicts"]["issues"]))
        self.assertNotIn("hospital_standards", checks)


if __name__ == "__main__":
    unittest.main()
