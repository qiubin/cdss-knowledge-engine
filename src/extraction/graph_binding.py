"""Resolve the graph file bound to an extraction template.

A template may leave ``graph_filename`` empty (the generic template does).
In that case the historical behaviour fell through to ``stemi_graph.json``,
which silently routed unrelated guidelines into the STEMI graph.  Resolution
must be template-driven: explicit filename first, then a deterministic
``KG-<template_id>.json`` binding.
"""
from __future__ import annotations

import re
from typing import Any


def resolve_template_graph_filename(template: Any) -> str:
    configured = str(getattr(template, "graph_filename", "") or "").strip()
    if configured:
        return configured
    template_id = str(getattr(template, "template_id", "") or "")
    # Legacy compatibility for the retired cardiology template.
    if template_id == "CARDIO_DIAGNOSIS_V1":
        return "stemi_graph.json"
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", template_id).strip("._-")
    return f"KG-{safe_id or 'GENERIC_GUIDELINE_V1'}.json"
