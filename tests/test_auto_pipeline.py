"""End-to-end test for the universal auto pipeline with a mocked LLM."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes import kg as kg_routes
from src.api.routes.guide_packages import _package_summary
from src.extraction.auto_pipeline import _chunk_groups, run_auto_pipeline
from src.llm import LLMError


def make_chunk() -> dict:
    return {
        "chunk_id": "c1",
        "section": "肺癌诊断与治疗",
        "page_start": 1,
        "page_end": 1,
        "line_start": 1,
        "line_end": 20,
        "text": "肺腺癌患者可表现为咳嗽，胸部CT见肺部肿块，病理确诊后可选择培美曲塞化疗。",
        "page_numbers": [1],
        "line_numbers": list(range(1, 21)),
    }


async def fake_llm(_settings, _messages):
    if "阶段一：" in _messages[0]["content"]:
        return {"statements": [{"statement_id": "S1", "disease_name": "肺腺癌", "scene": "diagnosis_confirmation",
            "population": "肺腺癌待评估患者", "subtype": "", "phase": "诊断阶段", "exceptions": [],
            "missing_information": [], "source": {"chunk_id": "c1", "page_start": 1, "line_start": 1,
            "line_end": 4, "quote": "肺腺癌患者可表现为咳嗽"}}]}
    input_payload = json.loads(_messages[1]["content"])
    statement_id = input_payload["clinical_statements"][0]["statement_id"]
    return {
        "entities": [
            {"entity_type": "disease", "name": "肺腺癌", "code": "C34.9", "aliases": ["肺癌"], "chunk_id": "c1", "page": 1, "line_start": 1, "line_end": 2, "quote": "肺腺癌", "confidence": 0.95},
            {"entity_type": "symptom", "name": "咳嗽", "code": None, "chunk_id": "c1", "page": 1, "line_start": 2, "line_end": 3, "quote": "咳嗽", "confidence": 0.9},
            {"entity_type": "exam", "name": "胸部CT", "code": None, "chunk_id": "c1", "page": 1, "line_start": 3, "line_end": 4, "quote": "胸部CT", "confidence": 0.9},
            {"entity_type": "medication", "name": "培美曲塞", "code": None, "chunk_id": "c1", "page": 1, "line_start": 5, "line_end": 6, "quote": "培美曲塞", "confidence": 0.9},
            {"entity_type": "treatment_plan", "name": "一线化疗方案", "code": None, "chunk_id": "c1", "page": 1, "line_start": 5, "line_end": 6, "quote": "培美曲塞化疗", "confidence": 0.85},
        ],
        "relations": [
            {"source_name": "肺腺癌", "source_type": "disease", "target_name": "咳嗽", "target_type": "symptom", "relation_code": "DISEASE_HAS_SYMPTOM", "chunk_id": "c1", "page": 1, "line_start": 1, "line_end": 3, "quote": "肺腺癌...咳嗽"},
            {"source_name": "肺腺癌", "source_type": "disease", "target_name": "胸部CT", "target_type": "exam", "relation_code": "DISEASE_HAS_EXAM", "chunk_id": "c1", "page": 1, "line_start": 1, "line_end": 4, "quote": "胸部CT"},
        ],
        "rules": [
            {
                "rule_id": "CASE-DX",
                "clinical_statement_id": statement_id,
                "rule_type": "diagnosis",
                "disease_name": "肺腺癌",
                "conditions": {"type": "group", "operator": "AND", "children": [
                    {"type": "symptom", "symptom_code": "咳嗽"},
                    {"type": "exam_finding", "exam_code": "胸部CT", "finding_code": "肺部肿块"},
                ]},
                "conclusion": {"diagnosis": "C34.9", "confidence": "medium", "message": "肺癌诊断"},
                "source": {"chunk_id": "c1", "page_start": 1, "line_start": 1, "line_end": 4, "quote": "肺腺癌...胸部CT"},
                "confidence": 0.9,
            },
            {
                "rule_id": "CASE-TX",
                "clinical_statement_id": statement_id,
                "rule_type": "treatment",
                "disease_name": "肺腺癌",
                "conditions": {"type": "group", "operator": "AND", "children": []},
                "conclusion": {
                    "diagnosis": "C34.9", "confidence": "medium", "message": "化疗",
                    "treatment_plan_id": "TP-LUNG-1", "treatment_name": "一线化疗方案",
                    "treatment_items": [{"code": "PEMETREXED", "name": "培美曲塞", "entity_type": "medication"}],
                },
                "source": {"chunk_id": "c1", "page_start": 1, "line_start": 5, "line_end": 6, "quote": "培美曲塞化疗"},
                "confidence": 0.9,
            },
        ],
        "treatment_plans": [],
    }


class AutoPipelineTest(unittest.TestCase):
    def test_disease_mention_survives_empty_statements_and_uses_parsed_line(self):
        chunk = {
            **make_chunk(), "line_start": 50, "line_end": 51,
            "page_numbers": [1, 1], "line_numbers": [50, 51],
            "text": "中医病名\n慢性心衰归属于中医学“心衰”的范畴，年老",
        }
        model = AsyncMock(return_value={"statements": [], "disease_mentions": [
            {"name": "慢性心衰", "source": {
                "chunk_id": "c1", "page_start": 9, "line_start": 90,
                "quote": "慢性心衰归属于中医学“心衰”的范畴",
            }},
            {"name": "标题病名", "source": {"chunk_id": "c1", "quote": "标题病名"}},
        ]})
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            kg_routes, "import_graph", new_callable=AsyncMock, return_value={},
        ), patch("src.extraction.auto_pipeline.UniversalTermResolver") as resolver:
            resolver.return_value.resolve.return_value = {
                "entity_type": "disease", "name": "慢性心衰", "code": "LOCAL-DIS-TEST",
            }
            resolver.return_value.resolved_terms = []
            result = asyncio.run(run_auto_pipeline(
                [chunk], "DOC-HEART", "标题病名", {}, data_root=Path(tmp), llm_call=model,
            ))
        self.assertEqual(model.await_count, 1)
        resolver.return_value.resolve.assert_called_once()
        self.assertEqual(
            [(e["name"], e["page"], e["line_start"], e["line_end"]) for e in result["entities"]],
            [("慢性心衰", 1, 51, 51)],
        )

    def test_no_body_mention_reports_coverage_without_creating_graph(self):
        model = AsyncMock(return_value={"statements": [], "disease_mentions": [
            {"name": "标题病名", "source": {"chunk_id": "c1", "quote": "标题病名"}},
        ]})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(LLMError, "模型响应 1 个.*疾病候选 1 个"):
                asyncio.run(run_auto_pipeline(
                    [make_chunk()], "DOC-NO-DISEASE", "标题病名", {},
                    data_root=Path(tmp), llm_call=model,
                ))
            self.assertFalse((Path(tmp) / "kg").exists())

    def test_chunk_group_limit_batches_without_truncating(self):
        chunks = [
            {"chunk_id": f"C{i}", "text": "内容", "page_start": 1, "page_end": 1,
             "line_start": i + 1, "line_end": i + 1}
            for i in range(5)
        ]
        groups = _chunk_groups(chunks, max_chars=100000, max_chunks=2)
        self.assertEqual([len(group) for group in groups], [2, 2, 1])
        self.assertEqual(
            [item["chunk_id"] for group in groups for item in group],
            [item["chunk_id"] for item in chunks],
        )

    def test_lung_cancer_guideline_builds_graph_and_pending_bases(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "kg").mkdir()
        (root / "rules").mkdir()
        (root / "guide_packages").mkdir()
        (root / "vocab").mkdir()
        (root / "vocab" / "concepts.json").write_text(
            (project_root / "data" / "vocab" / "concepts.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        # Keep this test independent of the user's replaceable reference catalogue.
        from unittest.mock import patch
        from src.vocab.reference_vocabulary import ReferenceVocabularyRepository
        reference_dir = root / "reference"
        reference_dir.mkdir()
        (reference_dir / "icd10.json").write_text(json.dumps({"entries": [{
            "code": "C34.9", "name": "肺癌", "aliases": ["肺腺癌"],
            "standard_system": "ICD-10", "standard_code": "C34.9", "domain": "western",
        }]}), encoding="utf-8")
        reference_patch = patch("src.extraction.terminology_resolver.ReferenceVocabularyRepository",
            side_effect=lambda standard: ReferenceVocabularyRepository(standard, reference_dir))
        reference_patch.start()
        self.addCleanup(reference_patch.stop)

        original_kg_dir = kg_routes.KG_DIR
        kg_routes.KG_DIR = root / "kg"
        self.addCleanup(setattr, kg_routes, "KG_DIR", original_kg_dir)

        result = asyncio.run(run_auto_pipeline(
            [make_chunk()],
            "DOC-LUNG",
            "肺癌诊疗指南",
            {"llm": {"max_chunks": 10, "max_chunk_chars": 12000}},
            llm_call=fake_llm,
            data_root=root,
            auto_import_disease=False,
        ))

        codes = {(entity["entity_type"], entity["code"]) for entity in result["entities"]}
        self.assertIn(("disease", "C34.9"), codes)
        self.assertTrue(any(entity_type == "symptom" and code.startswith("LOCAL-SYM-") for entity_type, code in codes))
        self.assertTrue((root / "kg" / result["graph_filename"]).exists())
        self.assertEqual(len(result["rules"]), 2)
        self.assertTrue(result["metadata"]["coverage"]["complete"])
        self.assertEqual(result["metadata"]["coverage"]["processed"], 1)
        self.assertEqual(result["metadata"]["clinical_statement_count"], 1)
        self.assertTrue(all(rule.get("clinical_statement_id") for rule in result["rules"]))
        self.assertTrue(all(rule["status"] == "draft" for rule in result["rules"]))
        graph = json.loads((root / "kg" / result["graph_filename"]).read_text(encoding="utf-8"))
        self.assertIn("ENTITY:disease:C34.9", {node["id"] for node in graph["nodes"]})
        rules_path = root / "rules" / "managed_rules.json"
        self.assertFalse(rules_path.exists())
        self.assertEqual(len(graph["clinical_bases"]), 2)
        self.assertTrue(all(basis["status"] == "draft" for basis in graph["clinical_bases"]))
        package_path = root / "guide_packages" / f"{result['package_id']}.json"
        self.assertTrue(package_path.exists())
        package = json.loads(package_path.read_text(encoding="utf-8"))
        # Auto mode imports immediately, so the package must not look "to import".
        self.assertTrue(package["import_result"]["success"])
        self.assertIn(package["status"], {"ready_for_test", "imported"})
        self.assertTrue(_package_summary(package)["imported"])

    def test_disease_entity_without_name_in_quote_is_rejected(self):
        """A disease whose quote never mentions the name/alias must not enter
        the graph. Regression guard for the 线粒体疾病 chapter binding HCM
        (I42.2) to descriptions of 心肌肥厚."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "kg").mkdir()
        (root / "rules").mkdir()
        (root / "guide_packages").mkdir()
        (root / "vocab").mkdir()
        (root / "vocab" / "concepts.json").write_text(
            (project_root / "data" / "vocab" / "concepts.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        from unittest.mock import patch
        from src.vocab.reference_vocabulary import ReferenceVocabularyRepository
        reference_dir = root / "reference"
        reference_dir.mkdir()
        (reference_dir / "icd10.json").write_text(json.dumps({"entries": [
            {"code": "C34.9", "name": "肺癌", "aliases": ["肺腺癌"],
             "standard_system": "ICD-10", "standard_code": "C34.9", "domain": "western"},
            {"code": "I42.2", "name": "其他肥厚型心肌病", "aliases": ["肥厚型心肌病", "HCM"],
             "standard_system": "ICD-10", "standard_code": "I42.2", "domain": "western"},
        ]}), encoding="utf-8")
        reference_patch = patch("src.extraction.terminology_resolver.ReferenceVocabularyRepository",
            side_effect=lambda standard: ReferenceVocabularyRepository(standard, reference_dir))
        reference_patch.start()
        self.addCleanup(reference_patch.stop)

        original_kg_dir = kg_routes.KG_DIR
        kg_routes.KG_DIR = root / "kg"
        self.addCleanup(setattr, kg_routes, "KG_DIR", original_kg_dir)

        async def mito_llm(_settings, _messages):
            if "阶段一：" in _messages[0]["content"]:
                return {"statements": [{"statement_id": "S1", "disease_name": "肺腺癌",
                    "scene": "diagnosis_confirmation", "population": "", "subtype": "", "phase": "",
                    "exceptions": [], "missing_information": [],
                    "source": {"chunk_id": "c1", "page_start": 1, "line_start": 1,
                    "line_end": 4, "quote": "肺腺癌患者可表现为咳嗽"}}]}
            return {
                "entities": [
                    {"entity_type": "disease", "name": "肺腺癌", "code": "C34.9",
                     "chunk_id": "c1", "page": 1, "line_start": 1, "line_end": 2,
                     "quote": "肺腺癌患者可表现为咳嗽", "confidence": 0.95},
                    # Mislabelled candidate: quote describes 心肌肥厚 in the
                    # mitochondrial-disease chapter and never names the disease.
                    {"entity_type": "disease", "name": "其他肥厚型心肌病", "code": "I42.2",
                     "chunk_id": "c1", "page": 1, "line_start": 3, "line_end": 4,
                     "quote": "心脏病变见于40%的患者，其中心肌肥厚最常见", "confidence": 0.9},
                ],
                "relations": [
                    {"source_name": "其他肥厚型心肌病", "source_type": "disease",
                     "target_name": "肺腺癌", "target_type": "disease",
                     "relation_code": "RELATED_TO", "chunk_id": "c1", "quote": "心脏病变见于40%的患者"},
                ],
                "rules": [],
                "treatment_plans": [],
            }

        result = asyncio.run(run_auto_pipeline(
            [make_chunk()],
            "DOC-MITO",
            "肥厚型心肌病指南",
            {"llm": {"max_chunks": 10, "max_chunk_chars": 12000}},
            llm_call=mito_llm,
            data_root=root,
            auto_import_disease=False,
        ))

        codes = {(entity["entity_type"], entity["code"]) for entity in result["entities"]}
        self.assertIn(("disease", "C34.9"), codes)
        self.assertNotIn(("disease", "I42.2"), codes)
        self.assertFalse(any(relation.get("source_code") == "I42.2" for relation in result["relations"]))
        self.assertTrue(any("错绑" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()


@pytest.mark.parametrize("error", ["大模型请求超时（120 秒）", "Anthropic 请求失败 HTTP 429"])
def test_auto_timeout_retries_and_reuses_successful_stage(tmp_path, monkeypatch, error):
    from unittest.mock import Mock
    from src.extraction import auto_pipeline as pipeline
    resolver = Mock()
    resolver.resolve.return_value = {'entity_type': 'disease', 'name': '肺腺癌', 'code': 'LOCAL-DIS-TEST'}
    resolver.resolved_terms = []
    monkeypatch.setattr(pipeline, 'UniversalTermResolver', lambda **kwargs: resolver)
    monkeypatch.setattr(kg_routes, 'import_graph', AsyncMock(return_value={}))
    monkeypatch.setattr(pipeline.asyncio, 'sleep', AsyncMock())
    model = AsyncMock(side_effect=[LLMError(error), {
        'statements': [], 'disease_mentions': [{'name': '肺腺癌', 'source': {
            'chunk_id': 'c1', 'quote': '肺腺癌', 'page_start': 1, 'line_start': 1}}]}])
    async def run():
        args = ([make_chunk()], 'DOC-RESUME', '肺癌', {})
        first = await run_auto_pipeline(*args, data_root=tmp_path, llm_call=model)
        second = await run_auto_pipeline(*args, data_root=tmp_path, llm_call=model)
        assert first['metadata']['coverage']['complete']
        assert second['metadata']['coverage']['complete']
    asyncio.run(run())
    assert model.await_count == 2  # timeout + retry; subsequent run is cached


def test_auto_excludes_bibliography_from_model_requests(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from src.extraction import auto_pipeline as pipeline
    resolver = Mock()
    resolver.resolve.return_value = {'entity_type': 'disease', 'name': '肺腺癌', 'code': 'LOCAL-DIS-TEST'}
    resolver.resolved_terms = []
    monkeypatch.setattr(pipeline, 'UniversalTermResolver', lambda **kwargs: resolver)
    monkeypatch.setattr(kg_routes, 'import_graph', AsyncMock(return_value={}))
    model = AsyncMock(return_value={'statements': [], 'disease_mentions': [{'name': '肺腺癌',
        'source': {'chunk_id': 'c1', 'quote': '肺腺癌'}}]})
    refs = {**make_chunk(), 'chunk_id': 'refs', 'section': '参考文献', 'text': '[1] reference'}
    result = asyncio.run(run_auto_pipeline([make_chunk(), refs], 'DOC-REF', '肺癌', {},
        data_root=tmp_path, llm_call=model))
    coverage = result['metadata']['coverage']
    assert coverage['complete'] and coverage['excluded'] == 1 and coverage['processed'] == 1
    assert model.await_count == 1
    assert len(json.loads(model.call_args.args[1][1]['content'])['chunks']) == 1


def test_persistent_rate_limit_stops_queued_chunks(tmp_path, monkeypatch):
    from src.extraction import auto_pipeline as pipeline
    monkeypatch.setattr(pipeline.asyncio, 'sleep', AsyncMock())
    model = AsyncMock(side_effect=LLMError('Anthropic 请求失败 HTTP 429'))
    chunks = [{**make_chunk(), 'chunk_id': f'C{i}'} for i in range(3)]
    with pytest.raises(LLMError, match='模型服务持续限流'):
        asyncio.run(run_auto_pipeline(chunks, 'DOC-LIMIT', '肺癌', {}, data_root=tmp_path, llm_call=model))
    assert model.await_count == 4
    assert not (tmp_path / 'kg').exists()


def test_auto_enforces_total_stage_deadline(tmp_path):
    async def hanging_model(*args):
        await asyncio.Event().wait()
    with pytest.raises(LLMError, match='总耗时超时'):
        asyncio.run(run_auto_pipeline([make_chunk()], 'DOC-DEADLINE', '肺癌',
            {'llm': {'timeout_seconds': 0.01}}, data_root=tmp_path, llm_call=hanging_model))
    assert not (tmp_path / 'kg').exists()


def test_compiler_batches_dense_statements_without_dropping_any(tmp_path, monkeypatch):
    from src.extraction import auto_pipeline as pipeline
    from unittest.mock import Mock
    resolver = Mock()
    resolver.resolve.return_value = {'entity_type': 'disease', 'name': '肺腺癌', 'code': 'LOCAL-DIS-TEST'}
    resolver.resolved_terms = []
    monkeypatch.setattr(pipeline, 'UniversalTermResolver', lambda **kw: resolver)
    monkeypatch.setattr(kg_routes, 'import_graph', AsyncMock(return_value={}))
    batches = []
    async def model(settings, messages):
        payload = json.loads(messages[1]['content'])
        if 'clinical_statements' not in payload:
            return {'statements': [{'disease_name': '肺腺癌', 'scene': 'diagnosis_confirmation',
                'source': {'chunk_id': 'c1', 'quote': '肺腺癌'}} for _ in range(5)]}
        records = payload['clinical_statements']
        batches.append([r['statement_id'] for r in records])
        return {'entities': [{'entity_type': 'disease', 'name': '肺腺癌', 'chunk_id': 'c1', 'quote': '肺腺癌'}]}
    result = asyncio.run(run_auto_pipeline([make_chunk()], 'DOC-DENSE', '肺癌', {},
        data_root=tmp_path, llm_call=model))
    assert [len(b) for b in batches] == [2, 2, 1]
    assert len({i for b in batches for i in b}) == 5
    assert result['metadata']['clinical_statement_count'] == 5
    assert result['metadata']['coverage']['complete']
