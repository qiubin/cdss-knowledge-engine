"use strict";

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(
  path.join(__dirname, "../../src/api/static/app.js"), "utf8"
);
const match = source.match(/function reviewQueueItemType[\s\S]*?\n  }/);
assert.ok(match, "app.js 缺少 reviewQueueItemType 纯函数");
const reviewQueueItemType = new Function("tab", "filters", `
  ${match[0]}
  return reviewQueueItemType(tab, filters);
`);

test("已通过页签可按三类资产请求服务端队列", () => {
  assert.strictEqual(reviewQueueItemType("active", {}), "");
  assert.strictEqual(reviewQueueItemType("active", { itemType: "graph_node" }), "graph_node");
  assert.strictEqual(reviewQueueItemType("active", { itemType: "graph_relation" }), "graph_relation");
  assert.strictEqual(reviewQueueItemType("active", { itemType: "rule" }), "rule");
});

test("已通过页签渲染并绑定资产类型筛选器", () => {
  assert.match(source, /id="reviewFilterItemType"/);
  assert.match(source, /value="graph_node"[^>]*>实体节点/);
  assert.match(source, /value="graph_relation"[^>]*>图谱关系/);
  assert.match(source, /value="rule"[^>]*>规则/);
  assert.match(source, /\$\("#reviewFilterItemType"\)\?\.addEventListener\("change"/);
});
