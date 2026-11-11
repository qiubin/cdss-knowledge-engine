"""Dictionary-API cascade wiring: rename cascade, delete protection, code remap.

The shared lab dictionary (data/vocab/entities/lab.json) is real data shared by
the whole suite, so every test that touches it snapshots the original entry and
restores it in a ``finally`` block.  Rules and graphs are redirected to tmp
paths via the monkeypatchable module globals (cascade.RULES_PATH,
cascade.KG_DIR, entity_vocabulary.KG_DIR).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest
from fastapi import HTTPException

from src.api.routes import vocab as vocab_routes
from src.extraction.models import TemplateEntityEntry
from src.terminology.labels import default_labels
from src.vocab import cascade
from src.vocab import entity_vocabulary
from src.vocab.entity_vocabulary import EntityVocabularyRepository

CODE = "CTN"
NEW_CODE = "CTNI-TEST"

LAB_DICT_PATH = project_root / "data" / "vocab" / "entities" / "lab.json"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Redirect rules + graph paths and clear the labels cache per test.

    The shared lab dictionary is real suite-wide data: it is snapshotted here
    and restored byte-for-byte at teardown, on top of the per-test ``finally``
    restores, so a crashing test can never leave it dirty.
    """
    rules_path = tmp_path / "managed_rules.json"
    rules_path.write_text("[]", encoding="utf-8")
    kg_dir = tmp_path / "kg"
    kg_dir.mkdir()
    monkeypatch.setattr(cascade, "RULES_PATH", rules_path)
    monkeypatch.setattr(cascade, "KG_DIR", kg_dir)
    monkeypatch.setattr(entity_vocabulary, "KG_DIR", kg_dir)
    lab_snapshot = LAB_DICT_PATH.read_bytes() if LAB_DICT_PATH.exists() else None
    default_labels.cache_clear()
    yield tmp_path, rules_path
    default_labels.cache_clear()
    if lab_snapshot is not None:
        LAB_DICT_PATH.write_bytes(lab_snapshot)


def _rule_with_ctn():
    return {
        "rule_id": "W-1", "disease_code": "I21.0", "disease_name": "急性心肌梗死",
        "anchor_type": "disease", "status": "draft",
        "conditions": {"type": "lab_concept", "lab_code": "CTN", "concept_code": "ELEVATED"},
        "conclusion": {"diagnosis": "I21.0", "message": ""},
    }


def _ensure_entry(repository: EntityVocabularyRepository):
    """Make sure the CTN fixture entry exists; return the original state."""
    original = repository.find(CODE)
    if original is None:
        repository.save(TemplateEntityEntry(code=CODE, name="心肌肌钙蛋白"), is_new=True)
    return original


def _restore_entry(repository: EntityVocabularyRepository, original):
    """Undo every dictionary mutation a test may have made on lab.json."""
    remapped = repository.find(NEW_CODE)
    if remapped is not None:
        repository.delete(NEW_CODE)
    current = repository.find(CODE)
    if original is None:
        if current is not None:
            repository.delete(CODE)
    elif current is None:
        repository.save(original, is_new=True)
    elif current.name != original.name:
        repository.save(original)


def test_delete_blocked_when_rule_references(isolated):
    tmp_path, rules_path = isolated
    rules_path.write_text(json.dumps([_rule_with_ctn()], ensure_ascii=False), encoding="utf-8")
    repository = EntityVocabularyRepository("lab")
    original = _ensure_entry(repository)
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(vocab_routes.delete_entity_entry("lab", CODE))
        assert exc.value.status_code == 409
        # 409 must come from the rule-reference check, not the graph check
        # (graphs are redirected to an empty tmp dir by the fixture).
        assert "规则" in str(exc.value.detail)
        assert repository.find(CODE) is not None
    finally:
        _restore_entry(repository, original)


def test_remap_endpoint_cascades_rules(isolated):
    tmp_path, rules_path = isolated
    rules_path.write_text(json.dumps([_rule_with_ctn()], ensure_ascii=False), encoding="utf-8")
    repository = EntityVocabularyRepository("lab")
    original = _ensure_entry(repository)
    try:
        result = asyncio.run(
            vocab_routes.remap_entity_code("lab", CODE, {"new_code": NEW_CODE})
        )
        assert result["old_code"] == CODE
        assert result["new_code"] == NEW_CODE
        assert result["rules_updated"] == ["W-1"]
        saved = json.loads(rules_path.read_text(encoding="utf-8"))[0]
        assert saved["conditions"]["lab_code"] == NEW_CODE
        assert repository.find(CODE) is None
        assert repository.find(NEW_CODE) is not None
    finally:
        _restore_entry(repository, original)


def test_rename_via_put_triggers_cascade(isolated):
    tmp_path, rules_path = isolated
    rules_path.write_text(json.dumps([_rule_with_ctn()], ensure_ascii=False), encoding="utf-8")
    repository = EntityVocabularyRepository("lab")
    original = _ensure_entry(repository)
    try:
        updated = asyncio.run(vocab_routes.update_entity_entry(
            "lab", CODE,
            {"code": CODE, "name": "高敏肌钙蛋白", "aliases": [], "domain": "western"},
        ))
        assert updated["name"] == "高敏肌钙蛋白"
        saved = json.loads(rules_path.read_text(encoding="utf-8"))[0]
        assert "高敏肌钙蛋白" in (saved.get("condition_text") or "")
    finally:
        _restore_entry(repository, original)
