import copy
import json
from pathlib import Path
import pytest
from src.domains.western.recommendations import assess, recommendation_issues, resolve_conflicts
from src.models.rule import Rule
from src.models.patient import PatientSnapshot, PatientDiagnosis
from tests.test_engine import build_engine

SOURCE={'guideline_id':'DOC-TEST','chunk_id':'DOC-TEST_p1_l1-2','page_start':1,'line_start':1,'line_end':2,'quote':'虚构测试推荐，不用于临床。'}


def action(**changes):
    return dict(code='TEST-MED',name='虚构药物',kind='medication',verb='start',regimen={'dose':'测试剂量','frequency':'测试频次','route':'测试途径'},source=SOURCE,**changes)


def rule(actions=None, **changes):
    rec=dict(schema_version=1,diagnosis_stage='suspected',clinical_question='虚构测试临床问题',combination='single',scope_confirmed=True,actions=actions or [action()])
    rec.update(changes)
    return Rule(rule_id='WEST-TEST',name='测试推荐',status='active',rule_type='treatment',disease_code='TEST-D',
        conditions={'type':'group','operator':'AND','children':[]},source=SOURCE,
        conclusion={'diagnosis':'TEST-D'},clinical_context={'western_recommendation':rec})


def patient(**changes):
    return PatientSnapshot(patient_id='T',hospital_id='XM_FIRST',age=52,
        diagnoses=[PatientDiagnosis(code='TEST-D',certainty='suspected')],**changes)


def evaluate(r=None,p=None):return assess(build_engine(),r or rule(),p or patient(),[])


def test_suspected_does_not_authorize_confirmed_treatment():
    assert evaluate()['status']=='ready'
    assert evaluate(rule(diagnosis_stage='confirmed'))['status']=='pending_diagnosis'
    p=patient();p.diagnoses[0].certainty='confirmed'
    assert evaluate(rule(diagnosis_stage='confirmed'),p)['status']=='ready'
    p.diagnoses[0].clinical_status='historical'
    assert evaluate(rule(diagnosis_stage='confirmed'),p)['status']=='pending_diagnosis'


def test_dose_information_missing_gives_strategy_without_dose():
    a=action();a['required_data']=[{'path':'clinical_attributes.weight_kg','label':'体重','purpose':'dose'}]
    result=evaluate(rule([a]))
    assert result['actions'][0]['status']=='strategy_only'
    assert result['actions'][0]['regimen']=={}
    assert evaluate(rule([a]),patient(clinical_attributes={'weight_kg':60}))['status']=='ready'


def test_unreviewed_allergies_and_missing_contraindications_are_unknown():
    a=action();a['required_data']=[{'path':'clinical_attributes.allergies_reviewed','label':'过敏史核实','expected_value':True}]
    assert evaluate(rule([a]))['status']=='needs_information'
    a['required_data']=[];a['contraindications']=[{'type':'attribute','attribute_code':'test_allergy','operator':'=','attribute_value':True}]
    assert evaluate(rule([a]))['status']=='needs_information'
    assert evaluate(rule([a]),patient(clinical_attributes={'test_allergy':True}))['status']=='contraindicated'
    assert evaluate(rule([a]),patient(clinical_attributes={'test_allergy':False}))['status']=='ready'


def test_existing_medication_does_not_repeat_start():
    assert evaluate(p=patient(current_medications=['TEST-MED']))['status']=='needs_review'


def test_alternatives_and_combination_keep_distinct_semantics():
    first=action();second=action();second['code']='TEST-OTHER';second['required_data']=[{'path':'clinical_attributes.egfr','label':'肾功能'}]
    r=rule([first,second],combination='alternatives',selection_group='TEST-G')
    result=evaluate(r)
    assert result['status']=='partial' and result['actions'][0]['status']=='ready'
    r.clinical_context['western_recommendation']['combination']='combined'
    result=evaluate(r)
    assert result['status']=='needs_information' and result['actions'][0]['status']=='conditional'
    assert not result['actions'][0]['regimen']


def test_emergency_independent_needs_trigger_and_can_run_without_diagnosis():
    r=rule([dict(code='TEST-REF',name='急诊评估测试',kind='referral',verb='assess',source=SOURCE)],diagnosis_stage='independent')
    assert recommendation_issues(r.model_dump())
    r.conditions=type(r.conditions)(type='attribute',attribute_code='emergency',operator='=',attribute_value=True)
    p=patient(clinical_attributes={'emergency':True});p.diagnoses=[]
    assert evaluate(r,p)['status']=='ready'


def test_avoid_and_incompatible_doses_block_concrete_recommendations():
    rows=[evaluate(),evaluate(rule([dict(code='TEST-MED',name='避免虚构药物',kind='medication',verb='avoid',source=SOURCE)]))]
    resolve_conflicts(rows)
    assert rows[0]['actions'][0]['status']=='contraindicated'
    a=action();a['regimen']={'dose':'另一测试剂量','frequency':'测试频次','route':'测试途径'}
    rows=[evaluate(),evaluate(rule([a]))];resolve_conflicts(rows)
    assert all(r['status']=='needs_review' and r['actions'][0]['regimen']=={} for r in rows)


def test_draft_recommendations_do_not_run_and_published_rules_have_assessments(tmp_path):
    from src.rules.repository import JsonRuleRepository
    r=rule();r.status='draft'
    (tmp_path/'managed_rules.json').write_text(json.dumps([r.model_dump()]))
    engine=build_engine();engine.rule_repo=JsonRuleRepository(str(tmp_path))
    engine.run(patient());assert engine.recommendation_assessments==[]
    r.status='active';engine.rule_repo.save_rule(r)
    engine.run(patient());assert engine.recommendation_assessments[0]['status']=='ready'
    assert not any(c.rule_id==r.rule_id for c in engine.run(patient()))  # no duplicate legacy positive recommendation


def test_missing_record_under_not_does_not_become_positive():
    r=rule();r.conditions=type(r.conditions)(type='group',operator='NOT',children=[{'type':'symptom','symptom_code':'MISSING'}])
    assert evaluate(r)['status']=='needs_information'


def test_unconfirmed_scope_and_invalid_combination_are_blocked():
    assert evaluate(rule(scope_confirmed=False))['status']=='needs_review'
    assert recommendation_issues(rule([action(),action()]).model_dump())
    assert recommendation_issues(rule([action(),action()],combination='sequential').model_dump())


def test_extraction_prompt_and_template_contract_request_clinical_recommendations():
    from src.extraction.auto_pipeline import _auto_prompt
    assert 'western_recommendation' in _auto_prompt([], 'TEST')[0]['content']


@pytest.mark.parametrize('condition', [
 {'type':'lab_concept','lab_code':'CTN','concept_code':'ELEVATED'},
 {'type':'exam_finding','exam_code':'ECHO','finding_code':'MISSING'},
])
def test_missing_tests_under_not_stay_unknown(condition):
    r=rule();r.conditions=type(r.conditions)(type='group',operator='NOT',children=[condition])
    assert evaluate(r)['status']=='needs_information'


def test_unrelated_disease_recommendations_are_not_shown(tmp_path):
    from src.rules.repository import JsonRuleRepository
    r=rule();(tmp_path/'managed_rules.json').write_text(json.dumps([r.model_dump()]))
    engine=build_engine();engine.rule_repo=JsonRuleRepository(str(tmp_path))
    p=patient();p.diagnoses=[]
    engine.run(p)
    assert engine.recommendation_assessments==[]


# Reuse the isolated review fixture; no live clinical assets are mutated.
from tests.test_clinical_basis import env


def test_graph_review_to_basis_rule_and_patient_recommendation(env):
    from src.api.routes import clinical_basis as api
    from src.api.routes.kg import _ensure_treatment_plan_semantics, DEFAULT_RELATION_NAMES
    from src.rules.repository import JsonRuleRepository
    service,path,rules_path,basis_id=env
    graph=service._load_graph('REVIEW-TEST.json')
    basis=graph['clinical_bases'][0]
    source={**basis['specification']['source'],'chunk_id':'DOC-TEST_p1_l1-2'}
    r=rule([dict(code='TEST-REF',name='虚构专科评估',kind='referral',verb='assess',source=source)])
    r.disease_code='I21.0';r.source=type(r.source)(**source)
    r.conclusion.diagnosis='I21.0';r.conclusion.treatment_plan_id='TP-TEST';r.conclusion.treatment_name='虚构评估方案'
    r.conclusion.treatment_items=[{'code':'TEST-REF','name':'虚构专科评估','entity_type':'referral'}]
    basis['specification']=r.model_dump()
    _ensure_treatment_plan_semantics(graph,basis['specification'],DEFAULT_RELATION_NAMES)
    for node in graph['nodes'].values():node['review_status']='active'
    for edge in graph['relations'].values():edge['review_status']='active'
    service._save_graph(graph,'REVIEW-TEST.json')
    check=api.list_bases('REVIEW-TEST.json')['items'][0]['validation']
    assert check['issues']==[],check['issues']
    api.review_basis(basis_id,api.BasisReview(action='approve',reviewer='测试审核医生',checks={k:True for k in ['entities','logic','scope','source']}),'REVIEW-TEST.json')
    generated=api.generate_rule(basis_id,'REVIEW-TEST.json')
    assert generated['status']=='draft'
    repo=JsonRuleRepository(str(rules_path.parent));saved=repo.list_all()[0]
    assert saved.clinical_context['western_recommendation']['actions'][0]['kind']=='referral'
    engine=build_engine();engine.rule_repo=repo
    p=patient();p.diagnoses=[PatientDiagnosis(code='I21.0',certainty='suspected')]
    engine.run(p);assert engine.recommendation_assessments==[]
    repo.save_rule(saved.model_copy(update={'status':'active'}))
    engine.run(p)
    assert engine.recommendation_assessments[0]['status']=='ready'


def test_explicit_drug_allergy_blocks_medication_recommendation():
    result=evaluate(p=patient(allergies=['TEST-MED']))
    assert result['actions'][0]['status']=='contraindicated'
    assert not result['actions'][0]['regimen']
