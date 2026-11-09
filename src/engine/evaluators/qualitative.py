"""Qualitative evaluator - for positive/negative lab results."""
from __future__ import annotations

from typing import Any, Dict, Optional

from ...models.patient import PatientSnapshot
from ...models.standard import LabInterpretationStandard
from .base import BaseEvaluator, EvaluationResult


class QualitativeEvaluator(BaseEvaluator):
    """Evaluates POSITIVE / NEGATIVE for qualitative lab tests."""

    def evaluate(
        self,
        patient: PatientSnapshot,
        standard: Optional[LabInterpretationStandard],
        parameter: Optional[Dict[str, Any]] = None,
    ) -> EvaluationResult:
        if standard is None:
            return EvaluationResult(matched=False, evidence_description="标准未配置")

        lab = patient.get_lab(standard.lab_code)
        if lab is None or lab.qualitative is None:
            return EvaluationResult(matched=False, evidence_description=f"{standard.lab_code} 无定性结果")

        expected = standard.concept_code  # POSITIVE or NEGATIVE
        actual = lab.qualitative.upper()   # "POSITIVE" or "NEGATIVE"
        matched = actual == expected

        return EvaluationResult(
            matched=matched,
            evidence_description=f"{standard.lab_name or standard.lab_code} {actual}",
            patient_value_display=f"{standard.lab_code}={actual}",
            standard_used=expected,
        )

