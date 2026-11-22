"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../src/api/static/app.js"), "utf8");
const pageFunctions = source.slice(source.indexOf("  async function renderGraphVisual()"), source.indexOf("  async function renderGraph(options"));

test("knowledge page shows only approved records without mutating review data", () => {
  const context = vm.createContext({});
  vm.runInContext(source.slice(source.indexOf("  function approvedKnowledgeGraph("), source.indexOf("  async function renderGraph(options")), context);
  const graph = {
    nodes: [{ id: "D", code: "D", node_type: "disease", review_status: "active" }, { id: "L", review_status: "active" }, { id: "P", review_status: "draft" }],
    relations: [{ source_node_id: "D", target_node_id: "L", review_status: "active" }, { source_node_id: "D", target_node_id: "P", review_status: "active" }, { source_node_id: "D", target_node_id: "L", review_status: "stale" }],
    diseases: [{ code: "D" }, { code: "pending" }],
    evidences: [{ id: "E", review_status: "active" }, { id: "pending" }],
    disease_evidence: [{ disease_code: "D", evidence_id: "E", review_status: "active" }, { disease_code: "D", evidence_id: "pending" }],
  };
  const result = context.approvedKnowledgeGraph(graph);
  assert.equal(result.nodes.length, 2);
  assert.equal(result.relations.length, 1);
  assert.equal(result.diseases.length, 1);
  assert.equal(result.evidences.length, 1);
  assert.equal(result.disease_evidence.length, 1);
  assert.equal(graph.nodes.length, 3);
});

function harness(route) {
  const elements = new Map();
  const calls = [];
  const mounts = [];
  const state = { route, clearGraphFilename: "west.json", visualGraphFilename: "west.json", reviewGraphOptions: [] };
  const graphs = [{ graph_filename: "west.json", domain: "western" }, { graph_filename: "tcm.json", domain: "tcm" }];
  const content = {
    html: "",
    set innerHTML(value) {
      this.html = value;
      elements.clear();
      for (const match of value.matchAll(/id="([^"]+)"/g)) {
        elements.set(`#${match[1]}`, {
          value: "west.json", checked: true, hidden: match[1] === "clearExecuteBtn", disabled: false,
          addEventListener(event, handler) { this[event] = handler; },
        });
      }
    },
    get innerHTML() { return this.html; },
  };
  const context = vm.createContext({
    state, content, pageEl: content, markStale: () => {}, $: (id) => elements.get(id),
    esc: (v) => String(v), panel: (title, body) => title + body,
    toast: () => {}, prompt: () => state.clearGraphFilename,
    window: { CdssGraphView: { destroy() {}, mount(element, graph, options) { mounts.push(options); } } },
    async api(url, options) {
      calls.push({ url, payload: options?.json });
      if (url === "/api/kg/graphs") return graphs;
      if (url.startsWith("/api/kg/graph?")) return { profile: { domain: url.includes("tcm") ? "tcm" : "western", anchor_types: ["disease"] } };
      if (url === "/api/kg/clear") return { graph_filename: options.json.graph_filename, node_count: 3, relation_count: 2, rule_ids: ["R-active", "R-draft"], cleared_rules: 2, backups: ["backup"] };
      throw new Error(url);
    },
  });
  vm.runInContext(pageFunctions, context);
  return { context, elements, calls, mounts, state, content };
}

test("clear page previews without deletion and invalidates preview after scope changes", async () => {
  const h = harness("graphClear");
  await h.context.renderGraphClear();
  assert.match(h.content.innerHTML, /已审核/);
  assert.match(h.content.innerHTML, /保留指南 PDF/);
  assert.match(h.content.innerHTML, /tcm.json/);
  await h.elements.get("#clearPreviewBtn").onclick();
  assert.equal(h.calls.at(-1).payload.dry_run, true);
  assert.equal(h.elements.get("#clearExecuteBtn").hidden, false);
  h.elements.get("#clearGraphRules").checked = false;
  h.elements.get("#clearGraphRules").change();
  assert.equal(h.elements.get("#clearExecuteBtn").hidden, true);
  const count = h.calls.length;
  await h.elements.get("#clearExecuteBtn").onclick();
  assert.equal(h.calls.length, count);
  await h.elements.get("#clearPreviewBtn").onclick();
  assert.match(h.elements.get("#clearPreviewBody").innerHTML, /保留关联规则/);
  await h.elements.get("#clearExecuteBtn").onclick();
  assert.equal(h.calls.at(-1).payload.dry_run, false);
  assert.equal(h.calls.at(-1).payload.clear_rules, false);
  assert.equal(h.elements.get("#clearExecuteBtn").hidden, true);
  assert.doesNotMatch(h.elements.get("#clearGraphSelect").innerHTML, /west\.json/);
  assert.equal(h.elements.get("#clearGraphSelect").value, "tcm.json");
});

test("clear page switches target graph without executing a clear", async () => {
  const h = harness("graphClear");
  await h.context.renderGraphClear();
  const select = h.elements.get("#clearGraphSelect");
  select.value = "tcm.json";
  select.change();
  await h.elements.get("#clearPreviewBtn").onclick();
  assert.equal(h.calls.at(-1).payload.graph_filename, "tcm.json");
  assert.equal(h.calls.at(-1).payload.dry_run, true);
});

test("visualization page lists both domains and mounts the selected graph", async () => {
  const h = harness("graphVisual");
  h.state.visualGraphFilename = "tcm.json";
  await h.context.renderGraphVisual();
  assert.match(h.content.innerHTML, /west.json/);
  assert.match(h.content.innerHTML, /tcm.json/);
  assert.equal(h.mounts[0].graphFilename, "tcm.json");
  assert.equal(h.mounts[0].domain, "tcm");
  assert.ok(!h.content.innerHTML.includes("clearExecuteBtn"));
});

test("TCM knowledge tables paginate approved entities and edges and can switch domains", async () => {
  const graph = {
    nodes: [
      ...Array.from({length: 12}, (_, i) => ({id: `S${i}`, node_type: "syndrome", name: `证候${i}`, review_status: "active"})),
      {id: "P", node_type: "symptom", name: "待审症状", review_status: "draft"},
    ],
    relations: [
      {source_node_id: "S0", target_node_id: "S1", relation_code: "EXAMPLE", review_status: "active"},
      {source_node_id: "S0", target_node_id: "P", relation_code: "SYNDROME_HAS_SYMPTOM", review_status: "active"},
    ],
  };
  const tables = new Map();
  const bound = [];
  const routes = [];
  const selectors = new Map();
  const context = vm.createContext({
    state: {route: "tcmGraph"},
    pageEl: {},
    $: (key) => {
      if (!selectors.has(key)) selectors.set(key, {});
      return selectors.get(key);
    },
    api: async (url) => url === "/api/kg/graphs" ? [
      {graph_filename: "west.json", domain: "western"},
      {graph_filename: "tcm.json", domain: "tcm", semantic_nodes: 13},
    ] : graph,
    normalizeDomainClient: (domain) => domain,
    esc: String,
    panel: (title, body) => title + body,
    relationLabel: (relation) => relation.relation_code,
    pagedPanel: (data) => { tables.set(data.key, data); return data.title; },
    bindPagedTable: (key) => bound.push(key),
    resetPagedTables: () => {},
    navigate: (route) => routes.push(route),
  });
  vm.runInContext(source.slice(source.indexOf("  function approvedKnowledgeGraph("), source.indexOf("  async function renderGraph(options")) +
    source.slice(source.indexOf("  async function renderTcmGraph()"), source.indexOf("  function findingLabel(")), context);
  await context.renderTcmGraph();
  assert.match(context.pageEl.innerHTML, /<optgroup label="西医图谱">.*west\.json/);
  assert.match(context.pageEl.innerHTML, /<optgroup label="中医图谱">.*tcm\.json/);
  assert.equal(tables.get("tcmNodes").items.length, 12);
  assert.equal(tables.get("tcmRelations").items.length, 1);
  assert.equal(tables.get("tcmObservations").items.length, 0);
  assert.deepEqual(bound, ["tcmObservations", "tcmFormulas", "tcmNodes", "tcmRelations", "tcmSources"]);
  await selectors.get("#tcmGraphSelector").onchange({target: {value: "west.json"}});
  assert.deepEqual(routes, ["graph"]);
});
