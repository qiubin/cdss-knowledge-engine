"""Base evaluator interface and result model."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ...models.patient import PatientSnapshot
from ...models.standard import LabInterpretationStandard


@dataclass
class EvaluationResult:
    """The result of evaluating a single concept against patient data."""
    matched: bool
    evidence_description: str = ""
    patient_value_display: str = ""  # e.g. "MYO=120 ng/mL (>77)"
    standard_used: str = ""          # e.g. ">77 ng/mL (XM_FIRST v1)"
    detail: Dict[str, Any] = field(default_factory=dict)


class BaseEvaluator(ABC):
    """Base class for all concept evaluators.

    Each evaluator knows how to determine whether a patient's data
    satisfies a particular concept (e.g. ELEVATED, DYNAMIC_RISE).

    To add a new evaluator:
    1. Subclass BaseEvaluator
    2. Implement evaluate()
    3. Register it with the EvaluatorFactory
    """

    @abstractmethod
    def evaluate(
        self,
        patient: PatientSnapshot,
        standard: Optional[LabInterpretationStandard],
        parameter: Optional[Dict[str, Any]] = None,
    ) -> EvaluationResult:
        """Evaluate whether the patient data matches this concept."""
        ...

