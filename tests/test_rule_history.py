# tests/test_rule_history.py
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.rules import history


def _rule(name):
    return {"rule_id": "H-1", "name": name, "status": "draft", "priority": 50}


def test_create_update_snapshot_sequence(tmp_path):
    directory = tmp_path / "history"
    history.record_snapshot(_rule("v1"), "create", directory=directory)
    history.record_snapshot(_rule("v2"), "update", directory=directory)
    items = history.list_snapshots("H-1", directory)
    assert [item["seq"] for item in items] == [1, 2]
    assert [item["action"] for item in items] == ["create", "update"]
    assert items[1]["rule"]["name"] == "v2"


def test_get_snapshot_by_seq(tmp_path):
    directory = tmp_path / "history"
    history.record_snapshot(_rule("v1"), "create", directory=directory)
    entry = history.get_snapshot("H-1", 1, directory)
    assert entry["rule"]["name"] == "v1"
    assert history.get_snapshot("H-1", 99, directory) is None


def test_missing_history_returns_empty(tmp_path):
    assert history.list_snapshots("NOPE", tmp_path / "history") == []


def test_rule_id_sanitized(tmp_path):
    directory = tmp_path / "history"
    history.record_snapshot({"rule_id": "a/b\\c:d", "name": "x"}, "create", directory=directory)
    files = list(directory.glob("*.jsonl"))
    assert len(files) == 1
    assert "/" not in files[0].name and "\\" not in files[0].name
