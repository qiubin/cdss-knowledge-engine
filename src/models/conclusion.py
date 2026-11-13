"""Inference output models."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EvidenceTrace(BaseModel):
    """A single piece of evidence that contributed to a conclusion."""
    evidence_id: str
    description: str
    lab_code: Optional[str] = None
    concept_code: Optional[str] = None
    patient_value_display: Optional[str] = None  # human-readable, e.g. "MYO=120 ng/mL (>77)"
    standard_used: Optional[str] = None  # e.g. ">77 ng/mL (XM_FIRST, v1)"
    source_ref: Optional[str] = None    # guideline quote reference


class Conclusion(BaseModel):
    """Output of the inference engine for one matched rule."""
    rule_id: str
    rule_version: int = 1
    rule_type: str = "diagnosis"  # diagnosis / treatment / contraindication / monitoring / tcm_*
    clinical_intent: str = ""
    # Anchor: disease for western medicine, syndrome for TCM.  disease_code /
    # disease_name remain populated as the legacy mirror of anchor_code /
    # anchor_name so existing consumers keep working.
    anchor_type: str = "disease"
    anchor_code: Optional[str] = None
    anchor_name: Optional[str] = None
    disease_code: str
    disease_name: str = ""
    # Treatment extension
    conclusion_type: Optional[str] = None      # treatment / monitoring / prescription / contraindication
    treatment_plan_id: Optional[str] = None    # e.g. TP-STEMI-PRIMARY-PCI
    treatment_name: Optional[str] = None       # display name
    priority_label: Optional[str] = None       # preferred / alternative / rescue
    treatment_items: List[Dict[str, Any]] = Field(default_factory=list)
    # Order-set materialization of the treatment plan (order_set node codes).
    order_set_ids: List[str] = Field(default_factory=list)
    triggered_by: List[str] = Field(default_factory=list)  # diagnosis rule_ids that triggered this treatment
    # TCM extension: structured treatment/prescription output.
    treatment_method_code: Optional[str] = None
    formula_code: Optional[str] = None
    formula_name: Optional[str] = None
    herbs: List[Dict[str, Any]] = Field(default_factory=list)
    modifications: List[str] = Field(default_factory=list)
    course: Optional[str] = None
    # Pattern scoring (TCM) and condition counts.
    score: Optional[float] = None
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)
    matched_items: List[Dict[str, Any]] = Field(default_factory=list)
    unmatched_items: List[Dict[str, Any]] = Field(default_factory=list)
    missing_items: List[Dict[str, Any]] = Field(default_factory=list)
    matched_conditions: int = 0
    total_conditions: int = 0
    confidence: str = "medium"  # high / medium / low
    message: str = ""
    suggested_action: Optional[str] = None
    priority: int = 0
    evidence: List[EvidenceTrace] = Field(default_factory=list)
    guideline_source: Optional[str] = None
    exclusions_checked: List[str] = Field(default_factory=list)
    matched_at: str = ""

