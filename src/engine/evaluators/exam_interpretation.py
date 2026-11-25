"""Interpret structured exam values; free report text is never a positive finding."""
from .base import EvaluationResult
from src.models.standard import LabInterpretationStandard
from src.standards.validation import standard_issues


def unknown(message):
    return EvaluationResult(False, evidence_description=message, detail={"evaluation_status": "unknown"})


def compare_numeric(value, operator, lower, upper=None):
    import math
    if lower is None or not math.isfinite(value) or not math.isfinite(lower) or (upper is not None and not math.isfinite(upper)): return None
    operations = {">": lambda: value > lower, ">=": lambda: value >= lower,
                  "<": lambda: value < lower, "<=": lambda: value <= lower, "=": lambda: abs(value - lower) < 1e-9}
    if operator in operations: return operations[operator]()
    if operator == "BETWEEN" and upper is not None: return lower <= value <= upper
    if operator == "OUTSIDE" and upper is not None: return value < lower or value > upper
    return None


def same_unit(left, right):
    normalize = lambda value: str(value or "").strip().replace("μ", "u").replace("µ", "u").replace(" ", "").lower()
    return normalize(left) == normalize(right)


def interpret_exam(patient, standard, parameter):
    parameter = parameter or {}
    exam = parameter.get("exam_code")
    finding_code = parameter.get("finding_code")
    concept = parameter.get("concept_code") or "PRESENT"
    finding = (patient.get_latest_finding(exam, finding_code) if parameter.get("selection_policy") == "latest"
               else patient.get_finding(exam, finding_code))
    if not finding: return unknown("缺少检查结果，无法判断")
    inline = parameter.get("interpretation")
    if isinstance(inline, dict):
        # Explicit guideline conditions take precedence over hospital reference values.
        try:
            standard = LabInterpretationStandard(**{**inline, "hospital_id": patient.hospital_id,
                "item_type": "exam", "exam_code": exam, "finding_code": finding_code, "concept_code": concept})
        except Exception as error: return unknown("指南判定参数无效：" + str(error))
    if standard is None:
        if parameter.get("standard_required") or finding.value is not None or finding.category is not None or (finding.report_text and "present" not in finding.model_fields_set):
            return unknown("检查标准缺失、不适用或未启用，不能解释原始结果")
        if finding.present is None: return unknown("检查发现未明确存在或不存在")
        if concept not in {"PRESENT", "ABSENT"}: return unknown("该概念需要配置检查判定标准")
        matched = finding.present if concept == "PRESENT" else not finding.present
        return EvaluationResult(matched, evidence_description="使用已结构化的存在/不存在结果", patient_value_display="存在" if finding.present else "不存在",
            standard_used="上游结构化结果 · " + concept, detail={"evaluation_status": "matched" if matched else "not_matched"})
    if standard.context_age_min is not None or standard.context_age_max is not None:
        if patient.age is None: return unknown("缺少年龄，无法核对检查标准适用人群")
        if ((standard.context_age_min is not None and patient.age < standard.context_age_min)
                or (standard.context_age_max is not None and patient.age > standard.context_age_max)):
            return unknown("检查标准年龄范围不适用")
    if standard.method and standard.method != finding.method:
        return unknown("检查方法与标准不一致或未记录")
    max_age = parameter.get("max_age_hours")
    if max_age is not None:
        from datetime import datetime, timezone
        from src.cardiomyopathy.service import utc
        import math
        if isinstance(max_age, bool) or not isinstance(max_age, (int, float)) or not math.isfinite(max_age) or max_age < 0:
            return unknown("检查有效时间窗无效")
        if finding.performed_at is None: return unknown("缺少检查时间，无法核对有效期")
        elapsed = (utc(patient.encounter_time or datetime.now(timezone.utc)) - utc(finding.performed_at)).total_seconds() / 3600
        if elapsed < 0 or elapsed > max_age: return unknown("检查结果不在有效时间窗内")
    issues = standard_issues(standard)
    if issues: return unknown("检查标准参数不完整：" + "；".join(issues))
    mode = standard.interpretation_type
    if mode == "numeric":
        if finding.value is None: return unknown("检查结果缺少数值")
        if not same_unit(finding.unit, standard.threshold_unit): return unknown("检查结果与标准单位不一致，尚未完成换算")
        matched = compare_numeric(finding.value, standard.operator, standard.threshold_value, standard.threshold_upper)
        display = f"{finding.value:g} {finding.unit}"
    elif mode == "categorical":
        if finding.category is None: return unknown("缺少结构化分级/定性结果；不能仅凭报告文本判断")
        matched = finding.category.strip().casefold() in {value.strip().casefold() for value in standard.accepted_values}
        display = finding.category
    elif mode == "presence":
        if finding.present is None or ((finding.report_text or finding.value is not None or finding.category is not None) and "present" not in finding.model_fields_set):
            return unknown("报告尚未转换为明确的存在/不存在结果")
        matched = finding.present if concept != "ABSENT" else not finding.present
        display = "存在" if finding.present else "不存在"
    else: return unknown("检查判定类型未配置")
    if matched is None: return unknown("比较参数无效")
    origin = "指南明确条件" if inline else f"{patient.hospital_id} 检查标准 v{standard.version}"
    criterion = (f"{standard.operator} {standard.threshold_value} {standard.threshold_unit}" if mode == "numeric"
                 else "满足值：" + "、".join(standard.accepted_values) if mode == "categorical" else concept)
    return EvaluationResult(bool(matched), evidence_description=f"{exam}/{finding_code} {criterion}",
        patient_value_display=display, standard_used=f"{origin} · {criterion}",
        detail={"evaluation_status": "matched" if matched else "not_matched", "source_note": standard.source_note})
