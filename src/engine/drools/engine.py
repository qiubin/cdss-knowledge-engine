# src/engine/drools/engine.py
"""Drools-backed rule engine: matching in the sidecar, conclusions native.

The native engine stays the source of truth.  Per inference stage the adapter
compiles compilable rules to DRL, projects pre-evaluated atoms as facts, asks
the sidecar which rules match, then rebuilds Conclusions through the native
evaluator so evidence traces and scores are identical to native mode.  Rules
with uncompilable trees (weighted scoring, facts, graph evidence) always
evaluate natively; a sidecar failure falls back to the native engine for the
whole run when ``fail_over_native`` is set.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ...models.conclusion import Conclusion, EvidenceTrace
from ...models.patient import PatientSnapshot
from ...models.rule import Rule
from .client import DroolsClient, DroolsClientError
from .compiler import CompiledRule, compile_rule, compile_rule_set

logger = logging.getLogger(__name__)


class DroolsRuleEngine:
    name = "drools"

    def __init__(
        self,
        native,
        base_url: str = "http://127.0.0.1:8090",
        timeout_seconds: float = 5.0,
        fail_over_native: bool = True,
        transport=None,
    ):
        self._native = native
        self._client = DroolsClient(base_url, timeout_seconds, transport=transport)
        self._fail_over_native = bool(fail_over_native)
        self._fallback = False
        self.last_fallback_reason = ""

    @property
    def effective_name(self) -> str:
        return "native" if self._fallback else self.name

    def describe(self) -> Dict[str, Any]:
        return {
            "requested": self.name,
            "used": self.effective_name,
            "fallback_reason": self.last_fallback_reason,
        }

    def run(
        self,
        patient: PatientSnapshot,
        department: Optional[str] = None,
        include_draft_patterns: bool = False,
    ) -> List[Conclusion]:
        self._fallback = False
        self.last_fallback_reason = ""
        try:
            return self._native._run_stages(
                patient, department, include_draft_patterns,
                matcher=self._guarded_stage_match,
            )
        except DroolsClientError as exc:
            if not self._fail_over_native:
                raise
            self._fallback = True
            self.last_fallback_reason = str(exc)
            logger.warning("Drools sidecar unavailable, falling back to native: %s", exc)
            return self._native.run(patient, department=department,
                                    include_draft_patterns=include_draft_patterns)

    def _guarded_stage_match(self, stage_rules, patient):
        if self._fallback:
            return self._native._native_match_stage(stage_rules, patient)
        try:
            return self._stage_match(stage_rules, patient)
        except DroolsClientError as exc:
            if not self._fail_over_native:
                raise
            self._fallback = True
            self.last_fallback_reason = str(exc)
            logger.warning("Drools sidecar unavailable mid-run, falling back: %s", exc)
            return self._native._native_match_stage(stage_rules, patient)

    def _stage_match(
        self,
        stage_rules: List[Rule],
        patient: PatientSnapshot,
    ) -> Dict[str, Tuple[List[EvidenceTrace], Dict[str, Any]]]:
        results: Dict[str, Tuple[List[EvidenceTrace], Dict[str, Any]]] = {}
        compiled_items: List[CompiledRule] = []
        compiled_rules: Dict[str, Rule] = {}
        for rule in stage_rules:
            item = compile_rule(rule)
            if item is None:
                matched, traces, weighted = self._native._eval_rule_with_graph(rule, patient)
                if matched:
                    results[rule.rule_id] = (traces, weighted)
                continue
            compiled_items.append(item)
            compiled_rules[rule.rule_id] = rule
        if not compiled_items:
            return results

        facts: List[Dict[str, str]] = []
        for item in compiled_items:
            for index, leaf in enumerate(item.leaves):
                # 编译叶子不引用 per-rule 图谱（无 evidence 类型），可直接求值
                matched, _ = self._native._evaluate_condition(leaf, patient)
                if matched:
                    facts.append({"ruleId": item.rule_id, "code": f"a{index}"})
        matched_ids = set(self._client.execute(compile_rule_set(compiled_items), facts))
        for rule_id in matched_ids:
            rule = compiled_rules.get(rule_id)
            if rule is None:
                continue
            # 用原生层重建结论与溯源（原子已被缓存，代价低）
            matched, traces, weighted = self._native._eval_rule_with_graph(rule, patient)
            if matched:
                results[rule_id] = (traces, weighted)
        return results
