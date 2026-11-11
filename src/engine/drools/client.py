# src/engine/drools/client.py
"""HTTP client for the Drools matching sidecar."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx


class DroolsClientError(RuntimeError):
    pass


class DroolsClient:
    def __init__(self, base_url: str, timeout_seconds: float = 5.0, transport: Optional[httpx.BaseTransport] = None):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                return client.request(method, f"{self._base_url}{path}", **kwargs)
        except Exception as exc:
            raise DroolsClientError(f"sidecar 调用失败: {exc}") from exc

    def health(self) -> Dict[str, Any]:
        response = self._request("GET", "/health")
        if response.status_code != 200:
            raise DroolsClientError(f"sidecar /health 返回 HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise DroolsClientError(f"sidecar /health 响应不是 JSON: {exc}") from exc

    def execute(self, drl: str, facts: List[Dict[str, str]]) -> List[str]:
        response = self._request("POST", "/execute", json={"drl": drl, "facts": facts})
        if response.status_code != 200:
            raise DroolsClientError(
                f"sidecar /execute 返回 HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise DroolsClientError(f"sidecar /execute 响应不是 JSON: {exc}") from exc
        return [str(item) for item in (data.get("matched") or [])]
