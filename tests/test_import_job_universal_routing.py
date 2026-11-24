"""中西医导入彻底分开的作业路由：西医走陈述流水线，中医走中医模板。

架构（2026-11-23 拆分）：
- 领域由建任务时的显式选择决定（模板领域优先，其次 domain 参数）；
- 西医指南（任何西医模板或无模板）→ 两阶段临床陈述流水线 run_auto_pipeline，
  入流水线前有 TCM 内容守卫，防中医指南误送西医链路；
- 中医指南 → TCM 专用模板作业体（抽取 → 关系门禁 → 知识包生成入库）。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes import import_jobs


def _patch_template(template_id, domain):
    """把 extraction_templates.get 打桩为返回指定领域的合成模板。"""
    from src.extraction.models import ExtractionTemplate
    real_get = import_jobs.extraction_templates.get

    def get_template(tid, *args, **kwargs):
        if tid == template_id:
            return ExtractionTemplate(template_id=template_id, name="T", domain=domain)
        return real_get(tid, *args, **kwargs)

    return mock.patch.object(import_jobs.extraction_templates, "get", side_effect=get_template)


class WesternImportRoutingTest(unittest.TestCase):
    def test_western_template_runs_statement_pipeline(self):
        async def fake_pipeline(chunks, doc_id, doc_title, config, **kwargs):
            return {
                "doc_id": doc_id, "doc_title": doc_title,
                "graph_filename": kwargs.get("graph_filename") or "KG-W.json",
                "entities": [{"entity_type": "disease", "code": "D1"}],
                "relations": [{"relation_code": "DISEASE_HAS_SYMPTOM"}],
                "rules": [], "warnings": [], "metadata": {},
            }

        with _patch_template("GENERIC_GUIDELINE_V1", "western"), \
             mock.patch.object(import_jobs, "run_auto_pipeline", side_effect=fake_pipeline) as pipeline, \
             mock.patch.object(import_jobs, "_run_template_job") as template_job, \
             mock.patch("src.api.routes.pdf.parse_pdf",
                        return_value={"chunks": [{"chunk_id": "C1", "text": "心肌病诊疗", "page_start": 1, "page_end": 1}]}), \
             mock.patch("src.api.routes.pdf._persist_doc") as persist:
            result = asyncio.run(import_jobs._run_auto_job(
                controller=None, file_path=Path("/tmp/cmp.pdf"),
                doc_id="DOC-T1", doc_title="心肌病诊疗指南",
                graph_filename="", auto_activate=False,
                domain="western", template_id="GENERIC_GUIDELINE_V1",
            ))

        template_job.assert_not_called()
        pipeline.assert_called_once()
        self.assertTrue(pipeline.call_args.kwargs.get("require_import_quality"))
        self.assertTrue(pipeline.call_args.kwargs.get("graph_filename"))
        self.assertEqual(result["domain"], "western")
        self.assertEqual(result["domain_detection"]["source"], "panel_selection")
        persist.assert_called()
        saved = persist.call_args_list[-1].args[0]
        self.assertEqual(saved["entities"], result["entities"])
        self.assertEqual(saved["extraction"]["template_id"], "GENERIC_GUIDELINE_V1")
        from src.api.routes.pdf import _doc_store
        _doc_store.pop("DOC-T1", None)

    def test_tcm_document_rejected_only_in_auto_fallback(self):
        # 仅 auto 兜底（未显式选择领域/模板）时守卫拦截，避免中医内容白耗 LLM
        with mock.patch.object(import_jobs, "run_auto_pipeline") as pipeline, \
             mock.patch("src.api.routes.pdf.parse_pdf", return_value={"chunks": [
                 {"text": "中医辨证分型：气虚血瘀证、气阴两虚证。舌质淡，脉细，治法益气活血，方剂随证加减。"}]}):
            with self.assertRaisesRegex(ValueError, "中医指南导入"):
                asyncio.run(import_jobs._run_auto_job(None, file_path=Path("/tmp/tcm.pdf"),
                    doc_id="DOC-GUARD", doc_title="慢性心力衰竭中医诊疗指南（2022年）",
                    graph_filename="", auto_activate=False,
                    domain="auto", template_id=""))
        pipeline.assert_not_called()

    def test_explicit_western_selection_skips_tcm_guard(self):
        # 显式西医链路（西医面板选模板上传）不做中医内容识别，按用户选择执行
        async def fake_pipeline(chunks, doc_id, doc_title, config, **kwargs):
            return {
                "doc_id": doc_id, "doc_title": doc_title,
                "graph_filename": kwargs.get("graph_filename") or "KG-W.json",
                "entities": [], "relations": [], "rules": [], "warnings": [],
                "metadata": {},
            }

        with _patch_template("GENERIC_GUIDELINE_V1", "western"), \
             mock.patch.object(import_jobs, "run_auto_pipeline", side_effect=fake_pipeline) as pipeline, \
             mock.patch("src.api.routes.pdf.parse_pdf", return_value={"chunks": [
                 {"text": "中医辨证分型：气虚血瘀证。舌质淡，脉细，方剂随证加减。"}]}), \
             mock.patch("src.api.routes.pdf._persist_doc"):
            result = asyncio.run(import_jobs._run_auto_job(None, file_path=Path("/tmp/tcm.pdf"),
                doc_id="DOC-WGUARD", doc_title="慢性心力衰竭中医诊疗指南（2022年）",
                graph_filename="", auto_activate=False,
                domain="western", template_id="GENERIC_GUIDELINE_V1"))
        pipeline.assert_called_once()
        self.assertEqual(result["domain"], "western")
        from src.api.routes.pdf import _doc_store
        _doc_store.pop("DOC-WGUARD", None)

    def test_domain_template_mismatch_raises(self):
        with _patch_template("TCM_GUIDELINE_V3", "tcm"), \
             mock.patch("src.api.routes.pdf.parse_pdf", return_value={"chunks": []}):
            with self.assertRaisesRegex(ValueError, "不一致"):
                asyncio.run(import_jobs._run_auto_job(None, file_path=Path("/tmp/x.pdf"),
                    doc_id="DOC-M", doc_title="t", graph_filename="",
                    auto_activate=False, domain="western", template_id="TCM_GUIDELINE_V3"))


class TcmImportRoutingTest(unittest.TestCase):
    def test_tcm_template_keeps_template_job(self):
        async def fake_generate(*args, **kwargs):
            return {"package": {"package_id": "PKG-TCM"}}

        async def fake_import(*args, **kwargs):
            return {"graph": {}}

        with _patch_template("TCM_GUIDELINE_V3", "tcm"), \
             mock.patch.object(import_jobs, "run_auto_pipeline") as pipeline, \
             mock.patch.object(import_jobs, "_run_template_job",
                               new=mock.AsyncMock(return_value={"doc_id": "DOC-T2", "relation_count": 3, "warnings": []})) as template_job, \
             mock.patch("src.api.routes.pdf.parse_pdf", return_value={"chunks": []}), \
             mock.patch("src.api.routes.pdf._persist_doc"), \
             mock.patch("src.api.routes.guide_packages.generate_package", side_effect=fake_generate), \
             mock.patch("src.api.routes.guide_packages.import_package", side_effect=fake_import):
            result = asyncio.run(import_jobs._run_auto_job(
                controller=None, file_path=Path("/tmp/t.pdf"),
                doc_id="DOC-T2", doc_title="心衰中医诊疗方案",
                graph_filename="", auto_activate=False,
                domain="tcm", template_id="TCM_GUIDELINE_V3",
            ))

        template_job.assert_called_once()
        pipeline.assert_not_called()
        # 跟随模板：中医与西医一致解析到模板绑定图，不再按 doc_id 新建
        self.assertEqual(template_job.call_args.kwargs.get("graph_name"),
                         "KG-TCM_GUIDELINE_V3.json")
        self.assertEqual(result["domain"], "tcm")
        self.assertEqual(result["template_id"], "TCM_GUIDELINE_V3")
        self.assertEqual(result["package_id"], "PKG-TCM")

    def test_tcm_without_template_defaults_to_tcm_v3(self):
        async def fake_generate(*args, **kwargs):
            return {"package": {"package_id": "PKG-TCM2"}}

        async def fake_import(*args, **kwargs):
            return {"graph": {}}

        with mock.patch.object(import_jobs, "run_auto_pipeline") as pipeline, \
             mock.patch.object(import_jobs, "_run_template_job",
                               new=mock.AsyncMock(return_value={"doc_id": "DOC-T3", "relation_count": 2, "warnings": []})) as template_job, \
             mock.patch("src.api.routes.pdf.parse_pdf", return_value={"chunks": []}), \
             mock.patch("src.api.routes.pdf._persist_doc"), \
             mock.patch("src.api.routes.guide_packages.generate_package", side_effect=fake_generate), \
             mock.patch("src.api.routes.guide_packages.import_package", side_effect=fake_import):
            result = asyncio.run(import_jobs._run_auto_job(
                controller=None, file_path=Path("/tmp/t.pdf"),
                doc_id="DOC-T3", doc_title="中医方案", graph_filename="",
                auto_activate=False, domain="tcm", template_id="",
            ))

        template_job.assert_called_once()
        pipeline.assert_not_called()
        self.assertEqual(template_job.call_args.kwargs.get("template_id"), "TCM_GUIDELINE_V3")
        self.assertEqual(template_job.call_args.kwargs.get("graph_name"),
                         "KG-TCM_GUIDELINE_V3.json")
        self.assertEqual(result["domain"], "tcm")

    def test_tcm_zero_relations_stops_import_with_review_hint(self):
        with _patch_template("TCM_GUIDELINE_V3", "tcm"), \
             mock.patch.object(import_jobs, "_run_template_job",
                               new=mock.AsyncMock(return_value={"doc_id": "DOC-T4", "relation_count": 0, "warnings": []})), \
             mock.patch("src.api.routes.pdf.parse_pdf", return_value={"chunks": []}), \
             mock.patch("src.api.routes.pdf._persist_doc"):
            with self.assertRaisesRegex(ValueError, "审核区"):
                asyncio.run(import_jobs._run_auto_job(
                    None, file_path=Path("/tmp/t.pdf"), doc_id="DOC-T4",
                    doc_title="中医方案", graph_filename="", auto_activate=False,
                    domain="tcm", template_id="TCM_GUIDELINE_V3"))


if __name__ == "__main__":
    unittest.main()
