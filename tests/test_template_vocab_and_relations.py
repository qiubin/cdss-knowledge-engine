#!/usr/bin/env python3
"""Tests for shared entity vocabulary, data-driven relations and per-chunk extraction.

Covers the three-part refactor:
1. Template entity dictionaries live in data/vocab/entities (shared, per type)
   and templates reference them via TemplateEntityType.vocabulary.
2. Relation suggestions and graph import are driven by template.relations
   instead of hard-coded disease/finding/symptom/lab pairs.
3. Local extraction records every chunk occurrence of an entity, not just the
   first mention in the document.
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
from src.extraction.models import (
    ExtractionTemplate,
    TemplateEntityEntry,
    TemplateEntityType,
    TemplateRelation,
)
from src.extraction.service import extract_local, relation_pairs_for_chunk
from src.vocab.entity_vocabulary import (
    EntityVocabularyRepository,
    resolve_template,
    template_private_entries,
)


def make_template(**overrides) -> ExtractionTemplate:
    defaults = dict(
        template_id="TEST_V1",
        name="测试模板",
        entity_types=[
            TemplateEntityType(entity_type="disease", label="疾病", discovery=True),
            TemplateEntityType(
                entity_type="symptom",
                label="症状",
                entries=[TemplateEntityEntry(code="FEVER", name="发热", aliases=["发热", "发烧"])],
            ),
        ],
        relations=[
            TemplateRelation(
                relation_code="DISEASE_HAS_SYMPTOM",
                name="疾病有症状",
                source_entity_type="disease",
                target_entity_type="symptom",
                concept_code="PRESENT",
            ),
        ],
    )
    defaults.update(overrides)
    return ExtractionTemplate(**defaults)


def make_chunk(chunk_id: str, text: str, page: int = 1) -> dict:
    return {
        "chunk_id": chunk_id,
        "section": "测试",
        "page_start": page,
        "page_end": page,
        "line_start": 1,
        "line_end": 4,
        "text": text,
        "page_numbers": [page],
        "line_numbers": [1, 2, 3, 4],
    }


class SharedVocabularyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_merges_shared_entries_with_local_override(self):
        repo = EntityVocabularyRepository("symptom", self.dir)
        repo.save(TemplateEntityEntry(code="FEVER", name="发烧（共享）", aliases=["发烧"]), is_new=True)
        repo.save(TemplateEntityEntry(code="COUGH", name="咳嗽", aliases=["咳嗽"]), is_new=True)

        template = make_template(
            entity_types=[
                TemplateEntityType(entity_type="disease", label="疾病"),
                TemplateEntityType(
                    entity_type="symptom",
                    label="症状",
                    vocabulary=True,
                    entries=[TemplateEntityEntry(code="FEVER", name="发热", aliases=["发热"])],
                ),
            ]
        )
        resolved = resolve_template(template, self.dir)
        entries = {item.code: item for item in resolved.entity_types[1].entries}
        # Local entry wins over the shared one with the same code.
        self.assertEqual(entries["FEVER"].name, "发热")
        # Shared-only entries are pulled in.
        self.assertEqual(entries["COUGH"].name, "咳嗽")
        # The raw template keeps its private entries untouched.
        self.assertEqual([item.code for item in template.entity_types[1].entries], ["FEVER"])

    def test_local_extraction_matches_shared_entries(self):
        repo = EntityVocabularyRepository("symptom", self.dir)
        repo.save(TemplateEntityEntry(code="PRURITUS", name="瘙痒", aliases=["瘙痒"]), is_new=True)
        template = make_template(
            entity_types=[
                TemplateEntityType(entity_type="disease", label="疾病"),
                TemplateEntityType(entity_type="symptom", label="症状", vocabulary=True),
            ]
        )
        result = extract_local([make_chunk("c1", "患者出现瘙痒不适")], template, "DOC-T")
        self.assertEqual(
            [(item["code"], item["name"]) for item in result.entities],
            [("PRURITUS", "瘙痒")],
        )

    def test_private_entries_exclude_codes_already_in_shared_dictionary(self):
        repo = EntityVocabularyRepository("symptom", self.dir)
        repo.save(TemplateEntityEntry(code="FEVER", name="发热"), is_new=True)
        type_def = TemplateEntityType(
            entity_type="symptom",
            label="症状",
            vocabulary=True,
            entries=[
                TemplateEntityEntry(code="FEVER", name="发热（模板覆盖）"),
                TemplateEntityEntry(code="RASH", name="皮疹"),
            ],
        )

        private = template_private_entries(type_def, self.dir)

        self.assertEqual([entry.code for entry in private], ["RASH"])


class PerChunkOccurrenceTest(unittest.TestCase):
    def test_entity_recorded_once_per_chunk(self):
        template = make_template()
        chunks = [
            make_chunk("c1", "患者发热，查体见发热面容", page=1),
            make_chunk("c2", "术后再次发热", page=5),
        ]
        result = extract_local(chunks, template, "DOC-T")
        fever = [item for item in result.entities if item["code"] == "FEVER"]
        self.assertEqual({item["chunk_id"] for item in fever}, {"c1", "c2"})
        self.assertEqual({item["page"] for item in fever}, {1, 5})


class DataDrivenRelationsTest(unittest.TestCase):
    def test_custom_relation_and_types_pair(self):
        template = make_template(
            entity_types=[
                TemplateEntityType(entity_type="gene", label="基因"),
                TemplateEntityType(entity_type="drug", label="药物"),
            ],
            relations=[
                TemplateRelation(
                    relation_code="GENE_TARGETS_DRUG",
                    name="基因靶向药物",
                    source_entity_type="gene",
                    target_entity_type="drug",
                ),
            ],
        )
        pairs = relation_pairs_for_chunk(
            [
                {"entity_type": "gene", "code": "BRAF", "name": "BRAF"},
                {"entity_type": "drug", "code": "VEMURAFENIB", "name": "维莫非尼"},
                {"entity_type": "symptom", "code": "FEVER", "name": "发热"},
            ],
            template,
        )
        self.assertEqual(
            [(rel.relation_code, src["code"], tgt["code"]) for rel, src, tgt in pairs],
            [("GENE_TARGETS_DRUG", "BRAF", "VEMURAFENIB")],
        )

    def test_multiple_diseases_do_not_cross_product(self):
        template = make_template()
        entities = [
            {"entity_type": "disease", "code": "D1", "name": "甲病"},
            {"entity_type": "disease", "code": "D2", "name": "乙病"},
            {"entity_type": "symptom", "code": "FEVER", "name": "发热"},
        ]
        self.assertEqual(relation_pairs_for_chunk(entities, template), [])
        # An explicit target disease disambiguates the chunk.
        pairs = relation_pairs_for_chunk(entities, template, target_code="D2")
        self.assertEqual([(rel.relation_code, src["code"]) for rel, src, _ in pairs],
                         [("DISEASE_HAS_SYMPTOM", "D2")])

    def test_concept_bound_relation_uses_synthetic_target(self):
        template = make_template(
            entity_types=[TemplateEntityType(entity_type="symptom", label="症状")],
            relations=[
                TemplateRelation(
                    relation_code="SYMPTOM_HAS_CONCEPT",
                    name="症状形成证据",
                    source_entity_type="symptom",
                    target_entity_type="concept",
                    concept_code="PRESENT",
                ),
            ],
        )
        pairs = relation_pairs_for_chunk(
            [{"entity_type": "symptom", "code": "FEVER", "name": "发热"}], template
        )
        self.assertEqual(pairs[0][2]["concept_code"], "PRESENT")

    def test_local_relations_follow_template_contract(self):
        template = make_template()
        chunks = [
            make_chunk("c1", "考虑为淋巴瘤。患者发热、盗汗"),
            make_chunk("c2", "另见发热"),
        ]
        result = extract_local(chunks, template, "DOC-T")
        # Chunk c2 has the symptom but no disease: no relation is invented.
        self.assertTrue(all(item["chunk_id"] != "c2" for item in result.suggested_relations))
        fever_relations = [
            item for item in result.suggested_relations
            if item["relation_code"] == "DISEASE_HAS_SYMPTOM" and item["target_code"] == "FEVER"
        ]
        self.assertEqual(len(fever_relations), 1)


class GraphImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kg_dir = Path(self.tmp.name)
        self._original_kg_dir = kg_routes.KG_DIR
        kg_routes.KG_DIR = self.kg_dir
        # import_graph reads the concept vocabulary relative to KG_DIR.
        vocab_dir = self.kg_dir.parent / "vocab"
        vocab_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(project_root / "data" / "vocab" / "concepts.json", vocab_dir / "concepts.json")
        self.template = make_template(
            entity_types=[
                TemplateEntityType(entity_type="disease", label="疾病"),
                TemplateEntityType(
                    entity_type="symptom",
                    label="症状",
                    entries=[TemplateEntityEntry(code="FEVER", name="发热", aliases=["发热"])],
                ),
                TemplateEntityType(entity_type="gene", label="基因"),
                TemplateEntityType(entity_type="drug", label="药物"),
            ],
            relations=[
                TemplateRelation(
                    relation_code="DISEASE_HAS_SYMPTOM",
                    name="疾病有症状",
                    source_entity_type="disease",
                    target_entity_type="symptom",
                    concept_code="PRESENT",
                ),
                TemplateRelation(
                    relation_code="GENE_TARGETS_DRUG",
                    name="基因靶向药物",
                    source_entity_type="gene",
                    target_entity_type="drug",
                ),
            ],
        )
        self._original_resolver = kg_routes._resolve_import_template
        kg_routes._resolve_import_template = lambda template_id: self.template

    def tearDown(self):
        kg_routes.KG_DIR = self._original_kg_dir
        kg_routes._resolve_import_template = self._original_resolver
        self.tmp.cleanup()

    def _import(self, entities):
        request = kg_routes.GraphImportRequest(
            doc_id="DOC-T1",
            doc_title="测试指南",
            target_disease_code="C83.3",
            template_id="TEST_V1",
            graph_filename="test_graph.json",
            entities=entities,
            chunks=[make_chunk("c1", "考虑为弥漫大B细胞淋巴瘤，患者发热，BRAF 靶向维莫非尼")],
        )
        return asyncio.run(kg_routes.import_graph(request))

    def test_custom_entity_types_import_with_relations(self):
        result = self._import([
            {"entity_type": "disease", "code": "C83.3", "name": "弥漫大B细胞淋巴瘤", "chunk_id": "c1"},
            {"entity_type": "symptom", "code": "FEVER", "name": "发热", "chunk_id": "c1"},
            {"entity_type": "gene", "code": "BRAF", "name": "BRAF", "chunk_id": "c1"},
            {"entity_type": "drug", "code": "VEMURAFENIB", "name": "维莫非尼", "chunk_id": "c1"},
        ])
        self.assertIn("ENTITY:gene:BRAF", self._node_ids())
        self.assertIn("ENTITY:drug:VEMURAFENIB", self._node_ids())
        self.assertTrue(any(
            rel["relation_code"] == "GENE_TARGETS_DRUG" for rel in self._relations()
        ))
        self.assertEqual(result["warnings"], [])

    def test_out_of_template_type_open_imports_as_draft(self):
        """模板外类型不再静默丢弃：开放导入为草稿节点，等待审核确认。"""
        result = self._import([
            {"entity_type": "disease", "code": "C83.3", "name": "弥漫大B细胞淋巴瘤", "chunk_id": "c1"},
            {"entity_type": "smoke", "code": "SMOKING", "name": "吸烟", "chunk_id": "c1"},
        ])
        node = next(
            (item for item in self._node_ids() if item == "ENTITY:smoke:SMOKING"),
            None,
        )
        self.assertIsNotNone(node, "模板外类型实体应开放导入图谱")
        graph = self._graph()
        smoke = next(
            (item for item in graph.get("nodes", []) if item.get("id") == "ENTITY:smoke:SMOKING"),
            None,
        )
        self.assertIsNotNone(smoke)
        self.assertEqual(smoke.get("review_status"), "draft")
        self.assertTrue(smoke.get("attributes", {}).get("open_import"))
        self.assertTrue(any("开放导入" in warning and "smoke" in warning for warning in result["warnings"]))

    def test_repeated_occurrence_accumulates_source_refs(self):
        entities = [
            {"entity_type": "disease", "code": "C83.3", "name": "弥漫大B细胞淋巴瘤", "chunk_id": "c1"},
            {"entity_type": "symptom", "code": "FEVER", "name": "发热", "chunk_id": "c1"},
        ]
        self._import(entities)
        result = self._import([
            *entities,
            {"entity_type": "symptom", "code": "FEVER", "name": "发热", "chunk_id": "c2"},
        ])
        self.assertEqual(result["evidences_added"], 0)  # dedup, not duplicated evidence
        graph = self._graph()
        fever_evidence = next(
            item for item in graph["evidences"] if item.get("symptom_code") == "FEVER"
        )
        self.assertEqual(fever_evidence["source_refs"], ["c1", "c2"])
        chunk_ids = {source["chunk_id"] for source in graph["sources"]}
        self.assertEqual(chunk_ids, {"c1", "c2"})

    def _graph(self):
        import json
        return json.loads((self.kg_dir / "test_graph.json").read_text(encoding="utf-8"))

    def _node_ids(self):
        nodes = self._graph()["nodes"]
        if isinstance(nodes, dict):
            return set(nodes.keys())
        return {item["id"] for item in nodes}

    def _relations(self):
        relations = self._graph()["relations"]
        if isinstance(relations, dict):
            return list(relations.values())
        return relations


if __name__ == "__main__":
    unittest.main(verbosity=2)
