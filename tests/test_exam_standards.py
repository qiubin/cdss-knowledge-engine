import asyncio
import json
from datetime import date
import pytest
from fastapi import HTTPException
from src.models.patient import PatientSnapshot, ExamFinding
from src.models.standard import LabInterpretationStandard
from src.models.rule import RuleCondition
from src.engine.evaluators.exam_interpretation import interpret_exam
from src.standards.repository import JsonStandardRepository
from src.standards.coverage import build_coverage
from src.api.routes import standards as api
from tests.test_engine import build_engine


def row(**changes):
    return {"hospital_id": "H", "item_type": "exam", "exam_code": "EX1", "finding_code": "F1", "concept_code": "PRESENT",
        "interpretation_type": "numeric", "operator": ">", "threshold_value": 10, "threshold_unit": "mm", "status": "active", **changes}


def patient(**result):
    return PatientSnapshot(patient_id="TEST", hospital_id="H", exam_findings=[ExamFinding(exam_code="EX1", finding_code="F1", **result)])


def evaluate(standard=None, **result):
    return interpret_exam(patient(**result), LabInterpretationStandard(**standard) if standard else None, {"exam_code": "EX1", "finding_code": "F1", "concept_code": "PRESENT"})


def test_numeric_and_units():
    assert evaluate(row(), value=12, unit="mm").matched
    assert not evaluate(row(), value=8, unit="mm").matched
    assert evaluate(row(), value=12, unit="cm").detail["evaluation_status"] == "unknown"
    assert evaluate(row(), value=12).detail["evaluation_status"] == "unknown"


def test_categories_match_exact_codes_and_not_report_substrings():
    standard = row(interpretation_type="categorical", accepted_values=["G3", "G4"])
    assert evaluate(standard, category="G3").matched
    assert not evaluate(standard, category="G30").matched
    assert evaluate(standard, report_text="未见G3表现").detail["evaluation_status"] == "unknown"


def test_structured_presence_remains_compatible_and_missing_standard_does_not_guess():
    assert evaluate(present=True).matched
    assert not evaluate(present=False).matched
    assert evaluate(value=12, unit="mm").detail["evaluation_status"] == "unknown"
    assert evaluate(report_text="疑似发现").detail["evaluation_status"] == "unknown"
    assert evaluate(present=None).detail["evaluation_status"] == "unknown"
    assert evaluate(row(interpretation_type="presence"), value=12, unit="mm").detail["evaluation_status"] == "unknown"


def test_guideline_explicit_comparison_overrides_hospital_threshold():
    result = interpret_exam(patient(value=12, unit="mm"), LabInterpretationStandard(**row(threshold_value=5)), {
        "exam_code": "EX1", "finding_code": "F1", "concept_code": "PRESENT",
        "interpretation": {"interpretation_type": "numeric", "operator": ">", "threshold_value": 20, "threshold_unit": "mm"},
    })
    assert not result.matched and "指南" in result.standard_used


def test_repository_isolates_types_context_and_validity(tmp_path):
    items = [row(), row(context_gender="F", threshold_value=20),
        {"hospital_id": "H", "lab_code": "EX1", "concept_code": "PRESENT", "threshold_value": 1, "status": "active"}]
    (tmp_path / "h.json").write_text(json.dumps(items), encoding="utf-8")
    repo = JsonStandardRepository(str(tmp_path))
    assert repo.get_active("H", "EX1", "PRESENT").item_type == "lab"
    assert repo.get_exam_active("H", "EX1", "F1", context_gender="U").threshold_value == 10
    assert repo.get_exam_active("H", "EX1", "F1", context_gender="F").threshold_value == 20
    (tmp_path / "h.json").write_text(json.dumps([row(effective_to="2000-01-01")]), encoding="utf-8")
    assert JsonStandardRepository(str(tmp_path)).get_exam_active("H", "EX1", "F1") is None


def test_not_unknown_is_unknown_and_or_known_true_can_still_match(tmp_path):
    engine = build_engine()
    engine.standard_repo = JsonStandardRepository(str(tmp_path))
    leaf = {"type": "exam_finding", "exam_code": "EX1", "finding_code": "F1", "standard_required": True}
    rule = RuleCondition(type="group", operator="NOT", children=[RuleCondition(**leaf)])
    actual = patient(value=12, unit="mm")
    assert engine._evaluate_condition(rule, actual)[0] is None
    assert engine._evaluate_condition(rule, actual)[0] is None  # cached unknown remains unknown
    true_leaf = {"type": "demographic", "age_min": 1}
    actual.age = 30
    either = RuleCondition(type="group", operator="OR", children=[RuleCondition(**leaf), RuleCondition(**true_leaf)])
    assert engine._evaluate_condition(either, actual)[0] is True
    assert engine.evaluation_issues


def test_coverage_includes_evidence_and_required_children_and_pending_bases():
    graph = {"evidences": [{"id": "EV", "data_type": "exam_finding", "exam_code": "EX1", "finding_code": "F1", "concept_code": "PRESENT"}]}
    spec = {"rule_id": "R", "graph_filename": "G.json", "conditions": {"type": "weighted_group", "children": [], "required_children": [{"type": "evidence", "evidence_id": "EV"}]}}
    result = build_coverage("H", [], [(spec, "待审诊疗依据")], {"G.json": graph})
    item = result["items"][0]
    assert item["item_type"] == "exam" and item["status"] == "structured"
    assert item["references"][0]["category"] == "待审诊疗依据"
    result = build_coverage("H", [row(threshold_value=None)], [(spec, "已启用规则")], {"G.json": graph})
    assert result["items"][0]["status"] == "incomplete"


def test_api_activation_validates_parameters_and_separates_lab_and_exam(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "STANDARDS_DIR", tmp_path)
    first = asyncio.run(api.add_standard("H", {"lab_code": "EX1", "concept_code": "PRESENT", "threshold_value": 1}))
    exam = row(threshold_value=None)
    asyncio.run(api.add_standard("H", exam))
    invalid_index = next(i for i, item in enumerate(api._load_hospital("H")) if item.get("item_type") == "exam")
    with pytest.raises(HTTPException): asyncio.run(api.activate_standard("H", invalid_index))
    # Keep direct storage order deterministic for this fixture.
    (tmp_path / "H.json").write_text(json.dumps([first, row(status="draft")]), encoding="utf-8")
    # Retrieval sorts legacy lab keys; use finding identity to locate the exam row.
    items = api._load_hospital("H")
    index = next(i for i, item in enumerate(items) if item.get("item_type") == "exam")
    activated = asyncio.run(api.activate_standard("H", index))
    assert activated["status"] == "active"
    updated = asyncio.run(api.update_standard("H", index, activated))
    assert updated["status"] == "draft" and updated["version"] >= 2
