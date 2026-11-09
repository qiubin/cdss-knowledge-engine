"""Traditional Chinese Medicine domain profile.

TCM anchors on the syndrome/pattern (证候) rather than the western disease.
A guideline section is expected to produce:

    病 → 证候 → 主症/次症/舌象/脉象 → 病机 → 治法 → 方剂 → 中药

Pattern matching is scored (main/secondary symptom weights) rather than a
single boolean, and treatment/prescription rules consume the syndrome facts
produced by the preceding stage.
"""
from __future__ import annotations

from ..base import DomainProfile, GraphProfile, InferenceStage, TCM

NODE_META = {
    "disease": {"label": "中医疾病", "color": "#c05621", "stroke": "#9c4221", "shape": "diamond", "size": 28},
    "syndrome": {"label": "证候", "color": "#d69e2e", "stroke": "#b7791f", "shape": "diamond", "size": 30},
    "symptom": {"label": "症状", "color": "#f687b3", "stroke": "#d53f8c", "shape": "dot", "size": 20},
    "tongue": {"label": "舌象", "color": "#ed8936", "stroke": "#c05621", "shape": "dot", "size": 20},
    "pulse": {"label": "脉象", "color": "#9f7aea", "stroke": "#6b46c1", "shape": "dot", "size": 20},
    "sign": {"label": "体征", "color": "#b794f4", "stroke": "#805ad5", "shape": "dot", "size": 18},
    "pathogenesis": {"label": "病机", "color": "#a0aec0", "stroke": "#718096", "shape": "box", "size": 20},
    "treatment_method": {"label": "治法", "color": "#38a169", "stroke": "#276749", "shape": "box", "size": 24},
    "formula": {"label": "方剂", "color": "#2f855a", "stroke": "#22543d", "shape": "box", "size": 24},
    "herb": {"label": "中药", "color": "#68d391", "stroke": "#38a169", "shape": "dot", "size": 18},
    "contraindication": {"label": "禁忌", "color": "#fc8181", "stroke": "#e53e3e", "shape": "dot", "size": 18},
    "meridian": {"label": "经络", "color": "#4299e1", "stroke": "#2b6cb0", "shape": "box", "size": 18},
    "acupoint": {"label": "腧穴", "color": "#63b3ed", "stroke": "#3182ce", "shape": "dot", "size": 16},
}

PATH_PANELS = (
    {
        "title": "辨证链路",
        "description": "中医疾病 → 证候 → 主症/次症/舌象/脉象",
        "spec": "disease->syndrome->symptom/tongue/pulse",
    },
    {
        "title": "治法方药链路",
        "description": "证候 → 治法 → 方剂 → 中药",
        "spec": "syndrome->treatment_method->formula->herb",
    },
)

INFERENCE_FORM = (
    {"key": "symptoms", "label": "症状（主症/次症）", "type": "tcm_symptoms"},
    {"key": "tongue", "label": "舌象", "type": "tcm_tongue"},
    {"key": "pulse", "label": "脉象", "type": "tcm_pulse"},
    {"key": "constitution", "label": "体质", "type": "tcm_constitution"},
    {"key": "past_history", "label": "既往史", "type": "codes"},
    {"key": "current_medications", "label": "当前用药", "type": "codes"},
)

TCM_PROFILE = DomainProfile(
    domain=TCM,
    label="中医",
    description="辨证、治法与方药推理",
    default_template_id="TCM_GUIDELINE_V1",
    graph=GraphProfile(
        domain=TCM,
        label="中医知识图谱",
        anchor_types=("syndrome", "disease"),
        allowed_entity_types=(
            "disease", "syndrome", "symptom", "tongue", "pulse", "sign",
            "pathogenesis", "treatment_method", "formula", "herb",
            "contraindication", "meridian", "acupoint",
        ),
        # TCM pattern conditions reference observation nodes directly; the
        # western diagnostic-evidence projection is intentionally unused.
        evidence_entity_types=(),
        legacy_graph_filename="tcm_graph.json",
    ),
    stages=(
        InferenceStage(
            name="tcm_pattern",
            rule_types=("tcm_pattern",),
            description="症状/舌象/脉象 → 证候（评分）",
            produces_facts=("syndrome",),
        ),
        InferenceStage(
            name="tcm_treatment",
            rule_types=("tcm_treatment",),
            description="证候 → 治法",
            draft_allowed=True,
            produces_facts=("treatment",),
        ),
        InferenceStage(
            name="tcm_prescription",
            rule_types=("tcm_prescription",),
            description="证候/治法 → 方剂与中药",
            draft_allowed=True,
        ),
        InferenceStage(
            name="tcm_contraindication",
            rule_types=("tcm_contraindication",),
            description="证候/方药 → 禁忌与慎用",
            draft_allowed=True,
        ),
    ),
    node_meta=NODE_META,
    path_panels=PATH_PANELS,
    inference_form=INFERENCE_FORM,
    package_checks=(
        "tcm_syndrome_completeness",
        "tcm_formula_composition",
        "tcm_contraindication_conflicts",
    ),
)
