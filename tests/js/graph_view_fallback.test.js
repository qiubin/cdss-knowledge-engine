"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const SOURCE_PATH = path.join(__dirname, "..", "..", "src", "api", "static", "graph-view.js");

function loadGraphView(fetchImpl) {
  const source = fs.readFileSync(SOURCE_PATH, "utf8");
  const context = vm.createContext({
    window: {},
    URLSearchParams,
    fetch: fetchImpl,
    console,
  });
  vm.runInContext(source, context, { filename: SOURCE_PATH });
  return context.window.CdssGraphView;
}

const SAMPLE_GRAPH = {
  graph_filename: "sample.json",
  profile: { domain: "western", anchor_types: ["disease"] },
  nodes: [
    { id: "n-d1", node_type: "disease", name: "甲病", code: "I10" },
    { id: "n-d2", node_type: "disease", name: "乙病", code: "I20" },
    { id: "n-l1", node_type: "lab", name: "检验一", code: "L1" },
    { id: "n-l2", node_type: "lab", name: "检验二", code: "L2" },
    { id: "n-s1", node_type: "symptom", name: "症状一", code: "S1" },
  ],
  relations: [
    { source_node_id: "n-d1", target_node_id: "n-l1", relation_code: "DISEASE_HAS_LAB" },
    { source_node_id: "n-d1", target_node_id: "n-l2", relation_code: "DISEASE_HAS_LAB" },
    { source_node_id: "n-d1", target_node_id: "n-s1", relation_code: "DISEASE_HAS_SYMPTOM" },
    { source_node_id: "n-d2", target_node_id: "n-l2", relation_code: "DISEASE_HAS_LAB" },
  ],
};

test("localPayload picks the most connected anchor as center and expands one level", () => {
  const view = loadGraphView();
  const payload = view.localPayload(SAMPLE_GRAPH, {
    centerCode: "",
    depth: 1,
    limit: 80,
    anchorTypes: ["disease"],
  });

  assert.equal(payload.engine, "json");
  assert.equal(payload.center_code, "I10");
  assert.deepEqual(
    payload.nodes.map((node) => node.code).sort(),
    ["I10", "L1", "L2", "S1"],
  );
  assert.ok(payload.relations.length > 0);
  assert.ok(payload.relations.every((relation) => relation.id));
});

test("localPayload respects depth and node limit", () => {
  const view = loadGraphView();
  const deep = view.localPayload(SAMPLE_GRAPH, {
    centerCode: "I10",
    depth: 2,
    limit: 80,
    anchorTypes: ["disease"],
  });
  assert.equal(deep.nodes.length, 5);

  const shallow = view.localPayload(SAMPLE_GRAPH, {
    centerCode: "I10",
    depth: 1,
    limit: 2,
    anchorTypes: ["disease"],
  });
  assert.equal(shallow.nodes.length, 2);
  assert.equal(shallow.center_code, "I10");
});

test("loadPayload falls back to the local graph when Neo4j is unavailable", async () => {
  const view = loadGraphView(async () => {
    throw new Error("connect ECONNREFUSED 127.0.0.1:7687");
  });

  const payload = await view.loadPayload(
    "/api/kg/neo4j/graph?graph_filename=sample.json",
    SAMPLE_GRAPH,
    { centerCode: "", depth: 1, limit: 80, anchorTypes: ["disease"] },
  );

  assert.equal(payload.engine, "json");
  assert.equal(payload.fallback_reason, "connect ECONNREFUSED 127.0.0.1:7687");
  assert.ok(payload.nodes.length > 0);
});

test("loadPayload uses Neo4j data when the endpoint succeeds", async () => {
  const neo4jPayload = {
    nodes: [{ id: "neo-1", node_type: "disease", name: "服务端疾病", code: "N1" }],
    relations: [],
    engine: "neo4j",
  };
  const view = loadGraphView(async () => ({
    ok: true,
    json: async () => neo4jPayload,
  }));

  const payload = await view.loadPayload(
    "/api/kg/neo4j/graph?graph_filename=sample.json",
    SAMPLE_GRAPH,
    { centerCode: "", depth: 1, limit: 80, anchorTypes: ["disease"] },
  );

  assert.equal(payload.engine, "neo4j");
  assert.deepEqual(payload.nodes.map((node) => node.code), ["N1"]);
  assert.equal(payload.fallback_reason, undefined);
});
