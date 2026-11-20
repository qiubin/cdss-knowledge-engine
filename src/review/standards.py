"""Stage source-bound guideline interpretations for the unified review center."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

from fastapi import HTTPException
from src.models.standard import LabInterpretationStandard
from src.standards.validation import standard_issues, standard_identity
from src.review.audit import append_audit


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]


def stage_standards(graph, rules, candidates, chunks, doc_id, title, filename):
    """Idempotent drafts. New inline interpretations cannot bypass review."""
    indexed = {c['chunk_id']: c for c in chunks}
    graph.setdefault('standard_source_chunks', {}).update(indexed)
    rows = graph.setdefault('standard_candidates', [])
    existing = {r['candidate_id']: r for r in rows}
    pending = [(deepcopy(c), None) for c in candidates if isinstance(c, dict)]
    def walk(node, rule):
        if not isinstance(node, dict): return
        inline = (node.get('parameter') or {}).get('interpretation')
        if node.get('type') in {'exam_finding', 'lab_concept'} and isinstance(inline, dict) and inline.get('interpretation_type') == 'numeric':
            pending.append(({**inline, 'item_type': 'exam' if node['type'] == 'exam_finding' else 'lab',
                **{k: node.get(k, '') for k in ['exam_code', 'finding_code', 'lab_code']},
                'source': rule.get('source') or {}, 'clinical_context': rule.get('clinical_context') or {}}, (node, rule)))
        for key in ['children', 'required_children']:
            for child in node.get(key) or []: walk(child, rule)
    for rule in rules:
        walk(rule.get('conditions'), rule)
        for action in ((rule.get('clinical_context') or {}).get('western_recommendation') or {}).get('actions', []):
            if action.get('conditions'): walk(action['conditions'], rule)
            for condition in action.get('contraindications') or []: walk(condition, rule)
    for raw, binding in pending:
        source = raw.get('source') or {k: raw.get(k) for k in ['chunk_id', 'quote', 'page_start', 'page_end', 'line_start', 'line_end']}
        source = {**source, 'guideline_id': doc_id, 'guideline_title': title}
        key = digest({'source': source, 'clinical_context': raw.get('clinical_context') or {},
            'standard': {k: None if raw.get(k) == '' else raw.get(k) for k in ['item_type', 'exam_code', 'finding_code', 'lab_code',
                'operator', 'threshold_value', 'threshold_upper', 'threshold_unit', 'context_age_min', 'context_age_max', 'context_gender', 'method']}})
        cid = 'STD-' + key
        standard = {**raw, 'hospital_id': '', 'status': 'draft', 'interpretation_type': 'numeric', 'ref_source': 'STATIC',
            'concept_code': 'GUIDE_' + key.upper(), 'source_note': title + ' · ' + str(source.get('chunk_id') or '')}
        standard.pop('source', None)
        candidate = existing.get(cid)
        if candidate is None:
            candidate = {'candidate_id': cid, 'status': 'draft', 'version': 1, 'standard': standard,
                'source': source, 'clinical_context': raw.get('clinical_context') or {}, 'graph_filename': filename,
                'domain': graph.get('domain') or 'western', 'rule_ids': []}
            rows.append(candidate); existing[cid] = candidate
        if binding:
            node, rule = binding
            node['concept_code'] = candidate['standard']['concept_code']
            node['standard_required'] = True
            node['parameter'] = {k: v for k, v in (node.get('parameter') or {}).items() if k != 'interpretation'}
            node['parameter']['standard_candidate_id'] = cid
            if rule.get('rule_id') not in candidate['rule_ids']: candidate['rule_ids'].append(rule.get('rule_id'))
        candidate['source_issues'] = source_issues(candidate, indexed)
    return rows


def source_issues(candidate, chunks):
    source = candidate.get('source') or {}
    chunk = chunks.get(source.get('chunk_id'))
    normalize = lambda text: re.sub(r'\s+', '', str(text or ''))
    quote = normalize(source.get('quote'))
    issues = []
    if not chunk or not quote or quote not in normalize(chunk.get('text') or chunk.get('quote')):
        issues.append('引文缺失或与所标原文片段不一致')
    if not source.get('line_start') or not source.get('line_end') or not (source.get('page_start') or source.get('page')):
        issues.append('缺少原文页码或行号')
    return issues


def list_standard_items(service):
    items = []
    for path in sorted(service.graph_dir.glob('*.json')):
        graph = service._load_graph(path.name)
        for c in graph.get('standard_candidates', []):
            s = c['standard']
            items.append({**c, 'configuration_issues': standard_issues({**s, 'hospital_id': 'PENDING'}), 'item_type': 'standard', 'item_id': c['candidate_id'], 'origin': 'guideline',
                'name': s.get('finding_name') or s.get('lab_name') or s.get('finding_code') or s.get('lab_code'),
                'graph_filename': path.name})
    directory = service.graph_dir.parent / 'standards'
    for path in sorted(directory.glob('*.json')):
        for s in json.loads(path.read_text(encoding='utf-8')):
            if s.get('standard_candidate_id'): continue
            sid = 'HSTD-' + digest([s['hospital_id'], standard_identity(s)])
            items.append({'item_type': 'standard', 'item_id': sid, 'origin': 'hospital', 'standard': s,
                'version': s.get('version', 1), 'status': s.get('status', 'draft'), 'domain': 'western',
                'source': s.get('source') or {}, 'name': s.get('finding_name') or s.get('lab_name') or s.get('finding_code') or s.get('lab_code'),
                'graph_filename': s.get('graph_filename', ''), 'candidate_id': sid})
    return items


def review_standard(service, item_id, payload):
    from src.vocab.repository import ConceptVocabularyRepository
    from src.kg.schema import ConceptDefinition
    item = next((i for i in list_standard_items(service) if i['item_id'] == item_id), None)
    if item is None: raise HTTPException(404, '判定标准未找到')
    if payload.get('version') != item['version']: raise HTTPException(409, '标准已更新，请刷新后重新审核')
    action = payload.get('action')
    if action not in {'save', 'approve', 'reject', 'return'}: raise HTTPException(422, '不支持的审核动作')
    reviewer = str(payload.get('reviewer') or '').strip()
    if not reviewer: raise HTTPException(422, '请填写审核人')
    if action == 'reject' and not str(payload.get('note') or '').strip(): raise HTTPException(422, '驳回须填写原因')
    now = datetime.now(timezone.utc).isoformat()
    before = item['status']
    row = deepcopy(item['standard'])
    if action == 'save':
        allowed = {'operator', 'threshold_value', 'threshold_upper', 'threshold_unit', 'context_age_min', 'context_age_max',
            'context_gender', 'method', 'source_note', 'exam_name', 'finding_name', 'lab_name'}
        row.update({k: v for k, v in (payload.get('standard') or {}).items() if k in allowed})
        try:
            original_hospital = row.get('hospital_id', '')
            row = {**row, **LabInterpretationStandard(**{**row, 'hospital_id': original_hospital or 'PENDING'}).model_dump(),
                'hospital_id': original_hospital}
        except ValueError as error:
            raise HTTPException(422, str(error))
    status = {'save': 'draft', 'approve': 'active', 'reject': 'rejected', 'return': 'draft'}[action]
    row.update(status=status, reviewed_by=reviewer, reviewed_at=now, review_note=payload.get('note', ''), version=item['version'] + 1)
    if item['origin'] == 'guideline':
        graph = service._load_graph(item['graph_filename'])
        candidate = next(c for c in graph['standard_candidates'] if c['candidate_id'] == item_id)
        chunks = {c.get('chunk_id'): c for c in graph.get('sources', [])}
        chunks.update(graph.get('standard_source_chunks') or {})
        issues = source_issues(candidate, chunks)
        if action == 'approve':
            if issues: raise HTTPException(422, '；'.join(issues))
            if not payload.get('scope_confirmed'): raise HTTPException(422, '须核对人群、例外与关联规则的适用条件')
        hospitals = payload.get('hospital_ids') or []
        if action == 'approve' and not hospitals: raise HTTPException(422, '请选择发布医院')
    else:
        hospitals = [row['hospital_id']]
    if action == 'approve':
        changed = payload.get('standard') or {}
        if any(changed.get(k) != row.get(k) and not (changed.get(k) in ('', None) and row.get(k) in ('', None)) for k in changed):
            raise HTTPException(409, '参数有未保存修改，请先保存草稿再审核')
        issues = standard_issues({**row, 'hospital_id': 'PENDING'})
        if issues: raise HTTPException(422, '；'.join(issues))
    # Validate every target before any write; do not overwrite unrelated local standards.
    targets = []
    directory = service.graph_dir.parent / 'standards'
    for hospital in hospitals:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', hospital): raise HTTPException(422, '医院ID格式错误')
        path = directory / (hospital.lower() + '.json')
        # Preserve existing filenames regardless of case.
        path = next((p for p in directory.glob('*.json') if p.stem.lower() == hospital.lower()), path)
        existing = json.loads(path.read_text()) if path.exists() else []
        published = {**row, 'hospital_id': hospital}
        if item['origin'] == 'guideline':
            published.update(source=item['source'], graph_filename=item['graph_filename'], standard_candidate_id=item_id)
        matches = [i for i, s in enumerate(existing) if standard_identity(s) == standard_identity(published)]
        if item['origin'] == 'hospital':
            original_matches = [i for i, s in enumerate(existing) if standard_identity(s) == standard_identity(item['standard'])
                and s.get('version', 1) == item['version']]
            if len(original_matches) != 1: raise HTTPException(409, '标准存在重复或已更新，请先核对配置')
            selected_index = original_matches[0]
            if action == 'approve':
                for index in matches:
                    if index != selected_index: existing[index] = {**existing[index], 'status': 'retired'}
            existing[selected_index] = published
        else:
            for index in matches: existing[index] = {**existing[index], 'status': 'retired'}
            existing.append(published)
        targets.append((path, existing))
    if item['origin'] == 'guideline':
        if action == 'approve':
            vocab = ConceptVocabularyRepository(directory.parent / 'vocab' / 'concepts.json')
            if not vocab.find(row['concept_code']):
                vocab.save(ConceptDefinition(code=row['concept_code'], name_cn=(item['name'] or item_id) + '·指南数值判定',
                    category='exam_concept' if row['item_type'] == 'exam' else 'lab_concept', evaluator_type='static_threshold'), is_new=True)
        # Retire previously published copies whenever edited, rejected or returned.
        if action != 'approve':
            for path in directory.glob('*.json'):
                existing = json.loads(path.read_text())
                if any(s.get('standard_candidate_id') == item_id and s.get('status') == 'active' for s in existing):
                    for s in existing:
                        if s.get('standard_candidate_id') == item_id and s.get('status') == 'active': s['status'] = 'retired'
                    targets.append((path, existing))
        candidate.update(standard=row, status=status, version=row['version'], source_issues=issues,
            reviewed_by=reviewer, reviewed_at=now, review_note=payload.get('note', ''))
        service._save_graph(graph, item['graph_filename'])
    for path, content in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding='utf-8')
    append_audit(dict(entity_type='standard', entity_id=item_id, action=action, reviewer=reviewer,
        note=payload.get('note', ''), hospital_ids=hospitals, before_status=before, after_status=status,
        graph_filename=item.get('graph_filename', ''), before=item['standard'], after=row), service.audit_path)
    return {'item_id': item_id, 'status': status, 'version': row['version']}
