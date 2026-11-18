"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../../src/api/static/app.js"), "utf8");
const backendSource = fs.readFileSync(require("node:path").join(__dirname, "../../src/api/routes/kg_review.py"), "utf8");

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

test("disease relation rows expose AI review alongside manual decisions", () => {
  const ctx = vm.createContext({
    esc: String, relationSemHtml: () => "关系", reviewBadge: () => "待审",
    flagChipsHtml: () => "", relationLabel: () => "症见",
  });
  const start = source.indexOf("  function diseaseRelationRowHtml");
  const end = source.indexOf("  function collectSectionItems", start);
  vm.runInContext(source.slice(start, end), ctx);
  const html = ctx.diseaseRelationRowHtml({ item_id: "D|R|S", status: "draft" });
  assert.match(html, /data-rel-ai=/);
  assert.match(html, /AI 审核/);
  assert.match(html, /data-rel-action="approve"/);
  assert.match(html, /data-rel-action="reject"/);
});

test("relation AI review prompt covers exam, lab, diagnostic evidence and symptom semantics", () => {
  assert.match(backendSource, /DISEASE_HAS_EXAM表示疾病需开展该检查/);
  assert.match(backendSource, /DISEASE_HAS_LAB表示需开展该检验/);
  assert.match(backendSource, /DISEASE_HAS_DIAGNOSTIC_EVIDENCE表示目标结果构成诊断依据/);
  assert.match(backendSource, /DISEASE_HAS_SYMPTOM表示该症状是疾病表现/);
  assert.match(backendSource, /标准ICD疾病实体是受保护主数据/);
  assert.match(backendSource, /拒绝或重新绑定该关系，不要修改标准疾病主数据/);
});

test("relation groups provide batch AI review and use a non-conflicting POST route", () => {
  assert.match(source, /data-section-ai=/);
  assert.match(source, /openBatchRelationAiReview/);
  assert.match(source, /\/api\/kg\/relation-ai-review\?graph_filename=/);
  assert.doesNotMatch(source, /\/api\/kg\/relations\/ai-review\?graph_filename=/);
  assert.match(backendSource, /@router\.post\("\/relation-ai-review"\)/);
});

test("batch and single AI review support stop, in-place retry, reject and one-click correction", () => {
  assert.match(source, /data-ai-direct-reject/);
  assert.match(source, /data-ai-direct-approve/);
  assert.match(source, /data-single-ai-approve/);
  assert.match(source, /按建议通过/);
  assert.match(source, /通过修正后的关系/);
  assert.match(source, /端点未通过/);
  assert.match(source, /按建议拒绝/);
  assert.match(source, /data-ai-refine/);
  assert.match(source, /输入提示词进一步修正/);
  assert.match(source, /data-ai-apply/);
  assert.match(source, /一键应用修正/);
  assert.match(source, /data-ai-rerun-one/);
  assert.match(source, /stopBatchRelationAi/);
  assert.match(source, /stopRelationAiReview/);
  assert.match(source, /data-single-ai-reject/);
  assert.match(source, /data-single-ai-apply/);
  assert.match(source, /result\.assessment === "insufficient_evidence" \? "因证据不足拒绝" : "拒绝此关系"/);
  assert.match(source, /reject\.hidden = !\["reject", "revise", "insufficient_evidence"\]\.includes\(currentResult\.assessment\)/);
  assert.match(source, /因证据不足拒绝/);
  assert.match(source, /\[证据不足\]/);
  assert.doesNotMatch(source, /data-ai-edit-source/);
  assert.doesNotMatch(source, /data-ai-edit-target/);
  assert.match(source, /\/api\/kg\/relation-ai-correction\?graph_filename=/);
  assert.match(backendSource, /suggested_source_name/);
  assert.match(backendSource, /suggested_target_name/);
  assert.match(backendSource, /issues\.area写entity_name/);
});
