"""Concept vocabulary - finite, managed set of semantic concepts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel


class ConceptDefinition(BaseModel):
    """A single concept in the vocabulary."""
    code: str
    name_cn: str
    name_en: str = ""
    category: str  # lab_concept / exam_concept / symptom_concept / tcm_*_concept
    description: str = ""
    evaluator_type: str  # tells the engine which evaluator to use
    domain: str = "western"  # western / tcm / shared
    is_active: bool = True


class ConceptVocabulary:
    """Manages the finite set of clinical concepts."""

    def __init__(self):
        self._concepts: Dict[str, ConceptDefinition] = {}

    def load(self, path: str) -> None:
        """Load concepts from a JSON file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for item in data:
            concept = ConceptDefinition(**item)
            self._concepts[concept.code] = concept

    def get(self, code: str) -> Optional[ConceptDefinition]:
        return self._concepts.get(code)

    def get_evaluator_type(self, code: str) -> str:
        concept = self._concepts.get(code)
        if concept is None:
            raise ValueError(f"Unknown concept code: {code}")
        return concept.evaluator_type

    def list_by_category(self, category: str) -> List[ConceptDefinition]:
        return [c for c in self._concepts.values() if c.category == category and c.is_active]

    def list_all(self) -> List[ConceptDefinition]:
        return list(self._concepts.values())

    def exists(self, code: str) -> bool:
        return code in self._concepts

    def add(self, concept: ConceptDefinition) -> None:
        self._concepts[concept.code] = concept

