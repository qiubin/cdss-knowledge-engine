# tests/test_drools_client.py
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import httpx
import pytest

from src.engine.drools.client import DroolsClient, DroolsClientError


def _client(handler) -> DroolsClient:
    return DroolsClient("http://sidecar.test", timeout_seconds=2, transport=httpx.MockTransport(handler))


def test_execute_returns_matched_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/execute"
        payload = json.loads(request.content)
        assert payload["drl"].startswith("package cdss;")
        assert payload["facts"] == [{"ruleId": "R1", "code": "a0"}]
        return httpx.Response(200, json={"matched": ["R1", "R2"]})

    client = _client(handler)
    assert client.execute("package cdss;...", [{"ruleId": "R1", "code": "a0"}]) == ["R1", "R2"]


def test_health_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    assert _client(handler).health() == {"status": "ok"}


def test_non_200_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(DroolsClientError):
        _client(handler).execute("...", [])


def test_connection_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(DroolsClientError):
        _client(handler).health()
