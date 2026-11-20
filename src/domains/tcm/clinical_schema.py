"""V3 contextual criteria and treatment contracts; no inferred clinical defaults."""
from __future__ import annotations
import copy
import hashlib
import json
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator
from src.models.rule import Rule, RuleCondition, RuleSource
from src.extraction.graph_binding import resolve_template_graph_filename

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Criterion(StrictModel):
    criterion_id: str
    condition: RuleCondition
    role: Literal['required', 'supporting', 'exclusion', 'differential', 'unspecified']
    manifestation_role: Literal['main', 'secondary', 'tongue', 'pulse', 'sign', 'unspecified'] = 'unspecified'
    source: RuleSource

class HerbComponent(StrictModel):
    code: str
    name: str
    dose_min: Optional[float] = Field(default=None, gt=0)
    dose_max: Optional[float] = Field(default=None, gt=0)
    unit: Optional[str] = None
    dose_basis: Literal['per_dose', 'per_day', 'per_kg', 'unspecified'] = 'unspecified'
    role: Optional[str] = None
    processing: Optional[str] = None
    preparation: Optional[str] = None
    source: RuleSource

    @model_validator(mode='after')
    def dose_range(self):
        if (self.dose_min is not None or self.dose_max is not None) and not self.unit:
            raise ValueError('有剂量必须有单位')
        if self.dose_min is not None and self.dose_max is not None and self.dose_min > self.dose_max:
            raise ValueError('剂量范围倒置')
        return self

class CriteriaCombination(StrictModel):
    criterion_id: Optional[str] = None
    type: Literal['group', 'count_group', 'weighted_group'] = 'group'
    operator: Optional[Literal['AND', 'OR', 'NOT']] = None
    children: list['CriteriaCombination'] = Field(default_factory=list)
    min_count: Optional[int] = Field(default=None, ge=1, strict=True)
    scoring: Optional[Literal['normalized_weight_sum']] = None
    threshold: Optional[float] = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    weight: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode='after')
    def shape(self):
        if self.criterion_id:
            if self.children or self.operator or self.min_count or self.threshold is not None:
                raise ValueError('判据引用不能同时包含分组')
            return self
        if not self.children:
            raise ValueError('分组缺少判据')
        if self.type == 'group' and (self.operator is None or self.operator == 'NOT' and len(self.children) != 1):
            raise ValueError('请明确分组逻辑')
        if self.type == 'count_group' and (self.min_count is None or self.min_count > len(self.children)):
            raise ValueError('满足数量超出判据数')
        if self.type == 'weighted_group' and (self.scoring is None or self.threshold is None or
                                              any(child.weight is None for child in self.children)):
            raise ValueError('原文未明确完整评分标准')
        return self


class PrescriptionModification(StrictModel):
    condition: RuleCondition
    action: Literal['add', 'remove', 'replace', 'adjust_dose']
    target_code: str
    component: Optional[HerbComponent] = None
    source: RuleSource

    @model_validator(mode='after')
    def payload(self):
        if self.action != 'remove' and self.component is None:
            raise ValueError('加药、换药或调量必须有目标组成')
        if self.action == 'adjust_dose' and self.component.code != self.target_code:
            raise ValueError('调量不能改变药物编码')
        return self


class ClinicalContract(StrictModel):
    rule_type: Literal['tcm_element', 'tcm_pattern', 'tcm_treatment', 'tcm_prescription', 'tcm_contraindication']
    target_type: Literal['syndrome', 'syndrome_element'] = 'syndrome'
    target_code: str
    name: str
    scope: RuleCondition
    criteria: list[Criterion] = Field(min_length=1)
    # A leaf is {criterion_id: ...}; groups carry operator/children and optional
    # min_count / scoring threshold. References prevent invented leaf criteria.
    combination: CriteriaCombination
    logic_origin: Literal['guideline_explicit', 'ai_suggested', 'human_defined']
    logic_source: RuleSource
    conclusion: dict[str, Any] = Field(default_factory=dict)
    herb_components: list[HerbComponent] = Field(default_factory=list)
    modifications: list[PrescriptionModification] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


def validate_tree(node: dict, path='conditions') -> list[str]:
    """Shared approval and execution contract, including count/unknown semantics."""
    issues = []
    kind = node.get('type')
    children = node.get('children') or []
    if kind in {'group', 'count_group', 'weighted_group'}:
        if not children:
            issues.append(f'{path}: 条件组为空')
        if kind == 'group' and (node.get('operator') not in {'AND', 'OR', 'NOT'} or
                                node.get('operator') == 'NOT' and len(children) != 1):
            issues.append(f'{path}: 必须明确且／或／非组合')
        if kind == 'count_group':
            count = node.get('min_count')
            if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= len(children):
                issues.append(f'{path}: 满足数量无效')
        if kind == 'weighted_group':
            if node.get('threshold') is None or node.get('scoring') != 'normalized_weight_sum':
                issues.append(f'{path}: 必须明确评分方法及阈值')
            for child in children:
                if child.get('weight') is None or child['weight'] < 0:
                    issues.append(f'{path}: 必须明确非负权重')
        for field in ('children', 'required_children'):
            for i, child in enumerate(node.get(field) or []):
                issues.extend(validate_tree(child, f'{path}.{field}[{i}]'))
    elif kind == 'observation':
        if not node.get('code') or node.get('concept_code') not in {'PRESENT', 'ABSENT'}:
            issues.append(f'{path}: 观察条件必须明确编码及阳性／阴性')
    elif kind == 'fact':
        if not node.get('code') or not node.get('fact_type'):
            issues.append(f'{path}: 缺少前置事实')
    elif kind not in {'past_history', 'attribute', 'demographic', 'medication', 'lab_concept', 'exam_finding', 'vital', 'symptom'}:
        issues.append(f'{path}: 不支持的条件类型 {kind}')
    return issues


def _generate_v3_rules(entities, chunks, template, doc_id, doc_title='', relations=None):
    chunk_map = {item['chunk_id']: item for item in chunks}
    rules = []
    for entity in entities:
        if entity.get('entity_type') != 'clinical_rule':
            continue
        data = (entity.get('attributes') or {}).get('CLINICAL_RULE')
        if not isinstance(data, dict):
            continue
        contract = ClinicalContract(**data)
        def check_source(source):
            part = chunk_map.get(source.chunk_id)
            if (not part or source.guideline_id != doc_id or not source.quote.strip()
                    or not source.page_start or not source.line_start or not source.line_end
                    or ''.join(source.quote.split()) not in ''.join(part.get('text', '').split())):
                raise ValueError('V3判据缺少可核对的原文及定位')
            if (source.page_end or source.page_start) < source.page_start or (source.page_end in (None, source.page_start) and source.line_end < source.line_start):
                raise ValueError('原文定位范围倒置')
            if part.get('page_start') and not part['page_start'] <= source.page_start <= part.get('page_end', part['page_start']):
                raise ValueError('原文页码不属于所引片段')
        check_source(contract.logic_source)
        criteria = {}
        signatures = set()
        for criterion in contract.criteria:
            check_source(criterion.source)
            if criterion.criterion_id in criteria:
                raise ValueError('判据编号重复')
            leaf = criterion.condition.model_dump(exclude_none=True)
            if leaf['type'] == 'symptom':
                leaf.update(type='observation', observation_type='symptom', code=leaf.get('symptom_code'), concept_code=leaf.get('concept_code') or 'PRESENT')
            if leaf['type'] in {'group', 'weighted_group', 'count_group'}:
                raise ValueError('判据必须为叶子，组合单独保存')
            signature = json.dumps({k:v for k,v in leaf.items() if k not in {'label','weight','required'}}, sort_keys=True, ensure_ascii=False)
            if signature in signatures:
                raise ValueError('同一观察判据重复，不能重复计数或计分')
            signatures.add(signature)
            leaf['parameter'] = {**(leaf.get('parameter') or {}), 'manifestation_role': criterion.manifestation_role, 'schema_version': 3, 'criterion_role': criterion.role,
                                 'source': criterion.source.model_dump()}
            leaf.pop('required', None)
            criteria[criterion.criterion_id] = leaf
        used = set()
        def compile_node(spec, depth=0):
            if depth > 16 or not isinstance(spec, dict):
                raise ValueError('条件树无效或过深')
            if 'criterion_id' in spec:
                key = spec['criterion_id']
                if key not in criteria or key in used:
                    raise ValueError('未知或重复判据')
                if next(c for c in contract.criteria if c.criterion_id == key).role == 'exclusion':
                    raise ValueError('排除判据由后端生成非条件，不能同时作为阳性判据')
                used.add(key)
                leaf = copy.deepcopy(criteria[key])
                if 'weight' in spec:
                    leaf['weight'] = spec['weight']
                return leaf
            kind = spec.get('type', 'group')
            result = {k: spec[k] for k in ('operator', 'min_count', 'scoring', 'threshold') if k in spec}
            result.update(type=kind, children=[compile_node(c, depth + 1) for c in spec.get('children', [])])
            return result
        conditions = {'type': 'group', 'operator': 'AND', 'children': [
            contract.scope.model_dump(exclude_none=True), compile_node(contract.combination.model_dump(exclude_none=True, exclude_defaults=True))]}
        def mark_context(node):
            if node.get('type') == 'observation':
                node['parameter'] = {**(node.get('parameter') or {}), 'schema_version': 3}
            for child in node.get('children', []): mark_context(child)
        mark_context(conditions)
        issues = validate_tree(conditions)
        if issues:
            raise ValueError('；'.join(issues))
        required = [copy.deepcopy(criteria[c.criterion_id]) for c in contract.criteria if c.role == 'required']
        exclusions = [{'type': 'group', 'operator': 'NOT', 'children': [copy.deepcopy(criteria[c.criterion_id])]}
                      for c in contract.criteria if c.role == 'exclusion']
        conditions['children'].extend(required + exclusions)
        # Scope must explicitly name a disease; never map a western disease as
        # a synonym for a syndrome. Stage/population restrictions belong here.
        def has_disease(node):
            if node.get('type') == 'group' and node.get('operator') != 'AND':
                return False
            return (node.get('type') == 'past_history' and bool(node.get('code')) or
                    node.get('type') == 'fact' and node.get('fact_type') in {'disease', 'western_disease'} and bool(node.get('code')) or
                    any(has_disease(c) for c in node.get('children', [])))
        if not has_disease(conditions['children'][0]):
            raise ValueError('V3规则必须明确适用病种前提')
        conclusion = copy.deepcopy(contract.conclusion)
        if contract.rule_type in {'tcm_treatment', 'tcm_prescription'}:
            def has_target_fact(node):
                return (node.get('type') == 'fact' and node.get('fact_type') == contract.target_type and node.get('code') == contract.target_code or
                        node.get('type') == 'group' and node.get('operator') == 'AND' and any(has_target_fact(c) for c in node.get('children', [])))
            if not has_target_fact(conditions):
                raise ValueError('治疗及方药方案必须依赖已成立的目标证候事实')
        conclusion['diagnosis'] = contract.target_code
        if contract.rule_type == 'tcm_prescription':
            if not conclusion.get('formula_code') and not conclusion.get('treatment_items'):
                raise ValueError('方药规则缺少方剂或制剂')
            conclusion['herbs'] = [c.model_dump() for c in contract.herb_components]
            for component in contract.herb_components:
                check_source(component.source)
            modifications = []
            for modification in contract.modifications:
                check_source(modification.source)
                if modification.component:
                    check_source(modification.component.source)
                condition = modification.condition.model_dump(exclude_none=True)
                problems = validate_tree(condition)
                if problems:
                    raise ValueError('；'.join(problems))
                def mark(node):
                    if node['type'] == 'observation':
                        node['parameter'] = {**(node.get('parameter') or {}), 'schema_version': 3}
                    for child in node.get('children', []): mark(child)
                mark(condition)
                modifications.append({**modification.model_dump(), 'condition': condition})
            conclusion['modification_rules'] = modifications
        if contract.rule_type == 'tcm_contraindication':
            if conclusion.get('safety_level') not in {'prohibit', 'caution'}:
                raise ValueError('必须明确禁用或慎用等级')
            if conclusion['safety_level'] == 'prohibit' and not conclusion.get('blocked_targets'):
                raise ValueError('禁用规则必须明确阻止对象')
            if conclusion['safety_level'] == 'caution' and conclusion.get('blocked_targets'):
                raise ValueError('慎用提示不能设置禁用对象')
        missing = list(contract.missing_information)
        if contract.logic_origin == 'ai_suggested':
            missing.append('AI建议组合尚未经人工确认')
        key = hashlib.sha256(f'{doc_id}:{entity["code"]}'.encode()).hexdigest()[:16]
        rule = Rule(rule_id=f'{doc_id}-TCM-V3-{key}', domain='tcm',
                    anchor_type=contract.target_type, rule_type=contract.rule_type,
                    name=contract.name, disease_code=contract.target_code, status='draft',
                    graph_filename=resolve_template_graph_filename(template), conditions=conditions,
                    conclusion=conclusion, source=contract.logic_source,
                    clinical_context={'schema_version': 3, 'contract_code': entity['code'],
                                      'logic_origin': contract.logic_origin, 'evidence': contract.evidence,
                                      'missing_information': missing}, tags=['tcm-v3', 'auto-generated']).model_dump()
        rules.append(rule)
    return rules


def generate_v3_rules(entities, chunks, template, doc_id, doc_title='', relations=None, warnings=None):
    rules = []
    for entity in entities:
        try:
            rules.extend(_generate_v3_rules([entity], chunks, template, doc_id, doc_title, relations))
        except (ValueError, TypeError, KeyError) as error:
            if warnings is None:
                raise
            warnings.append(f"临床判据 {entity.get('name') or entity.get('code')} 未生成规则：{str(error)[:200]}")
    return rules


def validate_v3_rule(rule):
    issues = validate_tree(rule.get('conditions') or {})
    def mandatory(node, predicate):
        return predicate(node) or (node.get('type') == 'group' and node.get('operator') == 'AND'
                                   and any(mandatory(child, predicate) for child in node.get('children') or []))
    if not mandatory(rule.get('conditions') or {}, lambda n: n.get('type') == 'past_history' and bool(n.get('code'))
                     or n.get('type') == 'fact' and n.get('fact_type') in {'disease', 'western_disease'} and bool(n.get('code'))):
        issues.append('缺少不可绕过的适用病种前提')
    context = rule.get('clinical_context') or {}
    if context.get('logic_origin') not in {'guideline_explicit', 'human_defined'}:
        issues.append('组合逻辑尚未确认')
    if context.get('missing_information'):
        issues.append('临床判据仍有待补信息')
    if rule.get('rule_type') in {'tcm_treatment', 'tcm_prescription'} and not mandatory(
            rule.get('conditions') or {}, lambda n: n.get('type') == 'fact' and n.get('fact_type') == rule.get('anchor_type')
            and n.get('code') == rule.get('disease_code')):
        issues.append('治疗方案缺少目标证候前置事实')
    return issues


def remap_contract_codes(contract, mapping):
    """Keep discovery IDs consistent across entity extraction and typed references."""
    def code(kind, value):
        return mapping.get((kind, value), value)
    def condition(node):
        kind = node.get('type')
        if kind == 'observation': node['code'] = code(node.get('observation_type', 'symptom'), node.get('code'))
        elif kind == 'fact': node['code'] = code(node.get('fact_type'), node.get('code'))
        elif kind == 'past_history':
            node['code'] = code('disease', code('western_disease', node.get('code')))
        elif kind == 'medication': node['code'] = code('medication', node.get('code'))
        for field in ('children', 'required_children'):
            for child in node.get(field) or []: condition(child)
    contract['target_code'] = code(contract.get('target_type', 'syndrome'), contract.get('target_code'))
    condition(contract.get('scope') or {})
    for criterion in contract.get('criteria') or []: condition(criterion.get('condition') or {})
    conclusion = contract.get('conclusion') or {}
    for field, kind in [('formula_code', 'formula'), ('treatment_method_code', 'treatment_method')]:
        if conclusion.get(field): conclusion[field] = code(kind, conclusion[field])
    for item in conclusion.get('treatment_items') or []: item['code'] = code('medication', item.get('code'))
    for item in contract.get('herb_components') or []: item['code'] = code('herb', item.get('code'))
    for item in contract.get('modifications') or []:
        condition(item.get('condition') or {})
        item['target_code'] = code('herb', item.get('target_code'))
        if item.get('component'): item['component']['code'] = code('herb', item['component'].get('code'))
    conclusion['blocked_targets'] = [next((mapping[(kind, value)] for kind in ('herb', 'formula', 'medication', 'treatment_method')
                                           if (kind, value) in mapping), value) for value in conclusion.get('blocked_targets') or []]
