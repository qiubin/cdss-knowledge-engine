"""Provider-neutral LLM client for guideline extraction.

Supports any OpenAI-compatible ``/chat/completions`` endpoint (OpenAI, Ark,
DeepSeek, Qwen, local gateways).  The API key may come from explicit config or
an environment variable; it is never logged.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx


class LLMError(RuntimeError):
    pass


ZHIPU_CODING_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"
ZHIPU_CODING_BASE_URL_INTL = "https://api.z.ai/api/coding/paas/v4"
ZHIPU_ANTHROPIC_BASE_URL = "https://open.bigmodel.cn/api/anthropic"
ZHIPU_ANTHROPIC_BASE_URL_INTL = "https://api.z.ai/api/anthropic"
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ZHIPU_DEFAULT_MODEL = "glm-5.3-flash"
ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5"


def provider_name(settings: Dict[str, Any]) -> str:
    return str(settings.get("provider") or "openai_compatible").strip().lower()


def resolve_api_format(settings: Dict[str, Any]) -> str:
    explicit = str(settings.get("api_format") or "").strip().lower()
    if explicit:
        return explicit
    provider = provider_name(settings)
    if provider in {"anthropic", "zhipu_anthropic", "cc_switch"}:
        return "anthropic_messages"
    return "openai_chat"


def validate_api_key(value: Any) -> str:
    """Reject labels/Chinese/whitespace accidentally pasted into the key field."""
    key = str(value or "").strip()
    if not key:
        return ""
    if any(ord(char) < 33 or ord(char) > 126 for char in key):
        raise LLMError(
            "API Key 格式无效：包含空格、中文或换行。请只粘贴密钥本身，"
            "不要粘贴说明文字或界面标签"
        )
    return key


def _cc_switch_token() -> str:
    """Read the token CC-SWITCH writes into ~/.claude/settings.json."""
    try:
        path = Path.home() / ".claude" / "settings.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return validate_api_key((data.get("env") or {}).get("ANTHROPIC_AUTH_TOKEN"))
    except (OSError, ValueError, TypeError):
        return ""


def resolve_api_key(settings: Dict[str, Any]) -> str:
    provider = provider_name(settings)
    env_name = str(settings.get("api_key_env") or "OPENAI_API_KEY")
    if provider == "cc_switch":
        # CC-SWITCH injects ANTHROPIC_AUTH_TOKEN into the environment; prefer
        # it over a stale key saved for another provider.
        env_key = validate_api_key(os.environ.get(env_name, ""))
        if env_key:
            return env_key
        if env_name == "ANTHROPIC_AUTH_TOKEN":
            claude_key = _cc_switch_token()
            if claude_key:
                return claude_key
    direct = validate_api_key(settings.get("api_key"))
    if direct:
        return direct
    return validate_api_key(os.environ.get(env_name, ""))


def endpoint_url(base_url: str) -> str:
    url = str(base_url or "").strip().rstrip("/")
    if not url:
        raise LLMError("大模型服务地址未配置")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def anthropic_endpoint_url(base_url: str) -> str:
    url = str(base_url or "").strip().rstrip("/")
    if not url:
        raise LLMError("Anthropic 服务地址未配置")
    if url.endswith("/v1/messages"):
        return url
    if url.endswith("/v1"):
        return f"{url}/messages"
    return f"{url}/v1/messages"


def anthropic_headers(settings: Dict[str, Any], api_key: str) -> Dict[str, str]:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    auth_mode = str(settings.get("anthropic_auth") or "x-api-key").strip().lower()
    if auth_mode in {"bearer", "both"}:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def request_headers(settings: Dict[str, Any], api_key: str) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    host = str(settings.get("host_header") or "").strip()
    if host:
        headers["Host"] = host
    return headers


def parse_json_response(text: str) -> Dict[str, Any]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise LLMError(
                f"大模型响应不是有效 JSON（{exc.msg}），输出可能被截断；"
                "可减小单次字符上限或提高最大输出 tokens"
            ) from exc
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError as inner:
            raise LLMError(
                f"大模型响应 JSON 格式错误（{inner.msg}），输出可能被截断；"
                "可减小单次字符上限或提高最大输出 tokens"
            ) from inner
    if not isinstance(value, dict):
        raise LLMError("大模型响应 JSON 根节点必须是对象")
    return value


async def _anthropic_chat_json(
    settings: Dict[str, Any],
    messages: List[Dict[str, str]],
    *,
    max_tokens: Optional[int] = None,
    with_meta: bool = False,
) -> Any:
    api_key = resolve_api_key(settings)
    if not api_key:
        raise LLMError("未配置 Anthropic API Key")
    model = str(settings.get("model") or "").strip()
    if not model:
        raise LLMError("Anthropic model 未配置")
    system_parts: List[str] = []
    converted: List[Dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or "")
        if role == "system":
            system_parts.append(content)
        else:
            converted.append({
                "role": "assistant" if role == "assistant" else "user",
                "content": content,
            })
    body: Dict[str, Any] = {
        "model": model,
        "max_tokens": int(max_tokens or settings.get("max_output_tokens", 8192)),
        "messages": converted,
        "temperature": float(settings.get("temperature", 0)),
    }
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    # GLM reasoning models spend output budget on a thinking block first.
    # Disable it for structured JSON extraction unless explicitly requested.
    if provider_name(settings) in {"zhipu", "zhipu_anthropic", "glm", "bigmodel"}:
        if str(settings.get("anthropic_thinking") or "disabled").lower() != "auto":
            body["thinking"] = {"type": "disabled"}
    url = anthropic_endpoint_url(str(settings.get("base_url") or ANTHROPIC_BASE_URL))
    timeout = float(settings.get("timeout_seconds", 120))

    async def _post(payload: Dict[str, Any]) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url, headers=anthropic_headers(settings, api_key), json=payload
            )
        if response.status_code >= 400:
            detail = response.text.strip()[:500] or "(空响应体)"
            request_id = response.headers.get("request-id") or response.headers.get("x-request-id") or ""
            hint = ""
            if response.status_code == 401:
                hint = "；Anthropic API Key 无效或无权限"
            elif response.status_code == 403:
                hint = "；Key 无该模型权限，或 Coding Plan 端点不匹配"
            elif response.status_code == 404:
                hint = "；检查 base_url，Anthropic Messages 需要 /v1/messages"
            elif response.status_code == 429:
                hint = "；请求频率或额度超限"
            suffix = f"，request_id={request_id}" if request_id else ""
            raise LLMError(
                f"Anthropic 请求失败 HTTP {response.status_code} @ {url}: {detail}{suffix}{hint}"
            )
        return response.json()

    data = await _post(body)
    texts = _anthropic_texts(data)
    if not texts and data.get("stop_reason") == "max_tokens":
        retry = dict(body)
        retry["max_tokens"] = min(16384, int(body.get("max_tokens") or 4096) * 2)
        if provider_name(settings) in {"zhipu", "zhipu_anthropic", "glm", "bigmodel"}:
            retry["thinking"] = {"type": "disabled"}
        data = await _post(retry)
        texts = _anthropic_texts(data)
    if not texts:
        raise LLMError(
            "Anthropic 响应中没有 text 内容块（可能只返回了 thinking，"
            "或 max_output_tokens 不足）；请提高最大输出 tokens 或关闭 thinking"
        )
    payload = parse_json_response("\n".join(texts))
    if with_meta:
        return payload, {
            "model": data.get("model") or model,
            "id": data.get("id") or "",
            "usage": data.get("usage"),
            "stop_reason": data.get("stop_reason"),
        }
    return payload


def _anthropic_texts(data: Dict[str, Any]) -> List[str]:
    content = data.get("content")
    texts: List[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(str(block.get("text") or ""))
    elif isinstance(content, str):
        texts.append(content)
    return texts


async def chat_json(
    settings: Dict[str, Any],
    messages: List[Dict[str, str]],
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    with_meta: bool = False,
) -> Any:
    """Call an OpenAI-compatible or Anthropic Messages endpoint."""
    provider = provider_name(settings)
    api_format = resolve_api_format(settings)
    if api_format == "anthropic_messages":
        prepared = dict(settings)
        provider_base = str(prepared.get("base_url") or "").strip()
        if not provider_base:
            if provider in {"zhipu", "zhipu_anthropic", "glm", "bigmodel"}:
                prepared["base_url"] = ZHIPU_ANTHROPIC_BASE_URL
            elif provider == "anthropic":
                prepared["base_url"] = ANTHROPIC_BASE_URL
        return await _anthropic_chat_json(
            prepared, messages, max_tokens=max_tokens, with_meta=with_meta
        )
    if provider in {"zhipu", "zhipu_coding", "glm", "bigmodel", "zhipu_anthropic"}:
        settings = dict(settings)
        settings["base_url"] = str(settings.get("base_url") or ZHIPU_CODING_BASE_URL)
    api_key = resolve_api_key(settings)
    if not api_key:
        raise LLMError(
            "未配置大模型 API Key：请在解析配置中填写 API Key，或设置 "
            f"{settings.get('api_key_env') or 'OPENAI_API_KEY'} 环境变量"
        )
    model = str(settings.get("model") or "").strip()
    if not model:
        raise LLMError("大模型 model 未配置")
    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": float(
            settings.get("temperature", 0) if temperature is None else temperature
        ),
        "response_format": {"type": "json_object"},
    }
    if max_tokens:
        body["max_tokens"] = int(max_tokens)
    timeout = float(settings.get("timeout_seconds", 120))
    url = endpoint_url(str(settings.get("base_url") or ""))

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            headers=request_headers(settings, api_key),
            json=body,
        )
        if response.status_code >= 400:
            # Some providers reject response_format; retry without it.
            body.pop("response_format", None)
            response = await client.post(
                url,
                headers=request_headers(settings, api_key),
                json=body,
            )
        if response.status_code >= 400:
            detail = response.text.strip()[:500] or "(空响应体)"
            request_id = response.headers.get("x-request-id") or response.headers.get("x-oneapi-request-id") or ""
            content_type = response.headers.get("content-type", "")
            server = response.headers.get("server", "")
            hint = ""
            if "text/html" in content_type:
                hint = "；该地址返回网页 HTML，通常是管理控制台而不是 OpenAI 兼容 API，请填写 API endpoint（常见以 /v1 结尾）"
            elif response.status_code == 502 and not response.text.strip():
                hint = "；网关上游不可用，或地址/端口指向了网页控制台"
            suffix = f"，request_id={request_id}" if request_id else ""
            raise LLMError(
                f"大模型请求失败 HTTP {response.status_code} @ {url}: {detail}"
                f"{suffix}，server={server or 'unknown'}{hint}"
            )
        data = response.json()

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("大模型响应结构不符合 OpenAI chat/completions 约定") from exc
    payload = parse_json_response(content)
    if with_meta:
        return payload, {
            "model": data.get("model") or model,
            "id": data.get("id") or "",
            "usage": data.get("usage"),
            "finish_reason": data["choices"][0].get("finish_reason"),
        }
    return payload
