"""End-to-end test for the universal auto pipeline with a mocked LLM."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes import kg as kg_routes
from src.api.routes.guide_packages import _package_summary
from src.extraction.auto_pipeline import run_auto_pipeline


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
    def test_lung_cancer_guideline_builds_graph_and_rules(self):
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
        self.assertTrue(all(rule["status"] == "active" for rule in result["rules"]))
        graph = json.loads((root / "kg" / result["graph_filename"]).read_text(encoding="utf-8"))
        self.assertIn("ENTITY:disease:C34.9", {node["id"] for node in graph["nodes"]})
        saved_rules = json.loads((root / "rules" / "managed_rules.json").read_text(encoding="utf-8"))
        self.assertEqual(len(saved_rules), 2)
        package_path = root / "guide_packages" / f"{result['package_id']}.json"
        self.assertTrue(package_path.exists())
        package = json.loads(package_path.read_text(encoding="utf-8"))
        # Auto mode imports immediately, so the package must not look "to import".
        self.assertTrue(package["import_result"]["success"])
        self.assertIn(package["status"], {"ready_for_test", "imported"})
        self.assertTrue(_package_summary(package)["imported"])


if __name__ == "__main__":
    unittest.main()
