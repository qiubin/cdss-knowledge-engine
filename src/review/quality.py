"""Deterministic provenance and clinical-statement quality checks."""
import json
import re
from functools import lru_cache
from pathlib import Path


def normalized(text):
    return re.sub(r"\s+", "", str(text or ""))


@lru_cache(maxsize=8)
def _read_document(path, modified_ns, size):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_source(source, uploads_dir):
    """Check the actual cited lines, not merely the presence of citation fields."""
    issues = []
    doc_id = str(source.get("guideline_id") or "")
    if not re.fullmatch(r"DOC-[A-Za-z0-9-]+", doc_id):
        return {"status": "invalid", "issues": ["原文指南标识无效"], "text": ""}
    quote = normalized(source.get("quote"))
    try:
        page = int(source.get("page_start") or 0)
        end_page = int(source.get("page_end") or page)
        start = int(source.get("line_start") or 0)
        end = int(source.get("line_end") or 0)
        if min(page, start, end) <= 0 or end_page < page or (end_page == page and end < start):
            raise ValueError()
    except (ValueError, TypeError):
        return {"status": "invalid", "issues": ["原文页行范围无效或倒置"], "text": ""}
    path = Path(uploads_dir) / (doc_id + ".parsed.json")
    if not path.is_file():
        return {"status": "unavailable", "issues": ["原文解析资料不可用，请恢复指南原文后复核"], "text": ""}
    try:
        stat = path.stat()
        doc = _read_document(str(path), stat.st_mtime_ns, stat.st_size)
        coverage = ((doc.get("extraction") or {}).get("metadata") or {}).get("coverage")
        if coverage and not coverage.get("complete"):
            issues.append("指南解析覆盖不完整，请先补全失败或未处理的片段")
        lines = (doc.get("parsed") or doc).get("lines") or []
        selected = [line for line in lines if page <= int(line["page"]) <= end_page
                    and (int(line["page"]) != page or int(line["line"]) >= start)
                    and (int(line["page"]) != end_page or int(line["line"]) <= end)]
        if not any(int(line["page"]) == page and int(line["line"]) == start for line in selected) or not any(
                int(line["page"]) == end_page and int(line["line"]) == end for line in selected):
            issues.append("原文定位超出实际页行范围")
        text = "\n".join(str(line.get("text") or "") for line in selected)
    except (ValueError, TypeError, KeyError, OSError):
        return {"status": "unavailable", "issues": ["原文解析资料损坏或格式无效"], "text": ""}
    if not quote or quote not in normalized(text):
        issues.append("引文与所定位原文不一致，请核对原句；不能用自动补齐的文字代替出处")
    return {"status": "verified" if not issues else "invalid", "issues": issues, "text": text,
            "semantic_review_required": True}


def statement_for(spec, scope_summary=""):
    """One projection shared by graph/basis/rule review; executable tree stays canonical."""
    context = spec.get("clinical_context") or {}
    return {"scene": spec.get("clinical_intent"), "target": spec.get("disease_name") or spec.get("disease_code"),
            "population": context.get("population") or "待核对",
            "subtype": context.get("subtype") or "待核对", "phase": context.get("phase") or "待核对",
            "conditions": spec.get("conditions"), "recommendation": spec.get("conclusion"),
            "exceptions": context.get("exceptions") or [], "exclusions": spec.get("exclusions") or [],
            "recommendation_strength": context.get("recommendation_strength") or "原文未标注／待核对",
            "evidence_grade": context.get("evidence_grade") or "原文未标注／待核对",
            "scope_summary": scope_summary, "source": spec.get("source") or {},
            "missing_information": context.get("missing_information") or []}


def coverage_report(chunks, completed, scheduled, excluded=None):
    excluded = set(excluded or [])
    items = [{"chunk_id": item["chunk_id"], "section": item.get("section") or "未分章节",
              "page_start": item.get("page_start"), "page_end": item.get("page_end"),
              "status": "excluded" if item["chunk_id"] in excluded else "processed" if item["chunk_id"] in completed else
                        "failed" if item["chunk_id"] in scheduled else "not_scheduled"} for item in chunks]
    return {"total": len(items), "processed": sum(item["status"] == "processed" for item in items),
            "failed": sum(item["status"] == "failed" for item in items),
            "not_scheduled": sum(item["status"] == "not_scheduled" for item in items),
            "excluded": len(excluded),
            "complete": all(item["status"] in {"processed", "excluded"} for item in items), "items": items,
            "note": "已处理表示已收到模型响应，不代表该章节全部知识均已正确抽取；excluded表示明确的参考文献或纯标题，不参与临床知识抽取"}
