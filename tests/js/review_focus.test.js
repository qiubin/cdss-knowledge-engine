"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../src/api/static/app.js"), "utf8");
function context(api) {
  return vm.createContext({ api, esc: (v) => String(v ?? ""),
    typeName: { disease: "疾病", symptom: "症状" },
    table: (headers, rows) => headers.join(" ") + rows.join(""),
    sourceRefsHtml: async () => "原文上下文", relatedItemsHtml: () => "",
    attrTableHtml: () => "属性表", dependentRuleTable: () => "", auditListHtml: () => "",
    relationLabel: () => "症见", miniStatus: (v) => v,
    reviewBadge: (v) => v, badge: String, clinicalIntentLabel: () => "确诊判断",
    normalizeDomainClient: (v) => v, conditionText: () => "条件组合", ruleConceptChips: () => "",
    formatTime: String,
  });
}
for (const [name, end] of [["nodeDetailHtml", "relationDetailHtml"], ["relationDetailHtml", "ruleDetailHtml"], ["ruleDetailHtml", "let reviewDetailToken"]]) {
  test(`${name} explicitly displays review points and missing provenance`, async () => {
    const api = async (url) => {
      if (url.endsWith("/history")) return [];
      if (url.endsWith("/graph-dependencies")) return { issues: ["逻辑组缺少子条件"], semantic_status: "inconsistent", references: [] };
      if (url.includes("/rules/")) return { rule_id: "R1", name: "测试", rule_type: "diagnosis", conditions: {}, conclusion: {}, source: {} };
      return { node: { name: "疾病甲", node_type: "disease", code: "A" }, relation: {}, source: { name: "疾病甲" }, target: { name: "症状乙" } };
    };
    const ctx = context(api);
    const start = source.indexOf(`  async function ${name}(`);
    const finish = source.indexOf(end === "let reviewDetailToken" ? "  let reviewDetailToken" : `  async function ${end}(`, start);
    vm.runInContext(source.slice(start, finish), ctx);
    const html = await ctx[name]({ rule_id: "R1", node_id: "N1", item_id: "REL1", status: "draft" });
    assert.match(html, /出处/);
    assert.match(html, /缺失/);
    if (name === "nodeDetailHtml") { assert.match(html, /名称/); assert.match(html, /疾病甲/); assert.match(html, /属性为空不直接等于实体识别错误/); }
    if (name === "relationDetailHtml") { assert.match(html, /关联对象与方向/); assert.match(html, /医学含义/); assert.match(html, /疾病甲 → 症见 → 症状乙/); assert.match(html, /前往审核此实体/); }
    if (name === "ruleDetailHtml") { assert.match(html, /data-rule-approval-blocked="true"/); assert.match(html, /条件完整性与组合逻辑/); assert.match(html, /编辑补充后再审核/); }
  });
}
test("disease workbench guides entity, relation and scenario review", () => {
  assert.match(source, /疾病审核工作台/);
  assert.match(source, /按场景核对用途和依据/);
  assert.match(source, /批量通过本组关系/);
});
