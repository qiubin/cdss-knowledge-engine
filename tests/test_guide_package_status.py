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


if __name__ == "__main__":
    unittest.main()
