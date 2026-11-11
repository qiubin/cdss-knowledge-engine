# tests/test_rule_history.py
import asyncio
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest
from fastapi import HTTPException

from src.api.routes import rules as rules_routes
from src.api.routes.rules import RuleRestoreRequest, restore_rule_snapshot
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


def _isolate(tmp_path, monkeypatch, live_rules):
    """Point HISTORY_DIR / MANAGED_RULES_PATH / audit path at tmp locations."""
    rules_path = tmp_path / "managed_rules.json"
    rules_path.write_text(json.dumps(live_rules, ensure_ascii=False), encoding="utf-8")
    audit_path = tmp_path / "review_log.jsonl"
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(rules_routes, "MANAGED_RULES_PATH", rules_path)
    monkeypatch.setattr(rules_routes, "REVIEW_AUDIT_PATH", audit_path)
    return rules_path, audit_path


def test_restore_endpoint_restores_content_and_triple_write(tmp_path, monkeypatch):
    rules_path, audit_path = _isolate(
        tmp_path, monkeypatch, [{"rule_id": "R-1", "name": "current", "status": "draft"}]
    )
    history.record_snapshot({"rule_id": "R-1", "name": "v1", "status": "draft"}, "create")

    restored = asyncio.run(restore_rule_snapshot("R-1", 1, RuleRestoreRequest(reviewer="测试")))

    assert restored["restored_from"] == 1
    assert restored["name"] == "v1"
    on_disk = json.loads(rules_path.read_text(encoding="utf-8"))
    assert len(on_disk) == 1
    assert on_disk[0]["name"] == "v1"
    assert on_disk[0]["restored_from"] == 1
    assert on_disk[0]["updated_at"]
    items = history.list_snapshots("R-1")
    assert [item["seq"] for item in items] == [1, 2]
    assert items[1]["action"] == "rollback"
    assert items[1]["actor"] == "测试"
    assert items[1]["rule"]["name"] == "v1"
    audit_lines = [
        json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert audit_lines[-1]["action"] == "rollback"
    assert audit_lines[-1]["entity_id"] == "R-1"
    assert audit_lines[-1]["reviewer"] == "测试"


def test_restore_unknown_seq_returns_404(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch, [{"rule_id": "R-1", "name": "current", "status": "draft"}])
    history.record_snapshot({"rule_id": "R-1", "name": "v1", "status": "draft"}, "create")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(restore_rule_snapshot("R-1", 99, RuleRestoreRequest()))
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "历史版本不存在"


def test_restore_missing_live_rule_returns_404(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch, [])
    history.record_snapshot({"rule_id": "GONE", "name": "v1", "status": "draft"}, "create")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(restore_rule_snapshot("GONE", 1, RuleRestoreRequest()))
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "规则未找到"


def test_restore_active_snapshot_blocked_by_dependency_gate(tmp_path, monkeypatch):
    live = [{"rule_id": "R-1", "name": "current", "status": "draft"}]
    rules_path, _ = _isolate(tmp_path, monkeypatch, live)
    history.record_snapshot(
        {
            "rule_id": "R-1",
            "name": "v1-active",
            "status": "active",
            "graph_filename": "../evil.json",
            "disease_code": "NOPE",
        },
        "create",
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(restore_rule_snapshot("R-1", 1, RuleRestoreRequest()))
    assert exc_info.value.status_code == 422
    assert "语义不一致" in str(exc_info.value.detail)
    assert json.loads(rules_path.read_text(encoding="utf-8")) == live
    assert [item["seq"] for item in history.list_snapshots("R-1")] == [1]
