"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../../src/api/static/app.js"), "utf8");
const start = source.indexOf('    $("#basisApprove").onclick = async () => {');
const end = source.indexOf('    run("#basisReject"', start);

for (const blocked of [false, true]) {
  test(`basis approval ${blocked ? "failure blocks generation" : "generates once and opens rule review"}`, async () => {
    const button = { textContent: "", disabled: false };
    const calls = [];
    const state = {};
    const ctx = vm.createContext({
      $: () => button, state, endpoint: "/basis/CB", query: "?graph_filename=G", filename: "G",
      review: async () => { calls.push("review"); if (blocked) throw new Error("conditions incomplete"); },
      api: async (url) => { calls.push(url); return { rule_id: "RULE-CB" }; },
      toast() {}, markStale() {}, renderReviewCenter: async () => calls.push("render"),
    });
    vm.runInContext(source.slice(start, end), ctx);
    await button.onclick();
    if (blocked) {
      assert.deepEqual(calls, ["review"]);
      assert.equal(button.disabled, false);
    } else {
      assert.deepEqual(calls, ["review", "/basis/CB/generate?graph_filename=G", "render"]);
      assert.equal(state.reviewTab, "rules");
      assert.equal(state.reviewQuery, "RULE-CB");
      assert.equal(state.reviewFilters.graph, "G");
      assert.equal(state.reviewFilters.nodeType, "");
    }
  });
}

test("saving a basis returns to the saved revision instead of an older disease basis", async () => {
  const start = source.indexOf('          const savedBasis = await options.onSave(payload);');
  const end = source.indexOf('        if (rule) await api', start);
  assert.ok(start > 0);
  const opened = [];
  const current = {basis_id: 'CB-R19', specification: {graph_filename: 'G'}};
  const ctx = vm.createContext({
    options: {onSave: async () => current}, payload: {},
    dialog: {close() {}}, toast() {}, renderClinicalBases: async () => {},
    api: async () => ({items: [{basis_id: 'CB-OLD'}, current]}),
    showClinicalBasis: async (basis) => opened.push(basis.basis_id), encodeURIComponent,
  });
  const block = source.slice(start, end).replace(/\n        }\s*$/, '');
  await vm.runInContext(`(async () => { ${block} })()`, ctx);
  assert.deepEqual(opened, ['CB-R19']);
});
