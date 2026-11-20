import json
import pytest
from src.llm.client import parse_json_response, LLMError
from src.extraction.chunk_limits import bounded_chunks


def test_multiple_extraction_objects_merge_without_dropping_records():
    result=parse_json_response('```json\n{"entities":[{"code":"A"}]}\n```\n```json\n{"entities":[{"code":"B"}],"suggested_relations":[]}\n```')
    assert [e['code'] for e in result['entities']]==['A','B']
    assert parse_json_response('{"result":{"entities":[]}}')=={'entities':[]}


def test_truncated_following_object_is_not_silently_dropped():
    with pytest.raises(LLMError): parse_json_response('{"entities":[]}\n{"entities":[')
    with pytest.raises(LLMError): parse_json_response('{"a":1}\n{"a":2}')


def test_bounded_chunks_preserve_every_line_and_its_location():
    original={'chunk_id':'C','section':'S','page_start':1,'page_end':2,'line_start':9,'line_end':2,
              'page_numbers':[1,1,2,2],'line_numbers':[9,10,1,2],'text':'AAAA\nBBBB\nCCCC\nDDDD'}
    parts=bounded_chunks([original],10)
    assert '\n'.join(c['text'] for c in parts)==original['text']
    assert [x for c in parts for x in c['line_numbers']]==[9,10,1,2]
    assert len({c['chunk_id'] for c in parts})==len(parts)
    assert parts[-1]['page_start']==2


def test_generate_package_uses_saved_upload_graph_and_blocks_empty(monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from src.api.routes import guide_packages as api
    doc={'doc_id':'D','doc_title':'test','entities':[], 'extraction':{'domain':'tcm','graph_filename':'PRESET.json'}}
    monkeypatch.setattr(api,'_load_doc',lambda _:doc)
    with pytest.raises(HTTPException) as error:
        asyncio.run(api.generate_package(api.GuidePackageGenerateRequest(doc_id='D')))
    assert error.value.status_code==409


def test_import_page_retains_configured_graph_and_explains_missing_anchors():
    from pathlib import Path
    source=Path('src/api/static/app.js').read_text()
    assert '沿用已设置图谱' in source
    assert '未识别到可用目标，请先重新解析' in source
    assert 'graphMode.value === "legacy" ? (doc.graph_filename' in source
    assert '重新解析未完成内容' in source


def test_reparse_replaces_empty_result_and_retains_target_graph(tmp_path,monkeypatch):
    import asyncio
    from src.api.routes import pdf as api
    from src.extraction.service import ProviderResult
    from src.extraction.models import ExtractionTemplate
    from types import SimpleNamespace
    doc={'doc_id':'D','doc_title':'Test','parsed':{'total_pages':1,'total_lines':1,'chunks':[{'chunk_id':'c','section':'S','page_start':1,'page_end':1,'line_start':1,'line_end':1,'text':'text'}]},
         'entities':[],'suggested_rules':[],'extraction':{'mode':'llm','template_id':'T','graph_filename':'PRESET.json','metadata':{'coverage':{'complete':True,'items':[{'chunk_id':'c','status':'processed'}]}}}}
    monkeypatch.setattr(api,'UPLOAD_DIR',tmp_path)
    monkeypatch.setattr(api,'_doc_store',{'D':doc})
    monkeypatch.setattr(api,'extraction_templates',SimpleNamespace(get=lambda _:ExtractionTemplate(template_id='T',name='T')))
    monkeypatch.setattr(api,'extraction_config',SimpleNamespace(load=lambda:{}))
    api._persist_doc(doc)
    async def extract(*args,**kwargs):
        return ProviderResult(entities=[{'entity_type':'syndrome','code':'S','name':'证候','chunk_id':'c','page':1,'line_start':1,'line_end':1,'quote':'text'}],suggested_rules=[],warnings=[],metadata={'coverage':{'complete':True,'items':[{'chunk_id':'c','status':'processed'}]}})
    monkeypatch.setattr(api,'extract',extract)
    result=asyncio.run(api.reparse_document('D'))
    assert result.graph_filename=='PRESET.json'
    assert result.entities[0].code=='S'
    saved=json.loads((tmp_path/'D.parsed.json').read_text())
    assert saved['extraction']['metadata']['coverage']['complete']
    assert (tmp_path/'D.parsed.before-reparse.json').exists()


def test_package_generation_inherits_graph_without_reentering_it(tmp_path,monkeypatch):
    import asyncio
    from src.api.routes import guide_packages as api
    doc={'doc_id':'D','doc_title':'test','entities':[{'entity_type':'syndrome','code':'S','name':'证候'}],
         'extraction':{'domain':'tcm','template_id':'TCM_GUIDELINE_V3','graph_filename':'PRESET.json'}}
    monkeypatch.setattr(api,'ROOT',tmp_path)
    monkeypatch.setattr(api,'_load_doc',lambda _:doc)
    monkeypatch.setattr(api,'_load_templates',lambda:[{'template_id':'TCM_GUIDELINE_V3','domain':'tcm','status':'active'}])
    monkeypatch.setattr(api,'_build_checks',lambda _:[])
    monkeypatch.setattr(api,'_save_package',lambda _:None)
    result=asyncio.run(api.generate_package(api.GuidePackageGenerateRequest(doc_id='D')))
    assert result['package']['graph_filename']=='PRESET.json'
    assert result['package']['target_disease_code']=='S'


def test_single_chunk_json_failure_is_split_and_coverage_tracks_children(monkeypatch):
    import asyncio
    from src.extraction import service
    from src.extraction.models import ExtractionTemplate
    calls=[]
    async def fake_chat(settings,messages):
        chunks=json.loads(messages[-1]['content'])['chunks'];calls.append(chunks[0]['chunk_id'])
        if len(calls)==1: raise LLMError('JSON output truncated')
        return {'entities':[{'code':'S','entity_type':'syndrome'}]}
    monkeypatch.setattr(service,'chat_json',fake_chat)
    monkeypatch.setattr(service,'resolve_api_key',lambda _: 'test-key')
    monkeypatch.setattr(service,'resolve_template',lambda x:x)
    monkeypatch.setattr(service,'_validate_llm_payload',lambda payload,*a:(payload['entities'],[],[],[],[],[],[]))
    chunk={'chunk_id':'C','section':'S','page_start':1,'page_end':1,'line_start':1,'line_end':20,
           'text':'\n'.join(['a'*99]*20)}
    chunks=[chunk]
    result=asyncio.run(service.extract_llm(chunks,ExtractionTemplate(template_id='TCM_GUIDELINE_V3',name='T',domain='tcm'),'D','T',{'llm':{'base_url':'http://example.test'}}))
    assert len(calls)==1+len(chunks)
    assert len(chunks)>=2
    assert result.metadata['coverage']['complete']
    assert result.metadata['coverage']['processed']==len(chunks)
    assert all(item['chunk_id']!='C' for item in result.metadata['coverage']['items'])


def test_entity_with_stale_chunk_id_is_retained_from_single_request():
    from src.extraction.service import _validate_llm_payload
    from src.extraction.models import ExtractionTemplate,TemplateEntityType,TemplateEntityEntry
    template=ExtractionTemplate(template_id='T',name='T',entity_types=[TemplateEntityType(entity_type='syndrome',label='证候',entries=[TemplateEntityEntry(code='S',name='气虚血瘀证')])])
    chunk={'chunk_id':'real-child','text':'气虚血瘀证','page_start':1,'page_end':1,'line_start':1,'line_end':1,'section':'证候'}
    result=_validate_llm_payload({'entities':[{'entity_type':'syndrome','code':'S','name':'气虚血瘀证','chunk_id':'old-parent'}]},[chunk],template,'D','test')
    assert result[0][0]['chunk_id']=='real-child'
    assert result[0][0]['name']=='气虚血瘀证'


def test_spaced_section_numbers_keep_clinical_context_and_not_doses():
    from src.api.routes.pdf import section_chunks
    lines=[{'page':1,'line':i+1,'text':text} for i,text in enumerate([
        '5\u3000临床治疗与推荐意见','5. 1\u3000HFrEF','5. 1. 1\u3000气虚血瘀证','主症：气短。','60 ml，每日一次。','4 片，每日三次。','5. 1. 2\u3000气阴两虚血瘀证','主症：乏力。','参考文献','[1] reference'])]
    chunks=section_chunks('D',lines)
    first=next(c for c in chunks if '5. 1. 1' in c['section'])
    assert 'HFrEF' in first['section']
    assert '60 ml' in first['text'] and '4 片' in first['text']
    assert chunks[-1]['section']=='参考文献'


def test_reference_exclusion_is_reported_separately_from_success():
    from src.review.quality import coverage_report
    result=coverage_report([{'chunk_id':'body'},{'chunk_id':'refs'}],{'body'},{'body','refs'},{'refs'})
    assert result['complete']
    assert result['processed']==1 and result['excluded']==1
    assert result['items'][1]['status']=='excluded'


def test_relationship_references_follow_normalized_entity_codes():
    from src.extraction.service import _validate_llm_payload
    from src.extraction.models import ExtractionTemplate,TemplateEntityType,TemplateEntityEntry,TemplateRelation
    template=ExtractionTemplate(template_id='T',name='T',entity_types=[
        TemplateEntityType(entity_type='syndrome',label='证候',entries=[TemplateEntityEntry(code='S',name='气虚血瘀证')]),
        TemplateEntityType(entity_type='symptom',label='症状',entries=[TemplateEntityEntry(code='A',name='气短')])],
        relations=[TemplateRelation(relation_code='SYNDROME_HAS_SYMPTOM',name='证候有症状',source_entity_type='syndrome',target_entity_type='symptom')])
    chunk={'chunk_id':'C','text':'气虚血瘀证：气短','section':'S','page_start':1,'page_end':1,'line_start':1,'line_end':1}
    payload={'entities':[{'entity_type':'syndrome','code':'RAW_S','name':'气虚血瘀证','chunk_id':'C'},
                         {'entity_type':'symptom','code':'RAW_A','name':'气短','chunk_id':'C'}],
             'suggested_relations':[{'source_type':'syndrome','source_code':'RAW_S','target_type':'symptom','target_code':'RAW_A','relation_code':'SYNDROME_HAS_SYMPTOM','chunk_id':'C'}]}
    result=_validate_llm_payload(payload,[chunk],template,'D','T')
    assert len(result[0])==2
    assert result[2][0]['source_code']=='S' and result[2][0]['target_code']=='A'


def test_relation_field_aliases_are_normalized_with_source_evidence():
    from src.extraction.service import _validate_llm_payload
    from src.extraction.models import ExtractionTemplate,TemplateEntityType,TemplateEntityEntry,TemplateRelation
    template=ExtractionTemplate(template_id='T',name='T',entity_types=[
        TemplateEntityType(entity_type='syndrome',label='证候',entries=[TemplateEntityEntry(code='S',name='气虚血瘀证')]),
        TemplateEntityType(entity_type='symptom',label='症状',entries=[TemplateEntityEntry(code='A',name='气短')])],
        relations=[TemplateRelation(relation_code='SYNDROME_HAS_SYMPTOM',name='证候有症状',source_entity_type='syndrome',target_entity_type='symptom')])
    chunk={'chunk_id':'C','text':'气虚血瘀证：气短','section':'S','page_start':1,'page_end':1,'line_start':1,'line_end':1}
    result=_validate_llm_payload({'entities':[], 'suggested_relations':[{'relation':'SYNDROME_HAS_SYMPTOM','source':'S','source_type':'syndrome','target':'A','target_type':'symptom','evidence':{'chunk_id':'C','quote':'气短'}}]},[chunk],template,'D','T')
    assert result[2][0]['relation_code']=='SYNDROME_HAS_SYMPTOM'
    assert result[2][0]['source_code']=='S'
