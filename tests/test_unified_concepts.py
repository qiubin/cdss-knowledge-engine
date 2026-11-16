import json
from pathlib import Path
import pytest
from fastapi import HTTPException
from src.vocab.repository import ConceptVocabularyRepository
from src.vocab.entity_vocabulary import EntityVocabularyRepository

@pytest.fixture
def repos(tmp_path):
    vocab_dir = tmp_path / "vocab"
    entities = vocab_dir / "entities"
    entities.mkdir(parents=True)
    canonical = vocab_dir / "concepts.json"
    canonical.write_text(json.dumps([{"code": "ELEVATED", "name_cn": "升高", "category": "lab_concept", "evaluator_type": "static_threshold"}]), encoding="utf-8")
    legacy = entities / "concept.json"
    legacy.write_text(json.dumps([{"code": "ELEVATED", "name": "增高", "aliases": ["升高", "上升"]}]), encoding="utf-8")
    return ConceptVocabularyRepository(canonical), EntityVocabularyRepository("concept", entities), legacy


def test_legacy_aliases_preserved_without_writes(repos):
    definitions, entities, legacy = repos
    before = legacy.read_bytes()
    assert set(definitions.get("ELEVATED").aliases) == {"增高", "升高", "上升"}
    assert entities.find("ELEVATED").name == "升高"
    assert legacy.read_bytes() == before


def test_definition_edit_updates_extraction_view_and_keeps_explicit_empty_aliases(repos):
    definitions, entities, legacy = repos
    concept = definitions.get("ELEVATED")
    definitions.save(concept.model_copy(update={"name_cn": "升高判定", "aliases": ["新别名"]}))
    assert entities.find("ELEVATED").name == "升高判定"
    assert entities.find("ELEVATED").aliases == ["新别名"]
    definitions.save(definitions.get("ELEVATED").model_copy(update={"aliases": []}))
    assert entities.find("ELEVATED").aliases == []


def test_legacy_entity_edit_preserves_execution_configuration(repos):
    definitions, entities, _ = repos
    entry = entities.find("ELEVATED").model_copy(update={"aliases": ["别名更新"]})
    entities.save(entry)
    assert definitions.get("ELEVATED").aliases == ["别名更新"]
    assert definitions.get("ELEVATED").evaluator_type == "static_threshold"


def test_inactive_concept_removed_from_extraction_view(repos):
    definitions, entities, _ = repos
    definitions.save(definitions.get("ELEVATED").model_copy(update={"is_active": False}))
    assert entities.list() == []
    assert definitions.get("ELEVATED").is_active is False


def test_delete_and_code_remap_do_not_leave_legacy_ghosts(repos):
    definitions, entities, _ = repos
    with pytest.raises(HTTPException):
        entities.remap_code("ELEVATED", "NEW")
    definitions.delete("ELEVATED")
    assert entities.list() == []


def test_legacy_api_payload_without_aliases_preserves_aliases(repos, monkeypatch):
    from src.api.routes import vocab
    definitions, _, _ = repos
    monkeypatch.setattr(vocab, "vocabulary", definitions)
    raw = definitions.get("ELEVATED").model_dump(exclude={"aliases"})
    parsed = vocab._parse_concept(raw, "ELEVATED")
    assert "上升" in parsed.aliases
