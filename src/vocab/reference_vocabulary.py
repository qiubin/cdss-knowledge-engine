"""Read-only reference catalogues used to populate active entity dictionaries.

The first catalogue is a trial ICD-10 subset.  It intentionally does not enter
guideline extraction directly: extraction still reads the small active disease
dictionary, while this catalogue provides curated standard codes for search and
explicit import.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent.parent.parent
REFERENCE_VOCAB_DIR = ROOT / "data" / "vocab" / "reference"


class ReferenceVocabularyRepository:
    """Loader and keyword search for a JSON reference catalogue."""

    def __init__(self, standard: str = "icd10", directory: Path = REFERENCE_VOCAB_DIR):
        self.standard = standard
        self.directory = directory
        self.path = directory / f"{standard}.json"

    def load(self) -> Dict[str, Any]:
        """Read the catalogue and fail clearly if its file is absent."""
        if not self.path.exists():
            raise FileNotFoundError(f"参考库不存在: {self.path}")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise ValueError(f"参考库文件结构无效: {self.path}")
        return data

    def metadata(self) -> Dict[str, Any]:
        data = self.load()
        return {key: value for key, value in data.items() if key != "entries"}

    def entries(self) -> List[Dict[str, Any]]:
        return [item for item in self.load().get("entries", []) if isinstance(item, dict)]

    def find(self, code: str) -> Optional[Dict[str, Any]]:
        wanted = str(code or "").strip().upper()
        return next((item for item in self.entries() if str(item.get("code", "")).upper() == wanted), None)

    def search(self, query: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        """Search by ICD-10 code prefix, standard name or alias containment."""
        keyword = str(query or "").strip()
        if not keyword:
            return self.entries()[: max(0, limit)]
        lowered = keyword.lower()
        matched: List[Dict[str, Any]] = []
        for item in self.entries():
            code = str(item.get("code", ""))
            names = [str(item.get("name", ""))] + [str(alias) for alias in item.get("aliases", [])]
            if code.lower().startswith(lowered) or any(keyword in name for name in names):
                matched.append(item)
            if len(matched) >= max(0, limit):
                break
        return matched
