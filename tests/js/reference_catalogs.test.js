"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../src/api/static/app.js"), "utf8");
const fn = source.slice(source.indexOf("  async function openReferenceImporter("), source.indexOf("  async function renderVocab("));
for (const [entityType, catalog] of Object.entries({ exam: "exam", lab: "lab", medication: "medication", intervention: "surgery" })) {
  test(`${entityType} opens its typed catalogue and activates only selected codes`, async () => {
    const calls = [];
    let refreshed = false;
    const controls = new Map();
    const control = (id) => {
      if (!controls.has(id)) controls.set(id, { value: "", innerHTML: "", showModal() {}, close() {} });
      return controls.get(id);
    };
    const context = vm.createContext({
      $: control, esc: String, typeName: { exam: "检查", lab: "检验", medication: "药品", intervention: "干预操作" }, toast() {},
      document: { querySelectorAll() { return [{ value: "test-code" }]; } },
      renderEntityVocab() { refreshed = true; }, setTimeout, clearTimeout,
      async api(url, options) {
        calls.push({ url, payload: options?.json });
        if (url === "/api/vocab/reference/catalogs") return [{ catalog, label: "试用库", standard: "TEST", count: 1, version: "v1", source: "source" }];
        if (url.endsWith("/activate")) return { imported_count: 1, skipped_count: 0 };
        return { items: [{ code: "test-code", name: "测试术语", standard_system: "TEST", standard_code: "code", source: "source", aliases: [] }] };
      },
    });
    vm.runInContext(fn, context);
    await context.openReferenceImporter(entityType);
    assert.ok(calls.some((call) => call.url === `/api/vocab/reference/catalogs/${catalog}?q=&limit=50`));
    assert.match(control("#icd10Results").innerHTML, /TEST/);
    await control("#editorSave").onclick();
    assert.equal(calls.at(-1).url, `/api/vocab/reference/catalogs/${catalog}/activate`);
    assert.deepEqual(Array.from(calls.at(-1).payload.codes), ["test-code"]);
    assert.equal(refreshed, true);
  });
}
