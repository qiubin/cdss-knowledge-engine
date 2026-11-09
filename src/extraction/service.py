"""Guideline extraction providers driven by managed templates."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import hashlib

import httpx

from ..models.rule import Rule
from ..domains import normalize_domain
from ..domains.western.rule_generation import generate_western_rules
from ..llm import LLMError, chat_json, resolve_api_key
from ..terminology.labels import default_labels
from .graph_binding import resolve_template_graph_filename
from ..vocab.entity_vocabulary import resolve_template
from .disease_discovery import _discovered_disease_entities, _position_for_match
from .models import ExtractionTemplate, TemplateEntityEntry, TemplateRelation


@dataclass
class ProviderResult:
    entities: List[Dict[str, Any]]
    suggested_rules: List[Dict[str, Any]]
    warnings: List[str]
    metadata: Dict[str, Any]
    suggested_relations: List[Dict[str, Any]] = None
    suggested_treatment_plans: List[Dict[str, Any]] = None
    suggested_treatment_rules: List[Dict[str, Any]] = None
    suggested_contraindications: List[Dict[str, Any]] = None

    def __post_init__(self):
        if self.suggested_relations is None:
            self.suggested_relations = []
        if self.suggested_treatment_plans is None:
            self.suggested_treatment_plans = []
        if self.suggested_treatment_rules is None:
            self.suggested_treatment_rules = []
        if self.suggested_contraindications is None:
            self.suggested_contraindications = []


def _entity_index(template: ExtractionTemplate) -> Dict[Tuple[str, str], TemplateEntityEntry]:
    return {
        (item.entity_type, entry.code): entry
        for item in template.entity_types
        for entry in item.entries
    }


def _compiled_alias(alias: str):
    try:
        return re.compile(alias, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(alias), re.IGNORECASE)


def _match_quote(text: str, start: int, end: int) -> str:
    left = max(0, start - 45)
    right = min(len(text), end + 85)
    return re.sub(r"\s+", " ", text[left:right]).strip()


def _local_entities(
    chunks: List[Dict[str, Any]],
    template: ExtractionTemplate,
) -> List[Dict[str, Any]]:
    entities: List[Dict[str, Any]] = []
    seen = set()
    for chunk in chunks:
        split_lines = chunk["text"].split("\n")
        pages = chunk.get("page_numbers") or [chunk.get("page_start", 1)]
        if chunk.get("line_numbers") and len(chunk["line_numbers"]) == len(split_lines):
            lines = chunk["line_numbers"]
        else:
            base_line = int(chunk.get("line_start", 1))
            last_line = int(chunk.get("line_end", base_line))
            lines = [min(base_line + index, last_line) for index in range(len(split_lines))]
        positions: List[Tuple[int, int, str]] = []
        offset = 0
        for text in split_lines:
            positions.append((offset, offset + len(text), text))
            offset += len(text) + 1

        for entity_type in template.entity_types:
            for entry in entity_type.entries:
                matches: List[Tuple[int, int]] = []
                for alias in entry.aliases or [re.escape(entry.name or entry.code)]:
                    pattern = _compiled_alias(alias)
                    found = pattern.search(chunk["text"])
                    if found:
                        matches.append((found.start(), found.end()))
                if not matches:
                    continue
                # One record per chunk occurrence so the review UI and graph
                # import see every piece of evidence, not just the first
                # mention in the whole document. Same dedupe shape as the
                # LLM provider, which also keys on (type, code, chunk_id).
                key = (entity_type.entity_type, entry.code, chunk["chunk_id"])
                if key in seen:
                    continue
                seen.add(key)
                start, end = min(matches)
                page, line = _position_for_match(start, positions, pages, lines, chunk)
                is_concept = entity_type.entity_type == "concept"
                entities.append({
                    "entity_type": entity_type.entity_type,
                    "name": entry.name or entry.code,
                    "code": "" if is_concept else entry.code,
                    "concept_code": entry.code if is_concept else "",
                    "chunk_id": chunk["chunk_id"],
                    "page": int(page),
                    "line_start": int(line),
                    "line_end": int(line),
                    "quote": _match_quote(chunk["text"], start, end),
                    "confidence": min(0.95, 0.65 + 0.03 * len(matches)),
                    "attributes": dict(entry.attributes or {}),
                })

        # Discovery runs after explicit entries and shares the same dedupe set.
        # Disease and TCM syndrome discovery are both type-driven.
        for discovery_type in template.entity_types:
            if discovery_type.discovery:
                entities.extend(
                    _discovered_disease_entities(
                        chunk, discovery_type, seen, positions, pages, lines,
                        template_domain=template.domain,
                    )
                )
    return entities


def relation_pairs_for_chunk(
    chunk_entities: List[Dict[str, Any]],
    template: ExtractionTemplate,
    target_code: str = "",
) -> List[Tuple[TemplateRelation, Dict[str, Any], Dict[str, Any]]]:
    """Pair same-chunk entities according to the template's relation contract.

    Driven entirely by template.relations so custom entity types and relations
    defined in a template flow through to suggestions and graph import. When a
    relation pins source_code/target_code only that entity participates. A
    relation into the concept type with a bound concept_code (e.g.
    FINDING_HAS_CONCEPT → PRESENT) pairs with that synthetic concept. Multiple
    differential diagnoses in one section must not turn the list into
    cross-product facts, so disease sources only pair when unambiguous: one
    disease in the chunk, a pinned code, or a match on target_code.
    """
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for entity in chunk_entities:
        by_type.setdefault(entity.get("entity_type", ""), []).append(entity)

    pairs: List[Tuple[TemplateRelation, Dict[str, Any], Dict[str, Any]]] = []
    for relation in template.relations:
        sources = list(by_type.get(relation.source_entity_type, []))
        if relation.source_code:
            sources = [item for item in sources if item.get("code") == relation.source_code]
        elif relation.source_entity_type == "disease" and len(sources) > 1:
            if target_code:
                sources = [item for item in sources if item.get("code") == target_code]
            else:
                sources = []
        targets: List[Dict[str, Any]]
        if (
            relation.target_entity_type == "concept"
            and relation.concept_code
            and not relation.target_code
        ):
            targets = [{"entity_type": "concept", "code": "", "concept_code": relation.concept_code, "name": ""}]
        else:
            targets = list(by_type.get(relation.target_entity_type, []))
            if relation.target_code:
                targets = [
                    item for item in targets
                    if (item.get("code") or item.get("concept_code")) == relation.target_code
                ]
        for source in sources:
            for target in targets:
                if not source.get("code"):
                    continue
                if not (target.get("code") or target.get("concept_code")):
                    continue
                pairs.append((relation, source, target))
    return pairs


def _local_relations(
    entities: List[Dict[str, Any]],
    template: ExtractionTemplate,
) -> List[Dict[str, Any]]:
    """Create conservative same-chunk relation suggestions for review."""
    relations: List[Dict[str, Any]] = []
    by_chunk: Dict[str, List[Dict[str, Any]]] = {}
    for entity in entities:
        by_chunk.setdefault(entity["chunk_id"], []).append(entity)

    for chunk_id, values in by_chunk.items():
        first = values[0]
        for definition, source, target in relation_pairs_for_chunk(values, template):
            relations.append({
                "relation_code": definition.relation_code,
                "relation_name": definition.name,
                "source_type": source["entity_type"],
                "source_code": source.get("code"),
                "target_type": target["entity_type"],
                "target_code": target.get("code") or "",
                "target_concept_code": target.get("concept_code") or definition.concept_code,
                "chunk_id": chunk_id,
                "page": first["page"],
                "line_start": first["line_start"],
                "line_end": first["line_end"],
                "quote": first["quote"],
                "confidence": 0.7,
            })
    return relations


def _rule_source_from_chunk(
    raw_source: Dict[str, Any],
    chunk_map: Dict[str, Dict[str, Any]],
    doc_id: str,
    doc_title: str,
) -> Dict[str, Any]:
    chunk = chunk_map.get(str(raw_source.get("chunk_id") or ""))
    if not chunk:
        chunk = next(iter(chunk_map.values()), {})
    return {
        "guideline_id": doc_id,
        "guideline_title": doc_title,
        "chunk_id": str(raw_source.get("chunk_id") or chunk.get("chunk_id", "")),
        "section": raw_source.get("section") or chunk.get("section", ""),
        "page_start": raw_source.get("page_start", chunk.get("page_start")),
        "page_end": raw_source.get("page_end", chunk.get("page_end")),
        "line_start": raw_source.get("line_start", chunk.get("line_start")),
        "line_end": raw_source.get("line_end", chunk.get("line_end")),
        "quote": raw_source.get("quote") or chunk.get("text", "")[:220],
    }


def _validate_llm_payload(
    payload: Dict[str, Any],
    chunks: List[Dict[str, Any]],
    template: ExtractionTemplate,
    doc_id: str,
    doc_title: str,
) -> Tuple[
    List[Dict[str, Any]],      # entities
    List[Dict[str, Any]],      # diagnosis rules
    List[Dict[str, Any]],      # relations
    List[str],                 # warnings
    List[Dict[str, Any]],      # treatment plans
    List[Dict[str, Any]],      # treatment rules
    List[Dict[str, Any]],      # contraindications
]:
    warnings: List[str] = []
    page_corrections = 0
    line_corrections = 0
    ignored_relation_unknown_entity: Dict[str, int] = {}
    relation_stubs: Dict[str, int] = {}
    ignored_relation_unknown_chunk = 0
    corrected_relation_chunks = 0
    corrected_plan_chunks = 0
    corrected_contraindication_chunks = 0
    ignored_plan_missing_code = 0
    ignored_contraindication_missing_code = 0
    chunk_map = {chunk["chunk_id"]: chunk for chunk in chunks}

    def _chunk_for(raw: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Resolve a model-provided chunk_id, with a conservative fallback.

        Models occasionally return a pseudo or stale chunk_id even though the
        page/line or quote points at a real chunk.  Preserve the row instead of
        discarding it when the target can be identified unambiguously.
        """
        chunk = chunk_map.get(str(raw.get("chunk_id") or ""))
        if chunk:
            return chunk, False
        try:
            page = int(raw.get("page") or 0)
            line = int(raw.get("line_start") or raw.get("line") or 0)
        except (TypeError, ValueError):
            page, line = 0, 0
        if page:
            for candidate in chunks:
                if candidate["page_start"] <= page <= candidate["page_end"]:
                    if not line or candidate["line_start"] <= line <= candidate["line_end"]:
                        return candidate, True
        quote = str(raw.get("quote") or "").strip()[:30]
        if quote:
            for candidate in chunks:
                if quote in str(candidate.get("text") or ""):
                    return candidate, True
        if len(chunks) == 1:
            return chunks[0], True
        return None, False
    entities: List[Dict[str, Any]] = []
    seen_entities = set()
    entity_index = _entity_index(template)
    attribute_index = {item.attribute_code: item for item in template.attributes}
    relation_index = {item.relation_code: item for item in template.relations}

    for raw in payload.get("entities", []):
        if not isinstance(raw, dict):
            continue
        chunk = chunk_map.get(str(raw.get("chunk_id") or ""))
        if not chunk:
            warnings.append("已忽略引用未知 chunk_id 的实体")
            continue
        entity_type = str(raw.get("entity_type") or "")
        if entity_type not in template.allowed_entity_types:
            warnings.append(f"已忽略模板外实体类型 {entity_type}")
            continue

        code = str(raw.get("concept_code") or raw.get("code") or "")
        entry = entity_index.get((entity_type, code))
        if not entry:
            candidate = next(
                (item for item in template.entity_types if item.entity_type == entity_type),
                None,
            )
            entry = next(
                (
                    item for item in (candidate.entries if candidate else [])
                    if item.name == raw.get("name") or item.name == code
                ),
                None,
            )
            # A discovery-enabled entity type accepts new names from guideline
            # text. Generate a stable candidate code and mark it for review.
            if not entry and candidate and candidate.discovery:
                display_name = str(raw.get("name") or code).strip()
                if display_name:
                    stable_code = code or (
                        "AUTO-" + hashlib.md5(display_name.encode("utf-8")).hexdigest()[:8].upper()
                    )
                    entry = TemplateEntityEntry(code=stable_code, name=display_name)
        if not entry:
            warnings.append(f"已忽略模板外实体 {raw.get('name') or code}")
            continue
        key = (entity_type, entry.code, str(chunk["chunk_id"]))
        if key in seen_entities:
            continue
        seen_entities.add(key)

        page = int(raw.get("page") or chunk.get("page_start") or 1)
        line_start = int(raw.get("line_start") or chunk.get("line_start") or 1)
        line_end = int(raw.get("line_end") or line_start)
        if page < chunk["page_start"] or page > chunk["page_end"]:
            page_corrections += 1
            page = chunk["page_start"]
        if line_start < chunk["line_start"] or line_start > chunk["line_end"]:
            line_corrections += 1
            line_start = chunk["line_start"]
        line_end = max(line_start, min(line_end, chunk["line_end"]))
        try:
            confidence = float(raw.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7

        raw_attributes = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
        attributes: Dict[str, Any] = dict(entry.attributes or {})
        for attribute_code, value in raw_attributes.items():
            definition = attribute_index.get(attribute_code)
            if not definition or definition.owner_entity_type != entity_type:
                warnings.append(f"已忽略模板外属性 {attribute_code}")
                continue
            if definition.data_type == "number":
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    warnings.append(f"属性 {attribute_code} 不是数字，已忽略")
                    continue
            elif definition.data_type == "boolean":
                value = bool(value)
            else:
                value = str(value)
                if definition.data_type == "enum" and definition.allowed_values and value not in definition.allowed_values:
                    warnings.append(f"属性 {attribute_code} 不在允许值内，已忽略")
                    continue
            attributes[attribute_code] = value

        is_concept = entity_type == "concept"
        entities.append({
            "entity_type": entity_type,
            "name": entry.name or entry.code,
            "code": "" if is_concept else entry.code,
            "concept_code": entry.code if is_concept else "",
            "chunk_id": chunk["chunk_id"],
            "page": page,
            "line_start": line_start,
            "line_end": line_end,
            "quote": str(raw.get("quote") or "").strip() or str(chunk.get("text", ""))[:220],
            "confidence": max(0.0, min(1.0, confidence)),
            "attributes": attributes,
        })

    validated_relations: List[Dict[str, Any]] = []
    accepted_entity_keys = set(entity_index.keys()) | {
        (item["entity_type"], item["code"]) for item in entities
    }
    entity_lookup: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for (entity_type, code), entry in entity_index.items():
        entity_lookup[(entity_type, code)] = (entity_type, entry.code)
        if entry.name:
            entity_lookup[(entity_type, entry.name)] = (entity_type, entry.code)
        for alias in entry.aliases:
            entity_lookup[(entity_type, str(alias))] = (entity_type, entry.code)
    for item in entities:
        entity_lookup[(item["entity_type"], item["code"])] = (
            item["entity_type"], item["code"]
        )
        if item.get("name"):
            entity_lookup[(item["entity_type"], item["name"])] = (
                item["entity_type"], item["code"]
            )

    def _relation_endpoint(
        entity_type: str, code: Any, name: Any = ""
    ) -> Tuple[str, str]:
        entity_type = str(entity_type or "")
        for candidate in (code, name):
            candidate = str(candidate or "")
            if not candidate:
                continue
            if entity_type:
                key = (entity_type, candidate)
                if key in entity_lookup:
                    return entity_lookup[key]
            # The model sometimes omits or mislabels the entity type; accept a
            # unique match by code/name across types.
            matches = [key for key in entity_lookup if key[1] == candidate]
            if len(matches) == 1:
                return entity_lookup[matches[0]]
        return (entity_type, str(code or ""))

    def _add_relation_stub(
        entity_type: str,
        code: Any,
        name: Any,
        chunk: Dict[str, Any],
        raw: Dict[str, Any],
    ) -> Optional[Tuple[str, str]]:
        """Keep a relation by materializing an endpoint the model forgot to list.

        Only code-shaped values are accepted; a Chinese name in the code field
        cannot become a stable semantic node code.
        """
        entity_type = str(entity_type or "")
        code = str(code or "").strip()
        if not entity_type or not code or not re.fullmatch(r"[A-Za-z0-9_.:\-]+", code):
            return None
        key = (entity_type, code)
        if key in accepted_entity_keys:
            return key
        labels = default_labels()
        display = str(name or "").strip()
        if not display:
            display = (
                labels.evidence_name(code)
                if entity_type == "diagnostic_evidence"
                else labels.name(entity_type, code)
            )
        display = display or code
        stub = {
            "entity_type": entity_type,
            "name": display,
            "code": "" if entity_type == "concept" else code,
            "concept_code": code if entity_type == "concept" else "",
            "chunk_id": chunk["chunk_id"],
            "page": chunk.get("page_start", 1),
            "line_start": chunk.get("line_start", 1),
            "line_end": chunk.get("line_end", chunk.get("line_start", 1)),
            "quote": str(raw.get("quote") or chunk.get("text", ""))[:220],
            "confidence": 0.5,
            "attributes": {},
            "discovered_from": "relation",
        }
        entities.append(stub)
        accepted_entity_keys.add(key)
        entity_lookup[key] = key
        entity_lookup[(entity_type, display)] = key
        relation_stubs[entity_type] = relation_stubs.get(entity_type, 0) + 1
        return key

    def _relation_fields(raw: Dict[str, Any], prefix: str) -> Tuple[str, str, str]:
        node = raw.get(prefix)
        if not isinstance(node, dict):
            node = raw.get(f"{prefix}_entity")
        if not isinstance(node, dict):
            node = {}
        fallback_name = raw.get(f"{prefix}_entity")
        return (
            str(
                raw.get(f"{prefix}_type")
                or raw.get(f"{prefix}_entity_type")
                or node.get("entity_type")
                or node.get("type")
                or ""
            ),
            str(
                raw.get(f"{prefix}_code")
                or raw.get(f"{prefix}_entity_code")
                or raw.get(f"{prefix}_id")
                or node.get("code")
                or ""
            ),
            str(
                raw.get(f"{prefix}_name")
                or raw.get(f"{prefix}_entity_name")
                or raw.get(f"{prefix}_label")
                or node.get("name")
                or (fallback_name if isinstance(fallback_name, str) else "")
                or ""
            ),
        )

    for raw in payload.get("suggested_relations", []):
        if not isinstance(raw, dict):
            continue
        relation_code = str(raw.get("relation_code") or "")
        definition = relation_index.get(relation_code)
        if not definition:
            warnings.append(f"已忽略模板外关系 {relation_code}")
            continue
        chunk, corrected = _chunk_for(raw)
        if chunk is None:
            ignored_relation_unknown_chunk += 1
            continue
        if corrected:
            corrected_relation_chunks += 1
        source_type, source_code, source_name = _relation_fields(raw, "source")
        target_type, target_code, target_name = _relation_fields(raw, "target")
        source_key = _relation_endpoint(source_type, source_code, source_name)
        target_key = _relation_endpoint(target_type, target_code, target_name)
        if source_key not in accepted_entity_keys:
            source_key = _add_relation_stub(
                definition.source_entity_type, source_code, source_name, chunk, raw
            ) or source_key
        if target_key not in accepted_entity_keys:
            target_key = _add_relation_stub(
                definition.target_entity_type, target_code, target_name, chunk, raw
            ) or target_key
        if source_key not in accepted_entity_keys or target_key not in accepted_entity_keys:
            ignored_relation_unknown_entity[relation_code] = (
                ignored_relation_unknown_entity.get(relation_code, 0) + 1
            )
            continue
        try:
            confidence = float(raw.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        validated_relations.append({
            "relation_code": relation_code,
            "relation_name": definition.name,
            "source_type": source_key[0],
            "source_code": source_key[1],
            "target_type": target_key[0],
            "target_code": target_key[1],
            "target_concept_code": definition.concept_code,
            "chunk_id": chunk["chunk_id"],
            "page": chunk["page_start"],
            "line_start": chunk["line_start"],
            "line_end": chunk["line_end"],
            "quote": str(raw.get("quote") or chunk.get("text", ""))[:220],
            "confidence": max(0.0, min(1.0, confidence)),
        })

    validated_rules: List[Dict[str, Any]] = []
    domain = normalize_domain(template.domain)
    labels = default_labels()
    anchor_type = "syndrome" if domain == "tcm" else "disease"
    anchor_types = {anchor_type}
    disease_codes = {
        entry.code
        for item in template.entity_types if item.entity_type in anchor_types
        for entry in item.entries
    }
    disease_codes.update(
        item["code"] for item in entities
        if item["entity_type"] in anchor_types and item.get("code")
    )
    for index, raw in enumerate(payload.get("suggested_rules", []), start=1):
        if not isinstance(raw, dict):
            continue
        disease_code = str(raw.get("anchor_code") or raw.get("disease_code") or "")
        if disease_code not in disease_codes:
            warnings.append("已忽略目标疾病不在模板中的候选规则")
            continue
        source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
        tags = sorted(set([*(raw.get("tags") or []), "auto-generated", doc_id]))
        normalized = {
            **raw,
            "rule_id": str(raw.get("rule_id") or f"{doc_id}-LLM-{index:03d}"),
            "version": int(raw.get("version") or 1),
            "domain": domain,
            "anchor_type": anchor_type,
            "disease_code": disease_code,
            "disease_name": str(raw.get("anchor_name") or raw.get("disease_name") or ""),
            "status": "draft",
            "graph_filename": resolve_template_graph_filename(template),
            "priority": int(raw.get("priority") or 50),
            "source": _rule_source_from_chunk(source, chunk_map, doc_id, doc_title),
            "tags": [tag for tag in tags if tag],
        }
        conditions = normalized.get("conditions")
        if not isinstance(conditions, dict):
            conditions = {"type": "group", "operator": "AND", "children": []}
            normalized["conditions"] = conditions
        conclusion = normalized.get("conclusion")
        if not isinstance(conclusion, dict):
            conclusion = {}
        conclusion.setdefault("diagnosis", disease_code)
        conclusion.setdefault("confidence", "medium")
        normalized["conclusion"] = conclusion
        labels.annotate(conditions)
        normalized["condition_text"] = labels.summarize(conditions)
        try:
            rule = Rule(**normalized)
            if rule.conclusion.confidence not in {"high", "medium", "low"}:
                rule.conclusion.confidence = "medium"
            rule.conclusion.diagnosis = rule.disease_code
            validated_rules.append(rule.model_dump())
        except Exception as error:
            warnings.append(f"候选规则校验失败: {str(error)[:140]}")
    validated_treatment_plans: List[Dict[str, Any]] = []
    validated_treatment_rules: List[Dict[str, Any]] = []
    validated_contraindications: List[Dict[str, Any]] = []
    for index, raw in enumerate(payload.get("suggested_treatment_plans", []), start=1):
        if not isinstance(raw, dict):
            continue
        plan_code = str(
            raw.get("treatment_plan_code")
            or raw.get("code")
            or raw.get("plan_id")
            or raw.get("treatment_plan_id")
            or ""
        )
        if not plan_code:
            ignored_plan_missing_code += 1
            continue
        chunk, corrected = _chunk_for(raw)
        if chunk is None:
            warnings.append(f"治疗方案 {plan_code} 引用未知 chunk_id，已忽略")
            continue
        if corrected:
            corrected_plan_chunks += 1
        validated_treatment_plans.append({
            "treatment_plan_code": plan_code,
            "treatment_plan_name": str(raw.get("treatment_plan_name") or raw.get("name") or plan_code),
            "priority": str(raw.get("priority") or ""),
            "evidence_level": str(raw.get("evidence_level") or ""),
            "patient_scope": str(raw.get("patient_scope") or ""),
            "time_window": str(raw.get("time_window") or ""),
            "chunk_id": chunk["chunk_id"],
            "page": chunk["page_start"],
            "line_start": chunk["line_start"],
            "line_end": chunk["line_end"],
            "quote": str(raw.get("quote") or chunk.get("text", ""))[:220],
        })
    for index, raw in enumerate(payload.get("suggested_treatment_rules", []), start=1):
        if not isinstance(raw, dict):
            continue
        disease_code = str(raw.get("anchor_code") or raw.get("disease_code") or "")
        if disease_code not in disease_codes:
            warnings.append("已忽略目标疾病不在模板中的候选治疗规则")
            continue
        source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
        tags = sorted(set([*(raw.get("tags") or []), "auto-generated", "treatment", doc_id]))
        normalized = {
            **raw,
            "rule_id": str(raw.get("rule_id") or f"{doc_id}-TX-{index:03d}"),
            "version": int(raw.get("version") or 1),
            "domain": domain,
            "anchor_type": anchor_type,
            "disease_code": disease_code,
            "disease_name": str(raw.get("anchor_name") or raw.get("disease_name") or ""),
            "rule_type": "treatment",
            "status": "draft",
            "graph_filename": resolve_template_graph_filename(template),
            "priority": int(raw.get("priority") or 50),
            "source": _rule_source_from_chunk(source, chunk_map, doc_id, doc_title),
            "tags": [tag for tag in tags if tag],
        }
        conditions = normalized.get("conditions")
        if not isinstance(conditions, dict):
            # Treatment rules are triggered by the matched diagnosis, so an
            # empty condition group is the intended no-op contract.
            conditions = {"type": "group", "operator": "AND", "children": []}
            normalized["conditions"] = conditions
        conclusion = normalized.get("conclusion")
        if not isinstance(conclusion, dict):
            conclusion = {}
        conclusion.setdefault("diagnosis", disease_code)
        conclusion.setdefault("confidence", "medium")
        normalized["conclusion"] = conclusion
        labels.annotate(conditions)
        normalized["condition_text"] = labels.summarize(conditions)
        try:
            rule = Rule(**normalized)
            if rule.conclusion.confidence not in {"high", "medium", "low"}:
                rule.conclusion.confidence = "medium"
            rule.conclusion.diagnosis = rule.disease_code
            validated_treatment_rules.append(rule.model_dump())
        except Exception as error:
            warnings.append(f"候选治疗规则校验失败: {str(error)[:140]}")
    for index, raw in enumerate(payload.get("suggested_contraindications", []), start=1):
        if not isinstance(raw, dict):
            continue
        plan_code = str(
            raw.get("treatment_plan_code")
            or raw.get("plan_id")
            or raw.get("treatment_plan_id")
            or ""
        )
        contraindication_code = str(
            raw.get("contraindication_code")
            or raw.get("code")
            or raw.get("contraindication_id")
            or ""
        )
        if not plan_code or not contraindication_code:
            ignored_contraindication_missing_code += 1
            continue
        chunk, corrected = _chunk_for(raw)
        if chunk is None:
            warnings.append(f"禁忌关系 {plan_code}->{contraindication_code} 引用未知 chunk_id，已忽略")
            continue
        if corrected:
            corrected_contraindication_chunks += 1
        validated_contraindications.append({
            "treatment_plan_code": plan_code,
            "contraindication_code": contraindication_code,
            "contraindication_name": str(raw.get("contraindication_name") or contraindication_code),
            "chunk_id": chunk["chunk_id"],
            "page": chunk["page_start"],
            "line_start": chunk["line_start"],
            "line_end": chunk["line_end"],
            "quote": str(raw.get("quote") or chunk.get("text", ""))[:220],
        })
    if page_corrections or line_corrections:
        parts = []
        if page_corrections:
            parts.append(f"页码 {page_corrections} 处")
        if line_corrections:
            parts.append(f"行号 {line_corrections} 处")
        warnings.append(
            "已校正实体溯源位置（" + "、".join(parts) + "），已对齐到所属片段，不影响入库"
        )
    if corrected_relation_chunks or corrected_plan_chunks or corrected_contraindication_chunks:
        parts = []
        if corrected_relation_chunks:
            parts.append(f"{corrected_relation_chunks} 条关系")
        if corrected_plan_chunks:
            parts.append(f"{corrected_plan_chunks} 个治疗方案")
        if corrected_contraindication_chunks:
            parts.append(f"{corrected_contraindication_chunks} 条禁忌关系")
        warnings.append(
            "已校正 " + "、".join(parts) + " 的片段归属（模型返回了未知 chunk_id），内容已保留"
        )
    if relation_stubs:
        total = sum(relation_stubs.values())
        detail = "、".join(
            f"{entity_type}×{count}"
            for entity_type, count in sorted(relation_stubs.items())
        )
        warnings.append(
            f"已根据关系补全 {total} 个未抽取实体（{detail}），关系已保留，建议核对"
        )
    if ignored_relation_unknown_chunk:
        warnings.append(f"已忽略 {ignored_relation_unknown_chunk} 条引用未知 chunk_id 的关系")
    if ignored_relation_unknown_entity:
        total = sum(ignored_relation_unknown_entity.values())
        detail = "、".join(
            f"{code}×{count}"
            for code, count in sorted(ignored_relation_unknown_entity.items())
        )
        warnings.append(f"已忽略 {total} 条引用未知实体的关系（{detail}）")
    if ignored_plan_missing_code:
        warnings.append(f"已忽略 {ignored_plan_missing_code} 个缺少编码的治疗方案")
    if ignored_contraindication_missing_code:
        warnings.append(
            f"已忽略 {ignored_contraindication_missing_code} 条缺少治疗方案或禁忌编码的禁忌关系"
        )
    return entities, validated_rules, validated_relations, warnings, validated_treatment_plans, validated_treatment_rules, validated_contraindications


def _parse_json_response(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError(
                f"大模型响应不是有效 JSON（{exc.msg}），输出可能被截断"
            ) from exc
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError as inner:
            raise ValueError(
                f"大模型响应 JSON 格式错误（{inner.msg}），输出可能被截断"
            ) from inner
    if not isinstance(value, dict):
        raise ValueError("大模型响应 JSON 根节点必须是对象")
    return value


def _llm_prompt(
    template: ExtractionTemplate,
    chunks: List[Dict[str, Any]],
    doc_title: str,
) -> List[Dict[str, str]]:
    payload_chunks = [
        {
            "chunk_id": chunk["chunk_id"],
            "section": chunk["section"],
            "page_start": chunk["page_start"],
            "page_end": chunk["page_end"],
            "line_start": chunk["line_start"],
            "line_end": chunk["line_end"],
            "text": chunk["text"],
        }
        for chunk in chunks
    ]
    contract = {
        "template_id": template.template_id,
        "entity_types": [
            {
                "entity_type": item.entity_type,
                "label": item.label,
                "discovery": item.discovery,
                "allowed_codes": [entry.code for entry in item.entries],
                "entries": [entry.model_dump() for entry in item.entries],
            }
            for item in template.entity_types
        ],
        "relations": [item.model_dump() for item in template.relations],
        "attributes": [item.model_dump() for item in template.attributes],
        "concept_codes": template.concept_codes,
        "relation_hints": template.relation_hints,
        "rule_hints": template.rule_hints,
        "output_contract": template.output_contract,
    }
    system = (
        "你是临床指南知识抽取引擎。严格按照模板抽取证据并生成候选规则；"
        "不添加模板外的编码，不遗漏页码、行号和原文引文。 "
        "实体 name 必须使用完整临床术语（中文全称），缩写或标准码只写入 code 字段，"
        "不得用缩写（如 CTN、MYO、SYM_FATIGUE）代替完整名称。 "
        "实体 attributes 只能使用该 entity_type 在模板中定义的属性编码，"
        "不得把治疗方案属性写到疾病等其它实体。 "
        "诊断候选规则必须优先使用检验概念（lab_code:concept_code），"
        "不得用出血、结节、溃疡等通用病理词充当疾病证据；"
        "治疗规则应从治疗方案、治疗措施、药物生成。 "
        "模板包含治疗方案（treatment_plan）、治疗措施（intervention）、药物（medication）、"
        "禁忌（contraindication）和监测要求（monitoring_requirement）时，"
        "请同时抽取治疗方案及其包含的措施、药物、禁忌、监测要求，"
        "并生成 rule_type=treatment 的候选治疗规则（默认草稿状态，不得自动启用）。 "
        + " ".join(template.prompt_instructions)
    )
    user = {
        "doc_title": doc_title,
        "template": contract,
        "chunks": payload_chunks,
        "required_output_shape": {
            "entities": [],
            "suggested_relations": [],
            "suggested_rules": [],
            "suggested_treatment_plans": [],
            "suggested_treatment_rules": [],
            "suggested_contraindications": []
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def _merge_warnings(items: List[str]) -> List[str]:
    """Collapse per-group summaries into one document-level message."""
    items = list(dict.fromkeys(items))
    merged: List[str] = []
    relation_counts: Dict[str, int] = {}
    stub_counts: Dict[str, int] = {}
    chunk_corrections = {"relations": 0, "plans": 0, "contraindications": 0}
    unknown_chunk = 0
    plan_missing = 0
    contraindication_missing = 0
    page_corrections = 0
    line_corrections = 0
    for text in items:
        match = re.match(r"^已忽略 (\d+) 条引用未知实体的关系（(.+)）$", text)
        if match:
            for part in match.group(2).split("、"):
                code, _, count = part.rpartition("×")
                if code and count.isdigit():
                    relation_counts[code] = relation_counts.get(code, 0) + int(count)
            continue
        match = re.match(r"^已校正 (.+) 的片段归属（模型返回了未知 chunk_id），内容已保留$", text)
        if match:
            for part in match.group(1).split("、"):
                number = re.match(r"(\d+)", part)
                if not number:
                    continue
                count = int(number.group(1))
                if "治疗方案" in part:
                    chunk_corrections["plans"] += count
                elif "禁忌关系" in part:
                    chunk_corrections["contraindications"] += count
                else:
                    chunk_corrections["relations"] += count
            continue
        match = re.match(r"^已根据关系补全 (\d+) 个未抽取实体（(.+)）", text)
        if match:
            for part in match.group(2).split("、"):
                entity_type, _, count = part.rpartition("×")
                if entity_type and count.isdigit():
                    stub_counts[entity_type] = stub_counts.get(entity_type, 0) + int(count)
            continue
        match = re.match(r"^已忽略 (\d+) 条引用未知 chunk_id 的关系$", text)
        if match:
            unknown_chunk += int(match.group(1))
            continue
        match = re.match(r"^已忽略 (\d+) 个缺少编码的治疗方案$", text)
        if match:
            plan_missing += int(match.group(1))
            continue
        match = re.match(r"^已忽略 (\d+) 条缺少治疗方案或禁忌编码的禁忌关系$", text)
        if match:
            contraindication_missing += int(match.group(1))
            continue
        match = re.match(r"^已校正实体溯源位置（(.+)）", text)
        if match:
            page = re.search(r"页码 (\d+) 处", match.group(1))
            line = re.search(r"行号 (\d+) 处", match.group(1))
            if page:
                page_corrections += int(page.group(1))
            if line:
                line_corrections += int(line.group(1))
            continue
        merged.append(text)
    if relation_counts:
        total = sum(relation_counts.values())
        detail = "、".join(
            f"{code}×{count}" for code, count in sorted(relation_counts.items())
        )
        merged.append(f"已忽略 {total} 条引用未知实体的关系（{detail}）")
    if stub_counts:
        total = sum(stub_counts.values())
        detail = "、".join(
            f"{entity_type}×{count}"
            for entity_type, count in sorted(stub_counts.items())
        )
        merged.append(
            f"已根据关系补全 {total} 个未抽取实体（{detail}），关系已保留，建议核对"
        )
    if any(chunk_corrections.values()):
        parts = []
        if chunk_corrections["relations"]:
            parts.append(f"{chunk_corrections['relations']} 条关系")
        if chunk_corrections["plans"]:
            parts.append(f"{chunk_corrections['plans']} 个治疗方案")
        if chunk_corrections["contraindications"]:
            parts.append(f"{chunk_corrections['contraindications']} 条禁忌关系")
        merged.append(
            "已校正 " + "、".join(parts) + " 的片段归属（模型返回了未知 chunk_id），内容已保留"
        )
    if unknown_chunk:
        merged.append(f"已忽略 {unknown_chunk} 条引用未知 chunk_id 的关系")
    if plan_missing:
        merged.append(f"已忽略 {plan_missing} 个缺少编码的治疗方案")
    if contraindication_missing:
        merged.append(
            f"已忽略 {contraindication_missing} 条缺少治疗方案或禁忌编码的禁忌关系"
        )
    if page_corrections or line_corrections:
        parts = []
        if page_corrections:
            parts.append(f"页码 {page_corrections} 处")
        if line_corrections:
            parts.append(f"行号 {line_corrections} 处")
        merged.append(
            "已校正实体溯源位置（" + "、".join(parts) + "），已对齐到所属片段，不影响入库"
        )
    return merged


async def extract_llm(
    chunks: List[Dict[str, Any]],
    template: ExtractionTemplate,
    doc_id: str,
    doc_title: str,
    config: Dict[str, Any],
) -> ProviderResult:
    settings = config.get("llm", {})
    template = resolve_template(template)
    max_chunks = int(settings.get("max_chunks", 80))
    max_chars = int(settings.get("max_chunk_chars", 12000))
    selected = chunks[:max_chunks]
    warnings: List[str] = []
    if len(chunks) > len(selected):
        warnings.append(f"解析限制：仅发送前 {len(selected)} 个片段，可调大 max_chunks")

    groups: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    size = 0
    for chunk in selected:
        current_size = len(chunk["text"])
        if current and size + current_size > max_chars:
            groups.append(current)
            current, size = [], 0
        current.append(chunk)
        size += current_size
    if current:
        groups.append(current)

    api_key = resolve_api_key(settings)
    if not api_key:
        raise ValueError(
            "未配置大模型 API Key：请在解析配置中填写 API Key，或设置 "
            f"{settings.get('api_key_env') or 'OPENAI_API_KEY'} 环境变量"
        )
    base_url = str(settings.get("base_url") or "").rstrip("/")
    if not base_url:
        raise ValueError("大模型 base_url 未配置")

    all_entities: List[Dict[str, Any]] = []
    all_rules: List[Dict[str, Any]] = []
    all_relations: List[Dict[str, Any]] = []
    all_treatment_plans: List[Dict[str, Any]] = []
    all_treatment_rules: List[Dict[str, Any]] = []
    all_contraindications: List[Dict[str, Any]] = []
    successful_groups = 0

    def absorb(payload: Dict[str, Any], group_chunks: List[Dict[str, Any]]) -> None:
        (entities, rules, relations, group_warnings,
         treatment_plans, treatment_rules, contraindications) = _validate_llm_payload(
            payload, group_chunks, template, doc_id, doc_title
        )
        all_entities.extend(entities)
        all_rules.extend(rules)
        all_relations.extend(relations)
        all_treatment_plans.extend(treatment_plans)
        all_treatment_rules.extend(treatment_rules)
        all_contraindications.extend(contraindications)
        warnings.extend(group_warnings)

    for group in groups:
        try:
            payload = await chat_json(settings, _llm_prompt(template, group, doc_title))
            absorb(payload, group)
            successful_groups += 1
        except (LLMError, KeyError, IndexError, json.JSONDecodeError, ValueError) as error:
            message = str(error)
            split_payloads: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
            if len(group) > 1 and any(
                token in message for token in ("JSON", "截断", "Expecting")
            ):
                # Retry oversized groups chunk by chunk so one truncated
                # response does not discard the whole group.
                for chunk in group:
                    try:
                        payload = await chat_json(
                            settings, _llm_prompt(template, [chunk], doc_title)
                        )
                        split_payloads.append((payload, [chunk]))
                    except (LLMError, KeyError, IndexError, json.JSONDecodeError, ValueError):
                        continue
            if split_payloads:
                for payload, chunk_group in split_payloads:
                    absorb(payload, chunk_group)
                successful_groups += 1
                failed = len(group) - len(split_payloads)
                warnings.append(
                    f"片段组 {group[0]['chunk_id']} 输出过大或格式错误，"
                    f"已按单片段重试成功 {len(split_payloads)}/{len(group)} 个"
                    + (f"，仍失败 {failed} 个" if failed else "")
                )
            else:
                warnings.append(f"片段 {group[0]['chunk_id']} 解析失败: {message[:180]}")

    if not successful_groups:
        raise ValueError("大模型解析没有成功片段，请检查服务配置或查看告警")
    dedupe_rules = {rule["rule_id"]: rule for rule in all_rules}
    dedupe_tx_rules = {rule["rule_id"]: rule for rule in all_treatment_rules}
    dedupe_tx_plans = {plan["treatment_plan_code"]: plan for plan in all_treatment_plans}
    dedupe_contras = {
        (item["treatment_plan_code"], item["contraindication_code"]): item
        for item in all_contraindications
    }
    return ProviderResult(
        entities=all_entities,
        suggested_rules=list(dedupe_rules.values()),
        suggested_relations=all_relations,
        suggested_treatment_plans=list(dedupe_tx_plans.values()),
        suggested_treatment_rules=list(dedupe_tx_rules.values()),
        suggested_contraindications=list(dedupe_contras.values()),
        warnings=_merge_warnings(warnings),
        metadata={
            "provider": "llm",
            "base_url": base_url,
            "model": settings.get("model"),
            "groups_sent": len(groups),
            "groups_succeeded": successful_groups,
            "chunks_sent": sum(len(group) for group in groups),
            "chunks_total": len(chunks),
        },
    )


def extract_local(
    chunks: List[Dict[str, Any]],
    template: ExtractionTemplate,
    doc_id: str,
    doc_title: str = "",
) -> ProviderResult:
    template = resolve_template(template)
    entities = _local_entities(chunks, template)
    relations = _local_relations(entities, template)
    if normalize_domain(template.domain) == "tcm":
        from ..domains.tcm.rule_generation import generate_tcm_rules
        rules = generate_tcm_rules(
            entities, chunks, template, doc_id, doc_title, relations=relations,
        )
    else:
        rules = generate_western_rules(
            entities, chunks, template, doc_id, doc_title, relations=relations,
        )
    warnings = []
    discovery_types = {
        item.entity_type for item in template.entity_types if item.discovery
    }
    if any(
        item.get("entity_type") in discovery_types
        and str(item.get("code", "")).startswith("AUTO-")
        for item in entities
    ):
        warnings.append("部分实体名称不在医学词表中，已自动编码并建议人工核对编码。")
    return ProviderResult(
        entities=entities,
        suggested_rules=rules,
        warnings=warnings,
        metadata={"provider": "local", "template_id": template.template_id},
        suggested_relations=relations,
    )


async def extract(
    mode: str,
    chunks: List[Dict[str, Any]],
    template: ExtractionTemplate,
    doc_id: str,
    doc_title: str,
    config: Dict[str, Any],
) -> ProviderResult:
    if mode == "llm":
        return await extract_llm(chunks, template, doc_id, doc_title, config)
    if mode == "local":
        return extract_local(chunks, template, doc_id, doc_title)
    raise ValueError(f"不支持的解析方式: {mode}")

