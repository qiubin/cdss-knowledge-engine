import json
import copy
import pytest
from fastapi import HTTPException
from src.api.routes import scenarios, clinical_basis
from src.review.clinical_basis import CHECKS
from src.review.service import ReviewService
from tests.test_graph_review_service import GRAPH


@pytest.fixture
def scenario_env(tmp_path, monkeypatch):
    graph_dir = tmp_path / "kg"
    graph_dir.mkdir()
    path = graph_dir / "SCENARIO.json"
    graph = copy.deepcopy(GRAPH)
    for edge in graph["relations"]:
        edge["source_refs"] = ["DOC-TEST_p1_l1-2"]
    path.write_text(json.dumps(graph), encoding="utf-8")
    rules = tmp_path / "rules.json"
    rules.write_text("[]", encoding="utf-8")
    service = ReviewService(graph_dir, rules, tmp_path / "audit.jsonl")
    monkeypatch.setattr(scenarios, "service", service)
    monkeypatch.setattr(clinical_basis, "service", service)
    return service, path, rules


def request(**changes):
    return scenarios.Prepare(**{**dict(disease_code="I21.0", scene="suspected_diagnosis",
        selections=[dict(relation_id="ENTITY:disease:I21.0|DISEASE_HAS_SYMPTOM|ENTITY:symptom:SYM_TEST", role="condition")],
        operator="OR", source=dict(guideline_id="DOC-TEST", quote="测试原文", page_start=1, line_start=1, line_end=2),
        scope_summary="测试适用范围", reviewer="医生"), **changes})


def test_existing_reviews_are_reused_and_preparation_is_idempotent(scenario_env):
    result = scenarios.prepare(request(), "SCENARIO.json")
    basis = result["basis"]
    assert basis["specification"]["conditions"]["children"][0]["symptom_code"] == "SYM_TEST"
    assert not basis["validation"]["issues"]
    assert scenarios.prepare(request(), "SCENARIO.json")["basis"]["basis_id"] == basis["basis_id"]
    graph = scenario_env[0]._load_graph("SCENARIO.json")
    assert graph["nodes"]["ENTITY:symptom:SYM_TEST"]["review_status"] == "active"
    assert json.loads(scenario_env[2].read_text()) == []


def test_unapproved_relation_cannot_become_scenario(scenario_env):
    with pytest.raises(HTTPException, match="409"):
        scenarios.prepare(request(selections=[dict(relation_id="ENTITY:disease:I21.0|DISEASE_HAS_SYMPTOM|ENTITY:symptom:SYM_DRAFT", role="condition")]), "SCENARIO.json")


def test_cross_document_association_rejected(scenario_env):
    source = request().source
    with pytest.raises(HTTPException):
        scenarios.prepare(request(source={**source, "guideline_id": "OTHER"}), "SCENARIO.json")


def test_changed_scene_relation_blocks_generation(scenario_env):
    basis = scenarios.prepare(request(), "SCENARIO.json")["basis"]
    clinical_basis.review_basis(basis["basis_id"], clinical_basis.BasisReview(
        action="approve", reviewer="医生", checks={key: True for key in CHECKS}), "SCENARIO.json")
    graph = json.loads(scenario_env[1].read_text(encoding="utf-8"))
    graph["relations"][0]["attributes"] = {"changed": "yes"}
    scenario_env[1].write_text(json.dumps(graph), encoding="utf-8")
    with pytest.raises(HTTPException):
        clinical_basis.generate_rule(basis["basis_id"], "SCENARIO.json")


def test_reviewed_treatment_content_is_output_not_a_condition(scenario_env):
    graph = json.loads(scenario_env[1].read_text(encoding="utf-8"))
    graph["nodes"].extend([
        dict(id="PLAN", node_type="treatment_plan", code="PLAN", name="测试方案", review_status="active"),
        dict(id="MED", node_type="medication", code="MED", name="测试药品", review_status="active"),
    ])
    graph["relations"].extend([
        dict(source_node_id="ENTITY:disease:I21.0", relation_code="DISEASE_HAS_TREATMENT_PLAN", target_node_id="PLAN", review_status="active", source_refs=["DOC-TEST_p1_l1-2"]),
        dict(source_node_id="PLAN", relation_code="TREATMENT_PLAN_INCLUDES_MEDICATION", target_node_id="MED", review_status="active", source_refs=["DOC-TEST_p1_l1-2"]),
    ])
    scenario_env[1].write_text(json.dumps(graph), encoding="utf-8")
    result = scenarios.prepare(request(scene="treatment_selection", operator="AND", selections=[
        dict(relation_id="PLAN|TREATMENT_PLAN_INCLUDES_MEDICATION|MED", role="recommendation")]), "SCENARIO.json")
    basis = result["basis"]
    assert basis["specification"]["conditions"]["children"] == []
    assert basis["specification"]["conclusion"]["treatment_items"][0]["code"] == "MED"
    assert basis["specification"]["diagnosis_prerequisite"] == "confirmed"
    assert not basis["validation"]["issues"]
    clinical_basis.review_basis(basis["basis_id"], clinical_basis.BasisReview(
        action="approve", reviewer="医生", checks={key: True for key in CHECKS}), "SCENARIO.json")
    generated = clinical_basis.generate_rule(basis["basis_id"], "SCENARIO.json")
    assert generated["status"] == "draft"
    assert json.loads(scenario_env[2].read_text(encoding="utf-8"))[0]["diagnosis_prerequisite"] == "confirmed"


@pytest.mark.parametrize("intent,status,expected", [
    ("suspected_diagnosis", "active", False),
    ("diagnosis_confirmation", "active", True),
    ("diagnosis_confirmation", "draft", False),
])
def test_treatment_requires_selected_diagnosis_state(intent, status, expected):
    from types import SimpleNamespace
    from src.models.rule import Rule
    from tests.test_engine import build_engine, make_patient
    diagnosis = Rule(rule_id="DX", disease_code="I21.0", clinical_intent=intent,
                     status="active", conditions={"type": "group", "operator": "AND", "children": []},
                     conclusion={"diagnosis": "I21.0"})
    treatment = Rule(rule_id="TX", disease_code="I21.0", rule_type="treatment", status=status,
                     diagnosis_prerequisite="confirmed", conditions={"type": "group", "operator": "AND", "children": []},
                     conclusion={"diagnosis": "I21.0"})
    engine = build_engine()
    engine.rule_repo = SimpleNamespace(list_all=lambda: [diagnosis, treatment])
    result = engine._run_stages(make_patient(), matcher=lambda rules, patient: {rule.rule_id: ([], {}) for rule in rules})
    assert ("TX" in [item.rule_id for item in result]) == expected
