"""Graph review operations: status transitions, impact, cascade and audit."""
from __future__ import annotations

import copy
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.api.routes.kg import (
    DEFAULT_GRAPH_FILENAME,
    KG_DIR,
    _load_graph,
    _save_graph,
    normalize_graph_filename,
)
from src.api.routes.rules import (
    MANAGED_RULES_PATH,
    _load_all_rules,
    _save_all_rules,
    check_rule_sync,
)
from .audit import AUDIT_PATH, append_audit, load_audit
from .errors import ReviewError
from .status import StatusTransitionError, next_graph_status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relation_key(relation: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(relation.get("source_node_id") or ""),
        str(relation.get("relation_code") or ""),
        str(relation.get("target_node_id") or ""),
    )


def _relation_id(key: Tuple[str, str, str]) -> str:
    return "|".join(key)


def _status(item: Dict[str, Any]) -> str:
    return str(item.get("review_status") or "draft")


class ReviewService:
    def __init__(
        self,
        graph_dir: Optional[Path] = None,
        rules_path: Optional[Path] = None,
        audit_path: Optional[Path] = None,
    ):
        self.graph_dir = Path(graph_dir) if graph_dir is not None else KG_DIR
        self.rules_path = Path(rules_path) if rules_path is not None else MANAGED_RULES_PATH
        self.audit_path = Path(audit_path) if audit_path is not None else AUDIT_PATH

    # ---- storage ----
    def _load_graph(self, filename: str) -> Dict[str, Any]:
        return _load_graph(filename, kg_dir=self.graph_dir)

    def _save_graph(self, graph: Dict[str, Any], filename: str) -> None:
        _save_graph(graph, filename, kg_dir=self.graph_dir)

    def _load_rules(self) -> List[Dict[str, Any]]:
        return _load_all_rules(self.rules_path)

    def _save_rules(self, rules: List[Dict[str, Any]]) -> None:
        _save_all_rules(rules, self.rules_path)

    def _rules_for_graph(
        self, rules: List[Dict[str, Any]], graph_filename: str
    ) -> List[Dict[str, Any]]:
        result = []
        for rule in rules:
            try:
                filename = normalize_graph_filename(
                    rule.get("graph_filename") or DEFAULT_GRAPH_FILENAME
                )
            except Exception:
                continue
            if filename == graph_filename:
                result.append(rule)
        return result

    def _sync(self, rules: List[Dict[str, Any]]) -> Dict[str, Any]:
        return check_rule_sync(rules, graph_loader=self._load_graph)

    # ---- audit ----
    def audit_records(self, **filters) -> List[Dict[str, Any]]:
        return load_audit(path=self.audit_path, **filters)

    def _persist(
        self,
        filename: str,
        previous_graph: Dict[str, Any],
        graph: Dict[str, Any],
        previous_rules: List[Dict[str, Any]],
        rules: List[Dict[str, Any]],
        audit_records: List[Dict[str, Any]],
    ) -> None:
        try:
            self._save_graph(graph, filename)
            self._save_rules(rules)
            for record in audit_records:
                append_audit(record, self.audit_path)
        except Exception as exc:
            self._save_graph(previous_graph, filename)
            self._save_rules(previous_rules)
            raise ReviewError(f"审核保存失败: {exc}", 500) from exc

    # ---- summaries / listings ----
    def summary(self, graph_filename: str) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        rules = self._rules_for_graph(self._load_rules(), filename)
        return {
            "graph_filename": filename,
            "nodes": dict(Counter(_status(item) for item in graph["nodes"].values())),
            "relations": dict(Counter(_status(item) for item in graph["relations"].values())),
            "rules": dict(Counter(str(item.get("status") or "draft") for item in rules)),
            "blocked_rules": sum(1 for item in rules if item.get("status") == "blocked"),
        }

    def _dependency_index(
        self, filename: str
    ) -> Tuple[Counter, Counter, Dict[str, Dict[str, Any]]]:
        rules = self._rules_for_graph(self._load_rules(), filename)
        sync = self._sync(rules)
        node_counts: Counter = Counter()
        relation_counts: Counter = Counter()
        by_rule: Dict[str, Dict[str, Any]] = {}
        for result in sync["rules"]:
            by_rule[result["rule_id"]] = result
            for ref in result.get("references") or []:
                if ref.get("kind") == "node":
                    node_counts[ref.get("node_id")] += 1
                else:
                    relation_counts[
                        _relation_id((
                            ref.get("source_node_id"),
                            ref.get("relation_code"),
                            ref.get("target_node_id"),
                        ))
                    ] += 1
        return node_counts, relation_counts, by_rule

    def list_items(
        self,
        graph_filename: str,
        status: Optional[str] = None,
        item_type: Optional[str] = None,
        node_type: Optional[str] = None,
        source_doc: Optional[str] = None,
        referenced_by_rule: Optional[bool] = None,
        q: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        node_counts, relation_counts, _ = self._dependency_index(filename)
        query = str(q or "").strip().lower()
        items: List[Dict[str, Any]] = []
        if item_type in (None, "", "node"):
            for node_id, node in graph["nodes"].items():
                items.append({
                    "item_type": "node",
                    "item_id": node_id,
                    "node_id": node_id,
                    "node_type": str(node.get("node_type") or ""),
                    "code": str(node.get("code") or node.get("concept_code") or ""),
                    "name": str(node.get("name") or ""),
                    "status": _status(node),
                    "source_refs": list(node.get("source_refs") or []),
                    "dependent_rule_count": int(node_counts.get(node_id, 0)),
                    "updated_at": str(node.get("reviewed_at") or ""),
                    "orphaned_from": list(node.get("orphaned_from") or []),
                    "orphaned": bool(node.get("orphaned_from")),
                })
        if item_type in (None, "", "relation"):
            nodes_index = graph["nodes"]
            for key, relation in graph["relations"].items():
                source_node = nodes_index.get(key[0]) or {}
                target_node = nodes_index.get(key[2]) or {}
                items.append({
                    "item_type": "relation",
                    "item_id": _relation_id(key),
                    "source_node_id": key[0],
                    "relation_code": key[1],
                    "target_node_id": key[2],
                    "node_type": "relation",
                    "name": str(relation.get("relation_name") or key[1]),
                    "status": _status(relation),
                    "source_name": str(source_node.get("name") or key[0]),
                    "source_code": str(source_node.get("code") or source_node.get("concept_code") or ""),
                    "source_status": _status(source_node) if source_node else "missing",
                    "target_name": str(target_node.get("name") or key[2]),
                    "target_code": str(target_node.get("code") or target_node.get("concept_code") or ""),
                    "target_status": _status(target_node) if target_node else "missing",
                    "source_refs": list(relation.get("source_refs") or []),
                    "dependent_rule_count": int(relation_counts.get(_relation_id(key), 0)),
                    "updated_at": str(relation.get("reviewed_at") or ""),
                    "orphaned_from": list(relation.get("orphaned_from") or []),
                    "orphaned": bool(relation.get("orphaned_from")),
                })
        filtered = []
        for item in items:
            if status and item["status"] != status:
                continue
            if node_type and item.get("node_type") != node_type:
                continue
            if source_doc and not any(str(ref).startswith(source_doc) for ref in item["source_refs"]):
                continue
            if referenced_by_rule is True and item["dependent_rule_count"] <= 0:
                continue
            if referenced_by_rule is False and item["dependent_rule_count"] > 0:
                continue
            if query and query not in " ".join([
                item.get("name", ""), item.get("code", ""), item.get("item_id", ""),
            ]).lower():
                continue
            filtered.append(item)
        return sorted(
            filtered,
            key=lambda item: (
                item["item_type"], item.get("node_type", ""), item.get("name", ""),
            ),
        )

    def node_detail(self, graph_filename: str, node_id: str) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        node = graph["nodes"].get(node_id)
        if node is None:
            raise ReviewError("节点不存在", 404)

        def neighbor_summary(node_id: str) -> Dict[str, Any]:
            node = graph["nodes"].get(node_id)
            if node is None:
                return {"node_id": node_id, "name": node_id, "code": "", "node_type": "", "status": "missing"}
            return {
                "node_id": node_id,
                "name": str(node.get("name") or node_id),
                "code": str(node.get("code") or node.get("concept_code") or ""),
                "node_type": str(node.get("node_type") or ""),
                "status": _status(node),
            }

        relations = [
            {
                "key": "|".join(key),
                "relation": relation,
                "source": neighbor_summary(key[0]),
                "target": neighbor_summary(key[2]),
            }
            for key, relation in graph["relations"].items()
            if node_id in (key[0], key[2])
        ]
        return {
            "node": node,
            "relations": relations,
            "dependents": self.node_dependents(filename, node_id),
            "audit": self.audit_records(entity_id=node_id),
        }

    def relation_detail(self, graph_filename: str, relation_id: str) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        parts = relation_id.split("|")
        if len(parts) != 3:
            raise ReviewError("关系标识不合法", 422)
        key = (parts[0], parts[1], parts[2])
        relation = graph["relations"].get(key)
        if relation is None:
            raise ReviewError("关系不存在", 404)

        def endpoint_summary(node_id: str) -> Dict[str, Any]:
            node = graph["nodes"].get(node_id)
            if node is None:
                return {"node_id": node_id, "name": node_id, "code": "", "status": "missing"}
            return {
                "node_id": node_id,
                "name": str(node.get("name") or node_id),
                "code": str(node.get("code") or node.get("concept_code") or ""),
                "status": _status(node),
            }

        return {
            "relation": relation,
            "relation_id": _relation_id(key),
            "source": endpoint_summary(key[0]),
            "target": endpoint_summary(key[2]),
            "rules": self._rule_items_for_reference(
                filename,
                lambda ref: (
                    ref.get("kind") == "relation"
                    and ref.get("source_node_id") == key[0]
                    and ref.get("relation_code") == key[1]
                    and ref.get("target_node_id") == key[2]
                ),
            ),
            "audit": self.audit_records(entity_id=_relation_id(key)),
        }

    # ---- impact ----
    def _rule_items_for_reference(
        self, filename: str, predicate
    ) -> Dict[str, Any]:
        rules = self._rules_for_graph(self._load_rules(), filename)
        sync = self._sync(rules)
        dependent = []
        for result in sync["rules"]:
            if any(predicate(ref) for ref in result.get("references") or []):
                dependent.append({
                    "rule_id": result["rule_id"],
                    "name": result.get("name", ""),
                    "status": result.get("status", ""),
                    "dependency_status": result.get("dependency_status", ""),
                    "issues": result.get("issues", []),
                })
        counts = Counter(item["status"] for item in dependent)
        return {
            "total": len(dependent),
            "active": int(counts.get("active", 0)),
            "draft": int(counts.get("draft", 0)),
            "testing": int(counts.get("testing", 0)),
            "blocked": int(counts.get("blocked", 0)),
            "rejected": int(counts.get("rejected", 0)),
            "retired": int(counts.get("retired", 0)),
            "items": dependent,
        }

    def impact_for_node(self, graph_filename: str, node_id: str) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        node = graph["nodes"].get(node_id)
        if node is None:
            raise ReviewError("节点不存在", 404)
        incident = [
            relation for relation in graph["relations"].values()
            if node_id in (relation.get("source_node_id"), relation.get("target_node_id"))
        ]
        return {
            "node_id": node_id,
            "name": node.get("name", ""),
            "status": _status(node),
            "incident_relations": len(incident),
            "rules": self._rule_items_for_reference(
                filename,
                lambda ref: ref.get("kind") == "node" and ref.get("node_id") == node_id,
            ),
        }

    def impact_for_relation(
        self,
        graph_filename: str,
        source_node_id: str,
        relation_code: str,
        target_node_id: str,
    ) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        key = (source_node_id, relation_code, target_node_id)
        relation = graph["relations"].get(key)
        if relation is None:
            raise ReviewError("关系不存在", 404)
        return {
            "relation_id": _relation_id(key),
            "status": _status(relation),
            "rules": self._rule_items_for_reference(
                filename,
                lambda ref: (
                    ref.get("kind") == "relation"
                    and ref.get("source_node_id") == source_node_id
                    and ref.get("relation_code") == relation_code
                    and ref.get("target_node_id") == target_node_id
                ),
            ),
        }

    def node_dependents(self, graph_filename: str, node_id: str) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        rules = self._rules_for_graph(self._load_rules(), filename)
        sync = self._sync(rules)
        items = []
        for result in sync["rules"]:
            refs = [
                ref for ref in result.get("references") or []
                if ref.get("kind") == "node" and ref.get("node_id") == node_id
            ]
            if refs:
                items.append({
                    "rule_id": result["rule_id"],
                    "name": result.get("name", ""),
                    "status": result.get("status", ""),
                    "dependency_status": result.get("dependency_status", ""),
                    "issues": result.get("issues", []),
                })
        return {"node_id": node_id, "rules": items}

    # ---- status changes ----
    def _blocked_by(self, references: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        now = _now()
        result = []
        for ref in references:
            if ref.get("status") == "active":
                continue
            if ref.get("kind") == "node":
                entity_id = str(ref.get("node_id") or "")
                entity_type = "node"
            else:
                entity_id = _relation_id((
                    ref.get("source_node_id"),
                    ref.get("relation_code"),
                    ref.get("target_node_id"),
                ))
                entity_type = "relation"
            result.append({
                "entity_type": entity_type,
                "entity_id": entity_id,
                "reason": str(ref.get("status") or "missing"),
                "since": now,
            })
        return result

    def _sync_blocked_rules(
        self, rules: List[Dict[str, Any]], graph_filename: str
    ) -> List[str]:
        graph_rules = self._rules_for_graph(rules, graph_filename)
        sync = self._sync(graph_rules)
        by_id = {item["rule_id"]: item for item in sync["rules"]}
        changed = []
        for rule in graph_rules:
            result = by_id.get(rule.get("rule_id"), {})
            if result.get("dependency_status") == "passed":
                continue
            if rule.get("status") != "active":
                continue
            rule["status"] = "blocked"
            rule["blocked_by"] = self._blocked_by(result.get("references") or [])
            rule["updated_at"] = _now()
            changed.append(rule["rule_id"])
        return changed

    def review_node(
        self,
        graph_filename: str,
        node_id: str,
        action: str,
        reviewer: str,
        note: str = "",
    ) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        node = graph["nodes"].get(node_id)
        if node is None:
            raise ReviewError("节点不存在", 404)
        current = _status(node)
        try:
            target = next_graph_status(current, action)
        except StatusTransitionError as exc:
            raise ReviewError(str(exc), 422) from exc

        previous_graph = copy.deepcopy(graph)
        previous_rules = self._load_rules()
        rules = copy.deepcopy(previous_rules)
        now = _now()
        node.update({
            "review_status": target,
            "reviewed_by": reviewer or "未署名",
            "reviewed_at": now,
            "review_note": note,
        })
        if target != "active":
            for relation in graph["relations"].values():
                if node_id in (relation.get("source_node_id"), relation.get("target_node_id")):
                    if _status(relation) not in {"rejected", "retired"}:
                        relation["review_status"] = "stale"
                        relation["reviewed_at"] = now
        # Persist the graph before re-syncing rules: check_rule_sync reloads
        # the graph from disk and must see the new status.
        self._save_graph(graph, filename)
        affected = self._sync_blocked_rules(rules, filename)
        audit = [{
            "action": action,
            "entity_type": "node",
            "entity_id": node_id,
            "graph_filename": filename,
            "before_status": current,
            "after_status": target,
            "reviewer": reviewer or "未署名",
            "note": note,
            "affected_rules": affected,
        }]
        self._persist(filename, previous_graph, graph, previous_rules, rules, audit)
        return {"item": node, "status": target, "affected_rules": affected}

    def review_relation(
        self,
        graph_filename: str,
        source_node_id: str,
        relation_code: str,
        target_node_id: str,
        action: str,
        reviewer: str,
        note: str = "",
    ) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        key = (source_node_id, relation_code, target_node_id)
        relation = graph["relations"].get(key)
        if relation is None:
            raise ReviewError("关系不存在", 404)
        current = _status(relation)
        try:
            target = next_graph_status(current, action)
        except StatusTransitionError as exc:
            raise ReviewError(str(exc), 422) from exc
        if action == "approve":
            problems = []
            for endpoint in (source_node_id, target_node_id):
                endpoint_node = graph["nodes"].get(endpoint)
                if endpoint_node is None or _status(endpoint_node) != "active":
                    problems.append(
                        f"{endpoint} 状态为 {(_status(endpoint_node) if endpoint_node else 'missing')}"
                    )
            if problems:
                raise ReviewError(
                    "关系两端节点必须先通过审核: " + "; ".join(problems), 422
                )

        previous_graph = copy.deepcopy(graph)
        previous_rules = self._load_rules()
        rules = copy.deepcopy(previous_rules)
        now = _now()
        relation.update({
            "review_status": target,
            "reviewed_by": reviewer or "未署名",
            "reviewed_at": now,
            "review_note": note,
        })
        # Persist the graph before re-syncing rules: check_rule_sync reloads
        # the graph from disk and must see the new status.
        self._save_graph(graph, filename)
        affected = self._sync_blocked_rules(rules, filename)
        audit = [{
            "action": action,
            "entity_type": "relation",
            "entity_id": _relation_id(key),
            "graph_filename": filename,
            "before_status": current,
            "after_status": target,
            "reviewer": reviewer or "未署名",
            "note": note,
            "affected_rules": affected,
        }]
        self._persist(filename, previous_graph, graph, previous_rules, rules, audit)
        return {"item": relation, "status": target, "affected_rules": affected}

    def bulk_review(
        self,
        graph_filename: str,
        items: List[Dict[str, Any]],
        action: str,
        reviewer: str,
        note: str = "",
    ) -> Dict[str, Any]:
        results = []
        for item in items:
            try:
                if item.get("item_type") == "relation":
                    result = self.review_relation(
                        graph_filename,
                        str(item.get("source_node_id") or ""),
                        str(item.get("relation_code") or ""),
                        str(item.get("target_node_id") or ""),
                        action, reviewer, note,
                    )
                else:
                    result = self.review_node(
                        graph_filename, str(item.get("node_id") or ""),
                        action, reviewer, note,
                    )
                results.append({"item": item, "ok": True, "result": result})
            except ReviewError as exc:
                results.append({
                    "item": item, "ok": False,
                    "error": exc.detail, "status_code": exc.status_code,
                })
        return {
            "action": action,
            "total": len(items),
            "succeeded": sum(1 for item in results if item["ok"]),
            "failed": sum(1 for item in results if not item["ok"]),
            "results": results,
        }


def sync_blocked_rules_for_graph(
    graph_filename: str,
    **service_kwargs,
) -> List[str]:
    """Block active rules whose graph dependencies are no longer usable."""
    service = ReviewService(**service_kwargs)
    filename = normalize_graph_filename(graph_filename)
    rules = service._load_rules()
    changed = service._sync_blocked_rules(rules, filename)
    if changed:
        service._save_rules(rules)
        for rule_id in changed:
            append_audit({
                "action": "auto_block",
                "entity_type": "rule",
                "entity_id": rule_id,
                "graph_filename": filename,
                "before_status": "active",
                "after_status": "blocked",
                "reviewer": "system",
                "note": "图谱依赖不再可用",
            }, service.audit_path)
    return changed
