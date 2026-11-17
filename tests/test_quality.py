import json
from types import SimpleNamespace
import pytest
from src.review.quality import verify_source, coverage_report, statement_for


@pytest.fixture
def source_env(tmp_path):
    path = tmp_path / "DOC-TEST.parsed.json"
    path.write_text(json.dumps({"parsed": {"lines": [
        {"page": 1, "line": 1, "text": "适用于测试人群，符合条件A时推荐方案B。"},
        {"page": 1, "line": 2, "text": "条件C为例外，需另行评估。"}]}}), encoding="utf-8")
    return tmp_path, {"guideline_id": "DOC-TEST", "page_start": 1, "line_start": 1, "line_end": 2,
                      "quote": "符合条件A时推荐方案B。"}


def test_exact_source_is_verified_but_semantics_still_require_review(source_env):
    root, source = source_env
    result = verify_source(source, root)
    assert result["status"] == "verified"
    assert result["semantic_review_required"]


@pytest.mark.parametrize("changes", [
    {"quote": "不符合条件A时推荐方案B"}, {"line_end": 99}, {"line_start": 3, "line_end": 2},
    {"quote": ""}, {"guideline_id": "../../secret"}, {"guideline_id": "DOC-MISSING"},
])
def test_bad_source_cannot_pass(source_env, changes):
    root, source = source_env
    assert verify_source({**source, **changes}, root)["status"] != "verified"


def test_incomplete_coverage_blocks_source_even_if_quote_matches(source_env):
    root, source = source_env
    path = root / "DOC-TEST.parsed.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["extraction"] = {"metadata": {"coverage": {"complete": False}}}
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert verify_source(source, root)["status"] == "invalid"


def test_coverage_distinguishes_budget_limit_and_failure():
    chunks = [{"chunk_id": str(i), "section": "测试章节"} for i in range(3)]
    report = coverage_report(chunks, {"0"}, {"0", "1"})
    assert [item["status"] for item in report["items"]] == ["processed", "failed", "not_scheduled"]
    assert not report["complete"]


def test_statement_uses_same_condition_and_conclusion():
    spec = {"conditions": {"type": "symptom", "symptom_code": "A"}, "conclusion": {"diagnosis": "D"},
            "clinical_context": {"population": "测试人群", "exceptions": ["C"]}}
    statement = statement_for(spec)
    assert statement["conditions"] is spec["conditions"]
    assert statement["recommendation"] is spec["conclusion"]
    assert statement["exceptions"] == ["C"]


def test_cached_source_is_rechecked_after_document_changes(source_env):
    root, source = source_env
    assert verify_source(source, root)["status"] == "verified"
    path = root / "DOC-TEST.parsed.json"
    path.write_text(json.dumps({"parsed": {"lines": [
        {"page": 1, "line": 1, "text": "已替换的原文，不再支持原引文"},
        {"page": 1, "line": 2, "text": "新说明"}]}}), encoding="utf-8")
    assert verify_source(source, root)["status"] == "invalid"


def test_cross_page_line_numbers_may_restart(source_env):
    root, source = source_env
    (root / "DOC-TEST.parsed.json").write_text(json.dumps({"parsed": {"lines": [
        {"page": 1, "line": 60, "text": "跨页引用"},
        {"page": 2, "line": 1, "text": "接续原文"}]}}), encoding="utf-8")
    assert verify_source({**source, "page_end": 2, "line_start": 60, "line_end": 1,
                          "quote": "跨页引用接续原文"}, root)["status"] == "verified"


def test_mixed_text_and_image_pdf_runs_ocr_on_image_pages(monkeypatch):
    import sys
    from src.api.routes import pdf
    pages = [SimpleNamespace(get_text=lambda mode: {"blocks": [{"type": 0, "lines": [
        {"spans": [{"text": "测试文字"}], "bbox": [0, 0, 10, 10]}]}]}),
        SimpleNamespace(get_text=lambda mode: {"blocks": [{"type": 1}]})]
    class Document:
        page_count = 2
        def __iter__(self): return iter(pages)
        def close(self): pass
    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=lambda path: Document()))
    captured = []
    def ocr(path, page_numbers=None):
        captured.append(page_numbers)
        return [{"page": 2, "line": 1, "text": "扫描页文字", "bbox": [0, 0, 10, 10]}]
    monkeypatch.setattr(pdf, "_ocr_pdf_lines", ocr)
    result = pdf.parse_pdf("unused.pdf", "DOC-TEST")
    assert captured == [{2}]
    assert result["reading_quality"]["pages_with_text"] == [1, 2]
