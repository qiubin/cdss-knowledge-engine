"""Shared lab-concept inference helpers for extraction and graph import."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

# Labs with configured hospital standards today.  Rules should prefer these so
# a generated condition can actually be evaluated instead of silently failing
# with "标准未配置".
STANDARD_BACKED_LABS = {"CTN", "CK_MB", "MYO"}

CARDIAC_EXAMS = {"ECG_12LEAD", "ECHO", "CXR", "CORONARY_ANGIO"}

CARDIAC_FINDINGS = {
    "ST_CONVEX_ELEVATION",
    "PATHOLOGICAL_Q_WAVE",
    "R_WAVE_REDUCTION",
    "HYPERACUTE_T_WAVE",
    "LUNG_RALES",
    "INFARCTION",
    "VASCULAR_STENOSIS",
    "LV_HYPERTROPHY",
    "LVOT_OBSTRUCTION",
    "SAM",
    "LVH_VOLTAGE",
    "T_WAVE_INVERSION",
    "ST_T_CHANGE",
}

FINDING_EXAM_MAP = {
    "ST_CONVEX_ELEVATION": "ECG_12LEAD",
    "PATHOLOGICAL_Q_WAVE": "ECG_12LEAD",
    "R_WAVE_REDUCTION": "ECG_12LEAD",
    "HYPERACUTE_T_WAVE": "ECG_12LEAD",
    "LUNG_RALES": "CXR",
    "INFARCTION": "CORONARY_ANGIO",
    "VASCULAR_STENOSIS": "CORONARY_ANGIO",
    "LV_HYPERTROPHY": "ECHO",
    "LVOT_OBSTRUCTION": "ECHO",
    "SAM": "ECHO",
    "LVH_VOLTAGE": "ECG_12LEAD",
    "T_WAVE_INVERSION": "ECG_12LEAD",
    "ST_T_CHANGE": "ECG_12LEAD",
}

# Generic pathology words are too common to be disease-specific evidence.
GENERIC_FINDING_BLOCKLIST = {
    "HEMORRHAGE",
    "NODULE",
    "ULCER",
    "EROSION",
    "MASS",
    "THROMBOSIS",
}

GENERIC_DISEASE_BLOCKLIST = {
    "感染",
    "炎症",
    "免疫系统疾病",
    "原因不明",
    "病毒感染",
    "细菌感染",
    "真菌感染",
    "肿瘤",
    "疾病",
    "并发症",
}

# Unspecified parent codes.  If a guideline also yields a more specific code,
# these parent rules are dropped to avoid duplicate diagnoses.
GENERIC_PARENT_CODES = {"I51.4", "I42.9", "I50.9"}


def normalize_lab_concept(lab_code: str, concept: str, text: str = "") -> str:
    """Map a nearby concept to one that has hospital-standard semantics.

    CTN/CK-MB standards use ABOVE_ULN_1 and DYNAMIC_RISE; MYO uses ELEVATED
    and DECREASED.  Text such as "阳性" or "异常" is not directly evaluable, so
    it falls back to a local-text inference or the disease-section default.
    """
    lab = str(lab_code or "").upper()
    code = str(concept or "").upper()
    inferred = infer_lab_concept(lab, text)
    if lab in {"CTN", "CK_MB"}:
        if code == "DYNAMIC_RISE" or inferred == "DYNAMIC_RISE":
            return "DYNAMIC_RISE"
        if inferred in {"ABOVE_ULN_1", "DYNAMIC_RISE"}:
            return inferred
        if code in {"DECREASED", "CRITICAL_LOW"}:
            return ""
        # Abnormal/positive/blank troponin in an acute disease section is
        # treated as above-ULN; the candidate rule still requires review.
        return "ABOVE_ULN_1"
    if lab == "MYO":
        if inferred == "DECREASED" or code == "DECREASED":
            return "DECREASED"
        return "ELEVATED"
    return code if code in {"ELEVATED", "DECREASED"} else ""


def infer_lab_concept(lab_code: str, text: str) -> str:
    """Infer an abstract lab concept from local guideline text."""
    value = str(text or "")
    if not value:
        return ""
    if ("动态" in value or "进行性" in value or "持续" in value) and any(
        word in value for word in ("升高", "增高", "上升")
    ):
        return "DYNAMIC_RISE"
    if any(word in value for word in ("升高", "增高", "上升", "高于", "超过")):
        return "ABOVE_ULN_1" if lab_code in {"CTN", "CK_MB"} else "ELEVATED"
    if any(word in value for word in ("降低", "下降", "减低", "低于")):
        return "DECREASED"
    if "阳性" in value:
        return "POSITIVE"
    if "阴性" in value:
        return "NEGATIVE"
    return ""


def nearest_concept(
    concepts: Iterable[Dict[str, Any]],
    line: int,
    max_distance: int = 3,
) -> str:
    """Return the nearest concept code by line distance, if close enough."""
    best = None
    best_distance = None
    for concept in concepts:
        concept_line = concept.get("line_start")
        if concept_line is None:
            continue
        distance = abs(int(concept_line) - int(line))
        if distance > max_distance:
            continue
        if best_distance is None or distance < best_distance:
            best = str(concept.get("concept_code") or "")
            best_distance = distance
    return best or ""


def is_generic_finding(code: str) -> bool:
    return str(code or "").upper() in GENERIC_FINDING_BLOCKLIST


def exam_code_for_finding(finding_code: str) -> str:
    return FINDING_EXAM_MAP.get(str(finding_code or "").upper(), "")
