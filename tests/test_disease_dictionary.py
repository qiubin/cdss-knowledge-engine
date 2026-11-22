"""Shared disease dictionary contract: all entries follow ICD-10."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi import HTTPException

from src.extraction.models import TemplateEntityEntry
from src.extraction.repository import ExtractionTemplateRepository
from src.api.routes.vocab import _validate_entry
from src.vocab.entity_vocabulary import resolve_template

DISEASE_PATH = project_root / "data" / "vocab" / "entities" / "disease.json"
LEGACY_PATH = project_root / "data" / "vocab" / "diseases.json"
ICD10_PATTERN = re.compile(r"^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$")


def disease_entries():
    return json.loads(DISEASE_PATH.read_text(encoding="utf-8"))


def test_disease_dictionary_is_pure_icd10():
    entries = disease_entries()
    assert entries, "疾病实体字典不应为空"
    assert len(entries) >= 14
    codes = [str(item["code"]) for item in entries]
    assert len(codes) == len(set(codes))
    assert "I40.9" in codes, "应包含暴发性/急性心肌炎 ICD-10 编码"
    assert {"I42.1", "I42.2", "I42.9"} <= set(codes), "应包含肥厚型心肌病 ICD-10 编码"
    for item in entries:
        assert ICD10_PATTERN.fullmatch(item["code"]), item["code"]
        assert item["domain"] == "western"
        assert item["standard_system"] == "ICD-10"
        assert item["standard_code"] == item["code"]
        assert item["source"] == "ICD-10"
        assert item["name"].strip()
        assert not item["code"].startswith("TCM_DIS_")


def test_legacy_disease_file_stays_aligned():
    assert json.loads(LEGACY_PATH.read_text(encoding="utf-8")) == disease_entries()


def test_templates_do_not_add_tcm_disease_to_shared_dictionary():
    templates = json.loads(
        (project_root / "data" / "templates" / "extraction_templates.json").read_text(encoding="utf-8")
    )
    assert templates
    for template in templates:
        disease = next(item for item in template["entity_types"] if item["entity_type"] == "disease")
        assert disease.get("vocabulary")
        if template.get("domain") != "tcm":
            assert all(not entry.get("code", "").startswith("TCM_DIS_") for entry in disease.get("entries", []))
        else:
            # 中医病名集中维护在 tcm_disease 字典（GB/T 16751.1 标准名 + 别名），
            # 模板本身不再内嵌词条；解析时经 resolve_template 合并生效。
            assert not disease.get("entries"), "中医模板词条应集中在 tcm_disease 字典"
            resolved = resolve_template(
                ExtractionTemplateRepository().get(template["template_id"])
            )
            entries = next(
                item for item in resolved.entity_types if item.entity_type == "disease"
            ).entries
            assert entries, "tcm_disease 字典应提供中医病名"
            owners: dict = {}
            for entry in entries:
                assert entry.code.startswith("TCM_DIS_") and entry.domain == "tcm"
                # 别名必须包含标准名本身：本地正则按别名表匹配，缺名即漏匹配。
                assert entry.name in (entry.aliases or []), entry.name
                # 别名跨条目冲突会破坏解析时的唯一别名归一。
                for term in [entry.name, *(entry.aliases or [])]:
                    assert owners.setdefault(term, entry.code) == entry.code, term


def test_api_rejects_non_icd10_disease_entry():
    entry = TemplateEntityEntry(
        code="TCM_DIS_X", name="中医病", domain="tcm", standard_system="ICD-11 TM1"
    )
    try:
        _validate_entry("disease", entry)
    except HTTPException as error:
        assert "ICD-10" in error.detail
    else:
        raise AssertionError("共享疾病字典应拒绝中医病名")
