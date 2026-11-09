"""Managed storage and reference tracking for clinical concept vocabulary."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from fastapi import HTTPException
from pydantic import ValidationError

from src.engine.evaluators.factory import EvaluatorFactory
from src.kg.schema import ConceptDefinition

ROOT = Path(__file__).parent.parent.parent
CONCEPTS_PATH = ROOT / "data" / "vocab" / "concepts.json"


def collect_concept_codes(value: Any) -> List[str]:
    """Collect concept_code fields from arbitrary rule trees or payload maps."""
    result: List[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            code = item.get("concept_code")
            if isinstance(code, str) and code:
                result.append(code)
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return list(dict.fromkeys(result))


class ConceptVocabularyRepository:
    def __init__(self, path: Path = CONCEPTS_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []

    def _write(self, items: List[Dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    def list(self) -> List[ConceptDefinition]:
        return [ConceptDefinition(**item) for item in self._read()]

    def get(self, code: str) -> ConceptDefinition:
        item = next((raw for raw in self._read() if raw.get("code") == code), None)
        if item is None:
            raise HTTPException(404, "概念词汇不存在")
        return ConceptDefinition(**item)

    def find(self, code: str) -> ConceptDefinition | None:
        item = next((raw for raw in self._read() if raw.get("code") == code), None)
        return ConceptDefinition(**item) if item else None

    def save(self, concept: ConceptDefinition, is_new: bool = False) -> ConceptDefinition:
        items = self._read()
        index = next((i for i, item in enumerate(items) if item.get("code") == concept.code), -1)
        if is_new and index >= 0:
            raise HTTPException(409, "概念编码已存在")
        if index == -1 and not is_new:
            raise HTTPException(404, "概念词汇不存在")
        if concept.evaluator_type not in EvaluatorFactory.list_types():
            raise HTTPException(422, f"未注册的执行器类型: {concept.evaluator_type}")
        payload = concept.model_dump()
        if index >= 0:
            items[index] = payload
        else:
            items.append(payload)
        self._write(items)
        return concept

    def delete(self, code: str) -> Dict[str, Any]:
        usage = self.usage(code)
        total = sum(usage["usage"].values())
        if total:
            raise HTTPException(409, f"概念仍被 {total} 处引用，不能删除；请先迁移或停用")
        items = self._read()
        self._write([item for item in items if item.get("code") != code])
        return {"deleted": code}

    def usage(self, code: str) -> Dict[str, Any]:
        self.get(code)
        references: List[Dict[str, str]] = []
        counts = {"template_count": 0, "graph_count": 0, "rule_count": 0, "standard_count": 0}

        data_dir = self.path.parent.parent
        templates_path = data_dir / "templates" / "extraction_templates.json"
        if templates_path.exists():
            templates = json.loads(templates_path.read_text(encoding="utf-8"))
            for template in templates:
                found = self._template_usage(template, code)
                if found:
                    counts["template_count"] += len(found)
                    references.extend({"scope": "模板", "label": template.get("name", ""), "detail": detail} for detail in found)

        for graph_path in sorted((data_dir / "kg").glob("*.json")):
            try:
                graph = json.loads(graph_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for evidence in graph.get("evidences", []):
                if evidence.get("concept_code") == code:
                    counts["graph_count"] += 1
                    references.append({
                        "scope": "图谱",
                        "label": f"{graph_path.name} / {evidence.get('description', '')}",
                        "detail": evidence.get("id", ""),
                    })
            nodes = graph.get("nodes") or []
            if isinstance(nodes, dict):
                nodes = list(nodes.values())
            for node in nodes:
                if node.get("concept_code") == code:
                    counts["graph_count"] += 1
                    references.append({
                        "scope": "图谱节点",
                        "label": f"{graph_path.name} / {node.get('name', '')}",
                        "detail": node.get("id", ""),
                    })

        for rule_path in sorted((data_dir / "rules").glob("*.json")):
            raw_rules = json.loads(rule_path.read_text(encoding="utf-8"))
            if isinstance(raw_rules, dict):
                raw_rules = [raw_rules]
            for rule in raw_rules:
                if code in collect_concept_codes(rule):
                    counts["rule_count"] += 1
                    references.append({"scope": "规则", "label": rule.get("name", ""), "detail": rule.get("rule_id", "")})

        for standard_path in sorted((data_dir / "standards").glob("*.json")):
            standards = json.loads(standard_path.read_text(encoding="utf-8"))
            for standard in standards:
                if standard.get("concept_code") == code:
                    counts["standard_count"] += 1
                    references.append({
                        "scope": "医院标准",
                        "label": standard.get("lab_name") or standard.get("lab_code", ""),
                        "detail": f"{standard.get('hospital_id', '')} / {standard.get('concept_code', '')}",
                    })

        return {
            "code": code,
            "can_delete": not any(counts.values()),
            "usage": counts,
            "references": references,
        }

    def _template_usage(self, template: Dict[str, Any], code: str) -> List[str]:
        found: List[str] = []
        if code in template.get("concept_codes", []):
            found.append("模板概念边界 concept_codes")
        for entity_type in template.get("entity_types", []):
            for entry in entity_type.get("entries", []):
                if entity_type.get("entity_type") == "concept" and entry.get("code") == code:
                    found.append(f"概念实体 {entry.get('name') or entry.get('code')}")
        for relation in template.get("relations", []):
            if relation.get("concept_code") == code:
                found.append(f"关系 {relation.get('name') or relation.get('relation_code')}")
        for attribute in template.get("attributes", []):
            if attribute.get("concept_code") == code:
                found.append(f"属性 {attribute.get('name') or attribute.get('attribute_code')}")
        return found

