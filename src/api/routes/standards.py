"""Lab interpretation standard CRUD API."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/standards", tags=["standards"])

STANDARDS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "standards"


def _standard_sort_key(item: Dict) -> tuple:
    return (
        str(item.get("lab_code") or ""),
        str(item.get("concept_code") or ""),
        str(item.get("context_gender") or ""),
        item.get("context_age_min") or 0,
        item.get("context_age_max") or 0,
    )


def _load_hospital(hospital_id: str) -> List[Dict]:
    path = STANDARDS_DIR / f"{hospital_id}.json"
    if path.exists():
        items = json.loads(path.read_text(encoding="utf-8"))
        items.sort(key=_standard_sort_key)
        return items
    return []


def _save_hospital(hospital_id: str, items: List[Dict]):
    STANDARDS_DIR.mkdir(parents=True, exist_ok=True)
    path = STANDARDS_DIR / f"{hospital_id}.json"
    items.sort(key=_standard_sort_key)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


@router.get("/hospitals")
async def list_hospitals():
    hospitals = []
    for f in STANDARDS_DIR.glob("*.json"):
        items = json.loads(f.read_text(encoding="utf-8"))
        if items:
            hospitals.append({
                "hospital_id": items[0].get("hospital_id", f.stem),
                "standard_count": len(items),
            })
    return hospitals


@router.get("/all")
async def list_all_standards():
    """Return every hospital's standard rows for dashboard-level audit views."""
    hospitals = []
    total_count = 0
    active_count = 0
    for f in sorted(STANDARDS_DIR.glob("*.json")):
        items = json.loads(f.read_text(encoding="utf-8"))
        if not items:
            continue
        hospital_id = items[0].get("hospital_id", f.stem)
        hospitals.append({
            "hospital_id": hospital_id,
            "standard_count": len(items),
            "active_count": sum(item.get("status") == "active" for item in items),
            "standards": items,
        })
        total_count += len(items)
        active_count += sum(item.get("status") == "active" for item in items)
    return {
        "total_count": total_count,
        "active_count": active_count,
        "hospital_count": len(hospitals),
        "hospitals": hospitals,
    }


@router.get("/{hospital_id}")
async def get_hospital_standards(hospital_id: str):
    return _load_hospital(hospital_id)


@router.post("/{hospital_id}")
async def add_standard(hospital_id: str, standard: Dict[str, Any]):
    items = _load_hospital(hospital_id)
    standard["hospital_id"] = hospital_id
    if standard.get("status") != "draft":
        standard["status"] = "draft"
    standard.setdefault("version", 1)
    standard.setdefault("ref_source", "STATIC")
    standard.setdefault("operator", ">")
    items.append(standard)
    _save_hospital(hospital_id, items)
    return standard


@router.put("/{hospital_id}/{index}")
async def update_standard(hospital_id: str, index: int, standard: Dict[str, Any]):
    items = _load_hospital(hospital_id)
    if index < 0 or index >= len(items):
        raise HTTPException(404, "标准条目未找到")
    standard["hospital_id"] = hospital_id
    items[index] = standard
    _save_hospital(hospital_id, items)
    return standard


@router.delete("/{hospital_id}/{index}")
async def delete_standard(hospital_id: str, index: int):
    items = _load_hospital(hospital_id)
    if index < 0 or index >= len(items):
        raise HTTPException(404, "标准条目未找到")
    removed = items.pop(index)
    _save_hospital(hospital_id, items)
    return {"deleted": removed}


@router.post("/{hospital_id}/{index}/activate")
async def activate_standard(hospital_id: str, index: int):
    items = _load_hospital(hospital_id)
    if index < 0 or index >= len(items):
        raise HTTPException(404)
    items[index]["status"] = "active"
    selected = items[index]
    # Keep one active interpretation for the same hospital/lab/concept/context.
    for item in items:
        if item is selected:
            continue
        if (
            item.get("lab_code") == selected.get("lab_code")
            and item.get("concept_code") == selected.get("concept_code")
            and item.get("context_gender") == selected.get("context_gender")
            and item.get("context_age_min") == selected.get("context_age_min")
            and item.get("context_age_max") == selected.get("context_age_max")
        ):
            item["status"] = "retired"
    _save_hospital(hospital_id, items)
    return items[index]

