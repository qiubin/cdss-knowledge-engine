"""Missing lab data must remain unknown, including under negation."""
from src.engine.evaluators.static_threshold import StaticThresholdEvaluator
from src.engine.inference_engine import InferenceEngine
from src.models.patient import PatientSnapshot, LabResult
from src.models.standard import LabInterpretationStandard


def test_missing_lab_is_unknown_to_engine():
    standard = LabInterpretationStandard(hospital_id='H', lab_code='X',
        concept_code='ELEVATED', operator='>', threshold_value=10)
    for labs in ([], [LabResult(lab_code='X')]):
        patient = PatientSnapshot(patient_id='T', hospital_id='H', labs=labs)
        result = StaticThresholdEvaluator().evaluate(patient, standard)
        engine = object.__new__(InferenceEngine)
        engine.evaluation_issues = []
        state = engine._interpretation_state(result, item_type='lab', lab_code='X')
        assert state is None
        assert engine.evaluation_issues[0]['status'] == 'unknown'

import pytest
from src.engine.evaluators.qualitative import QualitativeEvaluator
from src.engine.evaluators.uln_multiplier import UlnMultiplierEvaluator
from src.engine.evaluators.dynamic_trend import DynamicTrendEvaluator
from src.models.rule import RuleCondition


@pytest.mark.parametrize('evaluator,concept', [
    (QualitativeEvaluator, 'POSITIVE'),
    (UlnMultiplierEvaluator, 'ABOVE_ULN_1'),
    (DynamicTrendEvaluator, 'DYNAMIC_RISE'),
])
def test_other_lab_evaluators_preserve_missing(evaluator, concept):
    patient = PatientSnapshot(patient_id='T', hospital_id='H')
    standard = LabInterpretationStandard(hospital_id='H', lab_code='X', concept_code=concept)
    result = evaluator().evaluate(patient, standard)
    assert result.detail['evaluation_status'] == 'unknown'


@pytest.mark.parametrize('operator,states,expected', [
    ('AND', [True, None], None), ('AND', [False, None], False),
    ('OR', [False, None], None), ('OR', [True, None], True),
    ('NOT', [None], None), ('NOT', [False], True),
    ('COUNT', [True, None], None), ('COUNT', [False, None], False),
    ('COUNT', [True, True, None], True),
])
def test_missing_propagates_through_condition_tree(operator, states, expected):
    engine = object.__new__(InferenceEngine)
    children = [RuleCondition(type='symptom', symptom_code=str(i)) for i in range(len(states))]
    engine._evaluate_condition = lambda condition, patient: (states[int(condition.symptom_code)], [])
    condition = RuleCondition(type='count_group' if operator == 'COUNT' else 'group',
        operator='AND' if operator == 'COUNT' else operator, children=children,
        min_count=2 if operator == 'COUNT' else None)
    state, _ = engine._evaluate_condition_uncached(condition, PatientSnapshot(patient_id='T', hospital_id='H'))
    assert state is expected
