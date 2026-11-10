"""API smoke test for the TCM domain extension."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)


def check(path: str, method: str = "GET", **kwargs):
    response = client.request(method, path, **kwargs)
    print(method, path, response.status_code)
    assert response.status_code < 400, response.text[:500]
    return response.json()


profiles = check("/api/profiles")
assert {item["domain"] for item in profiles} == {"western", "tcm"}
tcm_profile = check("/api/profiles/tcm")
assert "syndrome" in tcm_profile["anchor_types"]

templates = check("/api/templates?domain=tcm")
assert any(item["template_id"] == "TCM_GUIDELINE_V2" for item in templates)

graphs = check("/api/kg/graphs?domain=tcm")
assert any(item["graph_filename"] == "KG-TCM-DEMO.json" for item in graphs)
graph = check("/api/kg/graph?graph_filename=KG-TCM-DEMO.json")
assert graph["domain"] == "tcm"
assert graph["profile"]["domain"] == "tcm"
assert len(graph["nodes"]) > 0

dictionaries = check("/api/vocab/entities")
assert any(item["entity_type"] == "syndrome" for item in dictionaries)
syndromes = check("/api/vocab/entities/syndrome")
assert len(syndromes) >= 3

rules = check("/api/rules")
tcm_rules = [item for item in rules if item.get("domain") == "tcm"]
assert len(tcm_rules) >= 9
sync = check("/api/rules/sync-check")
tcm_sync = [item for item in sync["rules"] if item.get("domain") == "tcm"]
assert tcm_sync and all(item["semantic_status"] == "consistent" for item in tcm_sync), tcm_sync[:2]

patient = check("/api/inference/demo-patient?domain=tcm")
result = check("/api/inference/run", method="POST", json={"patient": patient, "domain": "tcm"})
types = {item["rule_type"] for item in result["conclusions"]}
assert "tcm_pattern" in types and "tcm_treatment" in types and "tcm_prescription" in types, types
prescription = next(item for item in result["conclusions"] if item["rule_type"] == "tcm_prescription")
assert prescription["formula_name"] and prescription["herbs"]
print("conclusion types:", sorted(types))
print("formula:", prescription["formula_name"], "herbs:", len(prescription["herbs"]))
print("API OK")
