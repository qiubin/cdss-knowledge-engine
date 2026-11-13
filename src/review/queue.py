"""Unified review queue across graph items, rules and knowledge packages."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from src.api.routes.kg import normalize_graph_filename
from src.api.routes.rules import _load_all_rules, check_rule_sync
from src.domains import normalize_domain
from src.review.service import ReviewService


def _package_items(packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = []
    for package in packages:
        items.append({
            "item_type": "package",
            "item_id": package.get("package_id", ""),
            "package_id": package.get("package_id", ""),
            "doc_title": package.get("doc_title", ""),
            "status": package.get("status", ""),
            "domain": normalize_domain(package.get("domain") or "western"),
            "graph_filename": package.get("graph_filename", ""),
            "entity_count": len(package.get("entities") or []),
            "relation_count": len(package.get("suggested_relations") or []),
            "rule_count": len(package.get("suggested_rules") or []),
            "check_summary": package.get("check_summary") or {},
        })
    return items


def build_queue(
    service: Optional[ReviewService] = None,
    packages: Optional[List[Dict[str, Any]]] = None,
    graph_files: Optional[List[str]] = None,
    domain: Optional[str] = None,
    graph_filename: Optional[str] = None,
    status: Optional[str] = None,
    item_type: Optional[str] = None,
    node_type: Optional[str] = None,
    source_doc: Optional[str] = None,
    q: Optional[str] = None,
    relation_code: Optional[str] = None,
    clinical_intent: Optional[str] = None,
    limit: int = 300,
) -> Dict[str, Any]:
    service = service or ReviewService()
    if graph_files is None:
        graph_files = sorted(path.name for path in service.graph_dir.glob("*.json"))
    if graph_filename:
        graph_files = [normalize_graph_filename(graph_filename)]
    target_domain = normalize_domain(domain) if domain else ""
    query = str(q or "").strip().lower()
    # "graph" is a convenience alias covering nodes and relations, so the
    # review center can fetch one tab's payload without pulling rules too.
    want_graph = not clinical_intent and item_type in (None, "", "graph", "graph_node", "graph_relation")
    want_rules = item_type in (None, "", "rule")
    want_packages = not clinical_intent and item_type in (None, "", "package")

    items: List[Dict[str, Any]] = []
    for filename in graph_files:
        graph = service._load_graph(filename)
        graph_domain = normalize_domain(graph.get("domain") or "western")
        if target_domain and graph_domain != target_domain:
            continue
        if want_graph:
            wanted = (
                "node" if item_type == "graph_node"
                else "relation" if item_type == "graph_relation"
                else None
            )
            for item in service.list_items(
                filename, status=status, item_type=wanted, node_type=node_type, source_doc=source_doc, q=q,
                relation_code=relation_code,
            ):
                items.append({
                    **item,
                    "item_type": "graph_node" if item["item_type"] == "node" else "graph_relation",
                    "graph_filename": filename,
                    "domain": graph_domain,
                })

    rules = service._load_rules()
    if want_rules and rules:
        sync = check_rule_sync(rules, graph_loader=service._load_graph)
        by_id = {item["rule_id"]: item for item in sync["rules"]}
    else:
        by_id = {}
    for rule in rules:
        rule_domain = normalize_domain(rule.get("domain") or "western")
        if target_domain and rule_domain != target_domain:
            continue
        if graph_filename:
            try:
                rule_graph = normalize_graph_filename(
                    rule.get("graph_filename") or "stemi_graph.json"
                )
            except Exception:
                rule_graph = ""
            if rule_graph != normalize_graph_filename(graph_filename):
                continue
        result = by_id.get(rule.get("rule_id"), {})
        refs = result.get("references") or []
        statuses = [ref.get("status") for ref in refs]
        dependency_summary = {
            "total": len(refs),
            "active": sum(value == "active" for value in statuses),
            "blocked": result.get("dependency_status") != "passed",
        }
        if status and rule.get("status") != status:
            continue
        if item_type and item_type != "rule":
            continue
        if source_doc and (rule.get("source") or {}).get("guideline_id") != source_doc:
            continue
        if clinical_intent and rule.get("clinical_intent") != clinical_intent:
            continue
        text = f"{rule.get('rule_id', '')} {rule.get('name', '')}".lower()
        if query and query not in text:
            continue
        items.append({
            "item_type": "rule",
            "item_id": rule.get("rule_id", ""),
            "rule_id": rule.get("rule_id", ""),
            "name": rule.get("name", ""),
            "rule_type": rule.get("rule_type", "diagnosis"),
            "clinical_intent": rule.get("clinical_intent", ""),
            "status": rule.get("status", ""),
            "domain": rule_domain,
            "graph_filename": rule.get("graph_filename", ""),
            "dependency_summary": dependency_summary,
            "issues": result.get("issues", []),
        })

    if packages is not None and want_packages:
        for package in _package_items(packages):
            if status and package["status"] != status:
                continue
            if target_domain and package["domain"] != target_domain:
                continue
            if query and query not in (
                f"{package.get('package_id', '')} {package.get('doc_title', '')}".lower()
            ):
                continue
            items.append(package)

    status_order = {
        "draft": 0, "stale": 1, "blocked": 2, "rejected": 3,
        "active": 4, "retired": 5,
    }

    def _sort_key(item: Dict[str, Any]):
        return (
            status_order.get(item.get("status"), 9),
            item.get("graph_filename", ""),
            item.get("name", ""),
        )

    # Slice per item type so a flood of graph entries cannot starve rules (or
    # packages) out of the page: each category gets up to ``limit`` slots.
    safe_limit = max(1, int(limit))
    limited: List[Dict[str, Any]] = []
    truncated: Dict[str, bool] = {}
    for kind in ("graph_node", "graph_relation", "rule", "package"):
        subset = sorted(
            (item for item in items if item["item_type"] == kind), key=_sort_key,
        )
        limited.extend(subset[:safe_limit])
        truncated[kind] = len(subset) > safe_limit
    summary = {
        "graph_nodes": dict(Counter(
            item["status"] for item in items if item["item_type"] == "graph_node"
        )),
        "graph_relations": dict(Counter(
            item["status"] for item in items if item["item_type"] == "graph_relation"
        )),
        "rules": dict(Counter(
            item["status"] for item in items if item["item_type"] == "rule"
        )),
        "packages": dict(Counter(
            item["status"] for item in items if item["item_type"] == "package"
        )),
        "total": len(items),
    }
    return {"summary": summary, "truncated": truncated, "items": limited}
