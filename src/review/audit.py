"""Append-only review audit log."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

AUDIT_PATH = Path(__file__).resolve().parents[2] / "data" / "review" / "review_log.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_audit(record: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = Path(path) if path is not None else AUDIT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(record)
    payload.setdefault("ts", _now())
    line = json.dumps(payload, ensure_ascii=False, default=str)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()


def load_audit(
    path: Optional[Path] = None,
    entity_id: str = "",
    graph_filename: str = "",
    limit: int = 200,
) -> List[Dict[str, Any]]:
    target = Path(path) if path is not None else AUDIT_PATH
    if not target.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entity_id and item.get("entity_id") != entity_id:
                continue
            if graph_filename and item.get("graph_filename") != graph_filename:
                continue
            rows.append(item)
    return rows[-max(1, int(limit)):][::-1]
