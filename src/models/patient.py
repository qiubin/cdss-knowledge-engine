"""Patient data models - normalized clinical observations."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Gender(str, Enum):
    MALE = "M"
    FEMALE = "F"
    UNKNOWN = "U"


class LabResult(BaseModel):
    """A single lab test result from LIS."""
    lab_code: str
    lab_name: str = ""
    value: Optional[float] = None
    qualitative: Optional[str] = None  # "positive" / "negative" for qualitative tests
    unit: str = ""
    reference_low: Optional[float] = None
    reference_high: Optional[float] = None
    collected_at: Optional[datetime] = None
    reported_at: Optional[datetime] = None
    is_critical: bool = False


class ExamFinding(BaseModel):
    """A finding from an exam/procedure (ECG, imaging, etc.)."""
    exam_code: str
    exam_name: str = ""
    finding_code: str
    finding_name: str = ""
    present: bool = True
    detail: Optional[Dict[str, Any]] = None  # e.g. {"leads": ["II","III","aVF"], "magnitude_mv": 0.3}
    performed_at: Optional[datetime] = None


class SymptomObservation(BaseModel):
    """A clinical symptom documented in EMR."""
    symptom_code: str
    symptom_name: str = ""
    present: bool = True
    onset_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    duration_minutes: Optional[float] = None
    severity: Optional[str] = None  # mild / moderate / severe
    detail: Optional[Dict[str, Any]] = None


class VitalSign(BaseModel):
    """A vital sign measurement."""
    vital_code: str  # sbp, dbp, hr, rr, spo2, temp
    value: float
    unit: str = ""
    measured_at: Optional[datetime] = None


class Observation(BaseModel):
    """A generic structured observation.

    Keeps TCM tongue/pulse/sign/constitution data out of the western lab and
    exam models while still flowing through the same inference engine.
    """
    observation_type: str  # symptom / tongue / pulse / sign / constitution
    code: str
    name: str = ""
    present: bool = True
    severity: Optional[str] = None  # mild / moderate / severe
    detail: Optional[Dict[str, Any]] = None
    observed_at: Optional[datetime] = None


class PatientSnapshot(BaseModel):
    """Normalized clinical data for a patient at a point in time.

    This is the unified input to the inference engine. Adapters for
    EMR/HIS/LIS/monitoring systems produce this model.
    """
    patient_id: str
    hospital_id: str
    age: Optional[int] = None
    gender: Gender = Gender.UNKNOWN
    labs: List[LabResult] = Field(default_factory=list)
    exam_findings: List[ExamFinding] = Field(default_factory=list)
    symptoms: List[SymptomObservation] = Field(default_factory=list)
    observations: List[Observation] = Field(default_factory=list)
    # Generic clinical attributes for oncology and other specialties:
    # histology, stage, grade, molecular markers, etc.
    clinical_attributes: Dict[str, Any] = Field(default_factory=dict)
    vitals: List[VitalSign] = Field(default_factory=list)
    past_history: List[str] = Field(default_factory=list)  # standard disease codes
    current_medications: List[str] = Field(default_factory=list)  # standard drug codes
    allergies: List[str] = Field(default_factory=list)
    encounter_time: Optional[datetime] = None

    def get_lab(self, lab_code: str) -> Optional[LabResult]:
        for lab in self.labs:
            if lab.lab_code == lab_code:
                return lab
        return None

    def get_lab_series(self, lab_code: str) -> List[LabResult]:
        """All results for a lab code, sorted by time (for dynamic concepts)."""
        results = [l for l in self.labs if l.lab_code == lab_code and l.value is not None]
        results.sort(key=lambda x: x.reported_at or x.collected_at or datetime.min)
        return results

    def get_finding(self, exam_code: str, finding_code: str) -> Optional[ExamFinding]:
        for f in self.exam_findings:
            if f.exam_code == exam_code and f.finding_code == finding_code:
                return f
        return None

    def get_symptom(self, symptom_code: str) -> Optional[SymptomObservation]:
        for s in self.symptoms:
            if s.symptom_code == symptom_code:
                return s
        return None

    def get_observation(self, observation_type: str, code: str) -> Optional[Observation]:
        for item in self.observations:
            if item.observation_type == observation_type and item.code == code:
                return item
        return None

    def get_observations(self, observation_type: str) -> List[Observation]:
        return [
            item for item in self.observations
            if item.observation_type == observation_type
        ]

