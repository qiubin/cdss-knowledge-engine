"""Seed TCM entity dictionaries and concept predicates.

Run once to create the starter terminology set.  All entries are local stable
codes with an optional ``standard_code`` mapping and are intended for medical
review before production use.
"""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path("data/vocab/entities")


def entry(code, name, aliases=None, std="", source="自建种子", attrs=None,
          domain="", standard_system=""):
    return {
        "code": code,
        "name": name,
        "aliases": aliases or [name],
        "domain": domain,
        "standard_system": standard_system,
        "standard_code": std,
        "source": source,
        "attributes": attrs or {},
    }


def western_disease_entries():
    """ICD-10 western diseases merged into the shared disease dictionary."""
    path = Path("data/vocab/diseases.json")
    if not path.exists():
        return []
    entries = []
    for item in json.loads(path.read_text(encoding="utf-8")):
        code = str(item.get("code") or "")
        if not code:
            continue
        entries.append(entry(
            code,
            str(item.get("name") or code),
            list(item.get("aliases") or []),
            std=code,
            source="ICD-10",
            domain="western",
            standard_system="ICD-10",
        ))
    return entries


DATA = {
    "disease": [
        entry("TCM_DIS_XIONG_BI_XIN_TONG", "胸痹心痛病", ["胸痹心痛病", "胸痹", "心痛病"], "ICD11-TM1:SF01", domain="tcm", standard_system="ICD-11 TM1"),
        entry("TCM_DIS_XIAO_KE", "消渴病", ["消渴病", "消渴"], "ICD11-TM1:SD30", domain="tcm", standard_system="ICD-11 TM1"),
    ],
    "syndrome": [
        entry("TCM_SYN_QI_YIN_XU", "气阴两虚证", ["气阴两虚证", "气阴两虚", "气阴亏虚证"], "自建:TCM-SYN-001"),
        entry("TCM_SYN_TAN_ZHUO_BI_ZU", "痰浊闭阻证", ["痰浊闭阻证", "痰浊闭阻", "痰浊壅塞证", "痰湿阻遏证"], "自建:TCM-SYN-002"),
        entry("TCM_SYN_XIN_XUE_YU_ZU", "心血瘀阻证", ["心血瘀阻证", "心血瘀阻", "心脉瘀阻证", "血瘀证"], "自建:TCM-SYN-003"),
        entry("TCM_SYN_XIN_SHEN_YIN_XU", "心肾阴虚证", ["心肾阴虚证", "心肾阴虚"], "自建:TCM-SYN-004"),
        entry("TCM_SYN_YANG_QI_XU_SHUAI", "阳气虚衰证", ["阳气虚衰证", "阳气虚衰", "心阳不振证"], "自建:TCM-SYN-005"),
    ],
    "symptom": [
        entry("PRURITUS", "瘙痒", ["瘙痒", "瘙痒不适"], "自建:SHARED-001"),
        entry("SYM_CHEST_TIGHTNESS", "胸闷", ["胸闷", "胸中窒闷", "胸闷不舒"]),
        entry("SYM_CHEST_PAIN", "胸痛", ["胸痛", "心痛", "胸膺疼痛"]),
        entry("SYM_PALPITATION", "心悸", ["心悸", "心慌", "惊悸"]),
        entry("SYM_SHORTNESS_BREATH", "气短", ["气短", "短气", "气促"]),
        entry("SYM_FATIGUE", "乏力", ["乏力", "疲乏", "倦怠", "神疲"]),
        entry("SYM_SPONTANEOUS_SWEAT", "自汗", ["自汗"]),
        entry("SYM_NIGHT_SWEAT", "盗汗", ["盗汗", "夜寐汗出"]),
        entry("SYM_DRY_MOUTH", "口干", ["口干", "口渴", "咽干"]),
        entry("SYM_DIZZINESS", "头晕", ["头晕", "眩晕"]),
        entry("SYM_POOR_APPETITE", "纳呆", ["纳呆", "食少", "食欲不振"]),
        entry("SYM_EXCESS_PHLEGM", "痰多", ["痰多", "咳痰", "痰涎"]),
        entry("SYM_INSOMNIA", "失眠", ["失眠", "不寐", "眠差"]),
        entry("SYM_PALE_COMPLEXION", "面色少华", ["面色少华", "面色苍白", "面色无华"]),
        entry("SYM_AVERSION_TO_COLD", "畏寒", ["畏寒", "怕冷", "形寒"]),
        entry("SYM_BODY_HEAVINESS", "肢体沉重", ["肢体沉重", "身重"]),
        entry("SYM_CYANOTIC_LIPS", "唇甲紫暗", ["唇甲紫暗", "口唇紫暗", "爪甲青紫"]),
    ],
    "tongue": [
        entry("TCM_TONGUE_PALE_RED", "舌淡红", ["舌淡红", "舌质淡红"]),
        entry("TCM_TONGUE_RED", "舌红", ["舌红", "舌质红"]),
        entry("TCM_TONGUE_PALE_FLABBY", "舌淡胖", ["舌淡胖", "舌体胖大", "舌胖"]),
        entry("TCM_TONGUE_PURPLE_DARK", "舌紫暗", ["舌紫暗", "舌质紫暗", "舌暗"]),
        entry("TCM_TONGUE_ECCHYMOSIS", "舌有瘀斑", ["舌有瘀斑", "舌见瘀点", "瘀斑舌"]),
        entry("TCM_COATING_THIN_WHITE", "苔薄白", ["苔薄白", "舌苔薄白"]),
        entry("TCM_COATING_WHITE_GREASY", "苔白腻", ["苔白腻", "舌苔白腻"]),
        entry("TCM_COATING_SCANT", "苔少", ["苔少", "少苔", "苔剥"]),
    ],
    "pulse": [
        entry("TCM_PULSE_THREADY_WEAK", "脉细弱", ["脉细弱", "脉弱"]),
        entry("TCM_PULSE_WIRY_SLIPPERY", "脉弦滑", ["脉弦滑", "脉滑弦"]),
        entry("TCM_PULSE_CHOPPY", "脉涩", ["脉涩", "脉涩滞"]),
        entry("TCM_PULSE_INTERMITTENT", "脉结代", ["脉结代", "结代脉", "脉结"]),
        entry("TCM_PULSE_THREADY_FAST", "脉细数", ["脉细数"]),
        entry("TCM_PULSE_DEEP_THREADY", "脉沉细", ["脉沉细", "脉沉"]),
        entry("TCM_PULSE_WIRY", "脉弦", ["脉弦"]),
        entry("TCM_PULSE_SLIPPERY", "脉滑", ["脉滑"]),
    ],
    "sign": [
        entry("TCM_SIGN_EDEMA", "下肢水肿", ["下肢水肿", "双下肢浮肿", "水肿"]),
        entry("TCM_SIGN_JVD", "颈静脉怒张", ["颈静脉怒张", "颈静脉充盈"]),
    ],
    "pathogenesis": [
        entry("TCM_PATH_QI_XU", "气虚", ["气虚"]),
        entry("TCM_PATH_YIN_XU", "阴虚", ["阴虚"]),
        entry("TCM_PATH_TAN_ZHUO", "痰浊", ["痰浊", "痰湿"]),
        entry("TCM_PATH_XUE_YU", "血瘀", ["血瘀", "瘀血"]),
        entry("TCM_PATH_XINMAI_BIZU", "心脉痹阻", ["心脉痹阻", "心脉瘀阻"]),
        entry("TCM_PATH_YANG_QI_XU_SHUAI", "阳气虚衰", ["阳气虚衰", "心阳不振"]),
    ],
    "treatment_method": [
        entry("TCM_METHOD_YIQI_YANGYIN", "益气养阴", ["益气养阴", "益气滋阴", "养阴益气"]),
        entry("TCM_METHOD_TONGYANG_XIEZHUO_HUATAN", "通阳泄浊豁痰", ["通阳泄浊豁痰", "通阳泄浊", "豁痰宣痹"]),
        entry("TCM_METHOD_HUOXUE_HUAYU_TONGLUO", "活血化瘀通络", ["活血化瘀通络", "活血化瘀", "化瘀通络"]),
        entry("TCM_METHOD_WENBU_XINYANG", "温补心阳", ["温补心阳", "温阳益气"]),
    ],
    "formula": [
        entry("TCM_FORMULA_SHENGMAI_SAN", "生脉散", ["生脉散", "生脉饮"]),
        entry("TCM_FORMULA_GUALOU_XIEBAI_BANXIA_TANG", "瓜蒌薤白半夏汤", ["瓜蒌薤白半夏汤", "栝楼薤白半夏汤"]),
        entry("TCM_FORMULA_XUEFU_ZHUYU_TANG", "血府逐瘀汤", ["血府逐瘀汤"]),
        entry("TCM_FORMULA_SHENFU_TANG", "参附汤", ["参附汤"]),
    ],
    "contraindication": [
        entry("TCM_CONTRA_PREGNANCY", "孕妇慎用", ["孕妇慎用", "妊娠慎用", "孕妇忌用"]),
        entry("TCM_CONTRA_BLEEDING", "出血倾向者禁用", ["出血倾向者禁用", "有出血倾向者慎用"]),
    ],
}


def herb(code, name, role="", dose="", aliases=None):
    return entry(code, name, aliases or [name], attrs={"HERB_ROLE": role, "HERB_DOSE": dose})


DATA["herb"] = [
    herb("TCM_HERB_RENSHEN", "人参", "君", "10g", ["人参", "红参"]),
    herb("TCM_HERB_MAIDONG", "麦冬", "臣", "15g", ["麦冬", "麦门冬"]),
    herb("TCM_HERB_WUWEIZI", "五味子", "佐", "6g", ["五味子"]),
    herb("TCM_HERB_GUALOU", "瓜蒌", "君", "15g", ["瓜蒌", "栝楼"]),
    herb("TCM_HERB_XIEBAI", "薤白", "臣", "12g", ["薤白"]),
    herb("TCM_HERB_BANXIA", "半夏", "佐", "9g", ["半夏", "法半夏", "姜半夏"]),
    herb("TCM_HERB_TAOREN", "桃仁", "君", "12g", ["桃仁"]),
    herb("TCM_HERB_HONGHUA", "红花", "君", "9g", ["红花"]),
    herb("TCM_HERB_DANGGUI", "当归", "臣", "9g", ["当归"]),
    herb("TCM_HERB_CHUANXIONG", "川芎", "臣", "6g", ["川芎"]),
    herb("TCM_HERB_CHISHAO", "赤芍", "臣", "6g", ["赤芍"]),
    herb("TCM_HERB_CHAIHU", "柴胡", "佐", "3g", ["柴胡"]),
    herb("TCM_HERB_ZHIGU", "枳壳", "佐", "6g", ["枳壳"]),
    herb("TCM_HERB_JIEGENG", "桔梗", "佐", "6g", ["桔梗"]),
    herb("TCM_HERB_NIUXI", "牛膝", "佐", "9g", ["牛膝", "川牛膝"]),
    herb("TCM_HERB_GANCAO", "甘草", "使", "6g", ["甘草", "炙甘草"]),
    herb("TCM_HERB_FUZI", "附子", "君", "9g", ["附子", "制附子"]),
    herb("TCM_HERB_HUANGQI", "黄芪", "", "", ["黄芪", "炙黄芪"]),
    herb("TCM_HERB_BAIZHU", "白术", "", "", ["白术", "炒白术"]),
    herb("TCM_HERB_FULING", "茯苓", "", "", ["茯苓"]),
    herb("TCM_HERB_CHENPI", "陈皮", "", "", ["陈皮"]),
    herb("TCM_HERB_SHENGJIANG", "生姜", "", "", ["生姜"]),
    herb("TCM_HERB_GANSUI", "甘遂", "", "", ["甘遂"]),
    herb("TCM_HERB_DAJI", "大戟", "", "", ["大戟", "京大戟"]),
    herb("TCM_HERB_HAIZAO", "海藻", "", "", ["海藻"]),
    herb("TCM_HERB_YUANHUA", "芫花", "", "", ["芫花"]),
    herb("TCM_HERB_WUTOU", "乌头", "", "", ["乌头", "川乌", "草乌"]),
    herb("TCM_HERB_BEIMU", "贝母", "", "", ["贝母", "川贝母", "浙贝母"]),
    herb("TCM_HERB_BAILIAN", "白蔹", "", "", ["白蔹"]),
    herb("TCM_HERB_BAIJI", "白及", "", "", ["白及"]),
    herb("TCM_HERB_LILU", "藜芦", "", "", ["藜芦"]),
    herb("TCM_HERB_DANSHEN", "丹参", "", "", ["丹参"]),
    herb("TCM_HERB_XUANSHEN", "玄参", "", "", ["玄参"]),
    herb("TCM_HERB_XIXIN", "细辛", "", "", ["细辛"]),
    herb("TCM_HERB_SHAOYAO", "芍药", "", "", ["芍药", "白芍"]),
]

META = {
    "disease": {"label": "疾病（ICD-10）", "domain": "western", "color": "#c05621"},
    "syndrome": {"label": "证候", "domain": "tcm", "color": "#d69e2e"},
    "symptom": {"label": "症状", "domain": "shared", "color": "#f687b3"},
    "tongue": {"label": "舌象", "domain": "tcm", "color": "#ed8936"},
    "pulse": {"label": "脉象", "domain": "tcm", "color": "#9f7aea"},
    "sign": {"label": "体征", "domain": "shared", "color": "#b794f4"},
    "pathogenesis": {"label": "病机", "domain": "tcm", "color": "#a0aec0"},
    "treatment_method": {"label": "治法", "domain": "tcm", "color": "#38a169"},
    "formula": {"label": "方剂", "domain": "tcm", "color": "#2f855a"},
    "herb": {"label": "中药", "domain": "tcm", "color": "#68d391"},
    "contraindication": {"label": "禁忌", "domain": "shared", "color": "#fc8181"},
    "concept": {"label": "概念", "domain": "shared", "color": "#4fd1c5"},
    "diagnostic_evidence": {"label": "诊断证据", "domain": "western", "color": "#4fd1c5"},
}


def _sync_tcm_template_diseases(tcm_diseases) -> None:
    """Keep TCM disease names as template-local entries, not shared ICD-10."""
    path = Path("data/templates/extraction_templates.json")
    if not path.exists():
        return
    templates = json.loads(path.read_text(encoding="utf-8"))
    for template in templates:
        if template.get("template_id") != "TCM_GUIDELINE_V1":
            continue
        for entity_type in template.get("entity_types", []):
            if entity_type.get("entity_type") == "disease":
                entity_type["entries"] = list(tcm_diseases)
    path.write_text(json.dumps(templates, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    tcm_diseases = list(DATA.get("disease", []))
    for entity_type, entries in DATA.items():
        if entity_type == "disease":
            # The shared dictionary is pure ICD-10; TCM diseases stay local
            # to TCM_GUIDELINE_V1 so they cannot leak into western templates.
            entries = western_disease_entries()
        (BASE / f"{entity_type}.json").write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    _sync_tcm_template_diseases(tcm_diseases)
    (BASE / "_meta.json").write_text(
        json.dumps(META, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    concepts_path = Path("data/vocab/concepts.json")
    concepts = json.loads(concepts_path.read_text(encoding="utf-8"))
    existing = {item["code"] for item in concepts}
    for item in [
        {
            "code": "TCM_PATTERN_FORMED",
            "name_cn": "证候成立",
            "name_en": "pattern_formed",
            "category": "tcm_pattern_concept",
            "description": "证候满足主症/次症/舌脉条件后成立",
            "evaluator_type": "observation_present",
            "domain": "tcm",
            "is_active": True,
        },
        {
            "code": "TCM_PATTERN_NOT_FORMED",
            "name_cn": "证候未成立",
            "name_en": "pattern_not_formed",
            "category": "tcm_pattern_concept",
            "description": "证候条件未满足",
            "evaluator_type": "observation_present",
            "domain": "tcm",
            "is_active": True,
        },
    ]:
        if item["code"] not in existing:
            concepts.append(item)
    concepts_path.write_text(
        json.dumps(concepts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("entity dictionaries:", sorted(path.stem for path in BASE.glob("*.json")))
    print("concepts:", len(concepts))


if __name__ == "__main__":
    main()
