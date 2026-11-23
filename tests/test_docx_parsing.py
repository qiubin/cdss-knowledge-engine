"""Word (.docx) guideline parsing tests — mirrors the PDF chunking contract."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes import pdf as pdf_routes


def build_docx(path, paragraphs=(), table_rows=None, page_break_after=None, extra_xml=None):
    """Write a minimal .docx with python-docx; returns the file path."""
    from docx import Document
    from docx.enum.text import WD_BREAK

    doc = Document()
    for index, text in enumerate(paragraphs):
        para = doc.add_paragraph(text)
        if page_break_after is not None and index in page_break_after:
            para.runs[0].add_break(WD_BREAK.PAGE) if para.runs else para.add_run().add_break(WD_BREAK.PAGE)
    if table_rows:
        table = doc.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        for r, row in enumerate(table_rows):
            for c, cell in enumerate(row):
                table.cell(r, c).text = cell
    if extra_xml:
        extra_xml(doc)
    doc.save(str(path))
    return path


class ParseDocxTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_paragraphs_map_to_lines_with_empty_bbox(self):
        path = build_docx(self.dir / "g.docx", paragraphs=["1  定义和分型", "慢性心力衰竭的定义。", "", "推荐使用ACEI。"])
        parsed = pdf_routes.parse_docx(path, "DOC-DX1")
        self.assertEqual([l["text"] for l in parsed["lines"]],
                         ["1  定义和分型", "慢性心力衰竭的定义。", "推荐使用ACEI。"])
        self.assertTrue(all(l["bbox"] == [] for l in parsed["lines"]))
        self.assertFalse(parsed["ocr_used"])
        self.assertTrue(parsed["reading_quality"]["pages_approximate"])

    def test_soft_line_breaks_inside_paragraph_split_into_lines(self):
        """段内软换行（w:br）渲染为 \n：必须拆成多行，否则 chunk 文本行数与
        page_numbers/line_numbers 错位，bounded_chunks 会 list index out of range。"""
        from docx import Document

        doc = Document()
        para = doc.add_paragraph()
        para.add_run("第一段文字")
        para.add_run("\n第二行文字")  # python-docx 将软换行存为 w:br，text 读回 \n
        path = self.dir / "soft.docx"
        doc.save(str(path))
        parsed = pdf_routes.parse_docx(path, "DOC-DXSOFT")
        self.assertEqual([l["text"] for l in parsed["lines"]], ["第一段文字", "第二行文字"])
        # 行数对齐不变量：所有 chunk 的 text 行数 == page_numbers/line_numbers 长度
        for chunk in parsed["chunks"]:
            n = len(chunk.get("text", "").split("\n"))
            self.assertEqual(n, len(chunk.get("page_numbers") or []),
                             f"{chunk['chunk_id']} page_numbers 错位")
            self.assertEqual(n, len(chunk.get("line_numbers") or []),
                             f"{chunk['chunk_id']} line_numbers 错位")

    def test_explicit_page_break_resets_line_numbers(self):
        path = build_docx(self.dir / "g.docx",
                          paragraphs=["第一页内容一", "第一页内容二", "第二页内容一"],
                          page_break_after={1})
        parsed = pdf_routes.parse_docx(path, "DOC-DX2")
        pages = [l["page"] for l in parsed["lines"]]
        self.assertEqual(pages, [1, 1, 2])
        self.assertEqual(parsed["lines"][2]["line"], 1)  # 页内行号重置

    def test_no_break_markers_falls_back_to_fixed_lines_per_page(self):
        paragraphs = [f"第{index}行内容，用于验证无分页标记时的兜底切页。" for index in range(100)]
        path = build_docx(self.dir / "g.docx", paragraphs=paragraphs)
        parsed = pdf_routes.parse_docx(path, "DOC-DX3")
        pages = {l["page"] for l in parsed["lines"]}
        self.assertEqual(min(pages), 1)
        self.assertGreater(max(pages), 2)  # 100 行 / 45 行每页 ≥ 3 页
        # 行号在每页内重置
        for page in pages:
            lines_on_page = [l for l in parsed["lines"] if l["page"] == page]
            self.assertEqual([l["line"] for l in lines_on_page], list(range(1, len(lines_on_page) + 1)))

    def test_tables_mapped_and_attached_by_page(self):
        path = build_docx(self.dir / "g.docx",
                          paragraphs=["1  分级标准", "心功能分级如下。"],
                          table_rows=[["分级", "描述"], ["I级", "日常活动不受限"], ["II级", "轻度受限"]])
        parsed = pdf_routes.parse_docx(path, "DOC-DX4")
        self.assertEqual(len(parsed["tables"]), 1)
        table = parsed["tables"][0]
        self.assertTrue(table["table_id"].startswith("DOC-DX4:p"))
        self.assertTrue(table["review_required"])
        self.assertEqual(table["rows"][0], ["分级", "描述"])
        # 表格挂到覆盖该页的 chunk，而不是全文所有 chunk
        target = [c for c in parsed["chunks"] if any(t["table_id"] == table["table_id"] for t in c.get("tables", []))]
        self.assertEqual(len(target), 1)

    def test_reference_tail_reuses_pdf_chunking_chain(self):
        path = build_docx(self.dir / "g.docx", paragraphs=[
            "1  治疗", "推荐使用ACEI类药物改善预后。",
            "参 考 文 献",
            "[1] Author A, et al. Lancet 2020;18(2):33-41.",
            "[2] Author B, et al. NEJM 2019;380(5):411-22.",
            "[3] Author C, et al. JAMA 2021;325(6):543-55.",
        ])
        parsed = pdf_routes.parse_docx(path, "DOC-DX5")
        self.assertEqual(parsed["chunks"][-1]["section"], "参考文献")
        self.assertNotIn("[1] Author A", parsed["chunks"][-2]["text"])

    def test_dispatch_routes_pdf_to_parse_pdf_and_keeps_mock_patchable(self):
        # 硬约束：parse_document 以模块全局名调用 parse_pdf，
        # 7 处存量 mock.patch("src.api.routes.pdf.parse_pdf") 因此零改动生效。
        with mock.patch.object(pdf_routes, "parse_pdf", return_value={"marker": "patched"}) as patched:
            self.assertEqual(pdf_routes.parse_document("some.pdf", "DOC-D1"), {"marker": "patched"})
        patched.assert_called_once()

    def test_dispatch_routes_docx_without_pdf_parser(self):
        path = build_docx(self.dir / "g.docx", paragraphs=["正文"])
        with mock.patch.object(pdf_routes, "parse_pdf", side_effect=AssertionError("must not call parse_pdf")):
            parsed = pdf_routes.parse_document(path, "DOC-DX6")
        self.assertEqual(len(parsed["lines"]), 1)

    def test_dispatch_rejects_unsupported_extensions(self):
        for name in ("guide.txt", "guide", "guide.doc"):
            with self.assertRaises(ValueError, msg=name):
                pdf_routes.parse_document(self.dir / name, "DOC-DX7")

    def test_dispatch_accepts_str_path(self):
        path = build_docx(self.dir / "g.docx", paragraphs=["正文"])
        parsed = pdf_routes.parse_document(str(path), "DOC-DX8")
        self.assertEqual(parsed["doc_id"], "DOC-DX8")


class FileExtPersistenceTest(unittest.TestCase):
    """parsed.json 持久化 file_ext：重启恢复 file_path 不再假设 .pdf。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_persist_and_load_roundtrip(self):
        original_uploads = pdf_routes.UPLOAD_DIR
        pdf_routes.UPLOAD_DIR = self.dir
        self.addCleanup(setattr, pdf_routes, "UPLOAD_DIR", original_uploads)
        pdf_routes._doc_store.clear()
        self.addCleanup(pdf_routes._doc_store.clear)

        (self.dir / "DOC-EXT1.docx").write_bytes(b"PK")
        pdf_routes._persist_doc({
            "doc_id": "DOC-EXT1", "doc_title": "指南",
            "file_path": str(self.dir / "DOC-EXT1.docx"),
            "parsed": {"lines": []},
        })
        record = pdf_routes._load_doc("DOC-EXT1")
        self.assertTrue(str(record["file_path"]).endswith("DOC-EXT1.docx"))

    def test_legacy_record_without_file_ext_falls_back_to_pdf(self):
        original_uploads = pdf_routes.UPLOAD_DIR
        pdf_routes.UPLOAD_DIR = self.dir
        self.addCleanup(setattr, pdf_routes, "UPLOAD_DIR", original_uploads)
        pdf_routes._doc_store.clear()
        self.addCleanup(pdf_routes._doc_store.clear)

        # 旧版本写的 parsed.json 没有 file_ext：回退 .pdf，向后兼容
        (self.dir / "DOC-LEGACY.parsed.json").write_text(
            '{"doc_id": "DOC-LEGACY", "doc_title": "旧记录", "parsed": {"lines": []}}',
            encoding="utf-8",
        )
        record = pdf_routes._load_doc("DOC-LEGACY")
        self.assertTrue(str(record["file_path"]).endswith("DOC-LEGACY.pdf"))


if __name__ == "__main__":
    unittest.main()
