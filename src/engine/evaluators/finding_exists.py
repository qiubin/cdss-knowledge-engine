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
        # For exam findings, standard is not used; we need exam_code + finding_code
        # These come from the DiagnosticEvidence, passed via parameter
        exam_code = (parameter or {}).get("exam_code")
        finding_code = (parameter or {}).get("finding_code")
        concept = (parameter or {}).get("concept_code", "PRESENT")

        if not exam_code or not finding_code:
            return EvaluationResult(matched=False, evidence_description="缺少exam_code/finding_code参数")

        finding = patient.get_finding(exam_code, finding_code)
        if finding is None:
            return EvaluationResult(matched=False, evidence_description=f"{exam_code}/{finding_code} 无检查数据")

        if concept == "PRESENT":
            matched = finding.present
        elif concept == "ABSENT":
            matched = not finding.present
        else:
            matched = False

        return EvaluationResult(
            matched=matched,
            evidence_description=f"{exam_code}/{finding_code} {'存在' if finding.present else '不存在'}",
            patient_value_display=f"{exam_code}.{finding_code}={'+' if finding.present else '-'}",
            standard_used=concept,
            detail=finding.detail or {},
        )

