// tests/js/source_ref_context.test.js
"use strict";

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(
  path.join(__dirname, "../../src/api/static/app.js"), "utf8"
);
assert.ok(
  source.match(/function parseSourceRef[\s\S]*?\n  }/),
  "app.js 缺少两空格缩进的 parseSourceRef 纯函数"
);
const parseSourceRef = new Function("ref", `
  ${source.match(/function parseSourceRef[\s\S]*?\n  }/)[0]}
  return parseSourceRef(ref);
`);

test("parseSourceRef 解析标准溯源引用", () => {
  assert.deepStrictEqual(parseSourceRef("DOC-d51b97dd_p1_l1-30"), {
    docId: "DOC-d51b97dd", page: 1, start: 1, end: 30,
  });
  assert.deepStrictEqual(parseSourceRef("DOC-abc123_p12_l43-59"), {
    docId: "DOC-abc123", page: 12, start: 43, end: 59,
  });
});

test("parseSourceRef 修复 end 位数截断的脏引用", () => {
  // 生成器把 l100-120 截成了 l100-20：end < start 时按 start 前缀补全
  assert.strictEqual(parseSourceRef("DOC-d51b97dd_p3_l100-20").end, 120);
  assert.strictEqual(parseSourceRef("DOC-x_p2_l31-9").end, 39);
});

test("parseSourceRef 非法输入返回 null", () => {
  assert.strictEqual(parseSourceRef(""), null);
  assert.strictEqual(parseSourceRef("not-a-ref"), null);
  assert.strictEqual(parseSourceRef(undefined), null);
});
