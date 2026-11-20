"""Reviewed clinical specifications, kept in graphs until explicit rule generation."""
from __future__ import annotations
import copy
import hashlib
import json
from datetime import datetime, timezone
from fastapi import HTTPException
from src.models.rule import Rule
from src.review.metadata import node_content_hash, relation_content_hash
from src.review.quality import verify_source, statement_for

CHECKS = ("entities", "logic", "scope", "source")
def now():
    return datetime.now(timezone.utc).isoformat()
def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()

def stage_bases(graph, candidates, graph_filename):
    groups = graph.setdefault("clinical_bases", [])
    for raw in candidates:
        spec = copy.deepcopy(raw)
        spec["graph_filename"] = graph_filename
        spec["status"] = "draft"
        try:
            spec = Rule(**spec).model_dump()
        except Exception:
            pass  # Keep incomplete extraction visible for correction; approval will block it.
        identity = [spec.get("source", {}).get("guideline_id"), spec.get("rule_id") or digest(spec)]
        basis_id = "CB-" + digest(identity)[:20]
        existing = next((g for g in groups if g["basis_id"] == basis_id), None)
        if existing and (existing.get("generated_rule_id") or existing.get("status") == "active") and existing["specification"] != spec:
            basis_id += "-" + digest(spec)[:8]
            existing = next((g for g in groups if g["basis_id"] == basis_id), None)
        if existing and (existing.get("disposition") == "discarded" or existing["specification"] == spec):
            continue
        if existing:
            existing.update(specification=spec, status="draft", checks={}, updated_at=now())
        else:
            groups.append({"basis_id": basis_id, "name": spec.get("name", basis_id), "specification": spec,
                           "status": "draft", "checks": {}, "scope_summary": "", "updated_at": now()})
    return groups


def check_basis(service, filename, graph, basis):
    issues = []
    spec = basis["specification"]
    from src.domains.western.recommendations import recommendation_issues
    issues.extend(recommendation_issues(spec))
    if (spec.get("clinical_context") or {}).get("schema_version") == 3:
        from src.domains.tcm.clinical_schema import validate_tree
        issues.extend(validate_tree(spec.get("conditions") or {}))
        if (spec.get("clinical_context") or {}).get("logic_origin") == "ai_suggested":
            issues.append("请确认AI建议组合，将逻辑来源设为human_defined后保存")
    if (spec.get("clinical_context") or {}).get("missing_information"):
        issues.append("临床陈述仍有待补信息，请在编辑器中补全并核对后移除待补项")
    try:
        Rule(**spec)
    except Exception as error:
        issues.append("条件或结论结构不完整：" + str(error))
    def walk(condition):
        if not isinstance(condition, dict) or not condition.get("type"):
            issues.append("条件为空")
            return
        if condition.get("type") in ("group", "weighted_group"):
            children = condition.get("children") or []
            if not isinstance(children, list):
                issues.append("子条件必须是列表")
                children = []
            if not children:
                issues.append("条件组没有子条件")
            if condition.get("type") == "group" and condition.get("operator") not in ("AND", "OR", "NOT"):
                issues.append("还差一步：请选择这些条件是全部满足还是任意一项")
            if condition.get("operator") == "NOT" and len(children) != 1:
                issues.append("非条件组必须且只能包含一个子条件")
            if condition.get("type") == "weighted_group" and condition.get("threshold") is None:
                issues.append("加权条件组缺少评分阈值")
            for child in [*children, *(condition.get("required_children") or [] if isinstance(condition.get("required_children") or [], list) else [])]:
                walk(child)
    scenario = basis.get("scenario_review") or {}
    root = spec.get("conditions") or {}
    implicit_treatment = (spec.get("rule_type") == "treatment" and scenario.get("scope_confirmed")
                          and root.get("type") == "group" and root.get("operator") == "AND"
                          and root.get("children") == [] and spec.get("diagnosis_prerequisite") in {"confirmed", "suspected"})
    if not implicit_treatment and not ((spec.get("clinical_context") or {}).get("western_recommendation", {}).get("scope_confirmed") and root.get("type") == "group" and root.get("operator") == "AND" and root.get("children") == []):
        walk(root)
    source = spec.get("source") if isinstance(spec.get("source"), dict) else {}
    recommendation = (spec.get("clinical_context") or {}).get("western_recommendation") or {}
    if not isinstance(recommendation, dict): recommendation = {}
    for action in recommendation.get("actions") or []:
        action_source = action.get("source") or source
        action_provenance = verify_source(action_source, service.graph_dir.parent / "uploads")
        issues.extend(action_provenance["issues"])
    provenance = verify_source(source, service.graph_dir.parent / "uploads")
    issues.extend(provenance["issues"])
    if not source.get("guideline_id") or not str(source.get("quote") or "").strip():
        issues.append("缺少指南标识或原文引文")
    if not source.get("page_start") or not source.get("line_start") or not source.get("line_end"):
        issues.append("缺少原文页码或行号定位")
    # verify_source validates page-aware ranges: a later page may legitimately
    # end at a smaller line number than the first page starts.
    try:
        sync = service._sync([{**spec, "graph_filename": filename}])["rules"][0]
    except Exception as error:
        sync = {"semantic_status": "inconsistent", "issues": ["无法解析图谱依赖：" + str(error)], "references": []}
    if sync.get("semantic_status") != "consistent":
        issues.extend(sync.get("issues") or ["图谱语义链路不完整"])
    refs = sync.get("references") or []
    if not refs:
        issues.append("没有可核对的图谱依赖")
    if sync.get("dependency_status") != "passed":
        issues.append("所需实体或关系尚未全部审核通过")
    snapshots = []
    if scenario:
        from src.api.routes.scenarios import inventory
        selected = {item["id"]: item for item in inventory(graph, spec.get("disease_code"))}
        for selection in scenario.get("selections") or []:
            item = selected.get(selection["relation_id"])
            if not item or not item["approved"] or item["fingerprint"] != selection["fingerprint"]:
                issues.append("场景关联已变化或未通过，请重新核对场景依据")
            snapshots.append([selection["relation_id"], item["fingerprint"] if item else None])
    for ref in refs:
        if ref.get("kind") == "node":
            node = graph.get("nodes", {}).get(ref.get("node_id"))
            snapshots.append([ref.get("node_id"), node_content_hash(node) if node else None])
        else:
            key = (ref.get("source_node_id"), ref.get("relation_code"), ref.get("target_node_id"))
            relation = graph.get("relations", {}).get(key)
            snapshots.append([list(key), relation_content_hash(relation) if relation else None])
    # Include flat evidence too: its parameters can change without changing a node.
    links = [link for link in graph.get("disease_evidence", []) if link.get("disease_code") == spec.get("disease_code")]
    evidence_ids = {link.get("evidence_id") for link in links}
    def evidence_refs(condition):
        if not isinstance(condition, dict):
            return
        if condition.get("evidence_id"):
            evidence_ids.add(condition["evidence_id"])
        for field in ("children", "required_children"):
            children = condition.get(field) or []
            for child in children if isinstance(children, list) else []:
                evidence_refs(child)
    evidence_refs(spec.get("conditions") or {})
    evidences = [item for item in graph.get("evidences", []) if item.get("id") in evidence_ids]
    fingerprint = digest([spec, basis.get("scope_summary"), snapshots, evidences, links, scenario])
    changed = bool(basis.get("approved_fingerprint") and basis["approved_fingerprint"] != fingerprint)
    return {"issues": list(dict.fromkeys(issues)), "references": refs, "fingerprint": fingerprint,
            "provenance": provenance, "statement": statement_for(spec, basis.get("scope_summary") or ""),
            "status": "draft" if (changed or issues) and basis.get("status") == "active" else basis.get("status", "draft"), "changed": changed}


def get_basis(graph, basis_id):
    basis = next((g for g in graph.get("clinical_bases", []) if g["basis_id"] == basis_id), None)
    if basis is None:
        raise HTTPException(404, "诊疗依据组不存在")
    return basis


def require_approved(service, filename, graph, basis):
    if basis.get("disposition") == "discarded":
        raise HTTPException(409, "该依据已不采纳，不生成规则")
    check = check_basis(service, filename, graph, basis)
    if check["status"] != "active" or check["issues"] or check["changed"]:
        raise HTTPException(409, "诊疗依据尚未通过或已变化，请先复核：" + "；".join(check["issues"]))
    return check
