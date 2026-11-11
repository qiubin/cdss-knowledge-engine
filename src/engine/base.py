# src/engine/base.py
"""Rule engine interface shared by native and Drools implementations."""
from __future__ import annotations

from typing import List, Optional, Protocol

from ..models.conclusion import Conclusion
from ..models.patient import PatientSnapshot


class RuleEngine(Protocol):
    """A rule engine evaluates a patient snapshot into conclusions.

    Implementations must be repeatable: identical inputs produce identical
    Conclusion content, including evidence traces and scores.
    """

    name: str

    def run(
        self,
        patient: PatientSnapshot,
        department: Optional[str] = None,
        include_draft_patterns: bool = False,
    ) -> List[Conclusion]: ...
