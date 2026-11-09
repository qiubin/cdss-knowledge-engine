"""Domain profile registry.

Unknown or legacy values (empty, 通用, general) resolve to western so existing
data keeps working without a migration flag.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .base import DomainProfile, TCM, WESTERN
from .western.profile import WESTERN_PROFILE
from .tcm.profile import TCM_PROFILE

_PROFILES: Dict[str, DomainProfile] = {
    WESTERN_PROFILE.domain: WESTERN_PROFILE,
    TCM_PROFILE.domain: TCM_PROFILE,
}

_ALIASES = {
    "": WESTERN,
    "general": WESTERN,
    "通用": WESTERN,
    "modern": WESTERN,
    "西医": WESTERN,
    "western": WESTERN,
    "心血管内科": WESTERN,
    "心内科": WESTERN,
    "血液内科": WESTERN,
    "血液科": WESTERN,
    "tcm": TCM,
    "中医": TCM,
    "中医内科": TCM,
    "traditional_chinese_medicine": TCM,
}


def normalize_domain(domain: Optional[str]) -> str:
    return _ALIASES.get(str(domain or "").strip().lower(), str(domain or "").strip() or WESTERN)


def get_profile(domain: Optional[str]) -> DomainProfile:
    return _PROFILES.get(normalize_domain(domain), WESTERN_PROFILE)


def list_profiles() -> List[DomainProfile]:
    return list(_PROFILES.values())


def register_profile(profile: DomainProfile) -> None:
    _PROFILES[profile.domain] = profile
