"""Western-medicine template rule generation (v2).

The v1 generator paired every disease with the first entities in its chunk.
On long guidelines that produced dozens of identical, clinically meaningless
rules.  v2 scopes conditions to the disease's local section, uses lab concepts
inferred from local text, drops generic pathology findings, and emits treatment
rules from the same section.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..base import WESTERN
from ...extraction.models import ExtractionTemplate
from ...extraction.graph_binding import resolve_template_graph_filename
from ...terminology.labels import default_labels
from ...extraction.concept_mapping import (
    CARDIAC_FINDINGS,
    GENERIC_DISEASE_BLOCKLIST,
    GENERIC_PARENT_CODES,
    STANDARD_BACKED_LABS,
    exam_code_for_finding,
    infer_lab_concept,
    is_generic_finding,
    nearest_concept,
    normalize_lab_concept,
)

LAB_PRIORITY = {"CTN": 0, "CK_MB": 1, "MYO": 2}
MAX_LABS = 5
MAX_SYMPTOMS = 4
MAX_FINDINGS = 3
MAX_TREATMENTS = 6


def _line(value: Any, fallback: int = 1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _entry_aliases(code: str, template: ExtractionTemplate) -> List[str]:
    for entity_type in template.entity_types:
        if entity_type.entity_type != "disease":
            continue
        for entry in entity_type.entries:
            if entry.code == code:
                return [entry.name, *entry.aliases]
    return []


def _display_name(disease: Dict[str, Any], template: ExtractionTemplate) -> str:
    """Prefer a more specific guideline term over the canonical dictionary name.

    Fulminant myocarditis has no separate ICD-10 code; its dictionary entry is
    I40.9 (canonical name 急性心肌炎, alias 暴发性心肌炎).  The rule list should
    show the term used by the guideline when it is more specific.
    """
    code = str(disease.get("code") or "")
    canonical = str(disease.get("name") or code)
    quote = str(disease.get("quote") or "")
    aliases = _entry_aliases(code, template)
    candidates = [
        alias for alias in aliases
        if alias and alias in quote and len(alias) > len(canonical)
    ]
    return max(candidates, key=len) if candidates else canonical


def _diseases_in_chunk(chunk_entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    diseases: Dict[str, Dict[str, Any]] = {}
    for entity in chunk_entities:
        if entity.get("entity_type") != "disease":
            continue
        code = str(entity.get("code") or "")
        name = str(entity.get("name") or "")
        if not code or not name or name in GENERIC_DISEASE_BLOCKLIST:
            continue
        # Auto-discovered names are retained in the graph for review, but only
        # dictionary-backed diseases generate rules.
        if code.startswith("AUTO-"):
            continue
        diseases.setdefault(code, entity)
    return sorted(
        diseases.values(),
        key=lambda item: (_line(item.get("line_start")), _line(item.get("page"))),
    )


def _window(
    disease: Dict[str, Any],
    next_disease: Optional[Dict[str, Any]],
    chunk: Dict[str, Any],
) -> Tuple[int, int]:
    start = _line(disease.get("line_start"), _line(chunk.get("line_start")))
    next_start = _line(next_disease.get("line_start")) if next_disease else 0
    end = next_start - 1 if next_start and next_start > start else _line(
        chunk.get("line_end"), start + 25
    )
    # Keep a useful local section even when the next disease starts immediately.
    if end - start < 3:
        end = start + 12
    return max(_line(chunk.get("line_start")), start - 2), min(
        _line(chunk.get("line_end"), end), end + 1
    )


def _in_window(entity: Dict[str, Any], start: int, end: int) -> bool:
    line = _line(entity.get("line_start"))
    return start <= line <= end


def _lab_conditions(
    entities: List[Dict[str, Any]],
    start: int,
    end: int,
) -> List[Dict[str, Any]]:
    labs = [
        item for item in entities
        if item.get("entity_type") == "lab"
        and str(item.get("code") or "") in STANDARD_BACKED_LABS
        and _in_window(item, start, end)
    ]
    concepts = [
        item for item in entities
        if item.get("entity_type") == "concept"
        and item.get("concept_code")
        and _in_window(item, start, end)
    ]
    conditions: List[Dict[str, Any]] = []
    seen = set()
    for lab in sorted(labs, key=lambda item: LAB_PRIORITY.get(str(item.get("code")), 99)):
        code = str(lab.get("code") or "")
        context = str(lab.get("quote") or lab.get("name") or "")
        concept = nearest_concept(concepts, _line(lab.get("line_start"))) or infer_lab_concept(
            code, context
        )
        concept = normalize_lab_concept(code, concept, context)
        if not concept:
            continue
        key = (code, concept)
        if key in seen:
            continue
        seen.add(key)
        conditions.append({
            "type": "lab_concept",
            "lab_code": code,
            "concept_code": concept,
        })
        if len(conditions) >= MAX_LABS:
            break
    return conditions


def _symptom_conditions(
    entities: List[Dict[str, Any]],
    start: int,
    end: int,
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for item in entities:
        if item.get("entity_type") != "symptom" or not _in_window(item, start, end):
            continue
        code = str(item.get("code") or "")
        if not code or code in seen:
            continue
        seen.add(code)
        result.append({"type": "symptom", "symptom_code": code})
        if len(result) >= MAX_SYMPTOMS:
            break
    return result


def _finding_conditions(
    entities: List[Dict[str, Any]],
    start: int,
    end: int,
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for item in entities:
        if not _in_window(item, start, end):
            continue
        code = str(item.get("code") or "")
        if not code or code in seen or is_generic_finding(code) or code not in CARDIAC_FINDINGS:
            continue
        exam_code = exam_code_for_finding(code)
        if not exam_code:
            continue
        seen.add(code)
        result.append({
            "type": "exam_finding",
            "exam_code": exam_code,
            "finding_code": code,
        })
        if len(result) >= MAX_FINDINGS:
            break
    return result


def _source(entity: Dict[str, Any], chunk: Dict[str, Any], doc_id: str, doc_title: str) -> Dict[str, Any]:
    return {
        "guideline_id": doc_id,
        "guideline_title": doc_title,
        "chunk_id": entity.get("chunk_id", ""),
        "section": chunk.get("section", ""),
        "page_start": entity.get("page") or chunk.get("page_start"),
        "page_end": entity.get("page") or chunk.get("page_end"),
        "line_start": entity.get("line_start") or chunk.get("line_start"),
        "line_end": entity.get("line_end") or chunk.get("line_end"),
        "quote": entity.get("quote") or chunk.get("text", "")[:220],
    }


def _treatment_entities(
    entities: List[Dict[str, Any]],
    start: int,
    end: int,
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for item in entities:
        if item.get("entity_type") not in {"treatment_plan", "intervention", "medication"}:
            continue
        if not _in_window(item, start, end):
            continue
        code = str(item.get("code") or "")
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(item)
        if len(result) >= MAX_TREATMENTS:
            break
    return result


def _order_sets_in_window(
    entities: List[Dict[str, Any]],
    start: int,
    end: int,
) -> List[Dict[str, Any]]:
    """Order sets described in the same guideline window as the treatment.

    Order sets are guideline-level materialization of a treatment plan; rules
    carry their codes so the recommendation layer can expand them into
    orderable items with per-hospital mapping later.
    """
    result: List[Dict[str, Any]] = []
    seen = set()
    for item in entities:
        if item.get("entity_type") != "order_set":
            continue
        if not _in_window(item, start, end):
            continue
        code = str(item.get("code") or "")
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(item)
    return result


def generate_western_rules(
    entities: List[Dict[str, Any]],
    chunks: List[Dict[str, Any]],
    template: ExtractionTemplate,
    doc_id: str,
    doc_title: str = "",
    relations: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    chunk_map = {chunk["chunk_id"]: chunk for chunk in chunks}
    entities_by_chunk: Dict[str, List[Dict[str, Any]]] = {}
    for entity in entities:
        entities_by_chunk.setdefault(str(entity.get("chunk_id") or ""), []).append(entity)

    diagnosis: Dict[str, Dict[str, Any]] = {}
    treatment: Dict[str, Dict[str, Any]] = {}
    graph_filename = resolve_template_graph_filename(template)

    for chunk_id, chunk_entities in entities_by_chunk.items():
        chunk = chunk_map.get(chunk_id, {})
        diseases = _diseases_in_chunk(chunk_entities)
        for index, disease in enumerate(diseases):
            next_disease = diseases[index + 1] if index + 1 < len(diseases) else None
            start, end = _window(disease, next_disease, chunk)
            labs = _lab_conditions(chunk_entities, start, end)
            symptoms = _symptom_conditions(chunk_entities, start, end)
            findings = _finding_conditions(chunk_entities, start, end)
            treatments = _treatment_entities(chunk_entities, start, end)
            order_sets = _order_sets_in_window(chunk_entities, start, end)

            children: List[Dict[str, Any]] = []
            if labs:
                children.append({"type": "group", "operator": "OR", "children": labs})
            if symptoms:
                children.append({"type": "group", "operator": "OR", "children": symptoms})
            if findings:
                children.append({"type": "group", "operator": "OR", "children": findings})

            # Primary-disease policy: a diagnosis rule needs at least two
            # independent evidence categories (lab + symptom/exam, or symptom
            # + exam), or multiple labs.  Differential diagnoses and
            # comorbidities mentioned in the guideline do not become rules.
            usable = (
                (bool(labs) and bool(symptoms or findings))
                or len(labs) >= 2
                or len(symptoms) >= 2
            )
            if usable:
                score = len(labs) * 2 + len(symptoms) + len(findings)
                code = str(disease.get("code"))
                previous = diagnosis.get(code)
                if previous is None or score > previous["score"]:
                    diagnosis[code] = {
                        "score": score,
                        "disease": disease,
                        "chunk": chunk,
                        "display_name": _display_name(disease, template),
                        "conditions": {"type": "group", "operator": "AND", "children": children},
                    }

            if treatments or order_sets:
                code = str(disease.get("code"))
                previous = treatment.get(code)
                if previous is None or len(treatments) > previous["count"]:
                    treatment[code] = {
                        "count": len(treatments),
                        "disease": disease,
                        "chunk": chunk,
                        "display_name": _display_name(disease, template),
                        "items": treatments,
                        "order_sets": order_sets,
                    }

    # Primary-disease policy: drop unspecified parent codes when the same
    # guideline generated a more specific diagnosis.
    if any(code not in GENERIC_PARENT_CODES for code in diagnosis):
        for code in list(diagnosis):
            if code in GENERIC_PARENT_CODES:
                diagnosis.pop(code, None)
        for code in list(treatment):
            if code in GENERIC_PARENT_CODES:
                treatment.pop(code, None)

    rules: List[Dict[str, Any]] = []
    labels = default_labels()
    for index, (code, candidate) in enumerate(sorted(diagnosis.items()), start=1):
        disease = candidate["disease"]
        chunk = candidate["chunk"]
        display_name = candidate.get("display_name") or disease.get("name") or code
        conditions = candidate["conditions"]
        labels.annotate(conditions)
        rules.append({
            "rule_id": f"{doc_id}-AUTO-{index:03d}",
            "version": 1,
            "domain": WESTERN,
            "anchor_type": "disease",
            "rule_type": "diagnosis",
            "name": f"自动生成: {display_name}诊断规则",
            "disease_code": code,
            "disease_name": str(display_name),
            "priority": 50,
            "status": "draft",
            "graph_filename": graph_filename,
            "conditions": conditions,
            "condition_text": labels.summarize(conditions),
            "conclusion": {
                "diagnosis": code,
                "confidence": "medium",
                "message": "由模板按疾病所在章节生成的候选规则，需医学专员审核后启用",
            },
            "source": _source(disease, chunk, doc_id, doc_title),
            "tags": ["auto-generated", "diagnosis", doc_id],
        })

    treatment_index = 0
    for code, candidate in sorted(treatment.items()):
        # A treatment rule is only useful when the same disease has a
        # diagnosis rule to trigger it; otherwise it can never fire.
        if code not in diagnosis:
            continue
        items = candidate["items"]
        order_sets = candidate.get("order_sets") or []
        if not items and not order_sets:
            continue
        treatment_index += 1
        disease = candidate["disease"]
        chunk = candidate["chunk"]
        display_name = candidate.get("display_name") or disease.get("name") or code
        names = [str(item.get("name") or item.get("code")) for item in items]
        primary = next(
            (item for item in items if item.get("entity_type") == "treatment_plan"),
            items[0] if items else (order_sets[0] if order_sets else disease),
        )
        order_set_ids = [
            str(item.get("code")) for item in order_sets if item.get("code")
        ]
        conclusion = {
            "diagnosis": code,
            "confidence": "medium",
            "conclusion_type": "treatment",
            "treatment_plan_id": f"TP-{doc_id}-{code}",
            "treatment_name": f"{display_name}治疗方案",
            "treatment_items": [
                {
                    "code": str(item.get("code") or ""),
                    "name": str(item.get("name") or item.get("code") or ""),
                    "entity_type": str(item.get("entity_type") or "medication"),
                }
                for item in items if item.get("code")
            ],
            "message": "指南同章节推荐：" + "、".join(names[:6]),
        }
        if order_set_ids:
            conclusion["order_set_ids"] = order_set_ids
            if not names:
                set_names = [
                    str(item.get("name") or item.get("code")) for item in order_sets
                ]
                conclusion["message"] = "指南同章节医嘱套餐：" + "、".join(set_names[:6])
        rules.append({
            "rule_id": f"{doc_id}-TREAT-{treatment_index:03d}",
            "version": 1,
            "domain": WESTERN,
            "anchor_type": "disease",
            "rule_type": "treatment",
            "name": f"自动生成: {display_name}治疗规则",
            "disease_code": code,
            "disease_name": str(display_name),
            "priority": 40,
            "status": "draft",
            "graph_filename": graph_filename,
            # The treatment stage already gates on a matched diagnosis for the
            # same disease; an empty group is therefore a deliberate no-op.
            "conditions": {"type": "group", "operator": "AND", "children": []},
            "condition_text": "同疾病诊断成立后触发",
            "conclusion": conclusion,
            "source": _source(primary, chunk, doc_id, doc_title),
            "tags": ["auto-generated", "treatment", doc_id],
        })
    return rules
