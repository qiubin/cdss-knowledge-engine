"""Concept vocabulary and shared entity dictionary APIs."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from src.api.routes.rules import _load_all_rules
from src.domains import normalize_domain
from src.extraction.models import TemplateEntityEntry
from src.kg.schema import ConceptDefinition
from src.terminology.labels import default_labels
from src.terminology.repository import (
    HospitalTerminologyMappingRepository,
    TerminologyCandidateRepository,
)
from src.vocab import cascade as vocab_cascade
from src.vocab import entity_vocabulary
from src.vocab.cascade import (
    affected_rule_ids,
    apply_code_remap,
    apply_name_cascade,
    usage_in_graphs,
)
from src.vocab.entity_classification import classify_entries
from src.vocab.entity_vocabulary import EntityVocabularyRepository, graph_usage
from src.vocab.reference_vocabulary import (
    ReferenceBundleRepository,
    ReferenceVocabularyRepository,
    import_reference_catalog,
    parse_reference_catalog,
)
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
term_candidates = TerminologyCandidateRepository()
hospital_mappings = HospitalTerminologyMappingRepository()
icd10_bundles = ReferenceBundleRepository()


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


@router.post("/reference/icd10/catalog/import")
async def upload_icd10_catalog(
    file: UploadFile = File(...),
    version: str = Form(""),
    source: str = Form(""),
    mode: str = Form("merge"),
    dry_run: bool = Form(True),
):
    """Validate and optionally import an authorized ICD-10 JSON/CSV file."""
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "标准术语文件最大 20MB")
    try:
        metadata, entries = parse_reference_catalog(content, file.filename or "")
        if not dry_run and not (str(version or "").strip() or metadata.get("version")):
            raise HTTPException(422, "正式导入必须填写标准版本，或由 JSON 文件提供 version")
        if not dry_run and not (str(source or "").strip() or metadata.get("source")):
            raise HTTPException(422, "正式导入必须填写数据来源，或由 JSON 文件提供 source")
        report = import_reference_catalog(
            icd10_reference,
            entries,
            metadata=metadata,
            version=str(version or "").strip(),
            source=str(source or "").strip(),
            mode=str(mode or "merge").strip(),
            dry_run=bool(dry_run),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise HTTPException(422, str(error))
    if not report.get("valid"):
        return report
    return report


@router.get("/reference/icd10/bundles")
async def list_icd10_bundles():
    """Preview specialty bundles without activating reference entries."""
    catalogue = icd10_reference.entries()
    active_codes = {item.code for item in EntityVocabularyRepository("disease").list()}
    result = []
    for bundle in icd10_bundles.list():
        entries = icd10_bundles.entries(bundle, catalogue)
        codes = {str(item.get("code") or "") for item in entries}
        result.append({
            **bundle,
            "reference_count": len(entries),
            "active_count": len(codes & active_codes),
            "available_count": len(codes - active_codes),
        })
    return result


@router.post("/reference/icd10/bundles/{bundle_id}/activate")
async def activate_icd10_bundle(bundle_id: str):
    """Copy the missing entries in one specialty bundle to the active dictionary."""
    bundle = icd10_bundles.get(bundle_id)
    if bundle is None:
        raise HTTPException(404, f"专科疾病包 {bundle_id} 不存在")
    entries = icd10_bundles.entries(bundle, icd10_reference.entries())
    repository = EntityVocabularyRepository("disease")
    imported: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for reference in entries:
        code = str(reference.get("code") or "")
        if repository.find(code):
            skipped.append({"code": code, "reason": "活跃字典中已存在"})
            continue
        raw = {
            **reference,
            "specialty": str(reference.get("specialty") or bundle.get("specialty") or ""),
        }
        parsed = _validate_entry("disease", TemplateEntityEntry(**raw))
        imported.append(repository.save(parsed, is_new=True).model_dump())
    return {
        "bundle_id": bundle_id,
        "name": bundle.get("name"),
        "imported": imported,
        "skipped": skipped,
        "imported_count": len(imported),
        "skipped_count": len(skipped),
    }


@router.get("/terminology/summary")
async def terminology_summary():
    """Counts for the four terminology-management work areas."""
    active_diseases = EntityVocabularyRepository("disease").list()
    reference_entries = icd10_reference.entries()
    candidates = term_candidates.list()
    mappings = hospital_mappings.list()
    return {
        "active_entities": len(active_diseases),
        "reference_terms": len(reference_entries),
        "pending_candidates": sum(item.get("status") == "pending" for item in candidates),
        "approved_mappings": sum(item.get("status") == "approved" for item in mappings),
    }


@router.get("/terminology/candidates")
async def list_terminology_candidates(
    status: str = "",
    entity_type: str = "",
    q: str = "",
):
    return term_candidates.list(status=status, entity_type=entity_type, query=q)


@router.post("/terminology/candidates/{candidate_id}/review")
async def review_terminology_candidate(candidate_id: str, payload: Dict):
    status = str(payload.get("status") or "").strip()
    note = str(payload.get("note") or "").strip()
    candidate = term_candidates.get(candidate_id)
    if candidate is None:
        raise HTTPException(404, f"候选术语 {candidate_id} 不存在")
    if status == "rejected":
        return term_candidates.review(candidate_id, status, note)
    if status != "approved":
        raise HTTPException(422, "审核状态必须是 approved 或 rejected")
    if candidate.get("entity_type") != "disease":
        raise HTTPException(422, "当前审核入库仅支持疾病术语")

    standard_code = str(payload.get("standard_code") or "").strip().upper()
    if not standard_code:
        suggestions = candidate.get("suggested_mappings") or []
        if len(suggestions) == 1:
            standard_code = str(suggestions[0].get("code") or "").upper()
    reference = icd10_reference.find(standard_code)
    if reference is None:
        raise HTTPException(422, "请选择标准参考库中存在的 ICD-10 编码")

    repository = EntityVocabularyRepository("disease")
    active = repository.find(standard_code)
    if active is None:
        aliases = sorted(set([
            *map(str, reference.get("aliases") or []),
            str(candidate.get("name") or ""),
            *map(str, candidate.get("aliases") or []),
        ]))
        entry = TemplateEntityEntry(**{**reference, "aliases": [item for item in aliases if item]})
        repository.save(_validate_entry("disease", entry), is_new=True)
    return term_candidates.review(candidate_id, "approved", note)


@router.get("/terminology/mappings")
async def list_terminology_mappings(
    hospital_id: str = "",
    entity_type: str = "",
    status: str = "",
):
    return hospital_mappings.list(
        hospital_id=hospital_id,
        entity_type=entity_type,
        status=status,
    )


def _validate_mapping_target(payload: Dict) -> None:
    entity_type = str(payload.get("entity_type") or "").strip()
    canonical_code = str(payload.get("canonical_code") or "").strip()
    if entity_type and canonical_code \
            and EntityVocabularyRepository(entity_type).find(canonical_code) is None:
        raise HTTPException(422, f"活跃 {entity_type} 实体字典中不存在编码 {canonical_code}")


@router.post("/terminology/mappings")
async def create_terminology_mapping(payload: Dict):
    _validate_mapping_target(payload)
    return hospital_mappings.save(payload)


@router.put("/terminology/mappings/{mapping_id}")
async def update_terminology_mapping(mapping_id: str, payload: Dict):
    _validate_mapping_target(payload)
    return hospital_mappings.save(payload, mapping_id=mapping_id)


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
    entries = [item.model_dump() for item in EntityVocabularyRepository(entity_type).list()]
    return classify_entries(entity_type, entries)


@router.post("/entities/{entity_type}")
async def create_entity_entry(entity_type: str, entry: Dict):
    parsed = _validate_entry(entity_type, _parse_entry(entry, is_new=True))
    repository = EntityVocabularyRepository(entity_type)
    if repository.find(parsed.code):
        raise HTTPException(409, f"共享词表 {entity_type} 中编码 {parsed.code} 已存在")
    _check_concept_entry(entity_type, parsed.code)
    saved = repository.save(parsed, is_new=True)
    return classify_entries(entity_type, [saved.model_dump()])[0]


@router.put("/entities/{entity_type}/{code}")
async def update_entity_entry(entity_type: str, code: str, entry: Dict):
    parsed = _validate_entry(entity_type, _parse_entry(entry, code))
    _check_concept_entry(entity_type, parsed.code)
    repository = EntityVocabularyRepository(entity_type)
    previous = repository.find(code)
    saved = repository.save(parsed)
    default_labels.cache_clear()
    if previous is not None and previous.name != saved.name:
        # 字典已保存新名，标签级联基于现名重生成
        apply_name_cascade(entity_type, code, saved.name, rules_path=vocab_cascade.RULES_PATH)
    return classify_entries(entity_type, [saved.model_dump()])[0]


@router.delete("/entities/{entity_type}/{code}")
async def delete_entity_entry(entity_type: str, code: str):
    repository = EntityVocabularyRepository(entity_type)
    if repository.find(code) is None:
        raise HTTPException(404, f"共享词表 {entity_type} 中不存在编码 {code}")
    referencing = graph_usage(entity_type, code, kg_dir=entity_vocabulary.KG_DIR)
    if referencing:
        raise HTTPException(409, f"共享实体 {code} 已被图谱 {'、'.join(referencing)} 引用，不能删除")
    referenced_rules = affected_rule_ids(entity_type, code, _load_all_rules(vocab_cascade.RULES_PATH))
    if referenced_rules:
        raise HTTPException(
            409,
            f"共享实体 {code} 仍被 {len(referenced_rules)} 条规则引用（如 {'、'.join(referenced_rules[:5])}），不能删除",
        )
    if TEMPLATES_PATH.exists():
        for template in json.loads(TEMPLATES_PATH.read_text(encoding="utf-8")):
            for relation in template.get("relations", []):
                if code in (relation.get("source_code"), relation.get("target_code")):
                    raise HTTPException(409, f"共享实体 {code} 仍被模板 {template.get('name')} 的关系限定引用")
    return repository.delete(code)


@router.post("/entities/{entity_type}/{code}/remap")
async def remap_entity_code(entity_type: str, code: str, payload: Dict):
    new_code = str((payload or {}).get("new_code") or "").strip()
    if not new_code or new_code == code:
        raise HTTPException(422, "new_code 必须是与原码不同的非空编码")
    repository = EntityVocabularyRepository(entity_type)
    if repository.find(code) is None:
        raise HTTPException(404, f"共享词表 {entity_type} 中不存在编码 {code}")
    if repository.find(new_code) is not None:
        raise HTTPException(409, f"目标编码 {new_code} 已存在")
    graph_hits = usage_in_graphs(entity_type, code, kg_dir=vocab_cascade.KG_DIR)
    if graph_hits:
        raise HTTPException(
            409,
            f"编码 {code} 仍被图谱 {'、'.join(graph_hits)} 引用；请先在知识审核工作流迁移图谱节点，再改码",
        )
    repository.remap_code(code, new_code)
    default_labels.cache_clear()
    cascade_result = apply_code_remap(entity_type, code, new_code, rules_path=vocab_cascade.RULES_PATH)
    return {
        "entity_type": entity_type,
        "old_code": code,
        "new_code": new_code,
        "rules_updated": cascade_result["updated"],
    }


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


