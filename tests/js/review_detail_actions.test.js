"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../../src/api/static/app.js"), "utf8");

for (const type of ["graph_node", "graph_relation"]) {
  test(`approve related ${type} outside the filtered queue uses the displayed item once`, async () => {
    const button = { dataset: { detailAction: "approve" }, textContent: "通过", disabled: false };
    const mount = { innerHTML: "", querySelector: () => null,
      querySelectorAll: (selector) => selector === "[data-detail-action]" ? [button] : [] };
    const calls = [];
    const ctx = vm.createContext({
      $: () => mount, state: { reviewItems: [] }, markSelectedRow() {},
      reviewItemKey: () => "related-item", detailHeadHtml: () => "", detailActionsHtml: () => "",
      nodeDetailHtml: async () => "", relationDetailHtml: async () => "",
      quickReview: async (item, action) => calls.push({ item, action }),
    });
    const start = source.indexOf("  let reviewDetailToken");
    const end = source.indexOf("  async function bulkReview", start);
    const code = source.slice(start, end).replace(/  function markSelectedRow\(\) \{[\s\S]*?\n  \}\n/, "");
    vm.runInContext(code, ctx);
    const item = { item_type: type, node_id: "N", item_id: "N|R|M" };
    await ctx.loadReviewDetail(item);
    let stopped = 0;
    await button.onclick({ stopPropagation() { stopped++; } });
    assert.equal(stopped, 1);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].item, item);
    assert.equal(calls[0].action, "approve");
    assert.equal(button.disabled, false);
  });
}

test("disease endpoint review deduplicates pending nodes and does not auto-select or approve rejected nodes", () => {
  const ctx = vm.createContext({ state: {}, esc: String, miniStatus: String, typeName: { symptom: "症状" },
    table: (_, rows) => rows.join("") });
  vm.runInContext(source.slice(source.indexOf("  function diseaseEndpointReviewHtml"), source.indexOf("  function diseaseSectionHtml")), ctx);
  const item = { status: "draft", source_node_id: "D", source_name: "疾病", source_status: "active",
    target_node_id: "T", target_name: "症状", target_node_type: "symptom", target_status: "draft" };
  const html = ctx.diseaseEndpointReviewHtml([item, item, { ...item, target_node_id: "R", target_status: "rejected" }], "G");
  assert.equal(ctx.state.byDiseaseEndpoints.size, 2);
  assert.equal(ctx.state.byDiseaseEndpoints.get("T").node_type, "symptom");
  assert.match(html, /type-chip/);
  assert.match(html, /第 1 步/);
  assert.match(html, /第 2 步/);
  assert.doesNotMatch(html, /checked/);
  assert.match(html, /data-endpoint-select="R" disabled/);
  assert.equal(ctx.state.byDiseaseEndpoints.get("T").graph_filename, "G");
});
