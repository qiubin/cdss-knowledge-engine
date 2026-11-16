"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const app = fs.readFileSync(path.join(__dirname, "../../src/api/static/app.js"), "utf8");
const core = fs.readFileSync(path.join(__dirname, "../../src/api/static/condition-tree-editor.js"), "utf8");
function treeCore() {
  const ctx = vm.createContext({ window: {} });
  vm.runInContext(core, ctx);
  return ctx.window.CdssConditionTree;
}
test("changing a lab condition to symptom removes lab fields and uses symptom inputs", () => {
  const ct = treeCore();
  const original = { type: "group", operator: "AND", children: [
    { type: "lab_concept", lab_code: "CTN", concept_code: "ELEVATED" },
    { type: "symptom", symptom_code: "OTHER" },
  ] };
  const changed = ct.replaceNode(original, [0], ct.newLeaf("symptom"));
  assert.equal(changed.children[0].type, "symptom");
  assert.equal(changed.children[0].lab_code, undefined);
  assert.equal(changed.children[0].concept_code, undefined);
  assert.equal(changed.children[1].symptom_code, "OTHER");
  assert.deepEqual(Array.from(ct.LEAF_FIELDS.symptom, (f) => f.key), ["symptom_code", "duration_minutes"]);
  assert.equal(ct.LEAF_FIELDS.symptom[0].dict, "symptom");
});
test("exam, medication and observation each select their own dictionary", () => {
  const ct = treeCore();
  assert.equal(ct.LEAF_FIELDS.exam_finding[0].dict, "exam");
  assert.equal(ct.LEAF_FIELDS.exam_finding[1].dict, "exam_finding");
  assert.equal(ct.LEAF_FIELDS.medication[0].dict, "medication");
  assert.equal(ct.LEAF_FIELDS.observation.find((f) => f.key === "code").dict, "observation");
});
test("editor choices combine current graph nodes with shared terms and refresh concepts", async () => {
  const calls = [];
  const ctx = vm.createContext({ editorDictPromise: null, state: { vocab: [
    { code: "ELEVATED", category: "lab_concept", is_active: true },
    { code: "RETIRED", category: "lab_concept", is_active: false },
  ] }, toast() {}, async api(url) {
    calls.push(url);
    if (url.startsWith("/api/kg/graph")) return { nodes: { a: { node_type: "symptom", code: "GRAPH_SYM", name: "图谱症状" } }, evidences: [{ finding_code: "GRAPH_FIND", exam_code: "GRAPH_EXAM", description: "图谱检查发现" }] };
    if (url.endsWith("/symptom")) return [{ code: "SHARED_SYM", name: "共享症状" }];
    return [];
  } });
  const start = app.indexOf("  function mergeConditionEntries(");
  const end = app.indexOf("  const CT_LEAF_LABELS", start);
  vm.runInContext(app.slice(start, end), ctx);
  const dict = await ctx.loadEditorDict("CURRENT.json");
  assert.deepEqual(Array.from(dict.symptom, (item) => item.code), ["SHARED_SYM", "GRAPH_SYM"]);
  assert.equal(dict.exam_finding[0].exam_code, "GRAPH_EXAM");
  assert.deepEqual(Array.from(dict.lab_concept, (item) => item.code), ["ELEVATED"]);
  assert.ok(calls.includes("/api/kg/graph?graph_filename=CURRENT.json"));
});
