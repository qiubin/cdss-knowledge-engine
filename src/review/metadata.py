"""Content fingerprint and review metadata for graph import."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

_DOC_RE = re.compile(r"^(DOC-[A-Za-z0-9]+)")


def derive_doc_ids(source_refs) -> List[str]:
    docs: List[str] = []
    for ref in source_refs or []:
        match = _DOC_RE.match(str(ref))
        if match and match.group(1) not in docs:
            docs.append(match.group(1))
    return docs


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(payload: Dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def node_content_hash(node: Dict[str, Any]) -> str:
    return _hash({
        "node_type": node.get("node_type"),
        "code": node.get("code"),
        "name": node.get("name"),
        "attributes": node.get("attributes") or {},
        "source_refs": sorted(node.get("source_refs") or []),
    })


def relation_content_hash(relation: Dict[str, Any]) -> str:
    return _hash({
        "source_node_id": relation.get("source_node_id"),
        "relation_code": relation.get("relation_code"),
        "target_node_id": relation.get("target_node_id"),
        "attributes": relation.get("attributes") or {},
        "source_refs": sorted(relation.get("source_refs") or []),
    })


def _node_items(graph: Dict[str, Any]):
    nodes = graph.get("nodes") or {}
    return nodes.values() if isinstance(nodes, dict) else nodes


def _relation_items(graph: Dict[str, Any]):
    relations = graph.get("relations") or {}
    if isinstance(relations, dict):
        return list(relations.items())
    return [
        ((item.get("source_node_id"), item.get("relation_code"), item.get("target_node_id")), item)
        for item in relations
    ]


def snapshot_hashes(graph: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    return {
        "nodes": {str(node.get("id")): node_content_hash(node) for node in _node_items(graph)},
        "relations": {
            "|".join(str(part) for part in key): relation_content_hash(item)
            for key, item in _relation_items(graph)
        },
    }


def ensure_metadata(graph: Dict[str, Any], now: Optional[str] = None) -> None:
    now = now or _now()
    for node in _node_items(graph):
        node.setdefault("review_status", "draft")
        node.setdefault("review_note", "")
        node.setdefault("reviewed_by", "")
        node.setdefault("reviewed_at", "")
        node.setdefault("version", 1)
        node["content_hash"] = node_content_hash(node)
        if not node.get("source_doc_ids"):
            node["source_doc_ids"] = derive_doc_ids(node.get("source_refs"))
        node.setdefault("orphaned_from", [])
    for key, relation in _relation_items(graph):
        relation.setdefault("review_status", "draft")
        relation.setdefault("review_note", "")
        relation.setdefault("reviewed_by", "")
        relation.setdefault("reviewed_at", "")
        relation.setdefault("version", 1)
        relation["content_hash"] = relation_content_hash(relation)
        if not relation.get("source_doc_ids"):
            relation["source_doc_ids"] = derive_doc_ids(relation.get("source_refs"))
        relation.setdefault("orphaned_from", [])


def _update_orphan(
    item: Dict[str, Any],
    doc_id: str,
    current_chunk_ids: Optional[Set[str]],
) -> None:
    """Track guideline sources that no longer reference this item."""
    if current_chunk_ids is None:
        return
    refs = set(item.get("source_refs") or [])
    seen = bool(refs & current_chunk_ids)
    contributed = (
        doc_id in set(item.get("source_doc_ids") or [])
        or doc_id in derive_doc_ids(item.get("source_refs"))
    )
    orphaned = set(item.get("orphaned_from") or [])
    if seen:
        orphaned.discard(doc_id)
    elif contributed:
        orphaned.add(doc_id)
    item["orphaned_from"] = sorted(orphaned)


def apply_import_review_metadata(
    graph: Dict[str, Any],
    previous_hashes: Dict[str, Dict[str, str]],
    doc_id: str,
    now: Optional[str] = None,
    current_chunk_ids: Optional[Set[str]] = None,
) -> None:
    now = now or _now()
    for node in _node_items(graph):
        node_id = str(node.get("id"))
        old = previous_hashes.get("nodes", {}).get(node_id)
        new = node_content_hash(node)
        current = str(node.get("review_status") or "draft")
        if old is not None and old != new and current == "active":
            node["review_status"] = "stale"
        if old is None:
            node["version"] = int(node.get("version") or 1)
        elif old != new:
            node["version"] = int(node.get("version") or 1) + 1
        node["content_hash"] = new
        if old is None or old != new:
            node["last_imported_at"] = now
            docs = set(node.get("source_doc_ids") or [])
            docs.add(doc_id)
            node["source_doc_ids"] = sorted(docs)
        node.setdefault("review_status", "draft")
        _update_orphan(node, doc_id, current_chunk_ids)
    node_status = {
        str(node.get("id")): str(node.get("review_status") or "draft")
        for node in _node_items(graph)
    }
    for key, relation in _relation_items(graph):
        key_text = "|".join(str(part) for part in key)
        old = previous_hashes.get("relations", {}).get(key_text)
        new = relation_content_hash(relation)
        current = str(relation.get("review_status") or "draft")
        if old is not None and old != new and current == "active":
            relation["review_status"] = "stale"
        if old is None:
            relation["version"] = int(relation.get("version") or 1)
        elif old != new:
            relation["version"] = int(relation.get("version") or 1) + 1
        relation["content_hash"] = new
        if old is None or old != new:
            relation["last_imported_at"] = now
            docs = set(relation.get("source_doc_ids") or [])
            docs.add(doc_id)
            relation["source_doc_ids"] = sorted(docs)
        relation.setdefault("review_status", "draft")
        # A relation is only usable when both endpoints are active.
        endpoint_active = (
            node_status.get(str(relation.get("source_node_id"))) == "active"
            and node_status.get(str(relation.get("target_node_id"))) == "active"
        )
        if not endpoint_active and relation.get("review_status") == "active":
            relation["review_status"] = "stale"
        _update_orphan(relation, doc_id, current_chunk_ids)
