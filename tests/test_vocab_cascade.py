# tests/test_vocab_cascade.py
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.terminology.labels import TerminologyLabels
from src.vocab.cascade import (
    affected_rule_ids,
    apply_code_remap,
    apply_name_cascade,
    collect_rule_codes,
    usage_in_graphs,
)


def _labels(tmp_path):
    entity_dir = tmp_path / "entities"
    entity_dir.mkdir(exist_ok=True)
    (entity_dir / "lab.json").write_text(
        json.dumps([{"code": "CTN", "name": "心肌肌钙蛋白"}]), encoding="utf-8")
    (entity_dir / "disease.json").write_text(
        json.dumps([{"code": "I21.0", "name": "急性心肌梗死"}]), encoding="utf-8")
    (entity_dir / "symptom.json").write_text(
        json.dumps([{"code": "SYM_PAIN", "name": "胸痛"}]), encoding="utf-8")
    (tmp_path / "concepts.json").write_text(
        json.dumps([{"code": "ELEVATED", "name_cn": "升高"}]), encoding="utf-8")
    return TerminologyLabels(entity_dir=entity_dir, concept_path=tmp_path / "concepts.json")


def _rules_file(tmp_path, rules):
    path = tmp_path / "managed_rules.json"
    path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    return path


def _sample_rule():
    return {
        "rule_id": "CAS-1",
        "disease_code": "I21.0",
        "disease_name": "急性心肌梗死",
        "anchor_type": "disease",
        "status": "draft",
        "conditions": {
            "type": "group", "operator": "AND", "children": [
                {"type": "lab_concept", "lab_code": "CTN", "concept_code": "ELEVATED"},
                {"type": "symptom", "symptom_code": "SYM_PAIN"},
            ],
        },
        "conclusion": {"diagnosis": "I21.0", "message": ""},
    }


def test_collect_rule_codes():
    pairs = collect_rule_codes(_sample_rule())
    assert ("disease", "I21.0") in pairs
    assert ("lab", "CTN") in pairs
    assert ("symptom", "SYM_PAIN") in pairs


def test_affected_rule_ids():
    rules = [_sample_rule(), {"rule_id": "OTHER", "disease_code": "C81", "conditions": {"type": "group", "operator": "AND", "children": []}}]
    assert affected_rule_ids("lab", "CTN", rules) == ["CAS-1"]
    assert affected_rule_ids("lab", "BNP", rules) == []


def test_apply_name_cascade_updates_labels(tmp_path):
    path = _rules_file(tmp_path, [_sample_rule()])
    labels = _labels(tmp_path)
    result = apply_name_cascade("lab", "CTN", "肌钙蛋白I", path, labels=labels)
    assert result["updated"] == ["CAS-1"]
    saved = json.loads(path.read_text(encoding="utf-8"))[0]
    assert "肌钙蛋白I（CTN）" in json.dumps(saved["conditions"], ensure_ascii=False)
    assert "肌钙蛋白I" in (saved.get("condition_text") or "")


def test_apply_code_remap(tmp_path):
    path = _rules_file(tmp_path, [_sample_rule()])
    labels = _labels(tmp_path)
    result = apply_code_remap("lab", "CTN", "CTNI", path, labels=labels)
    assert result["updated"] == ["CAS-1"]
    saved = json.loads(path.read_text(encoding="utf-8"))[0]
    leaf = saved["conditions"]["children"][0]
    assert leaf["lab_code"] == "CTNI"


def _fact_med_rule():
    return {
        "rule_id": "CAS-2",
        "disease_code": "I21.0",
        "anchor_type": "disease",
        "status": "draft",
        "conditions": {
            "type": "group", "operator": "AND", "children": [
                {"type": "fact", "fact_type": "syndrome", "code": "TCM_SYN_QI_YIN"},
                {"type": "medication", "code": "ASP"},
            ],
        },
        "conclusion": {"diagnosis": "I21.0", "message": ""},
    }


def test_collect_rule_codes_fact_and_medication():
    pairs = collect_rule_codes(_fact_med_rule())
    assert ("syndrome", "TCM_SYN_QI_YIN") in pairs
    assert ("medication", "ASP") in pairs


def test_apply_code_remap_fact_node(tmp_path):
    path = _rules_file(tmp_path, [_fact_med_rule()])
    labels = _labels(tmp_path)
    result = apply_code_remap("syndrome", "TCM_SYN_QI_YIN", "TCM_SYN_QI_STAG", path, labels=labels)
    assert result["updated"] == ["CAS-2"]
    saved = json.loads(path.read_text(encoding="utf-8"))[0]
    assert saved["conditions"]["children"][0]["code"] == "TCM_SYN_QI_STAG"
    assert saved["disease_code"] == "I21.0"


def test_apply_code_remap_medication_node(tmp_path):
    path = _rules_file(tmp_path, [_fact_med_rule()])
    labels = _labels(tmp_path)
    result = apply_code_remap("medication", "ASP", "ASPIRIN", path, labels=labels)
    assert result["updated"] == ["CAS-2"]
    saved = json.loads(path.read_text(encoding="utf-8"))[0]
    assert saved["conditions"]["children"][1]["code"] == "ASPIRIN"


def test_usage_in_graphs(tmp_path):
    kg_dir = tmp_path / "kg"
    kg_dir.mkdir()
    (kg_dir / "g1.json").write_text(json.dumps({
        "nodes": [{"id": "ENTITY:lab:CTN", "node_type": "lab", "code": "CTN", "name": "CTN"}],
    }), encoding="utf-8")
    (kg_dir / "g2.json").write_text(json.dumps({"nodes": []}), encoding="utf-8")
    assert usage_in_graphs("lab", "CTN", kg_dir) == ["g1.json"]
    assert usage_in_graphs("lab", "BNP", kg_dir) == []
