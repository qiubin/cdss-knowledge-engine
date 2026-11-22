"""Danger-zone clear: wipe a graph and/or its rules with preview + backup."""
from __future__ import annotations

import json
import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes.kg import GraphClearRequest, clear_graph_and_rules
from src.api.routes import kg


def _write_graph(path: Path, node_count: int = 2) -> None:
    graph = {
        "domain": "western",
        "schema_version": 1,
        "nodes": [{"id": f"N{i}", "node_type": "disease"} for i in range(node_count)],
        "relations": [{"source_node_id": "N0", "relation_code": "r", "target_node_id": "N1"}],
        "diseases": [{"code": "I21.0"}],
        "evidences": [],
        "disease_evidence": [],
        "sources": [{"id": "S1"}],
    }
    path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")


def _write_rules(path: Path) -> None:
    rules = [
        {"rule_id": "R_KEEP", "graph_filename": "other_graph.json", "status": "active"},
        {"rule_id": "R_DROP_1", "graph_filename": "test_graph.json", "status": "active"},
        {"rule_id": "R_DROP_2", "graph_filename": "test_graph.json", "status": "draft"},
    ]
    path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")


class ClearGraphAndRulesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.kg_dir = self.root / "kg"
        self.kg_dir.mkdir()
        self.rules_path = self.root / "managed_rules.json"
        self.snapshot_dir = self.root / "history"
        _write_graph(self.kg_dir / "test_graph.json")
        _write_rules(self.rules_path)

    def _request(self, **kwargs) -> GraphClearRequest:
        base = {"graph_filename": "test_graph.json", "clear_graph": True, "clear_rules": True, "dry_run": True}
        base.update(kwargs)
        return GraphClearRequest(**base)

    def test_dry_run_reports_counts_and_touches_nothing(self):
        result = clear_graph_and_rules(
            self._request(), kg_dir=self.kg_dir, rules_path=self.rules_path, snapshot_dir=self.snapshot_dir
        )
        self.assertEqual(result["node_count"], 2)
        self.assertEqual(result["relation_count"], 1)
        self.assertEqual(result["rule_ids"], ["R_DROP_1", "R_DROP_2"])
        self.assertTrue(result["dry_run"])
        # Nothing was modified or backed up.
        self.assertEqual(len(json.loads(self.rules_path.read_text())), 3)
        self.assertEqual(len(json.loads((self.kg_dir / "test_graph.json").read_text())["nodes"]), 2)
        self.assertEqual(list(self.kg_dir.iterdir()), [self.kg_dir / "test_graph.json"])

    def test_clear_graph_writes_empty_skeleton_and_backup(self):
        result = clear_graph_and_rules(
            self._request(dry_run=False, clear_rules=False),
            kg_dir=self.kg_dir, rules_path=self.rules_path, snapshot_dir=self.snapshot_dir,
        )
        graph = json.loads((self.kg_dir / "test_graph.json").read_text())
        for key in ("nodes", "relations", "diseases", "evidences", "disease_evidence", "sources"):
            self.assertEqual(graph[key], [], f"{key} should be emptied")
        self.assertEqual(graph["domain"], "western")
        backups = [p for p in self.kg_dir.iterdir() if ".bak-" in p.name]
        self.assertEqual(len(backups), 1)
        self.assertIn("manual-clear", backups[0].name)
        self.assertEqual(len(json.loads(backups[0].read_text())["nodes"]), 2)
        # Rules untouched when clear_rules=False.
        self.assertEqual(len(json.loads(self.rules_path.read_text())), 3)
        self.assertEqual(result["cleared_rules"], 0)

    def test_clear_rules_removes_matching_rules_and_snapshots(self):
        result = clear_graph_and_rules(
            self._request(dry_run=False, clear_graph=False),
            kg_dir=self.kg_dir, rules_path=self.rules_path, snapshot_dir=self.snapshot_dir,
        )
        remaining = json.loads(self.rules_path.read_text())
        self.assertEqual([r["rule_id"] for r in remaining], ["R_KEEP"])
        self.assertEqual(result["cleared_rules"], 2)
        # A full rules backup was written next to the rules file.
        backups = [p for p in self.rules_path.parent.iterdir() if ".bak-" in p.name]
        self.assertEqual(len(backups), 1)
        self.assertEqual(len(json.loads(backups[0].read_text())), 3)
        # Deletion history was recorded per removed rule.
        snapshots = list(self.snapshot_dir.glob("R_DROP_*.jsonl"))
        self.assertEqual(len(snapshots), 2)
        # Graph untouched when clear_graph=False.
        self.assertEqual(len(json.loads((self.kg_dir / "test_graph.json").read_text())["nodes"]), 2)

    def test_graph_list_hides_cleared_graphs_and_shows_reimported_graphs(self):
        filename = "test_graph.json"
        graph_path = self.kg_dir / filename
        legacy_path = self.kg_dir / "legacy.json"
        legacy_path.write_text('{"nodes": [], "relations": []}', encoding="utf-8")
        (self.kg_dir / "legacy.json.bak-20261121-manual-clear").touch()
        empty_path = self.kg_dir / "new.json"
        empty_path.write_text('{"nodes": [], "relations": []}', encoding="utf-8")
        with patch.object(kg, "KG_DIR", self.kg_dir):
            listed = lambda: {item["graph_filename"] for item in asyncio.run(kg.list_graphs())}
            self.assertEqual(listed(), {filename, empty_path.name})
            clear_graph_and_rules(
                self._request(dry_run=False, clear_rules=False), kg_dir=self.kg_dir,
                rules_path=self.rules_path, snapshot_dir=self.snapshot_dir,
            )
            self.assertEqual(listed(), {empty_path.name})
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["nodes"] = [{"id": "NEW", "node_type": "disease"}]
            graph_path.write_text(json.dumps(graph), encoding="utf-8")
            self.assertEqual(listed(), {filename, empty_path.name})

    def test_preview_respects_selected_scope(self):
        for clear_graph, clear_rules in ((True, False), (False, True)):
            with self.subTest(clear_graph=clear_graph, clear_rules=clear_rules):
                result = clear_graph_and_rules(
                    self._request(clear_graph=clear_graph, clear_rules=clear_rules),
                    kg_dir=self.kg_dir, rules_path=self.rules_path, snapshot_dir=self.snapshot_dir,
                )
                self.assertEqual(result["node_count"], 2 if clear_graph else 0)
                self.assertEqual(result["relation_count"], 1 if clear_graph else 0)
                self.assertEqual(result["rule_ids"], ["R_DROP_1", "R_DROP_2"] if clear_rules else [])

    def test_clear_both_includes_reviewed_data_and_preserves_source_material(self):
        source_files = [self.root / name for name in ("guideline.pdf", "parsed.json", "package.json", "vocab.json")]
        for path in source_files:
            path.write_bytes(b"source-material")
        graph_path = self.kg_dir / "test_graph.json"
        graph = json.loads(graph_path.read_text())
        graph["nodes"][0]["review_status"] = "active"
        graph["relations"][0]["review_status"] = "active"
        graph_path.write_text(json.dumps(graph), encoding="utf-8")
        result = clear_graph_and_rules(
            self._request(dry_run=False), kg_dir=self.kg_dir,
            rules_path=self.rules_path, snapshot_dir=self.snapshot_dir,
        )
        self.assertEqual(result["cleared_rules"], 2)
        self.assertEqual(json.loads(graph_path.read_text())["nodes"], [])
        self.assertEqual(json.loads(graph_path.read_text())["relations"], [])
        self.assertEqual([r["rule_id"] for r in json.loads(self.rules_path.read_text())], ["R_KEEP"])
        for path in source_files:
            self.assertEqual(path.read_bytes(), b"source-material")

    def test_repeated_clear_preserves_each_backup(self):
        for _ in range(2):
            clear_graph_and_rules(
                self._request(dry_run=False, clear_rules=False), kg_dir=self.kg_dir,
                rules_path=self.rules_path, snapshot_dir=self.snapshot_dir,
            )
        self.assertEqual(len(list(self.kg_dir.glob("*.bak-*"))), 2)

    def test_clear_nothing_requested_is_rejected(self):
        with self.assertRaises(Exception):
            clear_graph_and_rules(
                self._request(clear_graph=False, clear_rules=False, dry_run=False),
                kg_dir=self.kg_dir, rules_path=self.rules_path, snapshot_dir=self.snapshot_dir,
            )


if __name__ == "__main__":
    unittest.main()
