"""Typed reference catalogue import and activation remain separate from extraction."""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from src.api.routes import vocab
from src.vocab.entity_vocabulary import EntityVocabularyRepository
from src.vocab.reference_vocabulary import REFERENCE_CATALOGS, ReferenceVocabularyRepository, import_reference_catalog

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    references = tmp_path / "references"
    references.mkdir()
    active = tmp_path / "entities"
    active.mkdir()
    original_repository = vocab._catalog_repository
    for catalog in REFERENCE_CATALOGS:
        (references / f"{catalog}.json").write_bytes(original_repository(catalog).path.read_bytes())
    monkeypatch.setattr(vocab, "_catalog_repository", lambda catalog: ReferenceVocabularyRepository(catalog, references) if catalog in REFERENCE_CATALOGS else original_repository(catalog))
    monkeypatch.setattr(vocab, "EntityVocabularyRepository", lambda entity_type: EntityVocabularyRepository(entity_type, active))
    return references, active

@pytest.mark.parametrize("catalog", REFERENCE_CATALOGS)
def test_search_and_activation_target_correct_dictionary(catalog, isolated):
    references, active = isolated
    result = asyncio.run(vocab.search_reference_catalog(catalog))
    assert result["items"]
    entry = result["items"][0]
    assert entry["source"].startswith("https://")
    assert not entry["in_active_dictionary"]
    # Search is read-only with respect to active entries.
    target = active / (REFERENCE_CATALOGS[catalog]["entity_type"] + ".json")
    assert not target.exists()
    activated = asyncio.run(vocab.activate_reference_catalog(catalog, {"codes": [entry["code"], entry["code"], "missing"]}))
    assert activated["imported_count"] == 1
    assert activated["skipped_count"] == 2
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved[0]["standard_code"] == entry["standard_code"]
    assert saved[0]["source"] == entry["source"]
    again = asyncio.run(vocab.search_reference_catalog(catalog))
    assert again["items"][0]["in_active_dictionary"]


def test_existing_local_code_and_mapping_not_overwritten(isolated):
    _, active = isolated
    repo = EntityVocabularyRepository("lab", active)
    from src.extraction.models import TemplateEntityEntry
    repo.save(TemplateEntityEntry(code="LOCAL_HGB", name="院内血红蛋白", standard_system="LOINC", standard_code="718-7"), is_new=True)
    result = asyncio.run(vocab.activate_reference_catalog("lab", {"codes": ["718-7"]}))
    assert result["imported_count"] == 0
    assert [entry.code for entry in repo.list()] == ["LOCAL_HGB"]


@pytest.mark.parametrize("catalog,code", [("exam", "EXAM_NEW"), ("lab", "12345-6"), ("medication", "A01AA01"), ("surgery", "99.99")])
def test_file_merge_and_dry_run(catalog, code, isolated):
    references, active = isolated
    repo = ReferenceVocabularyRepository(catalog, references)
    before = repo.path.read_bytes()
    rows = [{"code": code, "name": "测试条目", "aliases": ["测试"], "attributes": {"test": True}}]
    dry = import_reference_catalog(repo, rows, dry_run=True)
    assert dry["valid"] and repo.path.read_bytes() == before
    merged = import_reference_catalog(repo, rows, dry_run=False, source="test-source", version="test-v1")
    assert merged["valid"]
    assert repo.find(code)["attributes"] == {"test": True}
    assert repo.metadata()["standard"] == REFERENCE_CATALOGS[catalog]["standard"]
    assert list(active.iterdir()) == []


def test_wrong_category_and_invalid_rows_do_not_write(isolated):
    references, _ = isolated
    repo = ReferenceVocabularyRepository("lab", references)
    before = repo.path.read_bytes()
    with pytest.raises(ValueError, match="实体类型"):
        import_reference_catalog(repo, [], metadata={"entity_type": "medication"}, dry_run=False)
    report = import_reference_catalog(repo, [{"code": "../x", "name": "bad"}], dry_run=False)
    assert not report["valid"]
    assert repo.path.read_bytes() == before


def test_unknown_category_rejected():
    with pytest.raises(HTTPException) as error:
        vocab._catalog_repository("../../x")
    assert error.value.status_code == 404


def test_upload_requires_provenance_before_writing(isolated):
    from io import BytesIO
    from fastapi import UploadFile
    references, _ = isolated
    path = references / "lab.json"
    before = path.read_bytes()
    content = json.dumps([{"code": "LOCAL_TEST", "name": "本地测试"}]).encode()
    with pytest.raises(HTTPException) as error:
        asyncio.run(vocab.upload_reference_catalog("lab", UploadFile(filename="test.json", file=BytesIO(content)),
            version="", source="", mode="merge", dry_run=False))
    assert error.value.status_code == 422
    assert path.read_bytes() == before
    report = asyncio.run(vocab.upload_reference_catalog("lab", UploadFile(filename="test.json", file=BytesIO(content)),
        version="test-v1", source="test-source", mode="merge", dry_run=False))
    assert report["valid"] and report["result_count"] == 7
