# src/engine/factory.py
"""Create the active rule engine from data/engine.json."""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..kg.client import KnowledgeGraphClient
from ..kg.schema import ConceptVocabulary
from ..rules.repository import RuleRepository
from ..standards.repository import StandardRepository
from .base import RuleEngine
from .inference_engine import InferenceEngine
from .settings import load_settings


def create_rule_engine(
    rule_repo: RuleRepository,
    kg_client: KnowledgeGraphClient,
    standard_repo: StandardRepository,
    vocabulary: ConceptVocabulary,
    graph_client_factory=None,
    domain: str = "western",
    settings: Optional[Dict[str, Any]] = None,
    terminology=None,
) -> RuleEngine:
    """Build the configured engine. Drools failures fall back per its config."""
    native = InferenceEngine(
        rule_repo, kg_client, standard_repo, vocabulary,
        graph_client_factory, domain=domain, terminology=terminology,
    )
    config = settings if settings is not None else load_settings()
    if str(config.get("active") or "native") != "drools":
        return native
    try:
        from .drools.engine import DroolsRuleEngine

        drools_config = dict(config.get("drools") or {})
        return DroolsRuleEngine(native, **drools_config)
    except ImportError:
        return native
