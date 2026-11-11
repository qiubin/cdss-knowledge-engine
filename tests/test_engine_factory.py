# tests/test_engine_factory.py
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.engine import InferenceEngine
from src.engine.factory import create_rule_engine
from src.engine.settings import DEFAULT_SETTINGS


class _StubRepo:
    def list_all(self):
        return []

    def get_active_rules(self, department=None):
        return []

    def get_treatment_candidates(self, department=None):
        return []

    def save_rule(self, rule):
        pass


def _build(domain="western", settings=None):
    from src.kg.schema import ConceptVocabulary
    from src.standards.repository import JsonStandardRepository
    from src.rules.repository import JsonRuleRepository

    project_root = Path(__file__).parent.parent
    vocab = ConceptVocabulary()
    vocab.load(str(project_root / "data" / "vocab" / "concepts.json"))
    return create_rule_engine(
        JsonRuleRepository(str(project_root / "tests" / "fixtures" / "rules")),
        None,
        JsonStandardRepository(str(project_root / "data" / "standards")),
        vocab,
        domain=domain,
        settings=settings,
    )


def test_default_settings_create_native_engine():
    engine = _build(settings=dict(DEFAULT_SETTINGS))
    assert isinstance(engine, InferenceEngine)
    assert engine.name == "native"
    assert engine.describe() == {
        "requested": "native",
        "used": "native",
        "fallback_reason": "",
    }
