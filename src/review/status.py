"""Review status vocabularies and transition rules."""
from __future__ import annotations

from typing import Dict, Tuple

GRAPH_STATUSES = ("draft", "active", "rejected", "retired")
GRAPH_ACTIONS = ("approve", "reject", "retire", "restore")

GRAPH_TRANSITIONS: Dict[Tuple[str, str], str] = {
    ("draft", "approve"): "active",
    ("draft", "reject"): "rejected",
    ("draft", "retire"): "retired",
    ("active", "retire"): "retired",
    ("rejected", "restore"): "draft",
    ("retired", "restore"): "draft",
}

RULE_STATUSES = ("draft", "testing", "active", "blocked", "rejected", "retired")
RULE_ACTIONS = ("approve", "reject", "retire", "restore")

RULE_TRANSITIONS: Dict[Tuple[str, str], str] = {
    ("draft", "approve"): "active",
    ("draft", "reject"): "rejected",
    ("testing", "approve"): "active",
    ("testing", "reject"): "rejected",
    ("blocked", "approve"): "active",
    ("blocked", "reject"): "rejected",
    ("blocked", "retire"): "retired",
    ("active", "retire"): "retired",
    ("active", "block"): "blocked",
    ("rejected", "restore"): "draft",
    ("retired", "restore"): "draft",
}


class StatusTransitionError(ValueError):
    """Raised when an action is not allowed for the current status."""


def next_graph_status(current: str, action: str) -> str:
    current = "draft" if current == "stale" else current
    key = (str(current or "draft"), str(action or ""))
    if key not in GRAPH_TRANSITIONS:
        raise StatusTransitionError(
            f"图谱条目状态 {key[0]!r} 不支持动作 {key[1]!r}"
        )
    return GRAPH_TRANSITIONS[key]


def next_rule_status(current: str, action: str) -> str:
    key = (str(current or "draft"), str(action or ""))
    if key not in RULE_TRANSITIONS:
        raise StatusTransitionError(
            f"规则状态 {key[0]!r} 不支持动作 {key[1]!r}"
        )
    return RULE_TRANSITIONS[key]


def graph_status_usable(status: str) -> bool:
    return str(status or "draft") == "active"


def normalize_legacy_review_statuses(graph):
    """Legacy stale content becomes a draft; never infer approval from old metadata."""
    changed = 0
    for collection in ("nodes", "relations", "clinical_bases", "standard_candidates"):
        rows = graph.get(collection) or []
        if isinstance(rows, dict): rows = rows.values()
        for item in rows:
            for field in ("review_status", "status"):
                if item.get(field) == "stale":
                    item[field] = "draft"
                    changed += 1
    return changed
