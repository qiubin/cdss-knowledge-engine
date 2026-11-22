"""Independent, reviewed TCM-to-western disease mapping master data."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


DEFAULT_MAPPING_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "vocab" / "tcm_western_disease_mappings.json"
)
ALLOWED_MAPPING_TYPES = {"equivalent", "broader", "narrower", "related", "contextual"}


class DiseaseMappingRepository:
    """Loads mappings maintained outside individual guideline imports."""

    def __init__(self, path: Path | str = DEFAULT_MAPPING_PATH):
        self.path = Path(path)

    def list(self, *, active_only: bool = False) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        rows = raw.get("mappings", []) if isinstance(raw, dict) else raw
        result: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if not self._valid(item):
                continue
            if active_only and item.get("review_status") != "active":
                continue
            result.append(item)
        return result

    def active_for_tcm_codes(self, codes: Iterable[str]) -> List[Dict[str, Any]]:
        wanted = {str(code) for code in codes if code}
        return [row for row in self.list(active_only=True) if row["tcm_disease_code"] in wanted]

    @staticmethod
    def _valid(item: Dict[str, Any]) -> bool:
        required = ("mapping_id", "tcm_disease_code", "western_disease_code", "mapping_type", "source")
        return (
            all(str(item.get(key) or "").strip() for key in required)
            and item.get("mapping_type") in ALLOWED_MAPPING_TYPES
            and item.get("review_status") in {"draft", "active", "retired"}
        )
