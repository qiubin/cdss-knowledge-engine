"""Readable term labels for inference evidence traces.

Trace labels are shown to clinicians for review, so every label pairs the full
term with the code — for example ``肌钙蛋白（CTN）``.  Entity names come from
``data/vocab/entities``; concept names come from the loaded ConceptVocabulary,
falling back to ``data/vocab/concepts.json``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..kg.schema import ConceptVocabulary
from ..terminology.labels import ENTITY_VOCAB_DIR, TerminologyLabels


class Terminology(TerminologyLabels):
    """Resolve entity and concept codes to display labels."""

    def __init__(
        self,
        vocabulary: Optional[ConceptVocabulary] = None,
        entity_dir: Path = ENTITY_VOCAB_DIR,
    ):
        super().__init__(entity_dir=entity_dir)
        self._vocabulary = vocabulary

    def concept(self, code: str) -> str:
        """Return the concept label, e.g. ``超过正常上限（ABOVE_ULN_1）``."""
        code = str(code or "").strip()
        if not code:
            return ""
        definition = self._vocabulary.get(code) if self._vocabulary else None
        name = str(getattr(definition, "name_cn", "") or "").strip()
        if not name:
            name = self.concept_name(code)
        if name and name != code:
            return f"{name}（{code}）"
        return name or code
