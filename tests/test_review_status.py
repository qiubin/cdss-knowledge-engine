import json
import tempfile
import unittest
from pathlib import Path

from src.review.audit import append_audit, load_audit
from src.review.status import (
    StatusTransitionError,
    next_graph_status,
    next_rule_status,
)


class GraphStatusTest(unittest.TestCase):
    def test_draft_approve_becomes_active(self):
        self.assertEqual(next_graph_status("draft", "approve"), "active")

    def test_active_retire_becomes_retired(self):
        self.assertEqual(next_graph_status("active", "retire"), "retired")

    def test_active_cannot_be_approved_again(self):
        with self.assertRaises(StatusTransitionError):
            next_graph_status("active", "approve")

    def test_stale_approve_becomes_active(self):
        self.assertEqual(next_graph_status("stale", "approve"), "active")

    def test_retired_restore_becomes_draft(self):
        self.assertEqual(next_graph_status("retired", "restore"), "draft")


class RuleStatusTest(unittest.TestCase):
    def test_blocked_rule_can_be_approved(self):
        self.assertEqual(next_rule_status("blocked", "approve"), "active")

    def test_active_rule_cannot_be_approved_again(self):
        with self.assertRaises(StatusTransitionError):
            next_rule_status("active", "approve")


class AuditTest(unittest.TestCase):
    def test_append_and_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review" / "review_log.jsonl"
            append_audit({
                "action": "approve",
                "entity_type": "node",
                "entity_id": "ENTITY:symptom:SYM_TEST",
                "graph_filename": "TEST.json",
                "before_status": "draft",
                "after_status": "active",
                "reviewer": "张医生",
            }, path)
            append_audit({
                "action": "retire",
                "entity_type": "node",
                "entity_id": "ENTITY:symptom:OTHER",
                "graph_filename": "TEST.json",
                "before_status": "active",
                "after_status": "retired",
                "reviewer": "李医生",
            }, path)
            rows = load_audit(path=path, entity_id="ENTITY:symptom:SYM_TEST")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["reviewer"], "张医生")
            self.assertIn("ts", rows[0])
            all_rows = load_audit(path=path)
            self.assertEqual(len(all_rows), 2)
