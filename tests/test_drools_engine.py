import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import httpx
import pytest

from src.engine.drools.client import DroolsClientError
from src.engine.drools.engine import DroolsRuleEngine
from src.engine import InferenceEngine
from src.engine.evaluators import EvaluatorFactory
from src.engine.evaluators.base import BaseEvaluator, EvaluationResult
from src.kg.schema import ConceptVocabulary
from src.models.patient import Gender, LabResult, Observation, PatientSnapshot
from src.models.rule import Rule, RuleCondition, RuleConclusion
from src.rules.repository import RuleRepository
from src.standards.repository import JsonStandardRepository


class _Repo(RuleRepository):
    def __init__(self, extra=None):
        leaf = {"type": "lab_concept", "lab_code": "MYO", "concept_code": "ELEVATED"}
        self.rules = [
            Rule(rule_id="D-R1", disease_code="I21.0", disease_name="A",
                 conditions=RuleCondition(**leaf),
                 conclusion=RuleConclusion(diagnosis="I21.0", message="R1"),
                 graph_filename="stemi_graph.json"),
        ]
        self.rules.extend(extra or [])

    def list_all(self):
        return list(self.rules)

    def get_active_rules(self, department=None):
        return [r for r in self.rules if r.status == "active"]

    def get_treatment_candidates(self, department=None):
        return []

    def save_rule(self, rule):
        self.rules = [r for r in self.rules if r.rule_id != rule.rule_id] + [rule]


class _NullKG:
    """Minimal KG stand-in so conclusion building works without fixtures."""

    def get_disease(self, code):
        return None


class _Vocab(ConceptVocabulary):
    def get_evaluator_type(self, code):
        return "counting_drools"


class _Counting(BaseEvaluator):
    calls = 0

    def evaluate(self, patient, standard, parameter=None):
        _Counting.calls += 1
        value = patient.labs[0].value if patient.labs else 0
        return EvaluationResult(matched=value > 50, patient_value_display=str(value), standard_used="c>50")


def _patient():
    return PatientSnapshot(
        patient_id="P-D", hospital_id="XM_FIRST", age=55, gender=Gender.MALE,
        labs=[LabResult(lab_code="MYO", value=120.0)],
    )


def _stub_transport(matched_ids_fn, status=200):
    """matched_ids_fn(payload) -> list[str]"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        payload = json.loads(request.content)
        if status != 200:
            return httpx.Response(status, text="sidecar down")
        return httpx.Response(200, json={"matched": matched_ids_fn(payload)})

    return httpx.MockTransport(handler)


def _drools_engine(repo, transport, fail_over=True):
    native = InferenceEngine(
        repo, _NullKG(),
        JsonStandardRepository(str(project_root / "data" / "standards")),
        _Vocab(),
    )
    return DroolsRuleEngine(
        native, base_url="http://sidecar.test",
        timeout_seconds=2, fail_over_native=fail_over, transport=transport,
    )


@pytest.fixture(autouse=True)
def _register(monkeypatch):
    monkeypatch.setitem(EvaluatorFactory._registry, "counting_drools", _Counting)
    yield


def test_drools_conclusions_match_native():
    # sidecar 对每个出现原子的规则返回匹配（单原子规则下与原生语义一致）
    transport = _stub_transport(lambda payload: sorted({f["ruleId"] for f in payload["facts"]}))
    repo = _Repo()
    patient = _patient()
    drools_result = _drools_engine(repo, transport).run(patient)
    native = _drools_engine(repo, transport)._native

    def without_timestamp(conclusions):
        dumps = [c.model_dump() for c in conclusions]
        for item in dumps:
            item.pop("matched_at", None)  # 构建时间天然不同，不参与比较
        return dumps

    assert without_timestamp(drools_result) == without_timestamp(native.run(patient))
    assert drools_result[0].evidence  # 溯源由原生层重建


def test_uncompilable_rules_evaluated_natively():
    weighted = Rule(
        rule_id="D-W", disease_code="I21.9", disease_name="W",
        conditions=RuleCondition(**{
            "type": "weighted_group", "operator": "AND", "threshold": 0.1,
            "children": [{"type": "observation", "observation_type": "tongue", "code": "T_ANY"}],
        }),
        conclusion=RuleConclusion(diagnosis="I21.9"),
    )
    repo = _Repo(extra=[weighted])
    transport = _stub_transport(lambda payload: [])  # sidecar 什么都不匹配
    engine = _drools_engine(repo, transport)
    patient = _patient().model_copy(deep=True)
    patient.observations = [
        Observation(observation_type="tongue", code="T_ANY", present=True)
    ]
    conclusions = engine.run(patient)
    assert any(c.rule_id == "D-W" for c in conclusions)  # weighted_group 走原生


def test_fail_over_on_sidecar_error():
    transport = _stub_transport(lambda payload: [], status=500)
    engine = _drools_engine(_Repo(), transport, fail_over=True)
    conclusions = engine.run(_patient())
    assert [c.rule_id for c in conclusions] == ["D-R1"]
    assert engine.last_fallback_reason
    assert engine.effective_name == "native"
    assert engine.describe()["used"] == "native"


def test_no_fail_over_raises():
    transport = _stub_transport(lambda payload: [], status=500)
    engine = _drools_engine(_Repo(), transport, fail_over=False)
    with pytest.raises(DroolsClientError):
        engine.run(_patient())
