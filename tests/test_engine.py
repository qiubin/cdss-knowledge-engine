#!/usr/bin/env python3
"""End-to-end tests for the inference engine."""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import json
from datetime import datetime, timedelta, timezone

from src.kg.schema import ConceptVocabulary
from src.kg.client import JsonGraphClient
from src.standards.repository import JsonStandardRepository
from src.rules.repository import JsonRuleRepository
from src.engine import InferenceEngine
from src.engine.evaluators import EvaluatorFactory
from src.models.patient import (
    PatientSnapshot, LabResult, ExamFinding, SymptomObservation, VitalSign, Gender
)

DATA = project_root / "data"
FIXTURES = project_root / "tests" / "fixtures"


def build_engine() -> InferenceEngine:
    vocab = ConceptVocabulary()
    vocab.load(str(DATA / "vocab" / "concepts.json"))
    # Inference fixtures are isolated from live, guideline-generated assets so
    # clearing data/kg or data/rules does not break the test suite.
    kg = JsonGraphClient(str(FIXTURES / "kg" / "stemi_graph.json"))
    standards = JsonStandardRepository(str(DATA / "standards"))
    rules = JsonRuleRepository(str(FIXTURES / "rules"))
    return InferenceEngine(rules, kg, standards, vocab)


def make_patient(hospital_id="XM_FIRST", **kwargs) -> PatientSnapshot:
    base = dict(
        patient_id="TEST-001",
        hospital_id=hospital_id,
        age=55,
        gender=Gender.MALE,
        labs=[],
        exam_findings=[],
        symptoms=[],
        vitals=[],
    )
    base.update(kwargs)
    return PatientSnapshot(**base)


class TestStaticThreshold:
    def test_myo_elevated_xm_first(self):
        engine = build_engine()
        patient = make_patient(
            labs=[LabResult(lab_code="MYO", value=120.0, unit="ng/mL")]
        )
        std = engine.standard_repo.get_active("XM_FIRST", "MYO", "ELEVATED")
        assert std is not None
        assert std.threshold_value == 77.0
        ev = EvaluatorFactory.create("static_threshold")
        result = ev.evaluate(patient, std)
        assert result.matched is True

    def test_myo_elevated_fz_hospital(self):
        engine = build_engine()
        ev = EvaluatorFactory.create("static_threshold")
        patient_xm = make_patient(hospital_id="XM_FIRST",
                                  labs=[LabResult(lab_code="MYO", value=80.0)])
        std_xm = engine.standard_repo.get_active("XM_FIRST", "MYO", "ELEVATED")
        assert ev.evaluate(patient_xm, std_xm).matched is True
        patient_fz = make_patient(hospital_id="FZ_HOSPITAL",
                                  labs=[LabResult(lab_code="MYO", value=80.0)])
        std_fz = engine.standard_repo.get_active("FZ_HOSPITAL", "MYO", "ELEVATED")
        assert std_fz is not None
        assert std_fz.threshold_value == 85.0
        assert ev.evaluate(patient_fz, std_fz).matched is False


class TestUlnMultiplier:
    def test_ctn_above_uln(self):
        engine = build_engine()
        patient = make_patient(
            labs=[LabResult(lab_code="CTN", value=0.15, reference_high=0.04)]
        )
        std = engine.standard_repo.get_active("XM_FIRST", "CTN", "ABOVE_ULN_1")
        assert std is not None
        ev = EvaluatorFactory.create("uln_multiplier")
        result = ev.evaluate(patient, std)
        assert result.matched is True

    def test_ctn_below_uln(self):
        engine = build_engine()
        patient = make_patient(
            labs=[LabResult(lab_code="CTN", value=0.03, reference_high=0.04)]
        )
        std = engine.standard_repo.get_active("XM_FIRST", "CTN", "ABOVE_ULN_1")
        ev = EvaluatorFactory.create("uln_multiplier")
        assert ev.evaluate(patient, std).matched is False


class TestDynamicTrend:
    def test_ctn_dynamic_rise(self):
        engine = build_engine()
        now = datetime.now(timezone.utc)
        patient = make_patient(
            labs=[
                LabResult(lab_code="CTN", value=0.10,
                          reported_at=now - timedelta(hours=2)),
                LabResult(lab_code="CTN", value=0.25,
                          reported_at=now - timedelta(hours=1)),
                LabResult(lab_code="CTN", value=0.50,
                          reported_at=now),
            ]
        )
        std = engine.standard_repo.get_active("XM_FIRST", "CTN", "DYNAMIC_RISE")
        assert std is not None
        ev = EvaluatorFactory.create("dynamic_trend")
        result = ev.evaluate(patient, std)
        assert result.matched is True

    def test_ctn_not_rising(self):
        engine = build_engine()
        now = datetime.now(timezone.utc)
        patient = make_patient(
            labs=[
                LabResult(lab_code="CTN", value=0.50,
                          reported_at=now - timedelta(hours=2)),
                LabResult(lab_code="CTN", value=0.25,
                          reported_at=now),
            ]
        )
        std = engine.standard_repo.get_active("XM_FIRST", "CTN", "DYNAMIC_RISE")
        ev = EvaluatorFactory.create("dynamic_trend")
        assert ev.evaluate(patient, std).matched is False


class TestFullInference:
    def _make_full_stemi_patient(self, hospital_id="XM_FIRST") -> PatientSnapshot:
        return make_patient(
            hospital_id=hospital_id,
            labs=[
                LabResult(lab_code="MYO", value=120.0),
                LabResult(lab_code="CTN", value=0.15, reference_high=0.04),
                LabResult(lab_code="CK_MB", value=12.5, reference_high=5.0),
            ],
            exam_findings=[
                ExamFinding(exam_code="ECG_12LEAD",
                            finding_code="ST_CONVEX_ELEVATION", present=True),
            ],
            symptoms=[
                SymptomObservation(symptom_code="CHEST_PAIN_CRUSHING",
                                   present=True, duration_minutes=40),
                SymptomObservation(symptom_code="DIAPHORESIS", present=True),
            ],
        )

    def test_stemi_diagnosis_full(self):
        engine = build_engine()
        patient = self._make_full_stemi_patient()
        conclusions = engine.run(patient)
        assert len(conclusions) >= 2
        codes = {c.disease_code for c in conclusions}
        assert "I21.0" in codes

    def test_no_match_without_findings(self):
        engine = build_engine()
        patient = make_patient()
        conclusions = engine.run(patient)
        assert len(conclusions) == 0

    def test_hospital_standard_affects_conclusion(self):
        engine = build_engine()
        patient_xm = self._make_full_stemi_patient("XM_FIRST")
        patient_xm.labs[0].value = 80.0
        conclusions_xm = engine.run(patient_xm)
        rule_ids_xm = {c.rule_id for c in conclusions_xm}
        patient_fz = self._make_full_stemi_patient("FZ_HOSPITAL")
        patient_fz.labs[0].value = 80.0
        conclusions_fz = engine.run(patient_fz)
        rule_ids_fz = {c.rule_id for c in conclusions_fz}
        assert "STEMI-DX-001" in rule_ids_xm
        assert "STEMI-DX-001" in rule_ids_fz
        assert "STEMI-DX-003" in rule_ids_xm
        assert "STEMI-DX-003" not in rule_ids_fz

