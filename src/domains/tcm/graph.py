"""TCM graph semantic-layer derivation.

TCM graphs are already node/relation native: a guideline section produces
syndrome, symptom, tongue, pulse, treatment-method, formula and herb nodes.
Unlike western graphs there is no diagnostic-evidence projection; the only
derived work is ensuring anchor nodes exist and dangling relations are dropped.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ...terminology.labels import default_labels


def _node_id(node_type: str, code: str) -> str:
    return f"ENTITY:{node_type}:{code}"


def _display_name(node_type: str, code: str, name: str = "") -> str:
    """Pair the full term with its code: 气虚证（TCM_SYN_QI_XU）. """
    code = str(code or "").strip()
    name = str(name or "").strip() or default_labels().name(node_type, code)
    if name and code and name != code:
        return f"{name}（{code}）"
    return name or code


def _as_index(value: Any) -> Dict[str, Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    return {
        str(item.get("id")): item
        for item in (value or [])
        if isinstance(item, dict) and item.get("id")
    }


def _upsert_anchor(
    nodes: Dict[str, Dict[str, Any]],
    node_type: str,
    code: str,
    name: str = "",
) -> Dict[str, Any]:
    node_id = _node_id(node_type, code)
    node = nodes.get(node_id)
    if node is None:
        node_name = name or code
        node = {
            "id": node_id,
            "node_type": node_type,
            "code": code,
            "concept_code": "",
            "name": node_name,
            "display_name": _display_name(node_type, code, node_name),
            "attributes": {},
            "source_refs": [],
            "review_status": "draft",
        }
        nodes[node_id] = node
    elif name and (not node.get("name") or node.get("name") == code):
        node["name"] = name
        node["display_name"] = _display_name(node_type, code, name)
    return node


def ensure_tcm_semantic_layer(graph: Dict[str, Any], graph_filename: str = "") -> None:
    nodes = _as_index(graph.setdefault("nodes", {}))
    relations = _as_index(graph.setdefault("relations", {}))
    graph["nodes"] = nodes
    graph["relations"] = relations

    for disease in graph.get("diseases", []):
        code = str(disease.get("code") or "")
        if code:
            _upsert_anchor(
                nodes, "disease", code,
                str(disease.get("name_cn") or disease.get("name") or code),
            )

    for syndrome in graph.get("syndromes", []):
        code = str(syndrome.get("code") or "")
        if code:
            _upsert_anchor(
                nodes, "syndrome", code,
                str(syndrome.get("name_cn") or syndrome.get("name") or code),
            )

    for key, relation in list(relations.items()):
        source = relation.get("source_node_id")
        target = relation.get("target_node_id")
        if source not in nodes or target not in nodes:
            relations.pop(key, None)

    graph["_repair_stats"] = {"mode": "tcm", "dangling_relations_removed": 0}
