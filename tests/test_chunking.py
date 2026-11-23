"""PDF section/chunking tests for Arabic-numbered guideline headings."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes.pdf import (
    SECTION_PATTERN,
    _is_tail_section_heading,
    _is_reference_entry,
    _split_long_chunks,
    section_chunks,
)


def make_lines(texts, page=1):
    return [{"page": page, "line": index + 1, "text": text} for index, text in enumerate(texts)]


class ChunkingTest(unittest.TestCase):
    def test_arabic_and_chinese_headings_match(self):
        for text in ("1  定义和分型", "4.2.1  实验室检查", "一、前言", "（一）概述"):
            self.assertIsNotNone(SECTION_PATTERN.match(text), text)
        for text in ("20 世纪90 年代国外研究", "10. 1186/s41687-020-00269-8."):
            self.assertIsNone(SECTION_PATTERN.match(text), text)

    def test_tail_section_heading_variants(self):
        for text in ("参考文献", "参 考 文 献", "参　考　文　献", "引用文献", "References", "REFERENCES"):
            self.assertTrue(_is_tail_section_heading(text), text)
        for text in ("参考文献管理规范", "相关文献综述", "References and evidence tables"):
            self.assertFalse(_is_tail_section_heading(text), text)

    def test_reference_entry_detection(self):
        for text in ("[1] Zhang Y, et al. Lancet 2020;18(2):33-41.",
                     "[12] 中华心血管病杂志, 2019, 47(12): 1025-1036.",
                     "3. Ponikowski P, et al. Eur Heart J 2016;37(27):2129-2200m."):
            self.assertTrue(_is_reference_entry(text), text)
        for text in ("3. 评估左室射血分数", "[推荐] 使用ACEI类药物", "1、气虚血瘀证"):
            self.assertFalse(_is_reference_entry(text), text)

    def test_spaced_reference_heading_starts_tail_chunk(self):
        lines = make_lines([
            "1  定义和分型", "慢性心力衰竭的定义如下。", "参 考 文 献", "[1] reference one",
        ])
        chunks = section_chunks("D1", lines)
        self.assertEqual(chunks[-1]["section"], "参考文献")

    def test_headingless_tail_reference_run_is_carved_out(self):
        lines = make_lines([
            "1  治疗", "推荐使用ACEI类药物改善预后。",
            "[1] Author A, et al. Lancet 2020;18(2):33-41.",
            "[2] Author B, et al. NEJM 2019;380(5):411-22.",
            "[3] Author C, et al. JAMA 2021;325(6):543-55.",
        ])
        chunks = section_chunks("D2", lines)
        self.assertEqual(chunks[-1]["section"], "参考文献")
        self.assertIn("[3] Author C", chunks[-1]["text"])
        self.assertNotIn("[1] Author A", chunks[-2]["text"])

    def test_clinical_numbering_not_carved_out(self):
        lines = make_lines([
            "1  治疗",
            "1. 应当评估患者射血分数。",
            "2. 推荐使用ACEI类药物。",
        ])
        chunks = section_chunks("D3", lines)
        self.assertNotEqual(chunks[-1]["section"], "参考文献")

    def test_long_chunk_is_split_preserving_line_ranges(self):
        text = "\n".join(f"line {index} " + "x" * 80 for index in range(400))
        chunk = {
            "chunk_id": "DOC_p1_l1-400",
            "section": "长章节",
            "page_start": 1,
            "page_end": 2,
            "line_start": 1,
            "line_end": 400,
            "page_numbers": [1] * 200 + [2] * 200,
            "line_numbers": list(range(1, 401)),
            "text": text,
        }
        chunks = _split_long_chunks([chunk])
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0]["line_start"], 1)
        self.assertEqual(chunks[-1]["line_end"], 400)
        self.assertTrue(all(len(item["text"]) <= 12000 for item in chunks))
        self.assertEqual(len({item["chunk_id"] for item in chunks}), len(chunks))


if __name__ == "__main__":
    unittest.main()
