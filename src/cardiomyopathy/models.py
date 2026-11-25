"""Versioned contracts for longitudinal specialty decision support."""
from datetime import datetime
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field, model_validator


class ScenarioContract(BaseModel):
    version: int = Field(default=1, ge=1)
    trigger_events: List[str] = Field(min_length=1)
    required_attributes: List[str] = Field(default_factory=list)
    age_min: Optional[int] = Field(default=None, ge=0)
    age_max: Optional[int] = Field(default=None, ge=0)
    excluded_attributes: Dict[str, str] = Field(default_factory=dict)
    actions: List[str] = Field(min_length=1)
    reassessment_events: List[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def valid_age_range(self):
        if self.age_min is not None and self.age_max is not None and self.age_min > self.age_max:
            raise ValueError('年龄范围倒置')
        return self


class RiskTerm(BaseModel):
    attribute: str
    coefficient: float = Field(allow_inf_nan=False)


class RiskBand(BaseModel):
    minimum: float = Field(allow_inf_nan=False)
    label: str


class RiskModel(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_id: str
    version: int = Field(ge=1)
    name: str
    status: Literal['draft', 'active', 'retired'] = 'draft'
    source: str = Field(min_length=1)
    reviewer: str = ''
    reviewed_at: Optional[datetime] = None
    age_min: int = Field(default=18, ge=0)
    age_max: Optional[int] = Field(default=None, ge=0)
    required_population: Dict[str, str] = Field(default_factory=dict)
    # Deliberately limited to explicit point scores; no inferred clinical formula.
    calculation: Literal['linear_points'] = 'linear_points'
    intercept: float = Field(default=0, allow_inf_nan=False)
    terms: List[RiskTerm] = Field(min_length=1)
    bands: List[RiskBand] = Field(min_length=1)

    @model_validator(mode='after')
    def reviewed(self):
        if self.status == 'active' and (not self.reviewer.strip() or self.reviewed_at is None):
            raise ValueError('启用模型必须有审核人和审核时间')
        if len({t.attribute for t in self.terms}) != len(self.terms):
            raise ValueError('模型变量重复')
        if self.age_max is not None and self.age_max < self.age_min:
            raise ValueError('年龄范围倒置')
        return self


class FollowUpTask(BaseModel):
    task_id: str
    patient_id: str
    hospital_id: str
    disease_code: str
    title: str
    due_at: datetime
    source: str = Field(min_length=1)
    status: Literal['pending', 'completed', 'cancelled'] = 'pending'
    completed_at: Optional[datetime] = None
    reassessment_events: List[str] = Field(default_factory=list)
    required_attributes: List[str] = Field(default_factory=list)


class FamilyMember(BaseModel):
    family_id: str
    member_id: str
    hospital_id: str
    relationship: str
    index_patient_id: str
    patient_id: Optional[str] = None
    genetic_status: Literal['unknown', 'not_tested', 'negative', 'positive', 'uncertain'] = 'unknown'
    variant: str = ''
    classification: Literal['unknown', 'benign', 'likely_benign', 'VUS', 'likely_pathogenic', 'pathogenic'] = 'unknown'
    interpretation_version: str = ''
    source: str = Field(min_length=1)
    consent_recorded: bool = False


class OrderMapping(BaseModel):
    hospital_id: str
    code: str
    local_code: str
    name: str
    status: Literal['draft', 'active', 'retired'] = 'draft'
    reviewer: str = ''
    source: str = Field(min_length=1)

    @model_validator(mode='after')
    def reviewed(self):
        if self.status == 'active' and not self.reviewer.strip():
            raise ValueError('启用院内映射必须有审核人')
        return self
