"""Structural rule review checks, separate from clinical correctness."""

def condition_structure_issues(rule):
    issues = []
    def walk(node, root=False):
        if not isinstance(node, dict) or not node.get("type"):
            issues.append("条件结构缺失或无效")
            return
        if node["type"] not in {"group", "weighted_group"}:
            return
        children = node.get("children")
        implicit = (root and rule.get("rule_type") == "treatment" and node["type"] == "group"
                    and node.get("operator") in (None, "AND") and children == [])
        if implicit:
            return  # Existing western treatment stage requires a prior diagnosis.
        if not isinstance(children, list) or not children:
            issues.append("逻辑组缺少子条件，请补充后再审核")
            return
        if node["type"] == "group":
            if node.get("operator") not in {"AND", "OR", "NOT"}:
                issues.append("还差一步：请选择这些条件是全部满足还是任意一项")
            if node.get("operator") == "NOT" and len(children) != 1:
                issues.append("非条件组必须且只能包含一个子条件")
        elif node.get("threshold") is None:
            issues.append("加权条件组缺少评分阈值")
        for child in children:
            walk(child)
        for child in node.get("required_children") or []:
            walk(child)
    walk(rule.get("conditions"), root=True)
    return list(dict.fromkeys(issues))
