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
from .kg import normalize_graph_filename

router = APIRouter(prefix="/api/inference", tags=["inference"])

ROOT = Path(__file__).parent.parent.parent.parent


class InferenceRequest(BaseModel):
    patient: Dict[str, Any]
    department: Optional[str] = None
    domain: Optional[str] = None


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
    active_diagnosis = [
        rule for rule in rules
        if rule.rule_type in diagnosis_types and rule.status == "active"
    ]
    draft_diagnosis = [
        rule for rule in rules
        if rule.rule_type in diagnosis_types and rule.status == "draft"
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
        reasons.append(
            f"诊断规则均未启用（草稿 {len(draft_diagnosis)} 条）；请先在规则管理启用，或使用批量启用"
        )
    if not active_treatment and not draft_treatment:
        reasons.append(
            "没有治疗规则；本地模板解析不生成治疗规则，请使用大模型解析或手工创建治疗规则"
        )
    return {
        "domain": target,
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

    engine = InferenceEngine(
        rules, kg, standards, vocab, graph_factory,
        domain=request.domain or "western",
    )
    conclusions = engine.run(patient, department=request.department)

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
        rules, request.domain or "western", sorted(graph_clients)
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
        "domain": request.domain or "western",
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

