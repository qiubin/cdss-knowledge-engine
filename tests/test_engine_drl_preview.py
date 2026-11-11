# tests/test_engine_drl_preview.py
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest
from fastapi import HTTPException

from src.api.routes import engine as engine_routes


LAB_TREE = {
    "type": "group",
    "operator": "AND",
    "children": [
        {"type": "lab_concept", "lab_code": "CTN", "concept_code": "ELEVATED"},
        {"type": "symptom", "symptom_code": "SYM_PAIN"},
    ],
}


def test_preview_compilable_tree_returns_drl():
    payload = engine_routes.DrlPreviewPayload(conditions=LAB_TREE)
    result = engine_routes.drl_preview(payload)
    assert result["compilable"] is True
    assert result["reason"] is None
    assert result["leaf_count"] == 2
    assert 'rule "PREVIEW"' in result["drl"]
    assert "salience" in result["drl"]


def test_preview_weighted_group_not_compilable():
    payload = engine_routes.DrlPreviewPayload(
        conditions={
            "type": "weighted_group",
            "operator": "AND",
            "children": [{"type": "lab_concept", "lab_code": "CTN", "concept_code": "ELEVATED"}],
        }
    )
    result = engine_routes.drl_preview(payload)
    assert result["compilable"] is False
    assert result["drl"] is None
    assert "weighted_group" in result["reason"]
    assert result["leaf_count"] == 0


def test_preview_empty_group_not_compilable():
    payload = engine_routes.DrlPreviewPayload(
        conditions={"type": "group", "operator": "AND", "children": []}
    )
    result = engine_routes.drl_preview(payload)
    assert result["compilable"] is False
    assert "children 为空" in result["reason"]


def test_preview_invalid_tree_raises_422():
    payload = engine_routes.DrlPreviewPayload(conditions={"operator": "AND"})
    with pytest.raises(HTTPException) as exc:
        engine_routes.drl_preview(payload)
    assert exc.value.status_code == 422
    assert "条件树不合法" in str(exc.value.detail)
