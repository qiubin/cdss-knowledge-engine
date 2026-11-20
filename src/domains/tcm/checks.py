"""TCM rule-to-graph consistency checks."""
from __future__ import annotations

from typing import Any, Dict, List, Set


def _node_id(node_type: str, code: str) -> str:
    return f"ENTITY:{node_type}:{code}"


class TcmGraphSemanticChecker:
    """Validate TCM rules against syndrome/observation/treatment nodes."""

    def __init__(self, graph: Dict[str, Any]):
        nodes = graph.get("nodes") or []
        if isinstance(nodes, dict):
            nodes = list(nodes.values())
        self.nodes = {
            str(item.get("id")): item for item in nodes
            if isinstance(item, dict) and item.get("id")
        }
        self.node_ids: Set[str] = set(self.nodes)
        rows = graph.get("relations") or []
        self.relations = list(rows.values()) if isinstance(rows, dict) else rows
        self.strict = False
        self.anchor = ""
        self._references: List[Dict[str, Any]] = []
        self.syndromes = {
            str(item.get("code")): item
            for item in (graph.get("syndromes") or [])
            if isinstance(item, dict) and item.get("code")
        }
        self.diseases = {
            str(item.get("code")): item
            for item in (graph.get("diseases") or [])
            if isinstance(item, dict) and item.get("code")
        }

    def _require_node(self, issues: List[str], node_type: str, code: str) -> bool:
        if not code:
            issues.append(f"缺少{node_type}编码")
            return False
        node_id = _node_id(node_type, code)
        node = self.nodes.get(node_id)
        self._references.append({
            "kind": "node",
            "node_id": node_id,
            "status": str((node or {}).get("review_status") or "draft") if node else "missing",
        })
        if node_id not in self.node_ids:
            issues.append(f"图谱缺少{node_type}节点 {code}")
            return False
        return True

    def _unique_references(self) -> List[Dict[str, Any]]:
        seen: Dict[str, Dict[str, Any]] = {}
        for ref in self._references:
            seen[str(ref.get("node_id") or (ref.get("source_node_id"), ref.get("relation_code"), ref.get("target_node_id")))] = ref
        return list(seen.values())

    def check_rule(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        issues: List[str] = []
        self._references = []
        self.strict = (rule.get("clinical_context") or {}).get("schema_version") == 3
        anchor_type = str(rule.get("anchor_type") or "syndrome")
        self.anchor_type = anchor_type
        self.anchor = _node_id(anchor_type, str(rule.get("disease_code") or ""))
        anchor_code = str(rule.get("disease_code") or rule.get("anchor_code") or "")
        if not anchor_code:
            issues.append("目标证候编码为空")
        elif anchor_type in {"syndrome", "syndrome_element"}:
            self._require_node(issues, anchor_type, anchor_code)
        elif anchor_type == "disease":
            self._require_node(issues, "disease", anchor_code)

        self._walk(rule.get("conditions"), issues)

        conclusion = rule.get("conclusion") or {}
        if conclusion.get("treatment_method_code"):
            self._require_node(issues, "treatment_method", str(conclusion["treatment_method_code"]))
        if conclusion.get("formula_code"):
            self._require_node(issues, "formula", str(conclusion["formula_code"]))
        for herb in conclusion.get("herbs") or []:
            if isinstance(herb, dict) and herb.get("code"):
                self._require_node(issues, "herb", str(herb["code"]))

        if self.strict:
            from .clinical_schema import validate_v3_rule
            issues.extend(validate_v3_rule(rule))
            contract_code = (rule.get("clinical_context") or {}).get("contract_code")
            if not contract_code:
                issues.append("缺少场景化临床判据编码")
            else:
                self._require_node(issues, "clinical_rule", contract_code)
                relation = "ELEMENT_HAS_CLINICAL_RULE" if anchor_type == "syndrome_element" else "SYNDROME_HAS_CLINICAL_RULE"
                self._require_relation(issues, self.anchor, relation, _node_id("clinical_rule", contract_code))
            method, formula = conclusion.get("treatment_method_code"), conclusion.get("formula_code")
            if method:
                self._require_relation(issues, self.anchor, "SYNDROME_TREATED_BY_METHOD", _node_id("treatment_method", method))
            if formula:
                if not method:
                    issues.append("方药规则缺少治法与方剂链路")
                else:
                    self._require_relation(issues, _node_id("treatment_method", method), "METHOD_HAS_FORMULA", _node_id("formula", formula))
                for herb in conclusion.get("herbs") or []:
                    self._require_relation(issues, _node_id("formula", formula), "FORMULA_CONTAINS_HERB", _node_id("herb", herb["code"]))
            for modification in conclusion.get("modification_rules") or []:
                self._walk(modification.get("condition"), issues)
                component = modification.get("component")
                if component:
                    self._require_node(issues, "herb", component.get("code", ""))
                    self._require_relation(issues, _node_id("formula", formula or ""), "FORMULA_HAS_MODIFICATION", _node_id("herb", component.get("code", "")))
            for medicine in conclusion.get("treatment_items") or []:
                self._require_node(issues, "medication", medicine.get("code", ""))
                self._require_relation(issues, self.anchor, "SYNDROME_RECOMMENDS_MEDICATION", _node_id("medication", medicine.get("code", "")))
        references = self._unique_references()
        review_issues = [
            f"依赖 {ref.get('node_id') or ref.get('relation_code')} 当前状态为 {ref['status']}，需要 active"
            for ref in references if ref.get("status") != "active"
        ]
        return {
            "rule_id": rule.get("rule_id", ""),
            "name": rule.get("name", ""),
            "status": rule.get("status", ""),
            "rule_type": rule.get("rule_type", ""),
            "disease_code": anchor_code,
            "concept_codes": [],
            "semantic_status": "inconsistent" if issues else "consistent",
            "issues": issues,
            "references": references,
            "dependency_status": "passed" if references and not review_issues else "blocked",
            "review_issues": review_issues,
        }

    def _require_relation(self, issues, source, code, target):
        edge = next((edge for edge in self.relations if edge.get("source_node_id") == source
                     and edge.get("relation_code") == code and edge.get("target_node_id") == target), None)
        self._references.append({"kind": "relation", "source_node_id": source, "relation_code": code,
                                 "target_node_id": target, "status": (edge or {}).get("review_status", "missing")})
        if edge is None:
            issues.append(f"缺少已绑定关系：{source} → {code} → {target}")

    def _walk(self, condition: Any, issues: List[str]) -> None:
        if not isinstance(condition, dict):
            if condition is not None:
                issues.append("规则条件结构无效")
            return
        condition_type = str(condition.get("type") or "")
        if condition_type in {"group", "weighted_group", "count_group"}:
            children = condition.get("children")
            if not isinstance(children, list) or not children:
                issues.append("逻辑组缺少子条件")
            for child in children or []:
                self._walk(child, issues)
            for child in condition.get("required_children") or []:
                self._walk(child, issues)
            return
        if condition_type == "observation":
            if self.strict:
                kind = str(condition.get("observation_type") or "symptom")
                relation = ("ELEMENT_HAS_" if self.anchor_type == "syndrome_element" else "SYNDROME_HAS_") + kind.upper()
                self._require_relation(issues, self.anchor, relation, _node_id(kind, str(condition.get("code") or "")))
            self._require_node(
                issues,
                str(condition.get("observation_type") or "symptom"),
                str(condition.get("code") or ""),
            )
            return
        if condition_type == "symptom":
            self._require_node(issues, "symptom", str(condition.get("symptom_code") or ""))
            return
        if condition_type == "fact":
            if self.strict:
                fact_type, code = str(condition.get("fact_type") or ""), str(condition.get("code") or "")
                self._require_node(issues, fact_type, code)
                if self.anchor_type == "syndrome" and fact_type == "syndrome_element":
                    self._require_relation(issues, self.anchor, "SYNDROME_HAS_ELEMENT", _node_id(fact_type, code))
            return
        if self.strict and condition_type == "past_history":
            code = str(condition.get("code") or "")
            kind = "western_disease" if _node_id("western_disease", code) in self.nodes else "disease"
            self._require_node(issues, kind, code)
            return
        if self.strict and condition_type in {"attribute", "demographic", "medication", "vital"}:
            return
        issues.append(f"中医规则条件类型未接入图谱语义校验: {condition_type or '未知'}")
