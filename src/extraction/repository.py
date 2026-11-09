"""JSON storage for extraction templates and provider configuration."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from .models import ExtractionTemplate

DATA_DIR = Path(__file__).parent.parent.parent / "data"
TEMPLATES_PATH = DATA_DIR / "templates" / "extraction_templates.json"
CONFIG_PATH = DATA_DIR / "extraction" / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "default_mode": "local",
    "default_template_id": "",
    "llm": {
        "provider_name": "OpenAI Compatible",
        "provider": "openai_compatible",
        "api_format": "openai_chat",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "api_key": "",
        "api_key_env": "OPENAI_API_KEY",
        "host_header": "",
        "anthropic_auth": "x-api-key",
        "anthropic_thinking": "disabled",
        "max_output_tokens": 8192,
        "temperature": 0.0,
        "timeout_seconds": 120,
        "max_chunk_chars": 12000,
        "max_chunks": 80,
    },
}


class ExtractionTemplateRepository:
    def __init__(self, path: Path = TEMPLATES_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []

    def _write(self, items: List[Dict[str, Any]]) -> None:
        self.path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list(self) -> List[ExtractionTemplate]:
        return [ExtractionTemplate(**item) for item in self._read()]

    def list_available(self) -> List[ExtractionTemplate]:
        return [item for item in self.list() if item.status != "retired"]

    def get(self, template_id: str = "", allow_retired: bool = False) -> ExtractionTemplate:
        items = self._read()
        if not items:
            raise HTTPException(500, "解析模板库为空")
        if template_id:
            selected = next((item for item in items if item.get("template_id") == template_id), None)
            if selected is None:
                raise HTTPException(404, "解析模板不存在")
            template = ExtractionTemplate(**selected)
            if template.status != "active" and not allow_retired:
                raise HTTPException(422, "解析模板已停用")
            return template
        default = next((item for item in items if item.get("is_default") and item.get("status", "active") == "active"), None)
        if default:
            return ExtractionTemplate(**default)
        active = [item for item in items if item.get("status", "active") == "active"]
        if not active:
            raise HTTPException(500, "没有可用的活跃解析模板")
        return ExtractionTemplate(**active[0])

    def save(self, template: ExtractionTemplate) -> ExtractionTemplate:
        items = self._read()
        if template.is_default:
            for item in items:
                item["is_default"] = False
        replaced = False
        for index, item in enumerate(items):
            if item.get("template_id") == template.template_id:
                items[index] = template.model_dump()
                replaced = True
                break
        if not replaced:
            items.append(template.model_dump())
        self._write(items)
        return template

    def delete(self, template_id: str) -> Dict[str, str]:
        items = self._read()
        selected = next((item for item in items if item.get("template_id") == template_id), None)
        if selected is None:
            raise HTTPException(404, "解析模板不存在")
        if len(items) == 1:
            raise HTTPException(400, "至少保留一个模板")
        remaining = [item for item in items if item.get("template_id") != template_id]
        if selected.get("is_default"):
            remaining[0]["is_default"] = True
        self._write(remaining)
        return {"deleted": template_id}


class ExtractionConfigRepository:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            self.save_data(DEFAULT_CONFIG)
            return dict(DEFAULT_CONFIG)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        result = dict(DEFAULT_CONFIG)
        result.update(data)
        result["llm"] = {**DEFAULT_CONFIG["llm"], **data.get("llm", {})}
        return result

    def save(self, config: Dict[str, Any]) -> Dict[str, Any]:
        current = self.load()
        current["default_mode"] = config.get("default_mode", current["default_mode"])
        current["default_template_id"] = config.get("default_template_id", current["default_template_id"])
        current["llm"] = {**current["llm"], **config.get("llm", {})}
        self.save_data(current)
        return current

    def save_data(self, data: Dict[str, Any]) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

