"""Evaluator factory - registry pattern for pluggable evaluators."""
from __future__ import annotations

from typing import Dict, Type

from .base import BaseEvaluator
from .static_threshold import StaticThresholdEvaluator
from .uln_multiplier import UlnMultiplierEvaluator
from .dynamic_trend import DynamicTrendEvaluator
from .qualitative import QualitativeEvaluator
from .finding_exists import FindingExistsEvaluator
from .duration_check import DurationCheckEvaluator
from .observation_present import ObservationPresentEvaluator


class EvaluatorFactory:
    """Creates evaluators by type name. Extensible via register()."""

    _registry: Dict[str, Type[BaseEvaluator]] = {}

    @classmethod
    def register(cls, evaluator_type: str, evaluator_class: Type[BaseEvaluator]) -> None:
        cls._registry[evaluator_type] = evaluator_class

    @classmethod
    def create(cls, evaluator_type: str) -> BaseEvaluator:
        klass = cls._registry.get(evaluator_type)
        if klass is None:
            raise ValueError(f"未注册的评估器类型: {evaluator_type}。可用: {list(cls._registry.keys())}")
        return klass()

    @classmethod
    def list_types(cls):
        return list(cls._registry.keys())


# Register built-in evaluators
EvaluatorFactory.register("static_threshold", StaticThresholdEvaluator)
EvaluatorFactory.register("uln_multiplier", UlnMultiplierEvaluator)
EvaluatorFactory.register("dynamic_trend", DynamicTrendEvaluator)
EvaluatorFactory.register("qualitative", QualitativeEvaluator)
EvaluatorFactory.register("finding_exists", FindingExistsEvaluator)
EvaluatorFactory.register("duration_check", DurationCheckEvaluator)
EvaluatorFactory.register("observation_present", ObservationPresentEvaluator)

