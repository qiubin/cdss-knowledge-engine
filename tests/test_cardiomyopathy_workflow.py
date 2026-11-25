"""Workflow integration uses temporary storage and synthetic nonclinical scores."""
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from src.api.main import app
from src.api.routes import cardiomyopathy as routes
from src.cardiomyopathy.service import WorkflowStore, assess_contract, assess_risk
from src.cardiomyopathy.models import ScenarioContract, RiskModel
from src.models.patient import PatientSnapshot, ExamFinding, SymptomObservation
from src.models.standard import LabInterpretationStandard
from src.engine.evaluators.duration_check import DurationCheckEvaluator
from src.engine.evaluators.observation_present import ObservationPresentEvaluator
from src.engine.evaluators.exam_interpretation import interpret_exam


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, 'store', WorkflowStore(tmp_path / 'workflow.sqlite3'))
    return TestClient(app)


def patient(**kwargs):
    return PatientSnapshot(patient_id='P', hospital_id='H', age=40, **kwargs)


def model(**kwargs):
    return RiskModel(**{**dict(model_id='SYNTHETIC', version=1, name='测试分值（非临床）',
        source='test fixture', status='active', reviewer='reviewer', reviewed_at=datetime.now(timezone.utc),
        terms=[dict(attribute='x', coefficient=2)], bands=[dict(minimum=0, label='test')]), **kwargs})


def test_contract_missing_exclusion_and_trigger():
    contract = ScenarioContract(trigger_events=['result'], actions=['review'], required_attributes=['x'], age_min=18)
    assert assess_contract(contract, patient(), 'result')['state'] == 'unknown'
    assert assess_contract(contract, patient(clinical_attributes={'x': 0}), 'result')['state'] == 'ready'
    assert assess_contract(contract, patient(), 'other')['state'] == 'not_applicable'
    with pytest.raises(ValueError):
        ScenarioContract(trigger_events=['result'], actions=['review'], age_min=40, age_max=18)


def test_risk_missing_population_and_draft():
    assert assess_risk(model(), patient())['status'] == 'unknown'
    assert assess_risk(model(), patient(clinical_attributes={'x': 3}))['score'] == 6
    assert assess_risk(model(status='draft'), patient())['status'] == 'unavailable'
    assert assess_risk(model(required_population={'phenotype': 'HCM'}), patient(clinical_attributes={'phenotype': 'DCM'}))['status'] == 'not_applicable'
    with pytest.raises(ValueError):
        model(reviewer='')


def test_risk_version_and_audit(client):
    row = model().model_dump(mode='json')
    assert client.put('/api/cardiomyopathy/risk-models?actor=test', json=row).status_code == 200
    row['intercept'] = 4
    assert client.put('/api/cardiomyopathy/risk-models?actor=test', json=row).status_code == 409
    response = client.post('/api/cardiomyopathy/risk-assessments?actor=test', json={
        'model_id': 'SYNTHETIC', 'version': 1, 'patient': patient(clinical_attributes={'x': 4}).model_dump(mode='json')})
    assert response.json()['score'] == 8
    assert len(client.get('/api/cardiomyopathy/audit').json()) == 2


def test_follow_up_and_reassessment(client):
    task = dict(task_id='F', patient_id='P', hospital_id='H', disease_code='HCM', title='测试复查',
        source='test', due_at=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),
        required_attributes=['echo'], reassessment_events=['result'])
    assert client.put('/api/cardiomyopathy/follow-ups?actor=test', json=task).status_code == 200
    assert client.get('/api/cardiomyopathy/follow-ups?hospital_id=H&patient_id=P').json()[0]['overdue']
    result = client.post('/api/cardiomyopathy/follow-ups/reassess', json={'patient': patient().model_dump(mode='json'), 'event': 'result'}).json()
    assert result[0]['missing_items'] == ['echo']
    assert client.get('/api/cardiomyopathy/follow-ups?hospital_id=OTHER&patient_id=P').json() == []
    task['status'] = 'completed'
    assert client.put('/api/cardiomyopathy/follow-ups?actor=test', json=task).status_code == 422


def test_family_and_mapping_review(client):
    member = dict(family_id='F', member_id='M', hospital_id='H', relationship='child', index_patient_id='P', source='test')
    assert client.put('/api/cardiomyopathy/family-members?actor=test', json=member).status_code == 422
    member['consent_recorded'] = True
    assert client.put('/api/cardiomyopathy/family-members?actor=test', json=member).status_code == 200
    assert client.get('/api/cardiomyopathy/family-members?hospital_id=OTHER&family_id=F').json() == []
    mapping = dict(hospital_id='H', code='X', local_code='LOCAL-X', name='测试医嘱', source='test', status='active')
    assert client.put('/api/cardiomyopathy/order-mappings?actor=test', json=mapping).status_code == 422


def test_recommendation_mapping_block_and_feedback(client, monkeypatch):
    from src.api.routes import inference
    conclusion = dict(rule_id='R', rule_type='treatment', treatment_items=[dict(code='X', name='测试项目')])
    monkeypatch.setattr(inference, '_inference_result', lambda *a, **k: dict(conclusions=[conclusion], treatment_conclusions=[conclusion], diagnostics={}))
    response = client.post('/api/cardiomyopathy/recommendations?actor=test', json={'patient': patient().model_dump(mode='json')})
    card = response.json()['recommendations'][0]
    assert card['items'][0]['blocked_reason'] == 'mapping_missing'
    feedback = dict(hospital_id='H', patient_id='P', status='accepted')
    assert client.post(f"/api/cardiomyopathy/recommendations/{card['recommendation_id']}/feedback?actor=test", json=feedback).status_code == 409
    mapping = dict(hospital_id='H', code='X', local_code='LOCAL-X', name='测试医嘱', source='test', status='active', reviewer='test')
    client.put('/api/cardiomyopathy/order-mappings?actor=test', json=mapping)
    card = client.post('/api/cardiomyopathy/recommendations?actor=test', json={'patient': patient().model_dump(mode='json')}).json()['recommendations'][0]
    assert card['items'][0]['local_code'] == 'LOCAL-X'
    assert client.post(f"/api/cardiomyopathy/recommendations/{card['recommendation_id']}/feedback?actor=test", json=feedback).status_code == 200


def test_duration_and_strict_observation_unknown():
    assert DurationCheckEvaluator().evaluate(patient(), None, {'symptom_code': 'X'}).detail['evaluation_status'] == 'unknown'
    negative = patient(symptoms=[SymptomObservation(symptom_code='X', present=False)])
    assert DurationCheckEvaluator().evaluate(negative, None, {'symptom_code': 'X'}).matched is False
    result = ObservationPresentEvaluator().evaluate(patient(), None, {'code': 'X', 'concept_code': 'ABSENT', 'missing_policy': 'unknown'})
    assert not result.matched and result.detail['evaluation_status'] == 'unknown'


def test_hcm_context_age_method_and_expiry():
    row = LabInterpretationStandard(hospital_id='H', item_type='exam', exam_code='ECHO', finding_code='X',
        concept_code='GE_15', interpretation_type='numeric', operator='>=', threshold_value=15, threshold_unit='mm', context_age_min=18, method='test')
    finding = ExamFinding(exam_code='ECHO', finding_code='X', value=16, unit='mm', method='test', performed_at=datetime.now(timezone.utc)-timedelta(days=10))
    p = patient(exam_findings=[finding])
    params = dict(exam_code='ECHO', finding_code='X', max_age_hours=24)
    assert interpret_exam(p, row, params).detail['evaluation_status'] == 'unknown'
    assert interpret_exam(p, row, {**params, 'max_age_hours': 480}).matched
    p.age = 12
    assert interpret_exam(p, row, params).detail['evaluation_status'] == 'unknown'


def test_history_as_of_and_family_screening(client):
    p = patient().model_dump(mode='json')
    for year in (2024, 2025):
        assert client.put('/api/cardiomyopathy/patient-snapshots?actor=test', json={**p, 'encounter_time':f'{year}-01-01T00:00:00Z'}).status_code == 200
    history = client.get('/api/cardiomyopathy/patient-snapshots?hospital_id=H&patient_id=P&at=2024-06-01T00:00:00Z').json()
    assert len(history['snapshots']) == 1
    assert history['latest']['encounter_time'].startswith('2024')
    member = dict(family_id='F', member_id='M', hospital_id='H', relationship='child',index_patient_id='P',patient_id='CHILD',source='test', consent_recorded=True, classification='VUS')
    client.put('/api/cardiomyopathy/family-members?actor=test', json=member)
    # Uncertain variants do not themselves create screening recommendations.
    assert client.get('/api/cardiomyopathy/follow-ups?hospital_id=H&patient_id=CHILD').json() == []
    task = dict(hospital_id='H',family_id='F',member_id='M',task_id='SCREEN',disease_code='HCM',title='专家指定筛查',due_at='2027-01-01T00:00:00Z',source='clinician-reviewed')
    assert client.post('/api/cardiomyopathy/family-screening-tasks?actor=test', json=task).status_code == 200
    assert len(client.get('/api/cardiomyopathy/follow-ups?hospital_id=H&patient_id=CHILD').json()) == 1


def test_contract_blocks_rule_without_changing_legacy_execution():
    from tests.test_engine import build_engine
    from src.models.rule import Rule
    engine = build_engine()
    rule = Rule(rule_id='CONTRACT', disease_code='TEST', status='active',
        conditions={'type':'demographic', 'age_min':18}, conclusion={'diagnosis':'TEST'},
        clinical_context={'scenario_contract':{'trigger_events':['result'], 'actions':['review'], 'required_attributes':['x']}})
    engine.rule_repo.list_all = lambda: [rule]
    # Repository domain filtering may call list_by_type in some engine versions.
    assert not engine.run(patient())
    assert engine.evaluation_issues
    assert engine.run(patient(clinical_attributes={'trigger_event':'result', 'x':1}))


def test_only_approved_order_sets_expand():
    from src.cardiomyopathy.service import expand_order_sets
    graph = {'nodes': {'S':dict(node_type='order_set',code='S',review_status='active'),
        'I':dict(node_type='order_item',code='I',name='检查',review_status='active')},
        'relations': {'E':dict(source_node_id='S',target_node_id='I',relation_code='ORDER_SET_INCLUDES_ITEM',review_status='active')}}
    items, issues = expand_order_sets(graph, ['S'])
    assert items[0]['code'] == 'I' and not issues
    graph['nodes']['I']['review_status'] = 'draft'
    items, issues = expand_order_sets(graph, ['S'])
    assert not items and issues


def test_assessments_do_not_publish_diagnosis_facts():
    from src.models.conclusion import Conclusion
    from src.engine.inference_engine import InferenceEngine
    facts = {}
    InferenceEngine._materialize_facts(facts, [Conclusion(rule_id='R', disease_code='HCM',
        rule_type='diagnosis', clinical_intent='risk_stratification')])
    assert not facts
    InferenceEngine._materialize_facts(facts, [Conclusion(rule_id='D', disease_code='HCM',
        rule_type='diagnosis', clinical_intent='diagnosis_confirmation')])
    assert facts['disease']['HCM'].rule_id == 'D'


def test_changed_mapping_invalidates_acceptance(client, monkeypatch):
    from src.api.routes import inference
    conclusion = dict(rule_id='R', rule_type='treatment', treatment_items=[dict(code='X', name='测试项目')])
    monkeypatch.setattr(inference, '_inference_result', lambda *a, **k: dict(conclusions=[conclusion], treatment_conclusions=[conclusion], diagnostics={}))
    mapping = dict(hospital_id='H',code='X',local_code='A',name='项目',source='test',status='active',reviewer='test')
    client.put('/api/cardiomyopathy/order-mappings?actor=test',json=mapping)
    card = client.post('/api/cardiomyopathy/recommendations?actor=test',json={'patient':patient().model_dump(mode='json')}).json()['recommendations'][0]
    client.put('/api/cardiomyopathy/order-mappings?actor=test',json={**mapping,'local_code':'B'})
    response = client.post(f"/api/cardiomyopathy/recommendations/{card['recommendation_id']}/feedback?actor=test",json=dict(hospital_id='H',patient_id='P',status='accepted'))
    assert response.status_code == 409


def test_latest_finding_excludes_future_and_rejects_conflicting_timestamp():
    rows = [ExamFinding(exam_code='ECHO',finding_code='X',value=v,unit='mm',performed_at=datetime(y,1,1,tzinfo=timezone.utc))
            for y,v in [(2026,20),(2024,12),(2025,16)]]
    p = patient(exam_findings=rows, encounter_time=datetime(2025,6,1,tzinfo=timezone.utc))
    assert p.get_latest_finding('ECHO','X').value == 16
    p.exam_findings.append(rows[2].model_copy(update={'value':17}))
    assert p.get_latest_finding('ECHO','X') is None
