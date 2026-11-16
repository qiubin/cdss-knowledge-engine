import copy
import json
import pytest
from fastapi import HTTPException
from src.api.routes import clinical_basis as api
from src.review.service import ReviewService
from src.review.clinical_basis import stage_bases
from tests.test_graph_review_service import GRAPH, RULE

@pytest.fixture
def env(tmp_path, monkeypatch):
    directory = tmp_path / "kg"
    directory.mkdir()
    rules = tmp_path / "rules" / "managed_rules.json"
    rules.parent.mkdir()
    rules.write_text("[]", encoding="utf-8")
    graph = copy.deepcopy(GRAPH)
    spec = {**copy.deepcopy(RULE), "source": {"guideline_id": "DOC-TEST", "guideline_title": "测试指南", "quote": "测试症状作为判断依据", "page_start": 1, "line_start": 1, "line_end": 2}}
    stage_bases(graph, [spec], "REVIEW-TEST.json")
    graph["clinical_bases"][0]["scope_summary"] = "仅适用本依据条件，暂无额外人群限制；排除条件已核对"
    path = directory / "REVIEW-TEST.json"
    path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
    service = ReviewService(directory, rules, tmp_path / "audit.jsonl")
    monkeypatch.setattr(api, "service", service)
    return service, path, rules, graph["clinical_bases"][0]["basis_id"]


def approve(env):
    return api.review_basis(env[3], api.BasisReview(action="approve", reviewer="审核员", checks={key: True for key in ("entities", "logic", "scope", "source")}), "REVIEW-TEST.json")


def test_nodes_and_relations_alone_cannot_generate(env):
    with pytest.raises(HTTPException) as error:
        api.generate_rule(env[3], "REVIEW-TEST.json")
    assert error.value.status_code == 409
    assert json.loads(env[2].read_text()) == []


def test_explicit_four_category_confirmation_required(env):
    with pytest.raises(HTTPException) as error:
        api.review_basis(env[3], api.BasisReview(action="approve", reviewer="审核员"), "REVIEW-TEST.json")
    assert "逐项确认" in error.value.detail


def test_unapproved_dependencies_block_approval(env):
    graph = json.loads(env[1].read_text(encoding="utf-8"))
    graph["nodes"][1]["review_status"] = "draft"
    env[1].write_text(json.dumps(graph), encoding="utf-8")
    with pytest.raises(HTTPException) as error:
        approve(env)
    assert "实体或关系" in error.value.detail


def test_approved_basis_generates_draft_and_never_overwrites_rule(env):
    approve(env)
    result = api.generate_rule(env[3], "REVIEW-TEST.json")
    assert result["created"] and result["status"] == "draft"
    saved = json.loads(env[2].read_text(encoding="utf-8"))
    assert saved[0]["conditions"]["operator"] == "AND"
    saved[0]["name"] = "人工修订"
    env[2].write_text(json.dumps(saved), encoding="utf-8")
    assert not api.generate_rule(env[3], "REVIEW-TEST.json")["created"]
    assert json.loads(env[2].read_text(encoding="utf-8"))[0]["name"] == "人工修订"


def test_changed_clinical_content_requires_review_again(env):
    approve(env)
    graph = json.loads(env[1].read_text(encoding="utf-8"))
    graph["nodes"][1]["attributes"] = {"changed": True}
    env[1].write_text(json.dumps(graph), encoding="utf-8")
    listed = api.list_bases("REVIEW-TEST.json")
    assert listed["items"][0]["validation"]["status"] == "stale"
    with pytest.raises(HTTPException):
        api.generate_rule(env[3], "REVIEW-TEST.json")


def test_missing_quote_and_empty_boolean_group_block(env):
    graph = json.loads(env[1].read_text(encoding="utf-8"))
    spec = graph["clinical_bases"][0]["specification"]
    spec["source"]["quote"] = ""
    spec["conditions"]["children"] = []
    env[1].write_text(json.dumps(graph), encoding="utf-8")
    with pytest.raises(HTTPException) as error:
        approve(env)
    assert "原文引文" in error.value.detail and "没有子条件" in error.value.detail


def test_edit_clears_approval_and_preserves_logic(env):
    approve(env)
    graph = json.loads(env[1].read_text(encoding="utf-8"))
    spec = graph["clinical_bases"][0]["specification"]
    spec["conditions"]["operator"] = "OR"
    api.edit_basis(env[3], api.BasisEdit(specification=spec, scope_summary="已核对范围"), "REVIEW-TEST.json")
    assert api.list_bases("REVIEW-TEST.json")["items"][0]["status"] == "draft"
    with pytest.raises(HTTPException):
        api.generate_rule(env[3], "REVIEW-TEST.json")


def test_complete_basis_uses_only_reviewed_graph_associations(env):
    graph = json.loads(env[1].read_text(encoding="utf-8"))
    graph["evidences"] = [
        {"id": "EV-SYM", "data_type": "symptom", "symptom_code": "SYM_TEST", "description": "测试症状"},
        {"id": "EV-DRAFT", "data_type": "symptom", "symptom_code": "SYM_DRAFT", "description": "待审症状"},
    ]
    graph["disease_evidence"] = [
        {"disease_code": "I21.0", "evidence_id": "EV-SYM", "required": True, "weight": 1},
        {"disease_code": "I21.0", "evidence_id": "EV-DRAFT", "required": False, "weight": 0.5},
    ]
    graph["clinical_bases"][0]["specification"]["conditions"]["children"] = []
    env[1].write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")

    result = api.complete_basis_from_reviewed_graph(env[3], "REVIEW-TEST.json")

    assert result["changed"] is True
    serialized = json.dumps(result["basis"]["specification"]["conditions"], ensure_ascii=False)
    assert "SYM_TEST" in serialized
    assert "SYM_DRAFT" not in serialized
    assert "尚未全部审核通过" in " ".join(item["reason"] for item in result["skipped"])


def test_complete_basis_does_not_guess_treatment_conditions(env):
    graph = json.loads(env[1].read_text(encoding="utf-8"))
    graph["clinical_bases"][0]["specification"]["rule_type"] = "treatment"
    env[1].write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")

    result = api.complete_basis_from_reviewed_graph(env[3], "REVIEW-TEST.json")

    assert result["changed"] is False
    assert "treatment_links" in result
    assert result["basis"]["specification"]["conditions"] == graph["clinical_bases"][0]["specification"]["conditions"]


def test_generated_basis_can_create_reviewed_graph_revision(env):
    approve(env)
    generated = api.generate_rule(env[3], "REVIEW-TEST.json")
    graph = json.loads(env[1].read_text(encoding="utf-8"))
    graph["evidences"] = [
        {"id": "EV-SYM", "data_type": "symptom", "symptom_code": "SYM_TEST", "description": "测试症状"},
    ]
    graph["disease_evidence"] = [
        {"disease_code": "I21.0", "evidence_id": "EV-SYM", "required": True, "weight": 1},
    ]
    env[1].write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")

    result = api.revise_basis_from_reviewed_graph(env[3], "REVIEW-TEST.json")

    revised = result["basis"]
    assert revised["basis_id"].endswith("-R2")
    assert revised["status"] == "draft"
    assert revised["supersedes_rule_id"] == generated["rule_id"]
    assert "generated_rule_id" not in revised
    assert json.loads(env[2].read_text(encoding="utf-8"))[0]["rule_id"] == generated["rule_id"]


def test_rejected_basis_cannot_generate(env):
    api.review_basis(env[3], api.BasisReview(action="reject", reviewer="审核员", note="原文不足"), "REVIEW-TEST.json")
    with pytest.raises(HTTPException):
        api.generate_rule(env[3], "REVIEW-TEST.json")


def test_unrelated_entity_change_does_not_stale_basis(env):
    approve(env)
    graph = json.loads(env[1].read_text(encoding="utf-8"))
    graph["nodes"][2]["name"] = "另一个症状的新名"
    env[1].write_text(json.dumps(graph), encoding="utf-8")
    assert api.list_bases("REVIEW-TEST.json")["items"][0]["validation"]["status"] == "active"


def test_rejecting_basis_blocks_its_active_generated_rule(env):
    approve(env)
    api.generate_rule(env[3], "REVIEW-TEST.json")
    saved = json.loads(env[2].read_text(encoding="utf-8"))
    saved[0]["status"] = "active"
    env[2].write_text(json.dumps(saved), encoding="utf-8")
    api.review_basis(env[3], api.BasisReview(action="reject", reviewer="复核人", note="原文不支持"), "REVIEW-TEST.json")
    assert json.loads(env[2].read_text(encoding="utf-8"))[0]["status"] == "blocked"


def test_prepare_does_not_recycle_generated_rules(env):
    approve(env)
    api.generate_rule(env[3], "REVIEW-TEST.json")
    assert api.prepare_existing("REVIEW-TEST.json")["count"] == 1


def test_changed_generated_spec_is_a_new_revision(env):
    approve(env)
    api.generate_rule(env[3], "REVIEW-TEST.json")
    graph = env[0]._load_graph("REVIEW-TEST.json")
    old = graph["clinical_bases"][0]
    revised = copy.deepcopy(old["specification"])
    revised["conditions"]["operator"] = "OR"
    stage_bases(graph, [revised], "REVIEW-TEST.json")
    assert len(graph["clinical_bases"]) == 2
    assert graph["clinical_bases"][0]["generated_rule_id"]
    assert graph["clinical_bases"][1]["status"] == "draft"


def test_activation_gate_rechecks_reviewed_basis(env, monkeypatch):
    from src.api.routes import rules as rules_api
    approve(env)
    api.generate_rule(env[3], "REVIEW-TEST.json")
    generated = json.loads(env[2].read_text(encoding="utf-8"))[0]
    graph = env[0]._load_graph("REVIEW-TEST.json")
    graph["clinical_bases"][0]["status"] = "rejected"
    monkeypatch.setattr(rules_api, "_load_graph", lambda filename: graph)
    monkeypatch.setattr(ReviewService, "_load_graph", lambda self, filename: graph)
    with pytest.raises(HTTPException):
        rules_api._ensure_clinical_basis(generated)
