"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "../../src/api/static/app.js"), "utf8");

test("clinical basis editor prepares conditions from reviewed graph first", () => {
  assert.match(source, /complete-from-reviewed-graph/);
  assert.match(source, /配置规则条件/);
  assert.match(source, /已从审核通过的关联补入/);
  assert.match(source, /revise-from-reviewed-graph/);
  assert.match(source, /修订规则条件/);
  const helper = source.slice(
    source.indexOf("async function openCompletedBasisEditor"),
    source.indexOf("async function showClinicalBasis"),
  );
  assert.ok(helper.indexOf("complete-from-reviewed-graph") < helper.indexOf("openRuleEditor"));
});

test("clinical basis editor resolves logic in plain Chinese before opening the tree", () => {
  assert.match(source, /这些条件如何构成判断/);
  assert.match(source, /必须全部满足（且）/);
  assert.match(source, /满足任意一项（或）/);
  assert.match(source, /resolveBasisLogic/);
  assert.match(source, /当前没有可生成规则的已审诊断条件/);
  assert.match(source, /不需要编辑条件树/);
});
