import unittest

from src.review.metadata import (
    apply_import_review_metadata,
    derive_doc_ids,
    snapshot_hashes,
)


class OrphanTest(unittest.TestCase):
    def test_derive_doc_ids_from_chunk_refs(self):
        self.assertEqual(
            derive_doc_ids(["DOC-ABC_p1_l1-10", "DOC-ABC_p2_l3-9", "DOC-XYZ_p1_l1-2"]),
            ["DOC-ABC", "DOC-XYZ"],
        )

    def test_item_not_in_current_import_becomes_orphaned(self):
        graph = {
            "nodes": [{
                "id": "N1",
                "node_type": "symptom",
                "code": "S1",
                "name": "症状",
                "review_status": "active",
                "source_refs": ["DOC-1_p1_l1-10"],
                "source_doc_ids": ["DOC-1"],
            }],
            "relations": [],
        }
        previous = snapshot_hashes(graph)
        apply_import_review_metadata(
            graph, previous, "DOC-1", current_chunk_ids={"DOC-1_p9_l1-2"}
        )
        self.assertIn("DOC-1", graph["nodes"][0]["orphaned_from"])

    def test_item_seen_in_current_import_is_not_orphaned(self):
        graph = {
            "nodes": [{
                "id": "N1",
                "node_type": "symptom",
                "code": "S1",
                "name": "症状",
                "review_status": "active",
                "source_refs": ["DOC-1_p1_l1-10"],
                "source_doc_ids": ["DOC-1"],
            }],
            "relations": [],
        }
        previous = snapshot_hashes(graph)
        apply_import_review_metadata(
            graph, previous, "DOC-1", current_chunk_ids={"DOC-1_p1_l1-10"}
        )
        self.assertNotIn("DOC-1", graph["nodes"][0].get("orphaned_from", []))
