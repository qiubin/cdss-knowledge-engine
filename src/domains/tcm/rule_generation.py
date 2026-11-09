"""Template-driven TCM candidate rule generation.

Produces four conservative rule families from one guideline chunk:

* ``tcm_pattern``      syndrome ← main/secondary symptoms + tongue + pulse
* ``tcm_treatment``    syndrome fact → treatment method
* ``tcm_prescription`` syndrome fact → formula (+ herbs from relation)
* ``tcm_contraindication`` syndrome fact → contraindication

Everything stays ``draft`` and same-chunk constrained: a candidate rule never
combines manifestations scattered across unrelated guideline sections.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..base import RuleGenerator, TCM
from ...extraction.models import ExtractionTemplate
from ...extraction.graph_binding import resolve_template_graph_filename
from ...terminology.labels import default_labels

OBSERVATION_TYPES = ("symptom", "tongue", "pulse", "sign")

DEFAULT_PATTERN_CONFIG = {
    "rule_type": "tcm_pattern",
    "anchor": "syndrome",
    "condition_types": list(OBSERVATION_TYPES),
    "min_conditions": 2,
    "score_threshold": 0.4,
}


def _by_type(entities: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    result: Dict[str, List[Dict[str, Any]]] = {}
    for entity in entities:
        result.setdefault(str(entity.get("entity_type") or ""), []).append(entity)
    return result


def _same_chunk(items: Iterable[Dict[str, Any]], chunk_id: str) -> List[Dict[str, Any]]:
    return [item for item in items if item.get("chunk_id") == chunk_id]


def _code(entity: Dict[str, Any]) -> str:
    return str(entity.get("code") or entity.get("concept_code") or "")


def _observation(entity: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "observation",
        "observation_type": str(entity.get("entity_type") or ""),
        "code": _code(entity),
        "concept_code": "PRESENT",
    }


def _source(
    entity: Dict[str, Any],
    chunk: Dict[str, Any],
    doc_id: str,
    doc_title: str,
) -> Dict[str, Any]:
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


def _config(template: ExtractionTemplate, rule_type: str) -> Dict[str, Any]:
    configured = next(
        (item for item in template.rule_generation if item.get("rule_type") == rule_type),
        None,
    )
    if configured:
        return {**DEFAULT_PATTERN_CONFIG, **configured}
    return dict(DEFAULT_PATTERN_CONFIG)


def _or_group(entities: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    children = [_observation(item) for item in entities if _code(item)]
    if not children:
        return None
    if len(children) == 1:
        return children[0]
    return {"type": "group", "operator": "OR", "children": children}


class TcmPatternRuleGenerator(RuleGenerator):
    rule_type = "tcm_pattern"

    def generate(
        self,
        entities: List[Dict[str, Any]],
        chunks: List[Dict[str, Any]],
        template: ExtractionTemplate,
        doc_id: str,
        doc_title: str = "",
        relations: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        config = _config(template, self.rule_type)
        chunk_map = {chunk["chunk_id"]: chunk for chunk in chunks}
        by_type = _by_type(entities)
        rules: List[Dict[str, Any]] = []

        for syndrome in by_type.get("syndrome", []):
            syndrome_code = _code(syndrome)
            if not syndrome_code:
                continue
            chunk_id = syndrome.get("chunk_id", "")
            chunk = chunk_map.get(chunk_id, {})
            symptoms = _same_chunk(by_type.get("symptom", []), chunk_id)
            main_symptoms = [
                item for item in symptoms
                if str((item.get("attributes") or {}).get("SYMPTOM_ROLE") or "主症") != "次症"
            ]
            secondary_symptoms = [
                item for item in symptoms
                if str((item.get("attributes") or {}).get("SYMPTOM_ROLE") or "") == "次症"
            ]
            groups = [
                _or_group(main_symptoms),
                _or_group(secondary_symptoms),
                _or_group(_same_chunk(by_type.get("tongue", []), chunk_id)),
                _or_group(_same_chunk(by_type.get("pulse", []), chunk_id)),
                _or_group(_same_chunk(by_type.get("sign", []), chunk_id)),
            ]
            children = [group for group in groups if group]
            leaf_count = sum(
                len(group.get("children", [group])) if group.get("type") == "group" else 1
                for group in children
            )
            if leaf_count < int(config.get("min_conditions", 2)):
                continue

            rules.append({
                "rule_id": f"{doc_id}-TCM-PATTERN-{len(rules) + 1:03d}",
                "version": 1,
                "domain": TCM,
                "anchor_type": "syndrome",
                "rule_type": self.rule_type,
                "name": f"辨证规则: {syndrome.get('name') or syndrome_code}",
                "disease_code": syndrome_code,
                "disease_name": str(syndrome.get("name") or syndrome_code),
                "priority": 50,
                "status": "draft",
                "graph_filename": resolve_template_graph_filename(template),
                "conditions": {"type": "group", "operator": "AND", "children": children},
                "conclusion": {
                    "diagnosis": syndrome_code,
                    "confidence": "medium",
                    "message": "由中医模板自动生成的辨证候选规则，需中医师审核后启用",
                    "score_threshold": float(config.get("score_threshold", 0.4)),
                },
                "source": _source(syndrome, chunk, doc_id, doc_title),
                "tags": ["auto-generated", "tcm_pattern", doc_id],
            })
        return rules


class TcmTreatmentRuleGenerator(RuleGenerator):
    rule_type = "tcm_treatment"

    def generate(
        self,
        entities: List[Dict[str, Any]],
        chunks: List[Dict[str, Any]],
        template: ExtractionTemplate,
        doc_id: str,
        doc_title: str = "",
        relations: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        chunk_map = {chunk["chunk_id"]: chunk for chunk in chunks}
        by_type = _by_type(entities)
        syndrome_by_code = {_code(item): item for item in by_type.get("syndrome", [])}
        method_by_code = {_code(item): item for item in by_type.get("treatment_method", [])}
        rules: List[Dict[str, Any]] = []
        pairs: List[Tuple[str, str, str]] = []

        for relation in relations or []:
            if relation.get("relation_code") != "SYNDROME_TREATED_BY_METHOD":
                continue
            pairs.append((
                str(relation.get("source_code") or ""),
                str(relation.get("target_code") or ""),
                str(relation.get("chunk_id") or ""),
            ))
        if not pairs:
            for syndrome in syndrome_by_code.values():
                chunk_id = syndrome.get("chunk_id", "")
                for method in _same_chunk(by_type.get("treatment_method", []), chunk_id):
                    pairs.append((_code(syndrome), _code(method), chunk_id))

        seen = set()
        for syndrome_code, method_code, chunk_id in pairs:
            if not syndrome_code or not method_code or (syndrome_code, method_code) in seen:
                continue
            seen.add((syndrome_code, method_code))
            syndrome = syndrome_by_code.get(syndrome_code, {"code": syndrome_code, "chunk_id": chunk_id})
            method = method_by_code.get(method_code, {"code": method_code, "chunk_id": chunk_id, "name": method_code})
            chunk = chunk_map.get(chunk_id or syndrome.get("chunk_id", ""), {})
            rules.append({
                "rule_id": f"{doc_id}-TCM-TREAT-{len(rules) + 1:03d}",
                "version": 1,
                "domain": TCM,
                "anchor_type": "syndrome",
                "rule_type": self.rule_type,
                "name": f"治法规则: {syndrome.get('name') or syndrome_code}",
                "disease_code": syndrome_code,
                "disease_name": str(syndrome.get("name") or syndrome_code),
                "priority": 40,
                "status": "draft",
                "graph_filename": resolve_template_graph_filename(template),
                "conditions": {
                    "type": "fact",
                    "fact_type": "syndrome",
                    "code": syndrome_code,
                },
                "conclusion": {
                    "diagnosis": syndrome_code,
                    "confidence": "medium",
                    "conclusion_type": "treatment",
                    "treatment_method_code": method_code,
                    "treatment_name": str(method.get("name") or method_code),
                    "message": f"证候成立时推荐治法：{method.get('name') or method_code}",
                },
                "source": _source(method, chunk, doc_id, doc_title),
                "tags": ["auto-generated", "tcm_treatment", doc_id],
            })
        return rules


class TcmPrescriptionRuleGenerator(RuleGenerator):
    rule_type = "tcm_prescription"

    def generate(
        self,
        entities: List[Dict[str, Any]],
        chunks: List[Dict[str, Any]],
        template: ExtractionTemplate,
        doc_id: str,
        doc_title: str = "",
        relations: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        chunk_map = {chunk["chunk_id"]: chunk for chunk in chunks}
        by_type = _by_type(entities)
        syndrome_by_code = {_code(item): item for item in by_type.get("syndrome", [])}
        method_by_code = {_code(item): item for item in by_type.get("treatment_method", [])}
        formula_by_code = {_code(item): item for item in by_type.get("formula", [])}
        herb_by_code = {_code(item): item for item in by_type.get("herb", [])}

        # relationship indexes
        syndrome_methods: Dict[str, List[str]] = {}
        method_formulas: Dict[str, List[str]] = {}
        formula_herbs: Dict[str, List[str]] = {}
        for relation in relations or []:
            code = relation.get("relation_code")
            source = str(relation.get("source_code") or "")
            target = str(relation.get("target_code") or "")
            if not source or not target:
                continue
            if code == "SYNDROME_TREATED_BY_METHOD":
                syndrome_methods.setdefault(source, []).append(target)
            elif code == "METHOD_HAS_FORMULA":
                method_formulas.setdefault(source, []).append(target)
            elif code == "FORMULA_CONTAINS_HERB":
                formula_herbs.setdefault(source, []).append(target)

        # Fallback: same chunk pairing when the extractor produced no relations.
        if not syndrome_methods:
            for syndrome in syndrome_by_code.values():
                chunk_id = syndrome.get("chunk_id", "")
                syndrome_methods[_code(syndrome)] = [
                    _code(item) for item in _same_chunk(by_type.get("treatment_method", []), chunk_id)
                ]
        if not method_formulas:
            for method in method_by_code.values():
                chunk_id = method.get("chunk_id", "")
                method_formulas[_code(method)] = [
                    _code(item) for item in _same_chunk(by_type.get("formula", []), chunk_id)
                ]
        if not formula_herbs:
            for formula in formula_by_code.values():
                chunk_id = formula.get("chunk_id", "")
                formula_herbs[_code(formula)] = [
                    _code(item) for item in _same_chunk(by_type.get("herb", []), chunk_id)
                ]

        rules: List[Dict[str, Any]] = []
        seen = set()
        for syndrome_code, methods in syndrome_methods.items():
            for method_code in methods:
                for formula_code in method_formulas.get(method_code, []):
                    key = (syndrome_code, formula_code)
                    if key in seen:
                        continue
                    seen.add(key)
                    syndrome = syndrome_by_code.get(syndrome_code, {"code": syndrome_code})
                    method = method_by_code.get(method_code, {"code": method_code})
                    formula = formula_by_code.get(
                        formula_code, {"code": formula_code, "name": formula_code}
                    )
                    herbs = []
                    for herb_code in formula_herbs.get(formula_code, []):
                        herb = herb_by_code.get(herb_code, {"code": herb_code, "name": herb_code})
                        attributes = herb.get("attributes") or {}
                        herbs.append({
                            "code": herb_code,
                            "name": str(herb.get("name") or herb_code),
                            "dose": str(attributes.get("HERB_DOSE") or ""),
                            "role": str(attributes.get("HERB_ROLE") or ""),
                        })
                    chunk = chunk_map.get(formula.get("chunk_id", syndrome.get("chunk_id", "")), {})
                    rules.append({
                        "rule_id": f"{doc_id}-TCM-RX-{len(rules) + 1:03d}",
                        "version": 1,
                        "domain": TCM,
                        "anchor_type": "syndrome",
                        "rule_type": self.rule_type,
                        "name": f"方药规则: {syndrome.get('name') or syndrome_code}",
                        "disease_code": syndrome_code,
                        "disease_name": str(syndrome.get("name") or syndrome_code),
                        "priority": 30,
                        "status": "draft",
                        "graph_filename": resolve_template_graph_filename(template),
                        "conditions": {
                            "type": "fact",
                            "fact_type": "syndrome",
                            "code": syndrome_code,
                        },
                        "conclusion": {
                            "diagnosis": syndrome_code,
                            "confidence": "medium",
                            "conclusion_type": "prescription",
                            "treatment_method_code": method_code,
                            "treatment_name": str(method.get("name") or method_code),
                            "formula_code": formula_code,
                            "formula_name": str(formula.get("name") or formula_code),
                            "herbs": herbs,
                            "message": f"证候成立时推荐方剂：{formula.get('name') or formula_code}",
                        },
                        "source": _source(formula, chunk, doc_id, doc_title),
                        "tags": ["auto-generated", "tcm_prescription", doc_id],
                    })
        return rules


class TcmContraindicationRuleGenerator(RuleGenerator):
    rule_type = "tcm_contraindication"

    def generate(
        self,
        entities: List[Dict[str, Any]],
        chunks: List[Dict[str, Any]],
        template: ExtractionTemplate,
        doc_id: str,
        doc_title: str = "",
        relations: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        chunk_map = {chunk["chunk_id"]: chunk for chunk in chunks}
        by_type = _by_type(entities)
        syndrome_by_code = {_code(item): item for item in by_type.get("syndrome", [])}
        rules: List[Dict[str, Any]] = []
        for syndrome in syndrome_by_code.values():
            chunk_id = syndrome.get("chunk_id", "")
            chunk = chunk_map.get(chunk_id, {})
            for contradiction in _same_chunk(by_type.get("contraindication", []), chunk_id):
                rules.append({
                    "rule_id": f"{doc_id}-TCM-CONTRA-{len(rules) + 1:03d}",
                    "version": 1,
                    "domain": TCM,
                    "anchor_type": "syndrome",
                    "rule_type": self.rule_type,
                    "name": f"禁忌规则: {syndrome.get('name') or _code(syndrome)}",
                    "disease_code": _code(syndrome),
                    "disease_name": str(syndrome.get("name") or _code(syndrome)),
                    "priority": 60,
                    "status": "draft",
                    "graph_filename": resolve_template_graph_filename(template),
                    "conditions": {
                        "type": "fact",
                        "fact_type": "syndrome",
                        "code": _code(syndrome),
                    },
                    "conclusion": {
                        "diagnosis": _code(syndrome),
                        "confidence": "medium",
                        "conclusion_type": "contraindication",
                        "message": str(contradiction.get("name") or _code(contradiction)),
                    },
                    "source": _source(contradiction, chunk, doc_id, doc_title),
                    "tags": ["auto-generated", "tcm_contraindication", doc_id],
                })
        return rules


_GENERATORS = (
    TcmPatternRuleGenerator(),
    TcmTreatmentRuleGenerator(),
    TcmPrescriptionRuleGenerator(),
    TcmContraindicationRuleGenerator(),
)


def generate_tcm_rules(
    entities: List[Dict[str, Any]],
    chunks: List[Dict[str, Any]],
    template: ExtractionTemplate,
    doc_id: str,
    doc_title: str = "",
    relations: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Run every enabled TCM generator for one document."""
    abilities = set(template.capabilities or [])
    configured = {item.get("rule_type") for item in template.rule_generation}
    rules: List[Dict[str, Any]] = []
    for generator in _GENERATORS:
        if configured:
            enabled = generator.rule_type in configured
        else:
            enabled = generator.rule_type in abilities or "tcm" in abilities
        if enabled:
            rules.extend(generator.generate(
                entities, chunks, template, doc_id, doc_title, relations=relations,
            ))
    labels = default_labels()
    for rule in rules:
        labels.annotate(rule.get("conditions"))
        rule["condition_text"] = labels.summarize(rule.get("conditions"))
    return rules
