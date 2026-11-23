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

test("groupImportLogs 旧留痕按 doc_id 归并，删除标识可直接传给后端", () => {
  assert.ok(extract("groupImportLogs"), "缺少 groupImportLogs");
  const fn = new Function("entries", `
    ${source.match(/function groupImportLogs[\s\S]*?\n  }/)[0]}
    return groupImportLogs(entries);
  `);
  const attempts = fn([
    { job_id: "JOB-A", doc_id: "DOC-A1", status: "started", ts: "2026-11-23T09:00:00" },
    { job_id: "JOB-A", doc_id: "DOC-A1", status: "success", ts: "2026-11-23T09:01:00" },
    // 旧留痕：无 job_id，且各事件写入时间（ts）不同
    { doc_id: "DOC-L1", status: "failed", ts: "2026-11-01T08:00:00" },
    { doc_id: "DOC-L1", status: "success", ts: "2026-11-01T08:03:00" },
  ]);
  assert.strictEqual(attempts.length, 2, "同一次尝试的多条事件应归并为一行");
  const jobRow = attempts.find((a) => a.job_id === "JOB-A");
  const legacyRow = attempts.find((a) => a.job_id === "DOC-L1");
  assert.ok(jobRow && jobRow.doc_id === "DOC-A1", "任务流记录按 job_id 归并");
  assert.ok(legacyRow, "无 job_id 的旧留痕应回退为 DOC- 号作为删除标识（此前 doc_id-ts 组合键会被后端拒绝）");
  assert.strictEqual(legacyRow.display.status, "failed", "失败状态优先展示");
});

test("导入面板按中西医彻底分开，各自独立模板与上传区", () => {
  // 两个独立面板：西医指南导入 / 中医指南导入（曾因混在一个面板+自动识别，
  // 指定西医模板仍生成中医图谱；历史里中医导入被记成西医无法区分）
  assert.ok(source.includes("西医指南导入"), "缺少西医指南导入面板");
  assert.ok(source.includes("中医指南导入"), "缺少中医指南导入面板");
  assert.ok(!source.includes('<option value="auto">自动识别</option>'), "不应再提供“自动识别”选项");
  assert.ok(!source.includes('id="autoDomain"'), "不应再有领域自动识别下拉");
  // 每个面板有独立的模板与图谱下拉（后缀 West / Tcm），互不混读
  assert.ok(source.includes('id="uploadTemplate${cfg.suffix}"'), "面板模板下拉应按领域后缀生成");
  assert.ok(source.includes('id="uploadGraph${cfg.suffix}"'), "面板图谱下拉应按领域后缀生成");
  // 西医默认 UNIVERSAL（两阶段流水线），中医默认 TCM_V3
  assert.ok(source.includes('defaultTemplate: "GENERIC_GUIDELINE_V1"'), "西医面板默认通用模板（西医统一走陈述流水线）");
  assert.ok(source.includes('defaultTemplate: "TCM_GUIDELINE_V3"'), "中医面板默认中医专用模板 V3");
  // 选文件时读取本面板的模板，领域由模板决定
  const prep = source.match(/function prepareAutoUpload[\s\S]*?\n  }/);
  assert.ok(prep, "缺少 prepareAutoUpload");
  assert.ok(prep[0].includes("#uploadTemplate${cfg.suffix}"), "选文件时必须读取所在面板的模板下拉");
  assert.ok(prep[0].includes("请先在"), "未选模板时应明确提示所在面板");
  assert.ok(prep[0].includes("templateId: template.template_id"), "待导入必须携带所选模板");
  assert.ok(prep[0].includes("normalizeDomainClient(template.domain)"), "领域由所选模板决定");
  // 导入历史展示领域列，中医/西医导入可区分
  assert.ok(source.includes('"时间", "文档", "领域", "方式"'), "导入历史应有领域列");
  assert.ok(source.includes('attempt.domain === "tcm" ? "中医"'), "历史领域列应区分中医");
});
