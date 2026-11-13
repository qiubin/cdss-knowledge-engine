"""Persistent review queues and hospital terminology mappings.

The active entity dictionaries remain deliberately curated.  New terms found
during extraction are written here first, and only become active after review.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[2]
TERMINOLOGY_DIR = ROOT / "data" / "terminology"
CANDIDATE_PATH = TERMINOLOGY_DIR / "candidates.json"
MAPPING_PATH = TERMINOLOGY_DIR / "hospital_mappings.json"

CANDIDATE_STATUSES = {"pending", "approved", "rejected"}
MAPPING_STATUSES = {"draft", "approved", "retired"}
MAPPING_RELATIONS = {"exact", "narrower", "broader", "related"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(prefix: str, *parts: str) -> str:
    raw = ":".join(str(part).strip().lower() for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16].upper()
    return f"{prefix}-{digest}"


class _JsonListRepository:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"术语数据文件应为列表: {self.path}")
        return [item for item in data if isinstance(item, dict)]

    def _write(self, items: Iterable[Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(list(items), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class TerminologyCandidateRepository(_JsonListRepository):
    """Review queue for extracted terms that are not active entities yet."""

    def __init__(self, path: Path = CANDIDATE_PATH):
        super().__init__(path)

    def list(
        self,
        *,
        status: str = "",
        entity_type: str = "",
        query: str = "",
    ) -> List[Dict[str, Any]]:
        keyword = str(query or "").strip().lower()
        result = []
        for item in self._read():
            if status and item.get("status") != status:
                continue
            if entity_type and item.get("entity_type") != entity_type:
                continue
            haystack = " ".join([
                str(item.get("name") or ""),
                str(item.get("local_code") or ""),
                *map(str, item.get("aliases") or []),
            ]).lower()
            if keyword and keyword not in haystack:
                continue
            result.append(item)
        return sorted(
            result,
            key=lambda item: (item.get("status") != "pending", str(item.get("updated_at") or "")),
            reverse=False,
        )

    def get(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        return next((item for item in self._read() if item.get("candidate_id") == candidate_id), None)

    def upsert(
        self,
        *,
        entity_type: str,
        name: str,
        local_code: str,
        aliases: Iterable[str] = (),
        source_doc_id: str = "",
        match_type: str = "unmatched",
        suggested_mappings: Iterable[Dict[str, Any]] = (),
    ) -> Dict[str, Any]:
        entity_type = str(entity_type or "").strip()
        name = str(name or "").strip()
        candidate_id = _stable_id("TERM", entity_type, name)
        items = self._read()
        existing = next((item for item in items if item.get("candidate_id") == candidate_id), None)
        timestamp = _now()
        if existing is None:
            existing = {
                "candidate_id": candidate_id,
                "entity_type": entity_type,
                "name": name,
                "local_code": str(local_code or ""),
                "aliases": [],
                "source_doc_ids": [],
                "match_type": match_type,
                "suggested_mappings": [],
                "occurrences": 0,
                "status": "pending",
                "review_note": "",
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            items.append(existing)
        existing["aliases"] = sorted(set([
            *map(str, existing.get("aliases") or []),
            *[str(value).strip() for value in aliases if str(value).strip()],
        ]))
        if source_doc_id:
            existing["source_doc_ids"] = sorted(set([
                *map(str, existing.get("source_doc_ids") or []),
                str(source_doc_id),
            ]))
        mappings = list(existing.get("suggested_mappings") or [])
        seen = {(item.get("system"), item.get("code")) for item in mappings}
        for mapping in suggested_mappings:
            key = (mapping.get("system"), mapping.get("code"))
            if key not in seen:
                mappings.append(dict(mapping))
                seen.add(key)
        existing["suggested_mappings"] = mappings
        existing["match_type"] = match_type
        existing["occurrences"] = int(existing.get("occurrences") or 0) + 1
        existing["updated_at"] = timestamp
        self._write(items)
        return existing

    def review(self, candidate_id: str, status: str, note: str = "") -> Dict[str, Any]:
        if status not in CANDIDATE_STATUSES - {"pending"}:
            raise HTTPException(422, "候选术语只能审核为 approved 或 rejected")
        items = self._read()
        item = next((row for row in items if row.get("candidate_id") == candidate_id), None)
        if item is None:
            raise HTTPException(404, f"候选术语 {candidate_id} 不存在")
        item["status"] = status
        item["review_note"] = str(note or "").strip()
        item["updated_at"] = _now()
        self._write(items)
        return item


class HospitalTerminologyMappingRepository(_JsonListRepository):
    """Hospital-local code to canonical entity mapping records."""

    def __init__(self, path: Path = MAPPING_PATH):
        super().__init__(path)

    def list(
        self,
        *,
        hospital_id: str = "",
        entity_type: str = "",
        status: str = "",
    ) -> List[Dict[str, Any]]:
        return [
            item for item in self._read()
            if (not hospital_id or item.get("hospital_id") == hospital_id)
            and (not entity_type or item.get("entity_type") == entity_type)
            and (not status or item.get("status") == status)
        ]

    def lookup(self, hospital_id: str, entity_type: str, local_code: str) -> Optional[str]:
        item = next((
            row for row in self._read()
            if row.get("hospital_id") == hospital_id
            and row.get("entity_type") == entity_type
            and row.get("local_code") == local_code
            and row.get("status") == "approved"
        ), None)
        return str(item.get("canonical_code")) if item and item.get("canonical_code") else None

    def save(self, raw: Dict[str, Any], *, mapping_id: str = "") -> Dict[str, Any]:
        payload = {
            "hospital_id": str(raw.get("hospital_id") or "").strip(),
            "entity_type": str(raw.get("entity_type") or "").strip(),
            "local_code": str(raw.get("local_code") or "").strip(),
            "local_name": str(raw.get("local_name") or "").strip(),
            "canonical_code": str(raw.get("canonical_code") or "").strip(),
            "relation": str(raw.get("relation") or "exact").strip(),
            "status": str(raw.get("status") or "draft").strip(),
            "confidence": raw.get("confidence"),
            "source": str(raw.get("source") or "manual").strip(),
            "review_note": str(raw.get("review_note") or "").strip(),
        }
        required = ["hospital_id", "entity_type", "local_code", "canonical_code"]
        missing = [key for key in required if not payload[key]]
        if missing:
            raise HTTPException(422, f"缺少必要字段: {', '.join(missing)}")
        if payload["status"] not in MAPPING_STATUSES:
            raise HTTPException(422, f"无效映射状态: {payload['status']}")
        if payload["relation"] not in MAPPING_RELATIONS:
            raise HTTPException(422, f"无效映射关系: {payload['relation']}")
        if payload["confidence"] is not None:
            try:
                payload["confidence"] = float(payload["confidence"])
            except (TypeError, ValueError):
                raise HTTPException(422, "confidence 必须是 0 到 1 之间的数字")
            if not 0 <= payload["confidence"] <= 1:
                raise HTTPException(422, "confidence 必须是 0 到 1 之间的数字")

        items = self._read()
        timestamp = _now()
        if mapping_id:
            current = next((item for item in items if item.get("mapping_id") == mapping_id), None)
            if current is None:
                raise HTTPException(404, f"映射 {mapping_id} 不存在")
            created_at = current.get("created_at") or timestamp
            current.update(payload)
            current.update({"created_at": created_at, "updated_at": timestamp})
            saved = current
        else:
            duplicate = next((
                item for item in items
                if item.get("hospital_id") == payload["hospital_id"]
                and item.get("entity_type") == payload["entity_type"]
                and item.get("local_code") == payload["local_code"]
            ), None)
            if duplicate:
                raise HTTPException(409, "该医院、本地编码和实体类型的映射已存在")
            saved = {
                "mapping_id": _stable_id(
                    "MAP", payload["hospital_id"], payload["entity_type"], payload["local_code"]
                ),
                **payload,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            items.append(saved)
        self._write(items)
        return saved
