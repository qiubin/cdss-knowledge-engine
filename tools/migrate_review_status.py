#!/usr/bin/env python3
"""Bootstrap review metadata and approve dependencies of active rules once."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable

from src.api.routes.kg import DEFAULT_GRAPH_FILENAME, KG_DIR, _load_graph, _save_graph, normalize_graph_filename
from src.api.routes.rules import MANAGED_RULES_PATH, _load_all_rules, _save_all_rules, check_rule_sync
from src.review.audit import AUDIT_PATH, append_audit
from src.review.metadata import ensure_metadata


def _node_items(graph: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    nodes = graph.get("nodes") or {}
    return nodes.values() if isinstance(nodes, dict) else nodes


def _relation_items(graph: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    relations = graph.get("relations") or {}
    return relations.values() if isinstance(relations, dict) else relations


def _set_node_status(graph: Dict[str, Any], node_id: str, status: str) -> bool:
    for node in _node_items(graph):
        if node.get("id") == node_id:
            if str(node.get("review_status") or "draft") != status:
                node["review_status"] = status
                return True
            return False
    return False


def _set_relation_status(graph: Dict[str, Any], key, status: str) -> bool:
    for relation in _relation_items(graph):
        current = (
            relation.get("source_node_id"),
            relation.get("relation_code"),
            relation.get("target_node_id"),
        )
        if current == key:
            if str(relation.get("review_status") or "draft") != status:
                relation["review_status"] = status
                return True
            return False
    return False


def migrate(
    graph_dir=KG_DIR,
    rules_path=MANAGED_RULES_PATH,
    audit_path=AUDIT_PATH,
    dry_run: bool = False,
) -> Dict[str, int]:
    graph_dir = Path(graph_dir)
    rules_path = Path(rules_path)
    rules = _load_all_rules(rules_path)
    stats = {
        "graphs": 0,
        "bootstrapped_nodes": 0,
        "bootstrapped_relations": 0,
        "blocked_rules": 0,
    }

    def loader(name: str) -> Dict[str, Any]:
        return _load_graph(name, kg_dir=graph_dir)

    for graph_path in sorted(graph_dir.glob("*.json")):
        filename = normalize_graph_filename(graph_path.name)
        graph = loader(filename)
        ensure_metadata(graph)
        graph_rules = []
        for rule in rules:
            try:
                rule_filename = normalize_graph_filename(
                    rule.get("graph_filename") or DEFAULT_GRAPH_FILENAME
                )
            except Exception:
                continue
            if rule_filename == filename:
                graph_rules.append(rule)
        sync = check_rule_sync(graph_rules, graph_loader=loader)
        for result in sync["rules"]:
            if result.get("semantic_status") != "consistent":
                continue
            if result.get("dependency_status") == "passed":
                continue
            for ref in result.get("references") or []:
                if ref.get("status") == "active":
                    continue
                if ref.get("kind") == "node":
                    if _set_node_status(graph, ref["node_id"], "active"):
                        stats["bootstrapped_nodes"] += 1
                        if not dry_run:
                            append_audit({
                                "action": "migration_bootstrap",
                                "entity_type": "node",
                                "entity_id": ref["node_id"],
                                "graph_filename": filename,
                                "before_status": ref.get("status"),
                                "after_status": "active",
                                "reviewer": "system",
                                "note": "迁移：保持现有已启用规则的依赖可用",
                            }, audit_path)
                else:
                    key = (
                        ref["source_node_id"],
                        ref["relation_code"],
                        ref["target_node_id"],
                    )
                    if _set_relation_status(graph, key, "active"):
                        stats["bootstrapped_relations"] += 1
                        if not dry_run:
                            append_audit({
                                "action": "migration_bootstrap",
                                "entity_type": "relation",
                                "entity_id": "|".join(key),
                                "graph_filename": filename,
                                "before_status": ref.get("status"),
                                "after_status": "active",
                                "reviewer": "system",
                                "note": "迁移：保持现有已启用规则的依赖可用",
                            }, audit_path)
        if not dry_run:
            _save_graph(graph, filename, kg_dir=graph_dir)
        stats["graphs"] += 1

    if not dry_run:
        for rule in rules:
            if rule.get("status") != "active":
                continue
            try:
                filename = normalize_graph_filename(
                    rule.get("graph_filename") or DEFAULT_GRAPH_FILENAME
                )
            except Exception:
                filename = DEFAULT_GRAPH_FILENAME
            result = check_rule_sync([rule], graph_loader=loader)["rules"][0]
            if result.get("dependency_status") != "passed":
                rule["status"] = "blocked"
                rule["blocked_by"] = [
                    {
                        "entity_type": ref.get("kind"),
                        "entity_id": ref.get("node_id") or "|".join(filter(None, [
                            ref.get("source_node_id"),
                            ref.get("relation_code"),
                            ref.get("target_node_id"),
                        ])),
                        "reason": ref.get("status"),
                    }
                    for ref in result.get("references") or []
                    if ref.get("status") != "active"
                ]
                stats["blocked_rules"] += 1
        _save_all_rules(rules, rules_path)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-dir", default=str(KG_DIR))
    parser.add_argument("--rules-path", default=str(MANAGED_RULES_PATH))
    parser.add_argument("--audit-path", default=str(AUDIT_PATH))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        migrate(args.graph_dir, args.rules_path, args.audit_path, args.dry_run),
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
