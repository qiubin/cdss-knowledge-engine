"""Disease-centred, explicitly scoped graph-to-rule review workflow."""
import copy
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from src.api.routes.kg import normalize_graph_filename
from src.models.rule import Rule
from src.review.service import ReviewService
from src.review.clinical_basis import digest, stage_bases, now, check_basis
from src.review.metadata import node_content_hash, relation_content_hash

router = APIRouter(prefix="/api/review/scenarios", tags=["scenario-review"])
service = ReviewService()
SCENES = {"suspected_diagnosis": "疑似诊断", "diagnosis_confirmation": "确诊／分型",
          "treatment_recommendation": "治疗方案", "treatment_selection": "用药决策"}
ROLES = {"condition", "recommendation", "contraindication", "monitoring"}


def inventory(graph, disease_code):
    nodes = graph.get("nodes", {})
    anchors = {key for key, node in nodes.items() if node.get("node_type") == "disease" and node.get("code") == disease_code}
    reachable = set(anchors)
    paths = {key: {"approved": nodes[key].get("review_status") == "active",
                   "fingerprint": node_content_hash(nodes[key])} for key in anchors}
    edges = []
    # Traverse clinical containers, not other diseases or an entire shared graph.
    for _ in range(3):
        for key, edge in graph.get("relations", {}).items():
            if edge.get("source_node_id") not in reachable or key in [item[0] for item in edges]:
                continue
            edges.append((key, edge))
        for key, edge in edges:
            target = nodes.get(key[2], {})
            if target.get("node_type") in {"lab", "exam", "treatment_plan", "order_set", "care_pathway"} and key[2] not in paths:
                upstream = paths.get(key[0], {})
                paths[key[2]] = {"approved": bool(upstream.get("approved")) and edge.get("review_status") == "active" and target.get("review_status") == "active",
                                "fingerprint": digest([upstream.get("fingerprint"), relation_content_hash(edge), node_content_hash(target)])}
                reachable.add(key[2])
    items = []
    for key, edge in edges:
        source, target = nodes.get(key[0], {}), nodes.get(key[2], {})
        passed = bool(paths.get(key[0], {}).get("approved")) and all(item.get("review_status") == "active" for item in (edge, source, target))
        fingerprint = digest([paths.get(key[0], {}).get("fingerprint"), relation_content_hash(edge), node_content_hash(source), node_content_hash(target)])
        items.append({"id": "|".join(key), "source": source.get("name", key[0]), "target": target.get("name", key[2]),
                      "relation_code": key[1], "node_type": target.get("node_type"), "code": target.get("code"),
                      "approved": passed, "fingerprint": fingerprint, "source_refs": edge.get("source_refs") or [],
                      "target_node": target, "source_node": source})
    return items


@router.get("")
def overview(graph_filename: str, disease_code: str):
    filename = normalize_graph_filename(graph_filename)
    graph = service._load_graph(filename)
    items = inventory(graph, disease_code)
    bases = [basis for basis in graph.get("clinical_bases", []) if basis.get("specification", {}).get("disease_code") == disease_code]
    return {"items": items, "scenes": [{"key": key, "name": name,
        "bases": [{"basis_id": basis["basis_id"], "name": basis["name"], "generated_rule_id": basis.get("generated_rule_id"),
                   "status": check_basis(service, filename, graph, basis)["status"]} for basis in bases
                  if basis.get("specification", {}).get("clinical_intent") == key]} for key, name in SCENES.items()]}


class Selection(BaseModel):
    relation_id: str
    role: Literal["condition", "recommendation", "contraindication", "monitoring"]


class Prepare(BaseModel):
    disease_code: str
    scene: str
    selections: list[Selection]
    operator: Literal["AND", "OR"]
    source: dict
    scope_summary: str
    reviewer: str
    name: str = ""
    diagnosis_prerequisite: Literal["confirmed", "suspected"] = "confirmed"


def condition_for(item):
    node, source = item["target_node"], item["source_node"]
    code, kind = node.get("code"), node.get("node_type")
    if kind == "symptom":
        return {"type": "symptom", "symptom_code": code}
    if kind == "diagnostic_evidence":
        if source.get("node_type") == "lab" and code and code.startswith(str(source.get("code")) + ":"):
            return {"type": "lab_concept", "lab_code": source["code"], "concept_code": code.split(":", 1)[1]}
        return {"type": "exam_finding", "finding_code": code,
                **({"exam_code": source["code"]} if source.get("node_type") == "exam" else {})}
    raise HTTPException(422, f"{item['target']} 仅表示项目，缺少结果或判定值，不能直接作为条件；请选择对应诊断发现")


@router.post("/prepare")
def prepare(request: Prepare, graph_filename: str):
    filename = normalize_graph_filename(graph_filename)
    graph = service._load_graph(filename)
    previous = copy.deepcopy(graph)
    if request.scene not in SCENES or not request.selections:
        raise HTTPException(422, "请选择场景和关联依据")
    if not request.scope_summary.strip() or not request.reviewer.strip():
        raise HTTPException(422, "请填写适用范围和核对人")
    source = request.source
    if not source.get("guideline_id") or not str(source.get("quote") or "").strip() or not all(source.get(key) for key in ("page_start", "line_start", "line_end")):
        raise HTTPException(422, "请补全本场景的原文、指南及页行定位")
    if source["line_end"] < source["line_start"]:
        raise HTTPException(422, "原文行号范围倒置")
    lookup = {item["id"]: item for item in inventory(graph, request.disease_code)}
    selected, conditions, recommendations = [], [], []
    seen = set()
    for choice in request.selections:
        if choice.relation_id in seen:
            raise HTTPException(422, "请勿重复选择同一关联")
        seen.add(choice.relation_id)
        item = lookup.get(choice.relation_id)
        if not item or not item["approved"]:
            raise HTTPException(409, "所选关联及两端实体必须已审核通过")
        # No cross-document graph union without explicit source-specific evidence.
        if not any(str(ref).startswith(source["guideline_id"] + "_p") for ref in item["source_refs"]):
            raise HTTPException(409, f"{item['target']} 没有当前指南的关系出处，请选择同一指南的依据")
        selected.append({"relation_id": item["id"], "role": choice.role, "fingerprint": item["fingerprint"]})
        if choice.role == "condition":
            candidate = condition_for(item)
            if candidate not in conditions:
                conditions.append(candidate)
        elif choice.role == "recommendation":
            recommendations.append({"code": item["code"], "name": item["target"], "entity_type": item["node_type"]})
        else:
            raise HTTPException(422, "禁忌／监测关联需分别建立禁忌或监测规则，本场景暂不能把它们直接编入可执行条件")
    treatment = request.scene in {"treatment_recommendation", "treatment_selection"}
    if not treatment and not conditions:
        raise HTTPException(422, "诊断场景至少需要一项明确的判断条件")
    if treatment and not recommendations:
        raise HTTPException(422, "治疗／用药场景请选择至少一项建议项目")
    if treatment and not conditions and request.operator != "AND":
        raise HTTPException(422, "仅使用诊断前提时请选择全部满足；或条件组不能为空")
    if not treatment and recommendations:
        raise HTTPException(422, "诊断场景请选择判断条件；建议项目请在治疗方案场景中整理")
    name = request.name.strip() or f"{request.disease_code} · {SCENES[request.scene]}"
    identity = digest([request.model_dump(), selected])[:20]
    spec = Rule(rule_id="SCENE-" + identity, name=name, disease_code=request.disease_code,
                rule_type="treatment" if treatment else "diagnosis", clinical_intent=request.scene,
                graph_filename=filename, status="draft",
                conditions={"type": "group", "operator": request.operator, "children": conditions},
                conclusion={"diagnosis": request.disease_code, "message": name,
                            "treatment_items": recommendations}, source=source,
                diagnosis_prerequisite=request.diagnosis_prerequisite if treatment else None).model_dump()
    stage_bases(graph, [spec], filename)
    basis = next(item for item in graph["clinical_bases"] if item["specification"] == spec)
    if basis.get("scenario_review"):
        return {"basis": {**basis, "validation": check_basis(service, filename, graph, basis)}}
    basis.update(scope_summary=request.scope_summary, scenario_review={"scene": request.scene,
                 "selections": selected, "reviewer": request.reviewer, "reviewed_at": now(),
                 "scope_confirmed": True})
    rules = service._load_rules()
    service._persist(filename, previous, graph, rules, rules, [{"entity_type": "clinical_basis", "entity_id": basis["basis_id"],
                     "graph_filename": filename, "action": "prepare_scenario", "reviewer": request.reviewer}])
    return {"basis": {**basis, "validation": check_basis(service, filename, graph, basis)}}
