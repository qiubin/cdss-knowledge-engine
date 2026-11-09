"""Duration evaluator - checks if a symptom has persisted for a minimum duration."""
from __future__ import annotations

from typing import Any, Dict, Optional

from ...models.patient import PatientSnapshot
from ...models.standard import LabInterpretationStandard
from .base import BaseEvaluator, EvaluationResult


class DurationCheckEvaluator(BaseEvaluator):
    """Evaluates DURATION_GT for symptoms."""

    def evaluate(
        self,
        patient: PatientSnapshot,
        standard: Optional[LabInterpretationStandard],
        parameter: Optional[Dict[str, Any]] = None,
    ) -> EvaluationResult:
        symptom_code = (parameter or {}).get("symptom_code")
        min_minutes = (parameter or {}).get("min_minutes", 0)

        if not symptom_code:
            return EvaluationResult(matched=False, evidence_description="缺少symptom_code参数")

        symptom = patient.get_symptom(symptom_code)
        if symptom is None or not symptom.present:
            return EvaluationResult(matched=False, evidence_description=f"{symptom_code} 症状不存在")

        # Check duration: either explicit duration_minutes or calculated from onset/resolved
        actual_minutes = symptom.duration_minutes
        if actual_minutes is None and symptom.onset_at:
            from datetime import datetime, timezone
            end = symptom.resolved_at or patient.encounter_time or datetime.now(timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            onset = symptom.onset_at
            if onset.tzinfo is None:
                onset = onset.replace(tzinfo=timezone.utc)
            actual_minutes = (end - onset).total_seconds() / 60.0

        if actual_minutes is None:
            return EvaluationResult(matched=False, evidence_description=f"{symptom_code} 无法确定持续时间")

        matched = actual_minutes >= min_minutes
        return EvaluationResult(
            matched=matched,
            evidence_description=f"{symptom_code} 持续{actual_minutes:.0f}分钟（要求≥{min_minutes}分钟）",
            patient_value_display=f"{symptom_code} 持续时间={actual_minutes:.0f}分钟",
            standard_used=f"持续时间≥{min_minutes}分钟",
            detail={"actual_minutes": actual_minutes, "required_minutes": min_minutes},
        )

