# tests/test_engine_settings.py
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest

from src.engine import settings as engine_settings


def test_default_settings_when_missing(tmp_path):
    result = engine_settings.load_settings(tmp_path / "missing.json")
    assert result["active"] == "native"
    assert result["drools"]["fail_over_native"] is True
    assert result["drools"]["timeout_seconds"] == 5


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "engine.json"
    saved = engine_settings.save_settings(
        {"active": "drools", "drools": {"base_url": "http://localhost:9000"}},
        path,
    )
    assert saved["active"] == "drools"
    assert saved["drools"]["base_url"] == "http://localhost:9000"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["active"] == "drools"


def test_reject_unknown_engine(tmp_path):
    with pytest.raises(ValueError):
        engine_settings.save_settings({"active": "spark"}, tmp_path / "engine.json")


def test_unknown_keys_dropped_and_active_normalized(tmp_path):
    path = tmp_path / "engine.json"
    path.write_text(json.dumps({"active": "NATIVE", "drools": {"bogus": 1}}), encoding="utf-8")
    loaded = engine_settings.load_settings(path)
    assert loaded["active"] == "native"
    assert "bogus" not in loaded["drools"]
