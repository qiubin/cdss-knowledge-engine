"""Relation preflight checks: endpoint direction, provenance and duplication.

预检只读、可降级：模板加载失败返回空映射，仅导致少标方向可疑，不阻断任何审核动作。
端点类型的权威来源是 data/templates/extraction_templates.json 中各模板的
relations[].source_entity_type / target_entity_type。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

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
    except Exception:
        # 合法 JSON 但结构不对（顶层为 dict、含 null 元素等）同样降级为空映射
        logger.warning("加载抽取模板失败，关系方向预检降级: %s", target)
        return {}


def duplicate_relation_pairs(relations: Iterable[Dict[str, Any]]) -> Set[Tuple[str, str]]:
    """同 (source, target) 被多种 relation_code 连接的对——疑似重复连线。"""
    pair_codes: Dict[Tuple[str, str], Set[str]] = {}
    for relation in relations:
        pair = (str(relation.get("source_node_id") or ""), str(relation.get("target_node_id") or ""))
        pair_codes.setdefault(pair, set()).add(str(relation.get("relation_code") or ""))
    return {pair for pair, codes in pair_codes.items() if len(codes) > 1}


def relation_flags(
    relation: Dict[str, Any],
    source_node: Optional[Dict[str, Any]],
    target_node: Optional[Dict[str, Any]],
    endpoint_type_map: Dict[str, Dict[str, List[str]]],
    duplicate_pairs: Set[Tuple[str, str]],
) -> List[Dict[str, str]]:
    flags: List[Dict[str, str]] = []

    def _status(item: Optional[Dict[str, Any]]) -> str:
        return str((item or {}).get("review_status") or "draft")

    if _status(source_node) != "active" or _status(target_node) != "active":
        flags.append({"code": "endpoint_inactive", "message": "端点节点未全部「已通过」"})
    if not (relation.get("source_refs") or []):
        flags.append({"code": "no_source_refs", "message": "无指南溯源引用"})
    source_type = str((source_node or {}).get("node_type") or "")
    target_type = str((target_node or {}).get("node_type") or "")
    code = str(relation.get("relation_code") or "")
    expected = endpoint_type_map.get(code)
    if expected and expected.get("source") and expected.get("target") and source_type and target_type:
        if source_type not in expected["source"] or target_type not in expected["target"]:
            flags.append({
                "code": "direction_suspect",
                "message": (
                    f"预期 {type_label(expected['source'][0])}→{type_label(expected['target'][0])}，"
                    f"实际 {type_label(source_type)}→{type_label(target_type)}"
                ),
            })
    source_id = str(relation.get("source_node_id") or "")
    target_id = str(relation.get("target_node_id") or "")
    if source_id and source_id == target_id:
        flags.append({"code": "self_loop", "message": "源与目标是同一节点"})
    if (source_id, target_id) in duplicate_pairs:
        flags.append({"code": "duplicate_pair", "message": "同一对节点被多种关系类型重复连接"})
    return flags
