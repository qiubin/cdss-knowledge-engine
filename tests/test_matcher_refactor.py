# tests/test_matcher_refactor.py
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.engine import InferenceEngine
from src.engine.evaluators import EvaluatorFactory
from src.engine.evaluators.base import BaseEvaluator, EvaluationResult
from src.kg.client import JsonGraphClient
from src.kg.schema import ConceptVocabulary
from src.models.patient import Gender, LabResult, PatientSnapshot
from src.models.rule import Rule, RuleCondition, RuleConclusion
from src.rules.repository import RuleRepository
from src.standards.repository import JsonStandardRepository


class _Repo(RuleRepository):
    def __init__(self):
        leaf = {"type": "lab_concept", "lab_code": "MYO", "concept_code": "ELEVATED"}
        self.rules = [
            Rule(rule_id="M-R1", disease_code="I21.0", disease_name="A",
                 conditions=RuleCondition(**leaf),
                 conclusion=RuleConclusion(diagnosis="I21.0", message="R1"),
                 graph_filename="stemi_graph.json"),
        ]

    def list_all(self):
        return list(self.rules)

    def get_active_rules(self, department=None):
        return [r for r in self.rules if r.status == "active"]

    def get_treatment_candidates(self, department=None):
        return []

    def save_rule(self, rule):
        pass  # abstract in RuleRepository; stub keeps _Repo instantiable


class _NullKG:
    """Minimal KG stand-in so conclusion building works without fixtures."""

    def get_disease(self, code):
        return None


class _Counting(BaseEvaluator):
    calls = 0

    def evaluate(self, patient, standard, parameter=None):
        _Counting.calls += 1
        value = patient.labs[0].value if patient.labs else 0
        return EvaluationResult(matched=value > 50, patient_value_display=str(value), standard_used="c>50")


class _Vocab(ConceptVocabulary):
    def get_evaluator_type(self, code):
        return "counting_matcher"


def _engine():
    return InferenceEngine(
        _Repo(), _NullKG(),
        JsonStandardRepository(str(project_root / "data" / "standards")),
        _Vocab(),
    )


def _patient():
    return PatientSnapshot(
        patient_id="P-M", hospital_id="XM_FIRST", age=55, gender=Gender.MALE,
        labs=[LabResult(lab_code="MYO", value=120.0)],
    )


def test_run_stages_with_custom_matcher(monkeypatch):
    monkeypatch.setitem(EvaluatorFactory._registry, "counting_matcher", _Counting)
    engine = _engine()

    def fake_matcher(stage_rules, patient):
        # 假装 sidecar 说 M-R1 匹配了；引擎随后应重建结论与溯源
        return {"M-R1": ([], {})}

    conclusions = engine._run_stages(_patient(), None, False, matcher=fake_matcher)
    assert [c.rule_id for c in conclusions] == ["M-R1"]
    assert conclusions[0].evidence == []


def test_native_match_stage_returns_traces(monkeypatch):
    monkeypatch.setitem(EvaluatorFactory._registry, "counting_matcher", _Counting)
    engine = _engine()
    patient = _patient()
    results = engine._native_match_stage(engine.rule_repo.list_all(), patient)
    assert "M-R1" in results
    traces, weighted = results["M-R1"]
    assert traces  # 原生匹配必须产出证据链
    assert weighted == {}


def test_default_run_matches_previous_behavior(monkeypatch):
    monkeypatch.setitem(EvaluatorFactory._registry, "counting_matcher", _Counting)
    _Counting.calls = 0
    engine = _engine()
    conclusions = engine.run(_patient())
    assert [c.rule_id for c in conclusions] == ["M-R1"]
    assert conclusions[0].matched_conditions >= 1


def test_conclusion_build_rebinds_rule_graph(monkeypatch, tmp_path):
    """anchor_name must come from the rule's own graph, not the default one."""
    monkeypatch.setitem(EvaluatorFactory._registry, "counting_matcher", _Counting)
    default_graph = tmp_path / "default_graph.json"
    rule_graph = tmp_path / "rule_graph.json"
    default_graph.write_text(
        json.dumps({"diseases": [{"code": "I21.0", "name_cn": "默认名"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    rule_graph.write_text(
        json.dumps({"diseases": [{"code": "I21.0", "name_cn": "规则图谱长名称"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    repo = _Repo()
    for rule in repo.rules:
        # 让工厂在求值与结论构建时都拿到 rule_graph.json（不触碰默认图谱）
        rule.graph_filename = "rule_graph.json"

    engine = InferenceEngine(
        repo,
        JsonGraphClient(str(default_graph)),
        JsonStandardRepository(str(project_root / "data" / "standards")),
        _Vocab(),
        graph_client_factory=lambda filename: JsonGraphClient(str(tmp_path / filename)),
    )
    conclusions = engine.run(_patient())
    assert [c.rule_id for c in conclusions] == ["M-R1"]
    # 规则图谱名（7字）在 len() 决胜中压过 rule.disease_name "A"；默认图谱名则不会
    assert conclusions[0].anchor_name == "规则图谱长名称"
    assert conclusions[0].disease_name == "规则图谱长名称"
