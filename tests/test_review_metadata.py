import json
import tempfile
import unittest
from pathlib import Path

from src.review.metadata import (
    HISTORY_DIR,
    apply_import_review_metadata,
    capture_previous_contents,
    read_entity_history,
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


class EntityHistoryTest(unittest.TestCase):
    """内容变化必须归档旧版本，供审核台 stale diff 展示。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        original_dir = HISTORY_DIR
        import src.review.metadata as metadata
        metadata.HISTORY_DIR = Path(self.tmp.name)
        self.addCleanup(setattr, metadata, "HISTORY_DIR", original_dir)

    def test_changed_node_writes_snapshot(self):
        node = {"id": "N1", "node_type": "symptom", "code": "S1", "name": "旧名", "review_status": "active"}
        graph = {"nodes": [node], "relations": []}
        previous = snapshot_hashes(graph)
        contents = capture_previous_contents(graph)
        graph["nodes"][0]["name"] = "新名"
        apply_import_review_metadata(
            graph, previous, "DOC-1", previous_contents=contents, graph_filename="HIST.json"
        )
        rows = read_entity_history("HIST.json", "node", "N1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"]["name"], "旧名")
        self.assertEqual(rows[0]["entity_type"], "node")
        self.assertEqual(rows[0]["seq"], 1)
        self.assertEqual(graph["nodes"][0]["review_status"], "stale")

    def test_unchanged_node_writes_no_snapshot(self):
        node = {
            "id": "N1", "node_type": "symptom", "code": "S1", "name": "同名",
            "review_status": "active", "source_refs": ["DOC-1_p1_l1-5"],
        }
        graph = {"nodes": [node], "relations": []}
        previous = snapshot_hashes(graph)
        contents = capture_previous_contents(graph)
        graph["nodes"][0]["source_refs"].append("DOC-2_p2_l1-3")
        apply_import_review_metadata(
            graph, previous, "DOC-2", previous_contents=contents, graph_filename="UNCHANGED.json"
        )
        self.assertEqual(read_entity_history("UNCHANGED.json", "node", "N1"), [])

    def test_changed_relation_writes_snapshot(self):
        relation = {"source_node_id": "A", "relation_code": "REL", "target_node_id": "B", "review_status": "active"}
        graph = {"nodes": [], "relations": [relation]}
        previous = snapshot_hashes(graph)
        contents = capture_previous_contents(graph)
        graph["relations"][0]["attributes"] = {"strength": "强"}
        apply_import_review_metadata(
            graph, previous, "DOC-1", previous_contents=contents, graph_filename="RELHIST.json"
        )
        rows = read_entity_history("RELHIST.json", "relation", "A|REL|B")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"]["source_node_id"], "A")
        self.assertNotIn("strength", rows[0]["content"].get("attributes") or {})


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
