"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const app = fs.readFileSync(path.join(__dirname, "../../src/api/static/app.js"), "utf8");
const source = app.slice(app.indexOf("  function entityDictionaryPicker("), app.indexOf("  async function openConceptUsage("));
function setup() {
  const elements = new Map();
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, { innerHTML: "", value: "", showModal() {} });
    return elements.get(id);
  };
  const concepts = [{ code: "TEST", name_cn: "测试", name_en: "Test", aliases: ["识别别名"], category: "tcm_custom", evaluator_type: "observation_present", domain: "tcm", is_active: true }];
  const state = { route: "entityVocab", vocab: concepts, vocabSearch: "识别别名", vocabCategory: "" };
  const tcmButton = { dataset: { entityDictionary: "tcm_disease" } };
  const content = {
    innerHTML: "",
    querySelectorAll: (selector) => selector === "[data-entity-dictionary]" ? [tcmButton] : [],
  };
  const navigation = [];
  const context = vm.createContext({ state, content, pageEl: content, $, esc: String, typeName: { concept: "抽象概念", lab: "检验" },
    normalizeDomainClient: (v) => v, panel: (title, body) => title + body,
    table: (headers, rows) => headers.join(" ") + rows.join(""), loadBase: async () => {},
    navigate: (route) => navigation.push(route), renderEntityVocab() {}, openConceptUsage() {},
  });
  vm.runInContext(source, context);
  return { context, state, content, elements, navigation, concepts, tcmButton, $ };
}
test("unified entity concept view searches aliases and retains dictionary selector", async () => {
  const h = setup();
  await h.context.renderVocab([{ entity_type: "concept", entry_count: 1 }, { entity_type: "lab", entry_count: 2 }]);
  assert.match(h.content.innerHTML, /抽象概念词典/);
  assert.match(h.content.innerHTML, /识别别名/);
  assert.match(h.content.innerHTML, /id="entityVocabSelector"/);
  assert.match(h.content.innerHTML, /TEST/);
});
test("old concept route forwards to unified entity dictionary", async () => {
  const h = setup();
  h.state.route = "vocab";
  await h.context.renderVocab();
  assert.equal(h.state.entityVocabType, "concept");
  assert.deepEqual(h.navigation, ["entityVocab"]);
});
test("Chinese medicine and ICD-10 dictionaries have visible direct controls", async () => {
  const h = setup();
  await h.context.renderVocab([
    { entity_type: "concept", entry_count: 1 },
    { entity_type: "disease", label: "西医疾病（ICD-10）", entry_count: 40 },
    { entity_type: "tcm_disease", label: "中医疾病", entry_count: 3 },
  ]);
  assert.match(h.content.innerHTML, /<button[^>]+data-entity-dictionary="tcm_disease"[^>]*>\s+中医疾病（3）/);
  assert.match(h.content.innerHTML, /data-entity-dictionary="disease"/);
  await h.tcmButton.onclick();
  assert.equal(h.state.entityVocabType, "tcm_disease");
});
test("concept editor keeps custom category, evaluator, aliases and domain", () => {
  const h = setup();
  h.context.openConceptEditor(h.concepts[0]);
  const html = h.$("#editorBody").innerHTML;
  assert.match(html, /name="aliases"/);
  assert.match(html, /识别别名/);
  assert.match(html, /value="tcm_custom" selected/);
  assert.match(html, /value="observation_present" selected/);
  assert.match(html, /value="tcm" selected/);
});
