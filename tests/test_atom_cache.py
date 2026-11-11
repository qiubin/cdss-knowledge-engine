# tests/test_atom_cache.py
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.engine import InferenceEngine
from src.engine.evaluators import EvaluatorFactory
from src.engine.evaluators.base import BaseEvaluator, EvaluationResult
from src.kg.schema import ConceptVocabulary
from src.models.patient import Gender, LabResult, PatientSnapshot
from src.models.rule import Rule, RuleCondition, RuleConclusion
from src.rules.repository import RuleRepository
from src.standards.repository import JsonStandardRepository


class _InlineRepo(RuleRepository):
    """Two rules sharing one lab_concept atom, no file fixtures needed."""

    def __init__(self):
        leaf = {"type": "lab_concept", "lab_code": "MYO", "concept_code": "ELEVATED"}
        self.rules = [
            Rule(
                rule_id="CACHE-R1", disease_code="I21.0", disease_name="测试病A",
                conditions=RuleCondition(**leaf),
                conclusion=RuleConclusion(diagnosis="I21.0", message="R1"),
                graph_filename="stemi_graph.json",
            ),
            Rule(
                rule_id="CACHE-R2", disease_code="I21.1", disease_name="测试病B",
                conditions=RuleCondition(**leaf),
                conclusion=RuleConclusion(diagnosis="I21.1", message="R2"),
                graph_filename="stemi_graph.json",
            ),
        ]

    def list_all(self):
        return list(self.rules)

    def get_active_rules(self, department=None):
        return [r for r in self.rules if r.status == "active"]

    def get_treatment_candidates(self, department=None):
        return []

    def save_rule(self, rule):
        pass


class _NullKG:
    """Minimal KG stand-in so conclusion building works without fixtures."""

    def get_disease(self, code):
        return None


class _CountingEvaluator(BaseEvaluator):
    calls = 0

    def evaluate(self, patient, standard, parameter=None):
        _CountingEvaluator.calls += 1
        matched = bool(patient.labs) and (patient.labs[0].value or 0) > 50
        return EvaluationResult(
            matched=matched,
            patient_value_display=str(patient.labs[0].value if patient.labs else "-"),
            standard_used="counted>50",
        )


def _patient():
    return PatientSnapshot(
        patient_id="P-ATOM", hospital_id="XM_FIRST", age=55, gender=Gender.MALE,
        labs=[LabResult(lab_code="MYO", value=120.0, unit="ng/mL")],
    )


def _engine():
    vocab = ConceptVocabulary()
    vocab.load(str(project_root / "data" / "vocab" / "concepts.json"))
    # 把 MYO 的 ELEVATED 概念换成计数评估器（通过 monkeypatch vocabulary 实现）
    class _Vocab(ConceptVocabulary):
        def get_evaluator_type(self, code):
            return "counting_test"

    return InferenceEngine(
        _InlineRepo(), _NullKG(), JsonStandardRepository(str(project_root / "data" / "standards")),
        _Vocab(),
    )


def test_shared_atom_evaluated_once_per_run(monkeypatch):
    monkeypatch.setitem(EvaluatorFactory._registry, "counting_test", _CountingEvaluator)
    _CountingEvaluator.calls = 0
    engine = _engine()
    conclusions = engine.run(_patient())
    assert {c.rule_id for c in conclusions} == {"CACHE-R1", "CACHE-R2"}
    # 两条规则共享同一原子：评估器只应执行 1 次
    assert _CountingEvaluator.calls == 1


def test_cache_reset_between_runs(monkeypatch):
    monkeypatch.setitem(EvaluatorFactory._registry, "counting_test", _CountingEvaluator)
    _CountingEvaluator.calls = 0
    engine = _engine()
    first = engine.run(_patient())
    second = engine.run(_patient())
    assert _CountingEvaluator.calls == 2  # 每次运行各自求值一次
    # matched_at 是每次运行的时间戳，剔除后比较确定性字段
    def dumps(conclusions):
        return [
            {k: v for k, v in c.model_dump().items() if k != "matched_at"}
            for c in conclusions
        ]
    assert dumps(first) == dumps(second)
