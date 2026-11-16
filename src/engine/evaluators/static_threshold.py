"""Static threshold evaluator - compares a value against a configured threshold."""
from __future__ import annotations

from typing import Any, Dict, Optional

from ...models.patient import PatientSnapshot
from ...models.standard import LabInterpretationStandard
from .base import BaseEvaluator, EvaluationResult


class StaticThresholdEvaluator(BaseEvaluator):
    """Evaluates ELEVATED / DECREASED / CRITICAL_HIGH / CRITICAL_LOW etc.

    Uses the standard's operator + threshold_value to compare.
    """

    OPERATORS = {
        ">": lambda v, t: v > t,
        "<": lambda v, t: v < t,
        ">=": lambda v, t: v >= t,
        "<=": lambda v, t: v <= t,
        "=": lambda v, t: abs(v - t) < 1e-9,
    }

    def evaluate(
        self,
        patient: PatientSnapshot,
        standard: Optional[LabInterpretationStandard],
        parameter: Optional[Dict[str, Any]] = None,
    ) -> EvaluationResult:
        if standard is None:
            return EvaluationResult(matched=False, evidence_description="标准未配置")

        lab = patient.get_lab(standard.lab_code)
        if lab is None or lab.value is None:
            return EvaluationResult(matched=False, evidence_description=f"{standard.lab_code} 无检验数据")

        from .exam_interpretation import compare_numeric, same_unit
        if standard.threshold_unit and not same_unit(lab.unit, standard.threshold_unit):
            return EvaluationResult(False, evidence_description="检验结果与标准单位不一致，尚未完成换算", detail={"evaluation_status": "unknown"})
        matched = compare_numeric(lab.value, standard.operator, standard.threshold_value, standard.threshold_upper)
        if matched is None:
            return EvaluationResult(False, evidence_description="阈值或操作符未正确配置", detail={"evaluation_status": "unknown"})
        unit = standard.threshold_unit or lab.unit
        return EvaluationResult(
            matched=matched,
            evidence_description=f"{standard.lab_name or standard.lab_code} {standard.operator} {standard.threshold_value} {unit}",
            patient_value_display=f"{standard.lab_code}={lab.value} {lab.unit or unit}",
            standard_used=(
                f"{standard.operator}{standard.threshold_value} {unit}"
                f"（{standard.hospital_id} 医院标准 v{standard.version}）"
            ),
            detail={"actual_value": lab.value, "threshold": standard.threshold_value, "operator": standard.operator},
        )

