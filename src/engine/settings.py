"""Rule engine runtime settings (data/engine.json).

The file is optional; absence means "native engine, default drools endpoint".
This keeps current behaviour identical when the dual-engine feature is off.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "data" / "engine.json"

ENGINES = ("native", "drools")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "active": "native",
    "drools": {
        "base_url": "http://127.0.0.1:8090",
        "timeout_seconds": 5,
        "fail_over_native": True,
    },
}


def _deep_default() -> Dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_SETTINGS))


def load_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    target = path or SETTINGS_PATH
    merged = _deep_default()
    if not target.exists():
        return merged
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return merged
    if not isinstance(data, dict):
        return merged
    active = str(data.get("active") or merged["active"]).strip().lower()
    merged["active"] = active if active in ENGINES else "native"
    drools = data.get("drools")
    if isinstance(drools, dict):
        for key in list(merged["drools"]):
            if key in drools:
                merged["drools"][key] = drools[key]
    return merged


def save_settings(settings: Dict[str, Any], path: Optional[Path] = None) -> Dict[str, Any]:
    target = path or SETTINGS_PATH
    merged = load_settings(target)
    active = str(settings.get("active") or merged["active"]).strip().lower()
    if active not in ENGINES:
        raise ValueError(f"未知引擎: {active}，可选 {'、'.join(ENGINES)}")
    merged["active"] = active
    drools = settings.get("drools")
    if isinstance(drools, dict):
        for key in list(merged["drools"]):
            if key in drools:
                merged["drools"][key] = drools[key]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged
