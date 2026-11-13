"""Terminology candidates and hospital-local mapping workflow."""
from __future__ import annotations

import json
import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.extraction.terminology_resolver import UniversalTermResolver
from src.models.patient import PatientSnapshot
from src.terminology.mapper import TerminologyMapper
from src.terminology.repository import (
    HospitalTerminologyMappingRepository,
    TerminologyCandidateRepository,
)
from src.vocab.entity_vocabulary import EntityVocabularyRepository
from src.vocab.reference_vocabulary import ReferenceVocabularyRepository
from src.vocab.reference_vocabulary import (
    ReferenceBundleRepository,
    import_reference_catalog,
    parse_reference_catalog,
    validate_reference_entries,
)


def _reference(directory: Path) -> ReferenceVocabularyRepository:
    directory.mkdir(parents=True)
    (directory / "icd10.json").write_text(json.dumps({
        "entries": [
            {
                "code": "I10",
                "name": "原发性高血压",
                "aliases": ["高血压"],
                "domain": "western",
                "standard_system": "ICD-10",
                "standard_code": "I10",
                "source": "ICD-10",
            },
            {
                "code": "I11.9",
                "name": "高血压性心脏病",
                "aliases": ["高心病"],
                "domain": "western",
                "standard_system": "ICD-10",
                "standard_code": "I11.9",
                "source": "ICD-10",
            },
        ]
    }, ensure_ascii=False), encoding="utf-8")
    return ReferenceVocabularyRepository("icd10", directory)


def test_fuzzy_disease_match_enters_review_queue_not_active_dictionary(tmp_path, monkeypatch):
    active_dir = tmp_path / "entities"
    active_dir.mkdir()
    (active_dir / "disease.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        "src.extraction.terminology_resolver.EntityVocabularyRepository",
        lambda entity_type: EntityVocabularyRepository(entity_type, active_dir),
    )

    candidates = TerminologyCandidateRepository(tmp_path / "candidates.json")
    resolver = UniversalTermResolver(
        auto_import_disease=False,
        candidate_repository=candidates,
    )
    resolver.reference = _reference(tmp_path / "reference")
    resolver.reference_available = True

    result = resolver.resolve("disease", "高血压心脏损害", doc_id="DOC-1")

    assert result["code"].startswith("LOCAL-DIS-")
    assert result["standard_system"] == "LOCAL"
    queued = candidates.list(status="pending")
    assert len(queued) == 1
    assert queued[0]["match_type"] == "ambiguous"
    assert {item["code"] for item in queued[0]["suggested_mappings"]} == {"I10"}
    assert EntityVocabularyRepository("disease", active_dir).list() == []


def test_exact_reference_match_is_queued_until_review(tmp_path, monkeypatch):
    active_dir = tmp_path / "entities"
    active_dir.mkdir()
    (active_dir / "disease.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        "src.extraction.terminology_resolver.EntityVocabularyRepository",
        lambda entity_type: EntityVocabularyRepository(entity_type, active_dir),
    )
    candidates = TerminologyCandidateRepository(tmp_path / "candidates.json")
    resolver = UniversalTermResolver(False, candidates)
    resolver.reference = _reference(tmp_path / "reference")
    resolver.reference_available = True

    result = resolver.resolve("disease", "高血压", doc_id="DOC-2")

    assert result["code"] == "I10"
    assert result["standard_code"] == "I10"
    assert resolver.resolved_terms[-1]["resolution"] == "reference_exact"
    assert candidates.list(status="pending")[0]["match_type"] == "exact_reference"


def test_hospital_disease_mapping_only_uses_approved_records(tmp_path):
    path = tmp_path / "hospital_mappings.json"
    repository = HospitalTerminologyMappingRepository(path)
    repository.save({
        "hospital_id": "H1",
        "entity_type": "disease",
        "local_code": "DX001",
        "local_name": "高血压",
        "canonical_code": "I10",
        "status": "approved",
    })
    repository.save({
        "hospital_id": "H1",
        "entity_type": "disease",
        "local_code": "DX002",
        "canonical_code": "I11.9",
        "status": "draft",
    })

    mapper = TerminologyMapper(str(path))
    patient = PatientSnapshot(patient_id="P1", hospital_id="H1", past_history=["DX001", "DX002"])
    mapper.normalize_patient(patient)

    assert patient.past_history == ["I10", "DX002"]


def test_legacy_mapping_format_supports_diseases(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({
        "labs": {"H1": {"L1": "LAB-1"}},
        "diseases": {"H1": {"D1": "I10"}},
    }), encoding="utf-8")
    mapper = TerminologyMapper(str(path))
    assert mapper.map_lab_code("H1", "L1") == "LAB-1"
    assert mapper.map_disease_code("H1", "D1") == "I10"
    assert mapper.map_disease_code("H1", "UNKNOWN") == "UNKNOWN"


def test_authorized_csv_catalog_can_be_validated_then_merged(tmp_path):
    reference = _reference(tmp_path / "reference")
    content = (
        "疾病编码,疾病名称,别名,专科\n"
        "J18.9,肺炎，未特指,肺部感染|社区获得性肺炎,呼吸内科\n"
        "I10,原发性高血压,高血压|高血压病,心血管内科\n"
    ).encode("utf-8")
    metadata, rows = parse_reference_catalog(content, "authorized.csv")
    assert metadata == {}

    preview = import_reference_catalog(
        reference,
        rows,
        version="国家临床版测试版本",
        source="授权测试文件",
        dry_run=True,
    )
    assert preview["valid"]
    assert preview["added_count"] == 1
    assert preview["updated_count"] == 1
    assert reference.find("J18.9") is None

    result = import_reference_catalog(
        reference,
        rows,
        version="国家临床版测试版本",
        source="授权测试文件",
        dry_run=False,
    )
    assert result["result_count"] == 3
    assert reference.find("J18.9")["specialty"] == "呼吸内科"
    assert reference.metadata()["source"] == "授权测试文件"


def test_catalog_validation_rejects_duplicate_and_invalid_codes():
    report = validate_reference_entries([
        {"code": "I10", "name": "原发性高血压"},
        {"code": "I10", "name": "高血压"},
        {"code": "INVALID", "name": "错误疾病"},
    ])
    assert not report["valid"]
    assert len(report["entries"]) == 1
    assert {item["message"] for item in report["errors"]} == {
        "文件内疾病编码重复",
        "疾病编码不是有效 ICD-10 格式",
    }


def test_specialty_bundle_activation_imports_only_missing_entries(tmp_path, monkeypatch):
    from src.api.routes import vocab

    reference = _reference(tmp_path / "reference")
    bundle_path = tmp_path / "bundles.json"
    bundle_path.write_text(json.dumps([{
        "bundle_id": "CARDIO",
        "name": "心血管疾病包",
        "specialty": "心血管内科",
        "code_prefixes": ["I"],
    }], ensure_ascii=False), encoding="utf-8")
    active_dir = tmp_path / "entities"
    active_dir.mkdir()
    (active_dir / "disease.json").write_text(json.dumps([{
        "code": "I10",
        "name": "原发性高血压",
        "aliases": ["高血压"],
        "domain": "western",
        "standard_system": "ICD-10",
        "standard_code": "I10",
        "source": "ICD-10",
    }], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(vocab, "icd10_reference", reference)
    monkeypatch.setattr(vocab, "icd10_bundles", ReferenceBundleRepository(bundle_path))
    monkeypatch.setattr(
        vocab,
        "EntityVocabularyRepository",
        lambda entity_type: EntityVocabularyRepository(entity_type, active_dir),
    )

    preview = asyncio.run(vocab.list_icd10_bundles())
    assert preview[0]["reference_count"] == 2
    assert preview[0]["active_count"] == 1
    assert preview[0]["available_count"] == 1

    result = asyncio.run(vocab.activate_icd10_bundle("CARDIO"))
    assert result["imported_count"] == 1
    assert result["skipped_count"] == 1
    imported = EntityVocabularyRepository("disease", active_dir).find("I11.9")
    assert imported is not None
    assert imported.specialty == "心血管内科"
