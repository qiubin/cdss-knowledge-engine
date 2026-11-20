"""Synthetic contracts test mechanics, not clinical diagnostic validity."""
import copy
import json
from pathlib import Path
import pytest
from src.extraction.models import ExtractionTemplate
from src.domains.tcm.clinical_schema import generate_v3_rules, ClinicalContract, HerbComponent
from src.domains.tcm.checks import TcmGraphSemanticChecker
from src.models.rule import Rule, RuleCondition
from src.models.patient import PatientSnapshot
from src.engine.inference_engine import InferenceEngine

@pytest.fixture
def sample():
    template = ExtractionTemplate(**next(t for t in json.loads(Path('data/templates/extraction_templates.json').read_text()) if t['template_id']=='TCM_GUIDELINE_V3'))
    source = dict(guideline_id='DOC-T', chunk_id='c1', page_start=1, line_start=1, line_end=3, quote='Synthetic criteria')
    criteria = [dict(criterion_id=code, role='supporting', source=source,
                     condition=dict(type='observation', observation_type='symptom', code=code, concept_code='PRESENT')) for code in ('A','B','C')]
    contract = dict(rule_type='tcm_pattern', target_type='syndrome', target_code='S', name='Synthetic',
                    scope=dict(type='past_history', code='HF'), criteria=criteria,
                    combination=dict(type='count_group', min_count=2, children=[dict(criterion_id=c) for c in ('A','B','C')]),
                    logic_origin='guideline_explicit', logic_source=source)
    entity = dict(entity_type='clinical_rule', code='CR', attributes={'CLINICAL_RULE':contract})
    return template, [entity], [dict(chunk_id='c1', text='Synthetic criteria')]


def generate(sample):
    return generate_v3_rules(sample[1],sample[2],sample[0],'DOC-T')[0]


def test_no_scoring_or_same_chunk_pairing_without_contract(sample):
    assert generate_v3_rules([dict(entity_type='syndrome',code='S',chunk_id='c1')],sample[2],sample[0],'DOC-T') == []
    rule=generate(sample)
    assert rule['status']=='draft'
    assert rule['conditions']['children'][1]['type']=='count_group'
    assert rule['conditions']['children'][1]['min_count']==2
    assert rule['conditions']['children'][1]['threshold'] is None
    assert rule['clinical_context']['schema_version']==3


def test_unknown_reference_and_unsupported_source_rejected(sample):
    contract=sample[1][0]['attributes']['CLINICAL_RULE']
    contract['combination']['children'][0]['criterion_id']='UNKNOWN'
    with pytest.raises(ValueError): generate(sample)
    contract['combination']['children'][0]['criterion_id']='A'
    contract['logic_source']['quote']='invented'
    with pytest.raises(ValueError): generate(sample)


def test_scope_cannot_bypass_disease_via_or(sample):
    sample[1][0]['attributes']['CLINICAL_RULE']['scope']={'type':'group','operator':'OR','children':[{'type':'past_history','code':'HF'},{'type':'demographic','age_min':18}]}
    with pytest.raises(ValueError): generate(sample)


def test_ai_logic_is_pending_and_doses_are_contextual(sample):
    sample[1][0]['attributes']['CLINICAL_RULE']['logic_origin']='ai_suggested'
    assert generate(sample)['clinical_context']['missing_information']
    with pytest.raises(ValueError): HerbComponent(code='H',name='Herb',dose_min=1,source=sample[1][0]['attributes']['CLINICAL_RULE']['logic_source'])


def graph_for(rule):
    nodes=[dict(id=f'ENTITY:{kind}:{code}',node_type=kind,code=code,review_status='active',attributes={})
           for kind,code in [('syndrome','S'),('disease','HF'),('clinical_rule','CR'),('symptom','A'),('symptom','B'),('symptom','C')]]
    relations=[dict(source_node_id='ENTITY:syndrome:S',relation_code=rel,target_node_id=f'ENTITY:{kind}:{code}',review_status='active')
               for rel,kind,code in [('SYNDROME_HAS_CLINICAL_RULE','clinical_rule','CR'),*[('SYNDROME_HAS_SYMPTOM','symptom',c) for c in ('A','B','C')]]]
    return dict(nodes=nodes,relations=relations)


def test_approved_nodes_do_not_replace_approved_relationships(sample):
    rule=generate(sample);graph=graph_for(rule)
    result=TcmGraphSemanticChecker(graph).check_rule(rule)
    assert result['semantic_status']=='consistent'
    assert result['dependency_status']=='passed'
    graph['relations'][1]['review_status']='draft'
    assert TcmGraphSemanticChecker(graph).check_rule(rule)['dependency_status']=='blocked'
    graph['relations']=[]
    assert TcmGraphSemanticChecker(graph).check_rule(rule)['semantic_status']=='inconsistent'


def test_count_group_three_valued_evaluation(sample):
    engine=InferenceEngine.__new__(InferenceEngine)
    # Exercise native group execution independently of terminology lookup.
    tree=RuleCondition(type='count_group',min_count=2,children=[RuleCondition(type='fact',code=c,fact_type='syndrome') for c in ('A','B','C')])
    engine._evaluate_condition=lambda child,patient: ({'A':True,'B':None,'C':False}[child.code],[])
    assert engine._evaluate_condition_uncached(tree,None)[0] is None
    engine._evaluate_condition=lambda child,patient: (child.code in {'A','B'},[])
    assert engine._evaluate_condition_uncached(tree,None)[0] is True
    engine._evaluate_condition=lambda child,patient: (child.code=='A',[])
    assert engine._evaluate_condition_uncached(tree,None)[0] is False


def test_full_patient_run_respects_scope_and_contraindications(sample,tmp_path):
    from src.rules.repository import JsonRuleRepository
    from src.kg.client import JsonGraphClient
    from src.kg.schema import ConceptVocabulary
    from src.standards.repository import JsonStandardRepository
    pattern=generate(sample);pattern['status']='active';pattern['graph_filename']='G.json'
    treatment=copy.deepcopy(pattern)
    treatment.update(rule_id='RX',rule_type='tcm_prescription',conditions={'type':'fact','fact_type':'syndrome','code':'S'},conclusion={'diagnosis':'S','formula_code':'F','herbs':[{'code':'H'}], 'modification_rules':[{'action':'replace','target_code':'H','component':{'code':'H2'},'condition':{'type':'observation','observation_type':'symptom','code':'A','concept_code':'PRESENT','parameter':{'schema_version':3}}}]})
    contra=copy.deepcopy(treatment)
    contra.update(rule_id='BLOCK',rule_type='tcm_contraindication',conclusion={'diagnosis':'S','blocked_targets':['H2'],'message':'Synthetic block'})
    (tmp_path/'rules').mkdir();(tmp_path/'standards').mkdir()
    (tmp_path/'rules'/'managed_rules.json').write_text(json.dumps([pattern,treatment,contra]))
    (tmp_path/'G.json').write_text(json.dumps({**graph_for(pattern),'domain':'tcm','diseases':[],'syndromes':[],'evidences':[],'disease_evidence':[],'sources':[]}))
    engine=InferenceEngine(JsonRuleRepository(str(tmp_path/'rules')),JsonGraphClient(str(tmp_path/'G.json')),JsonStandardRepository(str(tmp_path/'standards')),ConceptVocabulary(),domain='tcm')
    patient=PatientSnapshot(patient_id='SYNTHETIC',hospital_id='TEST',past_history=['HF'],symptoms=[{'symptom_code':c,'present':True} for c in ('A','B')])
    result=engine.run(patient)
    assert {c.rule_type for c in result}=={'tcm_pattern','tcm_contraindication'}
    patient.past_history=[]
    assert engine.run(patient)==[]


def test_llm_payload_preserves_objects_and_ignores_free_form_rules(sample):
    from src.extraction.service import _validate_llm_payload
    template, entities, chunks=sample
    template=template.model_copy(deep=True)
    # Use fixed dictionary IDs so this test verifies the contract, not discovery naming.
    for kind,code in [('clinical_rule','CR'),('syndrome','S')]:
        from src.extraction.models import TemplateEntityEntry
        next(t for t in template.entity_types if t.entity_type==kind).entries.append(TemplateEntityEntry(code=code,name=code))
    chunks[0].update(section='test',page_start=1,page_end=1,line_start=1,line_end=3)
    payload={'entities':[{**entities[0],'name':'CR','chunk_id':'c1'},dict(entity_type='syndrome',code='S',name='S',chunk_id='c1')],
             'suggested_rules':[{'rule_id':'UNTRUSTED','disease_code':'S','conditions':{'type':'group','operator':'OR','children':[]}}]}
    result=_validate_llm_payload(payload,chunks,template,'DOC-T','Synthetic')
    assert isinstance(result[0][0]['attributes']['CLINICAL_RULE'],dict)
    assert len(result[1])==1
    assert result[1][0]['rule_id']!='UNTRUSTED'
    assert result[1][0]['conditions']['children'][1]['min_count']==2


def test_invalid_contract_is_visible_without_discarding_other_entities(sample):
    sample[1][0]['attributes']['CLINICAL_RULE']['combination']['children'][0]['criterion_id']='invalid'
    warnings=[]
    assert generate_v3_rules(sample[1],sample[2],sample[0],'DOC-T',warnings=warnings)==[]
    assert warnings and 'CR' in warnings[0]


def test_element_contract_and_scoring_need_explicit_numbers(sample):
    contract=sample[1][0]['attributes']['CLINICAL_RULE']
    contract.update(rule_type='tcm_element',target_type='syndrome_element',target_code='E')
    rule=generate(sample)
    assert rule['anchor_type']=='syndrome_element'
    assert rule['rule_type']=='tcm_element'
    contract['combination']={'type':'weighted_group','children':[{'criterion_id':'A'}]}
    with pytest.raises(ValueError):generate(sample)
    contract['combination'].update(scoring='normalized_weight_sum',threshold=0.7)
    contract['combination']['children'][0]['weight']=2
    assert generate(sample)['conditions']['children'][1]['threshold']==0.7


def test_rule_scope_and_logic_confirmation_are_approval_requirements(sample):
    from src.domains.tcm.clinical_schema import validate_v3_rule
    rule=generate(sample)
    assert validate_v3_rule(rule)==[]
    rule['conditions']=rule['conditions']['children'][1]
    assert any('病种' in issue for issue in validate_v3_rule(rule))
    rule=generate(sample)
    rule['clinical_context']['logic_origin']='ai_suggested'
    assert any('确认' in issue for issue in validate_v3_rule(rule))


def test_missing_absent_observation_never_satisfies_v3_negation():
    engine=InferenceEngine.__new__(InferenceEngine)
    engine._atom_cache={}
    condition=RuleCondition(type='observation',observation_type='symptom',code='A',concept_code='ABSENT',parameter={'schema_version':3})
    patient=PatientSnapshot(patient_id='synthetic',hospital_id='T')
    assert engine._evaluate_observation(condition,patient)[0] is None
    engine._evaluate_condition=lambda child,p: engine._evaluate_observation(child,p)
    assert engine._evaluate_group(RuleCondition(type='group',operator='NOT',children=[condition]),patient)[0] is None


def test_prescription_components_and_conditional_modification_roundtrip(sample):
    contract=sample[1][0]['attributes']['CLINICAL_RULE']
    source=contract['logic_source']
    contract.update(rule_type='tcm_prescription',
        criteria=[dict(criterion_id='confirmed',role='required',source=source,
                       condition=dict(type='fact',fact_type='syndrome',code='S'))],
        combination={'criterion_id':'confirmed'},
        conclusion={'formula_code':'F','treatment_method_code':'M','course':'as stated'},
        herb_components=[dict(code='H',name='Herb',dose_min=1,dose_max=2,unit='g',dose_basis='per_day',source=source)],
        modifications=[dict(action='replace',target_code='H',source=source,
                            condition={'type':'observation','observation_type':'symptom','code':'A','concept_code':'PRESENT'},
                            component=dict(code='H2',name='Alternative',dose_min=1,unit='g',source=source))])
    rule=Rule(**generate(sample))
    assert rule.conclusion.herbs[0]['dose_basis']=='per_day'
    assert rule.conclusion.modification_rules[0]['component']['code']=='H2'
    assert rule.conclusion.modification_rules[0]['condition']['parameter']['schema_version']==3
    assert rule.clinical_context['missing_information']==[]


def test_discovered_codes_rewrite_typed_references_without_touching_quotes(sample):
    from src.domains.tcm.clinical_schema import remap_contract_codes
    contract=sample[1][0]['attributes']['CLINICAL_RULE']
    before=copy.deepcopy(contract['logic_source'])
    remap_contract_codes(contract,{('syndrome','S'):'LOCAL-S',('symptom','A'):'LOCAL-A',('disease','HF'):'LOCAL-HF'})
    assert contract['target_code']=='LOCAL-S'
    assert contract['criteria'][0]['condition']['code']=='LOCAL-A'
    assert contract['scope']['code']=='LOCAL-HF'
    assert contract['logic_source']==before
