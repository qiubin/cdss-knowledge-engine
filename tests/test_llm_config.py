"""LLM configuration and endpoint resolution tests."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes import templates as template_routes
from src.extraction.repository import ExtractionConfigRepository
from src.llm.client import (
    ZHIPU_ANTHROPIC_BASE_URL,
    ZHIPU_CODING_BASE_URL,
    anthropic_endpoint_url,
    anthropic_headers,
    endpoint_url,
    provider_name,
    request_headers,
    resolve_api_format,
    resolve_api_key,
)
from fastapi.testclient import TestClient
from src.api.main import app


class LLMConfigTest(unittest.TestCase):
    def test_endpoint_url_normalization(self):
        self.assertEqual(
            endpoint_url("https://api.openai.com/v1"),
            "https://api.openai.com/v1/chat/completions",
        )
        self.assertEqual(
            endpoint_url("https://api.openai.com/v1/chat/completions"),
            "https://api.openai.com/v1/chat/completions",
        )

    def test_direct_key_wins_over_env(self):
        self.assertEqual(resolve_api_key({"api_key": "direct-key"}), "direct-key")

    def test_request_host_header(self):
        headers = request_headers({"host_header": "localhost"}, "app-key")
        self.assertEqual(headers["Host"], "localhost")
        self.assertEqual(headers["Authorization"], "Bearer app-key")

    def test_zhipu_coding_plan_endpoint(self):
        self.assertEqual(
            endpoint_url(ZHIPU_CODING_BASE_URL),
            "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions",
        )
        self.assertEqual(provider_name({"provider": "zhipu"}), "zhipu")
        self.assertEqual(provider_name({"provider": "GLM"}), "glm")

    def test_anthropic_messages_endpoint_and_headers(self):
        self.assertEqual(
            anthropic_endpoint_url(ZHIPU_ANTHROPIC_BASE_URL),
            "https://open.bigmodel.cn/api/anthropic/v1/messages",
        )
        self.assertEqual(
            anthropic_endpoint_url("https://api.anthropic.com/v1"),
            "https://api.anthropic.com/v1/messages",
        )
        headers = anthropic_headers({"anthropic_auth": "x-api-key"}, "key")
        self.assertEqual(headers["x-api-key"], "key")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertNotIn("Authorization", headers)
        both = anthropic_headers({"anthropic_auth": "both"}, "key")
        self.assertEqual(both["Authorization"], "Bearer key")
        self.assertEqual(
            resolve_api_format({"provider": "zhipu_anthropic"}),
            "anthropic_messages",
        )
        self.assertEqual(
            resolve_api_format({"provider": "zhipu", "api_format": "anthropic_messages"}),
            "anthropic_messages",
        )
        self.assertEqual(
            resolve_api_format({"provider": "cc_switch"}),
            "anthropic_messages",
        )

    def test_public_config_masks_direct_key(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "config.json"
        repository = ExtractionConfigRepository(path)
        repository.save_data({
            "default_mode": "auto",
            "default_template_id": "UNIVERSAL_GUIDELINE_V1",
            "llm": {
                "base_url": "https://example.test/v1",
                "model": "test-model",
                "api_key": "sk-1234567890abcdef",
                "api_key_env": "OPENAI_API_KEY",
            },
        })
        original = template_routes.config_repo
        template_routes.config_repo = repository
        self.addCleanup(setattr, template_routes, "config_repo", original)
        public = template_routes.public_config()
        self.assertNotIn("api_key", public["llm"])
        self.assertTrue(public["llm"]["api_key_configured"])
        self.assertEqual(public["llm"]["api_key_source"], "config")
        self.assertIn("****", public["llm"]["api_key_masked"])
        # Stored config still has the key for the provider call.
        self.assertEqual(repository.load()["llm"]["api_key"], "sk-1234567890abcdef")

    def test_config_api_saves_and_masks_key(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repository = ExtractionConfigRepository(Path(tmp.name) / "config.json")
        original = template_routes.config_repo
        template_routes.config_repo = repository
        self.addCleanup(setattr, template_routes, "config_repo", original)
        client = TestClient(app)

        saved = client.put("/api/extraction/config", json={"config": {
            "default_mode": "auto",
            "llm": {
                "base_url": "https://example.test/v1",
                "model": "test-model",
                "api_key": "sk-abcdefghijklmnop",
                "api_key_env": "OPENAI_API_KEY",
            },
        }}).json()
        self.assertNotIn("api_key", saved["llm"])
        self.assertTrue(saved["llm"]["api_key_configured"])
        self.assertIn("****", saved["llm"]["api_key_masked"])
        self.assertEqual(saved["default_mode"], "auto")

        cleared = client.put("/api/extraction/config", json={"config": {
            "default_mode": "auto",
            "llm": {"api_key": "__CLEAR__"},
        }}).json()
        self.assertFalse(cleared["llm"]["api_key_configured"])


if __name__ == "__main__":
    unittest.main()
