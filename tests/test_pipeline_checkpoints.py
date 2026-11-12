"""Pipeline checkpoint injection: progress, pause-between-groups, stop-discard."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.import_job_manager import ImportController, ImportJob, ImportStopped
from src.extraction.auto_pipeline import run_auto_pipeline
from src.extraction.service import extract_llm


def _controller() -> ImportController:
    job = ImportJob(job_id="JOB-T", mode="template-llm", doc_title="t")
    job.control.pause_event.set()
    return ImportController(job)


def _chunks(n: int) -> List[Dict[str, Any]]:
    return [{"chunk_id": f"C{i}", "section": "测试", "text": "内容" * 50, "page_start": 1,
             "line_start": 1, "line_end": 2, "page_end": 1} for i in range(n)]


class ExtractLlmCheckpointTest(unittest.IsolatedAsyncioTestCase):
    async def test_progress_reported_per_group(self):
        controller = _controller()
        calls: List[Dict[str, int]] = []

        async def fake_llm(settings, prompt):
            await asyncio.sleep(0)
            return {"entities": [], "relations": [], "rules": []}

        original = sys.modules["src.extraction.service"].chat_json
        sys.modules["src.extraction.service"].chat_json = fake_llm
        self.addCleanup(setattr, sys.modules["src.extraction.service"], "chat_json", original)

        real_checkpoint = controller.checkpoint
        async def spy_checkpoint(stage="", done=0, total=0):
            calls.append({"done": done, "total": total})
            await real_checkpoint(stage=stage, done=done, total=total)
        controller.checkpoint = spy_checkpoint  # type: ignore[assignment]

        from src.extraction.repository import ExtractionTemplateRepository
        template = ExtractionTemplateRepository().get("GENERIC_GUIDELINE_V1")
        result = await extract_llm(
            _chunks(2), template, "DOC-T", "t",
            {"llm": {"api_key": "TEST_KEY", "base_url": "http://localhost",
                     "max_chunks": 80, "max_chunk_chars": 100}},
            controller=controller,
        )
        self.assertEqual(result.entities, [])
        self.assertTrue(calls)
        self.assertEqual(calls[-1]["total"], len(calls))  # done 递增到 total

    async def test_stop_between_groups_raises(self):
        controller = _controller()

        async def fake_llm(settings, prompt):
            controller.job.control.stop_requested = True  # 第一组完成后请求停止
            return {"entities": [], "relations": [], "rules": []}

        original = sys.modules["src.extraction.service"].chat_json
        sys.modules["src.extraction.service"].chat_json = fake_llm
        self.addCleanup(setattr, sys.modules["src.extraction.service"], "chat_json", original)

        from src.extraction.repository import ExtractionTemplateRepository
        template = ExtractionTemplateRepository().get("GENERIC_GUIDELINE_V1")
        with self.assertRaises(ImportStopped):
            await extract_llm(
                _chunks(2), template, "DOC-T", "t",
                {"llm": {"api_key": "TEST_KEY", "base_url": "http://localhost",
                         "max_chunk_chars": 100}},
                controller=controller,
            )

    async def test_controller_none_keeps_working(self):
        from src.extraction.repository import ExtractionTemplateRepository
        template = ExtractionTemplateRepository().get("GENERIC_GUIDELINE_V1")

        async def fake_llm(settings, prompt):
            return {"entities": [], "relations": [], "rules": []}
        original = sys.modules["src.extraction.service"].chat_json
        sys.modules["src.extraction.service"].chat_json = fake_llm
        self.addCleanup(setattr, sys.modules["src.extraction.service"], "chat_json", original)
        result = await extract_llm(
            _chunks(1), template, "DOC-T", "t",
            {"llm": {"api_key": "TEST_KEY", "base_url": "http://localhost"}}, controller=None,
        )
        self.assertEqual(result.entities, [])


async def _fake_auto_llm(_settings, _prompt):
    return {
        "entities": [
            {"entity_type": "disease", "name": "肺腺癌", "code": "C34.9", "aliases": ["肺癌"],
             "chunk_id": "C0", "page": 1, "line_start": 1, "line_end": 2, "quote": "肺腺癌", "confidence": 0.95},
            {"entity_type": "symptom", "name": "咳嗽", "code": None,
             "chunk_id": "C0", "page": 1, "line_start": 2, "line_end": 3, "quote": "咳嗽", "confidence": 0.9},
        ],
        "relations": [
            {"source_name": "肺腺癌", "source_type": "disease", "target_name": "咳嗽",
             "target_type": "symptom", "relation_code": "DISEASE_HAS_SYMPTOM",
             "chunk_id": "C0", "page": 1, "line_start": 1, "line_end": 3, "quote": "肺腺癌...咳嗽"},
        ],
        "rules": [
            {
                "rule_id": "CASE-DX",
                "rule_type": "diagnosis",
                "disease_name": "肺腺癌",
                "conditions": {"type": "group", "operator": "AND", "children": [
                    {"type": "symptom", "symptom_code": "咳嗽"},
                ]},
                "conclusion": {"diagnosis": "C34.9", "confidence": "medium", "message": "肺癌诊断"},
                "source": {"chunk_id": "C0", "page_start": 1, "line_start": 1, "line_end": 2, "quote": "肺腺癌"},
                "confidence": 0.9,
            },
        ],
        "treatment_plans": [],
    }


class AutoPipelineCheckpointTest(unittest.IsolatedAsyncioTestCase):
    def _prepare_root(self) -> Path:
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
        return root

    async def test_progress_reported_and_finalizing_set(self):
        from src.api.routes import kg as kg_routes
        root = self._prepare_root()
        original_kg_dir = kg_routes.KG_DIR
        kg_routes.KG_DIR = root / "kg"
        self.addCleanup(setattr, kg_routes, "KG_DIR", original_kg_dir)

        controller = _controller()
        calls: List[Dict[str, Any]] = []
        real_checkpoint = controller.checkpoint

        async def spy_checkpoint(stage="", done=0, total=0):
            calls.append({"stage": stage, "done": done, "total": total})
            await real_checkpoint(stage=stage, done=done, total=total)
        controller.checkpoint = spy_checkpoint  # type: ignore[assignment]

        result = await run_auto_pipeline(
            _chunks(2), "DOC-AP", "自动管线指南",
            {"llm": {"max_chunks": 80, "max_chunk_chars": 10}},
            llm_call=_fake_auto_llm,
            data_root=root,
            auto_import_disease=False,
            controller=controller,
        )

        self.assertTrue(result["entities"])
        group_calls = [c for c in calls if "自动抽取" in c["stage"]]
        self.assertEqual(len(group_calls), 2)
        self.assertEqual([c["done"] for c in group_calls], [0, 1])
        self.assertEqual(group_calls[-1]["total"], 2)
        stages = [c["stage"] for c in calls]
        self.assertIn("术语映射与图谱整理", stages)
        self.assertIn("写入图谱与规则（不可中断）", stages)
        self.assertTrue(controller.finalizing)

    async def test_stop_between_groups_raises(self):
        controller = _controller()

        async def fake_llm(settings, prompt):
            controller.job.control.stop_requested = True  # 第一组完成后请求停止
            return {"entities": [], "relations": [], "rules": [], "treatment_plans": []}

        root = self._prepare_root()
        with self.assertRaises(ImportStopped):
            await run_auto_pipeline(
                _chunks(2), "DOC-STOP", "自动停止指南",
                {"llm": {"max_chunks": 80, "max_chunk_chars": 10}},
                llm_call=fake_llm,
                data_root=root,
                auto_import_disease=False,
                controller=controller,
            )
        self.assertFalse(controller.finalizing)


if __name__ == "__main__":
    unittest.main()
