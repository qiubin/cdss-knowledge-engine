"""Neo4j-backed generic knowledge graph visualization APIs."""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict, deque
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from neo4j import GraphDatabase

from src.domains import get_profile
from .kg import _load_graph, normalize_graph_filename

router = APIRouter(prefix="/api/kg/neo4j", tags=["kg-neo4j"])

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_BROWSER_URL = os.getenv("NEO4J_BROWSER_URL", "http://localhost:7474/browser/")

MAX_SYNC_BATCH = 500


def _driver():
    auth = None
    if NEO4J_USER and NEO4J_PASSWORD:
        auth = (NEO4J_USER, NEO4J_PASSWORD)
    try:
        return GraphDatabase.driver(NEO4J_URI, auth=auth, connection_timeout=2.0)
    except Exception as exc:
        raise HTTPException(503, f"无法连接 Neo4j：{exc}") from exc


def _safe_db_error(exc: Exception) -> HTTPException:
    message = str(exc).replace("\n", " ")
    if "Failed to establish connection" in message or "Cannot resolve address" in message:
        return HTTPException(503, "Neo4j 还没有就绪，请先启动 Neo4j 后重试。")
    return HTTPException(500, f"Neo4j 操作失败：{message}")


def _plain_property(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list) and all(isinstance(v, (str, int, float, bool)) or v is None for v in value):
        return [v for v in value if v is not None]
    return str(value)


def _node_payload(filename: str, node: Dict[str, Any], domain: str = "western") -> Dict[str, Any]:
    node_id = str(node.get("id") or "")
    uid = f"{filename}:{node_id}"
    source_refs = node.get("source_refs") or []
    if not isinstance(source_refs, list):
        source_refs = [source_refs]
    payload = {
        "uid": uid,
        "id": node_id,
        "graph_filename": filename,
        "domain": domain,
        "node_type": str(node.get("node_type") or "unknown"),
        "name": str(node.get("name") or ""),
        "code": str(node.get("code") or ""),
        "concept_code": str(node.get("concept_code") or ""),
        "review_status": str(node.get("review_status") or ""),
        "source_refs": [_plain_property(v) for v in source_refs if v is not None],
        "attributes_json": json.dumps(node.get("attributes") or {}, ensure_ascii=False),
    }
    return payload


def _relation_payload(filename: str, relation: Dict[str, Any], domain: str = "western") -> Dict[str, Any]:
    relation_code = str(relation.get("relation_code") or "").strip()
    if not relation_code:
        return {}
    # Neo4j relation labels are identifiers. Keep this deliberately conservative
    # for the generic template and map unsupported labels to RELATED_TO.
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", relation_code):
        relation_code = "RELATED_TO"
    source_refs = relation.get("source_refs") or []
    if not isinstance(source_refs, list):
        source_refs = [source_refs]
    return {
        "uid": f"{filename}:{relation.get('source_node_id')}:{relation_code}:{relation.get('target_node_id')}",
        "graph_filename": filename,
        "domain": domain,
        "relation_code": relation_code,
        "relation_name": str(relation.get("relation_name") or relation_code),
        "source_node_id": str(relation.get("source_node_id") or ""),
        "target_node_id": str(relation.get("target_node_id") or ""),
        "source_refs": [_plain_property(v) for v in source_refs if v is not None],
        "review_status": str(relation.get("review_status") or ""),
        "attributes_json": json.dumps(relation.get("attributes") or {}, ensure_ascii=False),
    }


@router.get("/status")
def neo4j_status():
    try:
        with _driver() as driver:
            driver.verify_connectivity()
            with driver.session() as session:
                counts = session.run(
                    """
                    MATCH (n:KGNode)
                    WITH count(n) AS nodes
                    MATCH ()-[r]->()
                    RETURN nodes, count(r) AS relations
                    """
                ).single()
                nodes = int(counts["nodes"]) if counts else 0
                relations = int(counts["relations"]) if counts else 0
            return {
                "available": True,
                "uri": NEO4J_URI,
                "browser_url": NEO4J_BROWSER_URL,
                "nodes": nodes,
                "relations": relations,
            }
    except HTTPException:
        raise
    except Exception as exc:
        detail = _safe_db_error(exc).detail
        return {"available": False, "error": detail, "browser_url": NEO4J_BROWSER_URL}


@router.post("/sync/{graph_filename}")
def neo4j_sync(graph_filename: str):
    return _sync_graph_to_neo4j(graph_filename)


def _sync_graph_to_neo4j(graph_filename: str) -> Dict[str, Any]:
    filename = normalize_graph_filename(graph_filename)
    graph = _load_graph(filename)
    raw_nodes = list(graph.get("nodes", {}).values()) if isinstance(graph.get("nodes"), dict) else graph.get("nodes", [])
    raw_relations = list(graph.get("relations", {}).values()) if isinstance(graph.get("relations"), dict) else graph.get("relations", [])
    domain = str(graph.get("domain") or "western")
    node_rows = [_node_payload(filename, node, domain) for node in raw_nodes if node.get("id")]
    relation_rows = []
    for relation in raw_relations:
        payload = _relation_payload(filename, relation, domain)
        if payload.get("source_node_id") and payload.get("target_node_id"):
            relation_rows.append(payload)

    known_ids = {row["id"] for row in node_rows}
    relation_rows = [row for row in relation_rows if row["source_node_id"] in known_ids and row["target_node_id"] in known_ids]

    try:
        with _driver() as driver:
            driver.verify_connectivity()
            with driver.session() as session:
                session.run(
                    """
                    CREATE CONSTRAINT kg_node_uid IF NOT EXISTS
                    FOR (n:KGNode) REQUIRE n.uid IS UNIQUE
                    """
                ).consume()
                session.run(
                    "MATCH (n:KGNode {graph_filename: $filename}) DETACH DELETE n",
                    filename=filename,
                ).consume()

                for start in range(0, len(node_rows), MAX_SYNC_BATCH):
                    rows = node_rows[start:start + MAX_SYNC_BATCH]
                    session.run(
                        """
                        UNWIND $rows AS row
                        MERGE (n:KGNode {uid: row.uid})
                        SET n = row
                        """,
                        rows=rows,
                    ).consume()

                grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
                for row in relation_rows:
                    grouped[row["relation_code"]].append(row)
                for relation_type, rows in grouped.items():
                    for start in range(0, len(rows), MAX_SYNC_BATCH):
                        batch = rows[start:start + MAX_SYNC_BATCH]
                        session.run(
                            f"""
                            UNWIND $rows AS row
                            MATCH (a:KGNode {{uid: row.graph_filename + ':' + row.source_node_id}})
                            MATCH (b:KGNode {{uid: row.graph_filename + ':' + row.target_node_id}})
                            MERGE (a)-[r:{relation_type}]->(b)
                            SET r = row
                            """,
                            rows=batch,
                        ).consume()

                stored_nodes = session.run(
                    "MATCH (n:KGNode {graph_filename: $filename}) RETURN count(n) AS count",
                    filename=filename,
                ).single()
                stored_relations = session.run(
                    "MATCH (:KGNode {graph_filename: $filename})-[r]->(:KGNode {graph_filename: $filename}) RETURN count(r) AS count",
                    filename=filename,
                ).single()
        return {
            "ok": True,
            "graph_filename": filename,
            "nodes": int(stored_nodes["count"]) if stored_nodes else 0,
            "relations": int(stored_relations["count"]) if stored_relations else 0,
            "browser_url": NEO4J_BROWSER_URL,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise _safe_db_error(exc) from exc


def _select_default_center(
    records: List[Dict[str, Any]],
    anchor_types: tuple[str, ...] = ("disease",),
) -> str:
    degrees: Dict[str, int] = defaultdict(int)
    node_index: Dict[str, Dict[str, Any]] = {}
    anchors = set(anchor_types)
    for record in records:
        source = record.get("source_node_id")
        target = record.get("target_node_id")
        degrees[source] += 1
        degrees[target] += 1
        if record.get("source_node_type") in anchors:
            node_index[source] = record["source"]
        if record.get("target_node_type") in anchors:
            node_index[target] = record["target"]
    anchor_ids = set(node_index)
    candidates = [(degrees.get(node_id, 0), node_id) for node_id in anchor_ids]
    if not candidates:
        candidates = [(degrees.get(node_id, 0), node_id) for node_id in node_index]
    if not candidates:
        return ""
    return node_index[max(candidates)[1]].get("code") or ""


def _serialize_node(record: Any) -> Dict[str, Any]:
    node = dict(record)
    node.pop("uid", None)
    node.pop("graph_filename", None)
    return node


def _serialize_relation(record: Any) -> Dict[str, Any]:
    return {
        "id": str(record.get("uid") or record.get("element_id") or ""),
        "source_node_id": record.get("source_node_id") or record.get("sourceId"),
        "target_node_id": record.get("target_node_id") or record.get("targetId"),
        "relation_code": record.get("relation_code") or record.type(),
        "relation_name": record.get("relation_name") or record.type(),
        "source_refs": record.get("source_refs") or [],
        "review_status": record.get("review_status") or "",
    }


@router.get("/graph")
def neo4j_graph(
    graph_filename: str = "",
    center_code: str = "",
    depth: int = 2,
    limit: int = 80,
):
    filename = normalize_graph_filename(graph_filename or "KG-GENERIC_GUIDELINE_V1.json")
    depth = min(max(depth, 1), 3)
    limit = min(max(limit, 10), 500)
    source_graph = _load_graph(filename)
    profile = get_profile(source_graph.get("domain"))
    anchor_types = tuple(profile.graph.anchor_types)
    try:
        with _driver() as driver:
            driver.verify_connectivity()
            with driver.session() as session:
                stored_record = session.run(
                    "MATCH (n:KGNode {graph_filename: $filename}) RETURN count(n) AS count",
                    filename=filename,
                ).single()
                stored_nodes = int(stored_record["count"]) if stored_record else 0

        auto_synced = False
        if stored_nodes == 0:
            _sync_graph_to_neo4j(filename)
            auto_synced = True

        with _driver() as driver:
            driver.verify_connectivity()
            with driver.session() as session:
                all_record = session.run(
                    """
                    MATCH (n:KGNode {graph_filename: $filename})
                    RETURN DISTINCT n ORDER BY n.name
                    """,
                    filename=filename,
                )
                all_nodes = [_serialize_node(item["n"]) for item in all_record]
                rel_record = session.run(
                    """
                    MATCH (a:KGNode {graph_filename: $filename})-[r]->(b:KGNode {graph_filename: $filename})
                    RETURN elementId(r) AS element_id, type(r) AS rel_type,
                           properties(r) AS props, elementId(a) AS source_element_id,
                           elementId(b) AS target_element_id
                    """,
                    filename=filename,
                )
                all_relations = []
                element_to_node = {node["id"]: node for node in all_nodes}
                for item in rel_record:
                    props = dict(item["props"])
                    source_id = props.get("source_node_id")
                    target_id = props.get("target_node_id")
                    if source_id not in element_to_node or target_id not in element_to_node:
                        continue
                    relation = {
                        "id": str(item["element_id"]),
                        "source_node_id": source_id,
                        "target_node_id": target_id,
                        "relation_code": item["rel_type"],
                        "relation_name": props.get("relation_name") or item["rel_type"],
                        "source_refs": props.get("source_refs") or [],
                        "review_status": props.get("review_status") or "",
                    }
                    all_relations.append(relation)

                if not center_code:
                    degree_parts = []
                    for relation in all_relations:
                        source = element_to_node.get(relation["source_node_id"], {})
                        target = element_to_node.get(relation["target_node_id"], {})
                        degree_parts.append({
                            "source": source,
                            "target": target,
                            "source_node_id": relation["source_node_id"],
                            "target_node_id": relation["target_node_id"],
                            "source_node_type": source.get("node_type"),
                            "target_node_type": target.get("node_type"),
                        })
                    center_code = _select_default_center(degree_parts, anchor_types)

                center_node = next((node for node in all_nodes if node.get("code") == center_code), None)
                if not center_node:
                    center_node = next(
                        (node for node in all_nodes if node.get("node_type") in anchor_types),
                        None,
                    )

                adjacency: Dict[str, List[str]] = defaultdict(list)
                for relation in all_relations:
                    adjacency[relation["source_node_id"]].append(relation["target_node_id"])
                    adjacency[relation["target_node_id"]].append(relation["source_node_id"])

                selected_ids = set()
                if center_node:
                    queue = deque([(center_node["id"], 0)])
                    selected_ids.add(center_node["id"])
                    while queue and len(selected_ids) < limit:
                        node_id, level = queue.popleft()
                        if level >= depth:
                            continue
                        for next_id in sorted(adjacency.get(node_id, [])):
                            if next_id not in selected_ids:
                                selected_ids.add(next_id)
                                queue.append((next_id, level + 1))
                            if len(selected_ids) >= limit:
                                break
                else:
                    selected_ids = {node["id"] for node in all_nodes[:limit]}

                visible_node_ids = selected_ids
                nodes = [node for node in all_nodes if node["id"] in visible_node_ids]
                relations = [
                    relation for relation in all_relations
                    if relation["source_node_id"] in visible_node_ids and relation["target_node_id"] in visible_node_ids
                ]

        return {
            "graph_filename": filename,
            "center_code": center_node.get("code") if center_node else "",
            "depth": depth,
            "limit": limit,
            "nodes": nodes,
            "relations": relations,
            "total_nodes": len(all_nodes),
            "total_relations": len(all_relations),
            "engine": "neo4j",
            "auto_synced": auto_synced,
            "browser_url": NEO4J_BROWSER_URL,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise _safe_db_error(exc) from exc
