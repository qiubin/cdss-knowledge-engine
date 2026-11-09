"""Dynamic trend evaluator - checks serial measurements for rising/falling patterns."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...models.patient import LabResult, PatientSnapshot
from ...models.standard import LabInterpretationStandard
from .base import BaseEvaluator, EvaluationResult


class DynamicTrendEvaluator(BaseEvaluator):
    """Evaluates DYNAMIC_RISE / DYNAMIC_FALL / PEAK_SHIFT.

    Requires multiple lab results over time in the patient snapshot.
    """

    def evaluate(
        self,
        patient: PatientSnapshot,
        standard: Optional[LabInterpretationStandard],
        parameter: Optional[Dict[str, Any]] = None,
    ) -> EvaluationResult:
        if standard is None:
            return EvaluationResult(matched=False, evidence_description="标准未配置")

        lab_code = standard.lab_code
        series = patient.get_lab_series(lab_code)

        if len(series) < 2:
            return EvaluationResult(
                matched=False,
                evidence_description=f"{lab_code} 需要至少2次测量值评估动态变化（当前{len(series)}次）",
            )

        values = [r.value for r in series]

        # DYNAMIC_RISE: consistently rising
        if standard.concept_code == "DYNAMIC_RISE":
            is_rising = all(values[i] < values[i+1] for i in range(len(values)-1))
            return EvaluationResult(
                matched=is_rising,
                evidence_description=f"{lab_code} 动态上升 ({len(series)}次: {values})",
                patient_value_display=f"{lab_code} 连续测量值：{[f'{v:.1f}' for v in values]}",
                standard_used="连续测量值递增",
                detail={"values": values, "trend": "rising" if is_rising else "not_rising"},
            )

        # DYNAMIC_FALL: consistently falling
        if standard.concept_code == "DYNAMIC_FALL":
            is_falling = all(values[i] > values[i+1] for i in range(len(values)-1))
            return EvaluationResult(
                matched=is_falling,
                evidence_description=f"{lab_code} 动态下降",
                patient_value_display=f"{lab_code} 连续测量值：{values}",
                standard_used="连续测量值递减",
            )

        # PEAK_SHIFT: peak occurs earlier than a threshold time
        if standard.concept_code == "PEAK_SHIFT":
            threshold_hours = (parameter or {}).get("threshold_hours", 14)
            peak_result = self._find_peak(series)
            if peak_result is None:
                return EvaluationResult(matched=False, evidence_description=f"{lab_code} 无法确定峰值")
            peak_lab, peak_hours = peak_result
            matched = peak_hours < threshold_hours
            return EvaluationResult(
                matched=matched,
                evidence_description=f"{lab_code} 峰值前移 (<{threshold_hours}h, 实际{peak_hours:.1f}h)",
                patient_value_display=f"{lab_code} 峰值={peak_lab.value:.1f}，于 {peak_hours:.1f} 小时出现",
                standard_used=f"峰值时间 < {threshold_hours}h",
                detail={"peak_value": peak_lab.value, "peak_hours": peak_hours, "threshold_hours": threshold_hours},
            )

        return EvaluationResult(matched=False, evidence_description=f"未知动态概念: {standard.concept_code}")

    def _find_peak(self, series: List[LabResult]):
        """Find the peak value and its time offset from first measurement."""
        if not series:
            return None
        first = series[0]
        first_time = first.reported_at or first.collected_at
        if first_time is None:
            return None
        best = None
        for r in series:
            t = r.reported_at or r.collected_at
            if t is None or r.value is None:
                continue
            hours = (t - first_time).total_seconds() / 3600.0
            if best is None or r.value > best[0].value:
                best = (r, hours)
        return best

