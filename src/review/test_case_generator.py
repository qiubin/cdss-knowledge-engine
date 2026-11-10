"""Auto-generate a test patient snapshot from an imported guideline package.

Walks the package's diagnosis/tcm_pattern rules, builds a patient snapshot that
satisfies the first constructible rule's condition tree (positive case), saves
it into ``data/patients`` and registers it in ``cases.json`` so the 病历测试
page can replay it immediately.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.kg.schema import ConceptVocabulary
from src.standards.repository import JsonStandardRepository
from src.terminology.labels import default_labels

ROOT = Path(__file__).resolve().parents[2]
PATIENTS_DIR = ROOT / "data" / "patients"
CASES_PATH = PATIENTS_DIR / "cases.json"

QUALITATIVE_MAP = {
    "POSITIVE": "positive",
    "NEGATIVE": "negative",
    "ABNORMAL": "abnormal",
    "PRESENT": "present",
}


def _name(kind: str, code: str) -> str:
    try:
        return default_labels().name(kind, code) or code
    except Exception:
        return code


class _PatientBuilder:
    """Construct a patient snapshot that satisfies one rule's condition tree."""

    def __init__(self, hospital_id: str = "XM_FIRST", graph_loader=None):
        self.hospital_id = hospital_id
        vocab = ConceptVocabulary()
        vocab.load(str(ROOT / "data" / "vocab" / "concepts.json"))
        self.vocab = vocab
        self.standards = JsonStandardRepository(str(ROOT / "data" / "standards"))
        self.graph_loader = graph_loader
        self.patient: Dict[str, Any] = {
            "patient_id": "",
            "hospital_id": hospital_id,
            "age": 58,
            "gender": "M",
            "labs": [],
            "exam_findings": [],
            "symptoms": [],
            "observations": [],
            "vitals": [],
            "past_history": [],
            "current_medications": [],
            "clinical_attributes": {},
        }

    # ---- helpers ----
    def _standard(self, lab_code: str, concept_code: str):
        try:
            return self.standards.get_active(
                hospital_id=self.hospital_id,
                lab_code=lab_code,
                concept_code=concept_code,
            )
        except Exception:
            return None

    def _lab_entry(self, lab_code: str, concept_code: str, sequence: int = 0) -> Optional[Dict[str, Any]]:
        if not lab_code or not concept_code:
            return None
        evaluator = self.vocab.get_evaluator_type(concept_code)
        standard = self._standard(lab_code, concept_code)
        base: Dict[str, Any] = {
            "lab_code": lab_code,
            "lab_name": _name("lab", lab_code),
            "collected_at": (
                datetime.now() + timedelta(hours=sequence * 2)
            ).isoformat(),
        }
        qualitative = QUALITATIVE_MAP.get(concept_code)
        if evaluator == "qualitative" or (qualitative and evaluator not in {"static_threshold", "uln_multiplier", "dynamic_trend"}):
            if evaluator != "dynamic_trend" and concept_code in QUALITATIVE_MAP:
                return {**base, "qualitative": qualitative}
        if evaluator == "dynamic_trend":
            threshold = getattr(standard, "threshold_value", None) if standard else None
            reference_high = threshold if threshold and threshold > 0 else 1.0
            first = reference_high * 0.8
            return [
                {**base, "value": round(first, 3), "reference_high": reference_high},
                {
                    **base,
                    "value": round(first * 2, 3),
                    "reference_high": reference_high,
                    "collected_at": (datetime.now() + timedelta(hours=4)).isoformat(),
                },
            ]
        if evaluator == "uln_multiplier":
            multiplier = getattr(standard, "uln_multiplier", None) if standard else None
            reference_high = (getattr(standard, "threshold_value", None) if standard else None) or 1.0
            multiplier = multiplier if multiplier and multiplier > 0 else 1.0
            value = reference_high * (multiplier + 0.5)
            return {**base, "value": round(value, 3), "reference_high": reference_high}
        # static threshold (default)
        threshold = getattr(standard, "threshold_value", None) if standard else None
        if threshold is None:
            threshold = 1.0
        operator = (getattr(standard, "operator", None) if standard else None) or ">"
        if operator in {"<", "<="} and threshold > 1:
            value = max(threshold - 1, 0)
        else:
            value = threshold + max(1.0, threshold * 0.2)
        return {
            **base,
            "value": round(value, 3),
            "reference_high": threshold,
        }

    # ---- condition walk ----
    def build_for(self, conditions: Dict[str, Any]) -> bool:
        return self._walk(conditions)

    def _walk(self, condition: Any) -> bool:
        if not isinstance(condition, dict):
            return True
        ctype = str(condition.get("type") or "")
        if ctype == "group":
            children = condition.get("children") or []
            if not children:
                return True
            operator = (condition.get("operator") or "AND").upper()
            if operator == "AND":
                return all(self._walk(child) for child in children)
            if operator == "OR":
                return any(self._walk(child) for child in children)
            if operator == "NOT":
                return True  # never synthesize an exclusion failure
            return False
        if ctype == "symptom":
            code = str(condition.get("symptom_code") or "")
            if not code:
                return False
            entry: Dict[str, Any] = {
                "symptom_code": code,
                "symptom_name": _name("symptom", code),
                "present": True,
            }
            if condition.get("duration_minutes"):
                entry["duration_minutes"] = float(condition["duration_minutes"])
            self.patient["symptoms"].append(entry)
            return True
        if ctype == "lab_concept":
            entries = self._lab_entry(
                str(condition.get("lab_code") or ""),
                str(condition.get("concept_code") or ""),
            )
            if not entries:
                return False
            for entry in (entries if isinstance(entries, list) else [entries]):
                self.patient["labs"].append(entry)
            return True
        if ctype == "exam_finding":
            exam_code = str(condition.get("exam_code") or "")
            finding_code = str(condition.get("finding_code") or "")
            if not finding_code:
                return False
            self.patient["exam_findings"].append({
                "exam_code": exam_code,
                "exam_name": _name("exam", exam_code),
                "finding_code": finding_code,
                "finding_name": _name("exam_finding", finding_code),
                "present": True,
            })
            return True
        if ctype == "vital":
            code = str(condition.get("vital_code") or "")
            threshold = condition.get("threshold_value")
            if not code or threshold is None:
                return False
            operator = (condition.get("operator") or ">")
            if operator in {"<", "<="}:
                value = float(threshold) - 10
            elif operator in {">", ">="}:
                value = float(threshold) + 10
            else:
                value = float(threshold)
            self.patient["vitals"].append({"vital_code": code, "value": value})
            return True
        if ctype == "demographic":
            age_min = condition.get("age_min")
            age_max = condition.get("age_max")
            if age_min:
                self.patient["age"] = max(int(self.patient.get("age") or 0), int(age_min))
            if age_max:
                self.patient["age"] = min(int(self.patient.get("age") or 58), int(age_max))
            if condition.get("gender"):
                self.patient["gender"] = str(condition["gender"])
            return True
        if ctype == "past_history":
            code = str(condition.get("code") or "")
            if code and code not in self.patient["past_history"]:
                self.patient["past_history"].append(code)
            return True
        if ctype == "medication":
            code = str(condition.get("code") or "")
            if code and code not in self.patient["current_medications"]:
                self.patient["current_medications"].append(code)
            return True
        if ctype == "attribute":
            code = str(condition.get("attribute_code") or "")
            if not code:
                return False
            self.patient["clinical_attributes"][code] = condition.get("attribute_value")
            return True
        if ctype == "observation":
            obs_type = str(condition.get("observation_type") or "symptom")
            code = str(condition.get("code") or "")
            if not code:
                return False
            self.patient["observations"].append({
                "observation_type": obs_type,
                "code": code,
                "name": _name(obs_type, code),
                "present": True,
            })
            return True
        if ctype == "evidence":
            return self._walk_evidence(condition.get("evidence_id") or "")
        # fact / weighted_group internals that need prior-stage conclusions:
        # treat as satisfiable so downstream groups still get built.
        return True

    def _walk_evidence(self, evidence_id: str) -> bool:
        if not evidence_id or not self.graph_loader:
            return True
        try:
            graph = self.graph_loader("stemi_graph.json")
        except Exception:
            return True
        row = next(
            (item for item in graph.get("evidences", []) if item.get("id") == evidence_id),
            None,
        )
        if not row:
            return True
        if row.get("data_type") == "lab" and row.get("lab_code"):
            entries = self._lab_entry(str(row["lab_code"]), str(row.get("concept_code") or ""))
            if entries:
                for entry in (entries if isinstance(entries, list) else [entries]):
                    self.patient["labs"].append(entry)
        elif row.get("data_type") == "symptom" and row.get("symptom_code"):
            self.patient["symptoms"].append({
                "symptom_code": str(row["symptom_code"]),
                "symptom_name": _name("symptom", str(row["symptom_code"])),
                "present": True,
            })
        elif row.get("data_type") == "exam_finding" and row.get("finding_code"):
            self.patient["exam_findings"].append({
                "exam_code": str(row.get("exam_code") or ""),
                "exam_name": _name("exam", str(row.get("exam_code") or "")),
                "finding_code": str(row["finding_code"]),
                "finding_name": _name("exam_finding", str(row["finding_code"])),
                "present": True,
            })
        return True


def _leaf_count(conditions: Any) -> int:
    if not isinstance(conditions, dict):
        return 0
    if conditions.get("type") == "group":
        return sum(_leaf_count(child) for child in conditions.get("children") or [])
    return 1


def _upsert_case(case: Dict[str, Any], cases_path: Optional[Path] = None) -> None:
    target = Path(cases_path) if cases_path else CASES_PATH
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    else:
        data = {}
    cases = [item for item in (data.get("cases") or []) if item.get("case_id") != case["case_id"]]
    cases.append(case)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"cases": cases}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def generate_case_for_package(
    package: Dict[str, Any],
    patients_dir: Optional[Path] = None,
    cases_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build (and persist) a positive test patient for one imported package."""
    doc_id = str(package.get("doc_id") or "")
    domain = str(package.get("domain") or "western")
    rules = [
        rule for rule in package.get("suggested_rules") or []
        if rule.get("rule_type") in {"diagnosis", "tcm_pattern"}
    ]
    if not rules:
        raise ValueError("包内没有诊断/辨证规则，无法生成测试病历")
    rules.sort(key=lambda rule: (
        -int(rule.get("priority") or 0),
        -_leaf_count(rule.get("conditions")),
    ))
    hospital_id = "TCM_DEMO" if domain == "tcm" else "XM_FIRST"
    graph_loader = None
    graph_filename = str(package.get("graph_filename") or "")
    if graph_filename:
        from src.api.routes.kg import _load_graph, normalize_graph_filename

        def graph_loader(name: str):
            return _load_graph(graph_filename or name)

        try:
            normalize_graph_filename(graph_filename)
        except Exception:
            graph_loader = None

    attempted: List[str] = []
    for rule in rules:
        builder = _PatientBuilder(hospital_id=hospital_id, graph_loader=graph_loader)
        builder.patient["patient_id"] = f"DEMO-{doc_id}"
        if not builder.build_for(rule.get("conditions")):
            continue
        if not builder.patient["symptoms"] and not builder.patient["labs"] \
                and not builder.patient["exam_findings"] and not builder.patient["observations"]:
            attempted.append(rule.get("rule_id") or "?")
            continue
        patient = builder.patient
        break
    else:
        raise ValueError(
            "无法从候选规则构造满足条件的病例（条件缺编码或依赖前阶段结论）: "
            + ", ".join(attempted or [rule.get("rule_id") or "?" for rule in rules])
        )

    target_dir = Path(patients_dir) if patients_dir else PATIENTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    patient_file = f"auto_{doc_id.lower()}_patient.json"
    (target_dir / patient_file).write_text(
        json.dumps(patient, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    disease_name = str(package.get("target_disease_code") or "")
    for entity in package.get("entities") or []:
        if entity.get("entity_type") in {"disease", "syndrome"} \
                and str(entity.get("code")) == str(package.get("target_disease_code")):
            disease_name = str(entity.get("name") or disease_name)
            break
    # 病例名以实际满足的规则锚点为准，避免与主目标不一致时产生误导
    disease_name = str(rule.get("disease_name") or "") or disease_name
    title = str(package.get("doc_title") or doc_id)
    case = {
        "case_id": f"auto-{doc_id.lower()}",
        "file": patient_file,
        "name": f"{disease_name or title[:14]} · 自动测试病例",
        "domain": domain,
        "description": f"由知识包自动生成的阳性测试病例（{title[:24]}），满足规则 {rule.get('rule_id', '')} 的条件。",
        "default": False,
        "category": disease_name or "自动生成",
    }
    _upsert_case(case, cases_path)
    return {
        "case": case,
        "patient_file": patient_file,
        "rule_id": rule.get("rule_id", ""),
        "summary": {
            "symptoms": len(patient.get("symptoms") or []),
            "labs": len(patient.get("labs") or []),
            "exam_findings": len(patient.get("exam_findings") or []),
            "observations": len(patient.get("observations") or []),
            "vitals": len(patient.get("vitals") or []),
        },
    }
