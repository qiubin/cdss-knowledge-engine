#!/usr/bin/env node
/** Full-flow browser regression: upload PDF, inspect managed pages, run inference. */
const { chromium } = require("playwright");

const BASE_URL = process.env.CDSS_BASE_URL || "http://127.0.0.1:8001";
const TEST_PDF = process.env.CDSS_TEST_PDF;

function fail(message) {
  console.error(message);
  process.exit(1);
}

async function expectVisible(page, selector, timeout = 15000) {
  await page.waitForSelector(selector, { state: "visible", timeout });
}

async function navigate(page, route, title) {
  await page.click(`[data-route="${route}"]`);
  await expectVisible(page, `#pageTitle:text-is("${title}")`);
  await page.waitForTimeout(350);
}

(async () => {
  if (!TEST_PDF) fail("Set CDSS_TEST_PDF to a guideline PDF before running this check.");

  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const pageErrors = [];
  const failedRequests = [];

  page.on("pageerror", async (error) => {
    pageErrors.push(error.stack || error.message);
    await page.screenshot({ path: "artifacts-browser-error.png", fullPage: true }).catch(() => {});
  });
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource")) {
      pageErrors.push(message.text());
    }
  });
  page.on("response", (response) => {
    if (response.status() >= 400 && !new URL(response.url()).pathname.endsWith("/favicon.ico")) {
      failedRequests.push(`${response.status()} ${response.url()}`);
    }
  });

  try {
    await page.goto(BASE_URL, { waitUntil: "networkidle" });
    await expectVisible(page, "#apiStatus:text-is(\"服务已连接\")");
    await page.screenshot({ path: "artifacts-dashboard.png", fullPage: true });

    await navigate(page, "import", "指南导入");
    await navigate(page, "templates", "解析模板");
    await expectVisible(page, "#defaultTemplate");
    await page.selectOption("#defaultMode", "local");
    await page.selectOption("#defaultTemplate", { index: 0 });
    await page.click("#saveExtraction");
    await expectVisible(page, "text=解析配置已保存");
    await page.click("[data-template-structure]");
    await expectVisible(page, "text=结构管理");
    for (const id of ["#addEntityType", "#addEntity", "#addRelation", "#addAttribute"]) {
      await expectVisible(page, id);
    }
    await page.click("#addEntityType");
    await expectVisible(page, "text=新增实体类型");
    await page.click("#editorCancel");
    await page.click("#templateBack");
    await expectVisible(page, "text=模板列表");
    await page.click("#newTemplate");
    await expectVisible(page, "text=新建解析模板");
    await page.click("#editorCancel");
    await page.screenshot({ path: "artifacts-templates.png", fullPage: true });

    await navigate(page, "vocab", "概念词汇");
    await expectVisible(page, "#newConcept");
    await expectVisible(page, "#vocabSearch");
    await expectVisible(page, "text=受控语义集合");
    await page.click("#newConcept");
    await expectVisible(page, "text=新增概念词汇");
    await page.click("#editorCancel");
    await page.click("[data-concept-usage]");
    await expectVisible(page, "text=概念引用：");
    await page.click("#editorCancel");
    await page.screenshot({ path: "artifacts-vocab.png", fullPage: true });

    await navigate(page, "import", "指南导入");
    await expectVisible(page, "#dropzone");
    await expectVisible(page, "#uploadMode");
    await expectVisible(page, "#uploadTemplate");
    await page.setInputFiles("#pdfInput", TEST_PDF);
    await expectVisible(page, ".result-banner.ok", 60000);
    const uploadBanner = await page.textContent(".result-banner.ok");
    if (!/解析成功.*页/.test(uploadBanner || "")) fail(`PDF upload failed: ${uploadBanner}`);

    for (const tab of ["lines", "entities", "relations", "rules"]) {
      await page.click(`[data-import-tab="${tab}"]`);
      await page.waitForTimeout(300);
      await page.screenshot({ path: `artifacts-import-${tab}.png`, fullPage: true });
    }

    await navigate(page, "graph", "知识图谱");
    await expectVisible(page, "text=诊断证据");
    await expectVisible(page, "text=语义节点");
    await expectVisible(page, "text=语义关系");
    await expectVisible(page, "text=诊断逻辑链路");
    await expectVisible(page, "text=检验形成抽象证据");
    await expectVisible(page, "text=检查发现形成抽象证据");
    await page.screenshot({ path: "artifacts-graph.png", fullPage: true });

    await navigate(page, "rules", "规则管理");
    await expectVisible(page, "text=规则列表");
    await expectVisible(page, "text=关联概念");
    if (!(await page.locator("[data-vocab-detail]").count())) {
      fail("Rule management does not show linked concept chips.");
    }
    await page.screenshot({ path: "artifacts-rules.png", fullPage: true });

    await navigate(page, "standards", "标准管理");
    await expectVisible(page, "text=标准列表");
    await page.screenshot({ path: "artifacts-standards.png", fullPage: true });

    await navigate(page, "inference", "病历测试");
    await expectVisible(page, "#casePatientId");
    await expectVisible(page, "#caseChief");
    await expectVisible(page, "text=1. 患者基本信息");
    await expectVisible(page, "text=2. 主诉");
    await expectVisible(page, "text=3. 现病史");
    await expectVisible(page, "text=4. 既往史");
    await expectVisible(page, "text=5. 本次检验、检查数据");
    await page.click("#loadDemo");
    await page.click("#saveCase");
    await expectVisible(page, "text=模拟病历已保存");
    await page.click("#runInference");
    await expectVisible(page, "text=疑似诊断推荐：", 20000);
    const inferenceTitle = await page.textContent("#inferResult h3");
    if (!/疑似诊断推荐：[1-9]\d* 条/.test(inferenceTitle || "")) {
      fail(`Inference did not match active rules: ${inferenceTitle}`);
    }
    await expectVisible(page, "text=标准：>77.0 ng/mL (XM_FIRST v1)");
    await page.screenshot({ path: "artifacts-inference.png", fullPage: true });

    await navigate(page, "dashboard", "概览");
    await page.click("#smokeTestBtn");
    await expectVisible(page, "text=页面自检结果", 30000);
    if (await page.locator("[data-smoke-failure]").count()) fail("Built-in smoke test contains failures.");
    await page.screenshot({ path: "artifacts-smoke.png", fullPage: true });

    if (failedRequests.length) fail(`HTTP errors:\n${failedRequests.join("\n")}`);
    if (pageErrors.length) fail(`Browser errors:\n${pageErrors.join("\n")}`);
    console.log(`PASS uploaded=${uploadBanner.trim()} inference=${inferenceTitle.trim()} smoke=all-pass`);
  } finally {
    await browser.close();
  }
})().catch((error) => fail(error.stack || error.message));
