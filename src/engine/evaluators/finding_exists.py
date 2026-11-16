"""Finding evaluator - checks if an exam finding is present/absent."""
from __future__ import annotations

from typing import Any, Dict, Optional

from ...models.patient import PatientSnapshot
from ...models.standard import LabInterpretationStandard
from .base import BaseEvaluator, EvaluationResult


class FindingExistsEvaluator(BaseEvaluator):
    """Evaluates PRESENT / ABSENT for exam findings."""

    def evaluate(
        self,
        patient: PatientSnapshot,
        standard: Optional[LabInterpretationStandard],
        parameter: Optional[Dict[str, Any]] = None,
    ) -> EvaluationResult:
        from .exam_interpretation import interpret_exam
        return interpret_exam(patient, standard, parameter)
