import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest

from src.api.routes import inference as inference_routes
from src.engine import settings as engine_settings


@pytest.fixture()
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / "engine.json"
    monkeypatch.setattr(engine_settings, "SETTINGS_PATH", path)
    return path


def _patient_payload():
    raw = json.loads(
        (project_root / "data" / "patients" / "demo_stemi_patient.json").read_text(encoding="utf-8")
    )
    return raw


def test_engine_field_present_by_default(settings_path):
    result = inference_routes._inference_result(
        inference_routes.PatientSnapshot(**_patient_payload()), "western", None
    )
    assert result["engine"]["requested"] == "native"
    assert result["engine"]["used"] == "native"


def test_drools_mode_falls_back_to_native(settings_path):
    engine_settings.save_settings(
        {
            "active": "drools",
            "drools": {"base_url": "http://127.0.0.1:1", "timeout_seconds": 1, "fail_over_native": True},
        },
        settings_path,
    )
    result = inference_routes._inference_result(
        inference_routes.PatientSnapshot(**_patient_payload()), "western", None
    )
    assert result["engine"]["requested"] == "drools"
    assert result["engine"]["used"] == "native"
    assert result["engine"]["fallback_reason"]
