"""Knowledge graph review API."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.api.routes import rules as rules_route
from src.review.audit import load_audit
from src.review.queue import build_queue
from src.review.service import ReviewService

router = APIRouter(prefix="/api/kg", tags=["kg-review"])
audit_router = APIRouter(prefix="/api/review", tags=["review-audit"])
service = ReviewService()


class ReviewActionRequest(BaseModel):
    action: str
    reviewer: str = "未署名"
    note: str = ""
    reason: str = ""


class RelationReviewRequest(ReviewActionRequest):
    source_node_id: str
    relation_code: str
    target_node_id: str


class BulkReviewRequest(ReviewActionRequest):
    graph_filename: str
    items: List[dict] = Field(default_factory=list)


class BulkReviewItem(BaseModel):
    item_type: str
    graph_filename: str = ""
    node_id: str = ""
    source_node_id: str = ""
    relation_code: str = ""
    target_node_id: str = ""
    rule_id: str = ""


class UnifiedBulkRequest(BaseModel):
    items: List[BulkReviewItem] = Field(default_factory=list)
    action: str
    reviewer: str = "未署名"
    note: str = ""
    reason: str = ""


def _compose_note(note: str, reason: str) -> str:
    """Prefix a structured reject/retire reason code so audit notes stay analyzable."""
    reason = (reason or "").strip()
    note = note or ""
    if reason and f"[{reason}]" not in note:
        return f"[{reason}] {note}".strip()
    return note


@router.get("/review/summary")
async def review_summary(graph_filename: str):
    return service.summary(graph_filename)


@router.get("/review/items")
async def review_items(
    graph_filename: str,
    status: Optional[str] = None,
    item_type: Optional[str] = None,
    node_type: Optional[str] = None,
    source_doc: Optional[str] = None,
    referenced_by_rule: Optional[bool] = None,
    q: Optional[str] = None,
):
    return service.list_items(
        graph_filename,
        status=status,
        item_type=item_type,
        node_type=node_type,
        source_doc=source_doc,
        referenced_by_rule=referenced_by_rule,
        q=q,
    )


@router.get("/nodes/{node_id}")
async def node_detail(node_id: str, graph_filename: str):
    return service.node_detail(graph_filename, node_id)


@router.post("/nodes/{node_id}/review")
async def review_node(node_id: str, request: ReviewActionRequest, graph_filename: str):
    return service.review_node(
        graph_filename,
        node_id,
        request.action,
        request.reviewer,
        _compose_note(request.note, request.reason),
    )


@router.post("/relations/review")
async def review_relation(request: RelationReviewRequest, graph_filename: str):
    return service.review_relation(
        graph_filename,
        request.source_node_id,
        request.relation_code,
        request.target_node_id,
        request.action,
        request.reviewer,
        _compose_note(request.note, request.reason),
    )


@router.post("/review/bulk")
async def review_bulk(request: BulkReviewRequest):
    return service.bulk_review(
        request.graph_filename,
        request.items,
        request.action,
        request.reviewer,
        _compose_note(request.note, request.reason),
    )


@router.get("/nodes/{node_id}/dependents")
async def node_dependents(node_id: str, graph_filename: str):
    return service.node_dependents(graph_filename, node_id)


@router.get("/nodes/{node_id}/impact")
async def node_impact(node_id: str, graph_filename: str):
    return service.impact_for_node(graph_filename, node_id)


@router.get("/relations/impact")
async def relation_impact(
    graph_filename: str,
    source_node_id: str,
    relation_code: str,
    target_node_id: str,
):
    return service.impact_for_relation(
        graph_filename, source_node_id, relation_code, target_node_id
    )


@router.get("/relations/{relation_id}")
async def relation_detail(relation_id: str, graph_filename: str):
    return service.relation_detail(graph_filename, relation_id)


@router.get("/entities/{entity_id}/history")
async def entity_history(
    entity_id: str,
    graph_filename: str,
    entity_type: str = "node",
    limit: int = 50,
):
    from src.review.metadata import read_entity_history

    entity_type = "relation" if entity_type == "relation" else "node"
    return {
        "items": read_entity_history(graph_filename, entity_type, entity_id, limit=limit)
    }


@audit_router.get("/audit")
async def review_audit(
    entity_id: Optional[str] = None,
    graph_filename: Optional[str] = None,
    limit: int = 200,
):
    items = load_audit(
        path=service.audit_path,
        entity_id=entity_id or "",
        graph_filename=graph_filename or "",
        limit=limit,
    )
    return {"items": items}


@audit_router.get("/queue")
async def review_queue(
    domain: Optional[str] = None,
    graph_filename: Optional[str] = None,
    status: Optional[str] = None,
    item_type: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 300,
):
    return build_queue(
        service=service,
        packages=[],
        domain=domain,
        graph_filename=graph_filename,
        status=status,
        item_type=item_type,
        q=q,
        limit=limit,
    )


@audit_router.post("/bulk")
async def review_bulk_unified(request: UnifiedBulkRequest):
    graph_items = [item for item in request.items if item.item_type != "rule"]
    rule_items = [item for item in request.items if item.item_type == "rule"]
    results = []
    grouped = {}
    for item in graph_items:
        grouped.setdefault(item.graph_filename, []).append(item)
    for filename, items in grouped.items():
        payload = [
            {
                "item_type": "relation" if item.item_type == "graph_relation" else "node",
                "node_id": item.node_id,
                "source_node_id": item.source_node_id,
                "relation_code": item.relation_code,
                "target_node_id": item.target_node_id,
            }
            for item in items
        ]
        result = service.bulk_review(
            filename,
            payload,
            request.action,
            request.reviewer,
            _compose_note(request.note, request.reason),
        )
        results.extend(result["results"])
    for item in rule_items:
        try:
            result = rules_route.apply_rule_review(
                item.rule_id,
                request.action,
                request.reviewer,
                _compose_note(request.note, request.reason),
            )
            results.append({"item": item.model_dump(), "ok": True, "result": result})
        except Exception as exc:
            results.append({
                "item": item.model_dump(),
                "ok": False,
                "error": getattr(exc, "detail", str(exc)),
                "status_code": getattr(exc, "status_code", 422),
            })
    return {
        "action": request.action,
        "total": len(request.items),
        "succeeded": sum(1 for item in results if item["ok"]),
        "failed": sum(1 for item in results if not item["ok"]),
        "results": results,
    }
