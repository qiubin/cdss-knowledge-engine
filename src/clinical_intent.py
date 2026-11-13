"""Clinical-purpose vocabulary and backward-compatible intent inference."""
from __future__ import annotations

from typing import Any, Dict


CLINICAL_INTENT_LABELS: Dict[str, str] = {
    "suspected_diagnosis": "疑似诊断",
    "diagnosis_confirmation": "确诊判断",
    "differential_diagnosis": "鉴别诊断",
    "diagnosis_exclusion": "排除诊断",
    "risk_stratification": "风险分层",
    "severity_assessment": "严重程度/分期评估",
    "treatment_recommendation": "治疗方案推荐",
    "treatment_selection": "方案选择/调整",
    "contraindication_check": "禁忌与慎用判断",
    "dose_adjustment": "剂量调整",
    "monitoring_plan": "监测计划",
    "follow_up_plan": "随访与复查计划",
    "syndrome_identification": "辨证判断",
}

DEFAULT_INTENT_BY_RULE_TYPE = {
    "diagnosis": "diagnosis_confirmation",
    "treatment": "treatment_recommendation",
    "contraindication": "contraindication_check",
    "monitoring": "monitoring_plan",
    "tcm_pattern": "syndrome_identification",
    "tcm_treatment": "treatment_recommendation",
    "tcm_prescription": "treatment_recommendation",
    "tcm_contraindication": "contraindication_check",
}


def infer_clinical_intent(rule_type: str, text: str = "") -> str:
    """Infer a stable purpose from type and human-readable guideline text."""
    kind = str(rule_type or "diagnosis").strip()
    content = str(text or "").lower()
    if any(token in content for token in ("随访", "复查", "复诊")):
        return "follow_up_plan"
    if any(token in content for token in ("剂量调整", "调整剂量", "减量", "加量")):
        return "dose_adjustment"
    if kind in {"diagnosis", "tcm_pattern"}:
        if any(token in content for token in ("鉴别", "区分")):
            return "differential_diagnosis"
        if any(token in content for token in ("排除", "除外")):
            return "diagnosis_exclusion"
        if any(token in content for token in ("风险分层", "危险分层", "风险评估")):
            return "risk_stratification"
        if any(token in content for token in ("严重程度", "病情分级", "疾病分期", "临床分期")):
            return "severity_assessment"
        if any(token in content for token in ("疑似", "疑诊", "初筛", "筛查", "诊断线索", "初步诊断")):
            return "suspected_diagnosis"
    if kind in {"treatment", "tcm_treatment", "tcm_prescription"} and any(
        token in content for token in ("首选", "替代", "升级", "降阶", "方案选择", "换用")
    ):
        return "treatment_selection"
    return DEFAULT_INTENT_BY_RULE_TYPE.get(kind, "diagnosis_confirmation")


def normalize_clinical_intent(rule: Dict[str, Any]) -> str:
    """Keep valid explicit classifications; infer a default for old/imported rules."""
    explicit = str(rule.get("clinical_intent") or "").strip()
    if explicit in CLINICAL_INTENT_LABELS:
        return explicit
    conclusion = rule.get("conclusion") if isinstance(rule.get("conclusion"), dict) else {}
    source = rule.get("source") if isinstance(rule.get("source"), dict) else {}
    text = " ".join(str(value or "") for value in (
        rule.get("name"), rule.get("condition_text"), conclusion.get("message"),
        conclusion.get("treatment_name"), source.get("section"), source.get("quote"),
    ))
    return infer_clinical_intent(str(rule.get("rule_type") or "diagnosis"), text)
