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
        self.node_ids: Set[str] = {
            str(item.get("id")) for item in nodes
            if isinstance(item, dict) and item.get("id")
        }
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
        if _node_id(node_type, code) not in self.node_ids:
            issues.append(f"图谱缺少{node_type}节点 {code}")
            return False
        return True

    def check_rule(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        issues: List[str] = []
        anchor_type = str(rule.get("anchor_type") or "syndrome")
        anchor_code = str(rule.get("disease_code") or rule.get("anchor_code") or "")
        if not anchor_code:
            issues.append("目标证候编码为空")
        elif anchor_type == "syndrome":
            if anchor_code not in self.syndromes and _node_id("syndrome", anchor_code) not in self.node_ids:
                issues.append(f"图谱缺少证候 {anchor_code}")
        elif anchor_type == "disease" and anchor_code not in self.diseases:
            issues.append(f"图谱缺少疾病 {anchor_code}")

        self._walk(rule.get("conditions"), issues)

        conclusion = rule.get("conclusion") or {}
        if conclusion.get("treatment_method_code"):
            self._require_node(issues, "treatment_method", str(conclusion["treatment_method_code"]))
        if conclusion.get("formula_code"):
            self._require_node(issues, "formula", str(conclusion["formula_code"]))
        for herb in conclusion.get("herbs") or []:
            if isinstance(herb, dict) and herb.get("code"):
                self._require_node(issues, "herb", str(herb["code"]))

        return {
            "rule_id": rule.get("rule_id", ""),
            "name": rule.get("name", ""),
            "status": rule.get("status", ""),
            "rule_type": rule.get("rule_type", ""),
            "disease_code": anchor_code,
            "concept_codes": [],
            "semantic_status": "inconsistent" if issues else "consistent",
            "issues": issues,
        }

    def _walk(self, condition: Any, issues: List[str]) -> None:
        if not isinstance(condition, dict):
            if condition is not None:
                issues.append("规则条件结构无效")
            return
        condition_type = str(condition.get("type") or "")
        if condition_type == "group":
            children = condition.get("children")
            if not isinstance(children, list) or not children:
                issues.append("逻辑组缺少子条件")
            for child in children or []:
                self._walk(child, issues)
            return
        if condition_type == "observation":
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
            # Facts are produced by an earlier stage; nothing to validate here.
            return
        issues.append(f"中医规则条件类型未接入图谱语义校验: {condition_type or '未知'}")
