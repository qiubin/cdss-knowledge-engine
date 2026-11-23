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


def test_many_syndrome_names_alone_recognize_tcm():
    """诊疗方案类文档：大量证候名称（无其它强特征）应直接判为中医。

    旧版证候名只加 20 分，30 个证候也会落到“需人工确认”，报
    “指南领域识别置信度不足”。证候体系完整本身就是强中医信号。
    """
    names = [
        "气虚血瘀证", "气阴两虚证", "心阳亏虚证", "水饮凌心证", "心血不足证",
        "痰浊痹阻证", "寒凝心脉证", "心肾阴虚证", "心肾阳虚证", "气虚欲脱证",
        "阴虚火旺证", "阴阳两虚证", "痰热扰心证", "肝阳上亢证", "肝肾阴虚证",
        "脾虚湿盛证", "风痰阻络证", "瘀血阻窍证", "热极生风证", "气血亏虚证",
        "肾精不足证", "痰蒙神窍证", "津亏燥结证", "脾肾阳虚证", "湿热蕴结证",
        "寒湿困脾证", "食滞胃脘证", "胃阴不足证", "肝胃不和证", "心肺气虚证",
    ]
    text = "，".join(names) + "等证候均可出现。"
    result = detect_guideline_domain([{"section": "证治分类", "text": text}], "慢性心衰诊疗方案")
    assert result["domain"] == "tcm"
    assert result["confidence"] == "high"
    assert result["requires_confirmation"] is False


def test_few_syndrome_names_still_require_confirmation():
    """零星几个证候名（如西医指南的中成药附录）不足以自动判为中医。"""
    text = "气虚血瘀证，气阴两虚证，阴虚火旺证，血虚证"
    result = detect_guideline_domain([{"section": "讨论", "text": text}], "冠心病诊疗指南")
    assert result["requires_confirmation"] is True


def test_common_words_ending_in_zheng_not_counted_as_syndromes():
    """“证明／验证／保证”等常用词不是证候名，不应推高中医得分。"""
    text = "多项研究证明该疗法有效，经过临床验证，并有严格的质量保证。"
    result = detect_guideline_domain([{"section": "方法学", "text": text}], "肺癌诊疗指南")
    assert result["domain"] == "western"
    assert result["requires_confirmation"] is False


def test_tcm_plan_style_headings_are_strong_signals():
    """中医诊疗方案常用章节词（证候分类／分证论治）应计为强特征。"""
    text = "证候分类：本病常见气虚血瘀证、气阴两虚证。分证论治：气虚血瘀证治以益气活血。"
    result = detect_guideline_domain([{"section": "证候分类", "text": text}], "心衰（心力衰竭）中医诊疗方案")
    assert result["domain"] == "tcm"
    assert result["confidence"] == "high"
