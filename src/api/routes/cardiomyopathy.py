"""Specialty workflow APIs; local workflow sandbox, no external order dispatch."""
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4
import json
import os

def json_key(*parts):
    return json.dumps(parts, ensure_ascii=False)

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from src.models.patient import PatientSnapshot
from src.cardiomyopathy.models import ScenarioContract, RiskModel, FollowUpTask, FamilyMember, OrderMapping
from src.cardiomyopathy.service import WorkflowStore, assess_contract, assess_risk, utc

router = APIRouter(prefix='/api/cardiomyopathy', tags=['cardiomyopathy'])
store = WorkflowStore(os.environ.get('CDSS_WORKFLOW_DB') or Path(__file__).resolve().parents[3] / 'data/workflows/cardiomyopathy.sqlite3')


class ContractRequest(BaseModel):
    contract: ScenarioContract
    patient: PatientSnapshot
    event: str


@router.post('/contracts/evaluate')
def evaluate_contract(request: ContractRequest):
    return assess_contract(request.contract, request.patient, request.event)


@router.put('/risk-models')
def save_model(model: RiskModel, actor: str = Query(..., min_length=1, pattern=r".*\S.*")):
    key = json_key(model.model_id, model.version)
    previous = store.get('risk_model', key)
    if previous and previous['status'] == 'active' and previous != model.model_dump(mode='json'):
        raise HTTPException(409, '已发布模型不可覆盖，请创建新版本')
    return store.put('risk_model', key, model.model_dump(mode='json'), actor)


@router.get('/risk-models')
def models():
    return store.list('risk_model')


class RiskRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_id: str
    version: int
    patient: PatientSnapshot


@router.post('/risk-assessments')
def evaluate_risk(request: RiskRequest, actor: str = Query(..., min_length=1, pattern=r".*\S.*")):
    row = store.get('risk_model', json_key(request.model_id, request.version))
    if not row:
        raise HTTPException(404, '模型不存在')
    result = assess_risk(RiskModel(**row), request.patient)
    result.update(assessment_id=uuid4().hex, patient_id=request.patient.patient_id, hospital_id=request.patient.hospital_id)
    return store.put('risk_assessment', result['assessment_id'], result, actor)


@router.put('/follow-ups')
def save_task(task: FollowUpTask, actor: str = Query(..., min_length=1, pattern=r".*\S.*")):
    if task.status == 'completed' and task.completed_at is None:
        raise HTTPException(422, '完成任务须记录完成时间')
    key = json_key(task.hospital_id, task.patient_id, task.task_id)
    return store.put('follow_up', key, task.model_dump(mode='json'), actor)


@router.get('/follow-ups')
def tasks(hospital_id: str, patient_id: str, at: Optional[datetime] = None):
    current = utc(at or datetime.now(timezone.utc))
    result = []
    for row in store.list('follow_up'):
        if row['hospital_id'] == hospital_id and row['patient_id'] == patient_id:
            result.append({**row, 'overdue': row['status'] == 'pending' and utc(datetime.fromisoformat(row['due_at'].replace('Z', '+00:00'))) < current})
    return result


class EventRequest(BaseModel):
    patient: PatientSnapshot
    event: str


@router.post('/follow-ups/reassess')
def reassess(request: EventRequest):
    rows = tasks(request.patient.hospital_id, request.patient.patient_id)
    return [{'task_id': row['task_id'], 'reason': 'event' if request.event in row['reassessment_events'] else 'due',
             'missing_items': [key for key in row['required_attributes'] if request.patient.clinical_attributes.get(key) is None]}
            for row in rows if row['status'] == 'pending' and (row['overdue'] or request.event in row['reassessment_events'])]


@router.put('/family-members')
def save_member(member: FamilyMember, actor: str = Query(..., min_length=1, pattern=r".*\S.*")):
    if not member.consent_recorded:
        raise HTTPException(422, '家系资料录入须记录授权依据')
    return store.put('family_member', json_key(member.hospital_id, member.family_id, member.member_id), member.model_dump(mode='json'), actor)


@router.get('/family-members')
def members(hospital_id: str, family_id: str):
    return [row for row in store.list('family_member') if row['hospital_id'] == hospital_id and row['family_id'] == family_id]


@router.put('/order-mappings')
def save_mapping(mapping: OrderMapping, actor: str = Query(..., min_length=1, pattern=r".*\S.*")):
    return store.put('order_mapping', json_key(mapping.hospital_id, mapping.code), mapping.model_dump(mode='json'), actor)


class RecommendationRequest(BaseModel):
    patient: PatientSnapshot


@router.post('/recommendations')
def recommend(request: RecommendationRequest, actor: str = Query(..., min_length=1, pattern=r".*\S.*")):
    from .inference import _inference_result
    result = _inference_result(request.patient, domain='western')
    cards = []
    from src.rules.repository import JsonRuleRepository
    from src.review.service import ReviewService
    from src.cardiomyopathy.service import expand_order_sets
    root = Path(__file__).resolve().parents[3]
    rules = {rule.rule_id: rule for rule in JsonRuleRepository(str(root / 'data/rules')).list_all()}
    blocked = set()
    for conclusion in result['conclusions']:
        if conclusion.get('rule_type') == 'contraindication':
            blocked.update(conclusion.get('blocked_targets') or [])
    for conclusion in result['treatment_conclusions']:
        if conclusion.get('rule_type') != 'treatment':
            continue
        items, expansion_issues = [], []
        candidates = list(conclusion.get('treatment_items') or [])
        set_ids = conclusion.get('order_set_ids') or []
        if set_ids:
            rule = rules.get(conclusion['rule_id'])
            if not rule or not rule.graph_filename:
                expansion_issues.append('缺少规则绑定图谱，无法展开医嘱组')
            else:
                try:
                    graph = ReviewService()._load_graph(rule.graph_filename)
                    expanded, expansion_issues = expand_order_sets(graph, set_ids)
                    candidates.extend(expanded)
                except (OSError, ValueError, KeyError):
                    expansion_issues.append('医嘱组图谱无法读取')
        seen_codes = set()
        for item in candidates:
            if item.get('code') in seen_codes:
                continue
            seen_codes.add(item.get('code'))
            code = item.get('code')
            mapping = store.get('order_mapping', json_key(request.patient.hospital_id, code))
            reason = ('safety_incomplete' if result['diagnostics'].get('undetermined_conditions') else
                      'contraindicated' if code in blocked or conclusion.get('treatment_plan_id') in blocked else
                      'mapping_missing' if not mapping or mapping['status'] != 'active' else None)
            items.append({**item, 'local_code': mapping['local_code'] if mapping and not reason else None, 'blocked_reason': reason})
        card = {'recommendation_id': uuid4().hex, 'hospital_id': request.patient.hospital_id,
                'patient_id': request.patient.patient_id, 'rule_id': conclusion['rule_id'],
                'items': items, 'order_set_ids': conclusion.get('order_set_ids') or [],
                'evidence': conclusion.get('evidence') or [], 'status': 'pending',
                'materialization_issues': expansion_issues,
                'delivery': 'local_draft_only', 'created_at': datetime.now(timezone.utc).isoformat()}
        cards.append(store.put('recommendation', card['recommendation_id'], card, actor))
    return {'recommendations': cards, 'diagnostics': result['diagnostics'],
            'safety_alerts': [c for c in result['conclusions'] if c.get('rule_type') in {'contraindication', 'monitoring'}]}


class Feedback(BaseModel):
    hospital_id: str
    patient_id: str
    status: Literal['accepted', 'rejected']
    reason: str = ''


@router.post('/recommendations/{recommendation_id}/feedback')
def feedback(recommendation_id: str, request: Feedback, actor: str = Query(..., min_length=1, pattern=r".*\S.*")):
    row = store.get('recommendation', recommendation_id)
    if not row or row['hospital_id'] != request.hospital_id or row['patient_id'] != request.patient_id:
        raise HTTPException(404, '推荐不存在')
    if row['status'] != 'pending':
        raise HTTPException(409, '推荐已处理')
    if request.status == 'accepted' and (not row['items'] or row.get('materialization_issues') or any(i['blocked_reason'] for i in row['items'])):
        raise HTTPException(409, '请先完成医嘱映射及安全核对')
    if request.status == 'accepted':
        created = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
        if (datetime.now(timezone.utc) - utc(created)).total_seconds() > 86400:
            raise HTTPException(409, '推荐已超过24小时，请重新评估')
        for item in row['items']:
            mapping = store.get('order_mapping', json_key(request.hospital_id, item['code']))
            if not mapping or mapping['status'] != 'active' or mapping['local_code'] != item['local_code']:
                raise HTTPException(409, '院内映射已变化，请重新评估')
    row.update(status=request.status, feedback_reason=request.reason, feedback_at=datetime.now(timezone.utc).isoformat())
    return store.put('recommendation', recommendation_id, row, actor)


@router.get('/audit')
def audit():
    return store.audit()


@router.get('/order-mappings')
def mappings(hospital_id: str):
    return [row for row in store.list('order_mapping') if row['hospital_id'] == hospital_id]


@router.get('/recommendations')
def recommendations(hospital_id: str, patient_id: str):
    return [row for row in store.list('recommendation') if row['hospital_id'] == hospital_id and row['patient_id'] == patient_id]


@router.get('/metrics')
def metrics(hospital_id: str):
    rows = [row for row in store.list('recommendation') if row['hospital_id'] == hospital_id]
    accepted = sum(row['status'] == 'accepted' for row in rows)
    rejected = sum(row['status'] == 'rejected' for row in rows)
    return {'total': len(rows), 'accepted': accepted, 'rejected': rejected,
            'pending': len(rows)-accepted-rejected,
            'acceptance_rate_among_reviewed': accepted/(accepted+rejected) if accepted+rejected else None}


@router.put('/patient-snapshots')
def save_snapshot(patient: PatientSnapshot, actor: str = Query(..., min_length=1, pattern=r'.*\S.*')):
    if patient.encounter_time is None:
        raise HTTPException(422, '纵向记录必须提供就诊时间')
    key = json_key(patient.hospital_id, patient.patient_id, utc(patient.encounter_time).isoformat())
    return store.put('snapshot', key, patient.model_dump(mode='json'), actor)


@router.get('/patient-snapshots')
def snapshots(hospital_id: str, patient_id: str, at: Optional[datetime] = None):
    rows = [row for row in store.list('snapshot') if row['hospital_id'] == hospital_id and row['patient_id'] == patient_id]
    rows.sort(key=lambda row: utc(PatientSnapshot(**row).encounter_time))
    if at:
        rows = [row for row in rows if utc(PatientSnapshot(**row).encounter_time) <= utc(at)]
    return {'snapshots': rows, 'latest': rows[-1] if rows else None}


class ScreeningRequest(BaseModel):
    hospital_id: str
    family_id: str
    member_id: str
    task_id: str
    disease_code: str
    title: str
    due_at: datetime
    source: str = Field(min_length=1)


@router.post('/family-screening-tasks')
def screening_task(request: ScreeningRequest, actor: str = Query(..., min_length=1, pattern=r'.*\S.*')):
    member = store.get('family_member', json_key(request.hospital_id, request.family_id, request.member_id))
    if not member or not member.get('patient_id'):
        raise HTTPException(422, '请先关联家属患者编号')
    if not member.get('consent_recorded'):
        raise HTTPException(409, '未记录授权依据')
    # A clinician explicitly requests screening; VUS never automatically triggers it.
    task = FollowUpTask(task_id=request.task_id, hospital_id=request.hospital_id,
        patient_id=member['patient_id'], disease_code=request.disease_code, title=request.title,
        due_at=request.due_at, source=request.source)
    return save_task(task, actor)


class ValidationRequest(BaseModel):
    case_id: str
    graph_filename: str
    basis_id: str
    patient: PatientSnapshot
    expected_rule_ids: list[str] = Field(min_length=1)
    forbidden_rule_ids: list[str] = Field(default_factory=list)


@router.post('/case-validations')
def validate_case(request: ValidationRequest, actor: str = Query(..., min_length=1, pattern=r'.*\S.*')):
    from .inference import _inference_result
    from .kg import normalize_graph_filename
    from src.review.service import ReviewService
    from src.review.clinical_basis import digest
    filename = normalize_graph_filename(request.graph_filename)
    review = ReviewService()
    graph = review._load_graph(filename)
    basis = next((b for b in graph.get('clinical_bases', []) if b['basis_id'] == request.basis_id), None)
    if not basis or not basis.get('generated_rule_id'):
        raise HTTPException(422, '请先生成场景规则，再登记病例验证')
    rule = next((r for r in review._load_rules() if r['rule_id'] == basis['generated_rule_id'] and r.get('graph_filename') == filename), None)
    if not rule or rule['rule_id'] not in request.expected_rule_ids:
        raise HTTPException(422, '预期命中规则必须包括当前依据生成的规则')
    result = _inference_result(request.patient, domain=rule.get('domain', 'western'), test_mode=True)
    matched = {c['rule_id'] for c in result['conclusions']}
    passed = set(request.expected_rule_ids).issubset(matched) and not set(request.forbidden_rule_ids).intersection(matched)
    row = {'validation_id': uuid4().hex, 'case_id': request.case_id, 'graph_filename': filename,
        'basis_id': request.basis_id, 'basis_fingerprint': digest(basis['specification']),
        'rule_fingerprint': digest(rule), 'passed': passed, 'matched_rule_ids': sorted(matched),
        'expected_rule_ids': request.expected_rule_ids, 'forbidden_rule_ids': request.forbidden_rule_ids,
        'validated_at': datetime.now(timezone.utc).isoformat(), 'test_mode': True}
    return store.put('case_validation', row['validation_id'], row, actor)
