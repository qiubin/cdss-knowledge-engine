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
from src.extraction.auto_pipeline import AUTO_TEMPLATE_ID, run_auto_pipeline
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
        "coverage": (provider.metadata or {}).get("coverage") or {},
        "warnings": all_warnings,
    }


def _persist_auto_document(doc_id, doc_title, parsed, file_path, result,
                           template_id: str = "", template_name: str = ""):
    """Keep the document result consistent with the imported auto package."""
    from .pdf import _persist_doc, _doc_store, _now
    record = {
        "doc_id": doc_id, "doc_title": doc_title, "parsed": parsed,
        "file_path": str(file_path), "uploaded_at": _now(),
        "entities": result.get("entities") or [],
        "suggested_relations": result.get("relations") or [],
        "suggested_rules": result.get("rules") or [],
        "extraction": {
            "mode": "auto", "domain": "western",
            "template_id": template_id or AUTO_TEMPLATE_ID,
            "template_name": template_name or "通用指南自动抽取模板",
            "graph_filename": result.get("graph_filename") or "",
            "metadata": result.get("metadata") or {}, "warnings": result.get("warnings") or [],
        },
    }
    _persist_doc(record)
    _doc_store[doc_id] = record


async def _run_tcm_template_job(controller, *, parsed, file_path, doc_id, doc_title,
                                graph_filename: str, template) -> Dict[str, Any]:
    """中医指南作业体：中医专用模板抽取 → 知识包生成与入库。

    辨病辨证结果与西医图谱分开维护；relation 门禁为 0 时中止入库但保留审核区数据。
    """
    template = template or extraction_templates.get("TCM_GUIDELINE_V3")
    graph_name = resolve_upload_graph_filename(template, graph_filename or f"KG-TCM-{doc_id}.json")
    result = await _run_template_job(
        controller, file_path=file_path, doc_id=doc_id, doc_title=doc_title,
        mode="template-llm", template_id=template.template_id,
        graph_name=graph_name,
    )
    coverage = result.get("coverage") or {}
    if coverage.get("complete") is False:
        raise ValueError(
            "中医指南解析覆盖不完整，已停止自动入库："
            f"片段共 {coverage.get('total', 0)} 个，模型响应 {coverage.get('processed', 0)} 个，"
            f"失败 {coverage.get('failed', 0)} 个，未安排 {coverage.get('not_scheduled', 0)} 个。"
            "解析结果已保留，请稍后重试失败片段"
        )
    if not result.get("relation_count"):
        raise ValueError(
            "中医专用模板未生成任何关系，已停止自动入库；解析结果已保留在审核区，"
            "可人工补全实体关系后手动生成知识包，或检查抽取告警后重试"
        )
    from .guide_packages import (
        GuidePackageGenerateRequest, GuidePackageImportRequest,
        generate_package, import_package,
    )
    from .kg import KG_DIR
    graph_mode = "existing" if (KG_DIR / graph_name).exists() else "new"
    generated = await generate_package(GuidePackageGenerateRequest(
        doc_id=doc_id, domain="tcm", template_id=template.template_id,
        graph_target={"mode": graph_mode, "filename": graph_name},
    ))
    package_id = generated["package"]["package_id"]
    imported = await import_package(package_id, GuidePackageImportRequest())
    result.update(
        domain="tcm",
        template_id=template.template_id,
        package_id=package_id,
        graph_result=imported["graph"],
        domain_detection={"domain": "tcm", "source": "panel_selection"},
    )
    return result


async def _run_western_statement_job(controller, *, parsed, file_path, doc_id, doc_title,
                                     graph_filename: str, template, auto_activate: bool) -> Dict[str, Any]:
    """西医指南作业体：两阶段临床陈述流水线（陈述 → 图谱/规则/开放术语）。

    单轮模板抽取无法达到同等结构化质量——曾导致整本指南只产出个别疾病、
    无实体无关系，因此西医导入统一走本流水线。入流水线前做 TCM 内容守卫，
    防止把中医指南误送西医链路白耗 LLM。
    """
    from .pdf import _persist_doc, _now
    detected = detect_guideline_domain(parsed["chunks"], doc_title, "auto")
    if detected["domain"] == "tcm" and not detected["requires_confirmation"]:
        raise ValueError(
            "检测到中医指南，但这是西医指南导入链路；"
            "请改用「中医指南导入」面板并选择中医模板后重试"
        )
    graph_name = (
        resolve_upload_graph_filename(template, graph_filename or "")
        if template else (graph_filename or "")
    )
    _persist_doc({"doc_id": doc_id, "doc_title": doc_title, "parsed": parsed,
                  "file_path": str(file_path), "uploaded_at": _now()})
    result = await run_auto_pipeline(
        parsed["chunks"], doc_id, doc_title,
        ExtractionConfigRepository().load(),
        graph_filename=graph_name,
        auto_activate=auto_activate,
        controller=controller,
        require_import_quality=True,
    )
    _persist_auto_document(
        doc_id, doc_title, parsed, file_path, result,
        template_id=template.template_id if template else "",
        template_name=template.name if template else "",
    )
    result.update(
        domain="western",
        template_id=template.template_id if template else AUTO_TEMPLATE_ID,
        domain_detection={"domain": "western", "source": "panel_selection"},
    )
    return result


async def _run_auto_job(
    controller: ImportController, *, file_path: Path, doc_id: str, doc_title: str,
    graph_filename: str, auto_activate: bool, domain: str = "auto", template_id: str = "",
) -> Dict[str, Any]:
    """作业体：西医指南 → 两阶段陈述流水线；中医指南 → 中医专用模板作业体。

    领域由建任务时的显式选择决定（模板领域优先，其次 domain 参数；auto 且无
    模板时默认西医链路，由 TCM 守卫拦截中医内容），不做运行时领域自动识别。
    """
    from .pdf import parse_pdf
    parsed = parse_pdf(file_path, doc_id)
    template = extraction_templates.get(template_id) if template_id else None
    if domain != "auto" and template is not None and domain != str(template.domain or "western"):
        raise ValueError("所选指南领域与抽取模板领域不一致")
    template_domain = str(template.domain or "western") if template else (
        domain if domain in {"western", "tcm"} else "western")
    if template_domain == "tcm":
        return await _run_tcm_template_job(
            controller, parsed=parsed, file_path=file_path, doc_id=doc_id,
            doc_title=doc_title, graph_filename=graph_filename, template=template,
        )
    return await _run_western_statement_job(
        controller, parsed=parsed, file_path=file_path, doc_id=doc_id,
        doc_title=doc_title, graph_filename=graph_filename, template=template,
        auto_activate=auto_activate,
    )


@router.post("/jobs", status_code=201)
async def create_import_job(
    file: UploadFile = File(...),
    mode: str = Form("template-local"),
    template_id: str = Form(""),
    graph_filename: str = Form(""),
    auto_activate: str = Form("false"),
    domain: str = Form("auto"),
    retry_doc_id: str = Form(""),
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
    # 建任务即解析领域并做一致性校验：模板领域与 domain 参数冲突直接 422，
    # 避免任务排队消耗 LLM 后才失败；领域同时登记进留痕（_JOB_DOMAINS）。
    resolved_domain = ""
    if template_id:
        selected = extraction_templates.get(template_id)
        if selected is not None:
            resolved_domain = str(selected.domain or "")
            if domain != "auto" and resolved_domain and resolved_domain != domain:
                raise HTTPException(
                    422,
                    f"所选领域（{'中医' if domain == 'tcm' else '西医'}）与模板「{selected.name}」（{'中医' if resolved_domain == 'tcm' else '西医'}）不一致，请在对应领域面板中选择模板",
                )
    elif domain in {"western", "tcm"}:
        resolved_domain = domain
    content = await file.read()
    if retry_doc_id:
        if not re.fullmatch(r"DOC-[A-Za-z0-9-]+", retry_doc_id):
            raise HTTPException(422, "重试文档标识无效")
        original_path = UPLOAD_DIR_PDF / f"{retry_doc_id}.pdf"
        if not original_path.exists() or original_path.read_bytes() != content:
            raise HTTPException(422, "重试文件与原文档不一致，请重新创建导入任务")
        if any(_job_doc_id(job) == retry_doc_id and job.status not in {FAILED, SUCCEEDED, STOPPED}
               for job in manager.list_jobs()):
            raise HTTPException(409, "此文档已有正在进行的导入任务")
    doc_id = retry_doc_id or f"DOC-{uuid.uuid4().hex[:8]}"
    UPLOAD_DIR_PDF.mkdir(parents=True, exist_ok=True)
    file_path = UPLOAD_DIR_PDF / f"{doc_id}.pdf"
    if not retry_doc_id:
        file_path.write_bytes(content)

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
    _log_job_created(job, doc_id, resolved_domain)
    return {"job_id": job.job_id, "status": job.status, "doc_id": doc_id}


# job_id -> 建任务时的 doc_id：终态前 job.result 为空，pause/resume 留痕需回查
_JOB_DOC_IDS: Dict[str, str] = {}
# job_id -> 建任务时解析出的领域（模板领域优先，其次显式 domain 参数）。
# 曾因留痕不带领域，中医导入在历史里与西医混排无法区分。
_JOB_DOMAINS: Dict[str, str] = {}


def _job_doc_id(job: ImportJob) -> str:
    """留痕 doc_id：先查建任务登记，再兜底 result（auto/template 终态均含 doc_id）。"""
    if job.job_id in _JOB_DOC_IDS:
        return _JOB_DOC_IDS[job.job_id]
    result = job.result if isinstance(job.result, dict) else {}
    return result.get("doc_id") or ""


def _job_domain(job: ImportJob) -> str:
    """留痕领域：成功任务以 result.domain 为准，否则回望建任务登记。"""
    result = job.result if isinstance(job.result, dict) else {}
    return str(result.get("domain") or _JOB_DOMAINS.get(job.job_id, ""))


def _log_job_event(job: ImportJob, status: str, **extra: Any) -> None:
    """导入日志留痕：字段沿用 _log_job_created 的惯例（doc_id/doc_title/status/mode/domain/job_id）。"""
    from .pdf import _append_import_log
    _append_import_log({
        "doc_id": _job_doc_id(job), "doc_title": job.doc_title, "status": status,
        "mode": job.mode.replace("template-", ""), "domain": _job_domain(job),
        "job_id": job.job_id, **extra,
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
    _JOB_DOMAINS.pop(job.job_id, None)


def _log_job_created(job: ImportJob, doc_id: str, domain: str = "") -> None:
    _JOB_DOC_IDS[job.job_id] = doc_id
    if domain:
        _JOB_DOMAINS[job.job_id] = domain
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
