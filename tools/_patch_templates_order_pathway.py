"""One-off migration: add order-set / care-pathway extraction contract to the
western extraction templates.

Adds four entity types (order_set, order_item, care_pathway, pathway_phase),
five relations and the phase/order attributes so LLM extraction can pull
医嘱套餐 and 诊疗路径 straight out of guideline text.  Creates a .bak backup of
extraction_templates.json before writing.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "data" / "templates" / "extraction_templates.json"

TARGET_TEMPLATES = {
    "GENERIC_GUIDELINE_V1",
    "CARDIO_DIAGNOSIS_V1",
    "LYMPHOMA_GUIDELINE_V1",
    "UNIVERSAL_GUIDELINE_V1",
}

ENTITY_TYPES = [
    {
        "entity_type": "order_set",
        "label": "医嘱套餐",
        "discovery": True,
        "domain": "western",
        "color": "#fbd38d",
        "icon": "clipboard-list",
        "extraction_hint": "指南中成组的医嘱推荐（如再灌注方案用药组合、监护与复查组合）",
    },
    {
        "entity_type": "order_item",
        "label": "医嘱条目",
        "discovery": True,
        "domain": "western",
        "color": "#fefcbf",
        "icon": "pill",
        "extraction_hint": "套餐内单条可开立医嘱：药品/检验/检查/治疗/护理/宣教/随访",
    },
    {
        "entity_type": "care_pathway",
        "label": "诊疗路径",
        "discovery": True,
        "domain": "western",
        "color": "#e9d8fd",
        "icon": "route",
        "extraction_hint": "覆盖疾病全诊疗周期的路径锚点（入院→治疗→出院→随访）",
    },
    {
        "entity_type": "pathway_phase",
        "label": "路径阶段",
        "discovery": True,
        "domain": "western",
        "color": "#d6bcfa",
        "icon": "git-branch",
        "extraction_hint": "按病程划分的阶段：急诊期/急性期/恢复期/维持期/随访期等",
    },
]

RELATIONS = [
    {
        "relation_code": "TREATMENT_PLAN_MATERIALIZED_AS",
        "name": "治疗方案物化为医嘱套餐",
        "source_entity_type": "treatment_plan",
        "target_entity_type": "order_set",
        "cardinality": "1:N",
        "extraction_hint": "治疗方案的医嘱落地形式（同章节或同疾病语境）",
    },
    {
        "relation_code": "ORDER_SET_INCLUDES_ITEM",
        "name": "医嘱套餐包含医嘱条目",
        "source_entity_type": "order_set",
        "target_entity_type": "order_item",
        "cardinality": "1:N",
        "extraction_hint": "套餐罗列的每一条医嘱",
    },
    {
        "relation_code": "DISEASE_HAS_CARE_PATHWAY",
        "name": "疾病有诊疗路径",
        "source_entity_type": "disease",
        "target_entity_type": "care_pathway",
        "cardinality": "1:N",
        "extraction_hint": "指南描述了该疾病的规范诊治流程时建立",
    },
    {
        "relation_code": "CARE_PATHWAY_HAS_PHASE",
        "name": "诊疗路径包含阶段",
        "source_entity_type": "care_pathway",
        "target_entity_type": "pathway_phase",
        "cardinality": "1:N",
        "extraction_hint": "按病程顺序罗列各阶段",
    },
    {
        "relation_code": "PHASE_USES_ORDER_SET",
        "name": "路径阶段使用医嘱套餐",
        "source_entity_type": "pathway_phase",
        "target_entity_type": "order_set",
        "cardinality": "N:M",
        "extraction_hint": "某阶段默认开立的医嘱套餐",
    },
]

ATTRIBUTES = [
    {"attribute_code": "ORDER_CLASS", "name": "医嘱类别", "owner_entity_type": "order_item",
     "data_type": "string", "required": True,
     "allowed_values": ["药品", "检验", "检查", "治疗", "手术", "护理", "宣教", "随访"],
     "description": "医嘱条目的开立类别"},
    {"attribute_code": "ORDER_REQUIRED", "name": "必选性", "owner_entity_type": "order_item",
     "data_type": "string", "required": False,
     "allowed_values": ["required", "optional", "conditional"],
     "description": "套餐内该条目为必选/可选/按条件勾选"},
    {"attribute_code": "ORDER_DOSE_RULE", "name": "剂量频次规则", "owner_entity_type": "order_item",
     "data_type": "string", "required": False,
     "description": "指南原文的剂量/频次/途径描述，保留数值供院内参数化"},
    {"attribute_code": "ORDER_SET_PRIORITY", "name": "套餐优先级", "owner_entity_type": "order_set",
     "data_type": "string", "required": False,
     "allowed_values": ["preferred", "alternative", "rescue"],
     "description": "首选/替代/挽救，与治疗规则优先级语义一致"},
    {"attribute_code": "PHASE_SEQUENCE", "name": "阶段顺序", "owner_entity_type": "pathway_phase",
     "data_type": "int", "required": False,
     "description": "路径内的阶段序号，从 1 开始"},
    {"attribute_code": "PHASE_ENTRY", "name": "进入条件", "owner_entity_type": "pathway_phase",
     "data_type": "string", "required": False,
     "description": "进入该阶段的临床条件（如确诊STEMI、入院完成）"},
    {"attribute_code": "PHASE_EXIT", "name": "转段条件", "owner_entity_type": "pathway_phase",
     "data_type": "string", "required": False,
     "description": "结束/转出该阶段的条件（如再灌注完成、出院评估通过）"},
]

PROMPT_INSTRUCTIONS = [
    "指南中的医嘱套餐抽为 order_set 实体，单条医嘱抽为 order_item 并标注 attributes."
    "ORDER_CLASS（药品/检验/检查/治疗/手术/护理/宣教/随访）与 ORDER_REQUIRED；"
    "剂量频次写入 ORDER_DOSE_RULE，保留指南原文数值。",
    "按病程划分的诊治阶段抽为 pathway_phase（attributes: PHASE_SEQUENCE 顺序、"
    "PHASE_ENTRY 进入条件、PHASE_EXIT 转段条件），全周期流程抽为 care_pathway；"
    "阶段与该阶段默认医嘱套餐用 PHASE_USES_ORDER_SET 连接。",
]


def main() -> None:
    if not TEMPLATE_PATH.exists():
        raise SystemExit(f"template file not found: {TEMPLATE_PATH}")
    backup = TEMPLATE_PATH.with_suffix(
        f".json.bak-{time.strftime('%Y%m%d')}-order-pathway"
    )
    shutil.copy2(TEMPLATE_PATH, backup)

    data = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    existing_types = {t["entity_type"] for t in (
        data[0].get("entity_types", []) if data else []
    )}
    patched = []
    for template in data:
        if template.get("template_id") not in TARGET_TEMPLATES:
            patched.append(template)
            continue
        types = template.setdefault("entity_types", [])
        have = {t.get("entity_type") for t in types}
        for entry in ENTITY_TYPES:
            if entry["entity_type"] not in have:
                types.append(dict(entry))
        relations = template.setdefault("relations", [])
        have_rel = {r.get("relation_code") for r in relations}
        for rel in RELATIONS:
            if rel["relation_code"] not in have_rel:
                relations.append(dict(rel))
        attributes = template.setdefault("attributes", [])
        have_attr = {a.get("attribute_code") for a in attributes}
        for attr in ATTRIBUTES:
            if attr["attribute_code"] not in have_attr:
                attributes.append(dict(attr))
        instructions = template.setdefault("prompt_instructions", [])
        for instruction in PROMPT_INSTRUCTIONS:
            if instruction not in instructions:
                instructions.append(instruction)
        patched.append(template)

    TEMPLATE_PATH.write_text(
        json.dumps(patched, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"patched templates: {sorted(TARGET_TEMPLATES)}")
    print(f"backup written to: {backup.name}")


if __name__ == "__main__":
    main()
