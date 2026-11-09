"""Trial ICD-10 reference catalogue and explicit import workflow."""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes import vocab
from src.vocab.entity_vocabulary import EntityVocabularyRepository
from src.vocab.reference_vocabulary import ReferenceVocabularyRepository

REFERENCE_PATH = project_root / "data" / "vocab" / "reference" / "icd10.json"
ACTIVE_DISEASE_PATH = project_root / "data" / "vocab" / "entities" / "disease.json"
ICD10_PATTERN = re.compile(r"^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$")


def reference_repository():
    repository = ReferenceVocabularyRepository("icd10")
    repository.load()
    return repository


def test_reference_catalogue_contains_exactly_200_curated_entries():
    assert REFERENCE_PATH.exists()
    repository = reference_repository()
    entries = repository.entries()
    assert len(entries) == 200

    codes = [str(item.get("code", "")) for item in entries]
    assert len(codes) == len(set(codes))
    for item in entries:
        assert ICD10_PATTERN.fullmatch(item["code"]), item["code"]
        assert item["name"].strip()
        assert isinstance(item.get("aliases"), list)
        assert all(alias.strip() for alias in item.get("aliases", []))
        assert item["domain"] == "western"
        assert item["standard_system"] == "ICD-10"
        assert item["standard_code"] == item["code"]
        assert item["source"] == "ICD-10"
        assert not item["code"].startswith("TCM_DIS_")
    assert "I10" in codes
    assert any(code.startswith("C8") for code in codes)


def test_reference_search_supports_code_name_and_alias():
    repository = reference_repository()
    assert any(item["code"] == "I10" for item in repository.search("I10", 10))
    assert any(item["name"] == "急性心肌梗死" for item in repository.search("急性心肌梗死", 10))
    assert any(item["code"].startswith("C8") for item in repository.search("淋巴瘤", 10))
    assert len(repository.search("", 5)) == 5


def test_reference_catalogue_is_separate_from_active_dictionary():
    active_codes = {
        item.get("code")
        for item in json.loads(ACTIVE_DISEASE_PATH.read_text(encoding="utf-8"))
    }
    assert active_codes, "活跃疾病识别字典不应为空"
    assert len(active_codes) >= 20
    assert {"I42.1", "I42.2", "I42.9"} <= active_codes, "肥厚型心肌病编码应在活跃字典中"


def test_import_endpoint_copies_only_selected_entries_into_active_dictionary(tmp_path, monkeypatch):
    reference_dir = tmp_path / "reference"
    active_dir = tmp_path / "entities"
    reference_dir.mkdir()
    active_dir.mkdir()

    entries = [
        {
            "code": "A09",
            "name": "感染性和未特指原因的胃肠炎和结肠炎",
            "aliases": ["感染性胃肠炎"],
            "domain": "western",
            "standard_system": "ICD-10",
            "standard_code": "A09",
            "source": "ICD-10",
        },
        {
            "code": "I10",
            "name": "原发性高血压",
            "aliases": ["高血压"],
            "domain": "western",
            "standard_system": "ICD-10",
            "standard_code": "I10",
            "source": "ICD-10",
        },
    ]
    reference_path = reference_dir / "icd10.json"
    reference_path.write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")

    active_path = active_dir / "disease.json"
    active_path.write_text(json.dumps([
        {
            "code": "I10",
            "name": "原发性高血压",
            "aliases": ["高血压"],
            "domain": "western",
            "standard_system": "ICD-10",
            "standard_code": "I10",
            "source": "ICD-10",
        }
    ], ensure_ascii=False), encoding="utf-8")

    temporary_reference = ReferenceVocabularyRepository("icd10", reference_dir)
    monkeypatch.setattr(vocab, "icd10_reference", temporary_reference)
    monkeypatch.setattr(
        vocab,
        "EntityVocabularyRepository",
        lambda entity_type, directory=None: EntityVocabularyRepository(entity_type, active_dir),
    )

    result = asyncio.run(vocab.import_icd10_reference({
        "codes": ["a09", "I10", "A09", "NOT-FOUND"],
    }))

    imported = result["imported"]
    skipped = result["skipped"]
    assert result["imported_count"] == 1
    assert result["skipped_count"] == 3
    assert imported[0]["code"] == "A09"
    assert imported[0]["domain"] == "western"
    assert imported[0]["standard_system"] == "ICD-10"
    assert imported[0]["standard_code"] == "A09"
    reasons = {item["code"]: item["reason"] for item in skipped}
    assert reasons["I10"] == "活跃字典中已存在"
    assert reasons["A09"] == "请求中重复"
    assert reasons["NOT-FOUND"] == "标准参考库中不存在"

    saved = json.loads(active_path.read_text(encoding="utf-8"))
    assert [item["code"] for item in saved] == ["I10", "A09"]

