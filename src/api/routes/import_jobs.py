"""Async import jobs: start/pause/resume/stop over guide ingestion."""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.api.import_job_manager import (
    FAILED, SUCCEEDED, STOPPED, ImportController, ImportJob, manager,
)
from src.extraction.auto_pipeline import run_auto_pipeline
from src.extraction.domain_detection import detect_guideline_domain
from src.extraction.graph_binding import resolve_upload_graph_filename
from src.extraction.repository import ExtractionConfigRepository
from .pdf import extraction_templates

router = APIRouter(prefix="/api/import", tags=["import-jobs"])

UPLOAD_DIR_PDF = Path(__file__).resolve().parents[3] / "data" / "uploads"
_VALID_MODES = {"template-local", "template-llm", "auto"}


async def _run_template_job(
    controller: ImportController, *, file_path: Path, doc_id: str, doc_title: str,
    mode: str, template_id: str, graph_name: str,
) -> Dict[str, Any]:
    """模板模式作业体：镜像 pdf.upload_pdf 的解析-抽取-落库链路。

    ``graph_name`` 已在 create 路由经 ``resolve_upload_graph_filename`` 校验。
    """
    from .pdf import (  # deferred: 保持本仓库函数内导入风格
        _doc_store, _now, _persist_doc, extraction_templates, parse_pdf,
        WARN_NO_TEXT_LAYER,
    )
    from src.extraction.service import extract

    config = ExtractionConfigRepository().load()
    parsed = parse_pdf(file_path, doc_id)
    _persist_doc({"doc_id": doc_id, "doc_title": doc_title, "parsed": parsed,
                  "file_path": str(file_path), "uploaded_at": _now()})
    await controller.checkpoint(stage="模板抽取", done=0, total=1)
    template = extraction_templates.get(template_id)
    extraction_mode = "llm" if mode == "template-llm" else "local"
    provider = await extract(
        extraction_mode,
        parsed["chunks"], template, doc_id, doc_title, config, controller=controller,
    )
    # 附加告警与 upload_pdf 对齐：OCR 兜底 + 本地长文噪声提示
    extra_warnings = [WARN_NO_TEXT_LAYER] if parsed.get("ocr_used") else []
    total_chars = sum(len(chunk.get("text", "")) for chunk in parsed["chunks"])
    if extraction_mode == "local" and (total_chars > 30000 or len(parsed["chunks"]) > 12):
        extra_warnings.append(
            "本地模板解析在长篇指南上容易产生重复或噪声规则；建议改用大模型解析并人工审核。"
        )
    all_warnings = [*provider.warnings, *extra_warnings]
    # —— 以下为写库段（不可中断）——
    controller.finalizing = True
    await controller.checkpoint(stage="保存解析结果")
    doc_record = {
        "doc_id": doc_id, "doc_title": doc_title, "file_path": str(file_path),
        "uploaded_at": _now(),
        "parsed": parsed, "entities": provider.entities,
        "suggested_relations": provider.suggested_relations,
        "suggested_rules": provider.suggested_rules,
        "extraction": {
            "mode": extraction_mode,
            "domain": template.domain, "template_id": template.template_id,
            "template_name": template.name, "graph_filename": graph_name,
            "warnings": all_warnings, "metadata": provider.metadata,
        },
    }
    _doc_store[doc_id] = doc_record
    _persist_doc(doc_record)
    return {
        "doc_id": doc_id, "doc_title": doc_title, "graph_filename": graph_name,
        "total_pages": parsed["total_pages"],
        "entity_count": len(provider.entities),
        "relation_count": len(provider.suggested_relations),
        "rule_count": len(provider.suggested_rules),
        "warnings": all_warnings,
    }


async def _run_auto_job(
    controller: ImportController, *, file_path: Path, doc_id: str, doc_title: str,
    graph_filename: str, auto_activate: bool, domain: str = "auto", template_id: str = "",
) -> Dict[str, Any]:
    """一键自动作业体：镜像 auto_import.auto_import 的 pipeline 调用。"""
    from .pdf import parse_pdf, _persist_doc, _now
    parsed = parse_pdf(file_path, doc_id)
    if template_id:
        template = extraction_templates.get(template_id)
        if template is None:
            raise ValueError(f"解析模板不存在: {template_id}")
        template_domain = str(template.domain or "western")
        if domain != "auto" and domain != template_domain:
            raise ValueError("所选指南领域与抽取模板领域不一致")
        graph_name = resolve_upload_graph_filename(template, graph_filename or "")
        result = await _run_template_job(
            controller, file_path=file_path, doc_id=doc_id, doc_title=doc_title,
            mode="template-llm", template_id=template.template_id,
            graph_name=graph_name,
        )
        if not result.get("relation_count"):
            raise ValueError("所选模板未生成任何关系，已停止自动入库，请检查抽取告警后重试")
        from .guide_packages import (
            GuidePackageGenerateRequest, GuidePackageImportRequest,
            generate_package, import_package,
        )
        from .kg import KG_DIR
        graph_mode = "existing" if (KG_DIR / graph_name).exists() else "new"
        generated = await generate_package(GuidePackageGenerateRequest(
            doc_id=doc_id, domain=template_domain, template_id=template.template_id,
            graph_target={"mode": graph_mode, "filename": graph_name},
        ))
        package_id = generated["package"]["package_id"]
        imported = await import_package(package_id, GuidePackageImportRequest())
        result.update(
            domain=template_domain,
            template_id=template.template_id,
            package_id=package_id,
            graph_result=imported["graph"],
            domain_detection={"domain": template_domain, "source": "selected_template"},
        )
        return result
    detection = detect_guideline_domain(parsed["chunks"], doc_title, domain)
    if detection["requires_confirmation"]:
        raise ValueError(
            "指南领域识别置信度不足，请明确选择中医或西医。识别依据："
            + "；".join(detection["reasons"])
        )
    if detection["domain"] == "tcm":
        template = extraction_templates.get("TCM_GUIDELINE_V3")
        graph_name = resolve_upload_graph_filename(
            template, graph_filename or f"KG-TCM-{doc_id}.json"
        )
        result = await _run_template_job(
            controller, file_path=file_path, doc_id=doc_id, doc_title=doc_title,
            mode="template-llm", template_id=template.template_id,
            graph_name=graph_name,
        )
        result["domain"] = "tcm"
        result["template_id"] = template.template_id
        result["domain_detection"] = detection
        result.setdefault("warnings", []).insert(0, "已识别为中医指南并使用中医专用模板")
        if not result.get("relation_count"):
            raise ValueError("中医专用模板未生成任何关系，已停止自动入库，请检查抽取告警后重试")
        from .guide_packages import (
            GuidePackageGenerateRequest, GuidePackageImportRequest,
            generate_package, import_package,
        )
        generated = await generate_package(GuidePackageGenerateRequest(
            doc_id=doc_id, domain="tcm", template_id=template.template_id,
            graph_target={"mode": "new", "filename": graph_name},
        ))
        package_id = generated["package"]["package_id"]
        imported = await import_package(package_id, GuidePackageImportRequest())
        result["package_id"] = package_id
        result["graph_result"] = imported["graph"]
        return result
    _persist_doc({"doc_id": doc_id, "doc_title": doc_title, "parsed": parsed,
                  "file_path": str(file_path), "uploaded_at": _now()})
    result = await run_auto_pipeline(
        parsed["chunks"], doc_id, doc_title,
        ExtractionConfigRepository().load(),
        graph_filename=graph_filename, auto_activate=auto_activate,
        controller=controller,
        require_import_quality=True,
    )
    result["domain"] = "western"
    result["template_id"] = "UNIVERSAL_GUIDELINE_V1"
    result["domain_detection"] = detection
    _persist_doc({"doc_id": doc_id, "doc_title": doc_title, "parsed": parsed,
                  "file_path": str(file_path), "uploaded_at": _now(),
                  "extraction": {"metadata": result.get("metadata") or {}, "warnings": result.get("warnings") or []}})
    return result  # run_auto_pipeline 的返回 dict 即结果摘要


@router.post("/jobs", status_code=201)
async def create_import_job(
    file: UploadFile = File(...),
    mode: str = Form("template-local"),
    template_id: str = Form(""),
    graph_filename: str = Form(""),
    auto_activate: str = Form("false"),
    domain: str = Form("auto"),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持 PDF 文件")
    if mode not in _VALID_MODES:
        raise HTTPException(422, f"mode 必须是 {'/'.join(sorted(_VALID_MODES))}")
    if domain not in {"auto", "western", "tcm"}:
        raise HTTPException(422, "domain 必须是 auto、western 或 tcm")
    if mode in {"template-local", "template-llm"} and not template_id:
        raise HTTPException(422, "模板模式必须提供 template_id")
    resolved_graph = ""
    if mode in {"template-local", "template-llm"}:
        template = extraction_templates.get(template_id)
        if template is None:
            raise HTTPException(422, f"解析模板不存在: {template_id}")
        try:
            resolved_graph = resolve_upload_graph_filename(template, graph_filename or "")
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    doc_title = re.sub(r"\.pdf$", "", file.filename, flags=re.IGNORECASE)
    doc_id = f"DOC-{uuid.uuid4().hex[:8]}"
    UPLOAD_DIR_PDF.mkdir(parents=True, exist_ok=True)
    file_path = UPLOAD_DIR_PDF / f"{doc_id}.pdf"
    file_path.write_bytes(await file.read())

    def runner(controller: ImportController):
        if mode == "auto":
            return _run_auto_job(
                controller, file_path=file_path, doc_id=doc_id, doc_title=doc_title,
                graph_filename=graph_filename,
                auto_activate=auto_activate.lower() in {"1", "true", "yes"},
                domain=domain,
                template_id=template_id,
            )
        return _run_template_job(
            controller, file_path=file_path, doc_id=doc_id, doc_title=doc_title,
            mode=mode, template_id=template_id, graph_name=resolved_graph,
        )

    job = manager.submit(mode, doc_title, runner, on_finish=_log_job_finished)
    _log_job_created(job, doc_id)
    return {"job_id": job.job_id, "status": job.status, "doc_id": doc_id}


# job_id -> 建任务时的 doc_id：终态前 job.result 为空，pause/resume 留痕需回查
_JOB_DOC_IDS: Dict[str, str] = {}


def _job_doc_id(job: ImportJob) -> str:
    """留痕 doc_id：先查建任务登记，再兜底 result（auto/template 终态均含 doc_id）。"""
    if job.job_id in _JOB_DOC_IDS:
        return _JOB_DOC_IDS[job.job_id]
    result = job.result if isinstance(job.result, dict) else {}
    return result.get("doc_id") or ""


def _log_job_event(job: ImportJob, status: str, **extra: Any) -> None:
    """导入日志留痕：字段沿用 _log_job_created 的惯例（doc_id/doc_title/status/mode/job_id）。"""
    from .pdf import _append_import_log
    _append_import_log({
        "doc_id": _job_doc_id(job), "doc_title": job.doc_title, "status": status,
        "mode": job.mode.replace("template-", ""), "job_id": job.job_id, **extra,
    })


# job.result 键 -> 留痕键（成功条目仅在 result 有时附带）
_RESULT_COUNT_KEYS = {
    "entity_count": "entity_count",
    "relation_count": "relation_count",
    "rule_count": "rule_count",
    "total_pages": "pages",
}


def _log_job_finished(job: ImportJob) -> None:
    """manager.submit 的 on_finish：succeeded/failed/stopped 终态留痕。"""
    if job.status == SUCCEEDED:
        result = job.result if isinstance(job.result, dict) else {}
        _log_job_event(job, "success", **{
            log_key: result[res_key]
            for res_key, log_key in _RESULT_COUNT_KEYS.items()
            if res_key in result
        })
    elif job.status == FAILED:
        _log_job_event(job, "failed", error=(job.error or "")[:300])
    elif job.status == STOPPED:
        _log_job_event(job, "stopped")
    _JOB_DOC_IDS.pop(job.job_id, None)  # 终态后无需回查，防 map 无界增长


def _log_job_created(job: ImportJob, doc_id: str) -> None:
    _JOB_DOC_IDS[job.job_id] = doc_id
    _log_job_event(job, "started")


@router.get("/jobs")
async def list_import_jobs():
    return {"jobs": [job.to_dict() for job in manager.list_jobs()]}


@router.get("/jobs/{job_id}")
async def get_import_job(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在（服务可能已重启）")
    return job.to_dict()


async def _dispatch(job_id: str, action: str) -> ImportJob:
    """pause/resume 为 async 状态迁移；错误转 404/409。"""
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在（服务可能已重启）")
    try:
        if action == "pause":
            return await manager.pause(job_id)
        return await manager.resume(job_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/jobs/{job_id}/pause")
async def pause_import_job(job_id: str):
    job = await _dispatch(job_id, "pause")
    _log_job_event(job, "paused")
    return job.to_dict()


@router.post("/jobs/{job_id}/resume")
async def resume_import_job(job_id: str):
    job = await _dispatch(job_id, "resume")
    _log_job_event(job, "resumed")
    return job.to_dict()


@router.post("/jobs/{job_id}/stop")
async def stop_import_job(job_id: str):
    if manager.get(job_id) is None:
        raise HTTPException(404, "任务不存在（服务可能已重启）")
    try:
        job = await manager.stop(job_id)  # 等待 cancel 落地（finalizing 时先跑完）
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return job.to_dict()
