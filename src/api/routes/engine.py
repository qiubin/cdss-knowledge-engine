"""Rule engine selection and status API."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.engine.settings import load_settings, save_settings

router = APIRouter(prefix="/api/engine", tags=["engine"])


def _settings_path():
    """Indirection for tests: monkeypatch target for the settings file."""
    from src.engine.settings import SETTINGS_PATH

    return SETTINGS_PATH


class EngineConfigPayload(BaseModel):
    config: Dict[str, Any]


@router.get("/config")
def get_engine_config():
    return load_settings(_settings_path())


@router.put("/config")
def update_engine_config(payload: EngineConfigPayload):
    try:
        return save_settings(payload.config, _settings_path())
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _probe_drools(drools: Dict[str, Any]) -> Dict[str, Any]:
    import httpx

    base_url = str(drools.get("base_url") or "").rstrip("/")
    timeout = float(drools.get("timeout_seconds") or 5)
    if not base_url:
        return {"reachable": False, "detail": "缺少 base_url"}
    try:
        response = httpx.get(f"{base_url}/health", timeout=timeout)
    except Exception as exc:
        return {"reachable": False, "detail": str(exc)[:200]}
    if response.status_code != 200:
        return {"reachable": False, "detail": f"HTTP {response.status_code}"}
    return {"reachable": True, "detail": response.text[:200]}


@router.get("/status")
def engine_status():
    settings = load_settings(_settings_path())
    active = str(settings.get("active") or "native")
    drools = settings.get("drools") or {}
    return {
        "active": active,
        "available": ["native", "drools"],
        "drools": _probe_drools(drools),
    }
