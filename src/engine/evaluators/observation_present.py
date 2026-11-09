"""Observation evaluator - presence checks for generic clinical observations.

TCM tongue/pulse/sign/constitution data and plain symptom observations share
this evaluator, so the inference engine does not need a bespoke condition type
per discipline.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ...models.patient import PatientSnapshot
from ...models.standard import LabInterpretationStandard
from .base import BaseEvaluator, EvaluationResult


class ObservationPresentEvaluator(BaseEvaluator):
    """Evaluates PRESENT / ABSENT for a generic observation."""

    def evaluate(
        self,
        patient: PatientSnapshot,
        standard: Optional[LabInterpretationStandard],
        parameter: Optional[Dict[str, Any]] = None,
    ) -> EvaluationResult:
        params = parameter or {}
        observation_type = str(params.get("observation_type") or "symptom")
        code = str(params.get("code") or "")
        expected = str(params.get("concept_code") or "PRESENT").upper()
        if not code:
            return EvaluationResult(matched=False, evidence_description="观察项编码为空")

        observed = False
        display = ""
        if observation_type == "symptom":
            symptom = patient.get_symptom(code)
            if symptom is not None:
                observed = bool(symptom.present)
                display = symptom.name or symptom.symptom_code
                if symptom.severity:
                    display += f"（{symptom.severity}）"

        observation = patient.get_observation(observation_type, code)
        if observation is not None:
            observed = bool(observation.present)
            display = observation.name or observation.code
            if observation.severity:
                display += f"（{observation.severity}）"

        matched = observed if expected != "ABSENT" else not observed
        label = f"{observation_type}:{code}"
        return EvaluationResult(
            matched=matched,
            evidence_description=f"{label} {'存在' if observed else '未见'}",
            patient_value_display=display or f"{label}={'+' if observed else '-'}",
            standard_used=expected,
            detail={"observation_type": observation_type, "code": code, "present": observed},
        )
