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
