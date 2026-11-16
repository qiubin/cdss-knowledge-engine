"""Lab interpretation standard repository - per-hospital threshold management."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

from ..models.standard import LabInterpretationStandard


class StandardRepository(ABC):
    """Abstract interface for storing and querying hospital-specific standards."""

    @abstractmethod
    def get_active(
        self,
        hospital_id: str,
        lab_code: str,
        concept_code: str,
        context_gender: Optional[str] = None,
        context_age: Optional[int] = None,
    ) -> Optional[LabInterpretationStandard]:
        """Get the active standard for a hospital/lab/concept, respecting context."""
        ...

    @abstractmethod
    def list_for_hospital(self, hospital_id: str) -> List[LabInterpretationStandard]: ...

    @abstractmethod
    def upsert(self, standard: LabInterpretationStandard) -> None: ...


class JsonStandardRepository(StandardRepository):
    """File-based standard repository for development and testing.

    Structure: data/standards/{hospital_id}.json
    """

    def __init__(self, data_dir: str):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, List[LabInterpretationStandard]] = {}
        self._load_all()

    def _load_all(self) -> None:
        for f in self._data_dir.glob("*.json"):
            items = json.loads(f.read_text(encoding="utf-8"))
            for item in items:
                standard = LabInterpretationStandard(**item)
                self._cache.setdefault(standard.hospital_id, []).append(standard)

    def _find_matching(
        self,
        standards: List[LabInterpretationStandard],
        concept_code: str,
        context_gender: Optional[str],
        context_age: Optional[int],
    ) -> Optional[LabInterpretationStandard]:
        """Find the best matching standard, preferring more specific context matches."""
        candidates = []
        for s in standards:
            from .validation import effective
            if s.status != "active" or not effective(s):
                continue
            if s.concept_code != concept_code:
                continue
            # Context matching
            if s.context_gender and s.context_gender != context_gender:
                continue
            if s.context_age_min is not None and (context_age is None or context_age < s.context_age_min):
                continue
            if s.context_age_max is not None and (context_age is None or context_age > s.context_age_max):
                continue
            # Specificity score: more context constraints = more specific = higher priority
            specificity = 2 if s.method else 0
            if s.context_gender:
                specificity += 1
            if s.context_age_min is not None or s.context_age_max is not None:
                specificity += 1
            candidates.append((specificity, s))

        if not candidates:
            return None
        candidates.sort(key=lambda x: (-x[0], -x[1].version))
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0] and candidates[0][1].version == candidates[1][1].version:
            return None
        return candidates[0][1]

    def get_active(
        self,
        hospital_id: str,
        lab_code: str,
        concept_code: str,
        context_gender: Optional[str] = None,
        context_age: Optional[int] = None,
    ) -> Optional[LabInterpretationStandard]:
        standards = self._cache.get(hospital_id, [])
        matching = [s for s in standards if s.item_type == "lab" and s.lab_code == lab_code]
        return self._find_matching(matching, concept_code, context_gender, context_age)

    def get_exam_active(self, hospital_id, exam_code, finding_code, concept_code="PRESENT", context_gender=None, context_age=None, method=""):
        rows = [row for row in self._cache.get(hospital_id, []) if row.item_type == "exam" and row.exam_code == exam_code
                and row.finding_code == finding_code and (not row.method or row.method == method)]
        return self._find_matching(rows, concept_code, context_gender, context_age)

    def has_exam_standard(self, hospital_id, exam_code, finding_code, concept_code="PRESENT"):
        return any(row.item_type == "exam" and row.exam_code == exam_code and row.finding_code == finding_code
                   and row.concept_code == concept_code for row in self._cache.get(hospital_id, []))

    def list_for_hospital(self, hospital_id: str) -> List[LabInterpretationStandard]:
        return self._cache.get(hospital_id, [])

    def upsert(self, standard: LabInterpretationStandard) -> None:
        hospital_standards = self._cache.setdefault(standard.hospital_id, [])
        hospital_standards.append(standard)
        self._save(standard.hospital_id)

    def _save(self, hospital_id: str) -> None:
        path = self._data_dir / f"{hospital_id}.json"
        items = [s.model_dump() for s in self._cache.get(hospital_id, [])]
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

