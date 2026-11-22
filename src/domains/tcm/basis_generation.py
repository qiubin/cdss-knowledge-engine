"""Build reviewable TCM clinical bases from graph-native treatment chains."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from src.models.rule import Rule


OBSERVATION_RELATIONS = {
    "SYNDROME_HAS_SYMPTOM": "symptom",
    "SYNDROME_HAS_TONGUE": "tongue",
    "SYNDROME_HAS_PULSE": "pulse",
    "SYNDROME_HAS_SIGN": "sign",
}


def _rows(value: Any) -> List[Dict[str, Any]]:
    return list(value.values()) if isinstance(value, dict) else list(value or [])


def materialize_syndrome_formula_relations(graph: Dict[str, Any]) -> int:
    """Backfill the direct prescription edge from an existing reviewed chain."""
    relations_value = graph.get("relations") or {}
    relations = _rows(relations_value)
    existing = {
        (edge.get("source_node_id"), edge.get("relation_code"), edge.get("target_node_id"))
        for edge in relations
    }
    method_formulas: Dict[str, List[Dict[str, Any]]] = {}
    for edge in relations:
        if edge.get("relation_code") == "METHOD_HAS_FORMULA":
            method_formulas.setdefault(str(edge.get("source_node_id") or ""), []).append(edge)
    added = 0
    for syndrome_method in list(relations):
        if syndrome_method.get("relation_code") != "SYNDROME_TREATED_BY_METHOD":
            continue
        for method_formula in method_formulas.get(str(syndrome_method.get("target_node_id") or ""), []):
            key = (syndrome_method.get("source_node_id"), "SYNDROME_RECOMMENDS_FORMULA",
                   method_formula.get("target_node_id"))
            if key in existing:
                continue
            edge = {
                "source_node_id": key[0], "relation_code": key[1],
                "relation_name": "证候推荐方剂", "target_node_id": key[2],
                "attributes": {"derived_from": "SYNDROME_TREATED_BY_METHOD+METHOD_HAS_FORMULA"},
                "source_refs": list(dict.fromkeys([
                    *(syndrome_method.get("source_refs") or []),
                    *(method_formula.get("source_refs") or []),
                ])),
                "review_status": "draft",
            }
            if isinstance(relations_value, dict):
                relations_value[key] = edge
            else:
                relations_value.append(edge)
            existing.add(key)
            added += 1
    return added


def generate_tcm_basis_candidates(
    graph: Dict[str, Any], doc_id: str, graph_filename: str,
) -> List[Dict[str, Any]]:
    """Derive draft pattern/treatment/prescription specifications.

    Only explicit graph edges are used.  No eligibility threshold, dose or
    treatment item is invented; every generated basis remains behind the
    normal graph dependency and human-review gates.
    """
    nodes = {item["id"]: item for item in _rows(graph.get("nodes")) if item.get("id")}
    relations = _rows(graph.get("relations"))
    sources = {str(item.get("chunk_id") or ""): item for item in graph.get("sources") or []}

    def outgoing(source_id: str, codes: Iterable[str] = ()) -> List[Dict[str, Any]]:
        allowed = set(codes)
        return [edge for edge in relations if edge.get("source_node_id") == source_id
                and (not allowed or edge.get("relation_code") in allowed)]

    def source_for(edges: List[Dict[str, Any]]) -> Dict[str, Any]:
        refs = [str(ref) for edge in edges for ref in edge.get("source_refs") or [] if ref]
        row = next((sources[ref] for ref in refs if ref in sources), {})
        return {
            "guideline_id": str(row.get("doc_id") or doc_id),
            "guideline_title": str(row.get("doc_title") or ""),
            "chunk_id": str(row.get("chunk_id") or (refs[0] if refs else "")),
            "section": str(row.get("section") or ""),
            "page_start": row.get("page_start"), "page_end": row.get("page_end"),
            "line_start": row.get("line_start"), "line_end": row.get("line_end"),
            "quote": str(row.get("quote") or ""),
        }

    def base(rule_id: str, rule_type: str, syndrome: Dict[str, Any], name: str,
             conditions: Dict[str, Any], conclusion: Dict[str, Any], edges: List[Dict[str, Any]]):
        return Rule(
            rule_id=rule_id, domain="tcm", anchor_type="syndrome", rule_type=rule_type,
            name=name, disease_code=str(syndrome.get("code") or ""),
            disease_name=str(syndrome.get("name") or ""), priority=40, status="draft",
            graph_filename=graph_filename, conditions=conditions, conclusion=conclusion,
            source=source_for(edges),
            clinical_context={"generated_from_graph": True, "requires_human_logic_review": True},
            tags=["auto-generated", "tcm-graph-basis", doc_id],
        ).model_dump()

    candidates: List[Dict[str, Any]] = []
    syndromes = [node for node in nodes.values() if node.get("node_type") == "syndrome"]
    for syndrome in syndromes:
        sid, scode = syndrome["id"], str(syndrome.get("code") or "")
        observations = [edge for edge in outgoing(sid) if edge.get("relation_code") in OBSERVATION_RELATIONS]
        children = []
        for edge in observations:
            target = nodes.get(edge.get("target_node_id"), {})
            kind = OBSERVATION_RELATIONS[edge["relation_code"]]
            children.append({
                "type": "observation", "observation_type": kind,
                "code": target.get("code"), "weight": 1.0,
                "label": target.get("display_name") or target.get("name"),
            })
        if children:
            candidates.append(base(
                f"{doc_id}-GRAPH-PATTERN-{scode}", "tcm_pattern", syndrome,
                f"辨证依据：{syndrome.get('name') or scode}",
                {"type": "weighted_group", "scoring": "normalized_weight_sum",
                 "threshold": 0.6, "children": children},
                {"diagnosis": scode, "confidence": "medium",
                 "message": f"图谱表现支持证候：{syndrome.get('name') or scode}"},
                observations,
            ))

        method_edges = outgoing(sid, ["SYNDROME_TREATED_BY_METHOD"])
        for edge in method_edges:
            method = nodes.get(edge.get("target_node_id"), {})
            mcode = str(method.get("code") or "")
            candidates.append(base(
                f"{doc_id}-GRAPH-TREAT-{scode}-{mcode}", "tcm_treatment", syndrome,
                f"治法依据：{syndrome.get('name') or scode}→{method.get('name') or mcode}",
                {"type": "fact", "fact_type": "syndrome", "code": scode},
                {"diagnosis": scode, "confidence": "medium", "conclusion_type": "treatment",
                 "treatment_method_code": mcode, "treatment_name": method.get("name"),
                 "message": f"证候成立时采用治法：{method.get('name') or mcode}"},
                [edge],
            ))

        formula_edges = outgoing(sid, ["SYNDROME_RECOMMENDS_FORMULA"])
        for edge in formula_edges:
            formula = nodes.get(edge.get("target_node_id"), {})
            fcode = str(formula.get("code") or "")
            herb_edges = outgoing(formula.get("id", ""), ["FORMULA_CONTAINS_HERB"])
            herbs = []
            for herb_edge in herb_edges:
                herb = nodes.get(herb_edge.get("target_node_id"), {})
                attrs = herb.get("attributes") or {}
                herbs.append({"code": herb.get("code"), "name": herb.get("name"),
                              "dose": attrs.get("HERB_DOSE", ""), "role": attrs.get("HERB_ROLE", "")})
            method_code = ""
            chain_edges: List[Dict[str, Any]] = []
            for method_edge in method_edges:
                matches = outgoing(method_edge.get("target_node_id", ""), ["METHOD_HAS_FORMULA"])
                match = next((item for item in matches if item.get("target_node_id") == formula.get("id")), None)
                if match:
                    method_code = str(nodes.get(method_edge.get("target_node_id"), {}).get("code") or "")
                    chain_edges = [method_edge, match]
                    break
            candidates.append(base(
                f"{doc_id}-GRAPH-RX-{scode}-{fcode}", "tcm_prescription", syndrome,
                f"方药依据：{syndrome.get('name') or scode}→{formula.get('name') or fcode}",
                {"type": "fact", "fact_type": "syndrome", "code": scode},
                {"diagnosis": scode, "confidence": "medium", "conclusion_type": "prescription",
                 "treatment_method_code": method_code or None, "formula_code": fcode,
                 "formula_name": formula.get("name"), "herbs": herbs,
                 "message": f"证候成立时推荐方剂：{formula.get('name') or fcode}"},
                [edge, *chain_edges, *herb_edges],
            ))
    return candidates
