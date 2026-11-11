// src/api/static/condition-tree-editor.js
// Drools-aware visual condition tree core. Pure functions only - no DOM, no
// fetch - so it runs under `node --test` via the vm loader pattern.
//
// Semantics mirror src/engine/drools/compiler.py (walk_leaves / _lhs /
// describe_uncompilable): a tree is compilable when every group is a
// non-empty AND/OR/NOT group and every leaf type is in SUPPORTED_LEAF_TYPES.
// weighted_group / evidence / fact stay editable-only-in-JSON and are shown
// read-only by the UI with a "仅原生引擎" badge.
//
// All CRUD functions are immutable: they take a tree and return a new tree.
// A node is addressed by `path`, an array of child indices ([] = root).
(function () {
  "use strict";

  const GROUP_OPERATORS = ["AND", "OR", "NOT"];
  const READONLY_TYPES = ["evidence", "fact", "weighted_group"];
  const SUPPORTED_LEAF_TYPES = [
    "lab_concept", "exam_finding", "symptom", "vital", "demographic",
    "past_history", "medication", "observation", "attribute",
  ];
  const KNOWN_TYPES = ["group", ...READONLY_TYPES, ...SUPPORTED_LEAF_TYPES];
  const FORBIDDEN_KEYS = ["__proto__", "constructor", "prototype"];

  // Field manifests per editable leaf type; app.js renders widgets from this.
  // widget: dict(app.js supplies dictionary options) | enum | number | text | bool
  const LEAF_FIELDS = {
    lab_concept: [
      { key: "lab_code", label: "检验项目", widget: "dict", dict: "lab", required: true },
      { key: "concept_code", label: "概念", widget: "dict", dict: "lab_concept", required: true },
    ],
    exam_finding: [
      { key: "exam_code", label: "检查项目", widget: "dict", dict: "exam", required: true },
      { key: "finding_code", label: "检查发现", widget: "dict", dict: "exam_finding", required: true },
    ],
    symptom: [
      { key: "symptom_code", label: "症状", widget: "dict", dict: "symptom", required: true },
      { key: "duration_minutes", label: "持续 ≥ 分钟", widget: "number" },
    ],
    vital: [
      { key: "vital_code", label: "体征编码", widget: "text", required: true },
      { key: "operator", label: "比较符", widget: "enum", options: [">", "<", ">=", "<=", "="] },
      { key: "threshold_value", label: "阈值", widget: "number" },
    ],
    demographic: [
      { key: "age_min", label: "最小年龄", widget: "number" },
      { key: "age_max", label: "最大年龄", widget: "number" },
      { key: "gender", label: "性别", widget: "enum", options: ["male", "female"] },
    ],
    past_history: [
      { key: "code", label: "既往史编码", widget: "text", required: true },
    ],
    medication: [
      { key: "code", label: "用药编码", widget: "text", required: true },
    ],
    observation: [
      { key: "observation_type", label: "观察类型", widget: "enum", required: true, options: ["symptom", "tongue", "pulse", "sign", "constitution"] },
      { key: "code", label: "编码", widget: "text", required: true },
      { key: "concept_code", label: "状态", widget: "enum", options: ["PRESENT"], readonly: true },
      { key: "weight", label: "权重", widget: "number" },
      { key: "required", label: "必要", widget: "bool" },
    ],
    attribute: [
      { key: "attribute_code", label: "属性编码", widget: "text", required: true },
      { key: "attribute_value", label: "属性值", widget: "text" },
    ],
  };

  // ---------------------------------------------------------------- sanitize

  function sanitizeNode(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      throw new Error("条件节点必须是对象");
    }
    if (!KNOWN_TYPES.includes(raw.type)) {
      throw new Error(`未知的条件类型: ${raw.type}`);
    }
    const node = { type: raw.type };
    for (const [key, value] of Object.entries(raw)) {
      if (key === "type") continue;
      if (FORBIDDEN_KEYS.includes(key)) {
        throw new Error(`禁止的字段: ${key}`);
      }
      if (value === null || value === undefined) continue; // 数据中大量 null 字段，剥掉
      if (key === "children" || key === "required_children") {
        if (!Array.isArray(value)) throw new Error(`${key} 必须是数组`);
        node[key] = value.map(sanitizeNode);
        continue;
      }
      node[key] = value;
    }
    if ((node.type === "group" || node.type === "weighted_group") && !node.children) {
      node.children = [];
    }
    return node;
  }

  function sanitizeTree(raw) {
    return sanitizeNode(raw);
  }

  // ------------------------------------------------------- compilability

  function compilabilityReason(node, path = "root") {
    if (!node || typeof node !== "object") return `${path}: 条件节点缺失`;
    if (node.type === "group") {
      const children = Array.isArray(node.children) ? node.children : [];
      if (!children.length) return `${path}: group children 为空`;
      const op = String(node.operator || "AND").toUpperCase();
      if (!GROUP_OPERATORS.includes(op)) return `${path}: operator=${node.operator} 不支持`;
      for (let i = 0; i < children.length; i += 1) {
        const reason = compilabilityReason(children[i], `${path}.children[${i}]`);
        if (reason) return reason;
      }
      return null;
    }
    if (!SUPPORTED_LEAF_TYPES.includes(node.type)) {
      return `${path}: type=${node.type} 仅原生引擎可执行`;
    }
    return null;
  }

  function isCompilable(tree) {
    return compilabilityReason(tree) === null;
  }

  function countLeaves(node) {
    if (!node) return 0;
    if (node.type === "group" || node.type === "weighted_group") {
      return (node.children || []).reduce((sum, child) => sum + countLeaves(child), 0);
    }
    return 1;
  }

  // ------------------------------------------------------------- path utils

  const pathKey = (path) => (path || []).join("-");

  const parsePathKey = (key) =>
    String(key || "").split("-").filter((part) => part !== "").map(Number);

  function getNode(tree, path) {
    let node = tree;
    for (const index of path || []) {
      const children = node.children || [];
      if (!children[index]) return null;
      node = children[index];
    }
    return node;
  }

  // Throw when the target or any of its ancestors is read-only.
  function assertMutable(tree, path) {
    let node = tree;
    const chain = [node];
    for (const index of path || []) {
      node = (node.children || [])[index];
      if (!node) throw new Error("路径不存在");
      chain.push(node);
    }
    for (const item of chain) {
      if (READONLY_TYPES.includes(item.type)) {
        throw new Error("仅原生引擎节点为只读，请切换 JSON 模式调整");
      }
    }
  }

  // --------------------------------------------------- immutable tree edits

  function mapAtPath(node, path, mapper) {
    if (!path || !path.length) return mapper(node);
    const index = path[0];
    const children = (node.children || []).map((child, i) =>
      i === index ? mapAtPath(child, path.slice(1), mapper) : child
    );
    return { ...node, children };
  }

  function updateNode(tree, path, patch) {
    assertMutable(tree, path);
    return mapAtPath(tree, path || [], (node) => {
      const next = { ...node };
      for (const [key, value] of Object.entries(patch || {})) {
        if (value === null || value === undefined || value === "") delete next[key];
        else next[key] = value;
      }
      return next;
    });
  }

  function replaceNode(tree, path, node) {
    assertMutable(tree, path);
    const sanitized = sanitizeTree(node);
    return mapAtPath(tree, path || [], () => sanitized);
  }

  function addChild(tree, path, child) {
    assertMutable(tree, path);
    return mapAtPath(tree, path || [], (node) => {
      if (node.type === "group") {
        return { ...node, children: [...(node.children || []), child] };
      }
      // 叶子上加子节点：把叶子包进新的 AND 组，保留原字段
      return { type: "group", operator: "AND", children: [node, child] };
    });
  }

  function addGroup(tree, path) {
    return addChild(tree, path, { type: "group", operator: "AND", children: [] });
  }

  function addSibling(tree, path, sibling) {
    assertMutable(tree, path);
    if (!path || !path.length) {
      // 根上加深兄弟：把整棵树包进新的 AND 组
      return { type: "group", operator: "AND", children: [tree, sibling] };
    }
    const parentPath = path.slice(0, -1);
    const index = path[path.length - 1];
    return mapAtPath(tree, parentPath, (node) => {
      const children = [...(node.children || [])];
      children.splice(index + 1, 0, sibling);
      return { ...node, children };
    });
  }

  // AND/OR 组只剩 1 个子节点时上提折叠（NOT 不折叠 - 语义不同）。
  function foldSingleChildGroups(node) {
    if (node.type !== "group") return node;
    const children = (node.children || []).map(foldSingleChildGroups);
    const op = String(node.operator || "AND").toUpperCase();
    if ((op === "AND" || op === "OR") && children.length === 1) {
      return children[0];
    }
    return { ...node, children };
  }

  function removeNode(tree, path) {
    assertMutable(tree, path);
    if (!path || !path.length) {
      return { type: "group", operator: "AND", children: [] };
    }
    const parentPath = path.slice(0, -1);
    const index = path[path.length - 1];
    const next = mapAtPath(tree, parentPath, (node) => ({
      ...node,
      children: (node.children || []).filter((_, i) => i !== index),
    }));
    return foldSingleChildGroups(next);
  }

  function moveNode(tree, path, delta) {
    assertMutable(tree, path);
    if (!path || !path.length) return tree;
    const parentPath = path.slice(0, -1);
    const index = path[path.length - 1];
    const target = index + Number(delta || 0);
    return mapAtPath(tree, parentPath, (node) => {
      const children = [...(node.children || [])];
      if (target < 0 || target >= children.length) return node;
      [children[index], children[target]] = [children[target], children[index]];
      return { ...node, children };
    });
  }

  // 跨组拖拽：把 srcPath 子树摘除，插入 targetParentPath（必须是可编辑 group）
  // 的第 index 个子位置。index 按拖拽时的原始 children 坐标解释（插到原第
  // index 个孩子之前）；同组内 insertAt > srcIndex 时先减 1 抵消摘除位移。
  // 返回新树；路径不存在 / 根节点 / 移入自身后代 / 只读 / 目标非 group 时
  // 返回 null（拖拽场景用 null 而不是 throw 更好消费）。
  function moveSubtree(tree, srcPath, targetParentPath, index) {
    const src = srcPath || [];
    const target = targetParentPath || [];
    const isPrefix = (prefix, path) =>
      prefix.length <= path.length && prefix.every((v, i) => v === path[i]);
    if (!src.length) return null; // 根不可移动
    if (isPrefix(src, target)) return null; // 不能移入自身或自己的后代
    try {
      assertMutable(tree, src);
      assertMutable(tree, target);
    } catch (_) {
      return null;
    }
    const srcParentPath = src.slice(0, -1);
    const srcParent = getNode(tree, srcParentPath);
    const targetParent = getNode(tree, target);
    if (!srcParent || !targetParent || targetParent.type !== "group") return null;
    const srcIndex = src[src.length - 1];
    const srcChildren = srcParent.children || [];
    if (srcIndex < 0 || srcIndex >= srcChildren.length) return null;
    const moved = srcChildren[srcIndex];
    // 同一个父节点 = 路径完全相等（target=[] 是根，不是 [0] 的父）
    const sameParent = target.length === srcParentPath.length && isPrefix(srcParentPath, target);
    let insertAt = Math.max(0, Math.min(Number(index) || 0, (targetParent.children || []).length));
    if (sameParent && insertAt > srcIndex) insertAt -= 1;
    // 摘除 src 后，target 若位于 src 右侧兄弟（或其子树）内，其路径要左移 1
    const adjustAfterDetach = (path) => {
      const depth = Math.min(path.length, srcParentPath.length);
      for (let i = 0; i < depth; i += 1) {
        if (path[i] !== srcParentPath[i]) return path;
      }
      const level = srcParentPath.length;
      if (path.length > level && path[level] > srcIndex) {
        const next = path.slice();
        next[level] -= 1;
        return next;
      }
      return path;
    };
    const detached = mapAtPath(tree, srcParentPath, (node) => ({
      ...node,
      children: (node.children || []).filter((_, i) => i !== srcIndex),
    }));
    const next = mapAtPath(detached, adjustAfterDetach(target), (node) => {
      const children = [...(node.children || [])];
      children.splice(insertAt, 0, moved);
      return { ...node, children };
    });
    return foldSingleChildGroups(next);
  }

  // ------------------------------------------------------------ new leaves

  function newLeaf(kind) {
    if (!SUPPORTED_LEAF_TYPES.includes(kind)) {
      throw new Error(`不支持的叶子类型: ${kind}`);
    }
    const node = { type: kind };
    if (kind === "observation") {
      node.observation_type = "symptom";
      node.concept_code = "PRESENT";
    }
    if (kind === "vital") node.operator = ">";
    return node;
  }

  // ------------------------------------------------------------- serialize

  function jsonToTree(text) {
    return sanitizeTree(JSON.parse(text));
  }

  function treeToJson(tree) {
    return JSON.stringify(tree, null, 2);
  }

  function describeLeaf(node) {
    switch (node.type) {
      case "lab_concept": {
        const lab = node.lab_code || "?";
        const concept = node.concept_code || "?";
        return `${lab} · ${concept}`;
      }
      case "exam_finding":
        return `${node.exam_code || "?"} · ${node.finding_code || "?"}`;
      case "symptom":
        return node.duration_minutes ? `${node.symptom_code || "?"} ≥${node.duration_minutes}min` : (node.symptom_code || "?");
      case "vital":
        return `${node.vital_code || "?"} ${node.operator || ""}${node.threshold_value ?? ""}`;
      case "demographic": {
        const parts = [];
        if (node.age_min != null || node.age_max != null) {
          parts.push(`${node.age_min ?? "-"}~${node.age_max ?? "-"}岁`);
        }
        if (node.gender) parts.push(node.gender === "male" ? "男" : "女");
        return parts.length ? parts.join(" ") : "人口学";
      }
      case "past_history":
        return `既往史 ${node.code || "?"}`;
      case "medication":
        return `用药 ${node.code || "?"}`;
      case "observation":
        return `${node.observation_type || "?"}:${node.code || "?"}`;
      case "attribute":
        return `属性 ${node.attribute_code || "?"}=${node.attribute_value ?? "?"}`;
      case "evidence":
        return `证据 ${node.evidence_id || "?"}`;
      case "fact":
        return `事实 ${node.fact_type || ""}${node.code ? ":" + node.code : ""}`;
      default:
        return node.label || node.type;
    }
  }

  function describeNode(node) {
    if (!node) return "";
    if (node.label) {
      return String(node.label).replace(/^[（(【\[]+/, "").replace(/[）)】\]]+$/, "");
    }
    if (node.type === "group" || node.type === "weighted_group") {
      const parts = (node.children || []).map(describeNode).filter(Boolean);
      const op = String(node.operator || "AND").toUpperCase();
      const joiner = op === "OR" ? " 或 " : " 且 ";
      const text = parts.join(joiner);
      if (op === "NOT") return `非（${text}）`;
      return parts.length > 1 ? `（${text}）` : text;
    }
    return describeLeaf(node);
  }

  const treeToText = describeNode;

  window.CdssConditionTree = {
    GROUP_OPERATORS,
    READONLY_TYPES,
    SUPPORTED_LEAF_TYPES,
    KNOWN_TYPES,
    LEAF_FIELDS,
    sanitizeTree,
    isCompilable,
    compilabilityReason,
    countLeaves,
    getNode,
    assertMutable,
    pathKey,
    parsePathKey,
    updateNode,
    replaceNode,
    addChild,
    addGroup,
    addSibling,
    removeNode,
    moveNode,
    moveSubtree,
    newLeaf,
    jsonToTree,
    treeToJson,
    treeToText,
    describeNode,
  };
}());
