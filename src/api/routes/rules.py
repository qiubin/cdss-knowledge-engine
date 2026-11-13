"""Rule CRUD API."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.models.rule import Rule
from src.domains import TCM, normalize_domain
from src.domains.tcm.checks import TcmGraphSemanticChecker
from src.extraction.concept_mapping import GENERIC_FINDING_BLOCKLIST
from src.extraction.graph_binding import resolve_template_graph_filename
from src.extraction.repository import ExtractionTemplateRepository
from src.review.audit import AUDIT_PATH as REVIEW_AUDIT_PATH, append_audit
from src.review.condition_completion import complete_rule_conditions
from src.review.status import StatusTransitionError, next_rule_status
from src.clinical_intent import normalize_clinical_intent
from src.rules.history import get_snapshot, list_snapshots, record_snapshot
from src.terminology.labels import default_labels
from src.vocab.repository import ConceptVocabularyRepository, collect_concept_codes
from .kg import _load_graph, normalize_graph_filename

router = APIRouter(prefix="/api/rules", tags=["rules"])

RULES_DIR = Path(__file__).parent.parent.parent.parent / "data" / "rules"
MANAGED_RULES_PATH = RULES_DIR / "managed_rules.json"
concept_vocab = ConceptVocabularyRepository()


def validate_rule_concepts(rule: Dict[str, Any]) -> None:
    unknown = []
    for code in collect_concept_codes(rule):
        try:
            concept_vocab.get(code)
        except Exception:
            unknown.append(code)
    if unknown:
        raise HTTPException(422, f"条件引用的概念词汇未定义: {', '.join(sorted(set(unknown)))}")


def _load_all_rules(path: Optional[Path] = None) -> List[Dict]:
    # After any management operation, managed_rules.json is the canonical file.
    target = Path(path) if path is not None else MANAGED_RULES_PATH
    if target.exists():
        rules = json.loads(target.read_text(encoding="utf-8"))
    else:
        rules = []
        for f in target.parent.glob("*.json"):
            items = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(items, list):
                rules.extend(items)
            elif isinstance(items, dict):
                rules.append(items)
    # Rules generated before labels were introduced still get readable
    # 全称（编码） condition text when listed for review.
    labels = default_labels()
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule["clinical_intent"] = normalize_clinical_intent(rule)
        conditions = rule.get("conditions")
        labels.annotate(conditions)
        if not rule.get("condition_text"):
            rule["condition_text"] = labels.summarize(conditions)
        if not rule.get("condition_text") and rule.get("rule_type") in {
            "treatment", "tcm_treatment", "tcm_prescription", "tcm_contraindication",
        }:
            rule["condition_text"] = "同疾病诊断成立后触发"
    return rules


def _save_all_rules(rules: List[Dict], path: Optional[Path] = None):
    target = Path(path) if path is not None else MANAGED_RULES_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    for rule in rules:
        if isinstance(rule, dict):
            rule["clinical_intent"] = normalize_clinical_intent(rule)
    target.write_text(
        json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class GraphSemanticChecker:
    """Check rule conditions against the unified diagnostic-evidence graph."""

    def __init__(self, graph: Dict[str, Any]):
        self.graph = graph
        self.nodes = {
            str(item.get("id")): item for item in self._as_list(graph.get("nodes"))
            if isinstance(item, dict) and item.get("id")
        }
        self.relations = {
            (item.get("source_node_id"), item.get("relation_code"), item.get("target_node_id")): item
            for item in self._as_list(graph.get("relations"))
            if isinstance(item, dict)
        }
        self.node_ids = set(self.nodes)
        self.relation_keys = set(self.relations)
        self._references: List[Dict[str, Any]] = []
        self.diseases = {
            str(item.get("code")): item for item in self._as_list(graph.get("diseases"))
            if isinstance(item, dict) and item.get("code")
        }
        self.evidences = {
            str(item.get("id")): item for item in self._as_list(graph.get("evidences"))
            if isinstance(item, dict) and item.get("id")
        }
        self.links = [
            item for item in self._as_list(graph.get("disease_evidence"))
            if isinstance(item, dict)
        ]
        self.vocabulary = {
            str(item.code): item for item in concept_vocab.list()
        }

    @staticmethod
    def _as_list(value: Any) -> List[Any]:
        return value if isinstance(value, list) else list((value or {}).values())

    def _node_id(self, node_type: str, code: Any) -> str:
        return f"ENTITY:{node_type}:{code}"

    def _record_node(self, node_id: str) -> None:
        node = self.nodes.get(node_id)
        self._references.append({
            "kind": "node",
            "node_id": node_id,
            "status": str((node or {}).get("review_status") or "draft") if node else "missing",
        })

    def _record_relation(self, source: Any, relation_code: str, target: Any) -> None:
        relation = self.relations.get((source, relation_code, target))
        self._references.append({
            "kind": "relation",
            "source_node_id": source,
            "relation_code": relation_code,
            "target_node_id": target,
            "status": str((relation or {}).get("review_status") or "draft") if relation else "missing",
        })

    def _add_node_issue(self, issues: List[str], node_type: str, code: Any) -> bool:
        node_id = self._node_id(node_type, code)
        self._record_node(node_id)
        if node_id not in self.node_ids:
            issues.append(f"图谱缺少{node_type}节点 {code}")
            return False
        return True

    def _add_concept_issue(self, issues: List[str], concept_code: Any) -> bool:
        code = str(concept_code or "")
        if not code:
            issues.append("概念编码为空")
            return False
        if code not in self.vocabulary:
            issues.append(f"概念词汇未定义 {code}")
            return False
        return True

    def _add_relation_issue(
        self, issues: List[str], source: Any, relation_code: str, target: Any
    ) -> None:
        self._record_relation(source, relation_code, target)
        if (source, relation_code, target) not in self.relation_keys:
            issues.append(f"图谱缺少关系 {relation_code}: {source} -> {target}")

    def _unique_references(self) -> List[Dict[str, Any]]:
        seen: Dict[tuple, Dict[str, Any]] = {}
        for ref in self._references:
            key = (
                ref.get("kind"),
                ref.get("node_id"),
                ref.get("source_node_id"),
                ref.get("relation_code"),
                ref.get("target_node_id"),
            )
            seen[key] = ref
        return list(seen.values())

    @staticmethod
    def _reference_issue(ref: Dict[str, Any]) -> str:
        if ref.get("kind") == "node":
            return f"节点 {ref['node_id']} 当前状态为 {ref['status']}，需要 active"
        return (
            f"关系 {ref['source_node_id']} -{ref['relation_code']}-> "
            f"{ref['target_node_id']} 当前状态为 {ref['status']}，需要 active"
        )

    def _check_diagnostic_evidence(
        self,
        issues: List[str],
        concepts: List[str],
        disease_code: str,
        lab_code: str = "",
        concept_code: str = "",
        finding_code: str = "",
        exam_code: str = "",
    ) -> None:
        """Validate the disease -> lab/exam -> diagnostic-evidence path."""
        if lab_code:
            if self._add_concept_issue(issues, concept_code):
                concepts.append(concept_code)
            lab_id = self._node_id("lab", lab_code)
            evidence_id = self._node_id("diagnostic_evidence", f"{lab_code}:{concept_code}")
            self._add_node_issue(issues, "lab", lab_code)
            self._add_node_issue(issues, "diagnostic_evidence", f"{lab_code}:{concept_code}")
            self._add_relation_issue(
                issues, self._node_id("disease", disease_code), "DISEASE_HAS_LAB", lab_id
            )
            self._add_relation_issue(issues, lab_id, "LAB_HAS_DIAGNOSTIC_EVIDENCE", evidence_id)
            self._add_relation_issue(
                issues, self._node_id("disease", disease_code),
                "DISEASE_HAS_DIAGNOSTIC_EVIDENCE", evidence_id,
            )
            return

        if finding_code:
            evidence_id = self._node_id("diagnostic_evidence", finding_code)
            self._add_node_issue(issues, "diagnostic_evidence", finding_code)
            self._add_relation_issue(
                issues, self._node_id("disease", disease_code),
                "DISEASE_HAS_DIAGNOSTIC_EVIDENCE", evidence_id,
            )
            if exam_code:
                exam_id = self._node_id("exam", exam_code)
                self._add_node_issue(issues, "exam", exam_code)
                self._add_relation_issue(
                    issues, self._node_id("disease", disease_code), "DISEASE_HAS_EXAM", exam_id
                )
                self._add_relation_issue(issues, exam_id, "EXAM_HAS_DIAGNOSTIC_EVIDENCE", evidence_id)

    def check_rule(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        issues: List[str] = []
        concepts: List[str] = []
        self._references = []
        disease_code = str(rule.get("disease_code") or "")

        if not disease_code:
            issues.append("目标疾病编码为空")
        else:
            self._add_node_issue(issues, "disease", disease_code)

        conditions = rule.get("conditions")
        empty_treatment_gate = (
            str(rule.get("rule_type") or "") == "treatment"
            and isinstance(conditions, dict)
            and conditions.get("type") == "group"
            and not conditions.get("children")
        )
        if not empty_treatment_gate:
            self._walk(conditions, disease_code, issues, concepts)
        concepts = list(dict.fromkeys(concepts))
        references = self._unique_references()
        review_issues = [
            self._reference_issue(ref) for ref in references
            if ref.get("status") != "active"
        ]
        return {
            "rule_id": rule.get("rule_id", ""),
            "name": rule.get("name", ""),
            "status": rule.get("status", ""),
            "disease_code": disease_code,
            "concept_codes": concepts,
            "semantic_status": "inconsistent" if issues else "consistent",
            "issues": issues,
            "references": references,
            "dependency_status": "passed" if references and not review_issues else "blocked",
            "review_issues": review_issues,
        }

    def _walk(
        self,
        condition: Any,
        disease_code: str,
        issues: List[str],
        concepts: List[str],
    ) -> None:
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
                self._walk(child, disease_code, issues, concepts)
            return

        if condition_type == "evidence":
            self._check_evidence(condition, disease_code, issues, concepts)
            return

        if condition_type == "lab_concept":
            lab_code = str(condition.get("lab_code") or "")
            concept_code = str(condition.get("concept_code") or "")
            if not lab_code:
                issues.append("检验条件缺少检验编码")
            if not concept_code:
                issues.append("检验条件缺少概念编码")
            if lab_code and concept_code:
                self._check_diagnostic_evidence(
                    issues, concepts, disease_code,
                    lab_code=lab_code, concept_code=concept_code,
                )
            return

        if condition_type == "exam_finding":
            finding_code = str(condition.get("finding_code") or "")
            exam_code = str(condition.get("exam_code") or "")
            if not finding_code:
                issues.append("检查发现条件缺少发现编码")
            else:
                concepts.append("PRESENT")
                self._check_diagnostic_evidence(
                    issues, concepts, disease_code,
                    finding_code=finding_code, exam_code=exam_code,
                )
            return

        if condition_type == "symptom":
            symptom_code = str(condition.get("symptom_code") or "")
            concept_code = str(condition.get("concept_code") or "PRESENT")
            if not symptom_code:
                issues.append("症状条件缺少症状编码")
                return
            concepts.append(concept_code)
            symptom_id = self._node_id("symptom", symptom_code)
            self._add_node_issue(issues, "symptom", symptom_code)
            self._add_relation_issue(
                issues,
                self._node_id("disease", disease_code),
                "DISEASE_HAS_SYMPTOM",
                symptom_id,
            )
            return

        if condition_type == "exam":
            exam_code = str(condition.get("exam_code") or "")
            if not exam_code:
                issues.append("检查条件缺少检查编码")
                return
            exam_id = self._node_id("exam", exam_code)
            self._add_node_issue(issues, "exam", exam_code)
            self._add_relation_issue(
                issues,
                self._node_id("disease", disease_code),
                "DISEASE_HAS_EXAM",
                exam_id,
            )
            return

        if condition_type in {"attribute", "observation", "fact"}:
            # Attribute/observation/fact conditions are evaluated directly
            # against patient data or prior-stage facts, not graph paths.
            return

        issues.append(f"规则条件类型未接入图谱语义校验: {condition_type or '未知'}")

    def _check_evidence(
        self,
        condition: Dict[str, Any],
        disease_code: str,
        issues: List[str],
        concepts: List[str],
    ) -> None:
        evidence_id = str(condition.get("evidence_id") or "")
        if not evidence_id:
            issues.append("证据条件缺少证据 ID")
            return
        evidence = self.evidences.get(evidence_id)
        if evidence is None:
            issues.append(f"图谱缺少诊断证据 {evidence_id}")
            return
        if not any(link.get("disease_code") == disease_code and link.get("evidence_id") == evidence_id for link in self.links):
            issues.append(f"证据 {evidence_id} 未关联目标疾病 {disease_code}")

        evidence_type = str(evidence.get("data_type") or "")
        if evidence_type == "lab":
            lab_code = str(evidence.get("lab_code") or "")
            concept_code = str(evidence.get("concept_code") or "")
            if not lab_code or not concept_code:
                issues.append(f"检验证据 {evidence_id} 缺少检验或概念编码")
                return
            self._check_diagnostic_evidence(
                issues, concepts, disease_code,
                lab_code=lab_code, concept_code=concept_code,
            )
            return

        if evidence_type == "exam_finding":
            finding_code = str(evidence.get("finding_code") or "")
            exam_code = str(evidence.get("exam_code") or "")
            if not finding_code:
                issues.append(f"检查发现证据 {evidence_id} 缺少发现编码")
                return
            self._check_diagnostic_evidence(
                issues, concepts, disease_code,
                finding_code=finding_code, exam_code=exam_code,
            )
            return

        if evidence_type == "exam":
            exam_code = str(evidence.get("exam_code") or "")
            if not exam_code:
                issues.append(f"检查证据 {evidence_id} 缺少检查编码")
                return
            exam_id = self._node_id("exam", exam_code)
            self._add_node_issue(issues, "exam", exam_code)
            self._add_relation_issue(
                issues,
                self._node_id("disease", disease_code),
                "DISEASE_HAS_EXAM",
                exam_id,
            )
            return

        if evidence_type == "symptom":
            symptom_code = str(evidence.get("symptom_code") or "")
            concept_code = str(evidence.get("concept_code") or "PRESENT")
            if not symptom_code:
                issues.append(f"症状证据 {evidence_id} 缺少症状编码")
                return
            concepts.append(concept_code)
            symptom_id = self._node_id("symptom", symptom_code)
            self._add_node_issue(issues, "symptom", symptom_code)
            self._add_relation_issue(
                issues,
                self._node_id("disease", disease_code),
                "DISEASE_HAS_SYMPTOM",
                symptom_id,
            )
            return

        issues.append(f"证据 {evidence_id} 数据类型不支持图谱语义校验: {evidence_type or '未知'}")


def check_rule_sync(rules: List[Dict[str, Any]], graph_loader=None) -> Dict[str, Any]:
    """Check each rule against the graph selected by its graph_filename."""
    loader = graph_loader or _load_graph
    groups: Dict[str, List[Dict[str, Any]]] = {}
    invalid_files: Dict[str, List[Dict[str, Any]]] = {}
    for rule in rules:
        filename = str(rule.get("graph_filename") or "")
        try:
            filename = normalize_graph_filename(filename)
            groups.setdefault(filename, []).append(rule)
        except Exception:
            invalid_files.setdefault(filename or "未配置", []).append(rule)

    results: List[Dict[str, Any]] = []
    for filename, grouped_rules in groups.items():
        graph = loader(filename)
        western_checker = GraphSemanticChecker(graph)
        tcm_checker = TcmGraphSemanticChecker(graph)
        graph_domain = normalize_domain(graph.get("domain"))
        for rule in grouped_rules:
            domain = normalize_domain(rule.get("domain") or graph_domain)
            checker = tcm_checker if domain == TCM else western_checker
            result = checker.check_rule(rule)
            result["graph_filename"] = filename
            result["domain"] = domain
            results.append(result)
    for filename, grouped_rules in invalid_files.items():
        for rule in grouped_rules:
            results.append({
                "rule_id": rule.get("rule_id", ""),
                "name": rule.get("name", ""),
                "status": rule.get("status", ""),
                "disease_code": rule.get("disease_code", ""),
                "concept_codes": [],
                "semantic_status": "inconsistent",
                "issues": [f"规则图谱文件名无效: {filename}"],
                "graph_filename": filename,
            })

    consistent = sum(item["semantic_status"] == "consistent" for item in results)
    dependency_passed = sum(item.get("dependency_status") == "passed" for item in results)
    return {
        "total": len(results),
        "consistent_count": consistent,
        "inconsistent_count": len(results) - consistent,
        "dependency_passed_count": dependency_passed,
        "dependency_blocked_count": len(results) - dependency_passed,
        "rules": results,
    }


@router.get("")
async def list_rules(status: Optional[str] = None, domain: Optional[str] = None):
    rules = _load_all_rules()
    if status:
        rules = [r for r in rules if r.get("status") == status]
    if domain:
        target = normalize_domain(domain)
        rules = [
            r for r in rules
            if normalize_domain(r.get("domain") or "western") == target
        ]
    sync = check_rule_sync(rules)
    by_id = {item["rule_id"]: item for item in sync["rules"]}
    for rule in rules:
        result = by_id.get(rule.get("rule_id"), {})
        refs = result.get("references") or []
        statuses = [ref.get("status") for ref in refs]
        rule["dependency_summary"] = {
            "total": len(refs),
            "active": sum(status == "active" for status in statuses),
            "draft": sum(status == "draft" for status in statuses),
            "stale": sum(status == "stale" for status in statuses),
            "rejected": sum(status == "rejected" for status in statuses),
            "retired": sum(status == "retired" for status in statuses),
            "missing": sum(status == "missing" for status in statuses),
            "blocked": result.get("dependency_status") != "passed",
        }
    return rules


@router.get("/sync-check")
async def sync_check():
    return check_rule_sync(_load_all_rules())


class ActivateConsistentRequest(BaseModel):
    domain: Optional[str] = None
    rule_ids: Optional[List[str]] = None
    quality_only: bool = False


class RuleReviewRequest(BaseModel):
    action: str
    reviewer: str = "未署名"
    note: str = ""


def _activation_blockers(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        ref for ref in result.get("references") or []
        if ref.get("status") != "active"
    ]


def _dependency_result(rule: Dict[str, Any], graph_loader=None) -> Dict[str, Any]:
    return check_rule_sync([rule], graph_loader=graph_loader)["rules"][0]


def _ensure_active_rule_dependencies(rule: Dict[str, Any]) -> None:
    """Reject saving an active rule whose graph dependencies are not approved."""
    if str(rule.get("status") or "draft") != "active":
        return
    result = _dependency_result(rule)
    if result.get("semantic_status") != "consistent":
        raise HTTPException(
            422,
            "规则与知识图谱语义不一致: " + "; ".join(result.get("issues") or []),
        )
    blockers = _activation_blockers(result)
    if blockers:
        raise HTTPException(422, {
            "error": "规则依赖未通过审核",
            "dependencies": blockers,
        })


def apply_rule_review(
    rule_id: str,
    action: str,
    reviewer: str,
    note: str = "",
) -> Dict[str, Any]:
    """Apply one review action to a rule and write an audit record."""
    rules = _load_all_rules()
    for rule in rules:
        if rule.get("rule_id") != rule_id:
            continue
        current = str(rule.get("status") or "draft")
        try:
            target = next_rule_status(current, action)
        except StatusTransitionError as exc:
            raise HTTPException(422, str(exc)) from exc
        if action == "approve":
            result = _dependency_result(rule)
            if result.get("semantic_status") != "consistent":
                issues = result.get("issues") or ["图谱语义不一致"]
                raise HTTPException(422, "规则与知识图谱语义不一致: " + "; ".join(issues))
            blockers = _activation_blockers(result)
            if blockers:
                raise HTTPException(422, {
                    "error": "规则依赖未通过审核",
                    "dependencies": blockers,
                })
        now = datetime.now(timezone.utc).isoformat()
        rule["status"] = target
        rule["reviewed_by"] = reviewer or "未署名"
        rule["reviewed_at"] = now
        rule["review_note"] = note
        rule["updated_at"] = now
        _save_all_rules(rules)
        append_audit({
            "action": action,
            "entity_type": "rule",
            "entity_id": rule_id,
            "graph_filename": rule.get("graph_filename", ""),
            "before_status": current,
            "after_status": target,
            "reviewer": reviewer or "未署名",
            "note": note,
            "affected_rules": [],
        }, REVIEW_AUDIT_PATH)
        return rule
    raise HTTPException(404, "规则未找到")


class RuleCompleteConditionsRequest(BaseModel):
    reviewer: str = "未署名"
    note: str = ""


class RuleBulkCompleteConditionsRequest(BaseModel):
    rule_ids: List[str] = []
    reviewer: str = "未署名"
    note: str = ""


def _apply_condition_completion(rule_id: str, reviewer: str, note: str = "") -> Dict[str, Any]:
    """Fill in concept conditions for a "no concept condition" rule from the graph."""
    rules = _load_all_rules()
    rule = next((r for r in rules if r.get("rule_id") == rule_id), None)
    if rule is None:
        raise HTTPException(404, "规则未找到")
    filename = normalize_graph_filename(rule.get("graph_filename") or "stemi_graph.json")
    graph = _load_graph(filename)
    baseline = _dependency_result(rule)
    if baseline.get("semantic_status") == "consistent":
        raise HTTPException(422, "规则已与图谱语义一致，无需补全（治疗类规则空条件为设计行为）")
    result = complete_rule_conditions(rule, graph)
    if not result.get("changed"):
        skipped = "; ".join(
            f"{item.get('evidence_id')}: {item.get('reason')}"
            for item in (result.get("skipped") or [])[:5]
        )
        reason = result.get("reason") or "规则条件无需补全"
        raise HTTPException(422, reason + (f"（{skipped}）" if skipped else ""))

    labels = default_labels()
    rule["conditions"] = result["conditions"]
    labels.annotate(rule["conditions"])
    rule["condition_text"] = labels.summarize(rule["conditions"])
    rule["condition_source"] = "graph_completion"
    now = datetime.now(timezone.utc).isoformat()
    rule["updated_at"] = now
    _save_all_rules(rules)
    append_audit({
        "action": "complete_conditions",
        "entity_type": "rule",
        "entity_id": rule_id,
        "graph_filename": filename,
        "before_status": rule.get("status"),
        "after_status": rule.get("status"),
        "reviewer": reviewer or "未署名",
        "note": note or "按图谱补全概念条件",
        "affected_rules": [],
    }, REVIEW_AUDIT_PATH)
    sync = _dependency_result(rule)
    return {
        "rule_id": rule_id,
        "used": result.get("used") or [],
        "skipped": result.get("skipped") or [],
        "semantic_status": sync.get("semantic_status"),
        "dependency_status": sync.get("dependency_status"),
        "condition_text": rule.get("condition_text", ""),
    }


@router.post("/{rule_id}/complete-conditions")
async def complete_rule_conditions_endpoint(
    rule_id: str, request: RuleCompleteConditionsRequest
):
    return _apply_condition_completion(rule_id, request.reviewer, request.note)


@router.post("/complete-conditions")
async def bulk_complete_conditions(request: RuleBulkCompleteConditionsRequest):
    results = []
    for rule_id in request.rule_ids:
        try:
            result = _apply_condition_completion(rule_id, request.reviewer, request.note)
            results.append({"rule_id": rule_id, "ok": True, **result})
        except HTTPException as exc:
            results.append({
                "rule_id": rule_id,
                "ok": False,
                "error": exc.detail,
                "status_code": exc.status_code,
            })
    return {
        "total": len(request.rule_ids),
        "succeeded": sum(1 for item in results if item["ok"]),
        "failed": sum(1 for item in results if not item["ok"]),
        "results": results,
    }


class RuleApproveDependenciesRequest(BaseModel):
    reviewer: str = "未署名"
    note: str = ""


class RuleRestoreRequest(BaseModel):
    reviewer: str = "未署名"
    note: str = ""


@router.post("/{rule_id}/approve-dependencies")
async def approve_rule_dependencies(rule_id: str, request: RuleApproveDependenciesRequest):
    """Approve all reviewable graph dependencies of one rule, then unblock it.

    Nodes are approved first, then relations (an approval requires both
    endpoints active).  ``missing`` references cannot be approved here and are
    reported back; ``rejected``/``retired`` entries are left for manual
    handling.  A ``blocked`` rule is re-approved automatically once its gate
    passes; ``draft``/``testing`` rules keep their status.
    """
    from src.review.errors import ReviewError
    from src.review.service import ReviewService

    rules = _load_all_rules()
    rule = next((r for r in rules if r.get("rule_id") == rule_id), None)
    if rule is None:
        raise HTTPException(404, "规则未找到")
    filename = normalize_graph_filename(rule.get("graph_filename") or "stemi_graph.json")
    refs = _dependency_result(rule).get("references") or []

    service = ReviewService()
    approved_nodes = 0
    approved_relations = 0
    skipped: List[Dict[str, Any]] = []
    missing: List[str] = []
    seen: set = set()

    def _try_approve(kind: str, status: str, entity_id: str, approve) -> bool:
        nonlocal approved_nodes, approved_relations
        if status == "active":
            return False
        if status == "missing":
            missing.append(entity_id)
            return False
        if status in {"rejected", "retired"}:
            skipped.append({
                "entity_id": entity_id,
                "status": status,
                "reason": "曾被拒绝/失效，需要人工处理",
            })
            return False
        try:
            approve()
            if kind == "node":
                approved_nodes += 1
            else:
                approved_relations += 1
            return True
        except (ReviewError, HTTPException) as exc:
            detail = getattr(exc, "detail", str(exc))
            skipped.append({"entity_id": entity_id, "status": status, "reason": str(detail)})
            return False

    for ref in refs:
        if ref.get("kind") != "node" or ref.get("status") == "active":
            continue
        node_id = str(ref.get("node_id") or "")
        if node_id in seen:
            continue
        seen.add(node_id)
        _try_approve(
            "node", ref.get("status"), node_id,
            lambda node_id=node_id: service.review_node(
                filename, node_id, "approve",
                request.reviewer or "未署名", request.note or "补审规则依赖",
            ),
        )

    seen_relations = set()
    for ref in refs:
        if ref.get("kind") != "relation" or ref.get("status") == "active":
            continue
        key = (
            str(ref.get("source_node_id") or ""),
            str(ref.get("relation_code") or ""),
            str(ref.get("target_node_id") or ""),
        )
        entity_id = "|".join(key)
        if entity_id in seen_relations:
            continue
        seen_relations.add(entity_id)
        _try_approve(
            "relation", ref.get("status"), entity_id,
            lambda key=key: service.review_relation(
                filename, key[0], key[1], key[2], "approve",
                request.reviewer or "未署名", request.note or "补审规则依赖",
            ),
        )

    after = _dependency_result(rule)
    rule_status_before = str(rule.get("status") or "draft")
    gate_error = ""
    rule_status = rule_status_before
    if rule_status_before == "blocked":
        try:
            rule_after = apply_rule_review(
                rule_id, "approve", request.reviewer, request.note or "依赖已通过，恢复启用"
            )
            rule_status = rule_after.get("status")
        except HTTPException as exc:
            gate_error = (
                exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
            )
    else:
        refreshed = next(
            (r for r in _load_all_rules() if r.get("rule_id") == rule_id), None
        )
        rule_status = str(refreshed.get("status") or rule_status_before) if refreshed else rule_status_before

    return {
        "rule_id": rule_id,
        "approved_nodes": approved_nodes,
        "approved_relations": approved_relations,
        "skipped": skipped,
        "missing": missing,
        "dependency_status": after.get("dependency_status"),
        "review_issues": after.get("review_issues") or [],
        "gate_error": gate_error,
        "rule_status": rule_status,
        "rule_status_before": rule_status_before,
    }


def _rule_quality_ok(rule: Dict[str, Any]) -> bool:
    if str(rule.get("rule_type") or "diagnosis") == "treatment":
        return True
    text = json.dumps(rule.get("conditions"), ensure_ascii=False)
    if '"lab_concept"' in text or '"symptom"' in text:
        return True
    if any(code in text for code in GENERIC_FINDING_BLOCKLIST):
        return False
    return True


@router.post("/activate-consistent")
async def activate_consistent(request: ActivateConsistentRequest):
    """Bulk-activate draft rules that pass graph sync and optional quality gate."""
    rules = _load_all_rules()
    target_domain = normalize_domain(request.domain) if request.domain else None
    selected_ids = set(request.rule_ids or [])
    candidates = []
    skipped = []
    for rule in rules:
        if rule.get("status") not in {"draft", "testing", "blocked"}:
            continue
        if target_domain and normalize_domain(rule.get("domain") or "western") != target_domain:
            continue
        if selected_ids and rule.get("rule_id") not in selected_ids:
            continue
        if request.quality_only and not _rule_quality_ok(rule):
            skipped.append({"rule_id": rule.get("rule_id"), "reason": "未通过规则质量检查"})
            continue
        candidates.append(rule)

    if candidates:
        sync = check_rule_sync(candidates)
        by_id = {item["rule_id"]: item for item in sync["rules"]}
        activated = 0
        audit_records = []
        for rule in candidates:
            result = by_id.get(rule.get("rule_id"), {})
            if result.get("semantic_status") != "consistent":
                skipped.append({
                    "rule_id": rule.get("rule_id"),
                    "reason": "; ".join(result.get("issues") or ["图谱语义不一致"]),
                })
                continue
            if result.get("dependency_status") != "passed":
                blockers = _activation_blockers(result)
                skipped.append({
                    "rule_id": rule.get("rule_id"),
                    "reason": "依赖未通过审核: " + "; ".join(
                        f"{item.get('node_id') or item.get('relation_code')}={item.get('status')}"
                        for item in blockers
                    ),
                })
                continue
            before = rule.get("status")
            rule["status"] = "active"
            activated += 1
            audit_records.append({
                "action": "approve",
                "entity_type": "rule",
                "entity_id": rule.get("rule_id"),
                "graph_filename": rule.get("graph_filename", ""),
                "before_status": before,
                "after_status": "active",
                "reviewer": "批量审核",
                "note": "批量启用一致规则",
                "affected_rules": [],
            })
        _save_all_rules(rules)
        for record in audit_records:
            append_audit(record, REVIEW_AUDIT_PATH)
    else:
        activated = 0
    return {
        "activated": activated,
        "skipped": len(skipped),
        "details": skipped[:20],
    }


@router.get("/{rule_id}")
async def get_rule(rule_id: str):
    for r in _load_all_rules():
        if r.get("rule_id") == rule_id:
            return r
    raise HTTPException(404, "规则未找到")


@router.put("/{rule_id}")
async def update_rule(rule_id: str, rule: Dict[str, Any]):
    rules = _load_all_rules()
    for i, r in enumerate(rules):
        if r.get("rule_id") == rule_id:
            if rule.get("graph_filename"):
                normalize_graph_filename(str(rule["graph_filename"]))
            rule["clinical_intent"] = normalize_clinical_intent(rule)
            validate_rule_concepts(rule)
            _ensure_active_rule_dependencies(rule)
            rules[i] = rule
            _save_all_rules(rules)
            record_snapshot(rule, "update")
            return rule
    raise HTTPException(404, "规则未找到")


@router.post("")
async def create_rule(rule: Dict[str, Any]):
    rules = _load_all_rules()
    if any(r.get("rule_id") == rule.get("rule_id") for r in rules):
        raise HTTPException(409, "规则ID已存在")
    if rule.get("graph_filename"):
        normalize_graph_filename(str(rule["graph_filename"]))
    rule["clinical_intent"] = normalize_clinical_intent(rule)
    validate_rule_concepts(rule)
    _ensure_active_rule_dependencies(rule)
    rules.append(rule)
    _save_all_rules(rules)
    record_snapshot(rule, "create", actor=str(rule.get("created_by") or ""))
    return rule


class RuleImportRequest(BaseModel):
    rules: List[Dict[str, Any]]
    graph_filename: str = ""
    template_id: str = ""


def _resolve_import_graph_filename(request: RuleImportRequest) -> str:
    configured = str(request.graph_filename or "").strip()
    if configured:
        return normalize_graph_filename(configured)
    if request.template_id:
        try:
            template = ExtractionTemplateRepository().get(
                request.template_id, allow_retired=True
            )
            return normalize_graph_filename(resolve_template_graph_filename(template))
        except Exception:
            return ""
    return ""


@router.post("/import")
async def import_rules(request: RuleImportRequest):
    """Import draft rules generated by guideline extraction."""
    rules = _load_all_rules()
    existing = {r.get("rule_id"): i for i, r in enumerate(rules)}
    imported = 0
    replaced = 0
    errors = []
    now = datetime.now(timezone.utc).isoformat()
    target_graph = _resolve_import_graph_filename(request)

    for index, raw in enumerate(request.rules):
        try:
            validated = Rule(**raw)
            item = validated.model_dump()
            if target_graph and not item.get("graph_filename"):
                item["graph_filename"] = target_graph
            if target_graph:
                item["graph_filename"] = normalize_graph_filename(item["graph_filename"])
            item.setdefault("status", "draft")
            item.setdefault("created_at", now)
            item["updated_at"] = now
            unknown = []
            for code in collect_concept_codes(item):
                try:
                    concept_vocab.get(code)
                except Exception:
                    unknown.append(code)
            if unknown:
                errors.append({
                    "index": index,
                    "rule_id": raw.get("rule_id", ""),
                    "error": f"条件引用的概念词汇未定义: {', '.join(sorted(set(unknown)))}",
                })
                continue
            if item["rule_id"] in existing:
                rules[existing[item["rule_id"]]] = item
                record_snapshot(item, "import")
                replaced += 1
            else:
                rules.append(item)
                existing[item["rule_id"]] = len(rules) - 1
                imported += 1
        except Exception as exc:
            errors.append({"index": index, "rule_id": raw.get("rule_id", ""), "error": str(exc)})

    _save_all_rules(rules)
    return {"imported": imported, "replaced": replaced, "errors": errors, "total": len(rules)}


@router.delete("/{rule_id}")
async def delete_rule(rule_id: str):
    rules = _load_all_rules()
    removed = next((r for r in rules if r.get("rule_id") == rule_id), None)
    rules = [r for r in rules if r.get("rule_id") != rule_id]
    _save_all_rules(rules)
    if removed is not None:
        record_snapshot(removed, "delete")
    return {"deleted": rule_id}


@router.post("/{rule_id}/activate")
async def activate_rule(rule_id: str):
    rules = _load_all_rules()
    for r in rules:
        if r.get("rule_id") == rule_id:
            result = _dependency_result(r)
            if result.get("semantic_status") != "consistent":
                issues = result.get("issues") or ["图谱语义不一致"]
                raise HTTPException(422, "规则与知识图谱语义不一致: " + "; ".join(issues))
            blockers = _activation_blockers(result)
            if blockers:
                raise HTTPException(422, {
                    "error": "规则依赖未通过审核",
                    "dependencies": blockers,
                })
            r["status"] = "active"
            r["updated_at"] = datetime.now(timezone.utc).isoformat()
            _save_all_rules(rules)
            return r
    raise HTTPException(404, "规则未找到")


@router.get("/{rule_id}/graph-dependencies")
async def graph_dependencies(rule_id: str):
    for rule in _load_all_rules():
        if rule.get("rule_id") == rule_id:
            return _dependency_result(rule)
    raise HTTPException(404, "规则未找到")


@router.get("/{rule_id}/history")
async def rule_history(rule_id: str):
    return list_snapshots(rule_id)


@router.post("/{rule_id}/history/{seq}/restore")
async def restore_rule_snapshot(rule_id: str, seq: int, request: RuleRestoreRequest):
    entry = get_snapshot(rule_id, seq)
    if entry is None:
        raise HTTPException(404, "历史版本不存在")
    restored = entry.get("rule") or {}
    if str(restored.get("status") or "draft") == "active":
        _ensure_active_rule_dependencies(restored)
    rules = _load_all_rules()
    for i, r in enumerate(rules):
        if r.get("rule_id") == rule_id:
            restored["updated_at"] = datetime.now(timezone.utc).isoformat()
            restored["restored_from"] = seq
            rules[i] = restored
            _save_all_rules(rules)
            record_snapshot(restored, "rollback", actor=request.reviewer)
            append_audit({
                "action": "rollback",
                "entity_type": "rule",
                "entity_id": rule_id,
                "graph_filename": restored.get("graph_filename", ""),
                "before_status": r.get("status"),
                "after_status": restored.get("status"),
                "reviewer": request.reviewer or "未署名",
                "note": request.note or f"恢复到 v{seq}",
                "affected_rules": [],
            }, REVIEW_AUDIT_PATH)
            return restored
    raise HTTPException(404, "规则未找到")


@router.post("/{rule_id}/review")
async def review_rule(rule_id: str, request: RuleReviewRequest):
    return apply_rule_review(rule_id, request.action, request.reviewer, request.note)


@router.post("/{rule_id}/retire")
async def retire_rule(rule_id: str):
    rules = _load_all_rules()
    for r in rules:
        if r.get("rule_id") == rule_id:
            before = r.get("status")
            r["status"] = "retired"
            r["updated_at"] = datetime.now(timezone.utc).isoformat()
            _save_all_rules(rules)
            append_audit({
                "action": "retire",
                "entity_type": "rule",
                "entity_id": rule_id,
                "graph_filename": r.get("graph_filename", ""),
                "before_status": before,
                "after_status": "retired",
                "reviewer": "未署名",
                "note": "",
                "affected_rules": [],
            }, REVIEW_AUDIT_PATH)
            return r
    raise HTTPException(404, "规则未找到")

