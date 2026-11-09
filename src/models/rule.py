"""Clinical rule models - recursive boolean condition tree."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class ConditionOperator(str, Enum):
    AND = "AND"
    OR = "OR"
    NOT = "NOT"


class RuleSource(BaseModel):
    """Provenance of a rule - which guideline section it came from."""
    guideline_id: str = ""
    guideline_title: str = ""
    chunk_id: str = ""
    section: str = ""
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    quote: str = ""


class RuleConclusion(BaseModel):
    """What happens when a rule matches."""
    diagnosis: str
    confidence: str = "medium"
    message: str = ""
    action: Optional[str] = None
    # Treatment extension (rule_type="treatment" only)
    conclusion_type: Optional[str] = None      # treatment / monitoring / prescription / contraindication
    treatment_plan_id: Optional[str] = None    # e.g. TP-STEMI-PRIMARY-PCI
    treatment_name: Optional[str] = None       # display name
    priority: Optional[str] = None             # preferred / alternative / rescue
    # Structured local treatment plan: medications/interventions from the
    # same guideline section.  Each item is {code, name, entity_type}.
    treatment_items: List[Dict[str, Any]] = Field(default_factory=list)
    # Guideline-level order sets that materialize this treatment plan
    # (order_set entity codes in the bound graph).  The recommendation layer
    # expands them into orderable items; guideline quotes stay on the nodes.
    order_set_ids: List[str] = Field(default_factory=list)
    # TCM extension: structured treatment/prescription output.
    treatment_method_code: Optional[str] = None
    formula_code: Optional[str] = None
    formula_name: Optional[str] = None
    herbs: List[Dict[str, Any]] = Field(default_factory=list)  # [{code,name,dose,role}]
    modifications: List[str] = Field(default_factory=list)
    course: Optional[str] = None
    score_threshold: Optional[float] = None


class RuleCondition(BaseModel):
    """A node in the recursive condition tree.

    Two types:
    - "group": has operator (AND/OR/NOT) and children
    - "evidence": references a DiagnosticEvidence in the KG by evidence_id
    - "lab_concept": references a lab_code + concept_code combination directly
    - "exam_finding": references exam_code + finding_code
    - "symptom": references symptom_code with optional duration
    - "vital": references vital_code with numeric operator
    - "demographic": age/gender conditions
    - "past_history": patient history
    - "medication": current medications
    - "observation": generic observation (TCM tongue/pulse/sign/constitution)
    - "fact": consume a fact produced by an earlier inference stage
    """
    type: str  # group / evidence / lab_concept / exam_finding / symptom / vital / demographic / past_history / medication / observation / fact
    operator: Optional[str] = None  # AND / OR / NOT for groups; > < >= <= = for numeric

    # For group type
    children: List["RuleCondition"] = Field(default_factory=list)

    # For evidence type
    evidence_id: Optional[str] = None

    # For lab_concept type
    lab_code: Optional[str] = None
    concept_code: Optional[str] = None
    parameter: Optional[Dict[str, Any]] = None  # extra params e.g. {"threshold_hours": 14}

    # For exam_finding type
    exam_code: Optional[str] = None
    finding_code: Optional[str] = None

    # For symptom type
    symptom_code: Optional[str] = None
    duration_minutes: Optional[float] = None

    # For vital type
    vital_code: Optional[str] = None
    threshold_value: Optional[float] = None

    # For demographic type
    age_min: Optional[int] = None
    age_max: Optional[int] = None
    gender: Optional[str] = None

    # For past_history / medication / observation / fact type
    code: Optional[str] = None

    # For observation type: symptom / tongue / pulse / sign / constitution
    observation_type: Optional[str] = None
    # For fact type: syndrome / treatment / disease
    fact_type: Optional[str] = None
    # For attribute type: histology / stage / biomarker / grade ...
    attribute_code: Optional[str] = None
    attribute_value: Optional[Any] = None
    # Human-readable label pairing full terms with codes, e.g.
    # 肌钙蛋白（CTN） 超正常上限（ABOVE_ULN_1）.  Generated at rule creation.
    label: Optional[str] = None


class Rule(BaseModel):
    """A complete clinical decision rule."""
    rule_id: str
    version: int = 1
    domain: str = "western"
    # Rules reason toward an anchor. Western anchors on disease; TCM anchors
    # on syndrome.  ``disease_code`` remains the storage field for backward
    # compatibility and holds the anchor code.
    anchor_type: str = "disease"
    rule_type: str = "diagnosis"  # diagnosis / treatment / tcm_pattern / tcm_treatment / tcm_prescription / tcm_contraindication
    name: str = ""
    disease_code: str
    disease_name: str = ""
    priority: int = 0  # higher = more important, resolved first on conflict
    status: str = "active"  # draft / testing / active / retired
    department: Optional[str] = None  # filter by department
    graph_filename: Optional[str] = None
    conditions: RuleCondition
    # Readable summary of the condition tree, generated for review.
    condition_text: str = ""
    conclusion: RuleConclusion
    source: Optional[RuleSource] = None
    exclusions: List[str] = Field(default_factory=list)  # disease codes to exclude
    tags: List[str] = Field(default_factory=list)
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""


RuleCondition.model_rebuild()

