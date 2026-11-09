"""Inference engine - evaluates rules against patient data."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..kg.client import KnowledgeGraphClient
from ..kg.schema import ConceptVocabulary
from ..domains import get_profile, normalize_domain
from ..models.conclusion import Conclusion, EvidenceTrace
from ..models.patient import PatientSnapshot
from ..models.rule import Rule, RuleCondition
from ..models.standard import LabInterpretationStandard
from ..rules.repository import RuleRepository
from ..standards.repository import StandardRepository
from .evaluators.base import EvaluationResult
from .evaluators.factory import EvaluatorFactory
from .terminology import Terminology

logger = logging.getLogger(__name__)


class InferenceEngine:
    """Core inference engine.

    Takes a PatientSnapshot, evaluates all active rules,
    and produces a list of Conclusions.

    Extension points:
    - Add new evaluator types via EvaluatorFactory.register()
    - Add new rules via RuleRepository (JSON/DB)
    - Add new standards via StandardRepository
    - Add new knowledge via KnowledgeGraphClient
    """

    def __init__(
        self,
        rule_repo: RuleRepository,
        kg_client: KnowledgeGraphClient,
        standard_repo: StandardRepository,
        vocabulary: ConceptVocabulary,
        graph_client_factory: Optional[Callable[[str], KnowledgeGraphClient]] = None,
        domain: str = "western",
        terminology: Optional[Terminology] = None,
    ):
        self.rule_repo = rule_repo
        self.kg = kg_client
        self.standard_repo = standard_repo
        self.vocabulary = vocabulary
        self.graph_client_factory = graph_client_factory
        self.domain = normalize_domain(domain)
        self.terminology = terminology or Terminology(vocabulary)
        self._current_kg = kg_client
        self._current_facts: Dict[str, Dict[str, Conclusion]] = {}

    def run(self, patient: PatientSnapshot, department: Optional[str] = None) -> List[Conclusion]:
        """Run domain-staged inference on a patient snapshot.

        Stages are declared by the domain profile.  Western medicine keeps the
        historical diagnosis → treatment flow; TCM runs pattern → treatment →
        prescription → contraindication.  Conclusions from each stage are
        materialized as facts so later ``fact`` conditions can consume them.
        """
        profile = get_profile(self.domain)
        all_rules = list(self.rule_repo.list_all())
        explicit_types = {
            rule_type
            for stage in profile.stages
            for rule_type in stage.rule_types
        }

        conclusions: List[Conclusion] = []
        facts: Dict[str, Dict[str, Conclusion]] = {}

        for stage in profile.stages:
            if stage.catch_all:
                stage_rules = [
                    rule for rule in all_rules
                    if rule.rule_type not in explicit_types
                    or rule.rule_type in stage.rule_types
                ]
            else:
                stage_rules = [
                    rule for rule in all_rules
                    if rule.rule_type in stage.rule_types
                ]
            allowed_status = {"active", "draft"} if stage.draft_allowed else {"active"}
            stage_rules = [
                rule for rule in stage_rules
                if rule.status in allowed_status
                and normalize_domain(getattr(rule, "domain", "western")) == profile.domain
                and (department is None or rule.department is None or rule.department == department)
            ]
            stage_rules.sort(key=lambda rule: -rule.priority)

            if stage.draft_allowed:
                prior_codes = set().union(*(set(items) for items in facts.values())) if facts else set()
                stage_rules = [rule for rule in stage_rules if rule.disease_code in prior_codes]

            self._current_facts = facts
            stage_conclusions: List[Conclusion] = []
            for rule in stage_rules:
                self._current_kg = (
                    self.graph_client_factory(str(rule.graph_filename or profile.graph.legacy_graph_filename))
                    if self.graph_client_factory
                    else self.kg
                )
                try:
                    matched, traces = self._evaluate_condition(rule.conditions, patient)
                    if matched:
                        trigger_ids = [
                            c.rule_id for c in conclusions
                            if c.disease_code == rule.disease_code
                            and c.rule_type not in {"treatment", "tcm_treatment", "tcm_prescription"}
                        ]
                        conclusion = self._build_conclusion(
                            rule, traces, patient,
                            trigger_ids=trigger_ids
                            if rule.rule_type in {"treatment", "tcm_treatment", "tcm_prescription"}
                            else None,
                        )
                        stage_conclusions.append(conclusion)
                except Exception as e:
                    logger.warning(f"Rule {rule.rule_id} evaluation failed: {e}")
                finally:
                    self._current_kg = self.kg

            conclusions.extend(stage_conclusions)
            self._materialize_facts(facts, stage_conclusions)

        conclusions.sort(key=lambda c: (-c.priority, c.rule_type))
        return conclusions

    @staticmethod
    def _materialize_facts(
        facts: Dict[str, Dict[str, Conclusion]],
        conclusions: List[Conclusion],
    ) -> None:
        for conclusion in conclusions:
            code = conclusion.anchor_code or conclusion.disease_code
            if code:
                facts.setdefault(conclusion.anchor_type or "disease", {})[code] = conclusion
            if conclusion.treatment_method_code:
                facts.setdefault("treatment", {})[conclusion.treatment_method_code] = conclusion

    def _evaluate_condition(
        self,
        condition: RuleCondition,
        patient: PatientSnapshot,
    ) -> Tuple[bool, List[EvidenceTrace]]:
        """Recursively evaluate a condition tree."""
        if condition.type == "group":
            return self._evaluate_group(condition, patient)
        elif condition.type == "evidence":
            return self._evaluate_evidence(condition, patient)
        elif condition.type == "lab_concept":
            return self._evaluate_lab_concept(condition, patient)
        elif condition.type == "exam_finding":
            return self._evaluate_exam_finding(condition, patient)
        elif condition.type == "symptom":
            return self._evaluate_symptom(condition, patient)
        elif condition.type == "vital":
            return self._evaluate_vital(condition, patient)
        elif condition.type == "demographic":
            return self._evaluate_demographic(condition, patient)
        elif condition.type == "past_history":
            return self._evaluate_past_history(condition, patient)
        elif condition.type == "medication":
            return self._evaluate_medication(condition, patient)
        elif condition.type == "observation":
            return self._evaluate_observation(condition, patient)
        elif condition.type == "fact":
            return self._evaluate_fact(condition)
        elif condition.type == "attribute":
            return self._evaluate_attribute(condition, patient)
        else:
            logger.warning(f"Unknown condition type: {condition.type}")
            return False, []

    def _evaluate_group(
        self,
        condition: RuleCondition,
        patient: PatientSnapshot,
    ) -> Tuple[bool, List[EvidenceTrace]]:
        op = condition.operator or "AND"
        results = []
        all_traces = []

        for child in condition.children:
            matched, traces = self._evaluate_condition(child, patient)
            results.append(matched)
            all_traces.extend(traces)

        if op == "AND":
            matched = all(results)
        elif op == "OR":
            matched = any(results)
        elif op == "NOT":
            matched = not any(results)
        else:
            matched = False

        return matched, all_traces

    def _evaluate_evidence(
        self,
        condition: RuleCondition,
        patient: PatientSnapshot,
    ) -> Tuple[bool, List[EvidenceTrace]]:
        """Evaluate via a DiagnosticEvidence node in the KG."""
        evidence = self._current_kg.get_evidence(condition.evidence_id or "")
        if evidence is None:
            logger.warning(f"Evidence not found in KG: {condition.evidence_id}")
            return False, []

        if evidence.data_type == "lab":
            return self._evaluate_lab_evidence(evidence, patient)
        elif evidence.data_type == "exam_finding":
            return self._evaluate_exam_evidence(evidence, patient)
        elif evidence.data_type == "symptom":
            return self._evaluate_symptom_evidence(evidence, patient)
        else:
            return False, []

    def _evaluate_lab_evidence(self, evidence, patient) -> Tuple[bool, List[EvidenceTrace]]:
        """Evaluate a lab-based evidence node."""
        lab_code = evidence.lab_code
        concept_code = evidence.concept_code
        if not lab_code or not concept_code:
            return False, []

        # Get evaluator type from vocabulary
        evaluator_type = self.vocabulary.get_evaluator_type(concept_code)

        # Get the standard for this hospital/lab/concept
        standard = self.standard_repo.get_active(
            hospital_id=patient.hospital_id,
            lab_code=lab_code,
            concept_code=concept_code,
            context_gender=patient.gender.value if patient.gender else None,
            context_age=patient.age,
        )

        # Create and run the evaluator
        evaluator = EvaluatorFactory.create(evaluator_type)
        result = evaluator.evaluate(patient, standard, evidence.parameter)
        lab = patient.get_lab(lab_code)
        lab_name = lab.lab_name if lab else ""

        trace = EvidenceTrace(
            evidence_id=evidence.id,
            description=self._evidence_title(
                evidence.description, "lab", lab_code, concept_code
            ),
            lab_code=lab_code,
            concept_code=concept_code,
            patient_value_display=self._localized_concept(
                self._localized_value(
                    result.patient_value_display, "lab", lab_code, lab_name
                ),
                concept_code,
            ),
            standard_used=self._localized_concept(result.standard_used, concept_code),
        )

        if result.matched:
            self._attach_source(evidence, trace)

        return result.matched, ([trace] if result.matched else [])

    def _evaluate_exam_evidence(self, evidence, patient) -> Tuple[bool, List[EvidenceTrace]]:
        """Evaluate an exam finding evidence node."""
        evaluator = EvaluatorFactory.create("finding_exists")
        concept_code = evidence.concept_code or "PRESENT"
        param = {
            "exam_code": evidence.exam_code,
            "finding_code": evidence.finding_code,
            "concept_code": concept_code,
        }
        result = evaluator.evaluate(patient, None, param)
        finding = patient.get_finding(
            evidence.exam_code or "", evidence.finding_code or ""
        )

        trace = EvidenceTrace(
            evidence_id=evidence.id,
            description=self._evidence_title(
                evidence.description,
                "exam_finding",
                evidence.finding_code or "",
                concept_code,
            ),
            patient_value_display=self._localized_value(
                self._localized_value(
                    result.patient_value_display,
                    "exam",
                    evidence.exam_code,
                    finding.exam_name if finding else "",
                ),
                "exam_finding",
                evidence.finding_code,
                finding.finding_name if finding else "",
            ),
            standard_used=self._localized_concept(
                result.standard_used, concept_code
            ),
        )

        if result.matched:
            self._attach_source(evidence, trace)

        return result.matched, ([trace] if result.matched else [])

    def _evaluate_symptom_evidence(self, evidence, patient) -> Tuple[bool, List[EvidenceTrace]]:
        """Evaluate a symptom evidence node."""
        if evidence.concept_code == "DURATION_GT":
            evaluator = EvaluatorFactory.create("duration_check")
            param = {
                "symptom_code": evidence.symptom_code,
                **(evidence.parameter or {}),
            }
        else:
            # Simple presence check
            symptom = patient.get_symptom(evidence.symptom_code or "")
            matched = symptom is not None and symptom.present
            trace = EvidenceTrace(
                evidence_id=evidence.id,
                description=self._evidence_title(
                    evidence.description,
                    "symptom",
                    evidence.symptom_code or "",
                    evidence.concept_code or "",
                ),
                patient_value_display=self._localized_value(
                    f"{evidence.symptom_code}={'+' if matched else '-'}",
                    "symptom",
                    evidence.symptom_code or "",
                    symptom.symptom_name if symptom else "",
                ),
                standard_used=self._concept_term(evidence.concept_code or "PRESENT"),
            )
            if matched:
                self._attach_source(evidence, trace)
            return matched, ([trace] if matched else [])

        result = evaluator.evaluate(patient, None, param)
        symptom = patient.get_symptom(evidence.symptom_code or "")
        trace = EvidenceTrace(
            evidence_id=evidence.id,
            description=self._evidence_title(
                evidence.description,
                "symptom",
                evidence.symptom_code or "",
                evidence.concept_code or "",
            ),
            patient_value_display=self._localized_value(
                result.patient_value_display,
                "symptom",
                evidence.symptom_code or "",
                symptom.symptom_name if symptom else "",
            ),
            standard_used=result.standard_used,
        )
        if result.matched:
            self._attach_source(evidence, trace)
        return result.matched, ([trace] if result.matched else [])

    def _evaluate_lab_concept(
        self,
        condition: RuleCondition,
        patient: PatientSnapshot,
    ) -> Tuple[bool, List[EvidenceTrace]]:
        """Direct lab + concept evaluation (bypasses KG evidence node)."""
        lab_code = condition.lab_code or ""
        concept_code = condition.concept_code or ""
        if not lab_code or not concept_code:
            return False, []

        evaluator_type = self.vocabulary.get_evaluator_type(concept_code)
        standard = self.standard_repo.get_active(
            hospital_id=patient.hospital_id,
            lab_code=lab_code,
            concept_code=concept_code,
            context_gender=patient.gender.value if patient.gender else None,
            context_age=patient.age,
        )

        evaluator = EvaluatorFactory.create(evaluator_type)
        result = evaluator.evaluate(patient, standard, condition.parameter)
        lab = patient.get_lab(lab_code)
        lab_name = lab.lab_name if lab else ""

        trace = EvidenceTrace(
            evidence_id=f"direct_{lab_code}_{concept_code}",
            description=f"{self._entity_label('lab', lab_code, lab_name)} "
            f"{self._concept_term(concept_code)}",
            lab_code=lab_code,
            concept_code=concept_code,
            patient_value_display=self._localized_concept(
                self._localized_value(
                    result.patient_value_display, "lab", lab_code, lab_name
                ),
                concept_code,
            ),
            standard_used=self._localized_concept(result.standard_used, concept_code),
        )

        return result.matched, ([trace] if result.matched else [])

    def _evaluate_exam_finding(
        self,
        condition: RuleCondition,
        patient: PatientSnapshot,
    ) -> Tuple[bool, List[EvidenceTrace]]:
        """Direct exam finding evaluation."""
        finding = patient.get_finding(condition.exam_code or "", condition.finding_code or "")
        matched = finding is not None and finding.present
        trace = EvidenceTrace(
            evidence_id=f"direct_{condition.exam_code}_{condition.finding_code}",
            description=f"{self._entity_label('exam', condition.exam_code or '', finding.exam_name if finding else '')} · "
            f"{self._entity_label('exam_finding', condition.finding_code or '', finding.finding_name if finding else '')}",
            patient_value_display=f"{'+' if matched else '-'}",
            standard_used=self._concept_term(condition.concept_code or "PRESENT"),
        )
        return matched, ([trace] if matched else [])

    def _evaluate_symptom(
        self,
        condition: RuleCondition,
        patient: PatientSnapshot,
    ) -> Tuple[bool, List[EvidenceTrace]]:
        """Direct symptom evaluation with optional duration check."""
        symptom = patient.get_symptom(condition.symptom_code or "")
        if symptom is None or not symptom.present:
            return False, []

        if condition.duration_minutes:
            actual = symptom.duration_minutes
            if actual is None:
                return False, []
            matched = actual >= condition.duration_minutes
        else:
            matched = True

        symptom_term = self._entity_label(
            "symptom", condition.symptom_code or "", symptom.symptom_name
        )
        if symptom.duration_minutes:
            display = f"持续时间={symptom.duration_minutes:g}分钟"
        else:
            display = "存在"
        trace = EvidenceTrace(
            evidence_id=f"direct_symptom_{condition.symptom_code}",
            description=symptom_term,
            patient_value_display=display,
            standard_used=(
                f"持续时间≥{condition.duration_minutes:g}分钟"
                if condition.duration_minutes
                else self._concept_term(condition.concept_code or "PRESENT")
            ),
        )
        return matched, ([trace] if matched else [])

    def _evaluate_vital(
        self,
        condition: RuleCondition,
        patient: PatientSnapshot,
    ) -> Tuple[bool, List[EvidenceTrace]]:
        """Vital sign evaluation with numeric operator."""
        matching = [v for v in patient.vitals if v.vital_code == condition.vital_code]
        if not matching or condition.threshold_value is None:
            return False, []

        value = matching[0].value
        op = condition.operator or ">"
        ops = {
            ">": lambda v, t: v > t,
            "<": lambda v, t: v < t,
            ">=": lambda v, t: v >= t,
            "<=": lambda v, t: v <= t,
            "=": lambda v, t: abs(v - t) < 0.01,
        }
        op_fn = ops.get(op)
        if op_fn is None:
            return False, []

        matched = op_fn(value, condition.threshold_value)
        trace = EvidenceTrace(
            evidence_id=f"direct_vital_{condition.vital_code}",
            description=f"{condition.vital_code} {op} {condition.threshold_value}",
            patient_value_display=f"{condition.vital_code}={value}",
            standard_used=f"{op}{condition.threshold_value}",
        )
        return matched, ([trace] if matched else [])

    def _evaluate_demographic(
        self,
        condition: RuleCondition,
        patient: PatientSnapshot,
    ) -> Tuple[bool, List[EvidenceTrace]]:
        """Age/gender demographic check."""
        matched = True
        if condition.age_min and patient.age and patient.age < condition.age_min:
            matched = False
        if condition.age_max and patient.age and patient.age > condition.age_max:
            matched = False
        if condition.gender and patient.gender.value and patient.gender.value != condition.gender:
            matched = False
        return matched, []

    def _evaluate_past_history(
        self,
        condition: RuleCondition,
        patient: PatientSnapshot,
    ) -> Tuple[bool, List[EvidenceTrace]]:
        """Check if patient has a condition in their history."""
        matched = condition.code in patient.past_history
        return matched, []

    def _evaluate_medication(
        self,
        condition: RuleCondition,
        patient: PatientSnapshot,
    ) -> Tuple[bool, List[EvidenceTrace]]:
        """Check if patient is on a medication."""
        matched = condition.code in patient.current_medications
        return matched, []

    def _evaluate_observation(
        self,
        condition: RuleCondition,
        patient: PatientSnapshot,
    ) -> Tuple[bool, List[EvidenceTrace]]:
        """Evaluate a generic observation (TCM tongue/pulse/sign/constitution)."""
        observation_type = condition.observation_type or "symptom"
        code = condition.code or ""
        if not code:
            return False, []
        evaluator = EvaluatorFactory.create("observation_present")
        result = evaluator.evaluate(patient, None, {
            "observation_type": observation_type,
            "code": code,
            "concept_code": condition.concept_code or "PRESENT",
        })
        kind = observation_type if observation_type in {
            "symptom", "tongue", "pulse", "sign"
        } else ""
        label = self._term(kind, code) if kind else f"{observation_type}:{code}"
        present = bool((result.detail or {}).get("present"))
        trace = EvidenceTrace(
            evidence_id=f"direct_observation_{observation_type}_{code}",
            description=f"{label} {'存在' if present else '未见'}",
            patient_value_display=result.patient_value_display,
            standard_used=self._localized_concept(result.standard_used, condition.concept_code or ""),
        )
        return result.matched, ([trace] if result.matched else [])

    def _evaluate_fact(
        self,
        condition: RuleCondition,
    ) -> Tuple[bool, List[EvidenceTrace]]:
        """Consume a fact produced by an earlier inference stage."""
        fact_type = condition.fact_type or "syndrome"
        code = condition.code or ""
        if not code:
            return False, []
        fact = self._current_facts.get(fact_type, {}).get(code)
        kind = {"syndrome": "syndrome", "treatment_method": "treatment_method", "disease": "disease"}.get(
            fact_type, ""
        )
        description = self._term(kind, code) if kind else f"{fact_type}:{code}"
        trace = EvidenceTrace(
            evidence_id=f"fact_{fact_type}_{code}",
            description=description,
            patient_value_display="已确认" if fact else "未确认",
            source_ref=getattr(fact, "guideline_source", None) if fact else None,
        )
        return bool(fact), ([trace] if fact else [])

    def _evaluate_attribute(
        self,
        condition: RuleCondition,
        patient: PatientSnapshot,
    ) -> Tuple[bool, List[EvidenceTrace]]:
        """Evaluate a clinical attribute such as histology/stage/biomarker."""
        code = str(condition.attribute_code or "")
        if not code:
            return False, []
        actual = patient.clinical_attributes.get(code)
        expected = condition.attribute_value
        op = condition.operator or "="
        if actual is None:
            return False, []
        if op in {"in", "IN"}:
            values = expected if isinstance(expected, list) else [expected]
            matched = actual in values
        elif op in {"contains", "CONTAINS"}:
            matched = expected in actual if isinstance(actual, (list, str)) else False
        else:
            try:
                if op == "=":
                    matched = str(actual) == str(expected)
                elif op == "!=":
                    matched = str(actual) != str(expected)
                elif op == ">":
                    matched = float(actual) > float(expected)
                elif op == "<":
                    matched = float(actual) < float(expected)
                elif op == ">=":
                    matched = float(actual) >= float(expected)
                elif op == "<=":
                    matched = float(actual) <= float(expected)
                else:
                    matched = False
            except (TypeError, ValueError):
                matched = False
        trace = EvidenceTrace(
            evidence_id=f"attribute_{code}",
            description=f"{code} {op} {expected}",
            patient_value_display=f"{code}={actual}",
            standard_used=f"{op} {expected}",
        )
        return matched, ([trace] if matched else [])

    def _term(self, kind: str, code: str) -> str:
        """Readable ``名称（编码）`` label for one entity code."""
        return self.terminology.term(kind, code)

    def _concept_term(self, code: str) -> str:
        return self.terminology.concept(code)

    def _entity_label(self, kind: str, code: str, fallback_name: str = "") -> str:
        """Use the shared dictionary name, else a patient-supplied name, else the code."""
        if self.terminology.name(kind, code):
            return self._term(kind, code)
        code = str(code or "").strip()
        fallback_name = str(fallback_name or "").strip()
        if fallback_name and fallback_name != code:
            return f"{fallback_name}（{code}）"
        return code or "-"

    def _localized_value(
        self, value: Optional[str], kind: str, code: str, fallback_name: str = ""
    ) -> Optional[str]:
        """Replace a raw code embedded in an evaluator message with its term.

        Patient snapshots sometimes carry a display name for codes that are
        absent from the shared dictionary; use it so the trace never falls back
        to a bare code.
        """
        text = str(value or "")
        code = str(code or "")
        if not text or not code or code not in text:
            return value
        label = self.terminology.term(kind, code)
        fallback_name = str(fallback_name or "").strip()
        if not self.terminology.name(kind, code) and fallback_name and fallback_name != code:
            label = f"{fallback_name}（{code}）"
        return text.replace(code, label)

    def _localized_concept(self, value: Optional[str], code: str) -> Optional[str]:
        text = str(value or "")
        code = str(code or "")
        if not text or not code or code not in text:
            return value
        return text.replace(code, self.terminology.concept(code))

    def _evidence_title(
        self, description: Optional[str], kind: str, code: str, concept_code: str = ""
    ) -> str:
        """Prefer the readable trace title; rebuild bare entity names with terms.

        Imported evidence often carries a full clinical phrase.  Generated
        evidence carries only the entity name, so append the concept label and
        keep the codes visible for review.
        """
        text = str(description or "").strip()
        name = self.terminology.name(kind, code)
        if not text or (name and text == name):
            parts = [self._term(kind, code), self._concept_term(concept_code)]
            return " ".join(part for part in parts if part).strip()
        return text

    def _attach_source(self, evidence, trace: EvidenceTrace) -> None:
        """Attach guideline source reference to a trace."""
        if evidence.source_refs:
            source = self._current_kg.get_source(evidence.source_refs[0])
            if source:
                trace.source_ref = f"{source.doc_title} p{source.page_start} l{source.line_start}-{source.line_end}: {source.quote[:80]}..."

    def _build_conclusion(
        self,
        rule: Rule,
        traces: List[EvidenceTrace],
        patient: PatientSnapshot,
        trigger_ids: Optional[List[str]] = None,
    ) -> Conclusion:
        """Build a Conclusion from a matched rule."""
        anchor_type = rule.anchor_type or "disease"
        anchor_code = rule.disease_code
        graph_name = self._resolve_anchor_name(anchor_type, anchor_code, rule.disease_name)
        # A guideline may use a more specific clinical term than the canonical
        # graph name (e.g. 暴发性心肌炎 vs 急性心肌炎); keep the specific term.
        anchor_name = (
            rule.disease_name
            if rule.disease_name and len(rule.disease_name) > len(graph_name)
            else graph_name
        )
        total_conditions = self._count_leaves(rule.conditions)
        matched_conditions = len(traces)
        score = (matched_conditions / total_conditions) if total_conditions else None
        confidence = rule.conclusion.confidence
        if rule.rule_type == "tcm_pattern" and score is not None:
            confidence = "high" if score >= 0.8 else "medium" if score >= 0.6 else "low"
        now = datetime.now(timezone.utc).isoformat()

        return Conclusion(
            rule_id=rule.rule_id,
            rule_version=rule.version,
            rule_type=rule.rule_type,
            anchor_type=anchor_type,
            anchor_code=anchor_code,
            anchor_name=anchor_name,
            disease_code=rule.disease_code,
            disease_name=anchor_name,
            confidence=confidence,
            message=rule.conclusion.message,
            suggested_action=rule.conclusion.action,
            priority=rule.priority,
            evidence=traces,
            guideline_source=f"{rule.source.guideline_title} {rule.source.section}" if rule.source else "",
            exclusions_checked=rule.exclusions,
            matched_at=now,
            conclusion_type=rule.conclusion.conclusion_type,
            treatment_plan_id=rule.conclusion.treatment_plan_id,
            treatment_name=rule.conclusion.treatment_name,
            treatment_items=list(rule.conclusion.treatment_items or []),
            priority_label=rule.conclusion.priority,
            treatment_method_code=rule.conclusion.treatment_method_code,
            formula_code=rule.conclusion.formula_code,
            formula_name=rule.conclusion.formula_name,
            herbs=list(rule.conclusion.herbs or []),
            modifications=list(rule.conclusion.modifications or []),
            course=rule.conclusion.course,
            score=score,
            matched_conditions=matched_conditions,
            total_conditions=total_conditions,
            triggered_by=trigger_ids or [],
        )

    def _resolve_anchor_name(self, anchor_type: str, code: str, fallback: str = "") -> str:
        getter = getattr(self._current_kg, "get_node_name", None)
        if callable(getter):
            name = getter(anchor_type, code)
            if name:
                return name
        disease = self._current_kg.get_disease(code)
        return disease.name_cn if disease else fallback

    @staticmethod
    def _count_leaves(condition: RuleCondition) -> int:
        if condition.type == "group":
            return sum(InferenceEngine._count_leaves(child) for child in condition.children)
        return 1

