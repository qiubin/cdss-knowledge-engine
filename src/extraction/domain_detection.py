"""Transparent, deterministic domain detection for guideline uploads."""
from __future__ import annotations

import re
from typing import Any, Dict, List


# 标题类标识只在标题和正文开头处找——正文中间引用“《XX中医诊疗方案》”不应主导判域。
_TITLE_TCM = re.compile(r"中医(?:药)?(?:诊疗指南|临床诊疗指南|诊疗方案|诊断疗效标准|专家共识)")
_STRONG_TCM = (
    (r"中医辨证|辨证分型|辨证论治|辨证施治|分证论治|辨证要点|证候分类|证型分类", 30, "存在中医辨证／证候分类结构"),
    (r"舌(?:质|苔|象).{0,80}脉(?:象|弦|细|沉|数|弱|涩)", 15, "同时出现舌象和脉象"),
    (r"(?:治法|治则).{0,100}(?:方剂|方药|中成药|汤剂)", 15, "同时出现治法和方药"),
    (r"君臣佐使", 15, "出现方剂君臣佐使结构"),
    (r"主症.{0,60}次症", 15, "症状分主症／次症结构"),
    (r"随证加减|加减化裁", 10, "存在随证加减"),
)
_TCM_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,16}证(?:候)?")
# 以这些词结尾的“X证”不是辨证证候：适应证/禁忌证类术语和“证明、验证”等常用词。
_NON_SYNDROME = ("适应证", "禁忌证", "并发证", "后遗症", "证明", "验证", "保证", "论证", "认证", "查证", "考证", "佐证", "印证")
SYNDROME_CONFIRM_THRESHOLD = 3  # 3-9 个证候名：中等置信，需人工确认领域
SYNDROME_STRONG_THRESHOLD = 10  # ≥10 个不同证候名：证候体系完整，直接判中医


def detect_guideline_domain(
    chunks: List[Dict[str, Any]], doc_title: str = "", requested_domain: str = "auto"
) -> Dict[str, Any]:
    """Classify a guideline with an auditable score; explicit choice wins."""
    requested = str(requested_domain or "auto").strip().lower()
    if requested in {"western", "tcm"}:
        return {
            "domain": requested, "confidence": "explicit", "score": None,
            "reasons": [f"用户明确选择{'中医' if requested == 'tcm' else '西医'}领域"],
            "requires_confirmation": False,
        }
    from .tail_sections import is_reference_section
    body = "\n".join(
        str(chunk.get("text") or "")
        for chunk in chunks
        if not is_reference_section(chunk.get("section"))
    )
    # 采样正文前 20 万字符：长指南的辨证章节可能落在末尾，5 万字符会漏采。
    head = f"{doc_title}\n{body[:5000]}"
    sample = f"{doc_title}\n{body[:200000]}"
    score = 0
    reasons = []
    if _TITLE_TCM.search(head):
        score += 50
        reasons.append("标题或开头包含“中医诊疗指南／诊疗方案”类标识")
    for pattern, points, reason in _STRONG_TCM:
        if re.search(pattern, sample, re.S):
            score += points
            reasons.append(reason)
    patterns = {
        match.group(0) for match in _TCM_PATTERN.finditer(sample)
        if not any(bad in match.group(0) for bad in _NON_SYNDROME)
    }
    if len(patterns) >= SYNDROME_STRONG_THRESHOLD:
        score += 50
        reasons.append(f"发现大量证候名称（{len(patterns)}个，证候体系完整）")
    elif len(patterns) >= SYNDROME_CONFIRM_THRESHOLD:
        score += 20
        reasons.append(f"发现多个证候名称（{len(patterns)}个）")
    if score >= 50:
        return {"domain": "tcm", "confidence": "high", "score": score,
                "reasons": reasons, "requires_confirmation": False}
    if score >= 20:
        return {"domain": "", "confidence": "medium", "score": score,
                "reasons": reasons, "requires_confirmation": True}
    return {"domain": "western", "confidence": "high", "score": score,
            "reasons": reasons or ["未发现成体系的中医辨证、治法和方药结构"],
            "requires_confirmation": False}
