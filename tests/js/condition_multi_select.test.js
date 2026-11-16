"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
function core() {
  const ctx = vm.createContext({ window: {} });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../../src/api/static/condition-tree-editor.js"), "utf8"), ctx);
  return ctx.window.CdssConditionTree;
}
test("multi medication selection explicitly produces OR or AND without changing original", () => {
  const ct = core();
  const original = { type: "medication", code: "A", label: "Old" };
  for (const op of ["OR", "AND"]) {
    const selected = ct.combineFieldSelection(original, "code", ["A", "B", "B"], op);
    assert.equal(selected.operator, op);
    assert.deepEqual(Array.from(selected.children, (child) => child.code), ["A", "B"]);
    assert.ok(selected.children.every((child) => child.type === "medication" && child.label === undefined));
    assert.equal(ct.isCompilable(selected), true);
  }
  assert.equal(original.code, "A");
  assert.equal(original.label, "Old");
});
test("symptom multi selection preserves duration and existing parent logic", () => {
  const ct = core();
  const leaf = { type: "symptom", symptom_code: "S1", duration_minutes: 30 };
  const tree = { type: "group", operator: "NOT", children: [leaf] };
  const next = ct.replaceNode(tree, [0], ct.combineFieldSelection(leaf, "symptom_code", ["S1", "S2"], "OR"));
  assert.equal(next.operator, "NOT");
  assert.equal(next.children.length, 1);
  assert.ok(next.children[0].children.every((item) => item.duration_minutes === 30));
});
test("zero selection and missing combination are rejected; one selection stays a leaf", () => {
  const ct = core();
  const leaf = { type: "lab_concept", lab_code: "A", concept_code: "ELEVATED" };
  assert.throws(() => ct.combineFieldSelection(leaf, "lab_code", [], "OR"), /至少选择/);
  assert.throws(() => ct.combineFieldSelection(leaf, "lab_code", ["A", "B"], ""), /全部满足或任一满足/);
  const single = ct.combineFieldSelection(leaf, "lab_code", ["B"], "");
  assert.equal(single.type, "lab_concept");
  assert.equal(single.lab_code, "B");
  assert.equal(single.concept_code, "ELEVATED");
});
test("saved ordinary condition groups can be reopened as a multi selection", () => {
  const ct = core();
  const group = ct.combineFieldSelection({ type: "medication", code: "A" }, "code", ["A", "B"], "OR");
  const reopened = ct.fieldSelectionInfo(ct.jsonToTree(ct.treeToJson(group)));
  assert.equal(reopened.field.key, "code");
  assert.deepEqual(Array.from(reopened.values), ["A", "B"]);
  assert.equal(reopened.operator, "OR");
});
test("conditions with different parameters cannot be silently merged by multi editor", () => {
  const ct = core();
  const group = { type: "group", operator: "AND", children: [
    { type: "symptom", symptom_code: "A", duration_minutes: 10 },
    { type: "symptom", symptom_code: "B", duration_minutes: 30 },
  ] };
  assert.equal(ct.fieldSelectionInfo(group), null);
});
