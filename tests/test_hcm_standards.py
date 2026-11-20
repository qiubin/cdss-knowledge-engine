"""HCM templates must preserve clinical boundaries and never imply activation."""
import asyncio
import json
from pathlib import Path

import pytest

from src.api.routes.standards import get_hcm_templates
from src.engine.evaluators.exam_interpretation import interpret_exam
from src.kg.schema import ConceptVocabulary
from src.models.patient import PatientSnapshot, ExamFinding
from src.models.standard import LabInterpretationStandard
from src.standards.hcm import hcm_templates
from src.standards.validation import standard_issues
from src.standards.repository import JsonStandardRepository
from tests.test_engine import build_engine
from src.models.rule import RuleCondition

ROOT = Path(__file__).resolve().parents[1]


def template(finding='LVOT_PEAK_GRADIENT_REST', concept='GE_30'):
    return next(t for t in hcm_templates('H') if t['finding_code'] == finding and t['concept_code'] == concept)


@pytest.mark.parametrize('concept,value,expected', [
    ('GE_30', 29.9, False), ('GE_30', 30, True),
    ('LT_30', 30, False), ('LT_30', 29.9, True),
    ('GE_50', 49.9, False), ('GE_50', 50, True),
])
def test_gradient_boundaries(concept, value, expected):
    row = template(concept=concept)
    patient = PatientSnapshot(patient_id='T', hospital_id='H', age=52, exam_findings=[
        ExamFinding(exam_code='ECHO', finding_code=row['finding_code'], value=value, unit='mmHg')])
    result = interpret_exam(patient, LabInterpretationStandard(**row), row)
    assert result.matched is expected


def test_numeric_concept_cannot_be_activated_as_presence_or_wrong_threshold():
    assert not standard_issues(template())
    assert standard_issues({**template(), 'interpretation_type': 'presence'})
    assert standard_issues({**template(), 'operator': '>', 'threshold_value': 50})


def test_catalog_and_seeded_templates_are_complete_and_registered():
    templates = asyncio.run(get_hcm_templates())
    assert len(templates) == 12
    vocab = ConceptVocabulary(); vocab.load(str(ROOT / 'data/vocab/concepts.json'))
    findings = {r['code'] for r in json.loads((ROOT / 'data/vocab/entities/exam_finding.json').read_text())}
    for row in templates:
        assert row['status'] == 'draft' and row['context_age_min'] == 18
        assert row['source_note'] and vocab.exists(row['concept_code'])
        assert row['finding_code'] in findings
        assert not standard_issues({**row, 'hospital_id': 'H'})
    repo = JsonStandardRepository(str(ROOT / 'data/standards'))
    for hospital in ['XM_FIRST', 'FZ_HOSPITAL']:
        for row in templates:
            matches = [s for s in repo.list_for_hospital(hospital) if s.item_type == 'exam'
                and (s.exam_code, s.finding_code, s.concept_code) == (row['exam_code'], row['finding_code'], row['concept_code'])]
            # Seeding never activates; rows may only become active through review.
            assert len(matches) == 1
            seeded = matches[0]
            assert seeded.status in ('draft', 'active')
            assert (seeded.operator, seeded.threshold_value) == (row['operator'], row['threshold_value'])
            assert seeded.source_note == row['source_note']


def test_numeric_rule_uses_standards_and_missing_provocation_stays_unknown(tmp_path):
    rows = [{**r, 'status': 'active'} for r in hcm_templates('H')]
    (tmp_path / 'h.json').write_text(json.dumps(rows))
    engine = build_engine(); engine.standard_repo = JsonStandardRepository(str(tmp_path))
    def leaf(finding, concept):
        return RuleCondition(type='exam_finding', exam_code='ECHO', finding_code=finding,
            concept_code=concept, standard_required=True)
    rest = 'LVOT_PEAK_GRADIENT_REST'; provoked = 'LVOT_PEAK_GRADIENT_PROVOKED'
    rule = RuleCondition(type='group', operator='OR', children=[leaf(rest, 'GE_30'),
        RuleCondition(type='group', operator='AND', children=[leaf(rest, 'LT_30'), leaf(provoked, 'GE_30')])])
    def evaluate(rest_value, provoked_value=None, age=52):
        engine._atom_cache = {}
        findings = [ExamFinding(exam_code='ECHO', finding_code=rest, value=rest_value, unit='mmHg')]
        if provoked_value is not None:
            findings.append(ExamFinding(exam_code='ECHO', finding_code=provoked, value=provoked_value, unit='mmHg'))
        return engine._evaluate_condition(rule, PatientSnapshot(patient_id='T', hospital_id='H', age=age, exam_findings=findings))[0]
    assert evaluate(58, 85) is True
    assert evaluate(20, 30) is True
    assert evaluate(20, 29) is False
    assert evaluate(20) is None
    assert evaluate(58, age=12) is None
