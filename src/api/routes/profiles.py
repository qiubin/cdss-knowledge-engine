"""Domain profile API - drives frontend navigation, graph rendering and forms."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.domains import get_profile, list_profiles, normalize_domain

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


@router.get("")
async def get_profiles():
    return [profile.to_dict() for profile in list_profiles()]


@router.get("/{domain}")
async def get_domain_profile(domain: str):
    normalized = normalize_domain(domain)
    profile = get_profile(normalized)
    if profile.domain != normalized:
        raise HTTPException(404, "领域不存在")
    return profile.to_dict()
