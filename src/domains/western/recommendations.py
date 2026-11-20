"""Patient-specific Western recommendation contract and conservative assessment."""
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class RequiredDatum(BaseModel):
    path: str  # Explicit patient field, e.g. clinical_attributes.egfr
    label: str
    purpose: Literal['selection', 'dose'] = 'selection'
    expected_value: Optional[Any] = None


class RecommendationAction(BaseModel):
    code: str
    name: str
    kind: Literal['medication', 'intervention', 'exam', 'lab', 'differential', 'monitoring', 'referral', 'care']
    verb: Literal['start', 'continue', 'adjust', 'switch', 'add', 'stop', 'avoid', 'assess', 'order', 'monitor']
    required_data: List[RequiredDatum] = Field(default_factory=list)
    conditions: Optional[Dict[str, Any]] = None
    contraindications: List[Dict[str, Any]] = Field(default_factory=list)
    regimen: Dict[str, str] = Field(default_factory=dict)  # quoted dose/route/frequency/course/titration; no invented defaults
    monitoring: List[str] = Field(default_factory=list)
    source: Dict[str, Any] = Field(default_factory=dict)


class WesternRecommendation(BaseModel):
    schema_version: Literal[1] = 1
    diagnosis_stage: Literal['suspected', 'confirmed', 'independent']
    clinical_question: str
    setting: str = ''
    combination: Literal['single', 'alternatives', 'combined', 'sequential']
    selection_group: str = ''
    scope_confirmed: bool = False
    actions: List[RecommendationAction] = Field(min_length=1)


def get_recommendation(rule):
    context = rule.clinical_context if hasattr(rule, 'clinical_context') else rule.get('clinical_context', {})
    value = (context or {}).get('western_recommendation')
    return value if isinstance(value, dict) or value is None else {'invalid_contract': value}


def recommendation_issues(spec):
    raw = get_recommendation(spec)
    if not raw: return []
    issues = []
    try:
        rec = WesternRecommendation(**raw)
    except (TypeError, ValueError) as error:
        return ['西医诊疗推荐结构不完整：' + str(error)]
    if spec.get('domain', 'western') != 'western' or spec.get('rule_type') != 'treatment':
        issues.append('西医诊疗推荐应使用西医治疗规则承载')
    if rec.diagnosis_stage == 'independent' and (spec.get('conditions') or {}).get('type') == 'group' and not (spec.get('conditions') or {}).get('children'):
        issues.append('独立处置须有明确触发条件，不能无条件推荐')
    if rec.combination == 'sequential' and any(not a.conditions for a in rec.actions):
        issues.append('序贯方案各动作须明确当前阶段或前序治疗条件')
    if not rec.scope_confirmed: issues.append('请核对诊断阶段、适用范围及方案组合后确认')
    if not rec.clinical_question.strip(): issues.append('请填写当前临床问题')
    if rec.combination == 'single' and len(rec.actions) != 1: issues.append('单项建议只能包含一个动作；多项请选择组合关系')
    if rec.combination == 'alternatives' and not rec.selection_group.strip(): issues.append('替代方案须填写选择组，避免被解释为联合治疗')
    from src.models.rule import RuleCondition
    def validate_tree(node):
        try: RuleCondition(**node)
        except ValueError as error: issues.append('推荐条件结构错误：' + str(error))
        if node.get('type') == 'group':
            children = node.get('children') or []
            if not children or node.get('operator') not in {'AND', 'OR', 'NOT'} or (node.get('operator') == 'NOT' and len(children) != 1):
                issues.append('推荐条件组须明确且/或/非关系并包含有效条件')
            for child in children: validate_tree(child)
    for action in rec.actions:
        if not action.code.strip() or not action.name.strip(): issues.append('推荐项目编码和名称必填')
        source = action.source or spec.get('source') or {}
        if not source.get('quote') or not source.get('chunk_id'): issues.append('每个推荐动作须绑定原文')
        if action.conditions: validate_tree(action.conditions)
        for condition in action.contraindications: validate_tree(condition)
        for datum in action.required_data:
            if not datum.path.startswith(('clinical_attributes.', 'age', 'gender', 'current_medications', 'allergies')):
                issues.append('必要资料路径须指向结构化患者信息')
    return list(dict.fromkeys(issues))


def patient_value(patient, path):
    value = patient.model_dump()
    for key in path.split('.'):
        if not isinstance(value, dict) or key not in value: return None
        value = value[key]
    return value


def evaluate_condition(engine, node, patient):
    """Absent clinical documentation is unknown, never a negative history."""
    if node.get('type') == 'group':
        results = [evaluate_condition(engine, c, patient) for c in node.get('children', [])]
        op = node.get('operator')
        if not results: return True if op == 'AND' else None
        if op == 'NOT': return None if len(results) != 1 or results[0] is None else not results[0]
        if op == 'AND': return False if False in results else None if None in results else True
        if op == 'OR': return True if True in results else None if None in results else False
        return None
    kind = node.get('type')
    if kind == 'lab_concept' and not any(l.lab_code == node.get('lab_code') and (l.value is not None or l.qualitative is not None) for l in patient.labs): return None
    if kind == 'exam_finding' and not any(f.finding_code == node.get('finding_code') and (not node.get('exam_code') or f.exam_code == node.get('exam_code')) for f in patient.exam_findings): return None
    if kind == 'demographic' and node.get('gender') and patient.gender.value == 'U': return None
    if kind == 'evidence':
        evidence = engine._current_kg.get_evidence(node.get('evidence_id') or '')
        if evidence is None: return None
        if evidence.data_type == 'lab' and not any(l.lab_code == evidence.lab_code and (l.value is not None or l.qualitative is not None) for l in patient.labs): return None
        if evidence.data_type == 'exam_finding' and not any(f.finding_code == evidence.finding_code and (not evidence.exam_code or f.exam_code == evidence.exam_code) for f in patient.exam_findings): return None
        if evidence.data_type == 'symptom' and not any(s.symptom_code == evidence.symptom_code for s in patient.symptoms): return None
    if kind == 'attribute' and patient.clinical_attributes.get(node.get('attribute_code')) is None: return None
    if kind == 'symptom' and not any(s.symptom_code == node.get('symptom_code') for s in patient.symptoms): return None
    if kind == 'vital' and not any(v.vital_code == node.get('vital_code') for v in patient.vitals): return None
    if kind == 'demographic' and ((node.get('age_min') is not None or node.get('age_max') is not None) and patient.age is None): return None
    if kind in {'medication', 'past_history'}:
        values = patient.current_medications if kind == 'medication' else patient.past_history
        reviewed = 'medications_reviewed' if kind == 'medication' else 'history_reviewed'
        if node.get('code') not in values and patient.clinical_attributes.get(reviewed) is not True: return None
    from src.models.rule import RuleCondition
    return engine._evaluate_condition(RuleCondition(**node), patient)[0]


def assess(engine, rule, patient, diagnoses):
    raw = get_recommendation(rule)
    rec = WesternRecommendation(**raw)
    base = dict(rule_id=rule.rule_id, disease_code=rule.disease_code, name=rule.name,
                clinical_question=rec.clinical_question, combination=rec.combination,
                selection_group=rec.selection_group, source=rule.source.model_dump(), actions=[])
    issues = recommendation_issues(rule.model_dump())
    if issues: return {**base, 'status':'needs_review', 'reasons':issues}
    applicable = [d for d in diagnoses if d.rule_type == 'diagnosis' and d.disease_code == rule.disease_code
                  and (d.clinical_intent == 'diagnosis_confirmation' or
                       (rec.diagnosis_stage == 'suspected' and d.clinical_intent == 'suspected_diagnosis'))]
    documented = any(d.code == rule.disease_code and d.clinical_status == 'active' and
        (d.certainty == 'confirmed' or (rec.diagnosis_stage == 'suspected' and d.certainty == 'suspected')) for d in patient.diagnoses)
    if rec.diagnosis_stage != 'independent' and not applicable and not documented:
        return {**base, 'status':'pending_diagnosis', 'reasons':['需满足' + ('确诊' if rec.diagnosis_stage == 'confirmed' else '疑似或确诊') + '前提']}
    condition = evaluate_condition(engine, rule.conditions.model_dump(), patient)
    for action in rec.actions:
        row = action.model_dump(); reasons = []; state = 'ready'
        matched = evaluate_condition(engine, action.conditions, patient) if action.conditions else True
        contraindications = [evaluate_condition(engine, c, patient) for c in action.contraindications]
        missing = [d for d in action.required_data if patient_value(patient, d.path) is None
                   or (d.expected_value is not None and patient_value(patient, d.path) != d.expected_value)]
        if condition is False or matched is False: state='not_applicable'; reasons.append('适用条件不满足')
        elif action.kind == 'medication' and action.verb not in {'avoid','stop','assess'} and action.code in patient.allergies:
            state='contraindicated'; reasons.append('患者已记录对该药品过敏')
        elif True in contraindications: state='contraindicated'; reasons.append('命中明确禁忌／不适用条件')
        elif condition is None or matched is None or None in contraindications or any(d.purpose == 'selection' for d in missing):
            state='needs_information'; reasons.append('必要条件或安全资料尚不明确')
        elif action.kind == 'medication' and action.verb in {'start','add'} and action.code in patient.current_medications:
            state='needs_review'; reasons.append('患者已在使用该药，请评估继续或调整，避免重复开始')
        elif action.kind == 'medication' and action.verb not in {'stop','avoid','assess'} and (not action.regimen.get('dose') or not action.regimen.get('frequency') or not action.regimen.get('route') or any(d.purpose == 'dose' for d in missing)):
            state='strategy_only'; reasons.append('仅提供治疗策略；用法依据或个体化剂量资料不足')
        row.update(status=state, reasons=reasons, missing_data=[d.model_dump() for d in missing],
            applicability={'shared_conditions':rule.conditions.model_dump(), 'shared_result':condition,
                           'action_conditions':action.conditions, 'action_result':matched,
                           'contraindications':action.contraindications, 'contraindication_results':contraindications})
        if state not in {'ready'}: row['regimen'] = {}  # no concrete dose recommendation from incomplete eligibility
        base['actions'].append(row)
    states = [a['status'] for a in base['actions']]
    base['status'] = 'ready' if all(s == 'ready' for s in states) else 'partial' if any(s in {'ready','strategy_only'} for s in states) else states[0]
    if rec.combination == 'combined' and not all(s == 'ready' for s in states):
        base['status']='needs_information' if any(s in {'needs_information','strategy_only'} for s in states) else 'not_applicable'
        base['reasons']=['组合／序贯方案尚未整体满足；不拆成可同时实施的独立建议']
        for row in base['actions']:
            if row['status'] == 'ready': row.update(status='conditional', regimen={})
    if rec.combination == 'sequential' and sum(s == 'ready' for s in states) > 1:
        base.update(status='needs_review', reasons=['多个序贯阶段同时满足，请核对当前治疗阶段'])
        for row in base['actions']:
            if row['status'] == 'ready': row.update(status='conditional', regimen={})
    return base


def resolve_conflicts(assessments):
    """Keep explicit avoidance and incompatible dose suggestions visible for clinician review."""
    avoided = {a['code'] for r in assessments for a in r.get('actions', [])
               if a['verb'] == 'avoid' and a['status'] == 'ready'}
    candidates = {}
    for result in assessments:
        for action in result.get('actions', []):
            if action['kind'] == 'medication' and action['verb'] not in {'avoid', 'stop'} and action['status'] in {'ready', 'strategy_only'}:
                if action['code'] in avoided:
                    action.update(status='contraindicated', regimen={}, reasons=['其他已命中依据明确要求避免该项目'])
                    result['status'] = 'needs_review'
                elif action['status'] == 'ready':
                    candidates.setdefault(action['code'], []).append((result, action))
    for rows in candidates.values():
        doses = {str(sorted(action['regimen'].items())) for _, action in rows}
        groups = {r.get('selection_group') for r, _ in rows}
        declared_alternatives = len(groups) == 1 and all(r.get('combination') == 'alternatives' and r.get('selection_group') for r, _ in rows)
        if len(doses) > 1 and not declared_alternatives:
            for result, action in rows:
                action.update(status='needs_review', regimen={}, reasons=['同一项目存在不同用法建议，需核对适用场景后选择'])
                result['status'] = 'needs_review'
