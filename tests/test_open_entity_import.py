import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import src.api.routes.kg as kg_routes
import src.api.routes.pdf as pdf_module
from src.api.routes.kg import GraphImportRequest, import_graph
from src.api.routes.pdf import get_import_logs


def make_request():
    return GraphImportRequest(
        doc_id="DOC-OPENTEST",
        doc_title="开放实体导入测试",
        domain="western",
        target_disease_code="K1.1",
        template_id="GENERIC_GUIDELINE_V1",
        graph_filename="OPEN-TEST.json",
        entities=[
            {"entity_type": "disease", "name": "开放测试病", "code": "K1.1",
             "chunk_id": "c1", "page": 1, "line_start": 1, "line_end": 2,
             "quote": "q", "confidence": 0.9, "attributes": {}},
            # 模板外类型：开放导入为草稿，不再丢弃
            {"entity_type": "biomarker", "name": "某生物标志物", "code": "LOCAL-BIO-1",
             "chunk_id": "c1", "page": 1, "line_start": 3, "line_end": 4,
             "quote": "q", "confidence": 0.8, "attributes": {}},
            # 缺编码：仍然跳过
            {"entity_type": "symptom", "name": "无编码症状", "code": "",
             "chunk_id": "c1", "page": 1, "line_start": 5, "line_end": 6,
             "quote": "q", "confidence": 0.8, "attributes": {}},
        ],
        chunks=[{"chunk_id": "c1", "section": "s", "page_start": 1, "page_end": 1,
                 "line_start": 1, "line_end": 6, "text": "t"}],
        suggested_relations=[],
        candidate_rules=[],
    )


class OpenEntityImportTest(unittest.TestCase):
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

    def test_template_out_type_imported_as_draft(self):
        result = asyncio.run(import_graph(make_request()))
        graph = json.loads((kg_routes.KG_DIR / "OPEN-TEST.json").read_text(encoding="utf-8"))
        nodes = {node["id"]: node for node in graph["nodes"]}
        bio = nodes.get("ENTITY:biomarker:LOCAL-BIO-1")
        self.assertIsNotNone(bio, "模板外类型实体应开放导入图谱")
        self.assertEqual(bio["review_status"], "draft")
        self.assertTrue(bio["attributes"].get("open_import"))
        self.assertTrue(any("开放导入" in warning for warning in result["warnings"]))

    def test_missing_code_entity_still_skipped(self):
        asyncio.run(import_graph(make_request()))
        graph = json.loads((kg_routes.KG_DIR / "OPEN-TEST.json").read_text(encoding="utf-8"))
        names = {node.get("name") for node in graph["nodes"]}
        self.assertNotIn("无编码症状", names)


class ImportLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_path = Path(self.tmp.name) / "imports" / "import_log.jsonl"
        self.original = pdf_module.IMPORT_LOG_PATH
        pdf_module.IMPORT_LOG_PATH = self.log_path
        self.addCleanup(setattr, pdf_module, "IMPORT_LOG_PATH", self.original)

    def test_append_and_read_latest_first(self):
        pdf_module._append_import_log({
            "doc_id": "DOC-1", "doc_title": "指南A", "status": "success",
            "mode": "llm", "entity_count": 10,
        })
        pdf_module._append_import_log({
            "doc_id": "DOC-2", "doc_title": "指南B", "status": "failed",
            "mode": "llm", "stage": "parse", "error": "boom",
        })
        rows = asyncio.run(get_import_logs(limit=10))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["doc_id"], "DOC-2")  # 最新在前
        self.assertIn("ts", rows[0])
        self.assertEqual(rows[1]["entity_count"], 10)

    def test_empty_log_returns_empty(self):
        self.assertEqual(asyncio.run(get_import_logs()), [])
