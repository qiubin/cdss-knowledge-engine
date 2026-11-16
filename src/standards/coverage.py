"""Rule and clinical-basis interpretation requirements; no clinical completeness claims."""
import json
from src.models.standard import LabInterpretationStandard
from .validation import standard_issues, effective


def collect_requirements(spec, graph):
    evidences = {item.get("id"): item for item in graph.get("evidences", [])}
    rows = []
    def walk(node):
        if not isinstance(node, dict): return
        kind = node.get("type")
        source = node
        if kind == "evidence":
            source = evidences.get(node.get("evidence_id"), {})
            kind = "lab_concept" if source.get("data_type") == "lab" else source.get("data_type")
        if kind == "lab_concept" and source.get("lab_code"):
            rows.append({"item_type": "lab", "lab_code": source["lab_code"], "concept_code": source.get("concept_code") or "ELEVATED", "parameter": source.get("parameter") or {}})
        if kind == "exam_finding" and source.get("exam_code") and source.get("finding_code"):
            rows.append({"item_type": "exam", "exam_code": source["exam_code"], "finding_code": source["finding_code"], "concept_code": source.get("concept_code") or "PRESENT", "parameter": {**(source.get("parameter") or {}), **({"standard_required": True} if source.get("standard_required") else {})}})
        for field in ("children", "required_children"):
            for child in node.get(field) or []: walk(child)
    walk(spec.get("conditions"))
    return rows


def build_coverage(hospital_id, standards, specs, graphs):
    grouped = {}
    for spec, category in specs:
        filename = spec.get("graph_filename") or "stemi_graph.json"
        for requirement in collect_requirements(spec, graphs.get(filename, {})):
            key = json.dumps(requirement, sort_keys=True)
            row = grouped.setdefault(key, {**requirement, "references": []})
            reference = {"name": spec.get("name") or spec.get("rule_id"), "category": category, "graph_filename": filename}
            if reference not in row["references"]: row["references"].append(reference)
    for row in grouped.values():
        rows = [(index, item) for index, item in enumerate(standards) if item.get("item_type", "lab") == row["item_type"]
                and item.get("concept_code") == row["concept_code"]
                and (item.get("lab_code") == row.get("lab_code") if row["item_type"] == "lab" else item.get("exam_code") == row.get("exam_code") and item.get("finding_code") == row.get("finding_code"))]
        row["standard_indices"] = [index for index, _ in rows]
        inline = row.get("parameter", {}).get("interpretation")
        if inline and row["item_type"] == "exam":
            issues = standard_issues({**inline, **{key: value for key, value in row.items() if key in {"item_type", "exam_code", "finding_code", "concept_code"}}, "hospital_id": hospital_id})
            row.update(status="incomplete" if issues else "guideline", message="；".join(issues) if issues else "使用规则明确的指南参数，不被医院参考范围覆盖")
            continue
        if not rows:
            structured = row["item_type"] == "exam" and row["concept_code"] in {"PRESENT", "ABSENT"} and not row.get("parameter", {}).get("standard_required")
            row.update(status="structured" if structured else "missing", message="可使用已结构化存在性结果；原始数值/分级/报告仍需判定标准" if structured else "未配置医院判定标准")
            continue
        active = [(i, item) for i, item in rows if item.get("status") == "active"]
        valid = [(i, item) for i, item in active if not standard_issues(item) and effective(LabInterpretationStandard(**item))]
        if not active: row.update(status="inactive", message="已有配置，尚未启用")
        elif not valid: row.update(status="incomplete", message="启用配置参数不完整或已失效：" + "；".join(sorted({issue for _, item in active for issue in standard_issues(item)})))
        else:
            limited = all(item.get("context_gender") or item.get("context_age_min") is not None or item.get("context_age_max") is not None or item.get("method") for _, item in valid)
            row.update(status="limited" if limited else "available", message="仅覆盖限定人群/方法，须按患者核对" if limited else "存在有效配置；执行时仍须核对患者范围及单位")
    return {"hospital_id": hospital_id, "items": list(grouped.values())}
