"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "../../src/api/static/app.js"), "utf8");

test("clinical basis editor prepares conditions from reviewed graph first", () => {
  assert.match(source, /complete-from-reviewed-graph/);
  assert.match(source, /按已审图谱补全并编辑/);
  assert.match(source, /已从审核通过的关联补入/);
  assert.match(source, /revise-from-reviewed-graph/);
  assert.match(source, /更新并重新生成待审规则/);
  const helper = source.slice(
    source.indexOf("async function openCompletedBasisEditor"),
    source.indexOf("async function showClinicalBasis"),
  );
  assert.ok(helper.indexOf("complete-from-reviewed-graph") < helper.indexOf("openRuleEditor"));
});
