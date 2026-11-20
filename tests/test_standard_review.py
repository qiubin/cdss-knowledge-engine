import asyncio
import copy
import json

import pytest
from fastapi import HTTPException

from src.review.service import ReviewService
from src.review.standards import stage_standards, review_standard, list_standard_items
from src.review.queue import build_queue
from src.standards.repository import JsonStandardRepository
from src.kg.schema import ConceptVocabulary
from src.models.patient import PatientSnapshot, ExamFinding
from src.models.rule import RuleCondition
from src.review.audit import load_audit
from tests.test_engine import build_engine


def setup(tmp_path, kind='exam'):
    graph_dir = tmp_path / 'kg'; graph_dir.mkdir()
    service = ReviewService(graph_dir, tmp_path / 'rules.json', tmp_path / 'audit.jsonl')
    chunk = {'chunk_id':'DOC-T_p1_l1-2', 'page_start':1, 'line_start':1, 'line_end':2,
        'text':'成人静息左室流出道峰值压差≥30 mmHg为梗阻。'}
    source = {**{k:chunk[k] for k in ['chunk_id','page_start','line_start','line_end']}, 'quote':chunk['text']}
    leaf = {'type':'exam_finding' if kind == 'exam' else 'lab_concept',
        'exam_code':'ECHO', 'finding_code':'LVOT_REST', 'lab_code':'LAB1', 'concept_code':'PRESENT',
        'parameter':{'interpretation':{'interpretation_type':'numeric','operator':'>=', 'threshold_value':30,
            'threshold_unit':'mmHg','context_age_min':18}}}
    rules = [{'rule_id':'R', 'source':source, 'clinical_context':{'population':'成人'}, 'conditions':leaf}]
    graph = {'domain':'western', 'nodes':[], 'relations':[], 'sources':[]}
    stage_standards(graph, rules, [], [chunk], 'DOC-T', '测试指南', 'TEST.json')
    service._save_graph(graph, 'TEST.json')
    return service, graph['standard_candidates'][0], leaf, rules, chunk


def action(service, candidate, **changes):
    return review_standard(service, candidate['candidate_id'], {'action':'approve','version':candidate['version'],
        'reviewer':'医生甲','hospital_ids':['H'], 'scope_confirmed':True, **changes})


def test_stage_removes_inline_and_is_idempotent(tmp_path):
    service, candidate, leaf, rules, chunk = setup(tmp_path)
    assert 'interpretation' not in leaf['parameter']
    assert leaf['standard_required'] and leaf['concept_code'].startswith('GUIDE_')
    graph = service._load_graph('TEST.json')
    stage_standards(graph, rules, [], [chunk], 'DOC-T', '测试指南', 'TEST.json')
    assert len(graph['standard_candidates']) == 1
    assert candidate['rule_ids'] == ['R']
    items = build_queue(service, packages=[], item_type='standard')['items']
    assert len(items) == 1 and items[0]['status'] == 'draft'
    assert items[0]['source']['quote'] == chunk['text']


def test_approve_publishes_then_return_removes_execution_and_logs(tmp_path):
    service, candidate, leaf, _, _ = setup(tmp_path)
    engine = build_engine()
    engine.standard_repo = JsonStandardRepository(str(tmp_path / 'standards'))
    patient = PatientSnapshot(patient_id='T', hospital_id='H', age=52, exam_findings=[
        ExamFinding(exam_code='ECHO',finding_code='LVOT_REST',value=58,unit='mmHg')])
    condition = RuleCondition(**leaf)
    assert engine._evaluate_condition(condition, patient)[0] is None
    result = action(service, candidate)
    assert result['status'] == 'active'
    engine.standard_repo = JsonStandardRepository(str(tmp_path / 'standards')); engine._atom_cache = {}
    assert engine._evaluate_condition(condition, patient)[0] is True
    assert engine.standard_repo.get_exam_active('OTHER','ECHO','LVOT_REST',leaf['concept_code']) is None
    vocab = ConceptVocabulary(); vocab.load(str(tmp_path / 'vocab/concepts.json'))
    assert vocab.exists(leaf['concept_code'])
    action(service, candidate, action='return', version=result['version'])
    engine.standard_repo = JsonStandardRepository(str(tmp_path / 'standards')); engine._atom_cache = {}
    assert engine._evaluate_condition(condition, patient)[0] is None
    assert [r['action'] for r in load_audit(service.audit_path)] == ['return','approve']
    assert len(list_standard_items(service)) == 1  # no duplicate published hospital rows


@pytest.mark.parametrize('changes', [dict(scope_confirmed=False),dict(hospital_ids=[]),dict(hospital_ids=['../bad']),dict(version=999),dict(reviewer='')])
def test_bad_review_is_rejected_without_publish(tmp_path, changes):
    service, candidate, *_ = setup(tmp_path)
    with pytest.raises(HTTPException): action(service,candidate,**changes)
    assert not list((tmp_path / 'standards').glob('*.json'))
    assert list_standard_items(service)[0]['status'] == 'draft'


def test_missing_quote_blocks_approval_and_rejection_needs_reason(tmp_path):
    service, candidate, *_ = setup(tmp_path)
    graph = service._load_graph('TEST.json'); graph['standard_candidates'][0]['source']['quote'] = '编造原文'
    service._save_graph(graph, 'TEST.json')
    with pytest.raises(HTTPException): action(service, candidate)
    with pytest.raises(HTTPException): action(service, candidate, action='reject')
    assert action(service, candidate, action='reject', note='原文不支持')['status'] == 'rejected'


def test_edit_resets_active_and_requires_new_review(tmp_path):
    service,candidate,*_ = setup(tmp_path)
    result = action(service,candidate)
    saved = action(service,candidate,action='save',version=result['version'],standard={'threshold_value':35})
    assert saved['status'] == 'draft'
    repo=JsonStandardRepository(str(tmp_path/'standards'))
    assert repo.get_exam_active('H','ECHO','LVOT_REST',candidate['standard']['concept_code']) is None
    with pytest.raises(HTTPException): action(service,candidate,version=saved['version'],standard={'threshold_value':40})
    assert action(service,candidate,version=saved['version'])['status'] == 'active'


def test_hospital_draft_can_be_reviewed_in_same_queue(tmp_path):
    service,candidate,*_ = setup(tmp_path)
    directory=tmp_path/'standards'; directory.mkdir()
    row={**candidate['standard'],'hospital_id':'H','concept_code':'GE_30'}
    (directory/'h.json').write_text(json.dumps([row]))
    item=next(i for i in list_standard_items(service) if i['origin']=='hospital')
    result=review_standard(service,item['item_id'],{'action':'approve','version':1,'reviewer':'医生甲'})
    assert result['status']=='active'
    assert JsonStandardRepository(str(directory)).get_exam_active('H','ECHO','LVOT_REST','GE_30',context_age=52)


def test_unreviewed_lab_in_not_is_unknown(tmp_path):
    service,candidate,leaf,*_ = setup(tmp_path,kind='lab')
    engine=build_engine(); engine.standard_repo=JsonStandardRepository(str(tmp_path/'standards'))
    patient=PatientSnapshot(patient_id='T',hospital_id='H',age=52)
    condition=RuleCondition(type='group',operator='NOT',children=[RuleCondition(**leaf)])
    assert engine._evaluate_condition(condition,patient)[0] is None


def test_generated_standard_cannot_bypass_source_review_via_legacy_activation(tmp_path, monkeypatch):
    from src.api.routes import standards as api
    service, candidate, *_ = setup(tmp_path)
    action(service, candidate)
    monkeypatch.setattr(api, 'STANDARDS_DIR', tmp_path/'standards')
    with pytest.raises(HTTPException) as error:
        asyncio.run(api.activate_standard('h', 0))
    assert error.value.status_code == 409
    with pytest.raises(HTTPException):
        asyncio.run(api.update_standard('h', 0, {}))


def test_import_route_stages_raw_and_inline_numeric_standards(tmp_path, monkeypatch):
    from src.api.routes import kg as api
    service, candidate, _, _, chunk = setup(tmp_path)
    monkeypatch.setattr(api, 'KG_DIR', tmp_path/'kg')
    vocab = tmp_path/'vocab'; vocab.mkdir()
    (vocab/'concepts.json').write_text('[]')
    leaf={'type':'exam_finding','exam_code':'ECHO','finding_code':'REST','concept_code':'PRESENT',
        'parameter':{'interpretation':{'interpretation_type':'numeric','operator':'>=',
            'threshold_value':30,'threshold_unit':'mmHg'}}}
    rule={'rule_id':'IMPORT-R','disease_code':'I42.1','disease_name':'梗阻性肥厚型心肌病',
        'rule_type':'diagnosis','conditions':leaf,'conclusion':{'diagnosis':'I42.1'},'source':candidate['source']}
    request=api.GraphImportRequest(doc_id='DOC-T',doc_title='测试指南',graph_filename='IMPORT.json',
        entities=[],chunks=[chunk],candidate_rules=[rule],candidate_standards=[
            {**candidate['standard'],'finding_code':'PROVOKED','source':candidate['source']}])
    result=asyncio.run(api.import_graph(request))
    graph=service._load_graph('IMPORT.json')
    assert result['standard_candidate_count']==2
    assert len(graph['standard_candidates'])==2
    assert all(c['status']=='draft' for c in graph['standard_candidates'])
    imported=next(b for b in graph['clinical_bases'] if b['specification']['conditions'].get('finding_code')=='REST')
    assert 'interpretation' not in imported['specification']['conditions']['parameter']
    assert imported['specification']['conditions']['standard_required']


def test_editing_local_scope_replaces_old_active_row(tmp_path):
    service,candidate,*_ = setup(tmp_path)
    directory=tmp_path/'standards'; directory.mkdir()
    row={**candidate['standard'],'hospital_id':'H','concept_code':'GE_30','status':'active'}
    (directory/'h.json').write_text(json.dumps([row]))
    item=next(i for i in list_standard_items(service) if i['origin']=='hospital')
    review_standard(service,item['item_id'],{'action':'save','version':1,'reviewer':'医生甲','standard':{'context_age_min':20}})
    rows=json.loads((directory/'h.json').read_text())
    assert len(rows)==1 and rows[0]['status']=='draft' and rows[0]['context_age_min']==20
