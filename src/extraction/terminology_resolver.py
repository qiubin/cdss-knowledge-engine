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
from ..terminology.repository import TerminologyCandidateRepository

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
    def __init__(
        self,
        auto_import_disease: bool = False,
        candidate_repository: Optional[TerminologyCandidateRepository] = None,
    ):
        self.reference = ReferenceVocabularyRepository("icd10")
        try:
            self.reference.load()
            self.reference_available = True
        except (FileNotFoundError, ValueError):
            self.reference_available = False
        self.auto_import_disease = auto_import_disease
        self.candidates = candidate_repository or TerminologyCandidateRepository()
        self.resolved_terms: List[Dict[str, Any]] = []

    def _reference_matches(
        self, name: str, aliases: Iterable[str], code: str = ""
    ) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """Return an exact match and separate fuzzy suggestions.

        Containment is useful for review suggestions, but unsafe as an
        automatic mapping because related diseases commonly contain each
        other's names.
        """
        if not self.reference_available:
            return None, []
        if code:
            found = self.reference.find(code)
            if found:
                return found, []
        names = {str(name or "").strip(), *[str(alias).strip() for alias in aliases or []]}
        names.discard("")
        fuzzy: List[Dict[str, Any]] = []
        for item in self.reference.entries():
            candidates = [str(item.get("name") or ""), *map(str, item.get("aliases") or [])]
            usable = [candidate for candidate in candidates if candidate and len(candidate) >= 2]
            if any(candidate in names for candidate in usable):
                return item, []
            if any(
                candidate in value or value in candidate
                for candidate in usable for value in names
            ):
                fuzzy.append(item)
        fuzzy.sort(key=lambda item: len(str(item.get("name") or "")), reverse=True)
        return None, fuzzy[:5]

    @staticmethod
    def _mapping_suggestions(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{
            "system": str(item.get("standard_system") or "ICD-10"),
            "code": str(item.get("code") or ""),
            "name": str(item.get("name") or ""),
            "relation": "related",
        } for item in items]

    def _queue_candidate(
        self,
        result: Dict[str, Any],
        *,
        doc_id: str,
        match_type: str,
        suggestions: Iterable[Dict[str, Any]] = (),
    ) -> None:
        try:
            self.candidates.upsert(
                entity_type="disease",
                name=str(result.get("name") or ""),
                local_code=str(result.get("code") or ""),
                aliases=result.get("aliases") or [],
                source_doc_id=doc_id,
                match_type=match_type,
                suggested_mappings=self._mapping_suggestions(suggestions),
            )
        except (OSError, ValueError):
            # Terminology resolution must not make guideline extraction fail
            # when the optional review queue is temporarily unavailable.
            pass

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
            matched, fuzzy = self._reference_matches(name, aliases, result["code"])
            if matched:
                result.update({
                    "code": str(matched.get("code") or result["code"]),
                    "name": str(matched.get("name") or name),
                    "standard_system": "ICD-10",
                    "standard_code": str(matched.get("code") or ""),
                })
                if self.auto_import_disease:
                    self._import_disease(result)
                elif EntityVocabularyRepository("disease").find(result["code"]) is None:
                    self._queue_candidate(
                        result,
                        doc_id=doc_id,
                        match_type="exact_reference",
                        suggestions=[matched],
                    )
                self.resolved_terms.append({**result, "resolution": "reference_exact"})
                return result

            # Fuzzy matches are suggestions only; keep a stable local code
            # until a reviewer chooses the correct standard concept.
            result["code"] = local_code(entity_type, name)
            result["standard_system"] = "LOCAL"
            result["standard_code"] = ""
            self._queue_candidate(
                result,
                doc_id=doc_id,
                match_type="ambiguous" if fuzzy else "unmatched",
                suggestions=fuzzy,
            )
            resolution = "ambiguous_candidate" if fuzzy else "local_candidate"
            self.resolved_terms.append({**result, "resolution": resolution})
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
