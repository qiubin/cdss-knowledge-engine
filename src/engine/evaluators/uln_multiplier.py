"""ULN multiplier evaluator - value exceeds reference upper limit by N times."""
from __future__ import annotations

from typing import Any, Dict, Optional

from ...models.patient import PatientSnapshot
from ...models.standard import LabInterpretationStandard
from .base import BaseEvaluator, EvaluationResult


class UlnMultiplierEvaluator(BaseEvaluator):
    """Evaluates ABOVE_ULN_1 / ABOVE_ULN_5 etc.

    Uses the patient lab's own reference_high (from LIS) multiplied by
    the standard's uln_multiplier. If the standard has a static threshold,
    it uses that instead.
    """

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

        multiplier = standard.uln_multiplier or 1.0

        # Determine ULN: either from standard's static threshold or from lab reference
        if standard.threshold_value is not None:
            uln = standard.threshold_value
            uln_source = "医院标准"
        elif lab.reference_high is not None:
            uln = lab.reference_high
            uln_source = "LIS 参考范围"
        else:
            return EvaluationResult(matched=False, evidence_description=f"{standard.lab_code} 无参考上限，无法判断超ULN")

        threshold = uln * multiplier
        matched = lab.value > threshold
        unit = standard.threshold_unit or lab.unit or ""
        unit_text = f" {unit}" if unit else ""

        return EvaluationResult(
            matched=matched,
            evidence_description=(
                f"{standard.lab_name or standard.lab_code} "
                f"超过正常上限（ULN）×{multiplier:g}"
            ),
            patient_value_display=(
                f"{standard.lab_code}={lab.value}{unit_text}"
                f"（正常上限={uln}{unit_text}，来源：{uln_source}）"
            ),
            standard_used=f"正常上限（ULN）×{multiplier:g} = {threshold:.2f}{unit_text}",
            detail={"actual_value": lab.value, "uln": uln, "multiplier": multiplier, "threshold": threshold},
        )

