import json
from pathlib import Path

from src.extraction.models import (
    ExtractionTemplate,
    TemplateEntityEntry,
    TemplateEntityType,
)
from src.extraction.repository import ExtractionTemplateRepository
from src.extraction.service import extract_local
from src.api.routes.pdf import (
    _doc_store,
    _load_doc,
    _now,
    _persist_doc,
    WARN_NO_TEXT_LAYER,
)

repo = ExtractionTemplateRepository()
template_id = "LYMPHOMA_GUIDELINE_V1"

diseases = [
    ("C81", "霍奇金淋巴瘤", ["霍奇金淋巴瘤", "霍奇金"]),
    ("C83.3", "弥漫大B细胞淋巴瘤", ["弥漫大B细胞淋巴瘤", "弥漫大B", "DLBCL"]),
    ("C82", "滤泡性淋巴瘤", ["滤泡性淋巴瘤"]),
    ("C83.1", "套细胞淋巴瘤", ["套细胞淋巴瘤"]),
    ("C88.4", "结外边缘区B细胞淋巴瘤［MALT淋巴瘤］", ["边缘区淋巴瘤", "MALT"]),
    ("C83.7", "Burkitt淋巴瘤", ["Burkitt淋巴瘤", "伯基特淋巴瘤", "Burkitt", "伯基特"]),
    ("C91.1", "慢性淋巴细胞白血病/小淋巴细胞淋巴瘤", ["慢性淋巴细胞白血病/小淋巴细胞淋巴瘤", "慢性淋巴细胞白血病", "小淋巴细胞淋巴瘤", "CLL/SLL"]),
]

labs = [
    ("CBC", "血常规", ["血常规"]),
    ("LDH", "乳酸脱氢酶", ["乳酸脱氢酶", "LDH"]),
    ("BETA2_MICROGLOBULIN", "β2微球蛋白", ["β2微球蛋白", "beta2微球蛋白", "β2-MG"]),
    ("ALBUMIN", "白蛋白", ["白蛋白"]),
    ("ESR", "血沉", ["血沉", "红细胞沉降率"]),
    ("HBV_SEROLOGY", "乙型肝炎病毒血清学", ["乙型肝炎病毒血清学", "乙肝五项", "乙肝", "HBV"]),
    ("HIV_SEROLOGY", "人类免疫缺陷病毒检测", ["人类免疫缺陷病毒检测", "HIV"]),
    ("EBV_DNA", "EB病毒DNA", ["EB病毒DNA", "EBV-DNA", "EBV DNA", "EBV"]),
]

findings = [
    ("PATHOLOGY_CONFIRMED", "病理确诊淋巴瘤", ["病理确诊", "病理.*淋巴瘤", "淋巴瘤.*病理"]),
    ("BONE_MARROW_INVOLVEMENT", "骨髓受累", ["骨髓受累", "骨髓侵犯"]),
    ("EXTRANODAL_INVOLVEMENT", "结外受累", ["结外受累", "结外侵犯"]),
    ("LYMPH_NODE_INVOLVEMENT", "淋巴结受累", ["淋巴结受累", "淋巴结侵犯"]),
    ("METABOLIC_AVIDITY", "病灶代谢活性增高", ["代谢活性增高", "摄取增高", "代谢增高"]),
]

exams = [
    ("PET_CT", "PET-CT", ["PET-CT", "PET/CT", "PET CT"]),
    ("CT_SCAN", "CT检查", ["CT检查", "CT"]),
    ("MRI_SCAN", "MRI检查", ["MRI检查", "MRI", "磁共振"]),
    ("ULTRASOUND", "超声检查", ["超声检查", "超声"]),
    ("BIOPSY", "活检", ["活检"]),
    ("BONE_MARROW_ASPIRATION", "骨髓穿刺", ["骨髓穿刺", "骨穿"]),
    ("BONE_MARROW_BIOPSY", "骨髓活检", ["骨髓活检"]),
    ("IMMUNOHISTOCHEMISTRY", "免疫组化", ["免疫组化", "免疫组织化学"]),
    ("FLOW_CYTOMETRY", "流式细胞术", ["流式细胞术", "流式细胞学"]),
    ("FISH_TEST", "FISH检测", ["FISH检测", "FISH"]),
    ("PCR_TEST", "PCR检测", ["PCR检测", "PCR"]),
]

symptoms = [
    ("PAINLESS_LYMPHADENOPATHY", "无痛性淋巴结肿大", ["无痛性淋巴结肿大", "无痛性.*淋巴结肿大"]),
    ("LYMPHADENOPATHY", "淋巴结肿大", ["淋巴结肿大"]),
    ("FEVER", "发热", ["发热"]),
    ("NIGHT_SWEATS", "盗汗", ["盗汗", "夜间盗汗"]),
    ("WEIGHT_LOSS", "体重下降", ["体重下降", "体重减轻"]),
    ("PRURITUS", "瘙痒", ["瘙痒"]),
    ("SPLENOMEGALY", "脾大", ["脾大", "脾脏肿大"]),
]

concepts = [
    ("ELEVATED", "升高", ["升高", "增高", "上升"]),
    ("DECREASED", "降低", ["降低", "减低", "下降"]),
    ("POSITIVE", "阳性", ["阳性"]),
    ("NEGATIVE", "阴性", ["阴性"]),
    ("PRESENT", "存在", ["存在", "有"]),
    ("ABSENT", "缺失", ["缺失", "未见", "阴性"]),
    ("ABOVE_ULN_1", "超过正常上限", ["超过正常上限", "超ULN", "超过ULN"]),
    ("ABNORMAL", "异常", ["异常"]),
]

def entity_type(code, label, entries):
    return TemplateEntityType(
        entity_type=code,
        label=label,
        entries=[TemplateEntityEntry(code=item[0], name=item[1], aliases=item[2]) for item in entries],
    )

# Reuse the same graph-compatible relation contract; entity codes are supplied
# by this lymphoma-specific entity vocabulary.
cardio = repo.get("CARDIO_DIAGNOSIS_V1")
relations = cardio.relations

template = ExtractionTemplate(
    template_id=template_id,
    name="淋巴瘤诊疗指南模板",
    description="CSCO淋巴瘤诊疗指南的血液肿瘤专用解析模板，候选结果仍需人工审核。",
    version=1,
    is_default=False,
    status="active",
    domain="血液肿瘤",
    graph_filename="lymphoma_graph.json",
    entity_types=[
        entity_type("disease", "疾病", diseases),
        entity_type("lab", "检验", labs),
        entity_type("exam_finding", "检查发现", findings),
        entity_type("exam", "检查", exams),
        entity_type("symptom", "症状", symptoms),
        entity_type("concept", "状态概念", concepts),
    ],
    relations=relations,
    concept_codes=["ELEVATED", "DECREASED", "POSITIVE", "NEGATIVE", "PRESENT", "ABSENT", "ABOVE_ULN_1", "ABNORMAL"],
    relation_hints=[
        "病理/影像检查发现应关联到对应淋巴瘤诊断",
        "LDH、β2微球蛋白等异常检验需要绑定升高/降低等状态概念",
        "发热、盗汗、体重下降可作为B症状证据",
    ],
    rule_hints=[
        "仅生成候选诊断规则，导入后需医学专员审核",
    ],
    prompt_instructions=[
        "优先识别指南正文中的淋巴瘤亚型、分期检查、预后指标和B症状",
        "不要把示例、参考文献或其他疾病内容抽取为诊断证据",
    ],
)

repo.save(template)

doc_id = "DOC-ead50a9c"
doc = _load_doc(doc_id)
if not doc:
    raise SystemExit(f"Document not found: {doc_id}")

doc_title = doc.get("doc_title") or doc_id
chunks = doc.get("parsed", {}).get("chunks", [])
provider_result = extract_local(chunks, template, doc_id, doc_title)

doc["entities"] = provider_result.entities
doc["suggested_relations"] = provider_result.suggested_relations
doc["suggested_rules"] = provider_result.suggested_rules
doc["extraction"] = {
    "mode": "local",
    "template_id": template.template_id,
    "template_name": template.name,
    "graph_filename": template.graph_filename,
    "warnings": [WARN_NO_TEXT_LAYER],
    "metadata": provider_result.metadata,
}
doc["uploaded_at"] = _now()
_doc_store[doc_id] = doc
_persist_doc(doc)

saved = next((item for item in repo.list() if item.template_id == template_id), None)
summary = {
    "template_saved": bool(saved),
    "template_id": template_id,
    "entity_types": {x.entity_type: len(x.entries) for x in saved.entity_types} if saved else {},
    "doc_id": doc_id,
    "total_lines": doc["parsed"].get("total_lines"),
    "entities": len(provider_result.entities),
    "relations": len(provider_result.suggested_relations),
    "rules": len(provider_result.suggested_rules),
    "rule_names": [item.get("name") for item in provider_result.suggested_rules],
}
Path("tools\\lymphoma_reextract_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
