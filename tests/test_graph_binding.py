"""Graph binding tests: a guideline must never fall into STEMI by default."""
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

from src.api.routes import kg as kg_routes
from src.extraction.graph_binding import resolve_template_graph_filename
from src.extraction.repository import ExtractionTemplateRepository


class GraphBindingTest(unittest.TestCase):
    def test_generic_template_binds_to_generic_graph(self):
        template = ExtractionTemplateRepository().get("GENERIC_GUIDELINE_V1")
        self.assertEqual(
            resolve_template_graph_filename(template),
            "KG-GENERIC_GUIDELINE_V1.json",
        )

    def test_import_without_graph_filename_does_not_fall_back_to_stemi(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        kg_dir = root / "kg"
        kg_dir.mkdir()
        vocab_dir = root / "vocab"
        vocab_dir.mkdir()
        shutil.copy(
            project_root / "data" / "vocab" / "concepts.json",
            vocab_dir / "concepts.json",
        )

        original_kg_dir = kg_routes.KG_DIR
        original_resolver = kg_routes._resolve_import_template
        kg_routes.KG_DIR = kg_dir
        template = ExtractionTemplateRepository().get("GENERIC_GUIDELINE_V1")
        kg_routes._resolve_import_template = lambda template_id: template
        self.addCleanup(setattr, kg_routes, "KG_DIR", original_kg_dir)
        self.addCleanup(setattr, kg_routes, "_resolve_import_template", original_resolver)

        request = kg_routes.GraphImportRequest(
            doc_id="DOC-BIND",
            doc_title="心肌炎指南",
            domain="western",
            target_disease_code="I40.9",
            entities=[
                {"entity_type": "disease", "code": "I40.9", "name": "急性心肌炎", "chunk_id": "c1"},
                {"entity_type": "symptom", "code": "SYM_CHEST_PAIN", "name": "胸痛", "chunk_id": "c1"},
            ],
            chunks=[{
                "chunk_id": "c1", "section": "诊断", "page_start": 1, "page_end": 1,
                "line_start": 1, "line_end": 2, "text": "急性心肌炎可出现胸痛",
            }],
            template_id="GENERIC_GUIDELINE_V1",
            graph_filename="",
        )
        result = asyncio.run(kg_routes.import_graph(request))
        self.assertEqual(result["graph_filename"], "KG-GENERIC_GUIDELINE_V1.json")
        self.assertTrue((kg_dir / "KG-GENERIC_GUIDELINE_V1.json").exists())
        self.assertFalse((kg_dir / "stemi_graph.json").exists())


if __name__ == "__main__":
    unittest.main()
