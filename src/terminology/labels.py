"""Human-readable labels that pair clinical codes with their full terms.

Generated knowledge graphs and rules are reviewed by clinicians, so a bare code
such as ``CTN``, ``RENAL_FUNCTION:ABNORMAL`` or ``SYM_FATIGUE`` is not
self-explanatory.  The labels written into generated artifacts use the
``全称（编码）`` form — for example ``肌钙蛋白（CTN）``.  Entity names come from
``data/vocab/entities``; concept names come from ``data/vocab/concepts.json``.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).parent.parent.parent
ENTITY_VOCAB_DIR = ROOT / "data" / "vocab" / "entities"
CONCEPT_PATH = ROOT / "data" / "vocab" / "concepts.json"

ENTITY_KINDS = (
    "lab",
    "symptom",
    "exam",
    "exam_finding",
    "diagnostic_evidence",
    "disease",
    "syndrome",
    "treatment_method",
    "tongue",
    "pulse",
    "sign",
    "medication",
    "intervention",
    "treatment_plan",
    "formula",
    "herb",
    "contraindication",
    "monitoring_requirement",
    # Order-set / care-pathway layer.  Dictionary files are optional; missing
    # files resolve to empty name maps without affecting other kinds.
    "order_set",
    "order_item",
    "care_pathway",
    "pathway_phase",
)

_LABEL_KINDS = ("lab", "symptom", "exam", "exam_finding")


class TerminologyLabels:
    """Resolve codes to ``全称（编码）`` labels for generated artifacts."""

    def __init__(
        self,
        entity_dir: Path = ENTITY_VOCAB_DIR,
        concept_path: Path = CONCEPT_PATH,
    ):
        self._names: Dict[str, Dict[str, str]] = {
            kind: self._load_names(entity_dir / f"{kind}.json") for kind in ENTITY_KINDS
        }
        self._concepts = self._load_concepts(concept_path)

    @staticmethod
    def _load_names(path: Path) -> Dict[str, str]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        names: Dict[str, str] = {}
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip()
            name = str(item.get("name") or "").strip()
            if code and name:
                names[code] = name
        return names

    @staticmethod
    def _load_concepts(path: Path) -> Dict[str, str]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        concepts: Dict[str, str] = {}
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip()
            name = str(item.get("name_cn") or item.get("name") or "").strip()
            if code and name:
                concepts[code] = name
        return concepts

    def name(self, kind: str, code: str) -> str:
        code = str(code or "").strip()
        if not code:
            return ""
        return self._names.get(kind, {}).get(code, "")

    def concept_name(self, code: str) -> str:
        return self._concepts.get(str(code or "").strip(), "")

    def term(self, kind: str, code: str) -> str:
        """Return ``全称（编码）``, falling back to the bare code."""
        code = str(code or "").strip()
        name = self.name(kind, code)
        if name and code and name != code:
            return f"{name}（{code}）"
        return name or code or "-"

    def concept(self, code: str) -> str:
        """Return the concept label, e.g. ``超正常上限（ABOVE_ULN_1）``."""
        code = str(code or "").strip()
        if not code:
            return ""
        name = self.concept_name(code)
        if name and name != code:
            return f"{name}（{code}）"
        return name or code

    def evidence_name(self, code: str) -> str:
        """Compose a name for an evidence code such as ``RENAL_FUNCTION:ABNORMAL``."""
        code = str(code or "").strip()
        existing = self.name("diagnostic_evidence", code)
        if existing:
            return existing
        if ":" not in code:
            return ""
        left, right = code.split(":", 1)
        base = next(
            (self.name(kind, left) for kind in _LABEL_KINDS if self.name(kind, left)),
            "",
        ) or left
        concept = self.concept_name(right)
        return f"{base}{concept}" if concept else base

    def evidence_label(self, code: str) -> str:
        """Return ``证据全称（编码）``, rebuilding the name when the code is missing."""
        code = str(code or "").strip()
        name = self.name("diagnostic_evidence", code) or self.evidence_name(code)
        if name and code and name != code:
            return f"{name}（{code}）"
        return name or code or "-"

    def condition_label(self, condition: Any) -> str:
        """Render one rule-condition node as a readable label."""
        if not isinstance(condition, dict):
            return ""
        condition_type = str(condition.get("type") or "")
        if condition_type == "group":
            children = [
                self.condition_label(child)
                for child in (condition.get("children") or [])
            ]
            children = [child for child in children if child]
            if not children:
                return ""
            if len(children) == 1:
                return children[0]
            operator = str(condition.get("operator") or "AND")
            return "(" + f" {operator} ".join(children) + ")"
        if condition_type == "lab_concept":
            return (
                f"{self.term('lab', condition.get('lab_code') or '')} "
                f"{self.concept(condition.get('concept_code') or '')}"
            ).strip()
        if condition_type == "exam_finding":
            exam = self.term("exam", condition.get("exam_code") or "")
            finding = self.term("exam_finding", condition.get("finding_code") or "")
            if condition.get("exam_code") and condition.get("finding_code"):
                return f"{exam} · {finding}"
            return finding or exam
        if condition_type == "symptom":
            label = self.term("symptom", condition.get("symptom_code") or "")
            duration = condition.get("duration_minutes")
            if duration:
                label += f" 持续≥{float(duration):g}分钟"
            return label
        if condition_type == "vital":
            return (
                f"{condition.get('vital_code') or ''} "
                f"{condition.get('operator') or ''} "
                f"{condition.get('threshold_value') if condition.get('threshold_value') is not None else ''}"
            ).strip()
        if condition_type == "demographic":
            parts = []
            if condition.get("age_min") is not None:
                parts.append(f"年龄≥{condition['age_min']}岁")
            if condition.get("age_max") is not None:
                parts.append(f"年龄≤{condition['age_max']}岁")
            if condition.get("gender"):
                parts.append(f"性别={condition['gender']}")
            return "，".join(parts)
        if condition_type == "past_history":
            return f"既往史：{condition.get('code') or ''}"
        if condition_type == "medication":
            return f"当前用药：{self.term('medication', condition.get('code') or '')}"
        if condition_type == "observation":
            observation_type = str(condition.get("observation_type") or "symptom")
            return (
                f"{self.term(observation_type, condition.get('code') or '')} "
                f"{self.concept(condition.get('concept_code') or 'PRESENT')}"
            ).strip()
        if condition_type == "fact":
            fact_type = str(condition.get("fact_type") or "")
            kind = fact_type if fact_type in ENTITY_KINDS else ""
            return f"已确认：{self.term(kind, condition.get('code') or '') if kind else condition.get('code') or ''}"
        if condition_type == "attribute":
            return (
                f"{condition.get('attribute_code') or ''} "
                f"{condition.get('operator') or ''} "
                f"{condition.get('attribute_value') if condition.get('attribute_value') is not None else ''}"
            ).strip()
        if condition_type == "evidence":
            return f"证据：{condition.get('evidence_id') or ''}"
        return str(condition.get("code") or condition_type or "")

    def annotate(self, condition: Any) -> Any:
        """Attach a ``label`` to every node of a condition tree, in place."""
        if not isinstance(condition, dict):
            return condition
        for child in condition.get("children") or []:
            self.annotate(child)
        label = self.condition_label(condition)
        if label:
            condition["label"] = label
        return condition

    def summarize(self, condition: Any) -> str:
        """Human-readable summary of a whole condition tree."""
        label = self.condition_label(condition)
        if label.startswith("(") and label.endswith(")"):
            return label[1:-1]
        return label


@lru_cache(maxsize=1)
def default_labels() -> TerminologyLabels:
    return TerminologyLabels()
