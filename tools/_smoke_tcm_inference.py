"""End-to-end TCM inference smoke test."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.engine import InferenceEngine
from src.kg.client import JsonGraphClient
from src.kg.schema import ConceptVocabulary
from src.models.patient import PatientSnapshot
from src.rules.repository import JsonRuleRepository
from src.standards.repository import JsonStandardRepository


def main() -> None:
    patient = PatientSnapshot(**json.loads(
        (ROOT / "data" / "patients" / "demo_tcm_patient.json").read_text(encoding="utf-8")
    ))
    vocab = ConceptVocabulary()
    vocab.load(str(ROOT / "data" / "vocab" / "concepts.json"))
    kg = JsonGraphClient(str(ROOT / "data" / "kg" / "tcm_graph.json"))
    rules = JsonRuleRepository(str(ROOT / "data" / "rules"))
    standards = JsonStandardRepository(str(ROOT / "data" / "standards"))

    def graph_factory(filename: str) -> JsonGraphClient:
        return JsonGraphClient(str(ROOT / "data" / "kg" / filename))

    engine = InferenceEngine(rules, kg, standards, vocab, graph_factory, domain="tcm")
    conclusions = engine.run(patient)
    for item in conclusions:
        print(
            item.rule_type,
            item.anchor_type,
            item.anchor_code,
            item.anchor_name,
            "score=", item.score,
            "method=", item.treatment_method_code,
            "formula=", item.formula_name,
            "herbs=", len(item.herbs),
            "triggered_by=", item.triggered_by,
        )
    assert any(item.rule_type == "tcm_pattern" for item in conclusions)
    assert any(item.rule_type == "tcm_treatment" for item in conclusions)
    assert any(item.rule_type == "tcm_prescription" for item in conclusions)
    print("OK")


if __name__ == "__main__":
    main()
