(function () {
  "use strict";

  const TYPE_META = {
    disease: { label: "疾病", color: "#f6ad55", stroke: "#dd6b20", shape: "diamond", size: 30 },
    lab: { label: "检验", color: "#63b3ed", stroke: "#3182ce", shape: "dot", size: 20 },
    exam: { label: "检查", color: "#b794f4", stroke: "#805ad5", shape: "dot", size: 20 },
    symptom: { label: "症状", color: "#f687b3", stroke: "#d53f8c", shape: "dot", size: 20 },
    diagnostic_evidence: { label: "诊断依据", color: "#4fd1c5", stroke: "#319795", shape: "dot", size: 18 },
    treatment_plan: { label: "治疗方案", color: "#ecc94b", stroke: "#d69e2e", shape: "diamond", size: 26 },
    intervention: { label: "干预操作", color: "#68d391", stroke: "#38a169", shape: "dot", size: 20 },
    medication: { label: "药品", color: "#9ae6b4", stroke: "#48bb78", shape: "dot", size: 18 },
    contraindication: { label: "禁忌", color: "#fc8181", stroke: "#e53e3e", shape: "dot", size: 18 },
    monitoring_requirement: { label: "监测要求", color: "#b794f4", stroke: "#805ad5", shape: "dot", size: 18 }
  };

  const DIAGNOSIS_TYPES = new Set(["disease", "lab", "exam", "symptom", "diagnostic_evidence"]);
  const TREATMENT_TYPES = new Set(["treatment_plan", "intervention", "medication", "contraindication", "monitoring_requirement"]);

  let active = null;
  let activeMeta = {};
  let activeAnchorTypes = new Set(["disease"]);

  function destroy() {
    if (active) {
      try { active.destroy(); } catch (_) {}
      active = null;
    }
  }

  function meta(type) {
    return activeMeta[type] || TYPE_META[type] || { label: type || "未知", color: "#cbd5e0", stroke: "#718096", shape: "dot", size: 18 };
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function displayName(node) {
    return node.name || node.code || node.concept_code || node.id;
  }

  function displayCode(node) {
    return node.code || node.concept_code || "";
  }

  function asArray(value) {
    return Array.isArray(value) ? value : Object.values(value || {});
  }

  // Build a subgraph from the JSON graph already loaded by the page.  This is
  // the zero-dependency fallback used when Neo4j is not running; its traversal
  // mirrors the server's depth/limit neighbourhood query.
  function localPayload(graph, options = {}) {
    const profile = graph.profile || {};
    const anchorTypes = new Set(
      options.anchorTypes || profile.anchor_types || ["disease"]
    );
    const depth = Math.min(Math.max(Number(options.depth) || 2, 1), 3);
    const limit = Math.max(Number(options.limit) || 80, 1);
    const centerCode = String(options.centerCode || "");
    const allNodes = asArray(graph.nodes).filter(node => node && node.id);
    const nodeIndex = new Map(allNodes.map(node => [node.id, node]));
    const allRelations = asArray(graph.relations)
      .filter(relation => relation
        && nodeIndex.has(relation.source_node_id)
        && nodeIndex.has(relation.target_node_id))
      .map((relation, index) => ({
        ...relation,
        id: relation.id || `json-edge-${index}`,
      }));

    const degrees = new Map();
    allRelations.forEach(relation => {
      degrees.set(relation.source_node_id, (degrees.get(relation.source_node_id) || 0) + 1);
      degrees.set(relation.target_node_id, (degrees.get(relation.target_node_id) || 0) + 1);
    });

    const anchors = allNodes.filter(node => anchorTypes.has(node.node_type));
    const autoCenter = anchors.slice().sort((a, b) => {
      const delta = (degrees.get(b.id) || 0) - (degrees.get(a.id) || 0);
      if (delta) return delta;
      return String(a.code || a.id).localeCompare(String(b.code || b.id), "zh-Hans-CN");
    })[0] || null;
    const requestedCenter = centerCode
      ? allNodes.find(node => node.code === centerCode) || null
      : null;
    const center = requestedCenter || autoCenter;

    const adjacency = new Map();
    allRelations.forEach(relation => {
      if (!adjacency.has(relation.source_node_id)) adjacency.set(relation.source_node_id, []);
      if (!adjacency.has(relation.target_node_id)) adjacency.set(relation.target_node_id, []);
      adjacency.get(relation.source_node_id).push(relation.target_node_id);
      adjacency.get(relation.target_node_id).push(relation.source_node_id);
    });

    let selectedIds = new Set();
    if (center) {
      const queue = [[center.id, 0]];
      selectedIds.add(center.id);
      for (let cursor = 0; cursor < queue.length && selectedIds.size < limit; cursor += 1) {
        const [nodeId, level] = queue[cursor];
        if (level >= depth) continue;
        const neighbors = (adjacency.get(nodeId) || []).slice().sort();
        for (const nextId of neighbors) {
          if (selectedIds.size >= limit) break;
          if (!selectedIds.has(nextId)) {
            selectedIds.add(nextId);
            queue.push([nextId, level + 1]);
          }
        }
      }
    } else {
      selectedIds = new Set(allNodes.slice(0, limit).map(node => node.id));
    }

    const nodes = allNodes
      .filter(node => selectedIds.has(node.id))
      .sort((a, b) => String(a.name || a.code || a.id)
        .localeCompare(String(b.name || b.code || b.id), "zh-Hans-CN"));
    const relations = allRelations.filter(relation => (
      selectedIds.has(relation.source_node_id) && selectedIds.has(relation.target_node_id)
    ));

    return {
      graph_filename: options.graphFilename || graph.graph_filename || "",
      center_code: center ? (center.code || "") : "",
      depth,
      limit,
      nodes,
      relations,
      total_nodes: allNodes.length,
      total_relations: allRelations.length,
      engine: "json",
    };
  }

  // Prefer Neo4j (server-side neighbourhood + sync); fall back to the local
  // JSON graph when the endpoint is down, empty or unreachable.
  async function loadPayload(url, localGraph, options = {}, fetchImpl) {
    const requester = fetchImpl || (typeof fetch === "function" ? fetch : null);
    if (requester) {
      try {
        const response = await requester(url);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.detail || "Neo4j 图谱读取失败");
        if (!Array.isArray(payload.nodes) || !payload.nodes.length) {
          throw new Error("Neo4j 图谱为空");
        }
        return { ...payload, engine: "neo4j" };
      } catch (error) {
        return {
          ...localPayload(localGraph, options),
          fallback_reason: (error && error.message) || "Neo4j 不可用",
        };
      }
    }
    return {
      ...localPayload(localGraph, options),
      fallback_reason: "fetch 不可用",
    };
  }

  function create(container, graph, options = {}) {
    if (!container) return null;
    destroy();

    const graphFilename = options.graphFilename || graph.graph_filename || "KG-GENERIC_GUIDELINE_V1.json";
    const localGraph = graph;
    const profile = options.profile || graph.profile || {};
    activeMeta = profile.node_meta || options.nodeMeta || {};
    const domain = profile.domain || options.domain || "western";
    activeAnchorTypes = new Set(options.anchorTypes || profile.anchor_types || ["disease"]);
    const sourceNodes = asArray(graph.nodes).filter(Boolean);
    const anchorNodes = sourceNodes
      .filter(node => activeAnchorTypes.has(node.node_type))
      .sort((a, b) => (a.name || "").localeCompare(b.name || "", "zh-Hans-CN"));
    const allTypes = [...new Set(sourceNodes.map(node => node.node_type).filter(Boolean))];
    const tcmTreatmentTypes = new Set([
      "treatment_method", "formula", "herb", "contraindication", "meridian", "acupoint"
    ]);
    const diagnosisTypes = options.diagnosisTypes
      ? new Set(options.diagnosisTypes)
      : domain === "tcm"
        ? new Set(allTypes.filter(type => !tcmTreatmentTypes.has(type)))
        : DIAGNOSIS_TYPES;
    const treatmentTypes = options.treatmentTypes
      ? new Set(options.treatmentTypes)
      : domain === "tcm"
        ? tcmTreatmentTypes
        : TREATMENT_TYPES;

    const state = {
      graphFilename,
      centerCode: options.defaultCenterCode || options.defaultDiseaseCode || "",
      depth: 2,
      maxNodes: 80,
      enabledTypes: new Set(allTypes),
      showNodeLabels: true,
      showEdgeLabels: true,
      loadedNodes: [],
      loadedEdges: [],
      selectedId: "",
      network: null,
      loading: false,
      viewMode: "all",
      engine: "neo4j",
      fallbackReason: "",
      neo4jDown: false
    };

    container.innerHTML = `
      <div class="kgviz neo4j-kgviz">
        <div class="kgviz-toolbar">
          <label>${domain === "tcm" ? "中心证候" : "中心疾病"}
            <select id="kgDiseaseSelect">
              <option value="">自动选择连接最多的${domain === "tcm" ? "证候" : "疾病"}</option>
              ${anchorNodes.map(node => `
                <option value="${escapeHtml(node.code)}" ${node.code === state.centerCode ? "selected" : ""}>
                  ${escapeHtml(node.name)}${displayCode(node) ? ` (${escapeHtml(displayCode(node))})` : ""}
                </option>`).join("")}
            </select>
          </label>
          <label>展开层级
            <select id="kgDepthSelect">
              ${[1, 2, 3].map(value => `<option value="${value}" ${value === state.depth ? "selected" : ""}>${value} 层</option>`).join("")}
            </select>
          </label>
          <label>节点上限
            <select id="kgLimitSelect">
              ${[40, 80, 120, 200].map(value => `<option value="${value}" ${value === state.maxNodes ? "selected" : ""}>${value}</option>`).join("")}
            </select>
          </label>
          <div class="kgviz-viewmodes">
            ${["all", "diagnosis", "treatment"].map(mode => `
              <label class="kgviz-viewmode"><input type="radio" name="kgViewMode" value="${mode}" ${mode === state.viewMode ? "checked" : ""}> ${mode === "all" ? "全部" : mode === "diagnosis" ? (domain === "tcm" ? "辨证" : "诊断") : "治疗"}</label>`).join("")}
          </div>
          <div class="kgviz-typebox">
            ${allTypes.map(type => `
              <label class="kgviz-type"><input type="checkbox" data-kg-type="${escapeHtml(type)}" checked> <span style="--dot:${meta(type).color}"></span>${escapeHtml(meta(type).label)}</label>`).join("")}
          </div>
          <div class="kgviz-actions">
            <label class="switch"><input type="checkbox" id="kgNodeLabels" checked>节点名</label>
            <label class="switch"><input type="checkbox" id="kgEdgeLabels" checked>关系名</label>
            <button id="kgRelayout" type="button">重新布局</button>
            <button id="kgFit" type="button">适配视图</button>
            <button id="kgReload" type="button">刷新</button>
            <button id="kgBrowser" type="button">Neo4j Browser</button>
          </div>
        </div>
        <div class="kgviz-stage">
          <div id="kgCanvas" class="kg-canvas"></div>
          <div id="kgLoadStatus" class="kg-load-status">正在读取 Neo4j 图谱…</div>
          <div id="kgInfo" class="kgviz-info empty-info">
            <p>拖动节点可调整布局；滚轮缩放；点击节点或关系查看详情。</p>
          </div>
          <div class="kgviz-legend" id="kgLegend"></div>
        </div>
        <div class="kgviz-footer">
          <span id="kgCounts">加载中…</span>
          <span id="kgEngine">数据引擎：Neo4j</span>
        </div>
      </div>
    `;

    const canvas = container.querySelector("#kgCanvas");
    const statusEl = container.querySelector("#kgLoadStatus");
    const countsEl = container.querySelector("#kgCounts");
    const infoEl = container.querySelector("#kgInfo");
    const legendEl = container.querySelector("#kgLegend");

    legendEl.innerHTML = allTypes.map(type => `
      <span><i style="background:${meta(type).color}"></i>${escapeHtml(meta(type).label)}</span>
    `).join("");

    function setStatus(message, tone = "") {
      statusEl.className = `kg-load-status${tone ? ` ${tone}` : ""}`;
      statusEl.textContent = message;
    }

    function engineLabel() {
      return state.engine === "json" ? "本地 JSON" : "Neo4j";
    }

    function updateEngineChrome() {
      const engineEl = container.querySelector("#kgEngine");
      if (engineEl) engineEl.textContent = `数据引擎：${engineLabel()}`;
      const browserBtn = container.querySelector("#kgBrowser");
      if (browserBtn) browserBtn.style.display = state.engine === "neo4j" ? "" : "none";
    }

    function setInfo(nodeOrEdge, kind) {
      if (!nodeOrEdge) {
        infoEl.className = "kgviz-info empty-info";
        infoEl.innerHTML = "<p>拖动节点可调整布局；滚轮缩放；点击节点或关系查看详情。</p>";
        return;
      }
      if (kind === "edge") {
        const source = state.loadedNodes.find(node => node.id === nodeOrEdge.source_node_id);
        const target = state.loadedNodes.find(node => node.id === nodeOrEdge.target_node_id);
        infoEl.className = "kgviz-info";
        infoEl.innerHTML = `
          <strong>关系</strong>
          <div class="muted">${escapeHtml(nodeOrEdge.relation_name || nodeOrEdge.relation_code)}</div>
          <div class="source"><b>${escapeHtml(displayName(source))}</b> → <b>${escapeHtml(displayName(target))}</b></div>
          ${(nodeOrEdge.source_refs || []).length ? `<div class="source">出处：${escapeHtml((nodeOrEdge.source_refs || []).join("；"))}</div>` : ""}
        `;
        return;
      }
      const refs = nodeOrEdge.source_refs || [];
      infoEl.className = "kgviz-info";
      infoEl.innerHTML = `
        <span class="type-dot" style="background:${meta(nodeOrEdge.node_type).color}"></span>
        <strong>${escapeHtml(displayName(nodeOrEdge))}</strong>
        <div class="muted">${escapeHtml(meta(nodeOrEdge.node_type).label)}${displayCode(nodeOrEdge) ? ` · ${escapeHtml(displayCode(nodeOrEdge))}` : ""}</div>
        ${nodeOrEdge.review_status ? `<span class="status">${escapeHtml(nodeOrEdge.review_status)}</span>` : ""}
        ${refs.length ? `<div class="source"><b>指南出处</b><br>${escapeHtml(refs.join("；"))}</div>` : ""}
      `;
    }

    function buildData() {
      const nodeMap = new Map(state.loadedNodes.map(node => [node.id, node]));
      const visibleNodes = state.loadedNodes.filter(node => state.enabledTypes.has(node.node_type));
      const visibleIds = new Set(visibleNodes.map(node => node.id));
      const edges = state.loadedEdges
        .filter(edge => visibleIds.has(edge.source_node_id) && visibleIds.has(edge.target_node_id))
        .map((edge, index) => ({
          id: edge.id || `edge-${index}`,
          from: edge.source_node_id,
          to: edge.target_node_id,
          label: state.showEdgeLabels ? (edge.relation_name || edge.relation_code) : "",
          title: `${edge.relation_name || edge.relation_code}\n${(edge.source_refs || []).join("\n")}`,
          arrows: { to: { enabled: true, scaleFactor: 0.7 } },
          smooth: { enabled: true, type: "continuous", roundness: 0.18 },
          color: { color: "#64748b", hover: "#fbbf24", highlight: "#fbbf24" },
          selectionWidth: 2,
          font: {
            size: 10,
            color: "#cbd5e0",
            face: "system-ui, -apple-system, sans-serif",
            background: "rgba(15,23,42,.82)",
            strokeWidth: 4,
            strokeColor: "#0f172a"
          },
          raw: edge
        }));

      const nodes = visibleNodes
        .filter(node => visibleIds.has(node.id))
        .map(node => {
          const item = meta(node.node_type);
          const nodeLabel = node.display_name || displayName(node);
          return {
            id: node.id,
            label: state.showNodeLabels ? nodeLabel : "",
            title: `${meta(node.node_type).label}：${nodeLabel}${!node.display_name && displayCode(node) ? `\n${displayCode(node)}` : ""}`,
            shape: item.shape,
            size: item.size,
            borderWidth: activeAnchorTypes.has(node.node_type) ? 3 : 2,
            color: {
              background: item.color,
              border: item.stroke,
              highlight: { background: item.color, border: "#fbbf24" },
              hover: { background: item.color, border: "#fbbf24" }
            },
            font: {
              size: activeAnchorTypes.has(node.node_type) ? 14 : 11,
              color: "#e2e8f0",
              face: "system-ui, -apple-system, sans-serif",
              strokeWidth: 4,
              strokeColor: "#0f172a",
              vadjust: activeAnchorTypes.has(node.node_type) ? -20 : -16
            },
            raw: node
          };
        });
      return { nodes, edges, nodeMap };
    }

    function applyViewMode() {
      if (state.viewMode === "diagnosis") {
        state.enabledTypes = new Set(diagnosisTypes);
      } else if (state.viewMode === "treatment") {
        state.enabledTypes = new Set(treatmentTypes);
      } else {
        state.enabledTypes = new Set(allTypes);
      }
      container.querySelectorAll("[data-kg-type]").forEach(input => {
        input.checked = state.enabledTypes.has(input.dataset.kgType);
      });
      render();
    }

    function applyFilter() {
      render();
    }

    function render() {
      if (!state.network) return;
      const data = buildData();
      state.network.setData(data);
      countsEl.textContent = `${engineLabel()}：${data.nodes.length} 个节点 / ${data.edges.length} 条关系`;
      updateEngineChrome();
      container.__cdssGraph = {
        network: state.network,
        nodeCount: data.nodes.length,
        edgeCount: data.edges.length,
        lastLoadedAt: state.lastLoadedAt || "",
        reload: load
      };
      setInfo(null);
    }

    function highlightNode(nodeId) {
      state.selectedId = nodeId;
      if (!state.network) return;
      const selected = [];
      state.network.getBody().edges.forEach(edge => {
        const raw = edge.options.raw;
        if (raw && (raw.source_node_id === nodeId || raw.target_node_id === nodeId)) {
          selected.push(edge.id);
        }
      });
      state.network.setSelection({ nodes: nodeId ? [nodeId] : [], edges: selected }, { highlightEdges: true });
      setInfo(state.loadedNodes.find(node => node.id === nodeId), nodeId ? "node" : null);
    }

    function applyPayload(payload) {
      state.loadedNodes = payload.nodes || [];
      state.loadedEdges = payload.relations || [];
      state.engine = payload.engine || "neo4j";
      state.fallbackReason = payload.fallback_reason || "";
      state.lastLoadedAt = new Date().toISOString();
      state.enabledTypes = new Set(allTypes);
      container.querySelectorAll("[data-kg-type]").forEach(input => { input.checked = true; });
      render();
      if (payload.browser_url) {
        container.querySelector("#kgBrowser").dataset.url = payload.browser_url;
      }
      if (!state.loadedNodes.length) {
        setStatus(`${engineLabel()} 图谱为空；请先导入指南或选择其他图谱。`, "error");
        infoEl.className = "kgviz-info empty-info";
        infoEl.innerHTML = `
          <p><strong>${escapeHtml(engineLabel())} 图谱为空。</strong></p>
          <p>请先在“指南导入”生成知识包，或切换其他图谱后刷新。</p>
        `;
      } else if (state.engine === "json") {
        setStatus("Neo4j 不可用，已加载本地 JSON 图谱；节点可拖动，滚轮可缩放。", "ok");
      } else {
        setStatus("Neo4j 图谱已加载；节点可拖动，滚轮可缩放。", "ok");
      }
    }

    function localOptions() {
      return {
        graphFilename: state.graphFilename,
        centerCode: state.centerCode,
        depth: state.depth,
        limit: state.maxNodes,
        anchorTypes: [...activeAnchorTypes]
      };
    }

    async function load({ probeNeo4j = false } = {}) {
      if (state.loading) return;
      state.loading = true;
      // After a failure, keep using the local graph for cheap interactions
      // (center/depth/limit changes) until the user explicitly refreshes.
      if (state.neo4jDown && !probeNeo4j) {
        applyPayload(localPayload(localGraph, localOptions()));
        state.loading = false;
        return;
      }
      setStatus("正在读取图谱…", "loading");
      const params = new URLSearchParams({
        graph_filename: state.graphFilename,
        center_code: state.centerCode || "",
        depth: String(state.depth),
        limit: String(state.maxNodes)
      });
      try {
        const payload = await loadPayload(
          `/api/kg/neo4j/graph?${params}`,
          localGraph,
          localOptions()
        );
        if (payload.engine === "neo4j") {
          state.neo4jDown = false;
        } else if (payload.fallback_reason && payload.fallback_reason !== "Neo4j 图谱为空") {
          state.neo4jDown = true;
        }
        applyPayload(payload);
      } catch (error) {
        setStatus(error.message || "图谱加载失败", "error");
        countsEl.textContent = `${engineLabel()} 未连接`;
      } finally {
        state.loading = false;
      }
    }

    function createNetwork() {
      const data = buildData();
      const options = {
        autoResize: true,
        locale: "zh-cn",
        interaction: {
          dragNodes: true,
          dragView: true,
          zoomView: true,
          hover: true,
          hoverConnectedEdges: true,
          selectConnectedEdges: true,
          navigationButtons: true,
          keyboard: { speed: { x: 8, y: 8, zoom: 0.05 } },
          tooltipDelay: 120
        },
        layout: { improvedLayout: true },
        physics: {
          enabled: true,
          solver: "forceAtlas2Based",
          forceAtlas2Based: {
            gravitationalConstant: -45,
            centralGravity: 0.015,
            springLength: 105,
            springConstant: 0.04,
            damping: 0.62
          },
          stabilization: {
            enabled: true,
            iterations: 180,
            updateInterval: 25,
            fit: true
          },
          adaptiveTimestep: true
        },
        edges: { selectionWidth: 2 }
      };
      state.network = new vis.Network(canvas, data, options);
      state.network.on("click", params => {
        if (params.nodes.length) highlightNode(params.nodes[0]);
        else if (params.edges.length) {
          state.selectedId = "";
          const edge = state.loadedEdges.find(item => (item.id || `edge-${state.loadedEdges.indexOf(item)}`) === params.edges[0]);
          setInfo(edge || state.loadedEdges.find(item => item.id === params.edges[0]), "edge");
        } else {
          highlightNode("");
        }
      });
      state.network.on("dragStart", () => setStatus("拖动中：松开节点后布局会自动稳定。", "ok"));
      container.__cdssGraph = {
        network: state.network,
        nodeCount: data.nodes.length,
        edgeCount: data.edges.length,
        lastLoadedAt: state.lastLoadedAt || "",
        reload: load
      };
      countsEl.textContent = `${engineLabel()}：${data.nodes.length} 个节点 / ${data.edges.length} 条关系`;
    }

    function wire() {
      container.querySelector("#kgDiseaseSelect").onchange = event => {
        state.centerCode = event.target.value;
        load();
      };
      container.querySelector("#kgDepthSelect").onchange = event => {
        state.depth = Number(event.target.value);
        load();
      };
      container.querySelector("#kgLimitSelect").onchange = event => {
        state.maxNodes = Number(event.target.value);
        load();
      };
      container.querySelectorAll("[name=kgViewMode]").forEach(input => {
        input.onchange = () => {
          state.viewMode = input.value;
          applyViewMode();
        };
      });
      container.querySelectorAll("[data-kg-type]").forEach(input => {
        input.onchange = () => {
          state.enabledTypes = new Set(
            [...container.querySelectorAll("[data-kg-type]:checked")].map(item => item.dataset.kgType)
          );
          applyFilter();
        };
      });
      container.querySelector("#kgNodeLabels").onchange = event => {
        state.showNodeLabels = event.target.checked;
        render();
      };
      container.querySelector("#kgEdgeLabels").onchange = event => {
        state.showEdgeLabels = event.target.checked;
        render();
      };
      container.querySelector("#kgRelayout").onclick = () => {
        if (!state.network) return;
        state.network.setOptions({ physics: true });
        state.network.stabilize();
        setStatus("正在重新布局…", "loading");
      };
      container.querySelector("#kgFit").onclick = () => state.network?.fit({ animation: { duration: 500, easingFunction: "easeOutQuad" } });
      container.querySelector("#kgReload").onclick = () => load({ probeNeo4j: true });
      container.querySelector("#kgBrowser").onclick = event => {
        const url = event.currentTarget.dataset.url || "http://localhost:7474/browser/";
        window.open(url, "_blank", "noopener");
      };
    }

    wire();
    createNetwork();
    load();

    active = {
      destroy() {
        try { state.network?.destroy(); } catch (_) {}
        container.innerHTML = "";
      }
    };
    return active;
  }

  window.CdssGraphView = { mount: create, destroy, localPayload, loadPayload };
})();
