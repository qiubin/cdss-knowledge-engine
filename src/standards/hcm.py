"""Source-backed adult HCM measurement templates; loading never activates a row."""
SOURCE = "中国成人肥厚型心肌病诊断与治疗指南（2023）"


def hcm_templates(hospital_id=""):
    rows = []
    for finding, name in [
        ("LVOT_PEAK_GRADIENT_REST", "静息左室流出道峰值压差"),
        ("LVOT_PEAK_GRADIENT_PROVOKED", "激发后左室流出道峰值压差"),
    ]:
        for concept, operator, threshold in [("GE_30", ">=", 30), ("LT_30", "<", 30), ("GE_50", ">=", 50)]:
            rows.append(dict(exam_code="ECHO", exam_name="超声心动图", finding_code=finding,
                finding_name=name, concept_code=concept, operator=operator,
                threshold_value=threshold, threshold_unit="mmHg",
                source_note=(SOURCE + " §1.1：梗阻阈值≥30 mmHg；2024 AHA/ACC HCM指南§LVOTO：≥50 mmHg用于结合症状评估进一步治疗，并非梗阻诊断阈值。"
                    "测量LVOT峰值瞬时压差，激发须记录有效方法；不能用主动脉瓣跨瓣或心室中部压差替代。")))
    for exam, name in [("ECHO", "超声心动图"), ("CMR", "心脏磁共振")]:
        for concept, operator, threshold in [("GE_15", ">=", 15), ("GE_13", ">=", 13), ("LT_15", "<", 15)]:
            rows.append(dict(exam_code=exam, exam_name=name,
                finding_code="LV_MAX_END_DIASTOLIC_WALL_THICKNESS", finding_name="左室舒张末期最大室壁厚度",
                concept_code=concept, operator=operator, threshold_value=threshold, threshold_unit="mm",
                source_note=SOURCE + " §1：成人壁厚≥15 mm需结合病因排除；≥13 mm分支需结合致病基因或遗传受累家系证据，单个阈值不等于HCM确诊。"))
    return [dict(hospital_id=hospital_id, item_type="exam", interpretation_type="numeric",
        context_age_min=18, status="draft", version=1, ref_source="STATIC", **row) for row in rows]
