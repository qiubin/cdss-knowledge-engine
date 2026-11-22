"""Shared entity dictionaries referenced by extraction templates.

Templates define the extraction *contract* (entity types, relations,
attributes). The concrete clinical entities (code/name/aliases) live in shared
per-type dictionaries under data/vocab/entities/ so multiple templates reuse
the same terminology instead of each embedding a private copy — the same
separation the concept vocabulary (concepts.json) already provides.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

from fastapi import HTTPException

from ..domains import normalize_domain
from src.extraction.models import ExtractionTemplate, TemplateEntityEntry, TemplateEntityType

ROOT = Path(__file__).parent.parent.parent
ENTITY_VOCAB_DIR = ROOT / "data" / "vocab" / "entities"
KG_DIR = ROOT / "data" / "kg"
TCM_DISEASE_DICTIONARY = "tcm_disease"
TCM_DISEASE_CODE = re.compile(r"TCM_DIS_[A-Z0-9_]+")


def dictionary_type(entity_type: str, domain: str = "") -> str:
    if entity_type == "disease" and normalize_domain(domain) == "tcm":
        return TCM_DISEASE_DICTIONARY
    return entity_type


def tcm_disease_entries() -> dict[str, TemplateEntityEntry]:
    return {entry.code: entry for entry in EntityVocabularyRepository(TCM_DISEASE_DICTIONARY).list()}


def unregistered_tcm_diseases(entities, registered=None) -> list[str]:
    codes = tcm_disease_entries() if registered is None else registered
    return sorted({
        f"{item.get('name') or '未命名'}（{item.get('code') or '无码'}）"
        for item in entities
        if item.get("entity_type") == "disease" and item.get("code") not in codes
    })


class EntityVocabularyRepository:
    """JSON storage for one entity type's shared dictionary."""

    def __init__(self, entity_type: str, directory: Path = ENTITY_VOCAB_DIR):
        self.entity_type = entity_type
        self.directory = directory
        self.path = directory / f"{entity_type}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _concept_repository(self):
        if self.entity_type == "concept" and (self.directory.parent / "concepts.json").exists():
            from src.vocab.repository import ConceptVocabularyRepository
            return ConceptVocabularyRepository(self.directory.parent / "concepts.json")
        return None

    def _read(self) -> List[dict]:
        concepts = self._concept_repository()
        if concepts is not None:
            return [{"code": item.code, "name": item.name_cn, "aliases": item.aliases,
                     "domain": item.domain} for item in concepts.list() if item.is_active]
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []

    def _write(self, items: List[dict]) -> None:
        self.path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list(self) -> List[TemplateEntityEntry]:
        return [TemplateEntityEntry(**item) for item in self._read()]

    def find(self, code: str) -> Optional[TemplateEntityEntry]:
        item = next((raw for raw in self._read() if raw.get("code") == code), None)
        return TemplateEntityEntry(**item) if item else None

    def save(self, entry: TemplateEntityEntry, is_new: bool = False) -> TemplateEntityEntry:
        if self.entity_type == TCM_DISEASE_DICTIONARY:
            systems = [entry.standard_system, *(item.get("system") for item in entry.standard_mappings)]
            if (not TCM_DISEASE_CODE.fullmatch(entry.code)
                    or entry.domain not in {"", "tcm"}
                    or any(str(system or "").strip().upper().startswith(("ICD-10", "ICD10")) for system in systems)):
                raise HTTPException(422, "中医疾病请使用 TCM_DIS_ 本地稳定编码；ICD-10 仅用于西医疾病对照")
            entry = entry.model_copy(update={"domain": "tcm"})
        concepts = self._concept_repository()
        if concepts is not None:
            concept = concepts.get(entry.code)
            concepts.save(concept.model_copy(update={"name_cn": entry.name, "aliases": entry.aliases}))
            return entry
        items = self._read()
        index = next((i for i, item in enumerate(items) if item.get("code") == entry.code), -1)
        if is_new and index >= 0:
            raise HTTPException(409, f"共享词表 {self.entity_type} 中编码 {entry.code} 已存在")
        if index == -1 and not is_new:
            raise HTTPException(404, f"共享词表 {self.entity_type} 中不存在编码 {entry.code}")
        payload = entry.model_dump()
        if index >= 0:
            items[index] = payload
        else:
            items.append(payload)
        self._write(items)
        return entry

    def delete(self, code: str) -> dict:
        concepts = self._concept_repository()
        if concepts is not None:
            return concepts.delete(code)
        items = self._read()
        remaining = [item for item in items if item.get("code") != code]
        if len(remaining) == len(items):
            raise HTTPException(404, f"共享词表 {self.entity_type} 中不存在编码 {code}")
        self._write(remaining)
        return {"deleted": code}

    def remap_code(self, old_code: str, new_code: str) -> dict:
        if self.entity_type == TCM_DISEASE_DICTIONARY:
            raise HTTPException(422, "中医病名使用稳定编码；请新增规范词条并审核迁移")
        if self._concept_repository() is not None:
            raise HTTPException(422, "抽象概念编码不可直接修改，请新增概念并迁移引用")
        items = self._read()
        if not any(item.get("code") == old_code for item in items):
            raise HTTPException(404, f"共享词表 {self.entity_type} 中不存在编码 {old_code}")
        if any(item.get("code") == new_code for item in items):
            raise HTTPException(409, f"共享词表 {self.entity_type} 中编码 {new_code} 已存在")
        for item in items:
            if item.get("code") == old_code:
                item["code"] = new_code
                if item.get("standard_code") == old_code:
                    item["standard_code"] = new_code
        self._write(items)
        return {"old_code": old_code, "new_code": new_code}


def merged_entries(
    type_def: TemplateEntityType,
    directory: Path = ENTITY_VOCAB_DIR,
    template_domain: str = "",
) -> List[TemplateEntityEntry]:
    """Effective entries: template-local entries override shared ones by code.

    Shared entries may declare a ``domain`` (western / tcm).  Domain-specific
    entries are only merged into templates of the same discipline. The shared
    disease file is ICD-10 western only. TCM disease entries use their own
    dictionary as the authoritative source.
    """
    dictionary = dictionary_type(type_def.entity_type, template_domain)
    if dictionary == TCM_DISEASE_DICTIONARY:
        return EntityVocabularyRepository(dictionary, directory).list()
    entries = list(type_def.entries)
    if not type_def.vocabulary:
        return entries
    known = {entry.code for entry in entries}
    domain = normalize_domain(template_domain) if template_domain else ""
    shared = EntityVocabularyRepository(type_def.entity_type, directory).list()
    for entry in shared:
        if entry.code in known:
            continue
        entry_domain = str(entry.domain or "").strip().lower()
        # Keep the shared disease dictionary from reaching TCM templates.
        # A blank domain is rejected because it would otherwise leak across
        # disciplines.
        if type_def.entity_type == "disease" and not entry_domain:
            continue
        if domain and entry_domain and entry_domain not in {"shared", "common", domain}:
            continue
        entries.append(entry)
    return entries


def template_private_entries(
    type_def: TemplateEntityType,
    directory: Path = ENTITY_VOCAB_DIR,
    template_domain: str = "",
) -> List[TemplateEntityEntry]:
    """Return template-local entries that are not owned by the shared dictionary.

    A template may carry local entries even when its entity type also references
    a shared vocabulary (for example legacy or discipline-specific entries).
    The structure-management UI only needs the genuinely private subset; codes
    already present in the shared dictionary must be maintained centrally.
    """
    dictionary = dictionary_type(type_def.entity_type, template_domain)
    if dictionary == TCM_DISEASE_DICTIONARY:
        return []
    shared_codes = {
        entry.code
        for entry in EntityVocabularyRepository(dictionary, directory).list()
    }
    return [entry for entry in type_def.entries if entry.code not in shared_codes]


def resolve_template(
    template: ExtractionTemplate,
    directory: Path = ENTITY_VOCAB_DIR,
) -> ExtractionTemplate:
    """Return a read-only projection with each entity type's effective entries.

    Shared dictionary entries are merged in for types that reference one.
    Never save a resolved template back to the repository — that would copy
    the shared dictionary into every template file.
    """
    resolved = template.model_copy(deep=True)
    for index, item in enumerate(resolved.entity_types):
        resolved.entity_types[index].entries = merged_entries(
            item, directory, resolved.domain
        )
    return resolved


def find_shared_entry(
    entity_type: str,
    code: str,
    directory: Path = ENTITY_VOCAB_DIR,
) -> Optional[TemplateEntityEntry]:
    """Look up a display name in the shared dictionary for one entity type."""
    if not code:
        return None
    if entity_type == "disease" and TCM_DISEASE_CODE.fullmatch(code):
        entity_type = TCM_DISEASE_DICTIONARY
    return EntityVocabularyRepository(entity_type, directory).find(code)


def graph_usage(entity_type: str, code: str, kg_dir: Path = KG_DIR) -> List[str]:
    """Graph node ids currently referencing this shared entry, if any."""
    node_id = f"ENTITY:{entity_type}:{code}"
    references: List[str] = []
    for path in sorted(kg_dir.glob("*.json")):
        try:
            graph = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        nodes = graph.get("nodes") or []
        if isinstance(nodes, dict):
            nodes = list(nodes.values())
        if any(item.get("id") == node_id for item in nodes):
            references.append(path.name)
    return references
