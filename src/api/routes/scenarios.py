"""Disease-centred, explicitly scoped graph-to-rule review workflow."""
import copy
from typing import Literal, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from src.api.routes.kg import normalize_graph_filename
from src.api.routes.clinical_basis import batch_sync_results
from src.models.rule import Rule
from src.domains.tcm.basis_generation import OBSERVATION_RELATIONS
from src.review.service import ReviewService, SYNDROME_MEMBERSHIP_ROLES
from src.review.clinical_basis import digest, stage_bases, now, check_basis
from src.review.metadata import node_content_hash, relation_content_hash

router = APIRouter(prefix="/api/review/scenarios", tags=["scenario-review"])
service = ReviewService()
SCENES = {"suspected_diagnosis": "疑似诊断", "diagnosis_confirmation": "确诊／分型",
          "treatment_recommendation": "治疗方案", "treatment_selection": "用药决策"}
ROLES = {"condition", "recommendation", "contraindication", "monitoring"}
DISEASE_OBSERVATIONS = {"DISEASE_HAS_SYMPTOM": "symptom", "DISEASE_HAS_SIGN": "sign"}
SYNDROME_SCENE_EVIDENCE = {**OBSERVATION_RELATIONS, "SYNDROME_HAS_PATHOGENESIS": "pathogenesis"}


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
    tcm = graph.get("domain") == "tcm"
    if tcm:
        memberships = {}
        for key, edge in edges:
            if key[0] not in anchors or key[1] not in SYNDROME_MEMBERSHIP_ROLES:
                continue
            syndrome = nodes.get(key[2], {})
            if syndrome.get("node_type") != "syndrome":
                continue
            approved = paths[key[0]]["approved"] and all(
                item.get("review_status") == "active" for item in (edge, syndrome)
            )
            rank = (not approved, list(SYNDROME_MEMBERSHIP_ROLES).index(key[1]), key)
            if key[2] not in memberships or rank < memberships[key[2]]:
                memberships[key[2]] = rank
        for syndrome_id, (blocked, _, key) in memberships.items():
            paths[syndrome_id] = {
                "approved": not blocked, "membership": key,
                "fingerprint": digest([paths[key[0]]["fingerprint"],
                                       relation_content_hash(graph["relations"][key])]),
            }
        edges.extend((key, edge) for key, edge in graph.get("relations", {}).items()
                     if key[0] in memberships and key[1] in SYNDROME_SCENE_EVIDENCE)
    for key, edge in edges:
        source, target = nodes.get(key[0], {}), nodes.get(key[2], {})
        passed = bool(paths.get(key[0], {}).get("approved")) and all(item.get("review_status") == "active" for item in (edge, source, target))
        fingerprint = digest([paths.get(key[0], {}).get("fingerprint"), relation_content_hash(edge), node_content_hash(source), node_content_hash(target)])
        items.append({"id": "|".join(key), "source": source.get("name", key[0]), "target": target.get("name", key[2]),
                      "relation_code": key[1], "node_type": target.get("node_type"), "code": target.get("code"),
                      "approved": passed, "fingerprint": fingerprint, "source_refs": edge.get("source_refs") or [],
                      "target_node": target, "source_node": source})
        clinical_condition = target.get("node_type") in {"symptom", "diagnostic_evidence"}
        items[-1]["suggested_role"] = "condition" if clinical_condition else "recommendation"
        items[-1]["suggested_scenes"] = (["suspected_diagnosis", "diagnosis_confirmation"] if clinical_condition else
            ["treatment_recommendation", "treatment_selection"] if target.get("node_type") in {"medication", "intervention", "treatment_plan"} else
            ["suspected_diagnosis", "diagnosis_confirmation", "treatment_recommendation", "treatment_selection"])
        if tcm:
            membership = paths.get(key[0], {}).get("membership")
            kind = (SYNDROME_SCENE_EVIDENCE.get(key[1]) if membership else
                    DISEASE_OBSERVATIONS.get(key[1]) if key[0] in anchors else None)
            if kind and target.get("node_type") == kind and target.get("code"):
                items[-1].update(observation_type=kind, membership=membership, suggested_role="condition",
                                 suggested_scenes=["suspected_diagnosis"])
                if membership and not paths[key[0]]["approved"]:
                    items[-1]["review_blocker"] = "请先通过所属疾病与该证候的分型或归属关系"
                elif source.get("review_status") != "active" or target.get("review_status") != "active":
                    items[-1]["review_blocker"] = "请先通过这条观察关系两端的实体"
                elif edge.get("review_status") != "active":
                    items[-1]["review_blocker"] = "请先通过这条观察关系"
    return items


@router.get("")
def overview(graph_filename: str, disease_code: str = "", syndrome_code: str = ""):
    filename = normalize_graph_filename(graph_filename)
    graph = service._load_graph(filename)
    owners = {}
    if syndrome_code and graph.get("domain") == "tcm":
        nodes = graph["nodes"]
        for (source_id, code, target_id), edge in graph["relations"].items():
            source, target = nodes.get(source_id, {}), nodes.get(target_id, {})
            if (code not in SYNDROME_MEMBERSHIP_ROLES or source.get("node_type") != "disease"
                    or target.get("node_type") != "syndrome" or target.get("code") != syndrome_code
                    or not source.get("code")):
                continue
            approved = all(item.get("review_status") == "active" for item in (source, edge, target))
            owner = owners.setdefault(source["code"], {"code": source["code"], "name": source["name"], "approved": False})
            owner["approved"] |= approved
        owners = dict(sorted(owners.items(), key=lambda entry: (not entry[1]["approved"], entry[1]["name"], entry[0])))
        if disease_code not in owners:
            disease_code = next(iter(owners), "")
    items = inventory(graph, disease_code) if disease_code else []
    tcm = graph.get("domain") == "tcm"
    linked_syndromes = {
        item["code"] for item in items
        if item["relation_code"] in SYNDROME_MEMBERSHIP_ROLES and item["node_type"] == "syndrome"
        and item["approved"]
    } if tcm else set()
    bases = []
    for basis in graph.get("clinical_bases", []):
        if basis.get("disposition") == "discarded":
            continue
        spec = basis.get("specification") or {}
        anchor_code = spec.get("disease_code")
        scenario_disease = (basis.get("scenario_review") or {}).get("disease_code")
        is_syndrome = tcm and spec.get("anchor_type") == "syndrome"
        if syndrome_code and (not is_syndrome or anchor_code != syndrome_code):
            continue
        if scenario_disease:
            in_scope = scenario_disease == disease_code
        elif is_syndrome:
            in_scope = anchor_code in linked_syndromes or anchor_code == syndrome_code
        else:
            in_scope = anchor_code == disease_code
        if in_scope:
            bases.append(basis)
    sync_results = batch_sync_results(service, filename, bases)
    reviewable = []
    for basis, sync_result in zip(bases, sync_results):
        if sync_result is not None and sync_result.get("dependency_status") != "passed":
            continue
        validation = check_basis(service, filename, graph, basis, sync_result=sync_result)
        if validation["dependencies_approved"]:
            reviewable.append((basis, validation["status"]))
    if syndrome_code:
        items = [item for item in items if item.get("observation_type")
                 and item["source_node"].get("node_type") == "syndrome"
                 and item["source_node"].get("code") == syndrome_code]
    scenes = dict(SCENES)
    if tcm:
        scenes["syndrome_identification"] = "辨证判断"
        scenes["treatment_recommendation"] = "治法与方药"
    return {"items": items, "domain": graph.get("domain"), "disease_code": disease_code,
            "owners": list(owners.values()), "scenes": [{"key": key, "name": name,
        "existing_only": key == "syndrome_identification" or bool(tcm and syndrome_code and key != "suspected_diagnosis"),
        "bases": [{"basis_id": basis["basis_id"], "name": basis["name"], "generated_rule_id": basis.get("generated_rule_id"),
                   "status": status} for basis, status in reviewable
                  if basis.get("specification", {}).get("clinical_intent") == key]} for key, name in scenes.items()
                if not syndrome_code or key in {"suspected_diagnosis", "syndrome_identification", "treatment_recommendation"}]}


class Selection(BaseModel):
    relation_id: str
    role: Literal["condition", "recommendation", "contraindication", "monitoring"]
    weight: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)


class Prepare(BaseModel):
    disease_code: str
    scene: str
    selections: list[Selection]
    operator: Literal["AND", "OR", "COUNT", "CUSTOM", "WEIGHTED"]
    min_count: Optional[int] = Field(default=None, ge=1)
    threshold: Optional[float] = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    source: dict
    scope_summary: str
    reviewer: str
    name: str = ""
    diagnosis_prerequisite: Literal["confirmed", "suspected"] = "confirmed"
    clinical_context: dict = Field(default_factory=dict)
    western_recommendation: Optional[dict] = None


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
    tcm_suspected = graph.get("domain") == "tcm" and request.scene == "suspected_diagnosis"
    anchor = None
    membership = None
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
            if tcm_suspected:
                if not item.get("observation_type"):
                    raise HTTPException(422, "请选择已审的症见、体征、舌象、脉象或病机")
                if anchor and anchor["id"] != item["source_node"]["id"]:
                    raise HTTPException(422, "请为每个证候或病名单独整理疑似诊断依据")
                anchor = item["source_node"]
                membership = item["membership"]
                candidate = {"type": "observation", "observation_type": item["observation_type"],
                             "code": item["code"], "label": item["target"]}
            else:
                candidate = condition_for(item)
            if request.operator == "WEIGHTED":
                if choice.weight is None:
                    raise HTTPException(422, f"请填写「{item['target']}」的正数权重")
                if any({key: value for key, value in existing.items() if key != "weight"} == candidate
                       for existing in conditions):
                    raise HTTPException(422, f"加权评分中「{item['target']}」只能计分一次")
                candidate["weight"] = choice.weight
            if candidate not in conditions:
                conditions.append(candidate)
        elif choice.role == "recommendation":
            recommendations.append({**(item["target_node"].get("attributes") or {}), "code": item["code"], "name": item["target"], "entity_type": item["node_type"]})
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
    condition_tree = {"type": "group", "operator": None if request.operator == "CUSTOM" else request.operator,
                      "children": conditions}
    if request.operator == "COUNT":
        if request.min_count is None or request.min_count > len(conditions):
            raise HTTPException(422, "至少满足的数量须介于 1 与有效判断条件数之间")
        condition_tree = {"type": "count_group", "min_count": request.min_count, "children": conditions}
    elif request.operator == "WEIGHTED":
        if request.threshold is None:
            raise HTTPException(422, "请填写加权评分阈值")
        condition_tree = {"type": "weighted_group", "scoring": "normalized_weight_sum",
                          "threshold": request.threshold, "children": conditions}
    name = request.name.strip() or f"{request.disease_code} · {SCENES[request.scene]}"
    clinical_context = dict(request.clinical_context)
    if tcm_suspected:
        disease = graph["nodes"][membership[0]] if membership else anchor
        clinical_context["suspected_tcm_diagnosis"] = {
            "disease_code": disease["code"],
            "disease_name": disease["name"],
            "membership_relation": membership[1] if membership else None,
        }
        name = request.name.strip() or " · ".join(dict.fromkeys([disease["name"], anchor["name"], "疑似诊断"]))
    identity_input = request.model_dump(exclude={
        **({"min_count": True} if request.operator != "COUNT" else {}),
        **({"threshold": True, "selections": {"__all__": {"weight"}}} if request.operator != "WEIGHTED" else {}),
    })
    identity = digest([identity_input, selected])[:20]
    anchor_code = anchor["code"] if tcm_suspected else request.disease_code
    spec = Rule(rule_id="SCENE-" + identity, name=name, disease_code=anchor_code,
                disease_name=anchor["name"] if tcm_suspected else "",
                domain="tcm" if tcm_suspected else "western",
                anchor_type=anchor["node_type"] if tcm_suspected else "disease",
                rule_type="tcm_pattern" if tcm_suspected else "treatment" if treatment else "diagnosis",
                clinical_intent=request.scene,
                graph_filename=filename, status="draft",
                conditions=condition_tree,
                conclusion={"diagnosis": anchor_code, "message": name,
                            "treatment_items": recommendations}, source=source,
                diagnosis_prerequisite=request.diagnosis_prerequisite if treatment else None,
                clinical_context=clinical_context).model_dump()
    if request.western_recommendation:
        spec["clinical_context"]["western_recommendation"] = request.western_recommendation
    elif treatment:
        contracts = [(item["target_node"].get("attributes") or {}).get("western_recommendation") for item in lookup.values()
                     if item["id"] in {c.relation_id for c in request.selections if c.role == "recommendation"}]
        contracts = [c for c in contracts if c]
        if len(contracts) == 1:
            spec["clinical_context"]["western_recommendation"] = copy.deepcopy(contracts[0])
            spec["clinical_context"]["western_recommendation"]["scope_confirmed"] = False
    stage_bases(graph, [spec], filename)
    basis = next(item for item in graph["clinical_bases"] if item["specification"] == spec)
    if basis.get("scenario_review"):
        return {"basis": {**basis, "validation": check_basis(service, filename, graph, basis)}}
    basis.update(scope_summary=request.scope_summary, scenario_review={"scene": request.scene,
                 **({"disease_code": request.disease_code} if tcm_suspected else {}),
                 "selections": selected, "reviewer": request.reviewer, "reviewed_at": now(),
                 "scope_confirmed": True})
    rules = service._load_rules()
    service._persist(filename, previous, graph, rules, rules, [{"entity_type": "clinical_basis", "entity_id": basis["basis_id"],
                     "graph_filename": filename, "action": "prepare_scenario", "reviewer": request.reviewer}])
    return {"basis": {**basis, "validation": check_basis(service, filename, graph, basis)}}
