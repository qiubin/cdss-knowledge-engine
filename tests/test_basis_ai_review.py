import asyncio
import json
import pytest
from fastapi import HTTPException
from src.api.routes import clinical_basis as api
from src.llm import LLMError
from tests.test_clinical_basis import env


@pytest.fixture(autouse=True)
def model_settings(monkeypatch):
    monkeypatch.setattr(api.ExtractionConfigRepository, "load", lambda self: {"llm": {
        "model": "test-model", "api_key": "test-only-key", "base_url": "https://example.invalid/v1"}})


def advice():
    return {"assessment": "no_obvious_issue", "summary": "请核对原文中的适用范围",
            "findings": [{"area": "适用范围", "problem": "需确认", "evidence": "测试原文", "suggestion": "对照指南确认"}],
            "suggested_logic": "保持原有逻辑，核对限制条件", "reviewer_checklist": ["核对范围"]}


def test_ai_advice_does_not_change_basis_or_rule_and_can_be_reopened(env, monkeypatch):
    before_graph = env[1].read_bytes()
    before_rules = env[2].read_bytes()
    captured = []
    async def fake(settings, messages, **kwargs):
        captured.extend(messages)
        return advice()
    monkeypatch.setattr(api, "chat_json", fake)
    result = asyncio.run(api.request_ai_review(env[3], "REVIEW-TEST.json"))
    assert result["report"]["advice"]["assessment"] == "no_obvious_issue"
    assert "测试症状作为判断依据" in captured[1]["content"]
    assert env[1].read_bytes() == before_graph
    assert env[2].read_bytes() == before_rules
    assert api.get_ai_review(env[3], "REVIEW-TEST.json")["report"]["stale"] is False
    graph = json.loads(env[1].read_text(encoding="utf-8"))
    graph["clinical_bases"][0]["specification"]["conditions"]["operator"] = "OR"
    env[1].write_text(json.dumps(graph), encoding="utf-8")
    assert api.get_ai_review(env[3], "REVIEW-TEST.json")["report"]["stale"] is True


def test_missing_source_overrides_model_optimism(env, monkeypatch):
    original = api.check_basis
    def missing(*args):
        result = original(*args)
        result["provenance"] = {"status": "unavailable", "text": ""}
        return result
    async def fake(*args, **kwargs):
        return advice()
    monkeypatch.setattr(api, "check_basis", missing)
    monkeypatch.setattr(api, "chat_json", fake)
    result = asyncio.run(api.request_ai_review(env[3], "REVIEW-TEST.json"))
    assert result["report"]["advice"]["assessment"] == "insufficient_evidence"


def test_model_error_does_not_expose_credentials(env, monkeypatch):
    async def failing(*args, **kwargs):
        raise LLMError("secret-credential")
    monkeypatch.setattr(api, "chat_json", failing)
    with pytest.raises(HTTPException) as error:
        asyncio.run(api.request_ai_review(env[3], "REVIEW-TEST.json"))
    assert error.value.status_code == 502
    assert "secret-credential" not in error.value.detail


def test_changed_dependency_review_status_invalidates_advice(env, monkeypatch):
    async def fake(*args, **kwargs):
        return advice()
    monkeypatch.setattr(api, "chat_json", fake)
    asyncio.run(api.request_ai_review(env[3], "REVIEW-TEST.json"))
    graph = json.loads(env[1].read_text(encoding="utf-8"))
    graph["nodes"][1]["review_status"] = "draft"
    env[1].write_text(json.dumps(graph), encoding="utf-8")
    assert api.get_ai_review(env[3], "REVIEW-TEST.json")["report"]["stale"] is True


def test_concurrent_content_change_marks_advice_stale(env, monkeypatch):
    async def fake(*args, **kwargs):
        graph = json.loads(env[1].read_text(encoding="utf-8"))
        graph["clinical_bases"][0]["scope_summary"] = "已修改范围"
        env[1].write_text(json.dumps(graph), encoding="utf-8")
        return advice()
    monkeypatch.setattr(api, "chat_json", fake)
    result = asyncio.run(api.request_ai_review(env[3], "REVIEW-TEST.json"))
    assert result["report"]["stale"] is True
    assert not api.ai_report_path("REVIEW-TEST.json", env[3]).exists()


def test_missing_key_has_actionable_error_before_model_call(env, monkeypatch):
    monkeypatch.setattr(api, "resolve_api_key", lambda settings: "")
    async def must_not_call(*args, **kwargs):
        raise AssertionError("model should not be called")
    monkeypatch.setattr(api, "chat_json", must_not_call)
    with pytest.raises(HTTPException) as error:
        asyncio.run(api.request_ai_review(env[3], "REVIEW-TEST.json"))
    assert error.value.status_code == 422
    assert "未配置模型API Key" in error.value.detail


@pytest.mark.parametrize("status, expected", [(401, "认证失败"), (403, "无权"), (429, "额度不足"), (404, "不存在")])
def test_provider_errors_are_actionable_and_redacted(status, expected):
    from src.review.ai_review import explain_model_error
    result = explain_model_error(LLMError(f"HTTP {status} @ https://host/?token=secret-credential"))
    assert expected in result
    assert "secret-credential" not in result
