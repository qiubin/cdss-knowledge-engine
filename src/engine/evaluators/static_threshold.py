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

        op_fn = self.OPERATORS.get(standard.operator)
        if op_fn is None:
            return EvaluationResult(matched=False, evidence_description=f"不支持的操作符: {standard.operator}")

        matched = op_fn(lab.value, standard.threshold_value)
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

