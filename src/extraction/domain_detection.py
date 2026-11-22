"""Transparent, deterministic domain detection for guideline uploads."""
from __future__ import annotations

import re
from typing import Any, Dict, List


_STRONG_TCM = (
    (r"中医(?:药)?诊疗指南", 50, "标题或正文包含“中医诊疗指南”"),
    (r"中医辨证|辨证分型|辨证论治", 30, "存在中医辨证章节"),
    (r"舌(?:质|苔|象).{0,80}脉(?:象|弦|细|沉|数|弱|涩)", 15, "同时出现舌象和脉象"),
    (r"(?:治法|治则).{0,100}(?:方剂|方药|中成药)", 15, "同时出现治法和方药"),
    (r"随证加减|加减化裁", 10, "存在随证加减"),
)
_TCM_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,16}证(?:候)?")


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
    body = "\n".join(
        str(chunk.get("text") or "")
        for chunk in chunks
        if str(chunk.get("section") or "").strip() != "参考文献"
    )
    sample = f"{doc_title}\n{body[:50000]}"
    score = 0
    reasons = []
    for pattern, points, reason in _STRONG_TCM:
        if re.search(pattern, sample, re.S):
            score += points
            reasons.append(reason)
    patterns = {
        match.group(0) for match in _TCM_PATTERN.finditer(sample)
        if match.group(0) not in {"适应证", "禁忌证", "并发证"}
    }
    if len(patterns) >= 3:
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
