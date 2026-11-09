"""Shared entity dictionaries referenced by extraction templates.

Templates define the extraction *contract* (entity types, relations,
attributes). The concrete clinical entities (code/name/aliases) live in shared
per-type dictionaries under data/vocab/entities/ so multiple templates reuse
the same terminology instead of each embedding a private copy — the same
separation the concept vocabulary (concepts.json) already provides.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from fastapi import HTTPException

from ..domains import normalize_domain
from src.extraction.models import ExtractionTemplate, TemplateEntityEntry, TemplateEntityType

ROOT = Path(__file__).parent.parent.parent
ENTITY_VOCAB_DIR = ROOT / "data" / "vocab" / "entities"


class EntityVocabularyRepository:
    """JSON storage for one entity type's shared dictionary."""

    def __init__(self, entity_type: str, directory: Path = ENTITY_VOCAB_DIR):
        self.entity_type = entity_type
        self.directory = directory
        self.path = directory / f"{entity_type}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> List[dict]:
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
        items = self._read()
        remaining = [item for item in items if item.get("code") != code]
        if len(remaining) == len(items):
            raise HTTPException(404, f"共享词表 {self.entity_type} 中不存在编码 {code}")
        self._write(remaining)
        return {"deleted": code}


def merged_entries(
    type_def: TemplateEntityType,
    directory: Path = ENTITY_VOCAB_DIR,
    template_domain: str = "",
) -> List[TemplateEntityEntry]:
    """Effective entries: template-local entries override shared ones by code.

    Shared entries may declare a ``domain`` (western / tcm).  Domain-specific
    entries are only merged into templates of the same discipline. The shared
    disease file is ICD-10 western only; TCM diseases remain template-local.
    """
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
) -> List[TemplateEntityEntry]:
    """Return template-local entries that are not owned by the shared dictionary.

    A template may carry local entries even when its entity type also references
    a shared vocabulary (for example legacy or discipline-specific entries).
    The structure-management UI only needs the genuinely private subset; codes
    already present in the shared dictionary must be maintained centrally.
    """
    shared_codes = {
        entry.code
        for entry in EntityVocabularyRepository(type_def.entity_type, directory).list()
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
    return EntityVocabularyRepository(entity_type, directory).find(code)


def graph_usage(entity_type: str, code: str, kg_dir: Path = ROOT / "data" / "kg") -> List[str]:
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
