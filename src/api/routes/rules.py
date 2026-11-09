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


def _load_all_rules() -> List[Dict]:
    # After any management operation, managed_rules.json is the canonical file.
    if MANAGED_RULES_PATH.exists():
        rules = json.loads(MANAGED_RULES_PATH.read_text(encoding="utf-8"))
    else:
        rules = []
        for f in RULES_DIR.glob("*.json"):
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
        conditions = rule.get("conditions")
        labels.annotate(conditions)
        if not rule.get("condition_text"):
            rule["condition_text"] = labels.summarize(conditions)
        if not rule.get("condition_text") and rule.get("rule_type") in {
            "treatment", "tcm_treatment", "tcm_prescription", "tcm_contraindication",
        }:
            rule["condition_text"] = "同疾病诊断成立后触发"
    return rules


def _save_all_rules(rules: List[Dict]):
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    MANAGED_RULES_PATH.write_text(
        json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class GraphSemanticChecker:
    """Check rule conditions against the unified diagnostic-evidence graph."""

    def __init__(self, graph: Dict[str, Any]):
        self.graph = graph
        self.node_ids = {
            item.get("id") for item in self._as_list(graph.get("nodes"))
            if isinstance(item, dict) and item.get("id")
        }
        self.relation_keys = {
            (item.get("source_node_id"), item.get("relation_code"), item.get("target_node_id"))
            for item in self._as_list(graph.get("relations"))
            if isinstance(item, dict)
        }
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

    def _add_node_issue(self, issues: List[str], node_type: str, code: Any) -> bool:
        node_id = self._node_id(node_type, code)
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
        if (source, relation_code, target) not in self.relation_keys:
            issues.append(f"图谱缺少关系 {relation_code}: {source} -> {target}")

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
        disease_code = str(rule.get("disease_code") or "")

        if not disease_code:
            issues.append("目标疾病编码为空")
        elif disease_code not in self.diseases:
            issues.append(f"图谱缺少疾病 {disease_code}")
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
        return {
            "rule_id": rule.get("rule_id", ""),
            "name": rule.get("name", ""),
            "status": rule.get("status", ""),
            "disease_code": disease_code,
            "concept_codes": concepts,
            "semantic_status": "inconsistent" if issues else "consistent",
            "issues": issues,
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


def check_rule_sync(rules: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Check each rule against the graph selected by its graph_filename."""
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
        graph = _load_graph(filename)
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
    return {
        "total": len(results),
        "consistent_count": consistent,
        "inconsistent_count": len(results) - consistent,
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
    return rules


@router.get("/sync-check")
async def sync_check():
    return check_rule_sync(_load_all_rules())


class ActivateConsistentRequest(BaseModel):
    domain: Optional[str] = None
    rule_ids: Optional[List[str]] = None
    quality_only: bool = False


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
        if rule.get("status") not in {"draft", "testing"}:
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
        for rule in candidates:
            result = by_id.get(rule.get("rule_id"), {})
            if result.get("semantic_status") != "consistent":
                skipped.append({
                    "rule_id": rule.get("rule_id"),
                    "reason": "; ".join(result.get("issues") or ["图谱语义不一致"]),
                })
                continue
            rule["status"] = "active"
            activated += 1
        _save_all_rules(rules)
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
            validate_rule_concepts(rule)
            rules[i] = rule
            _save_all_rules(rules)
            return rule
    raise HTTPException(404, "规则未找到")


@router.post("")
async def create_rule(rule: Dict[str, Any]):
    rules = _load_all_rules()
    if any(r.get("rule_id") == rule.get("rule_id") for r in rules):
        raise HTTPException(409, "规则ID已存在")
    if rule.get("graph_filename"):
        normalize_graph_filename(str(rule["graph_filename"]))
    validate_rule_concepts(rule)
    rules.append(rule)
    _save_all_rules(rules)
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
    rules = [r for r in rules if r.get("rule_id") != rule_id]
    _save_all_rules(rules)
    return {"deleted": rule_id}


@router.post("/{rule_id}/activate")
async def activate_rule(rule_id: str):
    rules = _load_all_rules()
    for r in rules:
        if r.get("rule_id") == rule_id:
            result = check_rule_sync([r])
            if result["inconsistent_count"]:
                issues = result["rules"][0]["issues"]
                raise HTTPException(422, "规则与知识图谱语义不一致: " + "; ".join(issues))
            r["status"] = "active"
            _save_all_rules(rules)
            return r
    raise HTTPException(404, "规则未找到")


@router.post("/{rule_id}/retire")
async def retire_rule(rule_id: str):
    rules = _load_all_rules()
    for r in rules:
        if r.get("rule_id") == rule_id:
            r["status"] = "retired"
            _save_all_rules(rules)
            return r
    raise HTTPException(404, "规则未找到")

