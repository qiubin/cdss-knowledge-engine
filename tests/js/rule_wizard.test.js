// tests/js/rule_wizard.test.js
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const SOURCE_PATH = path.join(__dirname, "..", "..", "src", "api", "static", "rule-wizard.js");

function loadWizard() {
  const source = fs.readFileSync(SOURCE_PATH, "utf8");
  const context = vm.createContext({ window: {}, console });
  vm.runInContext(source, context, { filename: SOURCE_PATH });
  return context.window.CdssRuleWizard;
}

// Objects returned from the vm context have another realm's prototype, which
// breaks deepStrictEqual's prototype check; plain-JSON roundtrip normalizes.
const plain = (value) => JSON.parse(JSON.stringify(value));

test("multi rows AND, multi concepts OR", () => {
  const wizard = loadWizard();
  const tree = wizard.buildConditionTree([
    { kind: "lab_concept", lab_code: "CTN", concept_codes: ["ELEVATED", "ABOVE_ULN_1"] },
    { kind: "symptom", symptom_code: "SYM_PAIN" },
  ]);
  assert.equal(tree.type, "group");
  assert.equal(tree.operator, "AND");
  const [labGroup, symptom] = tree.children;
  assert.equal(labGroup.type, "group");
  assert.equal(labGroup.operator, "OR");
  assert.equal(labGroup.children.length, 2);
  assert.equal(symptom.type, "symptom");
  assert.equal(symptom.symptom_code, "SYM_PAIN");
});

test("single concept stays a leaf", () => {
  const wizard = loadWizard();
  const tree = wizard.buildConditionTree([
    { kind: "lab_concept", lab_code: "MYO", concept_codes: ["ELEVATED"] },
  ]);
  assert.equal(tree.type, "lab_concept");
  assert.equal(tree.concept_code, "ELEVATED");
});

test("invalid rows dropped, empty rows produce empty AND group", () => {
  const wizard = loadWizard();
  assert.deepEqual(
    plain(wizard.buildConditionTree([
      { kind: "lab_concept", lab_code: "", concept_codes: [] },
      { kind: "symptom", symptom_code: "" },
    ])),
    { type: "group", operator: "AND", children: [] },
  );
});

test("observation row carries type and PRESENT", () => {
  const wizard = loadWizard();
  const tree = wizard.buildConditionTree([
    { kind: "observation", observation_type: "tongue", code: "T_RED" },
  ]);
  assert.deepEqual(plain(tree), {
    type: "observation", observation_type: "tongue", code: "T_RED", concept_code: "PRESENT",
  });
});

test("exam_finding row", () => {
  const wizard = loadWizard();
  const tree = wizard.buildConditionTree([
    { kind: "exam_finding", exam_code: "ECG", finding_code: "STE" },
  ]);
  assert.deepEqual(plain(tree), { type: "exam_finding", exam_code: "ECG", finding_code: "STE" });
});

test("describeTree renders readable text", () => {
  const wizard = loadWizard();
  const text = wizard.describeTree(wizard.buildConditionTree([
    { kind: "lab_concept", lab_code: "CTN", concept_codes: ["ELEVATED", "ABOVE_ULN_1"] },
    { kind: "symptom", symptom_code: "SYM_PAIN" },
  ]));
  assert.ok(text.includes("AND"));
  assert.ok(text.includes("OR"));
  assert.ok(text.includes("CTN"));
});
