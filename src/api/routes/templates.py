"""Extraction template and provider configuration APIs."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from ...extraction.models import (
    ExtractionTemplate,
    TemplateAttribute,
    TemplateEntityEntry,
    TemplateEntityType,
    TemplateRelation,
)
from ...extraction.repository import ExtractionConfigRepository, ExtractionTemplateRepository
from ...domains import normalize_domain
from ...llm import (
    ANTHROPIC_BASE_URL,
    ANTHROPIC_DEFAULT_MODEL,
    LLMError,
    ZHIPU_ANTHROPIC_BASE_URL,
    ZHIPU_ANTHROPIC_BASE_URL_INTL,
    ZHIPU_CODING_BASE_URL,
    ZHIPU_CODING_BASE_URL_INTL,
    ZHIPU_DEFAULT_MODEL,
    chat_json,
)
from ...vocab.entity_vocabulary import (
    EntityVocabularyRepository,
    graph_usage,
    resolve_template,
    template_private_entries,
)
from ...vocab.repository import ConceptVocabularyRepository

router = APIRouter(tags=["extraction-templates"])
templates = ExtractionTemplateRepository()
config_repo = ExtractionConfigRepository()
concept_vocab = ConceptVocabularyRepository()


class TemplatePayload(BaseModel):
    template: Dict[str, Any]


class ConfigPayload(BaseModel):
    config: Dict[str, Any]


class ItemPayload(BaseModel):
    item: Dict[str, Any]


def validate_template(template: ExtractionTemplate) -> ExtractionTemplate:
    if template.status not in {"active", "draft", "retired"}:
        raise HTTPException(422, "模板状态只能是 active、draft 或 retired")
    entity_types = template.allowed_entity_types
    if len(entity_types) != len(set(entity_types)):
        raise HTTPException(422, "模板实体类型编码重复")
    codes = {(item.entity_type, entry.code) for item in template.entity_types for entry in item.entries}
    total_codes = sum(len(item.entries) for item in template.entity_types)
    if len(codes) != total_codes:
        raise HTTPException(422, "同一实体类型下的实体编码必须唯一")
    relation_codes = [item.relation_code for item in template.relations]
    attribute_codes = [item.attribute_code for item in template.attributes]
    if len(relation_codes) != len(set(relation_codes)):
        raise HTTPException(422, "模板关系编码重复")
    if len(attribute_codes) != len(set(attribute_codes)):
        raise HTTPException(422, "模板属性编码重复")

    referenced_concepts = set(template.concept_codes)
    referenced_concepts.update(
        entry.code for item in template.entity_types if item.entity_type == "concept" for entry in item.entries
    )
    referenced_concepts.update(item.concept_code for item in template.relations if item.concept_code)
    referenced_concepts.update(item.concept_code for item in template.attributes if item.concept_code)
    for code in sorted(referenced_concepts):
        concept = concept_vocab.find(code)
        if concept is None:
            raise HTTPException(422, f"概念 {code} 不存在，请先在概念词汇中定义")
        if not concept.is_active:
            raise HTTPException(422, f"概念 {code} 已停用，请先启用或在模板中移除")

    owner_types = {item.source_entity_type for item in template.relations}
    owner_types.update(item.target_entity_type for item in template.relations)
    owner_types.update(item.owner_entity_type for item in template.attributes)
    for entity_type in sorted(owner_types):
        if entity_type not in entity_types:
            raise HTTPException(422, f"关系或属性引用了模板外实体类型 {entity_type}")
    for relation in template.relations:
        source_key = (relation.source_entity_type, relation.source_code)
        if relation.source_code and source_key not in codes:
            raise HTTPException(422, f"关系 {relation.relation_code} 的源实体不存在")
        target_key = (relation.target_entity_type, relation.target_code)
        if relation.target_code and target_key not in codes:
            raise HTTPException(422, f"关系 {relation.relation_code} 的目标实体不存在")
    return template


def _locate(template: ExtractionTemplate, kind: str, key: str):
    if kind == "entity":
        type_code, code = key.split("/", 1)
        owner = next((item for item in template.entity_types if item.entity_type == type_code), None)
        if owner is None:
            raise HTTPException(404, "模板实体类型不存在")
        index = next((i for i, item in enumerate(owner.entries) if item.code == code), -1)
        return owner, owner.entries, index
    values: List[Any]
    if kind == "entity-type":
        values = template.entity_types
        index = next((i for i, item in enumerate(values) if item.entity_type == key), -1)
    elif kind == "relation":
        values = template.relations
        index = next((i for i, item in enumerate(values) if item.relation_code == key), -1)
    else:
        values = template.attributes
        index = next((i for i, item in enumerate(values) if item.attribute_code == key), -1)
    if index < 0:
        raise HTTPException(404, "模板结构项不存在")
    return None, values, index


def _save_default(template: ExtractionTemplate) -> Dict[str, Any]:
    result = templates.save(template).model_dump()
    if template.is_default:
        config = config_repo.load()
        domain = normalize_domain(template.domain)
        if domain == "western":
            config["default_template_id"] = template.template_id
        else:
            domain_defaults = dict(config.get("domain_defaults") or {})
            domain_defaults[domain] = template.template_id
            config["domain_defaults"] = domain_defaults
        config_repo.save_data(config)
    return result


def _validate_and_save(template: ExtractionTemplate) -> Dict[str, Any]:
    """Validate the effective contract (shared vocabulary merged in), then
    persist the raw template so shared entries are not copied into it."""
    validate_template(resolve_template(template))
    return _save_default(template)


def _entity_vocabulary_for(owner):
    """Shared dictionary handle for a vocabulary-backed entity type."""
    return EntityVocabularyRepository(owner.entity_type)


def public_config() -> Dict[str, Any]:
    config = config_repo.load()
    llm = dict(config.get("llm", {}))
    env_name = str(llm.get("api_key_env") or "OPENAI_API_KEY")
    direct_key = str(llm.get("api_key") or "")
    env_key = os.environ.get(env_name, "")
    masked = ""
    if direct_key:
        masked = direct_key[:4] + "****" + direct_key[-4:] if len(direct_key) > 8 else "****"
    llm.pop("api_key", None)
    return {
        **config,
        "llm": {
            **llm,
            "api_key_configured": bool(direct_key or env_key),
            "api_key_source": "config" if direct_key else ("env" if env_key else "none"),
            "api_key_masked": masked,
        },
        "llm_presets": {
            "zhipu": {
                "label": "智谱 Coding Plan · OpenAI Chat",
                "api_format": "openai_chat",
                "base_url": ZHIPU_CODING_BASE_URL,
                "base_url_intl": ZHIPU_CODING_BASE_URL_INTL,
                "model": ZHIPU_DEFAULT_MODEL,
                "hint": "智谱 OpenAI 格式必须使用 /api/coding/paas/v4",
            },
            "zhipu_anthropic": {
                "label": "智谱 Coding Plan · Anthropic Messages",
                "api_format": "anthropic_messages",
                "base_url": ZHIPU_ANTHROPIC_BASE_URL,
                "base_url_intl": ZHIPU_ANTHROPIC_BASE_URL_INTL,
                "model": ZHIPU_DEFAULT_MODEL,
                "hint": "智谱 Anthropic 格式使用 /api/anthropic + /v1/messages",
            },
            "anthropic": {
                "label": "Anthropic 官方 · Messages",
                "api_format": "anthropic_messages",
                "base_url": ANTHROPIC_BASE_URL,
                "model": ANTHROPIC_DEFAULT_MODEL,
                "hint": "官方 Messages API，需要 x-api-key",
            },
            "cc_switch": {
                "label": "CC-SWITCH 本地代理 · 公司网关",
                "api_format": "anthropic_messages",
                "base_url": "http://127.0.0.1:15721",
                "model": "deepseek-flash",
                "api_key_env": "ANTHROPIC_AUTH_TOKEN",
                "anthropic_auth": "bearer",
                "hint": "CC-SWITCH 运行时可用；它将 192.168.3.77:13000 代理为 Anthropic Messages；Key 自动读取 ANTHROPIC_AUTH_TOKEN",
            },
        },
    }


@router.get("/api/templates")
async def list_templates(include_retired: bool = False, domain: Optional[str] = None):
    items = templates.list() if include_retired else templates.list_available()
    if domain:
        normalized = normalize_domain(domain)
        items = [
            item for item in items
            if normalize_domain(item.domain) == normalized
        ]
    return [resolve_template(item).model_dump() for item in items]


@router.post("/api/templates")
async def create_template(payload: TemplatePayload):
    try:
        template = ExtractionTemplate(**payload.template)
    except ValidationError as error:
        raise HTTPException(422, error.errors())
    if any(item.template_id == template.template_id for item in templates.list()):
        raise HTTPException(409, "解析模板 ID 已存在")
    return _validate_and_save(template)


@router.get("/api/templates/{template_id}")
async def get_template(template_id: str):
    return resolve_template(templates.get(template_id, allow_retired=True)).model_dump()


@router.put("/api/templates/{template_id}")
async def update_template(template_id: str, payload: TemplatePayload):
    templates.get(template_id, allow_retired=True)
    try:
        template = ExtractionTemplate(**payload.template)
    except ValidationError as error:
        raise HTTPException(422, error.errors())
    if template.template_id != template_id:
        raise HTTPException(400, "请求路径与模板 ID 不一致")
    return _validate_and_save(template)


@router.get("/api/templates/{template_id}/structure")
async def get_template_structure(template_id: str):
    raw = templates.get(template_id, allow_retired=True)
    template = resolve_template(raw)
    payload = template.model_dump()
    entity_types = []
    for raw_type, resolved_type in zip(raw.entity_types, template.entity_types):
        private_entries = template_private_entries(raw_type)
        private_codes = {entry.code for entry in private_entries}
        item = resolved_type.model_dump()
        item["private_entries"] = [entry.model_dump() for entry in private_entries]
        item["private_entry_count"] = len(private_entries)
        item["shared_entry_count"] = sum(
            1 for entry in resolved_type.entries if entry.code not in private_codes
        )
        entity_types.append(item)
    payload["entity_types"] = entity_types
    return payload


@router.post("/api/templates/{template_id}/entity-types")
async def create_entity_type(template_id: str, payload: ItemPayload):
    template = templates.get(template_id)
    try:
        item = TemplateEntityType(**payload.item)
    except ValidationError as error:
        raise HTTPException(422, error.errors())
    if any(existing.entity_type == item.entity_type for existing in template.entity_types):
        raise HTTPException(409, "模板实体类型已存在")
    template.entity_types.append(item)
    return _validate_and_save(template)


@router.put("/api/templates/{template_id}/entity-types/{entity_type}")
async def update_entity_type(template_id: str, entity_type: str, payload: ItemPayload):
    template = templates.get(template_id)
    _, values, index = _locate(template, "entity-type", entity_type)
    try:
        item = TemplateEntityType(**payload.item)
    except ValidationError as error:
        raise HTTPException(422, error.errors())
    if item.entity_type != entity_type:
        raise HTTPException(400, "实体类型编码不允许修改；请新建类型")
    values[index] = item
    return _validate_and_save(template)


@router.delete("/api/templates/{template_id}/entity-types/{entity_type}")
async def delete_entity_type(template_id: str, entity_type: str):
    template = templates.get(template_id)
    _, values, index = _locate(template, "entity-type", entity_type)
    selected = values[index]
    if selected.entries:
        raise HTTPException(409, "该类型下仍有实体，不能删除")
    if any(item.source_entity_type == entity_type or item.target_entity_type == entity_type for item in template.relations):
        raise HTTPException(409, "该类型仍被关系引用，不能删除")
    if any(item.owner_entity_type == entity_type for item in template.attributes):
        raise HTTPException(409, "该类型仍被属性引用，不能删除")
    values.pop(index)
    return _validate_and_save(template)


def _check_concept_entry(owner, code: str) -> None:
    """Concept-type entries must reference an active concept vocabulary item."""
    if owner.entity_type != "concept":
        return
    concept = concept_vocab.find(code)
    if concept is None:
        raise HTTPException(422, f"概念 {code} 不存在，请先在概念词汇中定义")
    if not concept.is_active:
        raise HTTPException(422, f"概念 {code} 已停用，请先启用")


@router.post("/api/templates/{template_id}/entities")
async def create_template_entity(template_id: str, payload: ItemPayload):
    template = templates.get(template_id)
    entity_type = str(payload.item.get("entity_type") or "")
    owner = next((item for item in template.entity_types if item.entity_type == entity_type), None)
    if owner is None:
        raise HTTPException(422, "实体类型不存在")
    try:
        item = TemplateEntityEntry(**{key: value for key, value in payload.item.items() if key != "entity_type"})
    except ValidationError as error:
        raise HTTPException(422, error.errors())
    if owner.vocabulary:
        # Vocabulary-backed types manage their entities in the shared
        # dictionary so every referencing template benefits at once.
        vocabulary = _entity_vocabulary_for(owner)
        if vocabulary.find(item.code):
            raise HTTPException(409, f"共享词表 {owner.entity_type} 中编码 {item.code} 已存在")
        _check_concept_entry(owner, item.code)
        vocabulary.save(item, is_new=True)
        return resolve_template(template).model_dump()
    if any(existing.code == item.code for existing in owner.entries):
        raise HTTPException(409, "该实体类型下编码已存在")
    owner.entries.append(item)
    return _validate_and_save(template)


@router.put("/api/templates/{template_id}/entities/{entity_type}/{code}")
async def update_template_entity(template_id: str, entity_type: str, code: str, payload: ItemPayload):
    template = templates.get(template_id)
    owner, values, index = _locate(template, "entity", f"{entity_type}/{code}")
    try:
        item = TemplateEntityEntry(**{key: value for key, value in payload.item.items() if key != "entity_type"})
    except ValidationError as error:
        raise HTTPException(422, error.errors())
    if item.code != code:
        raise HTTPException(400, "实体编码不允许修改；请新建实体")
    if owner is not None and owner.vocabulary:
        vocabulary = _entity_vocabulary_for(owner)
        if vocabulary.find(item.code) is not None:
            _check_concept_entry(owner, item.code)
            vocabulary.save(item)
            return resolve_template(template).model_dump()
    if index < 0:
        raise HTTPException(404, "模板结构项不存在")
    values[index] = item
    return _validate_and_save(template)


@router.delete("/api/templates/{template_id}/entities/{entity_type}/{code}")
async def delete_template_entity(template_id: str, entity_type: str, code: str):
    template = templates.get(template_id)
    owner, values, index = _locate(template, "entity", f"{entity_type}/{code}")
    if any(
        item.source_code == code or item.target_code == code
        for item in template.relations
        if item.source_entity_type == entity_type or item.target_entity_type == entity_type
    ):
        raise HTTPException(409, "该实体仍被关系引用，不能删除")
    if owner is not None and owner.vocabulary:
        vocabulary = _entity_vocabulary_for(owner)
        if vocabulary.find(code) is not None:
            referencing = graph_usage(owner.entity_type, code)
            if referencing:
                raise HTTPException(409, f"共享实体 {code} 已被图谱 {'、'.join(referencing)} 引用，不能删除")
            vocabulary.delete(code)
            return {"deleted": f"{entity_type}/{code}"}
    if index < 0:
        raise HTTPException(404, "模板结构项不存在")
    values.pop(index)
    return _validate_and_save(template)


@router.post("/api/templates/{template_id}/relations")
async def create_template_relation(template_id: str, payload: ItemPayload):
    template = templates.get(template_id)
    try:
        item = TemplateRelation(**payload.item)
    except ValidationError as error:
        raise HTTPException(422, error.errors())
    if any(existing.relation_code == item.relation_code for existing in template.relations):
        raise HTTPException(409, "模板关系编码已存在")
    template.relations.append(item)
    return _validate_and_save(template)


@router.put("/api/templates/{template_id}/relations/{relation_code}")
async def update_template_relation(template_id: str, relation_code: str, payload: ItemPayload):
    template = templates.get(template_id)
    _, values, index = _locate(template, "relation", relation_code)
    try:
        item = TemplateRelation(**payload.item)
    except ValidationError as error:
        raise HTTPException(422, error.errors())
    if item.relation_code != relation_code:
        raise HTTPException(400, "关系编码不允许修改；请新建关系")
    values[index] = item
    return _validate_and_save(template)


@router.delete("/api/templates/{template_id}/relations/{relation_code}")
async def delete_template_relation(template_id: str, relation_code: str):
    template = templates.get(template_id)
    _, values, index = _locate(template, "relation", relation_code)
    values.pop(index)
    return _validate_and_save(template)


@router.post("/api/templates/{template_id}/attributes")
async def create_template_attribute(template_id: str, payload: ItemPayload):
    template = templates.get(template_id)
    try:
        item = TemplateAttribute(**payload.item)
    except ValidationError as error:
        raise HTTPException(422, error.errors())
    if any(existing.attribute_code == item.attribute_code for existing in template.attributes):
        raise HTTPException(409, "模板属性编码已存在")
    template.attributes.append(item)
    return _validate_and_save(template)


@router.put("/api/templates/{template_id}/attributes/{attribute_code}")
async def update_template_attribute(template_id: str, attribute_code: str, payload: ItemPayload):
    template = templates.get(template_id)
    _, values, index = _locate(template, "attribute", attribute_code)
    try:
        item = TemplateAttribute(**payload.item)
    except ValidationError as error:
        raise HTTPException(422, error.errors())
    if item.attribute_code != attribute_code:
        raise HTTPException(400, "属性编码不允许修改；请新建属性")
    values[index] = item
    return _validate_and_save(template)


@router.delete("/api/templates/{template_id}/attributes/{attribute_code}")
async def delete_template_attribute(template_id: str, attribute_code: str):
    template = templates.get(template_id)
    _, values, index = _locate(template, "attribute", attribute_code)
    values.pop(index)
    return _validate_and_save(template)


@router.delete("/api/templates/{template_id}")
async def delete_template(template_id: str):
    result = templates.delete(template_id)
    config = config_repo.load()
    if config.get("default_template_id") == template_id:
        config["default_template_id"] = ""
        config_repo.save_data(config)
    return result


@router.post("/api/templates/{template_id}/default")
async def set_default_template(template_id: str):
    template = templates.get(template_id)
    template.is_default = True
    saved = templates.save(template).model_dump()
    config = config_repo.load()
    config["default_template_id"] = template.template_id
    config_repo.save_data(config)
    return saved


@router.get("/api/extraction/config")
async def get_extraction_config():
    return public_config()


@router.put("/api/extraction/config")
async def update_extraction_config(payload: ConfigPayload):
    incoming = payload.config
    mode = incoming.get("default_mode", public_config()["default_mode"])
    if mode not in {"local", "llm", "auto"}:
        raise HTTPException(422, "default_mode 只能是 local、llm 或 auto")
    template_id = str(incoming.get("default_template_id") or "")
    if template_id:
        active_template = templates.get(template_id)
        if not active_template.is_default:
            active_template.is_default = True
            templates.save(active_template)
    llm = incoming.get("llm")
    if llm is not None and not isinstance(llm, dict):
        raise HTTPException(422, "llm 配置必须是对象")
    if isinstance(llm, dict) and "api_key" in llm:
        value = str(llm.get("api_key") or "")
        if value == "__KEEP__":
            llm.pop("api_key", None)
        elif value == "__CLEAR__":
            llm["api_key"] = ""
        else:
            llm["api_key"] = value.strip()
    config_repo.save(incoming)
    return public_config()


@router.post("/api/extraction/test-llm")
async def test_llm_config():
    """Send a minimal request to verify address/key/model without extraction."""
    config = config_repo.load()
    settings = dict(config.get("llm", {}))
    provider = str(settings.get("provider") or "openai_compatible").lower()
    try:
        result, meta = await chat_json(settings, [
            {"role": "system", "content": "Return a JSON object."},
            {"role": "user", "content": 'Return {"ok": true} and nothing else.'},
        ], max_tokens=512, with_meta=True)
    except LLMError as error:
        raise HTTPException(
            422,
            f"{error} | provider={provider} | base_url={settings.get('base_url')} "
            f"| model={settings.get('model')} "
            f"| key_source={'config' if settings.get('api_key') else 'env'}",
        )
    response_model = str(meta.get("model") or settings.get("model") or "")
    return {
        "ok": True,
        "provider": provider,
        "model": response_model,
        "configured_model": settings.get("model"),
        "response_model": response_model,
        "base_url": settings.get("base_url"),
        "response_meta": meta,
        "response_keys": sorted(result.keys()),
    }


@router.get("/api/extraction/providers")
async def list_providers():
    config = public_config()
    return [
        {"value": "local", "label": "本地模板解析", "available": True},
        {
            "value": "llm",
            "label": "大模型解析",
            "available": bool(config.get("llm", {}).get("api_key_configured")),
            "model": config.get("llm", {}).get("model"),
        },
        {
            "value": "auto",
            "label": "一键自动生成（图谱+规则）",
            "available": bool(config.get("llm", {}).get("api_key_configured")),
            "model": config.get("llm", {}).get("model"),
        },
    ]
