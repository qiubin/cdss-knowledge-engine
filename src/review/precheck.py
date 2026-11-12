"""Relation preflight checks: endpoint direction, provenance and duplication.

预检只读、可降级：模板加载失败返回空映射，仅导致少标方向可疑，不阻断任何审核动作。
端点类型的权威来源是 data/templates/extraction_templates.json 中各模板的
relations[].source_entity_type / target_entity_type。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.api.routes.kg import TEMPLATE_PATH

logger = logging.getLogger(__name__)

# 预检提示文案里的类型中文名（前端 typeName 的服务端对应版本）。
NODE_TYPE_LABELS = {
    "disease": "疾病", "symptom": "症状", "sign": "体征", "lab": "检验", "exam": "检查",
    "diagnostic_evidence": "诊断依据", "treatment_plan": "治疗方案", "medication": "药品",
    "intervention": "干预操作", "contraindication": "禁忌", "monitoring_requirement": "监测要求",
    "syndrome": "证候", "tongue": "舌象", "pulse": "脉象", "formula": "方剂", "herb": "中药",
    "treatment_method": "治法", "care_pathway": "临床路径", "pathway_phase": "路径阶段",
    "order_set": "医嘱组", "order_item": "医嘱项", "care_guidance": "调护",
    "risk_factor": "危险因素", "stage": "分期", "pathology": "病理", "histology": "组织学",
    "biomarker": "生物标志物", "molecular_test": "分子检测", "syndrome_element": "证素",
    "pathogenesis": "病机", "acupoint": "穴位", "meridian": "经络", "western_disease": "西医疾病",
}


def type_label(node_type: str) -> str:
    return NODE_TYPE_LABELS.get(str(node_type or ""), str(node_type or "未知"))


def load_endpoint_type_map(path: Optional[Path] = None) -> Dict[str, Dict[str, List[str]]]:
    """合并全部模板的关系端点类型定义；失败降级为空映射。"""
    target = Path(path) if path is not None else TEMPLATE_PATH
    try:
        templates = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("加载抽取模板失败，关系方向预检降级: %s", target)
        return {}
    merged: Dict[str, Dict[str, List[str]]] = {}
    for template in templates or []:
        for relation in template.get("relations") or []:
            code = str(relation.get("relation_code") or "")
            if not code:
                continue
            entry = merged.setdefault(code, {"source": [], "target": []})
            for side, field in (("source", "source_entity_type"), ("target", "target_entity_type")):
                value = str(relation.get(field) or "")
                if value and value not in entry[side]:
                    entry[side].append(value)
    return merged
