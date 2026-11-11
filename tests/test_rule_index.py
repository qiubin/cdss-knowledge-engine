# tests/test_rule_index.py
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.engine.rule_index import RuleIndex, patient_keys, satisfiable
from src.models.patient import (
    ExamFinding, Gender, LabResult, Observation, PatientSnapshot,
    SymptomObservation, VitalSign,
)
from src.models.rule import Rule, RuleCondition


def _patient():
    return PatientSnapshot(
        patient_id="P-IDX", hospital_id="H1", age=60, gender=Gender.MALE,
        labs=[LabResult(lab_code="CTN", value=5.0)],
        symptoms=[SymptomObservation(symptom_code="SYM_PAIN", present=True)],
        exam_findings=[ExamFinding(exam_code="ECG", finding_code="STE", present=True)],
        observations=[Observation(observation_type="tongue", code="T_RED", present=False)],
        vitals=[VitalSign(vital_code="sbp", value=90)],
        past_history=["HTN"],
        current_medications=["ASP"],
        clinical_attributes={"stage": "II"},
    )


def _rule(conditions) -> Rule:
    return Rule(
        rule_id="IDX-R", disease_code="X", disease_name="X",
        conditions=RuleCondition(**conditions),
        conclusion={"diagnosis": "X"},
    )


def test_patient_keys_shape():
    keys = patient_keys(_patient())
    assert "lab:CTN" in keys
    assert "symptom:SYM_PAIN" in keys
    assert "finding:ECG:STE" in keys
    assert "vital:sbp" in keys
    assert "past_history:HTN" in keys
    assert "medication:ASP" in keys
    assert "observation:tongue:T_RED" not in keys  # present=False 不算
    assert "attribute:stage" in keys


def test_and_group_pruned_when_lab_absent():
    keys = patient_keys(_patient())
    rule = _rule({
        "type": "group", "operator": "AND", "children": [
            {"type": "lab_concept", "lab_code": "BNP", "concept_code": "ELEVATED"},
            {"type": "symptom", "symptom_code": "SYM_PAIN"},
        ],
    })
    assert not satisfiable(rule.conditions, keys)


def test_or_group_kept_when_one_branch_present():
    keys = patient_keys(_patient())
    rule = _rule({
        "type": "group", "operator": "OR", "children": [
            {"type": "lab_concept", "lab_code": "BNP", "concept_code": "ELEVATED"},
            {"type": "lab_concept", "lab_code": "CTN", "concept_code": "ELEVATED"},
        ],
    })
    assert satisfiable(rule.conditions, keys)


def test_not_group_never_pruned():
    keys = patient_keys(_patient())
    rule = _rule({
        "type": "group", "operator": "NOT", "children": [
            {"type": "symptom", "symptom_code": "ABSENT_CODE"},
        ],
    })
    assert satisfiable(rule.conditions, keys)


def test_weighted_group_required_child_controls():
    keys = patient_keys(_patient())
    missing_required = _rule({
        "type": "weighted_group", "operator": "AND", "threshold": 0.4,
        "children": [{"type": "observation", "observation_type": "tongue", "code": "T_RED"}],
        "required_children": [
            {"type": "observation", "observation_type": "pulse", "code": "P_SINKING"},
        ],
    })
    assert not satisfiable(missing_required.conditions, keys)
    # 只靠必需项也可能过阈值，因此必需项满足时恒不可剪
    present_required = _rule({
        "type": "weighted_group", "operator": "AND", "threshold": 0.4,
        "children": [{"type": "observation", "observation_type": "tongue", "code": "T_WHITE"}],
        "required_children": [
            {"type": "observation", "observation_type": "pulse", "code": "P_PRESENT_CODE"},
        ],
    })
    # P_PRESENT_CODE 不在 keys 中 → 必需项不可满足 → 可剪
    assert not satisfiable(present_required.conditions, keys)


def test_evidence_leaf_never_prunes():
    keys = set()
    rule = _rule({"type": "evidence", "evidence_id": "EV-1"})
    assert satisfiable(rule.conditions, keys)


def test_index_filter():
    keys = patient_keys(_patient())
    hit = _rule({
        "type": "group", "operator": "AND", "children": [
            {"type": "lab_concept", "lab_code": "CTN", "concept_code": "ELEVATED"},
        ],
    })
    miss = _rule({
        "type": "group", "operator": "AND", "children": [
            {"type": "lab_concept", "lab_code": "BNP", "concept_code": "ELEVATED"},
        ],
    })
    index = RuleIndex([hit, miss])
    kept = index.satisfiable_rule(hit, keys)
    assert kept is True
    assert index.satisfiable_rule(miss, keys) is False


def test_symptom_channel_satisfies_symptom_typed_observation_leaf():
    # 症状仅经 patient.symptoms 送达：observation 叶子（type=symptom）也须可满足，
    # 因为评估器会回退到 patient.get_symptom 查找该编码。
    patient = PatientSnapshot(
        patient_id="P-IDX-SYM", hospital_id="H1",
        symptoms=[SymptomObservation(symptom_code="SYM_CHEST_TIGHTNESS", present=True)],
    )
    keys = patient_keys(patient)
    assert "symptom:SYM_CHEST_TIGHTNESS" in keys
    assert "observation:symptom:SYM_CHEST_TIGHTNESS" in keys
    rule = _rule({
        "type": "weighted_group", "operator": "AND", "threshold": 0.6,
        "children": [
            {"type": "observation", "observation_type": "tongue", "code": "T_PALE"},
        ],
        "required_children": [
            {"type": "observation", "observation_type": "symptom", "code": "SYM_CHEST_TIGHTNESS"},
        ],
    })
    assert satisfiable(rule.conditions, keys)
    # present=False 的症状不算任何键：必需项不可满足 → 可剪（保持保守方向的另一半）
    resolved_patient = PatientSnapshot(
        patient_id="P-IDX-SYM2", hospital_id="H1",
        symptoms=[SymptomObservation(symptom_code="SYM_CHEST_TIGHTNESS", present=False)],
    )
    resolved_keys = patient_keys(resolved_patient)
    assert "observation:symptom:SYM_CHEST_TIGHTNESS" not in resolved_keys
    assert not satisfiable(rule.conditions, resolved_keys)


def test_weighted_group_required_flag_child_is_not_hard():
    # children 中 required=True 但不在 required_children 的子项：评估器根本不
    # 评估它（既非 required_specs 也非 main_children），因此不是硬条件，
    # 不可据此剪枝。
    keys = patient_keys(_patient())
    rule = _rule({
        "type": "weighted_group", "operator": "AND", "threshold": 0.4,
        "children": [
            {
                "type": "lab_concept", "lab_code": "BNP",
                "concept_code": "ELEVATED", "required": True,
            },
        ],
        "required_children": [
            {"type": "symptom", "symptom_code": "SYM_PAIN"},
        ],
    })
    assert satisfiable(rule.conditions, keys)


def test_observation_absent_expectation_never_pruned():
    # concept_code="ABSENT" 的观察叶子在数据缺失时反而成立
    # （ObservationPresentEvaluator: matched = not observed），不可剪。
    keys = patient_keys(_patient())
    rule = _rule({
        "type": "group", "operator": "AND", "children": [
            {
                "type": "observation", "observation_type": "tongue",
                "code": "T_NEVER_SEEN", "concept_code": "ABSENT",
            },
        ],
    })
    assert satisfiable(rule.conditions, keys)
