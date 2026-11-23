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
from unittest.mock import patch

from fastapi import HTTPException

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes import guide_packages, kg as kg_routes
from src.api.routes.vocab import _validate_entry
from src.domains import get_profile, normalize_domain
from src.domains.tcm.basis_generation import (
    generate_tcm_basis_candidates, materialize_syndrome_formula_relations,
)
from src.domains.tcm.checks import TcmGraphSemanticChecker
from src.engine import InferenceEngine
from src.extraction.models import TemplateEntityEntry
from src.extraction.repository import ExtractionTemplateRepository
from src.extraction.service import _validate_llm_payload, complete_tcm_formula_relations, extract_local
from src.kg.client import JsonGraphClient
from src.kg.schema import ConceptVocabulary
from src.models.patient import PatientSnapshot
from src.models.rule import Rule
from src.rules.repository import JsonRuleRepository
from src.standards.repository import JsonStandardRepository
from src.vocab.entity_vocabulary import EntityVocabularyRepository, resolve_template


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
    return ExtractionTemplateRepository().get("TCM_GUIDELINE_V3")


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
            ["tcm_element", "tcm_pattern", "tcm_treatment", "tcm_prescription", "tcm_contraindication"],
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

    def test_tcm_template_uses_shared_tcm_diseases_without_icd10_leakage(self):
        codes = self._disease_codes("TCM_GUIDELINE_V3")
        self.assertIn("TCM_DIS_XIONG_BI_XIN_TONG", codes)
        self.assertIn("TCM_DIS_XIN_SHUAI", codes)
        self.assertNotIn("I21.0", codes)

    def test_tcm_dictionary_rejects_icd10_and_keeps_stable_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = EntityVocabularyRepository("tcm_disease", Path(directory))
            with self.assertRaises(HTTPException):
                repo.save(TemplateEntityEntry(code="I50.9", name="心力衰竭"), is_new=True)
            entry = repo.save(TemplateEntityEntry(code="TCM_DIS_TEST", name="测试病名"), is_new=True)
            self.assertEqual(entry.domain, "tcm")
            self.assertEqual(repo.find("TCM_DIS_TEST").name, "测试病名")

    def test_tcm_graph_rejects_icd10_in_entities_and_relation_endpoints(self):
        entity = {"entity_type": "disease", "code": "I50.9", "name": "心力衰竭"}
        relation = {"source_type": "disease", "source_code": "I50.9",
                    "relation_code": "DISEASE_MAPS_TO_WESTERN",
                    "target_type": "western_disease", "target_code": "I50.9"}
        with tempfile.TemporaryDirectory() as directory, patch.object(kg_routes, "KG_DIR", Path(directory)):
            for entities, relations in [([entity], []), ([], [relation])]:
                request = kg_routes.GraphImportRequest(
                    doc_id="DOC-TCM-T", doc_title="测试指南", domain="tcm",
                    graph_filename="tcm_test.json", entities=entities, chunks=[],
                    suggested_relations=relations,
                )
                with self.assertRaisesRegex(HTTPException, "尚未登记到中医疾病词典"):
                    asyncio.run(kg_routes.import_graph(request))
            self.assertFalse((Path(directory) / "tcm_test.json").exists())

    def test_local_tcm_extraction_maps_heart_failure_aliases_to_dictionary(self):
        template = ExtractionTemplateRepository().get("TCM_GUIDELINE_V3")
        chunk = make_chunk("c1", "慢性心力衰竭\n慢性心衰归属于中医学“心衰”的范畴。")
        result = extract_local([chunk], template, "DOC-TCM-T", "测试指南")
        diseases = [item for item in result.entities if item["entity_type"] == "disease"]
        # 指南里的“慢性心力衰竭”经字典别名归一到标准病名心衰（GB/T 16751.1），
        # 证据锚定在该 chunk 中最早出现的别名上，同一 chunk 只留一条记录。
        self.assertEqual([item["code"] for item in diseases], ["TCM_DIS_XIN_SHUAI"])
        self.assertEqual(diseases[0]["name"], "心衰")
        self.assertEqual(diseases[0]["line_start"], 1)
        self.assertIn("慢性心力衰竭", diseases[0]["quote"])

    def test_llm_tcm_disease_alias_maps_to_dictionary_code(self):
        template = resolve_template(ExtractionTemplateRepository().get("TCM_GUIDELINE_V3"))
        chunk = make_chunk("c1", "慢性心力衰竭患者中医辨证常属气虚血瘀证。")
        payload = {"entities": [
            {"entity_type": "disease", "name": "慢性心力衰竭", "code": "",
             "quote": "慢性心力衰竭", "chunk_id": "c1"},
        ]}
        entities, _, _, warnings, *_ = _validate_llm_payload(
            payload, [chunk], template, "DOC-TCM-T", "测试指南",
        )
        # 模型返回指南原文写法而非标准病名时，按字典别名唯一命中归一编码。
        self.assertEqual([item["code"] for item in entities], ["TCM_DIS_XIN_SHUAI"])
        self.assertEqual(entities[0]["name"], "心衰")
        self.assertNotIn("慢性心力衰竭", {item["name"] for item in entities})

    def test_llm_tcm_diseases_keep_dictionary_codes_and_require_located_quotes(self):
        template = resolve_template(ExtractionTemplateRepository().get("TCM_GUIDELINE_V3"))
        chunk = make_chunk("c1", "慢性心衰归属于中医学“心衰”的范畴。另载某试验病。")
        candidates = [
            {"entity_type": "disease", "name": "心衰", "code": "I50.9", "quote": "“心衰”"},
            {"entity_type": "disease", "name": "某试验病", "code": "D999", "quote": "某试验病"},
            {"entity_type": "disease", "name": "标题病名", "quote": "标题病名"},
            {"entity_type": "western_disease", "name": "心力衰竭", "code": "I50.9", "quote": "慢性心衰"},
        ]
        payload = {"entities": [{**item, "chunk_id": "c1"} for item in candidates]}
        entities, _, _, warnings, *_ = _validate_llm_payload(
            payload, [chunk], template, "DOC-TCM-T", "标题病名",
        )
        by_name = {item["name"]: item for item in entities}
        self.assertEqual(by_name["心衰"]["code"], "TCM_DIS_XIN_SHUAI")
        self.assertEqual(by_name["某试验病"]["candidate_status"], "pending")
        self.assertEqual(by_name["心力衰竭"]["code"], "I50.9")
        self.assertNotIn("标题病名", by_name)
        self.assertTrue(any("标题病名" in warning for warning in warnings))

    def test_tcm_graph_requires_literal_quote_for_cross_diagnosis_mapping(self):
        text = "慢性心衰归属于中医学“心衰”的范畴。"
        chunk = make_chunk("c1", text)
        entities = [
            {"entity_type": "disease", "code": "TCM_DIS_XIN_SHUAI", "name": "心衰", "chunk_id": "c1"},
            {"entity_type": "western_disease", "code": "WESTERN-TEST", "name": "慢性心衰", "chunk_id": "c1"},
        ]
        relation = {"source_type": "disease", "source_code": "TCM_DIS_XIN_SHUAI",
                    "relation_code": "DISEASE_MAPS_TO_WESTERN",
                    "target_type": "western_disease", "target_code": "WESTERN-TEST", "chunk_id": "c1"}
        with tempfile.TemporaryDirectory() as directory, patch.object(kg_routes, "KG_DIR", Path(directory)):
            for index, quote in enumerate(("", text)):
                filename = f"tcm_mapping_test_{index}.json"
                request = kg_routes.GraphImportRequest(
                    doc_id="DOC-TCM-T", doc_title="测试指南", domain="tcm",
                    graph_filename=filename, entities=entities, chunks=[chunk],
                    suggested_relations=[{**relation, "quote": quote}],
                )
                asyncio.run(kg_routes.import_graph(request))
                graph = json.loads((Path(directory) / filename).read_text(encoding="utf-8"))
                # 导入时 DISEASE_MAPS_TO_WESTERN 一律降级为评审候选
                # GUIDELINE_SUGGESTS_DISEASE_MAPPING；无逐字引文时整条丢弃。
                mappings = [
                    item for item in graph["relations"]
                    if item["relation_code"] == "GUIDELINE_SUGGESTS_DISEASE_MAPPING"
                ]
                self.assertEqual(len(mappings), int(bool(quote)))
                if mappings:
                    source = mappings[0]["attributes"]["source"]
                    self.assertEqual((source["page"], source["line_start"], source["quote"]), (1, 1, text))

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
        self.assertEqual(template.graph_filename, "")
        self.assertIn("tcm_pattern", template.capabilities)
        self.assertIn("FORMULA_CONTAINS_HERB", {item.relation_code for item in template.relations})
        self.assertIn("SYNDROME_RECOMMENDS_FORMULA", {item.relation_code for item in template.relations})

    def test_formula_chain_is_materialized_for_direct_query(self):
        template = tcm_template()
        entities = [
            {"entity_type": "syndrome", "code": "S1", "chunk_id": "c1"},
            {"entity_type": "treatment_method", "code": "M1", "chunk_id": "c1"},
            {"entity_type": "formula", "code": "F1", "chunk_id": "c1", "quote": "治以益气养阴，方用生脉散"},
        ]
        relations = [
            {"relation_code": "SYNDROME_TREATED_BY_METHOD", "source_code": "S1", "target_code": "M1", "chunk_id": "c1"},
            {"relation_code": "METHOD_HAS_FORMULA", "source_code": "M1", "target_code": "F1", "chunk_id": "c1", "quote": "方用生脉散", "confidence": 0.8},
        ]
        completed = complete_tcm_formula_relations(entities, relations, template)
        direct = [item for item in completed if item["relation_code"] == "SYNDROME_RECOMMENDS_FORMULA"]
        self.assertEqual([(item["source_code"], item["target_code"]) for item in direct], [("S1", "F1")])

    def test_graph_chain_generates_reviewable_pattern_treatment_and_prescription_bases(self):
        def node(kind, code, name):
            return {"id": f"ENTITY:{kind}:{code}", "node_type": kind, "code": code,
                    "name": name, "review_status": "active", "attributes": {}}
        nodes = [
            node("syndrome", "S1", "气阴两虚证"), node("symptom", "Y1", "气短"),
            node("treatment_method", "M1", "益气养阴"), node("formula", "F1", "生脉散"),
            node("herb", "H1", "人参"),
        ]
        relations = []
        for source, code, target in [
            ("ENTITY:syndrome:S1", "SYNDROME_HAS_SYMPTOM", "ENTITY:symptom:Y1"),
            ("ENTITY:syndrome:S1", "SYNDROME_TREATED_BY_METHOD", "ENTITY:treatment_method:M1"),
            ("ENTITY:syndrome:S1", "SYNDROME_RECOMMENDS_FORMULA", "ENTITY:formula:F1"),
            ("ENTITY:treatment_method:M1", "METHOD_HAS_FORMULA", "ENTITY:formula:F1"),
            ("ENTITY:formula:F1", "FORMULA_CONTAINS_HERB", "ENTITY:herb:H1"),
        ]:
            relations.append({"source_node_id": source, "relation_code": code,
                              "target_node_id": target, "source_refs": ["c1"],
                              "review_status": "active"})
        graph = {"nodes": nodes, "relations": relations, "sources": [{
            "chunk_id": "c1", "doc_id": "D1", "doc_title": "指南", "page_start": 1,
            "page_end": 1, "line_start": 1, "line_end": 2, "quote": "气阴两虚，治以益气养阴，方用生脉散。",
        }]}
        candidates = generate_tcm_basis_candidates(graph, "D1", "tcm.json")
        self.assertEqual({item["rule_type"] for item in candidates},
                         {"tcm_pattern", "tcm_treatment", "tcm_prescription"})
        prescription = next(item for item in candidates if item["rule_type"] == "tcm_prescription")
        self.assertEqual(prescription["conclusion"]["herbs"][0]["name"], "人参")
        checked = TcmGraphSemanticChecker(graph).check_rule(prescription)
        self.assertEqual(checked["semantic_status"], "consistent")
        self.assertEqual(checked["dependency_status"], "passed")

    def test_existing_method_formula_chain_backfills_reviewable_direct_edge(self):
        graph = {"relations": [
            {"source_node_id": "ENTITY:syndrome:S1", "relation_code": "SYNDROME_TREATED_BY_METHOD",
             "target_node_id": "ENTITY:treatment_method:M1", "source_refs": ["c1"]},
            {"source_node_id": "ENTITY:treatment_method:M1", "relation_code": "METHOD_HAS_FORMULA",
             "target_node_id": "ENTITY:formula:F1", "source_refs": ["c1"]},
        ]}
        self.assertEqual(materialize_syndrome_formula_relations(graph), 1)
        direct = graph["relations"][-1]
        self.assertEqual(direct["relation_code"], "SYNDROME_RECOMMENDS_FORMULA")
        self.assertEqual(direct["review_status"], "draft")

    def test_v3_is_the_only_active_tcm_template(self):
        templates = [item for item in ExtractionTemplateRepository().list_available() if item.domain == "tcm"]
        self.assertEqual({item.template_id for item in templates}, {"TCM_GUIDELINE_V3"})
        self.assertEqual([item.template_id for item in templates if item.is_default], ["TCM_GUIDELINE_V3"])

    def test_local_extraction_yields_entities_and_relations_without_rules(self):
        result = extract_local(CHUNKS, tcm_template(), "DOC-TCM-T", "中医测试指南")
        counts = {}
        for entity in result.entities:
            counts[entity["entity_type"]] = counts.get(entity["entity_type"], 0) + 1
        self.assertEqual(counts.get("syndrome"), 3)
        self.assertGreaterEqual(counts.get("tongue", 0), 4)
        self.assertGreaterEqual(counts.get("pulse", 0), 3)
        self.assertGreaterEqual(counts.get("herb", 0), 10)

        # V3 起本地抽取不再直出规则；规则改由图谱基础生成与 V3 陈述契约派生。
        self.assertEqual(result.suggested_rules, [])
        relation_codes = {relation["relation_code"] for relation in result.suggested_relations}
        self.assertIn("SYNDROME_TREATED_BY_METHOD", relation_codes)
        self.assertIn("METHOD_HAS_FORMULA", relation_codes)
        self.assertIn("FORMULA_CONTAINS_HERB", relation_codes)

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
        # V3 之下本地抽取只产实体与关系；规则由图谱基础生成派生。
        self.assertEqual(self.result.suggested_rules, [])
        request = kg_routes.GraphImportRequest(
            doc_id="DOC-TCM-T",
            doc_title="中医测试指南",
            domain="tcm",
            target_disease_code="TCM_SYN_QI_YIN_XU",
            entities=self.result.entities,
            chunks=CHUNKS,
            suggested_relations=self.result.suggested_relations,
            template_id="TCM_GUIDELINE_V3",
            graph_filename="tcm_test_graph.json",
            candidate_rules=self.result.suggested_rules,
        )
        self.graph_result = asyncio.run(kg_routes.import_graph(request))

        imported_graph = json.loads(
            (self.kg_dir / "tcm_test_graph.json").read_text(encoding="utf-8")
        )
        rules = []
        for raw in generate_tcm_basis_candidates(imported_graph, "DOC-TCM-T", "tcm_test_graph.json"):
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
        conclusions = engine.run(patient, include_draft_rules=True)
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
                {"extraction": {"template_id": "TCM_GUIDELINE_V3"}}, "", "tcm"
            ),
            "TCM_GUIDELINE_V3",
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
            "template_id": "TCM_GUIDELINE_V3",
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
