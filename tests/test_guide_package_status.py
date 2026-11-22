"""Regression tests for knowledge package import state transitions."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes import guide_packages


class GuidePackageStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.package_dir = Path(self.tmp.name)
        original_dir = guide_packages.PACKAGE_DIR
        guide_packages.PACKAGE_DIR = self.package_dir
        self.addCleanup(setattr, guide_packages, "PACKAGE_DIR", original_dir)

    def _write_package(self, status: str) -> None:
        package = {
            "package_id": "PKG-TEST",
            "doc_id": "DOC-TEST",
            "doc_title": "测试指南",
            "domain": "western",
            "status": status,
            "entities": [],
            "chunks": [],
            "suggested_relations": [],
            "suggested_rules": [],
            "import_result": None,
        }
        (self.package_dir / "PKG-TEST.json").write_text(
            json.dumps(package, ensure_ascii=False), encoding="utf-8"
        )

    def test_cannot_manually_mark_unimported_package_as_imported(self) -> None:
        self._write_package("reviewed")

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(guide_packages.update_package(
                "PKG-TEST",
                guide_packages.GuidePackageUpdateRequest(status="ready_for_test"),
            ))

        self.assertEqual(raised.exception.status_code, 422)

    def test_ready_for_test_package_can_be_reimported(self) -> None:
        self.assertIn("ready_for_test", guide_packages.IMPORTABLE_STATUS)

    def test_delete_package_requires_confirm_and_removes_file(self) -> None:
        self._write_package("generated")

        # 未确认时拒绝删除
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(guide_packages.delete_package("PKG-TEST", confirm=False))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertTrue((self.package_dir / "PKG-TEST.json").exists())

        result = asyncio.run(guide_packages.delete_package("PKG-TEST", confirm=True))
        self.assertEqual(result["deleted"], "PKG-TEST")
        self.assertFalse(result["imported"])
        self.assertFalse((self.package_dir / "PKG-TEST.json").exists())

    def test_delete_imported_package_warns_but_allowed_with_confirm(self) -> None:
        package = {
            "package_id": "PKG-IMPORTED",
            "doc_id": "DOC-X",
            "doc_title": "已导入指南",
            "domain": "western",
            "status": "ready_for_test",
            "entities": [],
            "chunks": [],
            "suggested_relations": [],
            "suggested_rules": [],
            "import_result": {"success": True, "graph": {"graph_filename": "kg_x.json"}},
            "graph_filename": "kg_x.json",
        }
        (self.package_dir / "PKG-IMPORTED.json").write_text(
            json.dumps(package, ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(guide_packages.delete_package("PKG-IMPORTED", confirm=False))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("已导入", raised.exception.detail)

        result = asyncio.run(guide_packages.delete_package("PKG-IMPORTED", confirm=True))
        self.assertTrue(result["imported"])
        self.assertEqual(result["graph_filename"], "kg_x.json")
        self.assertIn("不受影响", result["note"])
        self.assertFalse((self.package_dir / "PKG-IMPORTED.json").exists())

    def test_delete_missing_package_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(guide_packages.delete_package("PKG-NONE", confirm=True))
        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
