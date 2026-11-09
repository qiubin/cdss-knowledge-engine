"""Western-medicine domain profile (historical default)."""
from __future__ import annotations

from ..base import DomainProfile, GraphProfile, InferenceStage, WESTERN

NODE_META = {
    "disease": {"label": "疾病", "color": "#f6ad55", "stroke": "#dd6b20", "shape": "diamond", "size": 30},
    "lab": {"label": "检验", "color": "#63b3ed", "stroke": "#3182ce", "shape": "dot", "size": 20},
    "exam": {"label": "检查", "color": "#b794f4", "stroke": "#805ad5", "shape": "dot", "size": 20},
    "symptom": {"label": "症状", "color": "#f687b3", "stroke": "#d53f8c", "shape": "dot", "size": 20},
    "diagnostic_evidence": {"label": "诊断证据", "color": "#4fd1c5", "stroke": "#319795", "shape": "dot", "size": 18},
    "treatment_plan": {"label": "治疗方案", "color": "#ecc94b", "stroke": "#d69e2e", "shape": "diamond", "size": 26},
    "intervention": {"label": "治疗措施", "color": "#68d391", "stroke": "#38a169", "shape": "dot", "size": 20},
    "medication": {"label": "药物", "color": "#9ae6b4", "stroke": "#48bb78", "shape": "dot", "size": 18},
    "contraindication": {"label": "禁忌", "color": "#fc8181", "stroke": "#e53e3e", "shape": "dot", "size": 18},
    "monitoring_requirement": {"label": "监测要求", "color": "#b794f4", "stroke": "#805ad5", "shape": "dot", "size": 18},
    "pathology": {"label": "病理", "color": "#b83280", "stroke": "#97266d", "shape": "box", "size": 20},
    "biomarker": {"label": "生物标志物", "color": "#38b2ac", "stroke": "#2c7a7b", "shape": "box", "size": 20},
    "molecular_test": {"label": "分子检测", "color": "#4fd1c5", "stroke": "#319795", "shape": "box", "size": 20},
    "stage": {"label": "分期", "color": "#805ad5", "stroke": "#553c9a", "shape": "box", "size": 18},
    "histology": {"label": "组织学", "color": "#d53f8c", "stroke": "#97266d", "shape": "box", "size": 18},
    "risk_factor": {"label": "危险因素", "color": "#dd6b20", "stroke": "#9c4221", "shape": "dot", "size": 18},
    "sign": {"label": "体征", "color": "#f6ad55", "stroke": "#c05621", "shape": "dot", "size": 18},
}

PATH_PANELS = (
    {
        "title": "诊断逻辑链路",
        "description": "疾病 → 检验/检查/症状 → 诊断证据",
        "spec": "disease->lab/exam/symptom->diagnostic_evidence",
    },
    {
        "title": "治疗方案链路",
        "description": "疾病 → 治疗方案 → 措施/药物/禁忌/监测",
        "spec": "disease->treatment_plan->intervention/medication/contraindication/monitoring_requirement",
    },
)

INFERENCE_FORM = (
    {"key": "labs", "label": "检验", "type": "labs"},
    {"key": "exam_findings", "label": "检查发现", "type": "exam_findings"},
    {"key": "symptoms", "label": "症状", "type": "symptoms"},
    {"key": "vitals", "label": "体征", "type": "vitals"},
    {"key": "past_history", "label": "既往史", "type": "codes"},
    {"key": "current_medications", "label": "当前用药", "type": "codes"},
)

WESTERN_PROFILE = DomainProfile(
    domain=WESTERN,
    label="西医",
    description="疾病诊断与治疗方案推理",
    default_template_id="GENERIC_GUIDELINE_V1",
    graph=GraphProfile(
        domain=WESTERN,
        label="西医知识图谱",
        anchor_types=("disease",),
        allowed_entity_types=(
            "disease", "lab", "exam", "diagnostic_evidence", "symptom", "sign",
            "treatment_plan", "intervention", "medication",
            "contraindication", "monitoring_requirement",
            "pathology", "biomarker", "molecular_test", "stage", "histology",
            "risk_factor",
        ),
        evidence_entity_types=("lab", "exam", "diagnostic_evidence", "symptom"),
        legacy_graph_filename="stemi_graph.json",
    ),
    stages=(
        InferenceStage(
            name="diagnosis",
            rule_types=("diagnosis", "monitoring"),
            description="疾病诊断与监测规则",
            catch_all=True,
        ),
        InferenceStage(
            name="treatment",
            rule_types=("treatment",),
            description="仅在疾病已命中时评估的治疗建议（草稿亦可）",
            draft_allowed=True,
        ),
    ),
    node_meta=NODE_META,
    path_panels=PATH_PANELS,
    inference_form=INFERENCE_FORM,
    package_checks=("hospital_standards",),
)
