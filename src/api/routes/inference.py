"""Inference testing API."""
from __future__ import annotations

import json

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.kg.client import JsonGraphClient
from src.kg.schema import ConceptVocabulary
from src.domains import normalize_domain
from src.models.patient import PatientSnapshot
from src.rules.repository import JsonRuleRepository
from src.standards.repository import JsonStandardRepository
from src.engine import InferenceEngine
from src.engine.factory import create_rule_engine
from .kg import normalize_graph_filename

router = APIRouter(prefix="/api/inference", tags=["inference"])

ROOT = Path(__file__).parent.parent.parent.parent


class InferenceRequest(BaseModel):
    patient: Dict[str, Any]
    department: Optional[str] = None
    domain: Optional[str] = None


class InferenceTestCaseRequest(BaseModel):
    patient: Dict[str, Any]
    expected: Optional[Dict[str, Any]] = None
    case_id: Optional[str] = None
    department: Optional[str] = None
    domain: Optional[str] = "tcm"
    include_draft_patterns: bool = True


def _rule_diagnostics(rule_repo: JsonRuleRepository, domain: str, graph_files: List[str]) -> Dict[str, Any]:
    target = normalize_domain(domain)
    if target == "tcm":
        diagnosis_types = {"tcm_pattern"}
        treatment_types = {"tcm_treatment", "tcm_prescription", "tcm_contraindication"}
    else:
        diagnosis_types = {"diagnosis", "monitoring"}
        treatment_types = {"treatment"}
    rules = [
        rule for rule in rule_repo.list_all()
        if normalize_domain(getattr(rule, "domain", "western")) == target
    ]
    diagnosis_rules = [rule for rule in rules if rule.rule_type in diagnosis_types]
    active_diagnosis = [
        rule for rule in diagnosis_rules if rule.status == "active"
    ]
    draft_diagnosis = [
        rule for rule in diagnosis_rules if rule.status == "draft"
    ]
    active_treatment = [
        rule for rule in rules
        if rule.rule_type in treatment_types and rule.status == "active"
    ]
    draft_treatment = [
        rule for rule in rules
        if rule.rule_type in treatment_types and rule.status == "draft"
    ]
    reasons: List[str] = []
    if not active_diagnosis:
        if not diagnosis_rules:
            reasons.append("当前领域没有诊断规则；请先导入指南生成规则，或手工创建规则")
        else:
            reasons.append(
                f"诊断规则均未启用（草稿 {len(draft_diagnosis)} 条）；请先在规则管理启用，或使用批量启用"
            )
    if not active_treatment and not draft_treatment:
        reasons.append(
            "没有治疗规则；本地模板解析不生成治疗规则，请使用大模型解析或手工创建治疗规则"
        )
    return {
        "domain": target,
        "diagnosis_rules": len(diagnosis_rules),
        "active_diagnosis_rules": len(active_diagnosis),
        "draft_diagnosis_rules": len(draft_diagnosis),
        "active_treatment_rules": len(active_treatment),
        "draft_treatment_rules": len(draft_treatment),
        "active_diagnosis_rule_ids": [rule.rule_id for rule in active_diagnosis[:10]],
        "graph_files": graph_files,
        "reasons": reasons,
    }


@router.post("/run")
async def run_inference(request: InferenceRequest):
    """Run inference on a patient snapshot and return conclusions."""
    try:
        patient = PatientSnapshot(**request.patient)
    except Exception as e:
        raise HTTPException(422, f"患者数据格式错误: {str(e)}")
    return _inference_result(patient, request.domain or "western", request.department)


def _inference_result(
    patient: PatientSnapshot,
    domain: str,
    department: Optional[str] = None,
    include_draft_patterns: bool = False,
):

    vocab = ConceptVocabulary()
    vocab.load(str(ROOT / "data" / "vocab" / "concepts.json"))
    kg = JsonGraphClient(str(ROOT / "data" / "kg" / "stemi_graph.json"))
    standards = JsonStandardRepository(str(ROOT / "data" / "standards"))
    rules = JsonRuleRepository(str(ROOT / "data" / "rules"))

    graph_clients: Dict[str, JsonGraphClient] = {}

    def graph_factory(filename: str) -> JsonGraphClient:
        try:
            filename = normalize_graph_filename(filename)
        except Exception as exc:
            raise HTTPException(422, str(exc.detail)) from exc
        if filename not in graph_clients:
            graph_path = ROOT / "data" / "kg" / filename
            if not graph_path.exists():
                raise HTTPException(503, f"规则引用的知识图谱不存在: {filename}")
            graph_clients[filename] = JsonGraphClient(str(graph_path))
        return graph_clients[filename]

    engine = create_rule_engine(
        rules, kg, standards, vocab, graph_factory,
        domain=domain,
    )
    conclusions = engine.run(
        patient,
        department=department,
        include_draft_patterns=include_draft_patterns,
    )

    all_conclusions = [c.model_dump() for c in conclusions]
    diagnosis_conclusions = [
        c for c in all_conclusions
        if c.get("rule_type") not in {"treatment", "tcm_treatment", "tcm_prescription"}
    ]
    treatment_conclusions = [
        c for c in all_conclusions
        if c.get("rule_type") in {"treatment", "tcm_treatment", "tcm_prescription"}
    ]

    diagnostics = _rule_diagnostics(
        rules, domain, sorted(graph_clients)
    )
    if not conclusions:
        if diagnostics["active_diagnosis_rules"]:
            diagnostics["reasons"].append(
                f"已启用 {diagnostics['active_diagnosis_rules']} 条诊断规则，但当前患者数据未命中规则条件"
            )
        if not diagnostics["active_treatment_rules"]:
            diagnostics["reasons"].append("没有已启用的治疗规则，无法给出治疗方案推荐")
        elif not [c for c in conclusions if c.rule_type == "treatment"]:
            diagnostics["reasons"].append("治疗阶段未触发：需要先命中同一疾病的诊断规则")
    return {
        "patient_id": patient.patient_id,
        "hospital_id": patient.hospital_id,
        "domain": domain,
        "engine": engine.describe(),
        "conclusion_count": len(conclusions),
        "conclusions": all_conclusions,
        "diagnosis_conclusions": diagnosis_conclusions,
        "treatment_conclusions": treatment_conclusions,
        "graph_files": sorted(graph_clients),
        "diagnostics": diagnostics,
    }


@router.get("/vocab")
async def get_vocabulary():
    vocab = ConceptVocabulary()
    vocab.load(str(ROOT / "data" / "vocab" / "concepts.json"))
    return [c.model_dump() for c in vocab.list_all()]


PATIENTS_DIR = ROOT / "data" / "patients"
CASES_PATH = PATIENTS_DIR / "cases.json"


def _load_cases() -> List[Dict[str, Any]]:
    if not CASES_PATH.exists():
        return []
    try:
        payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        cases = payload.get("cases", []) if isinstance(payload, dict) else payload
        return [item for item in cases if isinstance(item, dict) and item.get("case_id")]
    except (OSError, json.JSONDecodeError):
        return []


def _load_case_patient(case: Dict[str, Any]) -> Dict[str, Any]:
    filename = Path(str(case.get("file") or "")).name
    if not filename.endswith(".json"):
        raise HTTPException(404, "病例文件无效")
    path = PATIENTS_DIR / filename
    if not path.exists():
        raise HTTPException(404, "示例患者不存在")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/demo-patients")
async def list_demo_patients(domain: Optional[str] = None):
    cases = _load_cases()
    if domain:
        cases = [item for item in cases if item.get("domain", "western") == domain]
    return cases


@router.get("/demo-patient")
async def get_demo_patient(
    domain: Optional[str] = None,
    case_id: Optional[str] = None,
):
    if case_id:
        case = next((item for item in _load_cases() if item.get("case_id") == case_id), None)
        if case is None:
            raise HTTPException(404, "示例病例不存在")
        return _load_case_patient(case)
    if (domain or "").strip().lower() in {"tcm", "中医"}:
        filename = "demo_tcm_patient.json"
    else:
        default_case = next(
            (item for item in _load_cases() if item.get("default")),
            None,
        )
        if default_case:
            return _load_case_patient(default_case)
        filename = "demo_stemi_patient.json"
    path = PATIENTS_DIR / filename
    if not path.exists():
        raise HTTPException(404, "示例患者不存在")
    return json.loads(path.read_text(encoding="utf-8"))


def _assertion(
    check_id: str,
    label: str,
    expected: Any,
    actual: Any,
    passed: bool,
    reason: str,
) -> Dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "expected": expected,
        "actual": actual,
        "passed": bool(passed),
        "reason": reason,
    }


def _evaluate_expected(
    expected: Optional[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    conclusions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not expected:
        return []
    patterns = [item for item in candidates if item.get("conclusion", {}).get("rule_type") == "tcm_pattern"]
    matched_patterns = [item for item in patterns if item.get("matched")]
    ranked = sorted(
        patterns,
        key=lambda item: (
            not bool(item.get("matched")),
            -float((item.get("conclusion") or {}).get("score") or 0.0),
            -int((item.get("conclusion") or {}).get("priority") or 0),
        ),
    )
    top = ranked[0]["conclusion"] if ranked else None
    actual_top = {
        "syndrome_code": (top or {}).get("anchor_code"),
        "syndrome_name": (top or {}).get("anchor_name"),
        "score": (top or {}).get("score"),
        "matched": bool(ranked and ranked[0].get("matched")),
    }
    checks: List[Dict[str, Any]] = []

    wanted_top = str(expected.get("top_syndrome") or "")
    top_match = next(
        (item for item in patterns if (item.get("conclusion") or {}).get("anchor_code") == wanted_top),
        None,
    )
    checks.append(_assertion(
        "top_syndrome",
        "首选证候",
        wanted_top,
        actual_top,
        bool(top and (top or {}).get("anchor_code") == wanted_top and ranked[0].get("matched")),
        (
            f"实际首选为 {actual_top['syndrome_name'] or '未命中'}"
            if wanted_top else "缺少 top_syndrome 预期"
        ),
    ))

    min_score = expected.get("min_score")
    target_score = (top_match or {}).get("conclusion", {}).get("score")
    checks.append(_assertion(
        "min_score",
        "最低评分",
        min_score,
        target_score,
        bool(min_score is None or (target_score is not None and float(target_score) >= float(min_score))),
        (
            f"目标证候评分 {target_score if target_score is not None else '无'}"
            if min_score is not None else "未设置最低评分"
        ),
    ))

    wanted_formula = str(expected.get("must_include_formula") or "")
    formulas = {
        item.get("formula_code") or item.get("formula_name")
        for item in conclusions
        if item.get("rule_type") in {"treatment", "tcm_prescription"}
    }
    checks.append(_assertion(
        "must_include_formula",
        "必须包含方剂",
        wanted_formula,
        sorted(item for item in formulas if item),
        not wanted_formula or wanted_formula in formulas,
        f"实际推荐方剂 {', '.join(sorted(item for item in formulas if item)) or '无'}" if wanted_formula else "未设置方剂断言",
    ))

    excluded = [str(item) for item in expected.get("must_not_trigger") or []]
    triggered = {
        (item.get("conclusion") or {}).get("anchor_code")
        for item in matched_patterns
    }
    violations = [item for item in excluded if item in triggered]
    checks.append(_assertion(
        "must_not_trigger",
        "不得触发的证候",
        excluded,
        sorted(item for item in triggered if item),
        not violations,
        f"意外触发：{', '.join(violations)}" if violations else "未出现禁止证候",
    ))
    return checks


@router.post("/test-case")
async def run_inference_test_case(request: InferenceTestCaseRequest):
    """Run a staged inference case and compare it with expected assertions."""
    try:
        patient = PatientSnapshot(**request.patient)
    except Exception as e:
        raise HTTPException(422, f"患者数据格式错误: {str(e)}")
    domain = request.domain or "tcm"
    if normalize_domain(domain) != "tcm":
        raise HTTPException(422, "中医辨证测试台当前只接受 domain=tcm")

    result = _inference_result(
        patient,
        domain,
        request.department,
        include_draft_patterns=request.include_draft_patterns,
    )
    engine = InferenceEngine(
        JsonRuleRepository(str(ROOT / "data" / "rules")),
        JsonGraphClient(str(ROOT / "data" / "kg" / "stemi_graph.json")),
        JsonStandardRepository(str(ROOT / "data" / "standards")),
        ConceptVocabulary(),
        None,
        domain=domain,
    )
    vocab = ConceptVocabulary()
    vocab.load(str(ROOT / "data" / "vocab" / "concepts.json"))
    engine.vocabulary = vocab

    graph_clients: Dict[str, JsonGraphClient] = {}
    def graph_factory(filename: str) -> JsonGraphClient:
        filename = normalize_graph_filename(filename)
        if filename not in graph_clients:
            graph_clients[filename] = JsonGraphClient(str(ROOT / "data" / "kg" / filename))
        return graph_clients[filename]
    engine.graph_client_factory = graph_factory
    candidates = engine.evaluate_tcm_pattern_candidates(
        patient,
        department=request.department,
        include_draft_patterns=request.include_draft_patterns,
    )

    rule_map = {rule.rule_id: rule for rule in engine.rule_repo.list_all()}
    for item in candidates:
        rule = rule_map.get((item.get("conclusion") or {}).get("rule_id"))
        item["graph_filename"] = getattr(rule, "graph_filename", None)
        item["condition_text"] = getattr(rule, "condition_text", "")
        item["source"] = rule.source.model_dump() if getattr(rule, "source", None) else {}

    checks = _evaluate_expected(request.expected, candidates, result["conclusions"])
    dependencies = []
    referenced_ids = {item["conclusion"]["rule_id"] for item in candidates}
    referenced_ids.update(item.get("rule_id") for item in result["conclusions"])
    for rule_id in sorted(referenced_ids):
        rule = rule_map.get(rule_id)
        if rule:
            dependencies.append({
                "rule_id": rule.rule_id,
                "rule_name": rule.name,
                "rule_type": rule.rule_type,
                "status": rule.status,
                "graph_filename": rule.graph_filename,
                "condition_text": rule.condition_text,
                "source": rule.source.model_dump() if rule.source else {},
            })

    return {
        **result,
        "case_id": request.case_id,
        "expected": request.expected or {},
        "checks": checks,
        "passed": bool(checks) and all(item["passed"] for item in checks),
        "ranked_syndromes": candidates,
        "dependencies": dependencies,
    }


@router.get("/demo-case")
async def get_demo_case(case_id: str):
    case = next((item for item in _load_cases() if item.get("case_id") == case_id), None)
    if case is None:
        raise HTTPException(404, "示例病例不存在")
    return {"case": case, "patient": _load_case_patient(case)}

