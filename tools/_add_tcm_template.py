"""Add the TCM guideline extraction template (idempotent)."""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path("data/templates/extraction_templates.json")

TEMPLATE = {
    "template_id": "TCM_GUIDELINE_V1",
    "name": "中医指南抽取模板",
    "description": "中医指南/诊疗方案：病-证-症-舌脉-病机-治法-方药抽取与候选规则生成",
    "version": 1,
    "is_default": False,
    "status": "active",
    "domain": "tcm",
    "specialty": "中医内科",
    "graph_filename": "tcm_graph.json",
    "standard_system": "ICD-11 TM1 / 中医药学名词（本地稳定码 + 标准映射）",
    "entity_types": [
        {
            "entity_type": "disease",
            "label": "中医疾病",
            "discovery": True,
            "discovery_suffixes": ["病"],
            "vocabulary": True,
            "entries": [],
        },
        {
            "entity_type": "syndrome",
            "label": "证候",
            "discovery": True,
            "discovery_suffixes": ["证候", "证", "型"],
            "vocabulary": True,
            "entries": [],
        },
        {"entity_type": "symptom", "label": "症状", "vocabulary": True, "entries": []},
        {"entity_type": "tongue", "label": "舌象", "vocabulary": True, "entries": []},
        {"entity_type": "pulse", "label": "脉象", "vocabulary": True, "entries": []},
        {"entity_type": "sign", "label": "体征", "vocabulary": True, "entries": []},
        {"entity_type": "pathogenesis", "label": "病机", "vocabulary": True, "entries": []},
        {
            "entity_type": "treatment_method",
            "label": "治法",
            "discovery": True,
            "vocabulary": True,
            "entries": [],
        },
        {
            "entity_type": "formula",
            "label": "方剂",
            "discovery": True,
            "vocabulary": True,
            "entries": [],
        },
        {"entity_type": "herb", "label": "中药", "vocabulary": True, "entries": []},
        {
            "entity_type": "contraindication",
            "label": "禁忌",
            "discovery": True,
            "vocabulary": True,
            "entries": [],
        },
    ],
    "relations": [
        {
            "relation_code": "DISEASE_HAS_SYNDROME",
            "name": "疾病辨证分型",
            "source_entity_type": "disease",
            "target_entity_type": "syndrome",
            "generate_evidence": False,
        },
        {
            "relation_code": "SYNDROME_HAS_SYMPTOM",
            "name": "证候主症次症",
            "source_entity_type": "syndrome",
            "target_entity_type": "symptom",
            "generate_evidence": False,
        },
        {
            "relation_code": "SYNDROME_HAS_TONGUE",
            "name": "证候舌象",
            "source_entity_type": "syndrome",
            "target_entity_type": "tongue",
            "generate_evidence": False,
        },
        {
            "relation_code": "SYNDROME_HAS_PULSE",
            "name": "证候脉象",
            "source_entity_type": "syndrome",
            "target_entity_type": "pulse",
            "generate_evidence": False,
        },
        {
            "relation_code": "SYNDROME_HAS_SIGN",
            "name": "证候体征",
            "source_entity_type": "syndrome",
            "target_entity_type": "sign",
            "generate_evidence": False,
        },
        {
            "relation_code": "SYNDROME_HAS_PATHOGENESIS",
            "name": "证候病机",
            "source_entity_type": "syndrome",
            "target_entity_type": "pathogenesis",
            "generate_evidence": False,
        },
        {
            "relation_code": "SYNDROME_TREATED_BY_METHOD",
            "name": "证候治法",
            "source_entity_type": "syndrome",
            "target_entity_type": "treatment_method",
            "generate_evidence": False,
        },
        {
            "relation_code": "METHOD_HAS_FORMULA",
            "name": "治法代表方",
            "source_entity_type": "treatment_method",
            "target_entity_type": "formula",
            "generate_evidence": False,
        },
        {
            "relation_code": "FORMULA_CONTAINS_HERB",
            "name": "方剂组成",
            "source_entity_type": "formula",
            "target_entity_type": "herb",
            "generate_evidence": False,
        },
        {
            "relation_code": "SYNDROME_HAS_CONTRAINDICATION",
            "name": "证候禁忌",
            "source_entity_type": "syndrome",
            "target_entity_type": "contraindication",
            "generate_evidence": False,
        },
    ],
    "attributes": [
        {
            "attribute_code": "SYMPTOM_ROLE",
            "name": "症状角色",
            "owner_entity_type": "symptom",
            "data_type": "enum",
            "required": False,
            "allowed_values": ["主症", "次症"],
            "description": "主症用于证候成立的必要条件，次症用于加权",
        },
        {
            "attribute_code": "HERB_ROLE",
            "name": "君臣佐使",
            "owner_entity_type": "herb",
            "data_type": "enum",
            "required": False,
            "allowed_values": ["君", "臣", "佐", "使"],
        },
        {
            "attribute_code": "HERB_DOSE",
            "name": "剂量",
            "owner_entity_type": "herb",
            "data_type": "string",
            "required": False,
        },
        {
            "attribute_code": "SYNDROME_LOCATION",
            "name": "病位",
            "owner_entity_type": "syndrome",
            "data_type": "string",
            "required": False,
        },
        {
            "attribute_code": "SYNDROME_NATURE",
            "name": "病性",
            "owner_entity_type": "syndrome",
            "data_type": "string",
            "required": False,
        },
        {
            "attribute_code": "FORMULA_SOURCE",
            "name": "方剂出处",
            "owner_entity_type": "formula",
            "data_type": "string",
            "required": False,
        },
        {
            "attribute_code": "CONTRAINDICATION_LEVEL",
            "name": "禁忌级别",
            "owner_entity_type": "contraindication",
            "data_type": "enum",
            "required": False,
            "allowed_values": ["禁用", "慎用"],
        },
    ],
    "concept_codes": ["PRESENT", "ABSENT", "TCM_PATTERN_FORMED", "TCM_PATTERN_NOT_FORMED"],
    "relation_hints": [
        "同一证候章节中的症状、舌象、脉象、治法、方剂应建立关系",
        "方剂与中药组成关系需保留君臣佐使与剂量",
        "异病同证、同病异证均通过证候节点连接，不复制证候",
    ],
    "rule_hints": [
        "每个证候至少生成一条辨证规则，条件来自同一章节",
        "治法规则依赖证候事实，方药规则依赖治法与方剂关系",
        "所有候选规则保持 draft，需中医师审核后启用",
    ],
    "prompt_instructions": [
        "你是中医临床指南知识抽取引擎。",
        "严格区分中医疾病（以“病”结尾）与证候（以“证/证候/型”结尾），证候是辨证规则的锚点。",
        "症状需区分主症与次症；舌象、脉象作为独立实体，不要并入症状。",
        "治法、方剂、中药分别抽取；方剂组成需记录君臣佐使和剂量。",
        "不要将西医检验指标、影像检查混入中医证候条件。",
        "不添加模板外编码，不遗漏页码、行号和原文引文。",
    ],
    "output_contract": {
        "entities": "模板允许的中医实体",
        "suggested_relations": "证候-症状/舌/脉/治法/方剂、方剂-中药关系",
        "suggested_rules": "tcm_pattern / tcm_treatment / tcm_prescription 候选规则",
    },
    "capabilities": [
        "tcm_pattern",
        "tcm_treatment",
        "tcm_prescription",
        "tcm_contraindication",
    ],
    "rule_generation": [
        {
            "rule_type": "tcm_pattern",
            "anchor": "syndrome",
            "condition_types": ["symptom", "tongue", "pulse", "sign"],
            "min_conditions": 2,
            "score_threshold": 0.4,
        },
        {
            "rule_type": "tcm_treatment",
            "anchor": "syndrome",
            "via": "SYNDROME_TREATED_BY_METHOD",
        },
        {
            "rule_type": "tcm_prescription",
            "anchor": "syndrome",
            "via": ["METHOD_HAS_FORMULA", "FORMULA_CONTAINS_HERB"],
        },
        {
            "rule_type": "tcm_contraindication",
            "anchor": "syndrome",
            "via": "SYNDROME_HAS_CONTRAINDICATION",
        },
    ],
}


def main() -> None:
    templates = json.loads(PATH.read_text(encoding="utf-8"))
    templates = [
        item for item in templates
        if item.get("template_id") != TEMPLATE["template_id"]
    ]
    templates.append(TEMPLATE)
    PATH.write_text(
        json.dumps(templates, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("templates:", [item["template_id"] for item in templates])


if __name__ == "__main__":
    main()
