"""Automatic terminology resolution for the universal auto pipeline.

Standard codes are preferred.  When no reference code exists, a stable LOCAL
code is generated so extraction and rule generation are never blocked.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional

from .models import TemplateEntityEntry
from ..vocab.entity_vocabulary import EntityVocabularyRepository
from ..vocab.reference_vocabulary import ReferenceVocabularyRepository

UNIVERSAL_TYPES = {
    "disease", "symptom", "sign", "lab", "exam", "diagnostic_evidence",
    "pathology", "biomarker", "molecular_test", "stage", "histology",
    "risk_factor", "medication", "intervention", "treatment_plan",
    "contraindication", "monitoring_requirement",
    # Order / pathway layer: guideline-level materialization of treatment
    # plans into orderable items, order sets and whole-cycle care pathways.
    "order_set", "order_item", "care_pathway", "pathway_phase",
}

TYPE_ALIASES = {
    "疾病": "disease", "diagnosis": "disease", "condition": "disease",
    "症状": "symptom", "体征": "sign", "检验": "lab", "laboratory": "lab",
    "检查": "exam", "影像": "exam", "病理": "pathology", "病理类型": "pathology",
    "生物标志物": "biomarker", "标志物": "biomarker", "分子检测": "molecular_test",
    "基因检测": "molecular_test", "分期": "stage", "组织学": "histology",
    "组织学类型": "histology", "危险因素": "risk_factor", "风险因素": "risk_factor",
    "药物": "medication", "drug": "medication", "治疗措施": "intervention",
    "干预": "intervention", "治疗方案": "treatment_plan", "方案": "treatment_plan",
    "禁忌": "contraindication", "禁忌证": "contraindication",
    "监测要求": "monitoring_requirement", "监测": "monitoring_requirement",
    "诊断证据": "diagnostic_evidence", "证据": "diagnostic_evidence",
    # Order / pathway layer aliases used by guideline text and LLM output.
    "医嘱套餐": "order_set", "医嘱组套": "order_set", "套餐": "order_set",
    "医嘱条目": "order_item", "医嘱": "order_item", "医嘱项": "order_item",
    "诊疗路径": "care_pathway", "临床路径": "care_pathway", "路径": "care_pathway",
    "路径阶段": "pathway_phase", "治疗阶段": "pathway_phase", "阶段": "pathway_phase",
}


def normalize_type(raw: str) -> str:
    value = str(raw or "").strip()
    lowered = value.lower()
    if lowered in UNIVERSAL_TYPES:
        return lowered
    return TYPE_ALIASES.get(value, TYPE_ALIASES.get(lowered, lowered))


def local_code(entity_type: str, name: str) -> str:
    digest = hashlib.md5(f"{entity_type}:{name}".encode("utf-8")).hexdigest()[:10].upper()
    prefix = {
        "disease": "LOCAL-DIS", "symptom": "LOCAL-SYM", "lab": "LOCAL-LAB",
        "exam": "LOCAL-EXAM", "pathology": "LOCAL-PATH", "biomarker": "LOCAL-BIO",
        "molecular_test": "LOCAL-MOL", "stage": "LOCAL-STAGE", "histology": "LOCAL-HIST",
        "risk_factor": "LOCAL-RISK", "medication": "LOCAL-DRUG",
        "intervention": "LOCAL-INT", "treatment_plan": "LOCAL-TP",
        "contraindication": "LOCAL-CONTRA", "monitoring_requirement": "LOCAL-MON",
        "order_set": "LOCAL-OS", "order_item": "LOCAL-OI",
        "care_pathway": "LOCAL-CP", "pathway_phase": "LOCAL-PP",
    }.get(entity_type, "LOCAL")
    return f"{prefix}-{digest}"


class UniversalTermResolver:
    def __init__(self, auto_import_disease: bool = True):
        self.reference = ReferenceVocabularyRepository("icd10")
        try:
            self.reference.load()
            self.reference_available = True
        except (FileNotFoundError, ValueError):
            self.reference_available = False
        self.auto_import_disease = auto_import_disease
        self.resolved_terms: List[Dict[str, Any]] = []

    def _reference_match(self, name: str, aliases: Iterable[str], code: str = "") -> Optional[Dict[str, Any]]:
        if not self.reference_available:
            return None
        if code:
            found = self.reference.find(code)
            if found:
                return found
        names = [str(name or "")] + [str(alias) for alias in aliases or []]
        best = None
        for item in self.reference.entries():
            candidates = [str(item.get("name") or ""), *map(str, item.get("aliases") or [])]
            for candidate in candidates:
                if not candidate or len(candidate) < 2:
                    continue
                if candidate in names:
                    return item
                if candidate in (name or "") or (name and name in candidate):
                    if best is None or len(candidate) > len(str(best.get("name") or "")):
                        best = item
        return best

    def resolve(
        self,
        entity_type: str,
        name: str,
        *,
        code: str = "",
        aliases: Optional[List[str]] = None,
        standard_system: str = "",
        doc_id: str = "",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        entity_type = normalize_type(entity_type)
        if entity_type not in UNIVERSAL_TYPES:
            entity_type = "diagnostic_evidence"
        name = str(name or code or "").strip()
        aliases = [str(item).strip() for item in (aliases or []) if str(item).strip()]
        if not name:
            name = code
        raw_code = str(code or "").strip()
        if raw_code and not re.fullmatch(r"[A-Za-z0-9_.:-]+", raw_code):
            raw_code = ""
        result = {
            "entity_type": entity_type,
            "name": name,
            "aliases": sorted(set([name, *aliases])),
            "code": raw_code,
            "standard_system": str(standard_system or "").strip(),
            "standard_code": "",
            "domain": "western",
            "source_doc_id": doc_id,
            "attributes": dict(attributes or {}),
        }

        if entity_type == "disease" and self.reference_available:
            matched = self._reference_match(name, aliases, result["code"])
            if matched:
                result.update({
                    "code": str(matched.get("code") or result["code"]),
                    "name": str(matched.get("name") or name),
                    "standard_system": "ICD-10",
                    "standard_code": str(matched.get("code") or ""),
                })
                if self.auto_import_disease:
                    self._import_disease(result)
                self.resolved_terms.append({**result, "resolution": "reference"})
                return result

        if not result["code"]:
            result["code"] = local_code(entity_type, name)
            result["standard_system"] = "LOCAL"
        result["standard_code"] = result["standard_code"] or (
            result["code"] if result["standard_system"] and result["standard_system"] != "LOCAL" else ""
        )
        self.resolved_terms.append({**result, "resolution": "local" if result["standard_system"] == "LOCAL" else "standard"})
        return result

    def _import_disease(self, result: Dict[str, Any]) -> None:
        repository = EntityVocabularyRepository("disease")
        code = str(result.get("code") or "")
        if not code or repository.find(code):
            return
        entry = TemplateEntityEntry(
            code=code,
            name=str(result.get("name") or code),
            aliases=list(result.get("aliases") or []),
            domain="western",
            standard_system="ICD-10",
            standard_code=code,
            source="LLM-AUTO",
            attributes=dict(result.get("attributes") or {}),
        )
        try:
            repository.save(entry, is_new=True)
        except Exception:
            pass
