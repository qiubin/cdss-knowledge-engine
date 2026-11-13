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

// —— 全站统一守卫：类型标签只允许出现在 typeName（或声明过的领域专名）里 ——

function extractLabeledMap(file, pattern) {
  const text = fs.readFileSync(path.join(root, file), "utf8");
  const map = {};
  for (const m of text.matchAll(pattern)) map[m[1]] = m[2];
  return map;
}

// 中医图谱保留领域专名（有意的差异化命名，不是漂移）
const DOMAIN_FLAVOR = {
  disease: "中医疾病",
  acupoint: "腧穴",
  western_disease: "西医疾病映射",
};

function assertLabelsAligned(file, pattern, source) {
  const map = extractLabeledMap(file, pattern);
  const drifted = Object.entries(map)
    .filter(([key, label]) => key in frontendKeysSet && label !== frontendKeysSet[key])
    .filter(([key, label]) => DOMAIN_FLAVOR[key] === undefined || DOMAIN_FLAVOR[key] !== label);
  assert.deepStrictEqual(
    drifted, [],
    `${file} 的 ${source} 标签与 typeName 不一致: ${JSON.stringify(drifted)}（领域专名请登记进 DOMAIN_FLAVOR）`
  );
  return map;
}

const frontendKeysSet = Object.fromEntries(
  [...mapMatch[1].matchAll(/([a-z_]+)\s*:\s*"([^"]+)"/g)].map((m) => [m[1], m[2]])
);

test("graph-view 图例标签与 typeName 一致", () => {
  const drifted = Object.entries(extractLabeledMap(
    "src/api/static/graph-view.js", /([a-z_]+): \{ label: "([^"]+)"/g
  )).filter(([key, label]) => frontendKeysSet[key] !== undefined && frontendKeysSet[key] !== label);
  assert.deepStrictEqual(drifted, [], `graph-view.js TYPE_META 标签漂移: ${JSON.stringify(drifted)}`);
});

test("domain profile 类型标签与 typeName 一致（中医领域专名豁免）", () => {
  assertLabelsAligned(
    "src/domains/western/profile.py", /"([a-z_]+)": \{"label": "([^"]+)"/g, "NODE_META"
  );
  assertLabelsAligned(
    "src/domains/tcm/profile.py", /"([a-z_]+)": \{"label": "([^"]+)"/g, "NODE_META"
  );
});

test("模板实体类型标签与 typeName 一致（中医领域专名豁免）", () => {
  const templates = JSON.parse(
    fs.readFileSync(path.join(root, "data/templates/extraction_templates.json"), "utf8")
  );
  const drifted = [];
  for (const template of templates) {
    for (const item of template.entity_types || []) {
      const canonical = frontendKeysSet[item.entity_type];
      if (canonical === undefined) continue;
      const allowed = new Set([canonical, DOMAIN_FLAVOR[item.entity_type]].filter(Boolean));
      if (!allowed.has(item.label)) {
        drifted.push(`${template.template_id}/${item.entity_type}: ${item.label}（应为 ${[...allowed].join(" 或 ")}）`);
      }
    }
  }
  assert.deepStrictEqual(drifted, [], `模板实体类型标签漂移: ${drifted.join("; ")}`);
});

test("关系显示名不含漂移旧词或英文类型前缀", () => {
  const banned = /药物|医嘱套餐|诊断证据|治疗措施|医嘱条目|诊疗路径/;
  const englishPrefix = /(^|[^A-Za-z_])(lab|exam|pathology|biomarker|molecular_test|formula|herb)形成/;
  const offenders = [];
  const templates = JSON.parse(
    fs.readFileSync(path.join(root, "data/templates/extraction_templates.json"), "utf8")
  );
  for (const template of templates) {
    for (const relation of template.relations || []) {
      if (banned.test(relation.name) || englishPrefix.test(relation.name)) {
        offenders.push(`${template.template_id}/${relation.relation_code}: ${relation.name}`);
      }
    }
  }
  const kgText = fs.readFileSync(path.join(root, "src/api/routes/kg.py"), "utf8");
  const defaults = kgText.match(/DEFAULT_RELATION_NAMES = \{([\s\S]*?)\}/);
  assert.ok(defaults, "kg.py 缺少 DEFAULT_RELATION_NAMES");
  for (const m of defaults[1].matchAll(/"([A-Z_]+)": "([^"]+)"/g)) {
    if (banned.test(m[2]) || englishPrefix.test(m[2])) offenders.push(`kg.py/${m[1]}: ${m[2]}`);
  }
  assert.deepStrictEqual(offenders, [], `关系显示名漂移: ${offenders.join("; ")}`);
});
