"""Deterministic workflow helpers; recommendations never place orders."""
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .models import RiskModel, ScenarioContract


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def assess_contract(contract: ScenarioContract, patient, event):
    missing, exclusions = [], []
    attributes = patient.clinical_attributes
    if contract.age_min is not None or contract.age_max is not None:
        if patient.age is None:
            missing.append('age')
        elif ((contract.age_min is not None and patient.age < contract.age_min)
              or (contract.age_max is not None and patient.age > contract.age_max)):
            exclusions.append('age')
    missing.extend(code for code in contract.required_attributes if attributes.get(code) is None)
    exclusions.extend(code for code, value in contract.excluded_attributes.items() if str(attributes.get(code)) == value)
    state = 'not_applicable' if event not in contract.trigger_events or exclusions else 'unknown' if missing else 'ready'
    return {'state': state, 'missing_items': missing, 'exclusions': exclusions,
            'actions': contract.actions if state == 'ready' else [],
            'reassessment_events': contract.reassessment_events, 'contract_version': contract.version}


def assess_risk(model: RiskModel, patient):
    base = {'model_id': model.model_id, 'version': model.version, 'source': model.source}
    if model.status != 'active':
        return {**base, 'status': 'unavailable', 'reason': '模型未审核启用'}
    missing = []
    if patient.age is None:
        missing.append('age')
    elif patient.age < model.age_min or (model.age_max is not None and patient.age > model.age_max):
        return {**base, 'status': 'not_applicable', 'reason': '年龄不适用'}
    for key, value in model.required_population.items():
        actual = patient.clinical_attributes.get(key)
        if actual is None:
            missing.append(key)
        elif str(actual) != value:
            return {**base, 'status': 'not_applicable', 'reason': '模型人群不适用'}
    values = {}
    for term in model.terms:
        value = patient.clinical_attributes.get(term.attribute)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            missing.append(term.attribute)
        else:
            values[term.attribute] = value
    if missing:
        return {**base, 'status': 'unknown', 'missing_items': sorted(set(missing))}
    score = model.intercept + sum(values[t.attribute] * t.coefficient for t in model.terms)
    if not math.isfinite(score):
        return {**base, 'status': 'unknown', 'reason': '计算超出数值范围'}
    band = next((b.label for b in sorted(model.bands, key=lambda b: b.minimum, reverse=True) if score >= b.minimum), None)
    return {**base, 'status': 'assessed', 'score': score, 'band': band, 'inputs': values,
            'reviewer': model.reviewer, 'assessed_at': datetime.now(timezone.utc).isoformat()}


class WorkflowStore:
    """Local durable store with atomic state and audit writes."""
    def __init__(self, path):
        self.path = Path(path)
        self.lock = RLock()

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path))
        connection.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, payload TEXT, PRIMARY KEY(kind,id))')
        connection.execute('CREATE TABLE IF NOT EXISTS audit (id TEXT PRIMARY KEY, kind TEXT, record_id TEXT, actor TEXT, at TEXT, payload TEXT)')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def put(self, kind, key, payload, actor):
        if not actor.strip():
            raise ValueError('必须记录操作人')
        with self.lock, self.connect() as db:
            db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (kind, key, json.dumps(payload, ensure_ascii=False)))
            db.execute('INSERT INTO audit VALUES (?,?,?,?,?,?)', (uuid4().hex, kind, key, actor,
                datetime.now(timezone.utc).isoformat(), json.dumps(payload, ensure_ascii=False)))
        return payload

    def get(self, kind, key):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, kind):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM records WHERE kind=? ORDER BY id', (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def audit(self):
        with self.connect() as db:
            rows = db.execute('SELECT kind,record_id,actor,at FROM audit ORDER BY rowid DESC LIMIT 500').fetchall()
        return [dict(zip(('kind', 'record_id', 'actor', 'at'), row)) for row in rows]


def expand_order_sets(graph, codes):
    """Expand only approved set/item edges; unresolved sets block acceptance."""
    nodes = graph.get('nodes', {})
    items, issues = [], []
    for code in codes:
        sets = [(key, node) for key, node in nodes.items() if node.get('node_type') == 'order_set' and node.get('code') == code]
        if len(sets) != 1 or sets[0][1].get('review_status') != 'active':
            issues.append(f'{code}:医嘱组缺失或未通过审核')
            continue
        set_id, _ = sets[0]
        edges = [edge for edge in graph.get('relations', {}).values()
                 if edge.get('source_node_id') == set_id and edge.get('relation_code') == 'ORDER_SET_INCLUDES_ITEM']
        if not edges:
            issues.append(f'{code}:医嘱组尚无条目')
        for edge in edges:
            target = nodes.get(edge.get('target_node_id'), {})
            if edge.get('review_status') != 'active' or target.get('review_status') != 'active' or target.get('node_type') != 'order_item' or not target.get('code'):
                issues.append(f'{code}:条目或关系未通过审核')
                continue
            item = {**(target.get('attributes') or {}), 'code': target['code'], 'name': target.get('name', target['code']),
                    'order_set_id': code, 'source_refs': edge.get('source_refs') or []}
            if not any(existing['code'] == item['code'] for existing in items):
                items.append(item)
    return items, issues
