"use strict";

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(
  path.join(__dirname, "../../src/api/static/app.js"), "utf8"
);

test("规则管理和审核台提供临床用途筛选", () => {
  assert.match(source, /id="ruleClinicalIntentFilter"/);
  assert.match(source, /id="reviewFilterClinicalIntent"/);
  assert.match(source, /clinical_intent=\$\{encodeURIComponent\(filters\.clinicalIntent\)\}/);
});

test("规则编辑器保存临床用途", () => {
  assert.match(source, /name="clinical_intent"/);
  assert.match(source, /clinical_intent: form\.querySelector\("\[name=clinical_intent\]"\)\.value/);
  assert.match(source, /suspected_diagnosis: "疑似诊断"/);
  assert.match(source, /treatment_recommendation: "治疗方案推荐"/);
});
