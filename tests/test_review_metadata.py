import json
import tempfile
import unittest
from pathlib import Path

from src.review.metadata import (
    apply_import_review_metadata,
    snapshot_hashes,
)
from tools.migrate_review_status import migrate


class MetadataTest(unittest.TestCase):
    def test_unchanged_active_node_stays_active(self):
        node = {"id": "N1", "node_type": "symptom", "code": "S1", "name": "症状", "review_status": "active"}
        graph = {"nodes": [node], "relations": []}
        previous = snapshot_hashes(graph)
        apply_import_review_metadata(graph, previous, "DOC-1")
        self.assertEqual(graph["nodes"][0]["review_status"], "active")

    def test_provenance_only_change_keeps_active(self):
        """Another guideline sharing the same entity only grows source_refs —
        an approved node must NOT flip to stale."""
        graph = {
            "nodes": [{
                "id": "N1", "node_type": "symptom", "code": "S1", "name": "症状",
                "review_status": "active", "source_refs": ["DOC-1_p1_l1-10"],
                "source_doc_ids": ["DOC-1"],
            }],
            "relations": [],
        }
        previous = snapshot_hashes(graph)
        graph["nodes"][0]["source_refs"].append("DOC-2_p3_l1-5")
        apply_import_review_metadata(graph, previous, "DOC-2")
        self.assertEqual(graph["nodes"][0]["review_status"], "active")
        self.assertIn("DOC-2", graph["nodes"][0]["source_doc_ids"])

    def test_changed_active_node_becomes_stale(self):
        graph = {"nodes": [{"id": "N1", "node_type": "symptom", "code": "S1", "name": "旧名", "review_status": "active", "source_refs": ["DOC-1_p1_l1-5"]}], "relations": []}
        previous = snapshot_hashes(graph)
        graph["nodes"][0]["name"] = "新名"
        apply_import_review_metadata(graph, previous, "DOC-1")
        self.assertEqual(graph["nodes"][0]["review_status"], "stale")
        self.assertEqual(graph["nodes"][0]["version"], 2)
        self.assertIn("DOC-1", graph["nodes"][0]["source_doc_ids"])

    def test_stale_endpoint_marks_relation_stale(self):
        graph = {
            "nodes": [
                {"id": "N1", "node_type": "disease", "code": "D1", "name": "疾病", "review_status": "active"},
                {"id": "N2", "node_type": "symptom", "code": "S1", "name": "旧名", "review_status": "active"},
            ],
            "relations": [
                {"source_node_id": "N1", "relation_code": "DISEASE_HAS_SYMPTOM", "target_node_id": "N2", "review_status": "active"}
            ],
        }
        previous = snapshot_hashes(graph)
        graph["nodes"][1]["name"] = "新名"
        apply_import_review_metadata(graph, previous, "DOC-1")
        self.assertEqual(graph["relations"][0]["review_status"], "stale")


class MigrationTest(unittest.TestCase):
    def test_migration_bootstraps_active_rule_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_dir = root / "kg"
            graph_dir.mkdir()
            rules_path = root / "rules" / "managed_rules.json"
            rules_path.parent.mkdir()
            audit_path = root / "review" / "review_log.jsonl"
            graph = {
                "schema_version": 1,
                "domain": "western",
                "anchor_types": ["disease"],
                "diseases": [{"code": "I21.0", "name_cn": "测试病"}],
                "evidences": [],
                "disease_evidence": [],
                "sources": [],
                "nodes": [
                    {"id": "ENTITY:disease:I21.0", "node_type": "disease", "code": "I21.0", "name": "测试病", "review_status": "draft"},
                    {"id": "ENTITY:symptom:SYM_TEST", "node_type": "symptom", "code": "SYM_TEST", "name": "测试症状", "review_status": "draft"},
                ],
                "relations": [
                    {
                        "source_node_id": "ENTITY:disease:I21.0",
                        "relation_code": "DISEASE_HAS_SYMPTOM",
                        "target_node_id": "ENTITY:symptom:SYM_TEST",
                        "review_status": "draft",
                    }
                ],
            }
            (graph_dir / "MIGRATE.json").write_text(
                json.dumps(graph, ensure_ascii=False), encoding="utf-8"
            )
            rules_path.write_text(json.dumps([{
                "rule_id": "R1",
                "graph_filename": "MIGRATE.json",
                "disease_code": "I21.0",
                "rule_type": "diagnosis",
                "status": "active",
                "conditions": {
                    "type": "group",
                    "operator": "AND",
                    "children": [{"type": "symptom", "symptom_code": "SYM_TEST"}],
                },
                "conclusion": {"diagnosis": "I21.0"},
            }], ensure_ascii=False), encoding="utf-8")

            result = migrate(graph_dir, rules_path, audit_path)
            self.assertGreaterEqual(result["bootstrapped_nodes"], 2)
            saved = json.loads((graph_dir / "MIGRATE.json").read_text(encoding="utf-8"))
            statuses = {item["id"]: item["review_status"] for item in saved["nodes"]}
            self.assertEqual(statuses["ENTITY:disease:I21.0"], "active")
            self.assertEqual(statuses["ENTITY:symptom:SYM_TEST"], "active")
            self.assertTrue(audit_path.exists())
