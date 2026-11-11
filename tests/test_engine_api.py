# tests/test_engine_api.py
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest

from src.api.routes import engine as engine_routes
from src.engine import settings as engine_settings


@pytest.fixture()
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / "engine.json"
    monkeypatch.setattr(engine_settings, "SETTINGS_PATH", path)
    monkeypatch.setattr(engine_routes, "_settings_path", lambda: path)
    return path


def test_get_config_defaults(settings_path):
    result = engine_routes.get_engine_config()
    assert result["active"] == "native"


def test_put_config_validates(settings_path):
    payload = engine_routes.EngineConfigPayload(config={"active": "spark"})
    with pytest.raises(Exception) as exc:
        engine_routes.update_engine_config(payload)
    assert "未知引擎" in str(getattr(exc.value, "detail", exc.value))


def test_put_and_get_config(settings_path):
    payload = engine_routes.EngineConfigPayload(
        config={"active": "drools", "drools": {"base_url": "http://127.0.0.1:1"}}
    )
    saved = engine_routes.update_engine_config(payload)
    assert saved["active"] == "drools"


def test_status_reports_unreachable_drools(settings_path):
    engine_settings.save_settings(
        {"active": "drools", "drools": {"base_url": "http://127.0.0.1:1", "timeout_seconds": 1}},
        settings_path,
    )
    status = engine_routes.engine_status()
    assert status["active"] == "drools"
    assert status["drools"]["reachable"] is False


def test_status_native_has_no_probe(settings_path):
    status = engine_routes.engine_status()
    assert status["active"] == "native"
    assert "reachable" in status["drools"]
