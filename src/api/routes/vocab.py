"""Concept vocabulary and shared entity dictionary APIs."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from src.domains import normalize_domain
from src.extraction.models import TemplateEntityEntry
from src.kg.schema import ConceptDefinition
from src.vocab.entity_vocabulary import EntityVocabularyRepository, graph_usage
from src.vocab.reference_vocabulary import ReferenceVocabularyRepository
from src.vocab.repository import ConceptVocabularyRepository

router = APIRouter(prefix="/api/vocab", tags=["concept-vocabulary"])
vocabulary = ConceptVocabularyRepository()

ROOT = Path(__file__).parent.parent.parent.parent
ENTITY_VOCAB_DIR = ROOT / "data" / "vocab" / "entities"
TEMPLATES_PATH = ROOT / "data" / "templates" / "extraction_templates.json"


def _template_references() -> Dict[str, List[Dict[str, str]]]:
    """Map each dictionary entity_type to the templates referencing it."""
    references: Dict[str, List[Dict[str, str]]] = {}
    if not TEMPLATES_PATH.exists():
        return references
    for template in json.loads(TEMPLATES_PATH.read_text(encoding="utf-8")):
        for type_def in template.get("entity_types", []):
            if type_def.get("vocabulary"):
                references.setdefault(str(type_def.get("entity_type")), []).append({
                    "template_id": str(template.get("template_id", "")),
                    "name": str(template.get("name", "")),
                    "status": str(template.get("status", "active")),
                })
    return references


ICD10_CODE_PATTERN = re.compile(r"^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$")


def _parse_entry(raw: Dict, code: str = "", is_new: bool = False) -> TemplateEntityEntry:
    if code and raw.get("code") != code:
        raise HTTPException(400, "请求路径与实体编码不一致；修改编码请新建实体")
    try:
        return TemplateEntityEntry(**raw)
    except ValidationError as error:
        raise HTTPException(422, error.errors())


def _validate_entry(entity_type: str, entry: TemplateEntityEntry) -> TemplateEntityEntry:
    """Normalize and enforce the shared disease dictionary contract.

    The shared disease dictionary is maintained only as ICD-10 western
    diseases. TCM diseases stay as template-local entries, so they cannot be
    created here or leak into every western guideline through shared merging.
    """
    if entity_type != "disease":
        return entry

    domain = str(entry.domain or "").strip().lower()
    system = str(entry.standard_system or "").strip()
    if domain not in {"", "western"} or system.upper() not in {"", "ICD-10", "ICD-10-CM"}:
        raise HTTPException(
            422,
            "共享疾病实体字典只能维护 ICD-10 西医疾病；中医病名请保留在中医模板本地条目中",
        )
    if not ICD10_CODE_PATTERN.fullmatch(entry.code):
        raise HTTPException(422, f"疾病编码必须是 ICD-10 编码: {entry.code}")

    standard_code = str(entry.standard_code or "").strip()
    if standard_code and standard_code != entry.code:
        raise HTTPException(422, "标准编码必须与 ICD-10 疾病编码一致")
    return entry.model_copy(update={
        "domain": "western",
        "standard_system": "ICD-10",
        "standard_code": entry.code,
        "source": str(entry.source or "ICD-10"),
    })


icd10_reference = ReferenceVocabularyRepository("icd10")


@router.get("/reference/icd10")
async def search_icd10_reference(q: str = "", limit: int = 50):
    """Search the read-only ICD-10 catalogue and mark active-dictionary hits."""
    entries = icd10_reference.search(q, limit)
    active_codes = {item.code for item in EntityVocabularyRepository("disease").list()}
    items = []
    for item in entries:
        code = str(item.get("code", ""))
        normalized = TemplateEntityEntry(**item)
        items.append({
            **normalized.model_dump(),
            "in_active_dictionary": code in active_codes,
        })
    return {
        **icd10_reference.metadata(),
        "items": items,
    }


@router.post("/reference/icd10/import")
async def import_icd10_reference(payload: Dict):
    """Copy explicitly selected ICD-10 entries into the active disease dictionary."""
    codes = payload.get("codes")
    if not isinstance(codes, list) or not codes:
        raise HTTPException(400, "请提供要导入的 codes 列表")
    if len(codes) > 200:
        raise HTTPException(400, "单次最多导入 200 条")

    repository = EntityVocabularyRepository("disease")
    imported: List[dict] = []
    skipped: List[dict] = []
    seen: set[str] = set()
    for raw_code in codes:
        code = str(raw_code or "").strip().upper()
        if code in seen:
            skipped.append({"code": code, "reason": "请求中重复"})
            continue
        seen.add(code)
        reference = icd10_reference.find(code)
        if reference is None:
            skipped.append({"code": code, "reason": "标准参考库中不存在"})
            continue
        if repository.find(code):
            skipped.append({"code": code, "reason": "活跃字典中已存在"})
            continue
        try:
            parsed = _validate_entry("disease", TemplateEntityEntry(**reference))
            imported.append(repository.save(parsed, is_new=True).model_dump())
        except HTTPException as error:
            skipped.append({"code": code, "reason": str(error.detail)})
    return {
        "imported": imported,
        "skipped": skipped,
        "imported_count": len(imported),
        "skipped_count": len(skipped),
    }


def _check_concept_entry(entity_type: str, code: str) -> None:
    if entity_type != "concept":
        return
    concept = vocabulary.find(code)
    if concept is None:
        raise HTTPException(422, f"概念 {code} 不存在，请先在概念词汇中定义")
    if not concept.is_active:
        raise HTTPException(422, f"概念 {code} 已停用，请先启用")


@router.get("/entities")
async def list_entity_dictionaries():
    """Shared terminology layer: one dictionary per entity type."""
    references = _template_references()
    metadata: Dict[str, Dict[str, Any]] = {}
    meta_path = ENTITY_VOCAB_DIR / "_meta.json"
    if meta_path.exists():
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}
    dictionaries = []
    if ENTITY_VOCAB_DIR.exists():
        for path in sorted(ENTITY_VOCAB_DIR.glob("*.json")):
            if path.stem.startswith("_"):
                continue
            entity_type = path.stem
            entries = EntityVocabularyRepository(entity_type).list()
            meta = metadata.get(entity_type, {})
            dictionaries.append({
                "entity_type": entity_type,
                "label": str(meta.get("label") or entity_type),
                "domain": normalize_domain(meta.get("domain") or ""),
                "color": str(meta.get("color") or ""),
                "entry_count": len(entries),
                "referencing_templates": references.get(entity_type, []),
            })
    for entity_type in references:
        if not any(item["entity_type"] == entity_type for item in dictionaries):
            meta = metadata.get(entity_type, {})
            dictionaries.append({
                "entity_type": entity_type,
                "label": str(meta.get("label") or entity_type),
                "domain": normalize_domain(meta.get("domain") or ""),
                "color": str(meta.get("color") or ""),
                "entry_count": 0,
                "referencing_templates": references[entity_type],
            })
    return dictionaries


@router.get("/entities/{entity_type}")
async def list_entity_entries(entity_type: str):
    return [item.model_dump() for item in EntityVocabularyRepository(entity_type).list()]


@router.post("/entities/{entity_type}")
async def create_entity_entry(entity_type: str, entry: Dict):
    parsed = _validate_entry(entity_type, _parse_entry(entry, is_new=True))
    repository = EntityVocabularyRepository(entity_type)
    if repository.find(parsed.code):
        raise HTTPException(409, f"共享词表 {entity_type} 中编码 {parsed.code} 已存在")
    _check_concept_entry(entity_type, parsed.code)
    return repository.save(parsed, is_new=True).model_dump()


@router.put("/entities/{entity_type}/{code}")
async def update_entity_entry(entity_type: str, code: str, entry: Dict):
    parsed = _validate_entry(entity_type, _parse_entry(entry, code))
    _check_concept_entry(entity_type, parsed.code)
    return EntityVocabularyRepository(entity_type).save(parsed).model_dump()


@router.delete("/entities/{entity_type}/{code}")
async def delete_entity_entry(entity_type: str, code: str):
    repository = EntityVocabularyRepository(entity_type)
    if repository.find(code) is None:
        raise HTTPException(404, f"共享词表 {entity_type} 中不存在编码 {code}")
    referencing = graph_usage(entity_type, code)
    if referencing:
        raise HTTPException(409, f"共享实体 {code} 已被图谱 {'、'.join(referencing)} 引用，不能删除")
    if TEMPLATES_PATH.exists():
        for template in json.loads(TEMPLATES_PATH.read_text(encoding="utf-8")):
            for relation in template.get("relations", []):
                if code in (relation.get("source_code"), relation.get("target_code")):
                    raise HTTPException(409, f"共享实体 {code} 仍被模板 {template.get('name')} 的关系限定引用")
    return repository.delete(code)


def _parse_concept(raw: Dict, code: str = "", is_new: bool = False) -> ConceptDefinition:
    if code and raw.get("code") != code:
        raise HTTPException(400, "请求路径与概念编码不一致；修改编码请新建概念")
    try:
        return ConceptDefinition(**raw)
    except ValidationError as error:
        raise HTTPException(422, error.errors())


@router.get("")
async def list_concepts():
    return [item.model_dump() for item in vocabulary.list()]


@router.post("")
async def create_concept(concept: Dict):
    parsed = _parse_concept(concept, is_new=True)
    return vocabulary.save(parsed, is_new=True).model_dump()


@router.get("/{code}")
async def get_concept(code: str):
    return vocabulary.get(code).model_dump()


@router.put("/{code}")
async def update_concept(code: str, concept: Dict):
    parsed = _parse_concept(concept, code)
    return vocabulary.save(parsed).model_dump()


@router.delete("/{code}")
async def delete_concept(code: str):
    return vocabulary.delete(code)


@router.get("/{code}/usage")
async def get_concept_usage(code: str):
    return vocabulary.usage(code)


