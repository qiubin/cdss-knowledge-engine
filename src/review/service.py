"""Graph review operations: status transitions, impact, cascade and audit."""
from __future__ import annotations

import copy
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.api.routes.kg import (
    DEFAULT_GRAPH_FILENAME,
    DEFAULT_RELATION_NAMES,
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
from src.vocab.entity_vocabulary import EntityVocabularyRepository, dictionary_type
from .audit import AUDIT_PATH, append_audit, load_audit
from .errors import ReviewError
from .metadata import node_content_hash, relation_content_hash
from .precheck import (
    duplicate_relation_pairs,
    load_endpoint_type_map,
    relation_flags,
    type_label,
)
from .status import StatusTransitionError, next_graph_status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relation_key(relation: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(relation.get("source_node_id") or ""),
        str(relation.get("relation_code") or ""),
        str(relation.get("target_node_id") or ""),
    )


# 按病分组视图中，目标端为这些类型时展开其出边作为嵌套小节（仅一层）。
CONTAINER_NODE_TYPES: Tuple[str, ...] = ("treatment_plan", "care_pathway", "order_set", "lab", "exam")
SYNDROME_MEMBERSHIP_ROLES = {
    "DISEASE_HAS_MAIN_SYNDROME": "主证",
    "DISEASE_HAS_SECONDARY_SYNDROME": "次证",
    "DISEASE_HAS_SYNDROME": "证候",
}


def _approves_endpoints(graph: Dict[str, Any], key: Tuple[str, str, str]) -> bool:
    return (
        graph.get("domain") == "tcm"
        and key[1] in SYNDROME_MEMBERSHIP_ROLES
        and (graph["nodes"].get(key[0]) or {}).get("node_type") == "disease"
        and (graph["nodes"].get(key[2]) or {}).get("node_type") == "syndrome"
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

    def audit_records_enriched(
        self,
        entity_id: str = "",
        graph_filename: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """审核日志读时富化：把机械的 entity_id 解析成可读的类型与名称。

        日志是追加式的，历史记录只有 ID；实体/关系/规则在图谱与规则库里
        可按稳定 ID 反查，因此在读取侧统一补齐展示字段，存量记录同样受益。
        反查不到（图谱已删、实体已清）时保留原字段，前端回退显示 ID。
        """
        rows = self.audit_records(entity_id=entity_id, graph_filename=graph_filename, limit=limit)
        graphs: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            filename = str(row.get("graph_filename") or "")
            if filename and filename not in graphs:
                try:
                    graphs[filename] = self._load_graph(filename)
                except Exception:
                    graphs[filename] = {}
        rules_by_id = {
            str(rule.get("rule_id") or ""): rule
            for rule in self._load_rules()
            if rule.get("rule_id")
        }

        def node_ref(graph: Dict[str, Any], node_id: str) -> Dict[str, Any]:
            node = (graph.get("nodes") or {}).get(node_id) or {}
            return {
                "name": str(node.get("display_name") or node.get("name") or ""),
                "node_type": str(node.get("node_type") or ""),
                "code": str(node.get("code") or node.get("concept_code") or ""),
            }

        for row in rows:
            entity_type = str(row.get("entity_type") or "")
            raw_id = str(row.get("entity_id") or "")
            graph = graphs.get(str(row.get("graph_filename") or ""), {})
            if entity_type == "node":
                ref = node_ref(graph, raw_id)
                row["entity_label"] = type_label(ref["node_type"])
                row["entity_name"] = ref["name"]
                row["entity_code"] = ref["code"]
            elif entity_type == "relation" and raw_id.count("|") == 2:
                source_id, code, target_id = raw_id.split("|")
                source, target = node_ref(graph, source_id), node_ref(graph, target_id)
                relation = (graph.get("relations") or {}).get(
                    (source_id, code, target_id)) or {}
                rel_name = str(relation.get("relation_name")
                               or DEFAULT_RELATION_NAMES.get(code) or code)
                row.update({
                    "entity_label": "关系",
                    "entity_name": f"{source['name'] or source_id} —{rel_name}→ {target['name'] or target_id}",
                    "relation_code": code,
                    "source_node_type": source["node_type"],
                    "source_name": source["name"],
                    "target_node_type": target["node_type"],
                    "target_name": target["name"],
                })
            elif entity_type == "rule":
                rule = rules_by_id.get(raw_id) or {}
                row["entity_label"] = "规则"
                row["entity_name"] = str(rule.get("name") or "")
            elif entity_type == "clinical_basis":
                basis = next(
                    (b for b in (graph.get("clinical_bases") or [])
                     if str(b.get("basis_id") or "") == raw_id),
                    {},
                )
                spec = basis.get("specification") or {}
                row["entity_label"] = "诊疗依据"
                row["entity_name"] = str(basis.get("name") or spec.get("disease_name") or "")
            elif entity_type == "graph_node" and not row.get("graph_filename"):
                # 历史 merge 记录没有图谱归属，在已加载缓存里尽力反查一次。
                for candidate in graphs.values():
                    ref = node_ref(candidate, raw_id)
                    if ref["name"]:
                        row["entity_label"] = type_label(ref["node_type"])
                        row["entity_name"] = ref["name"]
                        row["entity_code"] = ref["code"]
                        break
        return rows

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
            "last_import_at": str(graph.get("last_import_at") or ""),
            "last_import_doc": str(graph.get("last_import_doc") or ""),
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

    def _relation_item(
        self,
        graph: Dict[str, Any],
        key: Tuple[str, str, str],
        relation: Dict[str, Any],
        relation_counts: Counter,
        endpoint_type_map: Dict[str, Dict[str, List[str]]],
        duplicate_pairs: set,
    ) -> Dict[str, Any]:
        source_node = graph["nodes"].get(key[0]) or {}
        target_node = graph["nodes"].get(key[2]) or {}
        return {
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
            "source_node_type": str(source_node.get("node_type") or ""),
            "target_name": str(target_node.get("name") or key[2]),
            "target_code": str(target_node.get("code") or target_node.get("concept_code") or ""),
            "target_status": _status(target_node) if target_node else "missing",
            "target_node_type": str(target_node.get("node_type") or ""),
            "approves_endpoints": _approves_endpoints(graph, key),
            "source_refs": list(relation.get("source_refs") or []),
            "dependent_rule_count": int(relation_counts.get(_relation_id(key), 0)),
            "updated_at": str(relation.get("reviewed_at") or ""),
            "created_at": str(relation.get("created_at") or ""),
            "last_import_at": str(relation.get("last_import_at") or ""),
            "orphaned_from": list(relation.get("orphaned_from") or []),
            "orphaned": bool(relation.get("orphaned_from")),
            "flags": relation_flags(relation, source_node, target_node, endpoint_type_map, duplicate_pairs),
        }

    def list_items(
        self,
        graph_filename: str,
        status: Optional[str] = None,
        item_type: Optional[str] = None,
        node_type: Optional[str] = None,
        source_doc: Optional[str] = None,
        referenced_by_rule: Optional[bool] = None,
        q: Optional[str] = None,
        relation_code: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        node_counts, relation_counts, _ = self._dependency_index(filename)
        query = str(q or "").strip().lower()
        items: List[Dict[str, Any]] = []
        endpoint_type_map: Dict[str, Dict[str, List[str]]] = {}
        duplicate_pairs: set = set()
        if item_type in (None, "", "relation"):
            endpoint_type_map = load_endpoint_type_map()
            duplicate_pairs = duplicate_relation_pairs(graph["relations"].values())
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
                    "created_at": str(node.get("created_at") or ""),
                    "last_import_at": str(node.get("last_import_at") or ""),
                    "orphaned_from": list(node.get("orphaned_from") or []),
                    "orphaned": bool(node.get("orphaned_from")),
                })
        if item_type in (None, "", "relation"):
            for key, relation in graph["relations"].items():
                items.append(self._relation_item(
                    graph, key, relation, relation_counts, endpoint_type_map, duplicate_pairs,
                ))
        filtered = []
        for item in items:
            if status and item["status"] != status:
                continue
            if node_type:
                endpoint_types = (item.get("source_node_type"), item.get("target_node_type"))
                if item.get("node_type") != node_type and node_type not in endpoint_types:
                    continue
            if relation_code and item.get("relation_code") != relation_code:
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

    def _section_summary(
        self, section_key: str, relation_code: str, relation_name: str,
        items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "section_key": section_key,
            "relation_code": relation_code,
            "relation_name": relation_name,
            "total": len(items),
            "status_counts": dict(Counter(item["status"] for item in items)),
            "items": items,
        }

    def relation_groups(self, graph_filename: str, subject_type: str = "disease") -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        if subject_type == "syndrome":
            allowed: Optional[Tuple[str, ...]] = ("syndrome",)
        elif subject_type == "all":
            allowed = None
        else:
            allowed = ("disease", "syndrome")
        endpoint_type_map = load_endpoint_type_map()
        duplicate_pairs = duplicate_relation_pairs(graph["relations"].values())
        stats: Dict[str, Counter] = {}
        for key, relation in graph["relations"].items():
            source = graph["nodes"].get(key[0])
            if source is None:
                continue
            source_type = str(source.get("node_type") or "")
            if allowed is not None and source_type not in allowed:
                continue
            entry = stats.setdefault(key[0], Counter())
            entry["total"] += 1
            flags = relation_flags(
                relation, source, graph["nodes"].get(key[2]) or {},
                endpoint_type_map, duplicate_pairs,
            )
            if _status(relation) in ("draft", "stale"):
                entry["pending"] += 1
            if flags:
                entry["suspect"] += 1
        subjects = []
        if allowed is not None:
            for node_id, node in graph["nodes"].items():
                if node.get("node_type") in allowed:
                    stats.setdefault(node_id, Counter())
        for node_id, entry in stats.items():
            node = graph["nodes"][node_id]
            subjects.append({
                "node_id": node_id,
                "name": str(node.get("name") or node_id),
                "display_name": str(node.get("display_name") or node.get("name") or node_id),
                "code": str(node.get("code") or node.get("concept_code") or ""),
                "node_type": str(node.get("node_type") or ""),
                "review_status": _status(node),
                "out_relation_count": int(entry["total"]),
                "pending_count": int(entry["pending"]),
                "suspect_count": int(entry["suspect"]),
                "created_at": str(node.get("created_at") or ""),
                "last_import_at": str(node.get("last_import_at") or ""),
            })
        subjects.sort(key=lambda item: (-item["pending_count"], item["name"]))
        # 疾病→证候归属分组：审核工作台以疾病为锚点展开业务链路。
        # 证候通过 DISEASE_HAS_(MAIN_|SECONDARY_)SYNDROME 关系归属疾病；
        # 没有任何疾病连接的证候进入 orphan_syndromes，保证仍有审核入口。
        subject_index = {item["node_id"]: item for item in subjects}
        role_by_code = SYNDROME_MEMBERSHIP_ROLES
        # 同一证候与同一疾病之间可能同时存在 证候/主证/次证 三种关系，
        # 归属时按 node_id 去重，角色取信息量最高的（主证 > 次证 > 证候）。
        # 已拒绝/失效的归属关系不参与分组；待审归属照常分组但携带状态，
        # 前端标注「归属待审」，不与已确认归属混淆。
        role_rank = {"DISEASE_HAS_MAIN_SYNDROME": 0, "DISEASE_HAS_SECONDARY_SYNDROME": 1, "DISEASE_HAS_SYNDROME": 2}
        memberships: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for key, relation in graph["relations"].items():
            source = graph["nodes"].get(key[0])
            target = graph["nodes"].get(key[2])
            if not source or not target:
                continue
            if (
                str(source.get("node_type") or "") == "disease"
                and str(target.get("node_type") or "") == "syndrome"
                and key[1] in role_by_code
            ):
                rel_status = _status(relation)
                if rel_status in ("rejected", "retired"):
                    continue
                bucket = memberships.setdefault(key[0], {})
                existing = bucket.get(key[2])
                if existing is None or role_rank[key[1]] < role_rank[existing["_code"]]:
                    bucket[key[2]] = {
                        "node_id": key[2],
                        "role": role_by_code[key[1]],
                        "_code": key[1],
                        "_status": rel_status,
                    }
        disease_groups = []
        claimed = set()
        for subject in subjects:
            if subject["node_type"] != "disease":
                continue
            # 已拒绝/退役的疾病是审核终态：其名下归属关系永久阻塞，组不再
            # 提供审核入口，不进列表。跳过时成员也不计入 claimed，draft/stale
            # 证候落入 orphan_syndromes，审核入口得以保留。
            if subject["review_status"] in ("rejected", "retired"):
                continue
            members = []
            for member in memberships.get(subject["node_id"], {}).values():
                entry = subject_index.get(member["node_id"])
                if entry:
                    members.append({
                        **entry,
                        "role": member["role"],
                        "membership_status": member["_status"],
                    })
                    claimed.add(member["node_id"])
            members.sort(key=lambda item: (-item["pending_count"], item["name"]))
            disease_groups.append({
                "disease": subject,
                "syndromes": members,
                "pending_total": subject["pending_count"]
                + sum(item["pending_count"] for item in members),
                "suspect_total": subject["suspect_count"]
                + sum(item["suspect_count"] for item in members),
            })
        disease_groups.sort(key=lambda item: (-item["pending_total"], item["disease"]["name"]))
        orphan_syndromes = [
            item for item in subjects
            if item["node_type"] == "syndrome"
            and item["node_id"] not in claimed
            # 已通过/已拒绝的孤立项不再需要审核入口，不进列表。
            and item["review_status"] in ("draft", "stale")
        ]
        orphan_syndromes.sort(key=lambda item: (-item["pending_count"], item["name"]))
        return {
            "graph_filename": filename,
            "last_import_at": str(graph.get("last_import_at") or ""),
            "subjects": subjects,
            "disease_groups": disease_groups,
            "orphan_syndromes": orphan_syndromes,
        }

    def relation_group_detail(self, graph_filename: str, subject_node_id: str) -> Dict[str, Any]:
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        subject = graph["nodes"].get(subject_node_id)
        if subject is None:
            raise ReviewError("节点不存在", 404)
        endpoint_type_map = load_endpoint_type_map()
        duplicate_pairs = duplicate_relation_pairs(graph["relations"].values())
        _, relation_counts, _ = self._dependency_index(filename)

        def item_for(key: Tuple[str, str, str], relation: Dict[str, Any]) -> Dict[str, Any]:
            return {
                **self._relation_item(
                    graph, key, relation, relation_counts, endpoint_type_map, duplicate_pairs,
                ),
                # 审核台契约用 graph_relation（同 queue 端点映射），动作链路据此分发
                "item_type": "graph_relation",
                "graph_filename": filename,
            }

        sections: Dict[str, Dict[str, Any]] = {}
        container_ids_by_section: Dict[str, List[str]] = {}
        container_items: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        scanned_containers: set = set()
        for key, relation in graph["relations"].items():
            if key[0] != subject_node_id:
                continue
            entry = item_for(key, relation)
            section = sections.setdefault(
                key[1], self._section_summary(key[1], key[1], entry["name"], []),
            )
            section["items"].append(entry)
            target = graph["nodes"].get(key[2]) or {}
            if str(target.get("node_type") or "") not in CONTAINER_NODE_TYPES:
                continue
            ids = container_ids_by_section.setdefault(key[1], [])
            if key[2] not in ids:
                ids.append(key[2])
            # 同一容器只全量扫描一次：主体经多种关系码指向同一容器时，
            # 后续直接边不再重复收集该容器的出边（嵌套 items 不翻倍）。
            # 守卫放容器粒度而非 (容器, 关系码) 键粒度，否则容器同一关系码
            # 指向多个目标时（如一个方案含多种药品）首次扫描内就会丢边。
            if key[2] in scanned_containers:
                continue
            scanned_containers.add(key[2])
            for ckey, crelation in graph["relations"].items():
                if ckey[0] != key[2]:
                    continue
                container_items.setdefault((key[2], ckey[1]), []).append(item_for(ckey, crelation))
        # setdefault 时 items 为空，total/status_counts 需在收集完成后重算。
        for section in sections.values():
            section["total"] = len(section["items"])
            section["status_counts"] = dict(
                Counter(item["status"] for item in section["items"])
            )
        ordered_keys = sorted(sections, key=lambda c: (-len(sections[c]["items"]), c))
        sections_out = []
        for code in ordered_keys:
            section = sections[code]
            containers = []
            for container_id in container_ids_by_section.get(code, []):
                node = graph["nodes"].get(container_id) or {}
                nested = []
                for (cid, ccode), items in container_items.items():
                    if cid != container_id:
                        continue
                    nested.append(self._section_summary(
                        f"{cid}:{ccode}", ccode,
                        items[0]["name"] if items else ccode, items,
                    ))
                nested.sort(key=lambda item: (-item["total"], item["relation_code"]))
                containers.append({
                    "container_node_id": container_id,
                    "container_name": str(node.get("name") or container_id),
                    "display_name": str(node.get("display_name") or node.get("name") or container_id),
                    "node_type": str(node.get("node_type") or ""),
                    "sections": nested,
                })
            section["containers"] = containers
            sections_out.append(section)
        totals: Counter = Counter()
        for section in sections_out:
            totals.update(section["status_counts"])
        suspect = sum(
            1 for section in sections_out for item in section["items"] if item["flags"]
        )
        return {
            "graph_filename": filename,
            "subject": {
                "node_id": subject_node_id,
                "name": str(subject.get("name") or subject_node_id),
                "display_name": str(subject.get("display_name") or subject.get("name") or subject_node_id),
                "code": str(subject.get("code") or subject.get("concept_code") or ""),
                "node_type": str(subject.get("node_type") or ""),
                "review_status": _status(subject),
            },
            "totals": {
                "total": sum(totals.values()),
                "pending": int(totals.get("draft", 0) + totals.get("stale", 0)),
                "suspect": suspect,
            },
            "sections": sections_out,
        }

    def pending_relations_for_subjects(
        self, graph_filename: str, subject_node_ids: List[str]
    ) -> Dict[str, Any]:
        """疾病组级一键审核：收集一组主体节点（疾病+名下证候）的全部待审出边。

        只统计 draft/stale 关系；同时给出硬阻塞数（端点被拒/失效/缺失），
        前端确认框据此提示可提交量，提交仍走 /review/bulk（link_endpoints）。
        """
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        wanted = {str(node_id) for node_id in (subject_node_ids or []) if node_id}
        hard_blocked = ("rejected", "retired", "missing")
        items = []
        blocked_count = 0
        for key, relation in graph["relations"].items():
            if key[0] not in wanted or _status(relation) not in ("draft", "stale"):
                continue
            source = graph["nodes"].get(key[0]) or {}
            target = graph["nodes"].get(key[2]) or {}
            source_status = _status(source) if source else "missing"
            target_status = _status(target) if target else "missing"
            blocked = source_status in hard_blocked or target_status in hard_blocked
            blocked_count += int(blocked)
            items.append({
                "item_type": "graph_relation",
                "source_node_id": key[0],
                "relation_code": key[1],
                "target_node_id": key[2],
                "source_name": str(source.get("name") or key[0]),
                "target_name": str(target.get("name") or key[2]),
                "source_status": source_status,
                "target_status": target_status,
                "status": _status(relation),
                "blocked": blocked,
            })
        return {
            "graph_filename": filename,
            "subjects": sorted(wanted),
            "total": len(items),
            "blocked": blocked_count,
            "submittable": len(items) - blocked_count,
            "items": items,
        }

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
                        relation["review_status"] = "draft"
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

    def update_node_name(
        self,
        graph_filename: str,
        node_id: str,
        name: str,
        reviewer: str,
        note: str = "",
    ) -> Dict[str, Any]:
        """Rename an entity without changing its stable code or identifier.

        A reviewed entity becomes stale after a semantic display-name change so
        the new wording must be explicitly approved. Incident relations and
        active dependent rules are invalidated by the same safety rule.
        """
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        node = graph["nodes"].get(node_id)
        if node is None:
            raise ReviewError("节点不存在", 404)
        cleaned = str(name or "").strip()
        if not cleaned:
            raise ReviewError("实体名称不能为空", 422)
        if len(cleaned) > 200:
            raise ReviewError("实体名称不能超过 200 个字符", 422)
        if dictionary_type(node.get("node_type", ""), graph.get("domain", "")) == "tcm_disease":
            canonical = EntityVocabularyRepository("tcm_disease").find(str(node.get("code") or ""))
            if canonical and cleaned != canonical.name:
                raise ReviewError("中医疾病名称请在中医疾病词典统一维护", 422)
        previous_name = str(node.get("name") or "")
        if cleaned == previous_name:
            raise ReviewError("实体名称没有变化", 422)

        previous_graph = copy.deepcopy(graph)
        previous_rules = self._load_rules()
        rules = copy.deepcopy(previous_rules)
        now = _now()
        current_status = _status(node)
        target_status = "draft" if current_status == "active" else current_status
        history = list(node.get("name_history") or [])
        history.append({
            "name": previous_name,
            "changed_at": now,
            "changed_by": reviewer or "未署名",
            "note": note,
        })
        node.update({
            "name": cleaned,
            "display_name": cleaned,
            "name_history": history[-50:],
            "review_status": target_status,
            "reviewed_by": reviewer or "未署名",
            "reviewed_at": now,
            "review_note": note,
            "version": int(node.get("version") or 1) + 1,
        })
        node["content_hash"] = node_content_hash(node)
        if target_status != "active":
            for relation in graph["relations"].values():
                if node_id in (relation.get("source_node_id"), relation.get("target_node_id")):
                    if _status(relation) not in {"rejected", "retired"}:
                        relation["review_status"] = "draft"
                        relation["reviewed_at"] = now

        self._save_graph(graph, filename)
        affected = self._sync_blocked_rules(rules, filename)
        audit = [{
            "action": "edit_name",
            "entity_type": "node",
            "entity_id": node_id,
            "graph_filename": filename,
            "before_status": current_status,
            "after_status": target_status,
            "before_name": previous_name,
            "after_name": cleaned,
            "reviewer": reviewer or "未署名",
            "note": note,
            "affected_rules": affected,
        }]
        self._persist(filename, previous_graph, graph, previous_rules, rules, audit)
        return {
            "item": node,
            "status": target_status,
            "before_name": previous_name,
            "affected_rules": affected,
        }

    def review_relation(
        self,
        graph_filename: str,
        source_node_id: str,
        relation_code: str,
        target_node_id: str,
        action: str,
        reviewer: str,
        note: str = "",
        link_endpoints: bool = False,
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
        endpoint_approvals = {}
        if action == "approve":
            approves_endpoints = _approves_endpoints(graph, key) or link_endpoints
            problems = []
            for endpoint in (source_node_id, target_node_id):
                endpoint_node = graph["nodes"].get(endpoint)
                status = _status(endpoint_node) if endpoint_node is not None else "missing"
                if status == "active":
                    continue
                if approves_endpoints and status in {"draft", "stale"}:
                    endpoint_approvals[endpoint] = status
                else:
                    # 被拒绝/已失效/缺失的端点是明确的人工决定或缺数据，
                    # 不允许随关系静默激活，必须先恢复或补全实体。
                    problems.append(f"{endpoint} 状态为 {status}")
            if problems:
                raise ReviewError(
                    "关系端点存在被拒绝/失效/缺失的实体，请先恢复或补全后再审核: " + "; ".join(problems), 422
                )

        previous_graph = copy.deepcopy(graph)
        previous_rules = self._load_rules()
        rules = copy.deepcopy(previous_rules)
        now = _now()
        review_metadata = {
            "review_status": target,
            "reviewed_by": reviewer or "未署名",
            "reviewed_at": now,
            "review_note": note,
        }
        linked_note = f"{note}（随关系 {source_node_id} -{relation_code}-> {target_node_id} 审核连带通过）"
        for endpoint in endpoint_approvals:
            graph["nodes"][endpoint].update({**review_metadata, "review_note": linked_note})
        relation.update(review_metadata)
        # Persist the graph before re-syncing rules: check_rule_sync reloads
        # the graph from disk and must see the new status.
        self._save_graph(graph, filename)
        affected = self._sync_blocked_rules(rules, filename)
        audit = [{
            "action": action,
            "entity_type": "node",
            "entity_id": endpoint,
            "graph_filename": filename,
            "before_status": status,
            "after_status": target,
            "reviewer": reviewer or "未署名",
            "note": note,
            "trigger_relation_id": _relation_id(key),
            "affected_rules": affected,
        } for endpoint, status in endpoint_approvals.items()]
        audit.append({
            "action": action,
            "entity_type": "relation",
            "entity_id": _relation_id(key),
            "graph_filename": filename,
            "before_status": current,
            "after_status": target,
            "reviewer": reviewer or "未署名",
            "note": note,
            "affected_rules": affected,
        })
        self._persist(filename, previous_graph, graph, previous_rules, rules, audit)
        return {"item": relation, "status": target, "affected_rules": affected,
                "linked_endpoints": list(endpoint_approvals)}

    def apply_relation_correction(
        self,
        graph_filename: str,
        source_node_id: str,
        relation_code: str,
        target_node_id: str,
        reviewer: str,
        note: str = "",
        suggested_source_name: str = "",
        suggested_target_name: str = "",
        suggested_relation_code: str = "",
        reverse_direction: bool = False,
    ) -> Dict[str, Any]:
        """Atomically apply a reviewed AI correction and return it to stale."""
        filename = normalize_graph_filename(graph_filename)
        graph = self._load_graph(filename)
        old_key = (source_node_id, relation_code, target_node_id)
        relation = graph["relations"].get(old_key)
        if relation is None:
            raise ReviewError("关系不存在", 404)
        source = graph["nodes"].get(source_node_id)
        target = graph["nodes"].get(target_node_id)
        if source is None or target is None:
            raise ReviewError("关系端点实体不存在", 422)

        source_name = str(suggested_source_name or "").strip()
        target_name = str(suggested_target_name or "").strip()
        for value in (source_name, target_name):
            if len(value) > 200:
                raise ReviewError("实体名称不能超过 200 个字符", 422)
        disease_repository = EntityVocabularyRepository(dictionary_type("disease", graph.get("domain", "")))
        for endpoint, proposed_name, role in (
            (source, source_name, "起点"), (target, target_name, "目标"),
        ):
            if not proposed_name or endpoint.get("node_type") != "disease":
                continue
            canonical = disease_repository.find(str(endpoint.get("code") or ""))
            if canonical and proposed_name != canonical.name:
                raise ReviewError(
                    f"{role}疾病编码 {canonical.code} 的标准名称是“{canonical.name}”，"
                    "请在对应疾病词典维护标准名称，或重新绑定关系",
                    422,
                )
        new_code = str(suggested_relation_code or relation_code).strip()
        endpoint_map = load_endpoint_type_map()
        if new_code != relation_code and new_code not in endpoint_map:
            raise ReviewError("AI 建议的关系类型不在当前模板中，不能一键应用", 422)
        new_source_id, new_target_id = (
            (target_node_id, source_node_id) if reverse_direction
            else (source_node_id, target_node_id)
        )
        new_key = (new_source_id, new_code, new_target_id)
        if new_key != old_key and new_key in graph["relations"]:
            raise ReviewError("修正后的关系已存在，请先处理重复关系", 409)
        changes = []
        if source_name and source_name != str(source.get("name") or ""):
            changes.append("source_name")
        if target_name and target_name != str(target.get("name") or ""):
            changes.append("target_name")
        if new_code != relation_code:
            changes.append("relation_code")
        if reverse_direction:
            changes.append("direction")
        if not changes:
            raise ReviewError("AI 没有给出可应用的结构化修正", 422)

        previous_graph = copy.deepcopy(graph)
        previous_rules = self._load_rules()
        rules = copy.deepcopy(previous_rules)
        now = _now()
        audit = []

        def rename_node(node_id: str, node: Dict[str, Any], new_name: str) -> None:
            old_name = str(node.get("name") or "")
            if not new_name or new_name == old_name:
                return
            history = list(node.get("name_history") or [])
            history.append({
                "name": old_name, "changed_at": now,
                "changed_by": reviewer or "未署名", "note": note,
            })
            old_status = _status(node)
            node.update({
                "name": new_name,
                "display_name": new_name,
                "name_history": history[-50:],
                # Clicking “apply AI correction” is an explicit human
                # acceptance of this name change. Keep an already-approved
                # endpoint active; draft endpoints still require their own review.
                "review_status": old_status,
                "reviewed_by": reviewer or "未署名",
                "reviewed_at": now,
                "review_note": note,
                "version": int(node.get("version") or 1) + 1,
            })
            node["content_hash"] = node_content_hash(node)
            audit.append({
                "action": "apply_ai_name_correction", "entity_type": "node",
                "entity_id": node_id, "graph_filename": filename,
                "before_status": old_status, "after_status": _status(node),
                "before_name": old_name, "after_name": new_name,
                "reviewer": reviewer or "未署名", "note": note,
            })

        rename_node(source_node_id, source, source_name)
        rename_node(target_node_id, target, target_name)
        old_status = _status(relation)
        if new_key != old_key:
            graph["relations"].pop(old_key)
            graph["relations"][new_key] = relation
        relation.update({
            "source_node_id": new_source_id,
            "relation_code": new_code,
            "relation_name": DEFAULT_RELATION_NAMES.get(
                new_code, relation.get("relation_name") or new_code
            ),
            "target_node_id": new_target_id,
            "review_status": "draft",
            "reviewed_by": reviewer or "未署名",
            "reviewed_at": now,
            "review_note": note,
            "version": int(relation.get("version") or 1) + 1,
        })
        relation["content_hash"] = relation_content_hash(relation)
        # A renamed endpoint changes the clinical statement of every incident edge.
        renamed_ids = set()
        if "source_name" in changes:
            renamed_ids.add(source_node_id)
        if "target_name" in changes:
            renamed_ids.add(target_node_id)
        for edge in graph["relations"].values():
            if renamed_ids.intersection({
                str(edge.get("source_node_id") or ""),
                str(edge.get("target_node_id") or ""),
            }) and _status(edge) not in {"rejected", "retired"}:
                edge["review_status"] = "draft"
                edge["reviewed_at"] = now

        self._save_graph(graph, filename)
        affected = self._sync_blocked_rules(rules, filename)
        for record in audit:
            record["affected_rules"] = affected
        audit.append({
            "action": "apply_ai_correction", "entity_type": "relation",
            "entity_id": _relation_id(old_key), "graph_filename": filename,
            "before_status": old_status, "after_status": "draft",
            "before_relation": _relation_id(old_key),
            "after_relation": _relation_id(new_key),
            "changes": changes, "reviewer": reviewer or "未署名",
            "note": note, "affected_rules": affected,
        })
        self._persist(filename, previous_graph, graph, previous_rules, rules, audit)
        return {
            "item": relation,
            "status": "draft",
            "relation_id": _relation_id(new_key),
            "approves_endpoints": _approves_endpoints(graph, new_key),
            "changes": changes,
            "affected_rules": affected,
        }

    def bulk_review(
        self,
        graph_filename: str,
        items: List[Dict[str, Any]],
        action: str,
        reviewer: str,
        note: str = "",
        link_endpoints: bool = False,
    ) -> Dict[str, Any]:
        results = []
        for item in items:
            try:
                # 队列契约用 "relation"，疾病工作台分组契约用 "graph_relation"，
                # 两者都按关系审核分发；其余（graph_node/rule）走节点审核。
                if item.get("item_type") in ("relation", "graph_relation"):
                    result = self.review_relation(
                        graph_filename,
                        str(item.get("source_node_id") or ""),
                        str(item.get("relation_code") or ""),
                        str(item.get("target_node_id") or ""),
                        action, reviewer, note, link_endpoints,
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
