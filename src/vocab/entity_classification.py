"""Specialty and disease classification for shared entity vocabulary entries.

The vocabulary is intentionally kept independent from individual templates.
This module provides a read-time projection: explicit metadata wins, otherwise
disease-to-entity links in the knowledge graphs are used to infer a useful
classification for the entity browser.
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
KG_DIR = ROOT / "data" / "kg"

# Coarse mapping from ICD-10 chapters to a default specialty. This is only a
# fallback for the browser; hospitals can override ``specialty`` on an entry.
_ICD10_SPECIALTY = {
    "A": "感染科",
    "B": "感染科",
    "C": "血液肿瘤",
    "D": "血液肿瘤",
    "E": "内分泌科",
    "F": "精神心理科",
    "G": "神经内科",
    "H": "眼科/耳鼻喉",
    "I": "心血管内科",
    "J": "呼吸内科",
    "K": "消化内科",
    "L": "皮肤科",
    "M": "风湿免疫/骨科",
    "N": "肾内科",
    "O": "妇产科",
    "P": "儿科",
    "Q": "儿科/遗传",
    "R": "通用",
    "S": "骨科/创伤",
    "T": "急诊/中毒",
    "U": "特殊用途",
    "V": "急诊/外伤",
    "W": "急诊/外伤",
    "X": "急诊/外伤",
    "Y": "急诊/外伤",
    "Z": "通用",
}

_MAX_GRAPH_DEPTH = 4
_GRAPH_INDEX_CACHE: Dict[Tuple[Tuple[str, int], ...], Dict[Tuple[str, str], Dict[str, Set[str]]]] = {}


def _specialty_for_disease(code: str, name: str = "") -> str:
    code = str(code or "").strip().upper()
    if code:
        mapped = _ICD10_SPECIALTY.get(code[0])
        if mapped:
            return mapped
    if any(token in name for token in ("淋巴", "白血病", "骨髓瘤", "血液")):
        return "血液肿瘤"
    if any(token in name for token in ("心肌", "心包", "心力衰竭", "心梗", "主动脉", "肺栓塞")):
        return "心血管内科"
    return "未分类"


def _graph_signature() -> Tuple[Tuple[str, int], ...]:
    if not KG_DIR.exists():
        return ()
    return tuple(
        (str(path), path.stat().st_mtime_ns)
        for path in sorted(KG_DIR.glob("*.json"))
    )


def _build_graph_index() -> Dict[Tuple[str, str], Dict[str, Set[str]]]:
    index: Dict[Tuple[str, str], Dict[str, Set[str]]] = defaultdict(
        lambda: {"diseases": set(), "specialties": set()}
    )
    if not KG_DIR.exists():
        return index

    for path in sorted(KG_DIR.glob("*.json")):
        try:
            graph = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        nodes = graph.get("nodes") or []
        if isinstance(nodes, dict):
            nodes = list(nodes.values())
        nodes_by_id = {
            str(node.get("id")): node
            for node in nodes
            if isinstance(node, dict) and node.get("id")
        }
        outgoing: Dict[str, List[str]] = defaultdict(list)
        for relation in graph.get("relations") or []:
            source = str(relation.get("source_node_id") or "")
            target = str(relation.get("target_node_id") or "")
            if source and target:
                outgoing[source].append(target)

        disease_nodes = [
            node for node in nodes_by_id.values()
            if str(node.get("node_type") or "") == "disease"
        ]
        for disease in disease_nodes:
            disease_id = str(disease.get("id"))
            disease_code = str(disease.get("code") or "")
            disease_name = str(
                disease.get("name")
                or disease.get("display_name")
                or disease_code
            )
            specialty = _specialty_for_disease(disease_code, disease_name)
            if disease_code:
                entry = index[("disease", disease_code)]
                entry["diseases"].add(disease_name)
                if specialty != "未分类":
                    entry["specialties"].add(specialty)

            seen = {disease_id}
            queue = deque([(disease_id, 0)])
            while queue:
                current_id, depth = queue.popleft()
                if depth >= _MAX_GRAPH_DEPTH:
                    continue
                for target_id in outgoing.get(current_id, ()):
                    if target_id in seen:
                        continue
                    seen.add(target_id)
                    target = nodes_by_id.get(target_id)
                    if target:
                        entity_type = str(target.get("node_type") or "")
                        entity_code = str(target.get("code") or "")
                        if entity_type and entity_code and entity_type != "disease":
                            entry = index[(entity_type, entity_code)]
                            entry["diseases"].add(disease_name)
                            if specialty != "未分类":
                                entry["specialties"].add(specialty)
                    queue.append((target_id, depth + 1))
    return index


def _graph_disease_index() -> Dict[Tuple[str, str], Dict[str, Set[str]]]:
    signature = _graph_signature()
    cached = _GRAPH_INDEX_CACHE.get(signature)
    if cached is not None:
        return cached
    index = _build_graph_index()
    _GRAPH_INDEX_CACHE.clear()
    _GRAPH_INDEX_CACHE[signature] = index
    return index


def _unique_sorted(values: Iterable[str]) -> List[str]:
    return sorted(
        {str(value).strip() for value in values if str(value).strip()},
        key=lambda value: value.casefold(),
    )


def classify_entries(entity_type: str, entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return entries with ``specialties``/``diseases`` filter projections.

    Explicit ``specialty``/``disease_tags`` always win. When absent, disease
    links reachable from a disease node in the knowledge graph provide the
    fallback. The original explicit fields are preserved so the editor can
    distinguish local overrides from derived values.
    """
    graph_index = _graph_disease_index()
    result: List[Dict[str, Any]] = []
    for raw in entries:
        item = dict(raw)
        code = str(item.get("code") or "")
        name = str(item.get("name") or "")
        manual_specialty = str(item.get("specialty") or "").strip()
        manual_diseases = _unique_sorted(item.get("disease_tags") or [])
        derived = graph_index.get((entity_type, code), {})
        derived_specialties = _unique_sorted(derived.get("specialties", set()))
        derived_diseases = _unique_sorted(derived.get("diseases", set()))

        if entity_type == "disease":
            if not derived_diseases:
                derived_diseases = [name or code]
            if not derived_specialties:
                derived_specialties = [_specialty_for_disease(code, name)]

        specialties = [manual_specialty] if manual_specialty else derived_specialties
        diseases = manual_diseases if manual_diseases else derived_diseases
        item["specialty"] = manual_specialty
        item["disease_tags"] = manual_diseases
        item["specialties"] = specialties or ["未分类"]
        item["diseases"] = diseases or ["未分类"]
        item["classification_source"] = (
            "manual" if manual_specialty or manual_diseases
            else "derived" if derived_specialties or derived_diseases
            else "default"
        )
        result.append(item)
    return result
