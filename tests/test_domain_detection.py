from src.extraction.domain_detection import detect_guideline_domain


def test_explicit_domain_always_wins():
    result = detect_guideline_domain([{"text": "中医辨证 舌象 脉象"}], "中医指南", "western")
    assert result["domain"] == "western"
    assert result["confidence"] == "explicit"


def test_tcm_guideline_is_detected_from_multiple_auditable_signals():
    chunks = [{"section": "中医辨证", "text": "中医辨证分型：气虚血瘀证、气阴两虚证、阳气亏虚证。舌质淡，脉细。治法益气活血，方剂补阳还五汤，随证加减。"}]
    result = detect_guideline_domain(chunks, "慢性心力衰竭中医诊疗指南（2022年）")
    assert result["domain"] == "tcm"
    assert result["confidence"] == "high"
    assert result["score"] >= 50


def test_reference_titles_do_not_turn_western_body_into_tcm():
    chunks = [
        {"section": "诊断", "text": "依据影像学和实验室检查明确诊断。"},
        {"section": "参考文献", "text": "中医诊疗指南 中医辨证 舌象脉象 治法方剂"},
    ]
    assert detect_guideline_domain(chunks, "肺癌诊疗指南")["domain"] == "western"
