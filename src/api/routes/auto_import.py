"""One-click universal guideline import: PDF → graph + rules + treatment plans."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.extraction.auto_pipeline import AUTO_TEMPLATE_ID, run_auto_pipeline
from src.extraction.repository import ExtractionConfigRepository
from .pdf import UPLOAD_DIR, _doc_store, _now, _persist_doc, parse_pdf

router = APIRouter(prefix="/api/auto", tags=["auto-import"])

ROOT = Path(__file__).parent.parent.parent.parent
config_repo = ExtractionConfigRepository()


@router.post("/import")
async def auto_import(
    file: UploadFile = File(...),
    graph_filename: Optional[str] = Form(None),
    auto_activate: Optional[str] = Form("false"),
    domain: Optional[str] = Form("western"),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持 PDF 文件")
    doc_title = re.sub(r"\.pdf$", "", file.filename, flags=re.IGNORECASE)
    doc_id = f"DOC-{uuid.uuid4().hex[:8]}"
    file_path = UPLOAD_DIR / f"{doc_id}.pdf"
    file_path.write_bytes(await file.read())
    try:
        parsed = parse_pdf(file_path, doc_id)
        config = config_repo.load()
        result = await run_auto_pipeline(
            parsed["chunks"],
            doc_id,
            doc_title,
            config,
            graph_filename=str(graph_filename or ""),
            auto_activate=str(auto_activate or "false").lower() not in {"false", "0", "no"},
        )
    except Exception as error:
        file_path.unlink(missing_ok=True)
        raise HTTPException(422, f"自动生成失败: {str(error)}") from error

    doc_record = {
        "doc_id": doc_id,
        "doc_title": doc_title,
        "file_path": str(file_path),
        "parsed": parsed,
        "entities": result["entities"],
        "suggested_relations": result["relations"],
        "suggested_rules": result["rules"],
        "extraction": {
            "mode": "auto",
            "domain": "western",
            "template_id": AUTO_TEMPLATE_ID,
            "template_name": "通用指南自动抽取模板",
            "graph_filename": result["graph_filename"],
            "warnings": result["warnings"],
            "metadata": result["metadata"],
            "terms": result["terms"],
        },
        "uploaded_at": _now(),
    }
    _doc_store[doc_id] = doc_record
    _persist_doc(doc_record)
    return {
        "doc_id": doc_id,
        "package_id": result["package_id"],
        "graph_filename": result["graph_filename"],
        "target_disease_code": result["target_disease_code"],
        "entity_count": len(result["entities"]),
        "relation_count": len(result["relations"]),
        "rule_count": len(result["rules"]),
        "active_rule_count": len([rule for rule in result["rules"] if rule.get("status") == "active"]),
        "local_term_count": len([
            term for term in result["terms"] if term.get("standard_system") == "LOCAL"
        ]),
        "checks": result["checks"],
        "warnings": result["warnings"],
        "graph_result": result["graph_result"],
    }
