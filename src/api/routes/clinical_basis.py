"""Clinical-basis review gate before publishing generated draft rules."""
import copy
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from src.api.routes.kg import normalize_graph_filename
from src.models.rule import Rule
from src.review.service import ReviewService
from src.review.clinical_basis import CHECKS, stage_bases, check_basis, get_basis, require_approved, now
from src.review.condition_completion import build_condition_tree, collect_disease_evidence_conditions, collect_reviewed_treatment_links, complete_rule_conditions

router = APIRouter(prefix="/api/review/bases", tags=["clinical-basis-review"])
service = ReviewService()

class BasisEdit(BaseModel):
    specification: dict
    scope_summary: str = ""

class BasisReview(BaseModel):
    action: str
    reviewer: str
    note: str = ""
    checks: dict = Field(default_factory=dict)


def load(filename):
    filename = normalize_graph_filename(filename)
    return filename, service._load_graph(filename)

@router.get("")
def list_bases(graph_filename: str):
    filename, graph = load(graph_filename)
    return {"items": [{**basis, "validation": check_basis(service, filename, graph, basis)}
                      for basis in graph.get("clinical_bases", [])]}

@router.post("")
def create_basis(request: BasisEdit, graph_filename: str):
    filename, graph = load(graph_filename)
    previous = copy.deepcopy(graph)
    try:
        spec = Rule(**{**request.specification, "status": "draft", "graph_filename": filename}).model_dump()
    except Exception as error:
        raise HTTPException(422, "诊疗条件结构不完整：" + str(error))
    stage_bases(graph, [spec], filename)
    basis = next(g for g in graph["clinical_bases"] if g["specification"] == spec)
    basis["scope_summary"] = request.scope_summary
    rules = service._load_rules()
    service._persist(filename, previous, graph, rules, rules, [])
    return basis

@router.post("/prepare")
def prepare_existing(graph_filename: str):
    filename, graph = load(graph_filename)
    previous = copy.deepcopy(graph)
    # Explicit migration only. Existing executable rules are never changed here.
    candidates = []
    for path in sorted((service.graph_dir.parent / "guide_packages").glob("*.json")):
        package = json.loads(path.read_text(encoding="utf-8"))
        if package.get("graph_filename") == filename:
            candidates.extend(package.get("suggested_rules") or [])
    candidates.extend(rule for rule in service._rules_for_graph(service._load_rules(), filename)
                      if not str(rule.get("rule_id", "")).startswith("RULE-CB-")
                      and not any(str(tag).startswith("clinical-basis:") for tag in rule.get("tags", [])))
    # Prefer existing reviewed candidate specifications over duplicates.
    by_id = {item.get("rule_id"): item for item in candidates if item.get("rule_id")}
    stage_bases(graph, list(by_id.values()), filename)
    rules = service._load_rules()
    service._persist(filename, previous, graph, rules, rules, [])
    return {"count": len(graph.get("clinical_bases", []))}

@router.put("/{basis_id}")
def edit_basis(basis_id: str, request: BasisEdit, graph_filename: str):
    filename, graph = load(graph_filename)
    previous = copy.deepcopy(graph)
    basis = get_basis(graph, basis_id)
    # Validate shape here; clinical completeness is checked at approval.
    try:
        spec = Rule(**{**request.specification, "status": "draft", "graph_filename": filename}).model_dump()
    except Exception as error:
        raise HTTPException(422, "诊疗条件结构不完整：" + str(error))
    if basis.get("generated_rule_id"):
        raise HTTPException(409, "已生成规则的依据不能直接覆盖；请保留原依据并新增修订依据组")
    basis.update(specification=spec, name=spec["name"], scope_summary=request.scope_summary,
                 status="draft", checks={}, updated_at=now())
    rules = service._load_rules()
    service._persist(filename, previous, graph, rules, rules, [])
    return basis


@router.post("/{basis_id}/complete-from-reviewed-graph")
def complete_basis_from_reviewed_graph(basis_id: str, graph_filename: str):
    """Fill only missing conditions from fully reviewed graph evidence chains."""
    filename, graph = load(graph_filename)
    previous = copy.deepcopy(graph)
    basis = get_basis(graph, basis_id)
    if basis.get("generated_rule_id"):
        raise HTTPException(409, "该依据已生成规则，不能覆盖；请新增修订依据")
    if str(basis.get("specification", {}).get("rule_type") or "diagnosis") != "diagnosis":
        links = collect_reviewed_treatment_links(graph, basis["specification"])
        spec = copy.deepcopy(basis["specification"])
        conclusion = spec.setdefault("conclusion", {})
        existing = conclusion.get("treatment_items") or []
        additions = [item for item in links["items"] if not any(
            old.get("code") == item.get("code") and old.get("entity_type") == item.get("entity_type") for old in existing)]
        if additions:
            conclusion["treatment_items"] = [*existing, *additions]
            basis.update(specification=Rule(**spec).model_dump(), status="draft", checks={}, updated_at=now())
        basis["reviewed_treatment_links"] = links
        rules = service._load_rules()
        service._persist(filename, previous, graph, rules, rules, [])
        return {
            "basis": {**basis, "validation": check_basis(service, filename, graph, basis)},
            "changed": bool(additions),
            "used": additions,
            "skipped": [],
            "reason": "已关联审核通过的治疗方案与检查检验。治疗项目已带入；适用人群和禁忌条件请对照当前原文核对。",
            "treatment_links": links,
        }
    result = complete_rule_conditions(
        basis["specification"], graph, approved_only=True
    )
    if result.get("changed"):
        spec = Rule(**{
            **basis["specification"],
            "conditions": result["conditions"],
            "status": "draft",
            "graph_filename": filename,
        }).model_dump()
        basis.update(
            specification=spec,
            status="draft",
            checks={},
            updated_at=now(),
            completion={
                "source": "reviewed_graph",
                "used": result.get("used") or [],
                "skipped": result.get("skipped") or [],
                "completed_at": now(),
            },
        )
        rules = service._load_rules()
        service._persist(filename, previous, graph, rules, rules, [])
    return {
        "basis": {**basis, "validation": check_basis(service, filename, graph, basis)},
        "changed": bool(result.get("changed")),
        "used": result.get("used") or [],
        "skipped": result.get("skipped") or [],
        "reason": result.get("reason") or "",
        "candidates": result.get("candidates") or [],
    }


@router.post("/{basis_id}/revise-from-reviewed-graph")
def revise_basis_from_reviewed_graph(basis_id: str, graph_filename: str):
    """Create a traceable revision using the latest fully reviewed associations."""
    filename, graph = load(graph_filename)
    previous = copy.deepcopy(graph)
    source = get_basis(graph, basis_id)
    spec = copy.deepcopy(source["specification"])
    if str(spec.get("rule_type") or "diagnosis") == "diagnosis":
        collected = collect_disease_evidence_conditions(
            graph, str(spec.get("disease_code") or ""), approved_only=True
        )
    else:
        links = collect_reviewed_treatment_links(graph, spec)
        collected = {"used": links["items"], "skipped": []}
    # A revision preserves the scenario's authored logic. Newly reviewed graph
    # links are candidates, not permission to rebuild a disease-wide AND tree.
    conditions = copy.deepcopy(spec.get("conditions"))
    if not conditions:
        raise HTTPException(409, "当前没有可用于重新生成的已审核关联")
    spec.update(
        conditions=conditions,
        status="draft",
        graph_filename=filename,
        version=int(spec.get("version") or 1) + 1,
    )
    spec = Rule(**spec).model_dump()
    revision_no = 2 + sum(
        item.get("revised_from_basis_id") == basis_id
        for item in graph.get("clinical_bases", [])
    )
    revision_id = f"{basis_id}-R{revision_no}"
    while any(item.get("basis_id") == revision_id for item in graph.get("clinical_bases", [])):
        revision_no += 1
        revision_id = f"{basis_id}-R{revision_no}"
    revised = {
        "basis_id": revision_id,
        "name": f"{source.get('name') or spec['name']}（修订 {revision_no}）",
        "specification": spec,
        "status": "draft",
        "checks": {},
        "scope_summary": source.get("scope_summary") or "",
        "updated_at": now(),
        "revised_from_basis_id": basis_id,
        "supersedes_rule_id": source.get("generated_rule_id"),
        "completion": {
            "source": "reviewed_graph_revision",
            "used": collected.get("used") or [],
            "skipped": collected.get("skipped") or [],
            "completed_at": now(),
        },
    }
    if source.get("scenario_review"):
        revised["scenario_review"] = copy.deepcopy(source["scenario_review"])
    graph.setdefault("clinical_bases", []).append(revised)
    rules = service._load_rules()
    service._persist(filename, previous, graph, rules, rules, [])
    return {
        "basis": {**revised, "validation": check_basis(service, filename, graph, revised)},
        "used": collected.get("used") or [],
        "skipped": collected.get("skipped") or [],
    }

@router.post("/{basis_id}/review")
def review_basis(basis_id: str, request: BasisReview, graph_filename: str):
    filename, graph = load(graph_filename)
    previous = copy.deepcopy(graph)
    basis = get_basis(graph, basis_id)
    if not request.reviewer.strip():
        raise HTTPException(422, "请填写审核人")
    if request.action == "approve":
        check = check_basis(service, filename, graph, basis)
        issues = list(check["issues"])
        if not str(basis.get("scope_summary") or "").strip():
            issues.append("请填写适用范围核对说明")
        if any(request.checks.get(key) is not True for key in CHECKS):
            issues.append("请逐项确认实体、组合逻辑、适用及排除条件、原文依据")
        if issues:
            raise HTTPException(409, "；".join(issues))
        basis.update(status="active", approved_fingerprint=check["fingerprint"], checks=request.checks)
    elif request.action == "reject":
        if not request.note.strip():
            raise HTTPException(422, "退回必须填写原因")
        basis.update(status="rejected", checks={})
    else:
        raise HTTPException(422, "仅支持 approve 或 reject")
    basis.update(reviewer=request.reviewer, note=request.note, updated_at=now())
    rules = service._load_rules()
    updated_rules = copy.deepcopy(rules)
    if request.action == "reject":
        for rule in updated_rules:
            if rule.get("rule_id") == basis.get("generated_rule_id") and rule.get("status") == "active":
                rule["status"] = "blocked"
    service._persist(filename, previous, graph, rules, updated_rules, [{"entity_type": "clinical_basis", "entity_id": basis_id,
        "graph_filename": filename, "action": request.action, "reviewer": request.reviewer, "note": request.note}])
    return basis

@router.post("/{basis_id}/generate")
def generate_rule(basis_id: str, graph_filename: str):
    filename, graph = load(graph_filename)
    previous = copy.deepcopy(graph)
    basis = get_basis(graph, basis_id)
    require_approved(service, filename, graph, basis)
    rules = service._load_rules()
    rule_id = "RULE-" + basis_id
    existing = next((rule for rule in rules if rule.get("rule_id") == rule_id), None)
    if existing:
        if basis.get("generated_rule_id") == rule_id:
            return {"rule_id": rule_id, "created": False, "status": existing.get("status")}
        raise HTTPException(409, "规则编码已存在，禁止覆盖")
    rule = Rule(**{**basis["specification"], "rule_id": rule_id, "status": "draft", "graph_filename": filename}).model_dump()
    rule["tags"] = list(dict.fromkeys([*rule.get("tags", []), "clinical-basis:" + basis_id]))
    if basis.get("supersedes_rule_id"):
        rule["supersedes_rule_id"] = basis["supersedes_rule_id"]
    basis.update(generated_rule_id=rule_id, generated_at=now())
    service._persist(filename, previous, graph, rules, [*rules, rule], [{"entity_type": "clinical_basis", "entity_id": basis_id,
        "graph_filename": filename, "action": "generate_rule", "reviewer": basis.get("reviewer"), "rule_id": rule_id}])
    return {"rule_id": rule_id, "created": True, "status": "draft"}
