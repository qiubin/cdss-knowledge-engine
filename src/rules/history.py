"""Content snapshots for rule edits (data/rules/history/<rule_id>.jsonl).

Every successful create/update/import-replace/delete/rollback appends the new
content so the timeline reads v1..vN.  Status-only review transitions stay in
the review audit log; this file tracks *content* for restore.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
HISTORY_DIR = ROOT / "data" / "rules" / "history"


def _snapshot_path(rule_id: str, directory: Optional[Path] = None) -> Path:
    target = directory or HISTORY_DIR
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(rule_id))
    return target / f"{safe}.jsonl"


def record_snapshot(
    rule: Dict[str, Any],
    action: str,
    actor: str = "",
    directory: Optional[Path] = None,
) -> Dict[str, Any]:
    rule_id = str(rule.get("rule_id") or "")
    existing = list_snapshots(rule_id, directory)
    entry = {
        "seq": (int(existing[-1]["seq"]) + 1) if existing else 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "actor": actor or "未署名",
        "rule": rule,
    }
    path = _snapshot_path(rule_id, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def list_snapshots(rule_id: str, directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = _snapshot_path(rule_id, directory)
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
    items.sort(key=lambda item: int(item.get("seq") or 0))
    return items


def get_snapshot(rule_id: str, seq: int, directory: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    for item in list_snapshots(rule_id, directory):
        if int(item.get("seq") or 0) == seq:
            return item
    return None
