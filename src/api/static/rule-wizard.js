// src/api/static/rule-wizard.js
"use strict";
// Dictionary-driven rule wizard core: rows -> native condition tree.
// Pure functions, no DOM, so node --test can exercise them (see
// tests/js/rule_wizard.test.js).  app.js renders the dialog around these.
(function () {
  const CONDITION_KINDS = [
    { value: "lab_concept", label: "检验概念" },
    { value: "symptom", label: "症状" },
    { value: "exam_finding", label: "检查发现" },
    { value: "observation", label: "四诊观察" },
  ];

  function rowToNode(row) {
    if (!row || !row.kind) return null;
    if (row.kind === "lab_concept") {
      const concepts = (row.concept_codes || []).filter(Boolean);
      if (!row.lab_code || !concepts.length) return null;
      const leaves = concepts.map((code) => ({
        type: "lab_concept",
        lab_code: row.lab_code,
        concept_code: code,
      }));
      return leaves.length === 1 ? leaves[0] : { type: "group", operator: "OR", children: leaves };
    }
    if (row.kind === "symptom") {
      return row.symptom_code ? { type: "symptom", symptom_code: row.symptom_code } : null;
    }
    if (row.kind === "exam_finding") {
      return row.exam_code && row.finding_code
        ? { type: "exam_finding", exam_code: row.exam_code, finding_code: row.finding_code }
        : null;
    }
    if (row.kind === "observation") {
      return row.code
        ? {
            type: "observation",
            observation_type: row.observation_type || "symptom",
            code: row.code,
            concept_code: "PRESENT",
          }
        : null;
    }
    return null;
  }

  function buildConditionTree(rows) {
    const children = (rows || []).map(rowToNode).filter(Boolean);
    if (!children.length) return { type: "group", operator: "AND", children: [] };
    if (children.length === 1) return children[0];
    return { type: "group", operator: "AND", children };
  }

  function describeNode(node) {
    if (!node || typeof node !== "object") return "";
    if (node.type === "group") {
      const parts = (node.children || []).map(describeNode).filter(Boolean);
      const joiner = node.operator === "OR" ? " OR " : " AND ";
      const text = parts.join(joiner);
      return node.operator === "NOT" ? `NOT (${text})` : text;
    }
    if (node.type === "lab_concept") return `${node.lab_code} ${node.concept_code}`;
    if (node.type === "symptom") return `症状 ${node.symptom_code}`;
    if (node.type === "exam_finding") return `${node.exam_code}·${node.finding_code}`;
    if (node.type === "observation") return `观察 ${node.observation_type}:${node.code}`;
    return node.type || "";
  }

  function describeTree(tree) {
    return describeNode(tree);
  }

  window.CdssRuleWizard = { CONDITION_KINDS, buildConditionTree, rowToNode, describeTree };
})();
