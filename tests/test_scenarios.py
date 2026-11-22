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
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "DOC-TEST.parsed.json").write_text(json.dumps({"parsed": {"lines": [
        {"page": 1, "line": 1, "text": "测试原文"}, {"page": 1, "line": 2, "text": "测试适用范围"}]}}), encoding="utf-8")
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


def test_scene_supports_advanced_condition_combinations(scenario_env):
    service, _, _ = scenario_env
    filename = "SCENARIO.json"
    disease = "ENTITY:disease:I21.0"
    symptom = "ENTITY:symptom:SYM_DRAFT"
    code = "DISEASE_HAS_SYMPTOM"
    service.review_node(filename, symptom, "approve", "医生")
    service.review_relation(filename, disease, code, symptom, "approve", "医生")
    selections = [*request().selections, scenarios.Selection(
        relation_id=f"{disease}|{code}|{symptom}", role="condition",
    )]
    count = scenarios.prepare(request(operator="COUNT", min_count=2, selections=selections), filename)["basis"]
    assert count["specification"]["conditions"]["type"] == "count_group"
    assert count["specification"]["conditions"]["min_count"] == 2
    assert not count["validation"]["issues"]
    with pytest.raises(HTTPException, match="422"):
        scenarios.prepare(request(operator="COUNT", min_count=3, selections=selections), filename)

    weighted = scenarios.prepare(request(operator="WEIGHTED", threshold=0.75, selections=[
        selection.model_copy(update={"weight": weight}) for selection, weight in zip(selections, (1.0, 3.0))
    ]), filename)["basis"]
    tree = weighted["specification"]["conditions"]
    assert tree["type"] == "weighted_group"
    assert tree["threshold"] == 0.75
    assert [child["weight"] for child in tree["children"]] == [1.0, 3.0]
    assert not weighted["validation"]["issues"]
    with pytest.raises(HTTPException, match="422"):
        scenarios.prepare(request(operator="WEIGHTED", threshold=0.75, selections=selections), filename)

    custom = scenarios.prepare(request(operator="CUSTOM", selections=selections), filename)["basis"]
    assert custom["specification"]["conditions"]["operator"] is None
    assert custom["validation"]["issues"]
    with pytest.raises(HTTPException, match="409"):
        clinical_basis.review_basis(custom["basis_id"], clinical_basis.BasisReview(
            action="approve", reviewer="医生", checks={key: True for key in CHECKS}), filename)
    completed = clinical_basis.complete_basis_from_reviewed_graph(custom["basis_id"], filename)
    assert completed["logic_questions"]


def test_changed_scene_relation_blocks_generation(scenario_env):
    basis = scenarios.prepare(request(), "SCENARIO.json")["basis"]
    clinical_basis.review_basis(basis["basis_id"], clinical_basis.BasisReview(
        action="approve", reviewer="医生", checks={key: True for key in CHECKS}), "SCENARIO.json")
    graph = json.loads(scenario_env[1].read_text(encoding="utf-8"))
    graph["relations"][0]["attributes"] = {"changed": "yes"}
    scenario_env[1].write_text(json.dumps(graph), encoding="utf-8")
    with pytest.raises(HTTPException):
        clinical_basis.generate_rule(basis["basis_id"], "SCENARIO.json")


def test_reviewed_tcm_observations_generate_suspected_syndrome_and_disease(scenario_env):
    from types import SimpleNamespace
    from src.models.patient import Observation
    from src.models.rule import Rule
    from tests.test_engine import build_engine, make_patient

    service, path, rules_path = scenario_env
    disease = "ENTITY:disease:TCM_TEST"
    syndrome = "ENTITY:syndrome:SYN_TEST"
    symptom = "ENTITY:symptom:SYM_TEST"
    tongue = "ENTITY:tongue:TONGUE_TEST"
    mechanism = "ENTITY:pathogenesis:PATH_TEST"
    membership = (disease, "DISEASE_HAS_SYNDROME", syndrome)
    observations = [
        (syndrome, "SYNDROME_HAS_SYMPTOM", symptom),
        (syndrome, "SYNDROME_HAS_TONGUE", tongue),
        (syndrome, "SYNDROME_HAS_PATHOGENESIS", mechanism),
    ]
    graph = {
        "domain": "tcm",
        "nodes": [
            dict(id=disease, node_type="disease", code="TCM_TEST", name="测试病", review_status="active"),
            dict(id=syndrome, node_type="syndrome", code="SYN_TEST", name="测试证", review_status="active"),
            dict(id=symptom, node_type="symptom", code="SYM_TEST", name="测试症", review_status="active"),
            dict(id=tongue, node_type="tongue", code="TONGUE_TEST", name="测试舌象", review_status="active"),
            dict(id=mechanism, node_type="pathogenesis", code="PATH_TEST", name="测试病机", review_status="active"),
        ],
        "relations": [dict(source_node_id=start, relation_code=code, target_node_id=end,
                           review_status="active", source_refs=["DOC-TEST_p1_l1-2"])
                      for start, code, end in [membership, *observations]],
    }
    path.write_text(json.dumps(graph), encoding="utf-8")
    payload = request(disease_code="TCM_TEST", operator="AND", selections=[
        dict(relation_id="|".join(key), role="condition") for key in observations
    ])
    filename = "SCENARIO.json"
    basis = scenarios.prepare(payload, filename)["basis"]
    assert not basis["validation"]["issues"]
    assert scenarios.overview(filename, "TCM_TEST")["scenes"][0]["bases"][0]["basis_id"] == basis["basis_id"]
    syndrome_scene = scenarios.overview(filename, syndrome_code="SYN_TEST")
    assert syndrome_scene["disease_code"] == "TCM_TEST"
    assert syndrome_scene["owners"][0]["approved"]
    assert {item["id"] for item in syndrome_scene["items"] if item["approved"]} == {
        "|".join(key) for key in observations
    }
    assert syndrome_scene["scenes"][0]["bases"][0]["basis_id"] == basis["basis_id"]
    saved_graph = service._load_graph(filename)
    for key, rule_type, intent in [
        ("CB-PATTERN", "tcm_pattern", "syndrome_identification"),
        ("CB-FORMULA", "tcm_prescription", "treatment_recommendation"),
    ]:
        saved_graph["clinical_bases"].append({
            "basis_id": key, "name": key, "status": "draft",
            "specification": {"anchor_type": "syndrome", "disease_code": "SYN_TEST",
                              "rule_type": rule_type, "clinical_intent": intent},
        })
    service._save_graph(saved_graph, filename)
    for scoped in (scenarios.overview(filename, "TCM_TEST"), scenarios.overview(filename, syndrome_code="SYN_TEST")):
        by_scene = {entry["key"]: entry["bases"] for entry in scoped["scenes"]}
        assert [item["basis_id"] for item in by_scene["suspected_diagnosis"]] == [basis["basis_id"]]
        assert [item["basis_id"] for item in by_scene["syndrome_identification"]] == ["CB-PATTERN"]
        assert [item["basis_id"] for item in by_scene["treatment_recommendation"]] == ["CB-FORMULA"]
    clinical_basis.review_basis(basis["basis_id"], clinical_basis.BasisReview(
        action="approve", reviewer="医生", checks={key: True for key in CHECKS}), filename)
    clinical_basis.generate_rule(basis["basis_id"], filename)
    raw_rule = json.loads(rules_path.read_text(encoding="utf-8"))[0]
    rule = Rule(**{**raw_rule, "status": "active"})
    engine = build_engine()
    engine.domain = "tcm"
    engine.rule_repo = SimpleNamespace(list_all=lambda: [rule])
    patient = make_patient(observations=[Observation(observation_type="symptom", code="SYM_TEST", present=True)])
    assert not engine.run(patient)
    patient.observations.append(Observation(observation_type="tongue", code="TONGUE_TEST", present=True))
    assert not engine.run(patient)
    patient.observations.append(Observation(observation_type="pathogenesis", code="PATH_TEST", present=True))
    matched, = engine.run(patient)
    assert matched.anchor_code == "SYN_TEST"
    assert matched.suspected_disease_code == "TCM_TEST"
    facts = {}
    engine._materialize_facts(facts, [matched])
    assert not facts

    service.review_relation(filename, *membership, "retire", "医生")
    assert basis["basis_id"] in {item["basis_id"] for item in clinical_basis.list_bases(filename)["items"]}
    assert basis["basis_id"] not in {
        item["basis_id"] for item in clinical_basis.list_bases(filename, reviewable_only=True)["items"]
    }
    blocked_scene = scenarios.overview(filename, syndrome_code="SYN_TEST")
    assert not blocked_scene["scenes"][0]["bases"]
    assert not blocked_scene["owners"][0]["approved"]
    assert blocked_scene["items"] and all(item["review_blocker"] for item in blocked_scene["items"])
    with pytest.raises(HTTPException, match="409"):
        scenarios.prepare(payload, filename)
    assert service._sync([raw_rule])["rules"][0]["dependency_status"] == "blocked"


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


@pytest.mark.parametrize("intent,status,test_mode,expected", [
    ("suspected_diagnosis", "active", False, False),
    ("diagnosis_confirmation", "active", False, True),
    ("diagnosis_confirmation", "draft", False, False),
    ("diagnosis_confirmation", "draft", True, True),
])
def test_treatment_requires_selected_diagnosis_state(intent, status, test_mode, expected):
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
    result = engine._run_stages(make_patient(), include_draft_rules=test_mode, matcher=lambda rules, patient: {rule.rule_id: ([], {}) for rule in rules})
    assert ("TX" in [item.rule_id for item in result]) == expected
