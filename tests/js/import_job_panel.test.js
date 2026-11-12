// tests/js/import_job_panel.test.js
"use strict";

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(
  path.join(__dirname, "../../src/api/static/app.js"), "utf8"
);
// 提取纯函数：app.js 内以 "function jobControlButtons" / "function jobProgressText" 定义
function extract(name) {
  const match = source.match(new RegExp(`function ${name}\\([^)]*\\)\\s*\\{`));
  return match;
}

test("jobControlButtons 映射各状态按钮", () => {
  assert.ok(extract("jobControlButtons"), "缺少 jobControlButtons");
  const fn = new Function("status", `
    ${source.match(/function jobControlButtons[\s\S]*?\n  }/)[0]}
    return jobControlButtons(status);
  `);
  assert.deepStrictEqual(fn("running"), { pause: true, resume: false, stop: true, start: false });
  assert.deepStrictEqual(fn("pausing"), { pause: false, resume: true, stop: true, start: false });
  assert.deepStrictEqual(fn("paused"), { pause: false, resume: true, stop: true, start: false });
  assert.deepStrictEqual(fn("queued"), { pause: true, resume: false, stop: true, start: false });
  assert.deepStrictEqual(fn("succeeded"), { pause: false, resume: false, stop: false, start: false });
  assert.deepStrictEqual(fn("stopped"), { pause: false, resume: false, stop: false, start: true });
  assert.deepStrictEqual(fn("failed"), { pause: false, resume: false, stop: false, start: true });
});

test("importLogStatusBadge 覆盖 paused/resumed 等全部状态", () => {
  assert.ok(extract("importLogStatusBadge"), "缺少 importLogStatusBadge");
  const fn = new Function("status", `
    ${source.match(/function importLogStatusBadge[\s\S]*?\n  }/)[0]}
    return importLogStatusBadge(status);
  `);
  assert.strictEqual(fn("success"), '<span class="badge active">成功</span>');
  assert.strictEqual(fn("started"), "已开始");
  assert.strictEqual(fn("paused"), '<span class="badge">已暂停</span>');
  assert.strictEqual(fn("resumed"), '<span class="badge">已继续</span>');
  assert.strictEqual(fn("stopped"), '<span class="badge">已停止</span>');
  assert.ok(!fn("paused").includes("danger"), "paused 不应渲染为 danger 红色徽章");
  assert.ok(!fn("resumed").includes("danger"), "resumed 不应渲染为 danger 红色徽章");
  assert.strictEqual(fn("some_unknown"), '<span class="badge danger">失败</span>');
  assert.strictEqual(fn(undefined), '<span class="badge danger">失败</span>');
});

test("jobProgressText 输出阶段与分组进度", () => {
  assert.ok(extract("jobProgressText"), "缺少 jobProgressText");
  const fn = new Function("job", `
    ${source.match(/function jobProgressText[\s\S]*?\n  }/)[0]}
    return jobProgressText(job);
  `);
  assert.match(fn({ status: "running", stage: "大模型抽取（第 3/10 组）", progress: { done: 2, total: 10 } }), /3\/10/);
  assert.match(fn({ status: "paused", stage: "任意", progress: { done: 0, total: 0 } }), /已暂停/);
});
