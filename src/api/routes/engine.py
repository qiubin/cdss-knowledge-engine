"""Rule engine selection and status API."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.engine.drools.compiler import compile_rule, describe_uncompilable, walk_leaves
from src.engine.settings import load_settings, save_settings
from src.models.rule import Rule, RuleCondition, RuleConclusion

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


class DrlPreviewPayload(BaseModel):
    rule_id: str = "PREVIEW"
    priority: int = 50
    conditions: Dict[str, Any]


@router.post("/drl-preview")
def drl_preview(payload: DrlPreviewPayload):
    """Compile a condition tree to DRL without persisting anything.

    Uses the exact same ``compile_rule`` path as Drools inference, so the
    preview shown in the rule editor always matches runtime behavior.
    """
    try:
        conditions = RuleCondition(**payload.conditions)
    except Exception as exc:
        raise HTTPException(422, f"条件树不合法: {exc}") from exc
    rule = Rule(
        rule_id=payload.rule_id or "PREVIEW",
        disease_code="PREVIEW",
        priority=payload.priority,
        conditions=conditions,
        conclusion=RuleConclusion(diagnosis="PREVIEW"),
    )
    compiled = compile_rule(rule)
    leaves = walk_leaves(conditions)
    return {
        "compilable": compiled is not None,
        "drl": compiled.drl if compiled else None,
        "reason": describe_uncompilable(conditions),
        "leaf_count": len(leaves or []),
    }
