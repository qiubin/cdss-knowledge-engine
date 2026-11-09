"""Lab interpretation standard - hospital-specific reference thresholds."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class LabInterpretationStandard(BaseModel):
    """Maps an abstract concept to a concrete numeric threshold for one hospital.

    The knowledge graph says "myoglobin ELEVATED indicates STEMI".
    This table says "for hospital XM_FIRST, ELEVATED means MYO > 77 ng/mL".
    """
    id: Optional[int] = None
    hospital_id: str
    lab_code: str
    lab_name: str = ""
    concept_code: str  # ELEVATED / DECREASED / ABOVE_ULN_1 / etc.
    operator: str = ">"  # > < >= <= = BETWEEN IN
    threshold_value: Optional[float] = None
    threshold_unit: str = ""
    uln_multiplier: Optional[float] = None  # for ABOVE_ULN_N concepts
    ref_source: str = "STATIC"  # STATIC / LIS_REALTIME
    context_gender: Optional[str] = None
    context_age_min: Optional[int] = None
    context_age_max: Optional[int] = None
    effective_from: str = ""
    effective_to: Optional[str] = None
    version: int = 1
    status: str = "active"
    source_note: str = ""
    created_by: str = ""

