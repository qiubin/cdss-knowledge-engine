"use strict";

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "../..");
const source = fs.readFileSync(path.join(root, "src/api/static/app.js"), "utf8");

// 从 app.js 提取 typeName 映射的字面量键（实体类型中文名表的唯一前端来源）。
const mapMatch = source.match(/const typeName = \{([\s\S]*?)\};/);
assert.ok(mapMatch, "app.js 缺少 typeName 映射");
const frontendKeys = new Set(
  [...mapMatch[1].matchAll(/([a-z_]+)\s*:/g)].map((m) => m[1])
);
assert.ok(frontendKeys.size > 0, "未能解析出 typeName 的键");

function templateTypes() {
  const templates = JSON.parse(
    fs.readFileSync(path.join(root, "data/templates/extraction_templates.json"), "utf8")
  );
  const types = new Set();
  for (const template of templates) {
    for (const item of template.entity_types || []) types.add(item.entity_type);
  }
  return types;
}

function backendKeys() {
  const precheck = fs.readFileSync(path.join(root, "src/review/precheck.py"), "utf8");
  const block = precheck.match(/NODE_TYPE_LABELS = \{([\s\S]*?)\}/);
  assert.ok(block, "precheck.py 缺少 NODE_TYPE_LABELS");
  return new Set([...block[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]));
}

test("typeName 覆盖全部模板实体类型（不再回退显示英文）", () => {
  const missing = [...templateTypes()].filter((t) => !frontendKeys.has(t));
  assert.deepStrictEqual(missing, [], `typeName 缺少类型: ${missing.join(", ")}`);
});

test("typeName 与后端 precheck 中文名映射保持一致（双向）", () => {
  const backend = backendKeys();
  const missingInFrontend = [...backend].filter((t) => !frontendKeys.has(t));
  const missingInBackend = [...frontendKeys].filter((t) => !backend.has(t) && !["concept", "exam_finding"].includes(t));
  assert.deepStrictEqual(
    missingInFrontend, [],
    `typeName 缺少后端已命名的类型: ${missingInFrontend.join(", ")}`
  );
  assert.deepStrictEqual(
    missingInBackend, [],
    `typeName 有而后端 NODE_TYPE_LABELS 没有的类型（需确认是遗留类型还是漂移）: ${missingInBackend.join(", ")}`
  );
});
