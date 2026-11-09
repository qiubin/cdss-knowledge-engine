"""PDF section/chunking tests for Arabic-numbered guideline headings."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes.pdf import SECTION_PATTERN, _split_long_chunks


class ChunkingTest(unittest.TestCase):
    def test_arabic_and_chinese_headings_match(self):
        for text in ("1  定义和分型", "4.2.1  实验室检查", "一、前言", "（一）概述"):
            self.assertIsNotNone(SECTION_PATTERN.match(text), text)
        for text in ("20 世纪90 年代国外研究", "10. 1186/s41687-020-00269-8."):
            self.assertIsNone(SECTION_PATTERN.match(text), text)

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
