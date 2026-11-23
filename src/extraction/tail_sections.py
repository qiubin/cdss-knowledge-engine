"""Shared tail-section (bibliography & friends) detection for extraction.

Every consumer of parsed chunks — western auto pipeline, template extraction,
domain detection — must agree on what counts as non-clinical tail content,
otherwise one template silently sends reference lists to the LLM while
another filters them.  This module is that single source of truth.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# Section names that never carry clinical recommendations.  Real-world
# guidelines print the heading with intra-character spaces ("参 考 文 献"),
# full-width spaces, as "引用文献", or in English ("References").
_TAIL_SECTION_PATTERN = re.compile(
    r"^[\s　]*(?:参[\s　]*考[\s　]*文[\s　]*献|引[\s　]*用[\s　]*文[\s　]*献"
    r"|[Rr][Ee][Ff][Ee][Rr][Ee][Nn][Cc][Ee][Ss]?[\s　]*$)"
)


def is_reference_section(section: str) -> bool:
    """True when a chunk section is bibliography-like tail content."""
    return bool(_TAIL_SECTION_PATTERN.match(str(section or "").strip()))


def split_clinical_chunks(chunks: List[Dict[str, Any]]) -> tuple:
    """Split chunks into (clinical, excluded) by bibliography-like sections.

    Applies to every domain and template, so western imports stop sending
    reference lists to the LLM the same way TCM ones already do.
    """
    clinical: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for chunk in chunks:
        (excluded if is_reference_section(chunk.get("section")) else clinical).append(chunk)
    return clinical, excluded
