"""Configuration readiness, distinct from clinical validation."""
from datetime import date
from src.models.standard import LabInterpretationStandard

def standard_issues(row):
    try:
        standard = LabInterpretationStandard(**row) if isinstance(row, dict) else row
    except Exception as error:
        return ["字段结构无效：" + str(error)]
    issues = []
    if not standard.hospital_id or not standard.concept_code:
        issues.append("医院和判定概念必填")
    if standard.item_type == "lab" and not standard.lab_code:
        issues.append("检验项目编码必填")
    if standard.item_type == "exam":
        if not standard.exam_code or not standard.finding_code:
            issues.append("检查项目和检查发现编码必填")
        if standard.interpretation_type == "auto":
            issues.append("检查标准须明确选择数值、分级/定性或存在性判定")
    mode = standard.interpretation_type
    if mode == "numeric" or (mode == "auto" and standard.concept_code in {"ELEVATED", "DECREASED", "ABNORMAL", "CRITICAL_HIGH", "CRITICAL_LOW"}):
        if standard.operator not in {">", ">=", "<", "<=", "=", "BETWEEN", "OUTSIDE"}:
            issues.append("数值比较方式无效")
        if standard.threshold_value is None:
            issues.append("数值判定缺少阈值")
        if not standard.threshold_unit.strip():
            issues.append("数值判定缺少单位")
        if standard.operator in {"BETWEEN", "OUTSIDE"} and (standard.threshold_upper is None or standard.threshold_value is None or standard.threshold_upper < standard.threshold_value):
            issues.append("范围比较须提供有效上下界")
    if mode == "categorical" and not standard.accepted_values:
        issues.append("分级/定性判定缺少满足条件的结果值")
    if standard.uln_multiplier is not None and standard.uln_multiplier <= 0:
        issues.append("正常上限倍数必须大于零")
    if standard.context_age_min is not None and standard.context_age_max is not None and standard.context_age_min > standard.context_age_max:
        issues.append("适用年龄上下限倒置")
    for field in ("effective_from", "effective_to"):
        value = getattr(standard, field)
        if value:
            try: date.fromisoformat(value)
            except ValueError: issues.append("有效期须使用 YYYY-MM-DD")
    if standard.effective_from and standard.effective_to and standard.effective_from > standard.effective_to:
        issues.append("有效期起止日期倒置")
    return issues


def effective(standard, on_date=None):
    current = on_date or date.today().isoformat()
    return (not standard.effective_from or standard.effective_from <= current) and (not standard.effective_to or standard.effective_to >= current)


def standard_identity(row):
    return (row.get("item_type", "lab"), row.get("lab_code", "") if row.get("item_type", "lab") == "lab" else row.get("exam_code", ""),
            row.get("finding_code", "") if row.get("item_type") == "exam" else "", row.get("concept_code"),
            row.get("context_gender"), row.get("context_age_min"), row.get("context_age_max"), row.get("method", ""))
