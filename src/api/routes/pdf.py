"""PDF upload, parsing, and clinical entity extraction."""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ...extraction.models import ExtractionTemplate
from ...extraction.graph_binding import (
    resolve_template_graph_filename,
    resolve_upload_graph_filename,
)
from ...domains import get_profile, normalize_domain
from ...extraction.repository import ExtractionConfigRepository, ExtractionTemplateRepository
from ...extraction.service import extract

router = APIRouter(prefix="/api/pdf", tags=["pdf"])

logger = logging.getLogger("uvicorn.error")

UPLOAD_DIR = Path(__file__).parent.parent.parent.parent / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
# Append-only import history: one record per upload attempt (success or
# failure).  New documents always get a fresh DOC id, so records never
# overwrite older ones.
IMPORT_LOG_PATH = UPLOAD_DIR.parent / "imports" / "import_log.jsonl"


def _append_import_log(entry: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = Path(path) if path is not None else IMPORT_LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {**entry, "ts": entry.get("ts") or _now()}
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _load_import_log(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Read every import log entry, skipping corrupt lines (append-only file)."""
    target = Path(path) if path is not None else IMPORT_LOG_PATH
    if not target.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _write_import_log(rows: List[Dict[str, Any]], path: Optional[Path] = None) -> None:
    target = Path(path) if path is not None else IMPORT_LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _remove_doc_files(doc_id: str) -> None:
    """Drop a parsed document from the store and delete its uploaded files."""
    if not re.fullmatch(r"DOC-[A-Za-z0-9-]+", doc_id or ""):
        return
    _doc_store.pop(doc_id, None)
    for suffix in (_PARSED_SUFFIX, ".pdf"):
        try:
            (UPLOAD_DIR / f"{doc_id}{suffix}").unlink(missing_ok=True)
        except OSError:
            logger.warning("导入历史删除：无法移除文档文件 %s%s", doc_id, suffix)

_doc_store: Dict[str, Dict[str, Any]] = {}
_PARSED_SUFFIX = ".parsed.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parsed_path(doc_id: str) -> Path:
    return UPLOAD_DIR / f"{doc_id}{_PARSED_SUFFIX}"


def _persist_doc(doc: Dict[str, Any]) -> None:
    """Persist parsed docs so the web workflow survives server restarts."""
    payload = {k: v for k, v in doc.items() if k != "file_path"}
    _parsed_path(doc["doc_id"]).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _load_doc(doc_id: str) -> Optional[Dict[str, Any]]:
    if doc_id in _doc_store:
        return _doc_store[doc_id]
    path = _parsed_path(doc_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    data["file_path"] = str(UPLOAD_DIR / f"{doc_id}.pdf")
    _doc_store[doc_id] = data
    return data


class ParsedLine(BaseModel):
    page: int
    line: int
    text: str
    bbox: List[float]


class ParsedChunk(BaseModel):
    chunk_id: str
    section: str
    page_start: int
    page_end: int
    line_start: int
    line_end: int
    text: str
    page_numbers: List[int] = []
    line_numbers: List[int] = []


class ExtractedEntity(BaseModel):
    entity_type: str  # disease / lab / exam_finding / symptom / concept
    name: str
    code: str = ""
    concept_code: str = ""
    chunk_id: str = ""
    page: int = 0
    line_start: int = 0
    line_end: int = 0
    quote: str = ""
    confidence: float = 0.0
    attributes: Dict[str, Any] = {}
    candidate_status: str = ""
    standard_system: str = ""
    discovered_from: str = ""
    candidate_original_code: str = ""


class ExtractionResult(BaseModel):
    doc_id: str
    doc_title: str
    total_pages: int
    total_lines: int
    chunks: List[ParsedChunk] = []
    entities: List[ExtractedEntity] = []
    suggested_relations: List[Dict[str, Any]] = []
    suggested_rules: List[Dict[str, Any]] = []
    extraction_mode: str = "local"
    domain: str = "western"
    template_id: str = ""
    template_name: str = ""
    graph_filename: str = ""
    warnings: List[str] = []
    provider_metadata: Dict[str, Any] = {}


SECTION_PATTERN = re.compile(
    r"^(?:[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|[1-9]\d*(?:\s*\.\s*\d+)+[\s\u3000]+(?:[A-Z\u4e00-\u9fff])|[1-9]\d*(?:\u3000+| {2,})[\u4e00-\u9fff]|参考文献$)"
)


def section_chunks(doc_id, lines):
    chunks, current, ancestors = [], [], []
    title = "前言"
    for line in lines:
        text = line['text'].strip()
        if SECTION_PATTERN.match(text):
            if current: chunks.append(_build_chunk(doc_id, title, current))
            number = re.match(r'^(\d+(?:\s*\.\s*\d+)*)', text)
            if number:
                depth = len(re.split(r'\s*\.\s*', number.group(1)))
                ancestors = ancestors[:depth - 1]
                ancestors.append(text)
                title = ' / '.join(ancestors)
            else:
                ancestors = []; title = text
            current = [line]
        else:
            current.append(line)
    if current: chunks.append(_build_chunk(doc_id, title, current))
    return chunks

MAX_CHUNK_CHARS = 12000
WARN_NO_TEXT_LAYER = "该 PDF 无文字层，已使用 OCR 识别文本"

extraction_templates = ExtractionTemplateRepository()
extraction_config = ExtractionConfigRepository()


def parse_pdf(file_path: Path, doc_id: str) -> Dict[str, Any]:
    """Parse PDF into structured lines and chunks."""
    import fitz

    doc = fitz.open(str(file_path))
    lines = []
    image_only_pages = []
    tables = []
    layout_warnings = []
    for page_num, page in enumerate(doc, start=1):
        blocks = page.get_text("dict")["blocks"]
        line_in_page = 0
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                text = "".join(span["text"] for span in line["spans"]).strip()
                if not text:
                    continue
                line_in_page += 1
                lines.append({
                    "page": page_num,
                    "line": line_in_page,
                    "text": text,
                    "bbox": [round(v, 1) for v in line["bbox"]],
                })
        if line_in_page == 0 and any(block.get("type") == 1 for block in blocks):
            image_only_pages.append(page_num)
        if hasattr(page, "find_tables"):
            try:
                found = page.find_tables()
                for index, table in enumerate(found.tables):
                    tables.append({"table_id": f"{doc_id}:p{page_num}:t{index + 1}", "page": page_num,
                                   "bbox": list(table.bbox), "rows": table.extract(),
                                   "review_required": True})
            except Exception:
                layout_warnings.append(f"第{page_num}页表格结构提取失败，请查看PDF原页")
        elif page_num == 1:
            layout_warnings.append("当前PDF组件未提供表格提取能力，请对照PDF原页核对表格")
    total_pages = doc.page_count
    doc.close()

    ocr_used = False
    if not lines:
        lines = _ocr_pdf_lines(file_path)
        ocr_used = True
    elif image_only_pages:
        lines.extend(_ocr_pdf_lines(file_path, page_numbers=set(image_only_pages)))
        lines.sort(key=lambda item: (item["page"], item["line"]))
        ocr_used = True

    chunks = section_chunks(doc_id, lines)

    chunks = _split_long_chunks(chunks)
    for chunk in chunks:
        chunk["tables"] = [table for table in tables if chunk.get("page_start", 1) <= table["page"] <= chunk.get("page_end", 1)]

    return {
        "doc_id": doc_id,
        "total_pages": total_pages,
        "total_lines": len(lines),
        "lines": lines,
        "chunks": chunks,
        "ocr_used": ocr_used,
        "tables": tables,
        "reading_quality": {"image_only_pages": image_only_pages,
                            "pages_with_text": sorted({line["page"] for line in lines}),
                            "pages_without_text": sorted(set(range(1, total_pages + 1)) - {line["page"] for line in lines}),
                            "layout_review_required": True,
                            "layout_warnings": layout_warnings,
                            "note": "表格、流程图及脚注仍需对照PDF核对，文字提取成功不等于版面结构完整"},
    }


def _ocr_pdf_lines(file_path: Path, page_numbers=None) -> List[Dict[str, Any]]:
    """OCR fallback for PDFs without a text layer (outlined fonts or scans)."""
    try:
        import cv2
        import numpy as np
        import fitz
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "该 PDF 没有可提取的文字层，需要 OCR 组件支持，请先执行: pip install rapidocr-onnxruntime"
        ) from exc

    logger.info("PDF has no text layer, running OCR fallback: %s", file_path)
    ocr = RapidOCR()
    doc = fitz.open(str(file_path))
    lines: List[Dict[str, Any]] = []
    for page_num, page in enumerate(doc, start=1):
        if page_numbers is not None and page_num not in page_numbers:
            continue
        try:
            pix = page.get_pixmap(dpi=150)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
            else:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            result, _elapsed = ocr(img)
        except Exception as exc:
            logger.warning("OCR failed on page %s: %s", page_num, exc)
            continue
        page_lines = 0
        for item in result or []:
            text = str(item[1]).strip()
            if not text:
                continue
            box = item[0]
            xs = [float(pt[0]) for pt in box]
            ys = [float(pt[1]) for pt in box]
            page_lines += 1
            lines.append({
                "page": page_num,
                "line": page_lines,
                "text": text,
                "bbox": [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)],
            })
        if page_num % 10 == 0 or page_num == doc.page_count:
            logger.info("OCR progress %s/%s pages", page_num, doc.page_count)
    doc.close()
    return lines

def _build_chunk(doc_id, section, lines):
    return {
        "chunk_id": f"{doc_id}_p{lines[0]['page']}_l{lines[0]['line']}-{lines[-1]['line']}",
        "section": section,
        "page_start": lines[0]["page"],
        "page_end": lines[-1]["page"],
        "line_start": lines[0]["line"],
        "line_end": lines[-1]["line"],
        "page_numbers": [line["page"] for line in lines],
        "line_numbers": [line["line"] for line in lines],
        "text": "\n".join(l["text"] for l in lines),
    }


def _split_long_chunks(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Split oversized sections so local rule generation stays local.

    A 100k-character section makes every disease in it share the same
    neighbours; paragraph-sized chunks preserve the disease-section boundary.
    """
    result: List[Dict[str, Any]] = []
    for chunk in chunks:
        if len(chunk.get("text", "")) <= MAX_CHUNK_CHARS:
            result.append(chunk)
            continue
        raw_lines = chunk["text"].split("\n")
        page_numbers = chunk.get("page_numbers") or [chunk.get("page_start", 1)] * len(raw_lines)
        line_numbers = chunk.get("line_numbers") or list(
            range(int(chunk.get("line_start", 1)),
                  int(chunk.get("line_start", 1)) + len(raw_lines))
        )
        group: List[int] = []
        size = 0
        part = 1
        for index, line in enumerate(raw_lines):
            if group and size + len(line) + 1 > MAX_CHUNK_CHARS // 2:
                result.append(_sub_chunk(chunk, raw_lines, page_numbers, line_numbers, group, part))
                part += 1
                group, size = [], 0
            group.append(index)
            size += len(line) + 1
        if group:
            result.append(_sub_chunk(chunk, raw_lines, page_numbers, line_numbers, group, part))
    return result


def _sub_chunk(chunk, raw_lines, page_numbers, line_numbers, indices, part):
    first, last = indices[0], indices[-1]
    return {
        "chunk_id": f"{chunk['chunk_id']}_part{part}",
        "section": chunk.get("section", ""),
        "page_start": page_numbers[first],
        "page_end": page_numbers[last],
        "line_start": line_numbers[first],
        "line_end": line_numbers[last],
        "page_numbers": [page_numbers[index] for index in indices],
        "line_numbers": [line_numbers[index] for index in indices],
        "text": "\n".join(raw_lines[index] for index in indices),
    }


@router.post("/upload", response_model=ExtractionResult)
async def upload_pdf(
    file: UploadFile = File(...),
    extraction_mode: Optional[str] = Form(None),
    template_id: Optional[str] = Form(None),
    domain: Optional[str] = Form(None),
    graph_filename: Optional[str] = Form(None),
):
    """Upload a guideline PDF, parse it, and extract clinical entities."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持 PDF 文件")
    doc_title = re.sub(r"\.pdf$", "", file.filename, flags=re.IGNORECASE)

    doc_id = f"DOC-{uuid.uuid4().hex[:8]}"
    file_path = UPLOAD_DIR / f"{doc_id}.pdf"

    import time
    started = time.time()
    mode = (extraction_mode or "").lower()

    def _log_import(status: str, **extra) -> None:
        _append_import_log({
            "doc_id": doc_id,
            "doc_title": doc_title,
            "filename": file.filename,
            "status": status,
            "mode": mode,
            **extra,
        })

    content = await file.read()
    file_path.write_bytes(content)

    try:
        parsed = parse_pdf(file_path, doc_id)
    except Exception as e:
        file_path.unlink(missing_ok=True)
        _log_import("failed", stage="parse", error=str(e)[:300])
        raise HTTPException(422, f"PDF解析失败: {str(e)}")

    config = extraction_config.load()
    mode = (extraction_mode or config["default_mode"]).lower()
    if mode not in {"local", "llm"}:
        file_path.unlink(missing_ok=True)
        raise HTTPException(422, "解析方式只支持 local 或 llm")
    selected_template_id = template_id or ""
    requested_domain = normalize_domain(domain) if domain else ""
    if not selected_template_id:
        if requested_domain:
            domain_defaults = config.get("domain_defaults") or {}
            selected_template_id = (
                str(domain_defaults.get(requested_domain) or "")
                or get_profile(requested_domain).default_template_id
                or str(config.get("default_template_id") or "")
            )
        else:
            selected_template_id = str(config.get("default_template_id") or "")
    template: ExtractionTemplate = extraction_templates.get(selected_template_id)
    if requested_domain and normalize_domain(template.domain) != requested_domain:
        file_path.unlink(missing_ok=True)
        _log_import("failed", stage="template", error=f"模板 {template.template_id} 不属于 {requested_domain}")
        raise HTTPException(422, f"解析模板 {template.template_id} 不属于 {requested_domain} 领域")
    try:
        provider_result = await extract(
            mode, parsed["chunks"], template, doc_id, doc_title, config
        )
    except Exception as e:
        file_path.unlink(missing_ok=True)
        _log_import("failed", stage="extract", template_id=template.template_id, error=str(e)[:300])
        raise HTTPException(422, f"实体抽取失败: {str(e)}")

    extra_warnings = [WARN_NO_TEXT_LAYER] if parsed.get("ocr_used") else []
    total_chars = sum(len(chunk.get("text", "")) for chunk in parsed["chunks"])
    if mode == "local" and (total_chars > 30000 or len(parsed["chunks"]) > 12):
        extra_warnings.append(
            "本地模板解析在长篇指南上容易产生重复或噪声规则；建议改用大模型解析并人工审核。"
        )
    try:
        graph_filename = resolve_upload_graph_filename(template, graph_filename or "")
    except ValueError as e:
        file_path.unlink(missing_ok=True)
        _log_import("failed", stage="template", error=str(e)[:300])
        raise HTTPException(422, str(e))

    result = ExtractionResult(
        doc_id=doc_id,
        doc_title=doc_title,
        total_pages=parsed["total_pages"],
        total_lines=parsed["total_lines"],
        chunks=[ParsedChunk(**c) for c in parsed["chunks"]],
        entities=[ExtractedEntity(**e) for e in provider_result.entities],
        suggested_relations=provider_result.suggested_relations,
        suggested_rules=provider_result.suggested_rules,
        extraction_mode=mode,
        domain=normalize_domain(template.domain),
        template_id=template.template_id,
        template_name=template.name,
        graph_filename=graph_filename,
        warnings=[*provider_result.warnings, *extra_warnings],
        provider_metadata=provider_result.metadata,
    )

    # Save parsed data for later retrieval
    doc_record = {
        "doc_id": doc_id,
        "doc_title": doc_title,
        "file_path": str(file_path),
        "parsed": parsed,
        "entities": provider_result.entities,
        "suggested_relations": provider_result.suggested_relations,
        "suggested_rules": provider_result.suggested_rules,
        "extraction": {
            "mode": mode,
            "domain": normalize_domain(template.domain),
            "template_id": template.template_id,
            "template_name": template.name,
            "graph_filename": graph_filename,
            "warnings": [*provider_result.warnings, *extra_warnings],
            "metadata": provider_result.metadata,
        },
        "uploaded_at": _now(),
    }
    _doc_store[doc_id] = doc_record
    _persist_doc(doc_record)

    _log_import(
        "success",
        template_id=template.template_id,
        domain=normalize_domain(template.domain),
        pages=parsed["total_pages"],
        lines=parsed["total_lines"],
        chunk_count=len(parsed["chunks"]),
        entity_count=len(provider_result.entities),
        relation_count=len(provider_result.suggested_relations),
        rule_count=len(provider_result.suggested_rules),
        ocr_used=bool(parsed.get("ocr_used")),
        duration_ms=int((time.time() - started) * 1000),
    )

    return result


@router.get("/import-logs")
async def get_import_logs(page: int = 1, page_size: int = 10):
    """导入历史（分页、倒序），每条记录一次导入尝试的事件留痕。"""
    rows = _load_import_log()
    total = len(rows)
    page = max(1, int(page))
    page_size = min(max(1, int(page_size)), 200)
    start = (page - 1) * page_size
    items = list(rows[start:start + page_size])
    items.reverse()  # 最新在前
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.delete("/import-logs/{job_id}")
async def delete_import_log(job_id: str):
    """删除一次导入尝试的全部日志留痕，并联动删除对应的已上传文档。"""
    if not re.fullmatch(r"JOB-[A-Za-z0-9-]+", job_id or ""):
        raise HTTPException(422, "导入任务标识无效")
    rows = _load_import_log()
    removed = [row for row in rows if row.get("job_id") == job_id]
    if not removed:
        raise HTTPException(404, "导入记录不存在")
    _write_import_log([row for row in rows if row.get("job_id") != job_id])
    doc_ids = sorted({row["doc_id"] for row in removed if row.get("doc_id")})
    for doc_id in doc_ids:
        _remove_doc_files(doc_id)
    return {"removed_entries": len(removed), "deleted_doc_ids": doc_ids}


@router.get("/{doc_id}/file")
async def get_pdf_file(doc_id: str):
    if not re.fullmatch(r"DOC-[A-Za-z0-9-]+", doc_id):
        raise HTTPException(422, "指南标识无效")
    path = UPLOAD_DIR / f"{doc_id}.pdf"
    if not path.is_file():
        raise HTTPException(404, "原始PDF不可用，请恢复该指南文件")
    return FileResponse(path, media_type="application/pdf", headers={"Content-Disposition": "inline"})


@router.get("/{doc_id}/lines")
async def get_lines(doc_id: str, page: int = 0):
    """Get parsed lines for a document, optionally filtered by page."""
    doc = _load_doc(doc_id)
    if not doc:
        raise HTTPException(404, "文档未找到")
    lines = doc["parsed"]["lines"]
    if page > 0:
        lines = [l for l in lines if l["page"] == page]
    return {"doc_id": doc_id, "lines": lines}


@router.get("/{doc_id}/result")
async def get_document_result(doc_id: str):
    """Return the complete review payload so an upload survives navigation."""
    doc = _load_doc(doc_id)
    if not doc:
        raise HTTPException(404, "文档未找到")
    parsed = doc["parsed"]
    extraction = doc.get("extraction", {})
    graph_filename = str(extraction.get("graph_filename") or "")
    if not graph_filename and extraction.get("template_id"):
        try:
            template = extraction_templates.get(
                str(extraction["template_id"]), allow_retired=True
            )
            graph_filename = resolve_template_graph_filename(template)
        except Exception:
            graph_filename = ""
    return ExtractionResult(
        doc_id=doc["doc_id"],
        doc_title=doc["doc_title"],
        total_pages=parsed["total_pages"],
        total_lines=parsed["total_lines"],
        chunks=[ParsedChunk(**c) for c in parsed["chunks"]],
        entities=[ExtractedEntity(**e) for e in doc.get("entities") or []],
        suggested_relations=doc.get("suggested_relations", []),
        suggested_rules=doc.get("suggested_rules") or [],
        extraction_mode=extraction.get("mode", "local"),
        domain=extraction.get("domain", "western"),
        template_id=extraction.get("template_id", ""),
        template_name=extraction.get("template_name", ""),
        graph_filename=graph_filename,
        warnings=extraction.get("warnings", []),
        provider_metadata=extraction.get("metadata", {}),
    )


@router.get("/{doc_id}/entities")
async def get_entities(doc_id: str):
    """Get extracted entities for a document."""
    doc = _load_doc(doc_id)
    if not doc:
        raise HTTPException(404, "文档未找到")
    return {"doc_id": doc_id, "entities": doc.get("entities") or []}


@router.get("/documents")
async def list_documents():
    """List all uploaded documents."""
    docs = list(_doc_store.values())
    for path in UPLOAD_DIR.glob(f"*{_PARSED_SUFFIX}"):
        doc_id = path.name[: -len(_PARSED_SUFFIX)]
        if not any(d["doc_id"] == doc_id for d in docs):
            doc = _load_doc(doc_id)
            if doc:
                docs.append(doc)
    return [
        {
            "doc_id": d["doc_id"],
            "doc_title": d["doc_title"],
            "total_pages": d["parsed"]["total_pages"],
            "total_lines": d["parsed"]["total_lines"],
            "entity_count": len(d.get("entities") or []),
            "domain": d.get("extraction", {}).get("domain", "western"),
            "template_id": d.get("extraction", {}).get("template_id", ""),
            "uploaded_at": d["uploaded_at"],
        }
        for d in sorted(docs, key=lambda x: x.get("uploaded_at", ""), reverse=True)
    ]



_reparse_locks = {}


async def reparse_document(doc_id: str, controller=None):
    """Retry failed coverage; an empty extraction is reparsed in full."""
    import asyncio
    import copy
    from src.review.quality import coverage_report
    from src.review.clinical_basis import digest
    lock = _reparse_locks.setdefault(doc_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "此文档正在重新解析")
    async with lock:
        original = _load_doc(doc_id)
        if not original:
            raise HTTPException(404, "文档未找到")
        baseline_bytes = _parsed_path(doc_id).read_bytes()
        previous = copy.deepcopy(original)
        doc = copy.deepcopy(original)
        extraction = doc.get('extraction') or {}
        template = extraction_templates.get(extraction.get('template_id'))
        old_coverage = (extraction.get('metadata') or {}).get('coverage') or {}
        excluded = {item['chunk_id'] for item in old_coverage.get('items') or [] if item['status'] == 'excluded'}
        successful = {item['chunk_id'] for item in old_coverage.get('items') or [] if item['status'] in {'processed', 'excluded'}}
        if not doc.get('entities') or any('已忽略引用未知 chunk_id 的实体' in warning for warning in extraction.get('warnings') or []):
            successful = set()
        if doc['parsed'].get('lines') and len({c.get('section') for c in doc['parsed']['chunks']}) <= 1:
            doc['parsed']['chunks'] = section_chunks(doc_id, doc['parsed']['lines'])
            successful = set(); excluded = set()
            doc['entities'] = []; doc['suggested_relations'] = []; doc['suggested_rules'] = []
        selected = [chunk for chunk in doc['parsed']['chunks'] if chunk['chunk_id'] not in successful]
        if not selected:
            return await get_document_result(doc_id)
        old_ids = {chunk['chunk_id'] for chunk in selected}
        config = copy.deepcopy(extraction_config.load())
        if doc.get('reparse_history') and successful:
            config.setdefault('llm', {})['max_chunk_chars'] = min(int(config.get('llm', {}).get('max_chunk_chars', 12000)), 1200)
        result = await extract(extraction.get('mode','llm'), selected, template, doc_id, doc['doc_title'], config, controller=controller)
        doc['parsed']['chunks'] = [chunk for chunk in doc['parsed']['chunks'] if chunk['chunk_id'] not in old_ids] + selected
        for key, values in [('entities', result.entities), ('suggested_relations', result.suggested_relations), ('suggested_rules', result.suggested_rules)]:
            existing = [item for item in doc.get(key) or [] if (item.get('chunk_id') or (item.get('source') or {}).get('chunk_id')) not in old_ids]
            doc[key] = existing + values
        new_coverage = result.metadata.get('coverage') or {}
        successful.update(item['chunk_id'] for item in new_coverage.get('items') or [] if item['status'] == 'processed')
        excluded.update(item['chunk_id'] for item in new_coverage.get('items') or [] if item['status'] == 'excluded')
        coverage = coverage_report(doc['parsed']['chunks'], successful, {chunk['chunk_id'] for chunk in doc['parsed']['chunks']}, excluded)
        metadata = {**extraction.get('metadata', {}), **result.metadata, 'coverage': coverage}
        doc.setdefault('reparse_history', []).append({'at': _now(), 'previous_coverage': old_coverage,
                                                     'previous_warnings': extraction.get('warnings', []), 'retried_chunks': sorted(old_ids)})
        doc['extraction'] = {**extraction, 'metadata': metadata, 'warnings': result.warnings}
        if digest(_load_doc(doc_id)) != digest(original) or _parsed_path(doc_id).read_bytes() != baseline_bytes:
            raise HTTPException(409, "文档已被修改，请刷新后重新解析")
        # Keep a recoverable copy; no graph or clinical rule is imported here.
        backup = _parsed_path(doc_id).with_suffix('.before-reparse.json')
        backup.write_text(json.dumps({k:v for k,v in previous.items() if k != 'file_path'}, ensure_ascii=False), encoding='utf-8')
        _persist_doc(doc)
        _doc_store[doc_id] = doc
        return await get_document_result(doc_id)


@router.post('/{doc_id}/reparse')
async def retry_document_extraction(doc_id: str):
    return await reparse_document(doc_id)
