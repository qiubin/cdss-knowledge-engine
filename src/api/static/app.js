(function () {
  "use strict";

  const CASE_STORAGE_KEY = "cdss-case-test-v1";

  const state = {
    route: "dashboard",
    hospitalId: "XM_FIRST",
    doc: null,
    guidePackages: [],
    targetGraphs: [],
    guidePackageId: "",
    importTab: "entities",
    graph: null,
    graphs: [],
    graphFilename: "KG-GENERIC_GUIDELINE_V1.json",
    tcmGraphFilename: "",
    graphFilter: "",
    rules: [],
    ruleSync: null,
    ruleDomain: "",
    ruleDisease: "",
    terms: { lab: {}, symptom: {}, exam: {}, exam_finding: {}, diagnostic_evidence: {} },
    vocab: [],
    standards: [],
    templates: [],
    extractionConfig: null,
    templateEditorId: "",
    templateStructureId: "",
    vocabSearch: "",
    vocabCategory: "",
    entityVocabType: "",
    standardEditIndex: -1,
    inferenceResult: null,
    tcmInferenceResult: null,
    tcmTestResult: null,
    tcmTestCases: [],
    tcmTestCaseId: "",
    tcmExpected: null,
    autoResult: null,
    lastLlmProvider: "",
    demoCases: [],
    demoCaseId: "",
    caseRecord: null,
    caseSavedAt: "",
    importRequestToken: 0,
    pendingUpload: null,
    importJob: null,
    reviewSelectedKey: "",
    reviewItems: [],
    reviewFilters: { graph: "", sourceDoc: "", nodeType: "", relationCode: "" },
    reviewRelationCodeOptions: [],
    reviewSuspectOnly: false,
    reviewGraphOptions: null,
    reviewDocOptions: null,
    reviewNodeTypeOptions: [],
  };
  let importTimer = null;
  let importSubmitting = false; // 「开始导入」提交进行中：防双击重复建任务
  window.__cdssState = state;

  const $ = (selector) => document.querySelector(selector);
  const content = $("#content");
  const titles = {
    dashboard: ["概览", "知识资产、流程状态与快速验证入口"],
    import: ["指南导入", "上传并解析指南，审核候选内容后生成知识包"],
    packages: ["知识包", "审核预检结果，一次性导入图谱和草稿规则；规则仍需人工启用"],
    templates: ["解析模板", "Schema 层：管理本地与大模型共用的抽取契约（实体类型、关系、属性）"],
    entityVocab: ["实体词典", "术语层：共享实体字典，具体临床实体与别名跨模板复用"],
    vocab: ["概念词汇", "管理规则生成与推理判断使用的抽象语义"],
    graph: ["知识图谱", "管理疾病、诊断证据和指南出处"],
    tcmGraph: ["中医图谱", "证候为中心的辨证、治法与方药知识图谱"],
    rules: ["规则管理", "规则使用抽象证据概念，不保存医院阈值"],
    review: ["审核台", "跨图谱与规则的待办、阻断、待复核与审核记录"],
    standards: ["标准管理", "按医院维护概念到检验判定阈值的标准"],
    inference: ["病历测试", "用模拟病历调用知识图谱、规则和医院标准"],
    tcmInference: ["中医辨证", "输入症状、舌象、脉象，输出证候、治法与方药"],
  };

  async function api(path, options = {}) {
    const config = { ...options };
    if (config.json !== undefined) {
      config.method = config.method || "POST";
      config.headers = { "Content-Type": "application/json", ...(config.headers || {}) };
      config.body = JSON.stringify(config.json);
      delete config.json;
    }
    const response = await fetch(path, config);
    const text = await response.text();
    let data;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = { message: text }; }
    if (!response.ok) {
      const detail = data && data.detail;
      const message = typeof detail === "string"
        ? detail
        : detail ? JSON.stringify(detail)
        : (data && data.message) || `请求失败 ${response.status}`;
      throw new Error(message);
    }
    return data;
  }

  function esc(value) {
    if (value === null || value === undefined) value = "";
    return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function toast(message, isError = false) {
    const el = $("#toast");
    el.textContent = message;
    el.className = `toast show${isError ? " error" : ""}`;
    setTimeout(() => { el.className = "toast"; }, isError ? 5000 : 2800);
  }
  const badge = (value) => `<span class="badge ${esc(value)}">${esc(value)}</span>`;
  const REVIEW_LABELS = {
    draft: "待审核", active: "已通过", rejected: "已拒绝",
    retired: "已失效", stale: "待复核", blocked: "已阻断", testing: "测试中",
    approved: "预审通过",
  };
  const reviewBadge = (value) => {
    const status = value || "draft";
    return `<span class="badge ${esc(status)}">${esc(REVIEW_LABELS[status] || status)}</span>`;
  };
  const conceptName = (code) => {
    const item = state.vocab.find((v) => v.code === code);
    return item ? `${item.name_cn} (${code})` : code || "-";
  };
  const normalizeDomainClient = (value) => {
    const text = String(value || "").trim().toLowerCase();
    return ["tcm", "中医", "中医内科"].includes(text) ? "tcm" : "western";
  };
  const availableTcmTargetGraphs = (items) => items.filter((item) => (
    item.source !== "builtin-demo" && Number(item.schema_version || 1) >= 2
  ));
  const typeName = {
    disease: "疾病", lab: "检验", concept: "概念", exam: "检查", exam_finding: "检查发现",
    symptom: "症状", diagnostic_evidence: "诊断证据", syndrome: "证候", tongue: "舌象",
    pulse: "脉象", sign: "体征", pathogenesis: "病机", treatment_method: "治法",
    formula: "方剂", herb: "中药", contraindication: "禁忌",
  };

  function navigate(route) {
    state.route = route;
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.route === route));
    const [title, desc] = titles[route] || ["概览", ""];
    $("#pageTitle").textContent = title;
    $("#pageDesc").textContent = desc;
    render();
  }

  function panel(title, body, actions = "", description = "", flush = false) {
    return `<section class="panel"><div class="panel-head"><div><h3>${esc(title)}</h3>${description ? `<p>${esc(description)}</p>` : ""}</div><div class="actions">${actions}</div></div><div class="panel-body${flush ? " flush" : ""}">${body || '<div class="empty">暂无数据</div>'}</div></section>`;
  }
  function table(headers, rows) {
    if (!rows.length) return '<div class="empty">暂无数据</div>';
    return `<div class="table-wrap"><table><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
  }

  // ---- Paginated, searchable tables for long panels ----
  // items: [{ text, values, html }] — text feeds the search box, values feeds
  // the dropdown filters, html is the <tr> markup. Rendering is client-side
  // because these graph payloads are already in memory.
  const pagedTableData = {};
  const pagedTableState = {};

  function tableStateFor(key) {
    if (!pagedTableState[key]) {
      pagedTableState[key] = { query: "", page: 1, size: 10, filters: {} };
    }
    return pagedTableState[key];
  }

  function pagedPanel({ key, title, description = "", headers, items, filters = [], placeholder = "搜索…", emptyText = "暂无数据", actions = "" }) {
    const ts = tableStateFor(key);
    pagedTableData[key] = { headers, items, filters, emptyText };
    const filterHtml = filters.map((filter) => `
      <select class="table-filter" data-table-filter="${key}" data-filter-key="${esc(filter.key)}">
        <option value="">${esc(filter.label)}：全部</option>
        ${filter.options.map((option) => `<option value="${esc(option.value)}" ${ts.filters[filter.key] === option.value ? "selected" : ""}>${esc(option.label ?? option.value)}</option>`).join("")}
      </select>`).join("");
    return `<section class="panel">
      <div class="panel-head"><div><h3>${esc(title)}</h3>${description ? `<p>${esc(description)}</p>` : ""}</div><div class="actions">${actions}</div></div>
      <div class="panel-body flush">
        <div class="table-toolbar">
          <input type="search" class="table-search" data-table-search="${key}" placeholder="${esc(placeholder)}" value="${esc(ts.query)}">
          ${filterHtml}
          <select class="table-size" data-table-size="${key}">${[10, 20, 50, 100].map((n) => `<option value="${n}" ${ts.size === n ? "selected" : ""}>每页 ${n}</option>`).join("")}</select>
          <span class="table-summary" data-table-summary="${key}"></span>
        </div>
        <div data-table-body="${key}"></div>
      </div>
    </section>`;
  }

  function visibleTableItems(key) {
    const data = pagedTableData[key];
    const ts = tableStateFor(key);
    if (!data) return [];
    const query = ts.query.trim().toLowerCase();
    return data.items.filter((item) => {
      if (query && !String(item.text || "").toLowerCase().includes(query)) return false;
      return data.filters.every((filter) => {
        const selected = ts.filters[filter.key] || "";
        if (!selected) return true;
        const value = item.values?.[filter.key];
        if (Array.isArray(value)) return value.map(String).includes(selected);
        return String(value ?? "") === selected;
      });
    });
  }

  function renderTableBody(key) {
    const data = pagedTableData[key];
    const ts = tableStateFor(key);
    const mount = document.querySelector(`[data-table-body="${key}"]`);
    if (!data || !mount) return;
    const filtered = visibleTableItems(key);
    const totalPages = Math.max(1, Math.ceil(filtered.length / ts.size));
    if (ts.page > totalPages) ts.page = totalPages;
    const start = (ts.page - 1) * ts.size;
    const rows = filtered.slice(start, start + ts.size);
    const summary = document.querySelector(`[data-table-summary="${key}"]`);
    if (summary) summary.textContent = `共 ${filtered.length} 条`;
    const tableHtml = rows.length
      ? `<div class="table-wrap"><table><thead><tr>${data.headers.map((header) => `<th>${header}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => row.html).join("")}</tbody></table></div>`
      : `<div class="empty">${esc(data.emptyText)}</div>`;
    const pager = totalPages > 1
      ? `<div class="table-pager"><button type="button" class="btn sm ghost" data-page-prev="${key}" ${ts.page <= 1 ? "disabled" : ""}>上一页</button><span>第 ${ts.page} / ${totalPages} 页</span><button type="button" class="btn sm ghost" data-page-next="${key}" ${ts.page >= totalPages ? "disabled" : ""}>下一页</button></div>`
      : "";
    mount.innerHTML = tableHtml + pager;
  }

  function bindPagedTable(key) {
    const ts = tableStateFor(key);
    const search = document.querySelector(`[data-table-search="${key}"]`);
    if (search) {
      search.addEventListener("input", (event) => {
        ts.query = event.target.value;
        ts.page = 1;
        renderTableBody(key);
      });
    }
    document.querySelectorAll(`[data-table-filter="${key}"]`).forEach((select) => {
      select.addEventListener("change", (event) => {
        ts.filters[event.target.dataset.filterKey] = event.target.value;
        ts.page = 1;
        renderTableBody(key);
      });
    });
    const size = document.querySelector(`[data-table-size="${key}"]`);
    if (size) {
      size.addEventListener("change", (event) => {
        ts.size = Number(event.target.value) || 10;
        ts.page = 1;
        renderTableBody(key);
      });
    }
    const body = document.querySelector(`[data-table-body="${key}"]`);
    if (body) {
      body.addEventListener("click", (event) => {
        if (event.target.closest(`[data-page-prev="${key}"]`)) {
          ts.page = Math.max(1, ts.page - 1);
          renderTableBody(key);
        } else if (event.target.closest(`[data-page-next="${key}"]`)) {
          ts.page += 1;
          renderTableBody(key);
        }
      });
    }
    renderTableBody(key);
  }

  function resetPagedTables() {
    Object.keys(pagedTableState).forEach((key) => { delete pagedTableState[key]; });
    Object.keys(pagedTableData).forEach((key) => { delete pagedTableData[key]; });
  }
  function collectConceptCodes(node, result = []) {
    if (!node) return result;
    if (node.concept_code) result.push(node.concept_code);
    if (Array.isArray(node.children)) node.children.forEach((child) => collectConceptCodes(child, result));
    return [...new Set(result)];
  }
  function resolveRuleConceptCodes(rule, graph = state.graph, result = []) {
    if (!rule) return result;
    if (rule.concept_code) result.push(rule.concept_code);
    if (rule.type === "evidence" && rule.evidence_id && graph) {
      const evidence = graph.evidences.find((item) => item.id === rule.evidence_id);
      if (evidence) {
        if (evidence.concept_code) result.push(evidence.concept_code);
        else if (evidence.data_type === "exam_finding" || evidence.data_type === "symptom") result.push("PRESENT");
      }
    }
    if (rule.type === "exam_finding" && rule.finding_code) result.push(rule.concept_code || "PRESENT");
    if (rule.type === "symptom" && rule.symptom_code) result.push(rule.concept_code || "PRESENT");
    if (Array.isArray(rule.children)) rule.children.forEach((child) => resolveRuleConceptCodes(child, graph, result));
    return [...new Set(result)];
  }
  function conceptChip(code, options = {}) {
    const item = state.vocab.find((v) => v.code === code);
    if (!item) return `<span class="badge danger" title="概念词汇未定义">${esc(code || "-")} / 未定义</span>`;
    const inactive = !item.is_active;
    const cls = inactive ? "draft" : "active";
    const title = `${item.name_cn} / ${item.code}\n类别：${item.category}\n执行器：${item.evaluator_type}`;
    const label = `${esc(item.name_cn)} / ${esc(item.code)}`;
    return options.static ? `<span class="badge ${cls}" title="${esc(title)}">${label}</span>` : `<button class="badge ${cls}" data-vocab-detail="${esc(code)}" title="${esc(title)}">${label}</button>`;
  }
  function evidenceConceptChip(evidence) {
    if (evidence.concept_code) return conceptChip(evidence.concept_code);
    return evidence.data_type === "exam_finding" || evidence.data_type === "symptom" ? conceptChip("PRESENT") : '<span class="muted">-</span>';
  }

  async function render() {
    content.innerHTML = '<div class="empty">加载中...</div>';
    try {
      if (state.route === "dashboard") await renderDashboard();
      else if (state.route === "import") await renderImport();
      else if (state.route === "packages") await renderGuidePackages();
      else if (state.route === "templates") await renderTemplates();
      else if (state.route === "entityVocab") await renderEntityVocab();
      else if (state.route === "vocab") await renderVocab();
      else if (state.route === "graph") await renderGraph();
      else if (state.route === "tcmGraph") await renderTcmGraph();
      else if (state.route === "rules") await renderRules();
      else if (state.route === "review") await renderReviewCenter();
      else if (state.route === "standards") await renderStandards();
      else if (state.route === "tcmInference") await renderTcmInference();
      else await renderInference();
    } catch (error) {
      content.innerHTML = `<div class="result-banner error">${esc(error.message)}</div>`;
    }
  }

  async function loadBase() {
    const [stats, rules, vocab, hospitals, labs, symptoms, exams, findings, evidence] = await Promise.all([
      api("/api/kg/stats"),
      api("/api/rules"),
      api("/api/vocab"),
      api("/api/standards/hospitals"),
      api("/api/vocab/entities/lab").catch(() => []),
      api("/api/vocab/entities/symptom").catch(() => []),
      api("/api/vocab/entities/exam").catch(() => []),
      api("/api/vocab/entities/exam_finding").catch(() => []),
      api("/api/vocab/entities/diagnostic_evidence").catch(() => []),
    ]);
    const index = (items) => Object.fromEntries((items || []).map((item) => [item.code, item]));
    state.terms = {
      lab: index(labs),
      symptom: index(symptoms),
      exam: index(exams),
      exam_finding: index(findings),
      diagnostic_evidence: index(evidence),
    };
    state.rules = rules; state.vocab = vocab; state.hospitals = hospitals;
    return { stats, rules, hospitals };
  }

  function termLabel(kind, code) {
    if (!code) return "-";
    const item = (state.terms[kind] || {})[code];
    return item && item.name ? `${item.name}（${code}）` : code;
  }

  async function renderDashboard() {
    const [{ stats, rules, hospitals }, docs, graphs, graph, standardOverview, ruleSync, reviewQueue] = await Promise.all([
      loadBase(),
      api("/api/pdf/documents"),
      api("/api/kg/graphs").then((items) => items.filter((item) => normalizeDomainClient(item.domain) === "western")),
      api(`/api/kg/graph?graph_filename=${encodeURIComponent(state.graphFilename)}`),
      api("/api/standards/all"),
      api("/api/rules/sync-check"),
      api("/api/review/queue?limit=1").catch(() => ({ summary: {} })),
    ]);
    state.graphs = graphs;
    state.graph = graph;
    state.rules = rules;
    state.ruleSync = ruleSync;
    if (!graphs.some((item) => item.graph_filename === state.graphFilename)) {
      state.graphFilename = graphs.find((item) => item.graph_filename === "KG-GENERIC_GUIDELINE_V1.json")?.graph_filename || graphs[0]?.graph_filename || "stemi_graph.json";
    }
    const graphFilename = state.graphFilename;
    const selectedRules = rules.filter((rule) => (rule.graph_filename || "KG-GENERIC_GUIDELINE_V1.json") === graphFilename);
    const activeRules = selectedRules.filter((rule) => rule.status === "active");
    const reviewSummary = reviewQueue.summary || {};
    const sumStatuses = (counter, statuses) => statuses.reduce((sum, s) => sum + (counter?.[s] || 0), 0);
    const pendingReview =
      sumStatuses(reviewSummary.graph_nodes, ["draft", "stale"])
      + sumStatuses(reviewSummary.graph_relations, ["draft", "stale"])
      + sumStatuses(reviewSummary.rules, ["draft", "testing", "blocked"]);
    const semanticNodes = Array.isArray(graph.nodes) ? graph.nodes : Object.values(graph.nodes || {});
    const semanticRelations = Array.isArray(graph.relations) ? graph.relations : Object.values(graph.relations || {});
    const planNodes = semanticNodes.filter((node) => node.node_type === "treatment_plan");
    const planDiseaseLinks = semanticRelations.filter((relation) => relation.relation_code === "DISEASE_HAS_TREATMENT_PLAN");
    const medNodes = semanticNodes.filter((node) => node.node_type === "medication");
    const interventionNodes = semanticNodes.filter((node) => node.node_type === "intervention");
    const steps = [
      ["上传指南", `${docs.length} 份文档`, docs.length ? "done" : "pending"],
      ["知识包生成", "候选图谱与规则", "pending"],
      ["人工导入", "草稿进入图谱与规则", "pending"],
      ["图谱入库", `${graph.evidences.length} 证据节点`, "done"],
      ["规则配置", `${activeRules.length} 条活跃`, "done"],
      ["医院标准", `${standardOverview.active_count} 条活跃`, "done"],
    ];
    content.innerHTML = `
      ${panel("知识图谱上下文", `
        <div class="inline-controls">
          <div class="field"><label>当前图谱</label><select id="dashboardGraph">${graphs.map((item) => `<option value="${esc(item.graph_filename)}" ${item.graph_filename === graphFilename ? "selected" : ""}>${esc(item.graph_filename)}</option>`).join("")}</select></div>
        </div>
      `, "", "统计口径跟随当前图谱；规则执行仍按规则自身配置的图谱加载")}
      <div class="grid c4">
        <div class="kpi"><span>疾病节点</span><strong>${graph.diseases.length}</strong><small>当前图谱 ICD 实体</small></div>
        <div class="kpi"><span>诊断证据</span><strong>${graph.evidences.length}</strong><small>${graph.disease_evidence.length} 条疾病关联</small></div>
        <div class="kpi"><span>治疗方案</span><strong>${planNodes.length}</strong><small>${planDiseaseLinks.length} 条疾病关联</small></div>
        <div class="kpi"><span>药物与措施</span><strong>${medNodes.length + interventionNodes.length}</strong><small>${medNodes.length} 药物 / ${interventionNodes.length} 措施</small></div>
        <div class="kpi"><span>推理规则</span><strong>${selectedRules.length}</strong><small>${activeRules.length} 条活跃</small></div>
        <div class="kpi"><span>医院标准</span><strong>${standardOverview.total_count}</strong><small>${standardOverview.active_count} 条活跃 / ${standardOverview.hospital_count} 家机构</small></div>
      </div>
      ${panel("全流程状态", `<div class="pipeline">${steps.map((s) => `<div class="step ${s[2]}"><strong>${s[0]}</strong><span>${s[1]}</span></div>`).join("")}</div>`)}
      ${panel("快速操作", `
        <div class="actions">
          <button class="btn primary" data-quick="import">上传 PDF 指南</button>
          <button class="btn ${pendingReview ? "primary" : ""}" data-quick="review">进入审核台${pendingReview ? `（${pendingReview} 条待办）` : ""}</button>
          <button class="btn" data-quick="packages">管理知识包</button>
          <button class="btn" data-quick="graph">查看知识图谱</button>
          <button class="btn" data-quick="standards">维护医院标准</button>
          <button class="btn" data-quick="inference">病历测试</button>
          <button class="btn" data-quick="tcmInference">中医辨证</button>
        </div>`, "", "按 引入 → 审核 → 验证 的主线组织；示例患者已内置在病历测试页")}
      ${panel("已上传文档", table(["文档", "ID", "页数", "实体", "上传时间"], docs.map((d) => `<tr><td>${esc(d.doc_title)}</td><td class="mono">${esc(d.doc_id)}</td><td>${d.total_pages}</td><td>${d.entity_count}</td><td class="nowrap">${esc(formatTime(d.uploaded_at))}</td></tr>`)))}
    `;
    $("#dashboardGraph").onchange = (event) => {
      state.graphFilename = event.target.value;
      renderDashboard();
    };
  }

  function templateGraphFilename(t) {
    return t.graph_filename || `KG-${t.template_id || "GENERIC_GUIDELINE_V1"}.json`;
  }

  // ---- 导入任务控制（消费 /api/import/jobs* 异步作业端点）----
  function jobControlButtons(status) {
    const map = {
      queued: { pause: true, resume: false, stop: true, start: false },
      running: { pause: true, resume: false, stop: true, start: false },
      pausing: { pause: false, resume: true, stop: true, start: false },
      paused: { pause: false, resume: true, stop: true, start: false },
      succeeded: { pause: false, resume: false, stop: false, start: false },
      failed: { pause: false, resume: false, stop: false, start: true },
      stopped: { pause: false, resume: false, stop: false, start: true },
    };
    return map[status] || map.succeeded;
  }

  function jobProgressText(job) {
    const { done = 0, total = 0 } = job.progress || {};
    const suffix = job.status === "paused" ? " · 已暂停" : job.status === "pausing" ? " · 暂停中" : "";
    return total ? `${job.stage || "处理中"}（${done}/${total} 组）${suffix}` : `${job.stage || "处理中"}${suffix}`;
  }

  // 导入历史表结果徽章：paused/resumed 为正常暂停/继续，中性样式；未知状态按失败兜底
  function importLogStatusBadge(status) {
    if (status === "success") return '<span class="badge active">成功</span>';
    if (status === "started") return "已开始";
    if (status === "paused") return '<span class="badge">已暂停</span>';
    if (status === "resumed") return '<span class="badge">已继续</span>';
    if (status === "stopped") return '<span class="badge">已停止</span>';
    return '<span class="badge danger">失败</span>';
  }

  const IMPORT_JOB_STATUS_LABELS = {
    queued: "排队中", running: "正在导入", pausing: "正在暂停", paused: "已暂停",
    succeeded: "导入完成", failed: "导入失败", stopped: "已停止",
  };
  const IMPORT_JOB_TERMINAL = ["succeeded", "failed", "stopped"];

  function activeImportJob() {
    return ["queued", "running", "pausing", "paused"].includes(state.importJob?.status) ? state.importJob : null;
  }

  function pendingUploadMode() {
    const pending = state.pendingUpload;
    if (!pending) return "";
    return pending.kind === "auto" ? "auto" : pending.mode === "llm" ? "template-llm" : "template-local";
  }

  function refreshUploadGraphOptions() {
    const sel = $("#uploadGraph");
    if (!sel) return;
    const template = state.templates.find((t) => t.template_id === $("#uploadTemplate")?.value);
    const domain = template ? normalizeDomainClient(template.domain) : "";
    const graphs = (state.uploadGraphs || []).filter((g) => !domain || normalizeDomainClient(g.domain) === domain);
    const choice = state.uploadGraphChoice || "follow";
    sel.innerHTML = [
      `<option value="follow">跟随模板（${esc(templateGraphFilename(template || {}))}）</option>`,
      ...graphs.map((g) => `<option value="${esc(g.graph_filename)}">${esc(g.graph_filename)}</option>`),
      `<option value="__new__">＋ 新建图谱…</option>`,
    ].join("");
    sel.value = choice === "follow" || choice === "__new__" || graphs.some((g) => g.graph_filename === choice) ? choice : "follow";
    $("#uploadGraphNew").hidden = sel.value !== "__new__";
  }

  async function renderImport() {
    await loadBase();
    await loadExtractionSettings();
    if (!state.doc) {
      const docs = await api("/api/pdf/documents");
      if (docs.length) state.doc = await api(`/api/pdf/${docs[0].doc_id}/result`);
    }
    const doc = state.doc;
    content.innerHTML = `
      <div id="pendingUploadPanel"></div>
      <div id="importJobPanel"></div>
      ${panel("一键自动生成（LLM 推荐）", `
        <div class="inline-controls">
          <button id="autoUploadBtn" class="btn primary">选择指南 PDF 自动生成</button>
          <span class="muted">自动完成并直接入库：开放术语发现 → 标准码/LOCAL 码 → 图谱 → 诊断/治疗/禁忌规则 → 结构化治疗方案</span>
        </div>
        <input id="autoPdfInput" type="file" accept=".pdf" hidden>
      `, "", "不需要预先补疾病、症状、检查词典；需先在「解析模板」页配置 LLM 地址、API Key、模型")}
      ${state.autoResult ? `
        <div id="autoResultPanel" class="result-banner ok">
          <strong>自动生成并已入库：${esc(state.autoResult.doc_title || state.autoResult.doc_id)}</strong>
          <div>实体 ${state.autoResult.entity_count} / 关系 ${state.autoResult.relation_count} / 规则 ${state.autoResult.rule_count}（已启用 ${state.autoResult.active_rule_count}）/ LOCAL 术语 ${state.autoResult.local_term_count}</div>
          <div class="muted">图谱和草稿规则已自动写入；请到「知识包」页核验预检结果，再到「规则管理」逐条启用。</div>
          <div class="mono">图谱：${esc(state.autoResult.graph_filename)} · 知识包：${esc(state.autoResult.package_id)}</div>
          <div class="actions"><button id="autoOpenPackage" class="btn sm primary">查看知识包</button><button id="autoOpenGraph" class="btn sm">查看图谱</button><button id="autoOpenRules" class="btn sm">查看规则</button></div>
        </div>` : ""}
      ${panel("上传指南 PDF（模板模式）", `
        <div class="inline-controls">
          <div class="field"><label>解析方式</label><select id="uploadMode"><option value="local">本地模板解析</option><option value="llm">大模型解析</option></select></div>
          <div class="field"><label>抽取模板</label><select id="uploadTemplate">${state.templates.map((t) => `<option value="${esc(t.template_id)}">[${normalizeDomainClient(t.domain) === "tcm" ? "中医" : "西医"}] ${esc(t.name)} / ${esc(t.template_id)}</option>`).join("")}</select></div>
          <div class="field"><label>目的图谱</label><div id="uploadGraphWrap"><select id="uploadGraph"></select><input id="uploadGraphNew" placeholder="新图谱文件名，如 KG-RESPIRATORY_V1.json" hidden></div></div>
          <div class="field"><label>API Key</label><input value="${state.extractionConfig.llm.api_key_configured ? "环境变量已配置" : "未配置"}" readonly></div>
        </div>
        <div id="dropzone" class="dropzone"><strong>点击或拖拽 PDF 到此处</strong><p class="muted">解析页码、行号与正文片段；自动抽取实体并生成候选规则</p></div>
        <input id="pdfInput" type="file" accept=".pdf" hidden>
      `, "", "选择文件后先进入「待导入」卡片，点击「开始导入」才提交服务端；解析仅进入审核区，图谱和规则需人工确认后才入库；长篇指南建议使用大模型解析，本地解析容易产生噪声规则")}
      <div id="importResult"></div>
      ${panel("导入历史", '<div id="importHistoryBody" class="muted">加载中…</div>', "", "记录每次上传（含一键自动）的成功与失败；新文档使用新编号，不会覆盖历史记录")}
    `;
    const drop = $("#dropzone"), input = $("#pdfInput");
    const autoInput = $("#autoPdfInput"), autoButton = $("#autoUploadBtn");
    autoButton.onclick = () => autoInput.click();
    autoInput.onchange = () => autoInput.files[0] && prepareAutoUpload(autoInput.files[0]);
    $("#autoOpenPackage")?.addEventListener("click", () => {
      state.guidePackageId = state.autoResult.package_id;
      navigate("packages");
    });
    $("#autoOpenGraph")?.addEventListener("click", () => {
      state.graphFilename = state.autoResult.graph_filename;
      navigate("graph");
    });
    $("#autoOpenRules")?.addEventListener("click", () => navigate("rules"));
    drop.onclick = () => input.click();
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("drag"); };
    drop.ondragleave = () => drop.classList.remove("drag");
    drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("drag"); if (e.dataTransfer.files[0]) prepareUpload(e.dataTransfer.files[0]); };
    input.onchange = () => input.files[0] && prepareUpload(input.files[0]);
    $("#uploadMode").value = state.extractionConfig.default_mode || "local";
    $("#uploadTemplate").value = state.extractionConfig.default_template_id || state.templates.find((t) => t.is_default)?.template_id || "";
    $("#uploadMode").onchange = (e) => {
      if (e.target.value === "llm" && !state.extractionConfig.llm.api_key_configured) toast("当前环境变量未配置大模型 API Key，调用将失败", true);
    };
    $("#uploadTemplate").onchange = () => { state.uploadGraphChoice = $("#uploadGraph")?.value || "follow"; refreshUploadGraphOptions(); };
    $("#uploadGraph").onchange = (e) => {
      state.uploadGraphChoice = e.target.value;
      $("#uploadGraphNew").hidden = e.target.value !== "__new__";
    };
    api("/api/kg/graphs").then((graphs) => { state.uploadGraphs = graphs; refreshUploadGraphOptions(); }).catch(() => { state.uploadGraphs = []; });
    if (activeImportJob()) setImportBusy(true); else setImportBusy(false);
    renderImportJob();
    if (doc) renderImportResult();
    api("/api/pdf/import-logs?limit=50").then((logs) => {
      const body = $("#importHistoryBody");
      if (!body) return;
      if (!logs.length) { body.innerHTML = '<div class="empty">暂无导入记录；每次上传（含失败）都会在这里留痕。</div>'; return; }
      body.className = "";
      body.innerHTML = table(["时间", "文档", "方式", "结果", "数量", "耗时", "失败原因"], logs.map((log) => `<tr>
        <td class="nowrap mono">${esc(formatTime(log.ts))}</td>
        <td><strong>${esc(log.doc_title || "-")}</strong><div class="mono muted">${esc(log.doc_id || "")}</div></td>
        <td>${log.mode === "auto" ? "一键自动" : log.mode === "llm" ? "大模型" : log.mode === "local" ? "本地模板" : esc(log.mode || "-")}</td>
        <td>${importLogStatusBadge(log.status)}</td>
        <td class="muted">${log.status === "success" ? `${log.entity_count ?? "-"} 实体 / ${log.relation_count ?? "-"} 关系 / ${log.rule_count ?? "-"} 规则` : "-"}</td>
        <td class="mono muted">${log.duration_ms != null ? `${log.duration_ms} ms` : "-"}</td>
        <td>${esc(log.error || "-")}</td>
      </tr>`));
    }).catch(() => {});
  }

  function renderPendingUpload() {
    const box = $("#pendingUploadPanel");
    if (!box) return;
    const pending = state.pendingUpload;
    if (!pending || activeImportJob()) { box.innerHTML = ""; return; }
    const summary = pending.kind === "auto"
      ? "一键自动生成：开放术语发现 → 标准码/LOCAL 码 → 图谱 → 诊断/治疗/禁忌规则 → 结构化治疗方案"
      : `解析方式：${pending.mode === "llm" ? "大模型解析" : "本地模板解析"} · 抽取模板：${esc(pending.templateId || "-")} · 目的图谱：${pending.graphFilename ? esc(pending.graphFilename) : "跟随模板"}`;
    box.innerHTML = `
      <section class="panel">
        <div class="panel-head"><div><h3>待导入</h3><p>已选择指南文件；确认配置后点击开始，任务提交到服务端异步执行，可暂停、继续或停止</p></div></div>
        <div class="panel-body"><div class="inline-controls">
          <strong>${esc(pending.file.name)}</strong>
          <span class="muted">${summary}</span>
          <button id="jobStartBtn" class="btn primary">开始导入</button>
          <button id="pendingDiscardBtn" class="btn">重新选择</button>
        </div></div>
      </section>`;
    $("#jobStartBtn").onclick = (event) => {
      const btn = event.currentTarget;
      btn.disabled = true; // 双击防护：settle 后恢复；成功时面板被任务卡片替换
      startImportJob().finally(() => { btn.disabled = false; });
    };
    $("#pendingDiscardBtn").onclick = () => { state.pendingUpload = null; renderPendingUpload(); };
  }

  function renderImportJob() {
    renderPendingUpload();
    const box = $("#importJobPanel");
    if (!box) return;
    const job = state.importJob;
    if (!job) { box.innerHTML = ""; return; }
    const controls = jobControlButtons(job.status);
    const terminal = IMPORT_JOB_TERMINAL.includes(job.status);
    const { done = 0, total = 0 } = job.progress || {};
    const percent = job.status === "succeeded" ? 100
      : total ? Math.max(2, Math.min(99, Math.round((done / total) * 100))) : 2;
    const tone = job.status === "failed" ? "error" : job.status;
    const clause = job.mode === "auto" ? "一键自动生成（解析 + 图谱 + 规则）"
      : job.mode === "template-llm" ? "模板模式解析（大模型）" : "模板模式解析（本地）";
    const nowSec = job.finished_at || Date.now() / 1000;
    const elapsed = Math.max(0, nowSec - (job.created_at || nowSec));
    const logs = (job.logs || []).slice(-10);
    box.innerHTML = `
      <section class="progress-panel ${tone}" aria-live="polite">
        <div class="progress-head">
          <div><strong>${esc(IMPORT_JOB_STATUS_LABELS[job.status] || job.status)}：${esc(job.doc_title || "")}</strong><div class="muted">${clause}</div></div>
          <strong class="progress-percent">${job.status === "failed" ? "失败" : job.status === "stopped" ? "已停止" : `${percent}%`}</strong>
        </div>
        <div class="progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><div class="progress-fill ${tone}" style="width:${percent}%"></div></div>
        <div class="progress-meta">
          <span>${esc(jobProgressText(job))}</span>
          <span>${terminal ? `总用时 ${elapsed.toFixed(1)} 秒` : `已用时 ${Math.floor(elapsed)} 秒`}</span>
        </div>
        <div class="progress-log">${logs.map((item) => `<div class="log-line ${esc(item.level || "info")}"><span class="time">${esc(item.time)}</span><span>${esc(item.message)}</span></div>`).join("")}</div>
        ${job.error ? `<div class="result-banner error" style="margin:10px 18px 14px">${esc(job.error)}</div>` : ""}
        <div class="actions" style="padding:0 18px 14px">
          ${controls.pause ? '<button id="jobPauseBtn" class="btn sm">暂停</button>' : ""}
          ${controls.resume ? '<button id="jobResumeBtn" class="btn sm primary">继续</button>' : ""}
          ${controls.stop ? '<button id="jobStopBtn" class="btn sm danger">停止</button>' : ""}
          ${controls.start && state.pendingUpload ? '<button id="jobRestartBtn" class="btn sm primary">重新开始</button>' : ""}
        </div>
      </section>`;
    $("#jobPauseBtn")?.addEventListener("click", () => importJobControl("pause"));
    $("#jobResumeBtn")?.addEventListener("click", () => importJobControl("resume"));
    $("#jobStopBtn")?.addEventListener("click", () => importJobControl("stop"));
    $("#jobRestartBtn")?.addEventListener("click", (event) => {
      const btn = event.currentTarget;
      btn.disabled = true; // 同开始按钮：提交进行中防双击
      startImportJob().finally(() => { btn.disabled = false; });
    });
  }

  function renderImportJobSummary(job) {
    const box = $("#importResult");
    if (!box) return;
    const result = job.result || {};
    const warnings = result.warnings || [];
    box.innerHTML = `
      <div class="result-banner ok">
        <strong>${esc(result.doc_title || job.doc_title || "")} 解析成功：${esc(result.total_pages ?? "-")} 页 / ${esc(result.entity_count ?? "-")} 实体 / ${esc(result.relation_count ?? "-")} 关系 / ${esc(result.rule_count ?? "-")} 条候选规则</strong>
        <div class="mono">文档 ${esc(result.doc_id || "-")} · 图谱 ${esc(result.graph_filename || "-")}</div>
        ${warnings.length ? `<details class="debug-panel" style="margin-top:8px"><summary>解析告警 ${warnings.length} 条</summary><ul class="issue-list">${warnings.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></details>` : ""}
        <div class="actions" style="margin-top:10px"><button id="jobOpenDetailBtn" class="btn sm primary">查看详情并审核</button></div>
        <div class="muted" style="margin-top:6px">此处为提交结果摘要；候选实体、关系与规则在完整解析结果中审核。</div>
      </div>`;
    $("#jobOpenDetailBtn")?.addEventListener("click", async () => {
      try {
        state.doc = await api(`/api/pdf/${encodeURIComponent(result.doc_id)}/result`);
        state.importTab = "entities";
        renderImportResult();
      } catch (error) { toast(error.message, true); }
    });
  }

  async function startImportJob() {
    const pending = state.pendingUpload;
    if (!pending) return toast("请先选择指南 PDF", true);
    if (importSubmitting) return toast("任务正在提交，请勿重复点击", true);
    if (activeImportJob()) return toast("已有导入任务正在进行，请等待完成或停止当前任务", true);
    importSubmitting = true;
    stopImportPolling();
    // Hide results from a previous document so a failed new parse cannot be
    // mistaken for the old success banner.
    state.doc = null;
    state.autoResult = null;
    const resultBox = $("#importResult");
    if (resultBox) resultBox.innerHTML = "";
    $("#autoResultPanel")?.remove();
    const form = new FormData();
    form.append("file", pending.file);
    form.append("mode", pendingUploadMode());
    if (pending.kind === "template") {
      form.append("template_id", pending.templateId || "");
      if (pending.graphFilename) form.append("graph_filename", pending.graphFilename);
    } else {
      form.append("auto_activate", "false");
    }
    let created;
    try {
      created = await api("/api/import/jobs", { method: "POST", body: form });
    } catch (error) {
      toast(`任务提交失败：${error.message}`, true);
      return;
    } finally {
      importSubmitting = false; // 请求 settle（成功/失败）即解除提交锁
    }
    state.importJob = {
      job_id: created.job_id,
      mode: pendingUploadMode(),
      doc_title: pending.file.name.replace(/\.pdf$/i, ""),
      status: created.status || "queued",
      stage: "排队中",
      progress: { done: 0, total: 0 },
      logs: [],
      result: null,
      error: "",
      created_at: Date.now() / 1000,
      finished_at: null,
    };
    setImportBusy(true);
    renderImportJob();
    pollImportJob(created.job_id);
  }

  async function fetchImportJob(jobId) {
    const response = await fetch(`/api/import/jobs/${encodeURIComponent(jobId)}`);
    if (response.status === 404) return { __not_found: true };
    if (!response.ok) throw new Error(`请求失败 ${response.status}`);
    return response.json();
  }

  function stopImportPolling() {
    if (importTimer) { clearInterval(importTimer); importTimer = null; }
  }

  function applyImportJobData(data) {
    state.importJob = data;
    if (IMPORT_JOB_TERMINAL.includes(data.status)) {
      stopImportPolling();
      handleImportJobFinished(data);
    } else {
      renderImportJob();
    }
  }

  function pollImportJob(jobId) {
    stopImportPolling();
    let failures = 0;
    importTimer = setInterval(async () => {
      if (state.importJob?.job_id !== jobId) { stopImportPolling(); return; }
      let data;
      try {
        data = await fetchImportJob(jobId);
        failures = 0;
      } catch (_) {
        failures += 1;
        if (failures < 5) return; // 偶发网络抖动：跳过本轮，继续轮询
        stopImportPolling();
        state.importJob = { ...state.importJob, status: "failed", error: "连续多次无法获取任务状态，已停止轮询" };
        setImportBusy(false);
        renderImportJob();
        toast("无法连接服务，已停止轮询；确认服务恢复后可重新开始", true);
        return;
      }
      if (data.__not_found) {
        stopImportPolling();
        state.importJob = { ...state.importJob, status: "failed", stage: "任务已丢失", error: "服务已重启，任务终止" };
        setImportBusy(false);
        renderImportJob();
        toast("服务已重启，任务终止", true);
        return;
      }
      applyImportJobData(data);
    }, 1500);
  }

  async function importJobControl(action) {
    const job = state.importJob;
    if (!job) return;
    try {
      const data = await api(`/api/import/jobs/${encodeURIComponent(job.job_id)}/${action}`, { method: "POST" });
      applyImportJobData(data);
      // stop 的响应若仍未终态（finalizing 兜底），立即再刷新一次
      if (action === "stop" && importTimer) await refreshImportJob(job.job_id);
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function refreshImportJob(jobId) {
    try {
      const data = await fetchImportJob(jobId);
      if (data.__not_found || state.importJob?.job_id !== jobId) return;
      applyImportJobData(data);
    } catch (_) { /* 下一次轮询再取 */ }
  }

  function handleImportJobFinished(job) {
    setImportBusy(false);
    if (job.status === "succeeded") {
      state.pendingUpload = null;
      if (job.mode === "auto") {
        state.autoResult = job.result || {};
        state.guidePackageId = state.autoResult.package_id || state.guidePackageId;
        if (state.route === "import") renderImport();
        return;
      }
      renderImportJob();
      renderImportJobSummary(job);
      return;
    }
    // failed / stopped：保留 pendingUpload，允许直接重新开始
    renderImportJob();
  }

  function setImportBusy(busy) {
    ["#autoUploadBtn", "#pdfInput", "#uploadMode", "#uploadTemplate"].forEach((selector) => {
      const el = $(selector);
      if (!el) return;
      el.disabled = busy;
    });
    const drop = $("#dropzone");
    if (drop) {
      drop.style.pointerEvents = busy ? "none" : "auto";
      drop.style.opacity = busy ? ".6" : "1";
    }
  }

  function prepareAutoUpload(file) {
    if (!file.name.toLowerCase().endsWith(".pdf")) return toast("仅支持 PDF 文件", true);
    if (activeImportJob()) return toast("已有导入任务正在进行，请等待完成或停止当前任务", true);
    state.pendingUpload = { file, kind: "auto", mode: "auto", templateId: "", graphFilename: "" };
    renderPendingUpload();
    toast(`已选择 ${file.name}；确认后点击「开始导入」`);
  }

  function prepareUpload(file) {
    if (!file.name.toLowerCase().endsWith(".pdf")) return toast("仅支持 PDF 文件", true);
    if (activeImportJob()) return toast("已有导入任务正在进行，请等待完成或停止当前任务", true);
    const graphSel = $("#uploadGraph");
    let graphFilename = "";
    if (graphSel && graphSel.value === "__new__") {
      const name = ($("#uploadGraphNew")?.value || "").trim();
      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*(\.json)?$/.test(name) || name.includes("..")) {
        return toast("新图谱文件名无效：仅限字母、数字、点、下划线、连字符", true);
      }
      graphFilename = name;
    } else if (graphSel && graphSel.value !== "follow") {
      graphFilename = graphSel.value;
    }
    state.pendingUpload = {
      file,
      kind: "template",
      mode: $("#uploadMode")?.value || "local",
      templateId: $("#uploadTemplate")?.value || "",
      graphFilename,
    };
    renderPendingUpload();
    toast(`已选择 ${file.name}；确认配置后点击「开始导入」`);
  }

  function renderImportResult() {
    const box = $("#importResult"); if (!box || !state.doc) return;
    const doc = state.doc;
    const domain = normalizeDomainClient(doc.extraction?.domain || doc.domain);
    const anchors = doc.entities.filter((e) => domain === "tcm" ? e.entity_type === "syndrome" : e.entity_type === "disease");
    const target = doc.targetDisease || (anchors[0] && anchors[0].code) || "";
    doc.targetDisease = target;
    const relationCount = (doc.suggested_relations || []).length;
    const tabs = [["lines", "原文定位"], ["entities", `抽取实体 ${doc.entities.length}`], ["relations", `候选关系 ${relationCount}`], ["rules", `候选规则 ${doc.suggested_rules.length}`]];
    box.innerHTML = `
      <div class="result-banner ok">${esc(doc.doc_title)} 解析成功：${doc.total_pages} 页，${doc.total_lines} 行，候选规则 ${doc.suggested_rules.length} 条。<div class="muted">方式：${doc.extraction_mode === "llm" ? "大模型解析" : "本地模板解析"}；领域：${domain === "tcm" ? "中医" : "西医"}；模板：${esc(doc.template_name || doc.template_id || "-")}</div></div>
      <div class="pipeline">
        <div class="step done"><strong>1 上传并解析</strong><span>原文定位、实体与候选规则</span></div>
        <div class="step"><strong>2 审核候选内容</strong><span>核对实体、关系和候选规则</span></div>
        <div class="step pending"><strong>3 生成知识包</strong><span>生成后进入知识包页</span></div>
        <div class="step pending"><strong>4 在知识包页入库</strong><span>预检后统一导入图谱和草稿规则</span></div>
      </div>
      ${(doc.warnings || []).length ? (() => {
        const list = doc.warnings;
        const render = (items) => `<ul class="issue-list">${items.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`;
        return `<div class="result-banner warning"><strong>解析告警 ${list.length} 条</strong><div class="muted">以下为自动修正或忽略记录，通常不影响已生成内容；请重点核对包含“失败”的条目。</div>${render(list.slice(0, 8))}${list.length > 8 ? `<details class="debug-panel"><summary>展开其余 ${list.length - 8} 条</summary>${render(list.slice(8))}</details>` : ""}</div>`;
      })() : ""}
      ${panel("审核与生成知识包", `
        <div class="inline-controls">
          <div class="field"><label>主目标${domain === "tcm" ? "证候" : "疾病"}（预检锚点）</label><select id="targetDisease">${anchors.map((d) => `<option value="${esc(d.code)}" ${d.code === target ? "selected" : ""}>${esc(d.name)} ${esc(d.code)}</option>`).join("")}</select></div>
          <div class="muted" style="flex-basis:100%">${anchors.length > 1
            ? `本次将导入包内全部 ${anchors.length} 个${domain === "tcm" ? "证候" : "疾病"}：${esc(anchors.map((d) => `${d.name}（${d.code}）`).join("、"))}。主目标仅用于预检锚点与诊断链路定位，不是唯一导入对象。`
            : "主目标仅用于预检锚点与诊断链路定位；包内全部实体、关系和规则都会导入。"}</div>
          ${domain === "tcm" ? `
          <div class="field"><label>目标图谱</label><select id="graphTargetMode">
            <option value="new" selected>新建独立中医图谱</option>
            <option value="existing">追加既有中医图谱</option>
          </select></div>
          <div class="field hidden" id="newGraphNameField"><label>图谱文件名（可选）</label><input id="newGraphName" placeholder="留空自动生成 KG-TCM-{doc_id}.json"></div>
          <div class="field hidden" id="graphTargetField"><label>既有图谱</label><select id="graphTargetFilename"><option value="">加载中</option></select></div>
          <div class="field hidden" id="legacyGraphField"><label>模板绑定图谱</label><input id="legacyGraphName" value="${esc(doc.graph_filename || doc.extraction?.graph_filename || "")}" readonly></div>` : ""}
          <button id="generatePackage" class="btn primary">生成知识包并进入审核</button>
        </div>
        <p class="muted">生成后进入「知识包」页查看自动预检并完成入库；此处不再单独导入图谱或规则。</p>
      `, "", "生成的是候选知识包；图谱和草稿规则只在知识包页审核后统一入库，不会自动启用")}
      <section class="panel"><div class="tabs">${tabs.map(([id, text]) => `<button class="tab ${state.importTab === id ? "active" : ""}" data-import-tab="${id}">${text}</button>`).join("")}</div><div id="importTabBody" class="panel-body"></div></section>
    `;
    $("#targetDisease").onchange = (e) => { doc.targetDisease = e.target.value; };
    const graphMode = $("#graphTargetMode");
    if (graphMode) {
      const renderGraphTargetFields = () => {
        $("#newGraphNameField")?.classList.toggle("hidden", graphMode.value !== "new");
        $("#graphTargetField")?.classList.toggle("hidden", graphMode.value !== "existing");
        $("#legacyGraphField")?.classList.toggle("hidden", graphMode.value !== "legacy");
      };
      graphMode.onchange = async () => {
        renderGraphTargetFields();
        if (graphMode.value !== "existing") return;
        try {
          state.targetGraphs = availableTcmTargetGraphs(await api("/api/kg/graphs?domain=tcm"));
          const selector = $("#graphTargetFilename");
          selector.innerHTML = state.targetGraphs.map((item) => `<option value="${esc(item.graph_filename)}">${esc(item.graph_filename)}（证候 ${item.syndromes || 0}）</option>`).join("");
        } catch (error) { toast(error.message, true); }
      };
      renderGraphTargetFields();
    }
    $("#generatePackage").onclick = generatePackage;
    box.querySelectorAll("[data-import-tab]").forEach((btn) => btn.onclick = () => { state.importTab = btn.dataset.importTab; renderImportResult(); });
    renderImportTab();
  }

  async function renderImportTab() {
    const body = $("#importTabBody"); const doc = state.doc; if (!body || !doc) return;
    if (state.importTab === "lines") {
      const pages = [...new Set(doc.chunks.map((c) => c.page_start))].sort((a, b) => a - b);
      const page = doc.linePage || pages[0] || 1;
      body.innerHTML = `<div class="inline-controls"><div class="field"><label>页码</label><select id="pageSelect">${pages.map((p) => `<option value="${p}" ${p === page ? "selected" : ""}>第 ${p} 页</option>`).join("")}</select></div></div><div id="lineList" class="table-wrap"><div class="empty">加载中</div></div>`;
      $("#pageSelect").onchange = async (e) => { doc.linePage = Number(e.target.value); renderImportTab(); };
      const requestToken = ++state.importRequestToken;
      const data = await api(`/api/pdf/${doc.doc_id}/lines?page=${page}`);
      const lineList = $("#lineList");
      if (requestToken !== state.importRequestToken || !lineList) return;
      lineList.innerHTML = table(["页", "行", "正文", "定位"], data.lines.map((l) => `<tr><td>${l.page}</td><td>${l.line}</td><td>${esc(l.text)}</td><td class="mono muted">p${l.page}:l${l.line}</td></tr>`));
    } else if (state.importTab === "entities") {
      body.innerHTML = table(["类型", "名称", "编码", "页 / 行", "证据引文"], doc.entities.map((e) => `<tr><td>${typeName[e.entity_type] || e.entity_type}</td><td>${esc(e.name)}</td><td class="mono">${esc(e.code || e.concept_code || "-")}</td><td class="nowrap">p${e.page}:l${e.line_start}</td><td><div class="quote">${esc(e.quote)}</div></td></tr>`));
    } else if (state.importTab === "relations") {
      const relations = doc.suggested_relations || [];
      body.innerHTML = relations.length ? table(["关系", "源实体", "目标实体", "证据概念", "页 / 行", "引文"], relations.map((r) => `
        <tr>
          <td><strong>${esc(r.relation_name || r.relation_code)}</strong><div class="mono muted">${esc(r.relation_code)}</div></td>
          <td>${esc(typeName[r.source_type] || r.source_type)}<div class="mono muted">${esc(r.source_code)}</div></td>
          <td>${esc(typeName[r.target_type] || r.target_type)}<div class="mono muted">${esc(r.target_code || r.target_concept_code)}</div></td>
          <td>${r.target_concept_code ? conceptChip(r.target_concept_code) : "-"}</td>
          <td class="nowrap">p${r.page}:l${r.line_start}-${r.line_end}</td>
          <td><div class="quote">${esc(r.quote)}</div></td>
        </tr>`)) : '<div class="empty">本次解析未生成显式候选关系；导入图谱时会按同片段实体和证据结构兜底补齐。</div>';
    } else {
      body.innerHTML = doc.suggested_rules.length ? table(["规则 / 结论", "条件逻辑", "溯源"], doc.suggested_rules.map((r) => `<tr><td><strong>${esc(r.name)}</strong><div class="muted">${badge(r.status)} 优先级 ${r.priority}</div></td><td><div class="quote">${esc(r.condition_text || "无条件下")}</div><details class="debug-panel"><summary>原始条件 JSON</summary><pre class="mono">${esc(JSON.stringify(r.conditions, null, 2))}</pre></details></td><td class="mono muted">${esc(r.source.section || "-")}</td></tr>`)) : '<div class="empty">未生成候选规则，可先导入实体</div>';
    }
  }

  async function loadExtractionSettings(force = false) {
    if (!force && state.templates.length && state.extractionConfig) return;
    const [templates, config] = await Promise.all([api("/api/templates?include_retired=true"), api("/api/extraction/config")]);
    state.templates = templates;
    state.extractionConfig = config;
    if (!config.default_template_id) {
      const fallback = templates.find((t) => t.is_default && t.status === "active") || templates.find((t) => t.is_default) || templates[0];
      state.extractionConfig.default_template_id = fallback ? fallback.template_id : "";
    }
  }

  function templateRow(t) {
    const entityEntryCount = (t.entity_types || []).reduce((sum, item) => sum + (item.entries || []).length, 0);
    return `
      <tr>
        <td><strong>${esc(t.name)}</strong><div class="mono muted">${esc(t.template_id)} v${t.version}</div></td>
        <td>${badge(normalizeDomainClient(t.domain))} ${esc(t.specialty || "")}</td>
        <td class="mono">${esc(t.graph_filename || "未绑定")}</td>
        <td>${(t.entity_types || []).length} 类 / ${entityEntryCount} 实体<br>${(t.relations || []).length} 关系 / ${(t.attributes || []).length} 属性</td>
        <td>${t.is_default ? '<span class="badge active">默认</span>' : ""} ${t.status === "retired" ? '<span class="badge retired">已停用</span>' : badge(t.status)}</td>
        <td><div class="actions"><button class="btn sm primary" data-template-structure="${esc(t.template_id)}">结构管理</button><button class="btn sm" data-template-edit="${esc(t.template_id)}">编辑</button><button class="btn sm" data-template-copy="${esc(t.template_id)}">复制</button><button class="btn sm" data-template-default="${esc(t.template_id)}" ${t.status === "retired" ? "disabled" : ""}>设默认</button><button class="btn sm danger" data-template-delete="${esc(t.template_id)}">删除</button></div></td>
      </tr>
    `;
  }

  async function renderTemplates() {
    await loadExtractionSettings(true);
    if (state.templateStructureId) return renderTemplateStructure();
    const config = state.extractionConfig;
    state.lastLlmProvider = config.llm.provider || "openai_compatible";
    content.innerHTML = `
      ${panel("解析服务", `
        <div class="form-grid">
          <div class="field"><label>默认解析方式</label><select id="defaultMode"><option value="local" ${config.default_mode === "local" ? "selected" : ""}>本地模板解析</option><option value="llm" ${config.default_mode === "llm" ? "selected" : ""}>大模型解析</option></select></div>
          <div class="field"><label>默认模板</label><select id="defaultTemplate">${state.templates.map((t) => `<option value="${esc(t.template_id)}" ${t.template_id === config.default_template_id ? "selected" : ""} ${t.status === "retired" ? "disabled" : ""}>${esc(t.name)}${t.status === "retired" ? "（已停用）" : ""}</option>`).join("")}</select></div>
          <div class="field"><label>Provider</label><select id="llmProvider">
            <option value="zhipu" ${(config.llm.provider || "") === "zhipu" ? "selected" : ""}>智谱 Coding Plan · OpenAI</option>
            <option value="zhipu_anthropic" ${config.llm.provider === "zhipu_anthropic" ? "selected" : ""}>智谱 Coding Plan · Anthropic</option>
            <option value="anthropic" ${config.llm.provider === "anthropic" ? "selected" : ""}>Anthropic 官方</option>
            <option value="cc_switch" ${config.llm.provider === "cc_switch" ? "selected" : ""}>CC-SWITCH 本地代理 · 公司网关</option>
            <option value="openai_compatible" ${(config.llm.provider || "openai_compatible") === "openai_compatible" ? "selected" : ""}>OpenAI Compatible</option>
          </select></div>
          <div class="field"><label>API 格式</label><select id="llmApiFormat">
            <option value="openai_chat" ${(config.llm.api_format || "openai_chat") === "openai_chat" ? "selected" : ""}>OpenAI Chat Completions</option>
            <option value="anthropic_messages" ${config.llm.api_format === "anthropic_messages" ? "selected" : ""}>Anthropic Messages</option>
          </select></div>
          <div class="field"><label>Anthropic 鉴权头</label><select id="llmAnthropicAuth">
            <option value="x-api-key" ${(config.llm.anthropic_auth || "x-api-key") === "x-api-key" ? "selected" : ""}>x-api-key</option>
            <option value="bearer" ${config.llm.anthropic_auth === "bearer" ? "selected" : ""}>Authorization Bearer</option>
            <option value="both" ${config.llm.anthropic_auth === "both" ? "selected" : ""}>两者都发送</option>
          </select></div>
          <div class="field"><label>服务地址</label><input id="llmBaseUrl" value="${esc(config.llm.base_url)}" placeholder="智谱 OpenAI：https://open.bigmodel.cn/api/coding/paas/v4；Anthropic：https://open.bigmodel.cn/api/anthropic"></div>
          <div class="field"><label>模型</label><input id="llmModel" value="${esc(config.llm.model)}" placeholder="智谱：glm-5.3-flash；公司网关：deepseek-flash；Anthropic：claude-sonnet-5"></div>
          <div class="field"><label>Host 头（反代需要时）</label><input id="llmHostHeader" value="${esc(config.llm.host_header || "")}" placeholder="例如 localhost"></div>
          <div class="field"><label>API Key</label><input id="llmApiKey" type="password" placeholder="${config.llm.api_key_configured ? `已配置 ${esc(config.llm.api_key_masked || "")}，留空保持不变` : "输入 API Key"}"></div>
          <div class="field"><label>Key 状态</label><input value="${config.llm.api_key_configured ? `已配置（${esc(config.llm.api_key_source || "")}）` : "未配置"}" readonly></div>
          <div class="field"><label>API Key 环境变量（可选回退）</label><input id="llmKeyEnv" value="${esc(config.llm.api_key_env)}"></div>
          <div class="field"><label>温度</label><input id="llmTemperature" type="number" step="0.1" min="0" max="2" value="${config.llm.temperature}"></div>
          <div class="field"><label>超时秒数</label><input id="llmTimeout" type="number" min="5" value="${config.llm.timeout_seconds}"></div>
          <div class="field"><label>单次字符上限</label><input id="llmMaxChars" type="number" min="1000" value="${config.llm.max_chunk_chars}"></div>
          <div class="field"><label>最大片段数</label><input id="llmMaxChunks" type="number" min="1" value="${config.llm.max_chunks}"></div>
        </div>
        <div class="actions"><button id="saveExtraction" class="btn primary">保存解析配置</button><button id="testLlm" class="btn">测试连接</button><button id="clearLlmKey" class="btn ghost">清除 API Key</button></div>
      `, "", "API Key 可保存在本地配置中，也可回退到环境变量；不会回显完整 Key")}
      ${panel("模板列表", table(["模板", "范围", "目标图谱", "结构规模", "状态", "操作"], state.templates.map(templateRow)), '<button id="newTemplate" class="btn primary">新建模板</button>', "本地解析读取别名/正则；大模型解析读取同一结构、提示指令和输出契约")}
    `;
    $("#newTemplate").onclick = () => openTemplateEditor(null);
    content.querySelectorAll("[data-template-structure]").forEach((b) => b.onclick = () => {
      state.templateStructureId = b.dataset.templateStructure;
      render();
    });
    $("#saveExtraction").onclick = saveExtractionConfig;
    $("#llmProvider").onchange = (event) => {
      const provider = event.target.value;
      const preset = (config.llm_presets || {})[provider] || {};
      const currentBase = $("#llmBaseUrl").value.trim();
      if (provider === "zhipu") {
        if (!currentBase || (!currentBase.includes("bigmodel.cn") && !currentBase.includes("z.ai"))) {
          $("#llmBaseUrl").value = preset.base_url || "https://open.bigmodel.cn/api/coding/paas/v4";
        }
        $("#llmApiFormat").value = "openai_chat";
        $("#llmHostHeader").value = "";
      }
      if (provider === "zhipu_anthropic") {
        if (!currentBase || (!currentBase.includes("bigmodel.cn") && !currentBase.includes("z.ai"))) {
          $("#llmBaseUrl").value = preset.base_url || "https://open.bigmodel.cn/api/anthropic";
        }
        $("#llmApiFormat").value = "anthropic_messages";
        $("#llmHostHeader").value = "";
      }
      if (provider === "anthropic") {
        if (!currentBase || currentBase.includes("bigmodel.cn") || currentBase.includes("z.ai")) {
          $("#llmBaseUrl").value = preset.base_url || "https://api.anthropic.com";
        }
        $("#llmApiFormat").value = "anthropic_messages";
        $("#llmHostHeader").value = "";
      }
      if (provider === "cc_switch") {
        $("#llmBaseUrl").value = preset.base_url || "http://127.0.0.1:15721";
        $("#llmApiFormat").value = "anthropic_messages";
        $("#llmAnthropicAuth").value = "bearer";
        $("#llmKeyEnv").value = preset.api_key_env || "ANTHROPIC_AUTH_TOKEN";
        $("#llmHostHeader").value = "";
      }
      if (provider === "openai_compatible") {
        $("#llmApiFormat").value = "openai_chat";
        $("#llmHostHeader").value = "";
      }
      const currentModel = $("#llmModel").value.trim();
      const previousProvider = state.lastLlmProvider || config.llm.provider || "openai_compatible";
      const previousPreset = (config.llm_presets || {})[previousProvider] || {};
      const previousDefault = String(previousPreset.model || "").trim().toLowerCase();
      // If the field still holds the previous provider's default (or is empty),
      // switch to this provider's default. Custom user models are preserved.
      if (preset.model && (!currentModel || (previousDefault && currentModel.toLowerCase() === previousDefault))) {
        $("#llmModel").value = preset.model;
      }
      state.lastLlmProvider = provider;
    };
    $("#llmApiFormat").onchange = (event) => {
      const format = event.target.value;
      const base = $("#llmBaseUrl").value.trim();
      if (format === "anthropic_messages") {
        if (base.includes("/api/coding/paas/v4")) {
          $("#llmBaseUrl").value = base.replace("/api/coding/paas/v4", "/api/anthropic");
        } else if (base.endsWith("/v1")) {
          $("#llmBaseUrl").value = base.slice(0, -3);
        }
      } else if (format === "openai_chat") {
        if (base.includes("/api/anthropic")) {
          $("#llmBaseUrl").value = base.replace("/api/anthropic", "/api/coding/paas/v4");
        }
      }
    };
    $("#testLlm").onclick = async () => {
      try {
        const result = await api("/api/extraction/test-llm", { method: "POST" });
        const actual = result.response_model || result.model || "未知";
        const configured = result.configured_model || result.model || "";
        toast(configured && configured !== actual
          ? `连接成功：实际模型 ${actual}（配置 ${configured}）`
          : `连接成功：${actual}`);
      } catch (error) { toast(error.message, true); }
    };
    $("#clearLlmKey").onclick = async () => {
      if (!confirm("确认清除本地保存的 API Key？环境变量回退仍然有效。")) return;
      state.extractionConfig = await api("/api/extraction/config", {
        method: "PUT",
        json: { config: { default_mode: $("#defaultMode").value, default_template_id: $("#defaultTemplate").value, llm: { api_key: "__CLEAR__" } } },
      });
      toast("API Key 已清除");
      renderTemplates();
    };
    content.querySelectorAll("[data-template-edit]").forEach((b) => b.onclick = () => openTemplateEditor(state.templates.find((t) => t.template_id === b.dataset.templateEdit)));
    content.querySelectorAll("[data-template-copy]").forEach((b) => b.onclick = () => {
      const source = state.templates.find((t) => t.template_id === b.dataset.templateCopy);
      const copied = JSON.parse(JSON.stringify(source));
      copied.template_id = `${source.template_id}_COPY_${Date.now().toString(36).toUpperCase()}`;
      copied.name = `${source.name} 副本`;
      copied.is_default = false;
      copied.version = 1;
      openTemplateEditor(copied, true);
    });
    content.querySelectorAll("[data-template-default]").forEach((b) => b.onclick = async () => {
      await api(`/api/templates/${b.dataset.templateDefault}/default`, { method: "POST" });
      await loadExtractionSettings(true);
      const template = state.templates.find((item) => item.template_id === b.dataset.templateDefault);
      if (normalizeDomainClient(template?.domain) === "tcm") {
        state.extractionConfig.domain_defaults = { ...(state.extractionConfig.domain_defaults || {}), tcm: b.dataset.templateDefault };
      } else {
        state.extractionConfig.default_template_id = b.dataset.templateDefault;
      }
      toast("默认模板已更新");
      renderTemplates();
    });
    content.querySelectorAll("[data-template-delete]").forEach((b) => b.onclick = async () => {
      if (!confirm("确认删除该解析模板？")) return;
      await api(`/api/templates/${b.dataset.templateDelete}`, { method: "DELETE" });
      toast("模板已删除");
      renderTemplates();
    });
  }

  function openTemplateEditor(template, isNew = false) {
    const base = template || {
      template_id: "", name: "", description: "", version: 1, is_default: false, status: "active", domain: "",
      graph_filename: "",
      entity_types: [], concept_codes: [], relation_hints: [], rule_hints: [], prompt_instructions: [], output_contract: {},
      relations: [], attributes: [],
    };
    isNew = isNew || !template;
    const editable = {
      entity_types: base.entity_types || [],
      concept_codes: base.concept_codes || [],
      relations: base.relations || [],
      attributes: base.attributes || [],
      relation_hints: base.relation_hints || [],
      rule_hints: base.rule_hints || [],
      prompt_instructions: base.prompt_instructions || [],
      output_contract: base.output_contract || {},
      capabilities: base.capabilities || [],
      rule_generation: base.rule_generation || [],
      standard_system: base.standard_system || "",
    };
    $("#editorBody").innerHTML = `
      <h3>${isNew ? "新建解析模板" : "编辑解析模板"}</h3>
      <div class="form-grid">
        <div class="field"><label>模板 ID</label><input id="templateId" value="${esc(base.template_id)}" ${template && !isNew ? "readonly" : ""}></div>
        <div class="field"><label>模板名称</label><input id="templateName" value="${esc(base.name)}"></div>
        <div class="field"><label>领域</label><select id="templateDomain"><option value="western" ${normalizeDomainClient(base.domain) === "western" ? "selected" : ""}>西医</option><option value="tcm" ${normalizeDomainClient(base.domain) === "tcm" ? "selected" : ""}>中医</option></select></div>
        <div class="field"><label>专科</label><input id="templateSpecialty" value="${esc(base.specialty || "")}" placeholder="例如 心血管内科 / 中医内科"></div>
        <div class="field"><label>目标图谱文件</label><input id="templateGraph" value="${esc(base.graph_filename || "")}" placeholder="例如 KG-LUNG_CANCER_V1.json"></div>
        <div class="field"><label>版本</label><input id="templateVersion" type="number" min="1" value="${base.version || 1}"></div>
        <div class="field"><label>状态</label><select id="templateStatus">${["active", "draft", "retired"].map((s) => `<option value="${s}" ${base.status === s ? "selected" : ""}>${s}</option>`).join("")}</select></div>
        <div class="field"><label>设为默认</label><select id="templateDefault"><option value="true" ${base.is_default ? "selected" : ""}>是</option><option value="false" ${!base.is_default ? "selected" : ""}>否</option></select></div>
        <div class="field half"><label>描述</label><input id="templateDescription" value="${esc(base.description)}"></div>
        <div class="field wide"><label>模板结构 / 提示 / 输出契约 JSON</label><textarea id="templateJson" class="code" style="min-height:420px">${esc(JSON.stringify(editable, null, 2))}</textarea></div>
      </div>`;
    const dialog = $("#editorDialog"); dialog.showModal();
    $("#editorSave").onclick = async () => {
      try {
        const structured = JSON.parse($("#templateJson").value);
        const payload = {
          template_id: $("#templateId").value.trim(),
          name: $("#templateName").value.trim(),
          description: $("#templateDescription").value.trim(),
          domain: $("#templateDomain").value,
          specialty: $("#templateSpecialty").value.trim(),
          graph_filename: $("#templateGraph").value.trim(),
          version: Number($("#templateVersion").value || 1),
          status: $("#templateStatus").value,
          is_default: $("#templateDefault").value === "true",
          ...structured,
        };
        const result = template && !isNew
          ? await api(`/api/templates/${template.template_id}`, { method: "PUT", json: { template: payload } })
          : await api("/api/templates", { json: { template: payload } });
        dialog.close(); state.templateEditorId = result.template_id; toast("解析模板已保存"); renderTemplates();
      } catch (error) { toast(error.message, true); }
    };
    $("#editorCancel").onclick = () => dialog.close();
  }

  async function renderTemplateStructure() {
    await loadBase();
    const template = await api(`/api/templates/${state.templateStructureId}/structure`);
    const back = '<button id="templateBack" class="btn ghost">返回模板列表</button>';
    const conceptOption = (code) => {
      const item = state.vocab.find((v) => v.code === code);
      return item ? `${item.name_cn} / ${item.code}` : code || "不绑定概念";
    };
    const privateEntities = template.entity_types.flatMap((owner) =>
      (owner.private_entries || []).map((item) => ({ owner, item }))
    ).sort((left, right) => {
      const typeOrder = String(left.owner.entity_type).localeCompare(String(right.owner.entity_type), "zh-Hans-CN");
      return typeOrder || String(left.item.name).localeCompare(String(right.item.name), "zh-Hans-CN");
    });
    const privateEntityTypes = template.entity_types.filter((item) => !item.vocabulary);
    const typeCounts = privateEntities.reduce((counts, { owner }) => {
      counts.set(owner.entity_type, (counts.get(owner.entity_type) || 0) + 1);
      return counts;
    }, new Map());
    const entityTypeFilters = [...typeCounts.entries()]
      .sort(([left], [right]) => left.localeCompare(right, "zh-Hans-CN"))
      .map(([value, count]) => ({ value, label: `${typeName[value] || value}（${count}）` }));
    const entityTableKey = `template-private-entities-${template.template_id}`;
    const entityItems = privateEntities.map(({ owner, item }) => ({
      text: [item.code, item.name, ...(item.aliases || []), owner.label, owner.entity_type].join(" "),
      values: { entity_type: owner.entity_type },
      html: `<tr><td>${esc(typeName[owner.entity_type] || owner.label || owner.entity_type)}</td><td class="mono">${esc(item.code)}</td><td>${esc(item.name)}</td><td><div class="quote">${esc((item.aliases || []).join(" / "))}</div></td><td><div class="actions"><button class="btn sm" data-struct-kind="entity" data-struct-key="${esc(owner.entity_type)}/${esc(item.code)}" data-struct-mode="edit">编辑</button><button class="btn sm danger" data-struct-kind="entity" data-struct-key="${esc(owner.entity_type)}/${esc(item.code)}" data-struct-mode="delete">删除</button></div></td></tr>`,
    }));
    const attributeTableKey = `template-attributes-${template.template_id}`;
    const attributesSorted = [...template.attributes].sort((left, right) => {
      const typeOrder = String(left.owner_entity_type).localeCompare(String(right.owner_entity_type), "zh-Hans-CN");
      return typeOrder || String(left.attribute_code).localeCompare(String(right.attribute_code), "zh-Hans-CN");
    });
    const attrTypeCounts = template.attributes.reduce((counts, attr) => {
      counts.set(attr.owner_entity_type, (counts.get(attr.owner_entity_type) || 0) + 1);
      return counts;
    }, new Map());
    const attributeTypeFilters = [...attrTypeCounts.entries()]
      .sort(([left], [right]) => left.localeCompare(right, "zh-Hans-CN"))
      .map(([value, count]) => ({ value, label: `${typeName[value] || value}（${count}）` }));
    const attributeItems = attributesSorted.map((item) => ({
      text: [item.attribute_code, item.name, item.owner_entity_type, typeName[item.owner_entity_type] || "", item.data_type, item.description].join(" "),
      values: { owner_entity_type: item.owner_entity_type },
      html: `<tr><td class="mono">${esc(item.attribute_code)}</td><td>${esc(item.name)}</td><td>${esc(typeName[item.owner_entity_type] || item.owner_entity_type)} <span class="mono muted">${esc(item.owner_entity_type)}</span></td><td>${esc(item.data_type)}</td><td>${item.required ? '<span class="badge active">必填</span>' : "否"}</td><td class="mono">${esc((item.allowed_values || []).join(", ")) || "-"}</td><td>${item.concept_code ? conceptChip(item.concept_code) : "-"}</td><td><div class="quote">${esc(item.description)}</div></td><td><div class="actions"><button class="btn sm" data-struct-kind="attribute" data-struct-key="${esc(item.attribute_code)}" data-struct-mode="edit">编辑</button><button class="btn sm danger" data-struct-kind="attribute" data-struct-key="${esc(item.attribute_code)}" data-struct-mode="delete">删除</button></div></td></tr>`,
    }));
    const addPrivateEntity = privateEntityTypes.length
      ? '<button id="addEntity" class="btn primary">新增私有实体</button>'
      : "";
    content.innerHTML = `
      ${panel(`${template.name} · 结构管理`, `
        <div class="inline-controls">
          ${back}
          <button id="editTemplateMeta" class="btn">编辑模板信息</button>
          <div class="field"><label>状态</label><input readonly value="${esc(template.status)}"></div>
          <div class="field"><label>概念边界</label><input readonly value="${template.concept_codes.length} 个概念"></div>
        </div>
      `, "", "本页是 Schema 层：实体类型、关系、属性构成解析契约；具体实体在「实体词典」（术语层）维护；医院阈值只保存在标准管理中")}
      ${panel("实体类型", table(["类型编码", "名称", "编码字段", "属性", "私有 / 共享实体", "词表", "操作"], template.entity_types.map((item) => {
        const attrCount = template.attributes.filter((a) => a.owner_entity_type === item.entity_type).length;
        return `
        <tr><td class="mono">${esc(item.entity_type)}</td><td>${esc(item.label)}</td><td class="mono">${esc(item.code_field)}</td><td><button type="button" class="btn sm ghost" data-attr-filter="${esc(item.entity_type)}" title="点击在下方属性面板中过滤该类型的属性定义">${attrCount ? `${attrCount} 个` : "0 个"}</button></td><td>${item.private_entry_count ?? (item.private_entries || item.entries).length} 私有${item.shared_entry_count ? ` <span class="muted">/ ${item.shared_entry_count} 共享</span>` : ""}</td><td>${item.vocabulary ? `<button class="badge active" data-goto-vocab="${esc(item.entity_type)}" title="点击进入实体词典管理">共享词表</button>` : '<span class="badge draft" title="实体内嵌在模板中">模板私有</span>'}</td><td><div class="actions"><button class="btn sm" data-struct-kind="entity-type" data-struct-key="${esc(item.entity_type)}" data-struct-mode="edit">编辑</button><button class="btn sm danger" data-struct-kind="entity-type" data-struct-key="${esc(item.entity_type)}" data-struct-mode="delete">删除</button></div></td></tr>`;
      })), '<button id="addEntityType" class="btn primary">新增实体类型</button>', "实体类型决定图谱节点族和输出类型；「属性」列可点击跳转到属性面板查看该类型的属性定义")}
      ${pagedPanel({
        key: entityTableKey,
        title: "实体（仅模板私有）",
        description: "共享词表中已有的实体不再重复展示，请在「实体词典」统一维护；这里只管理模板自有的实体。",
        headers: ["类型", "编码", "名称", "别名 / 正则", "操作"],
        items: entityItems,
        filters: [{ key: "entity_type", label: "实体类型", options: entityTypeFilters }],
        placeholder: "搜索编码、名称或别名…",
        emptyText: entityItems.length ? "没有匹配的私有实体" : "当前模板没有私有实体，共享词条请到实体词典维护",
        actions: addPrivateEntity,
      })}
      ${panel("关系", table(["编码", "名称", "源类型", "目标类型", "限定编码", "证据概念", "说明", "操作"], template.relations.map((item) => `
        <tr><td class="mono">${esc(item.relation_code)}</td><td>${esc(item.name)}</td><td>${esc(item.source_entity_type)}${item.source_code ? ` / ${esc(item.source_code)}` : ""}</td><td>${esc(item.target_entity_type)}${item.target_code ? ` / ${esc(item.target_code)}` : ""}</td><td class="mono">${esc(item.cardinality)}</td><td>${item.concept_code ? conceptChip(item.concept_code) : "-"}</td><td><div class="quote">${esc(item.extraction_hint)}</div></td><td><div class="actions"><button class="btn sm" data-struct-kind="relation" data-struct-key="${esc(item.relation_code)}" data-struct-mode="edit">编辑</button><button class="btn sm danger" data-struct-kind="relation" data-struct-key="${esc(item.relation_code)}" data-struct-mode="delete">删除</button></div></td></tr>`
      )), '<button id="addRelation" class="btn primary">新增关系</button>', "关系约束图谱导入和大模型抽取，避免自由生成无边界的边")}
      <div id="attributePanel">
      ${pagedPanel({
        key: attributeTableKey,
        title: "属性",
        description: "属性定义抽取字段契约，可绑定受控概念，但不承载医院阈值；按所属实体类型分组排序。",
        headers: ["编码", "名称", "所属实体类型", "数据类型", "必填", "允许值", "绑定概念", "说明", "操作"],
        items: attributeItems,
        filters: [{ key: "owner_entity_type", label: "实体类型", options: attributeTypeFilters }],
        placeholder: "搜索编码、名称或说明…",
        emptyText: attributeItems.length ? "没有匹配的属性" : "当前模板还没有属性定义，点击「新增属性」创建",
        actions: '<button id="addAttribute" class="btn primary">新增属性</button>',
      })}
      </div>
    `;
    $("#templateBack").onclick = () => { state.templateStructureId = ""; render(); };
    content.querySelectorAll("[data-goto-vocab]").forEach((b) => b.onclick = () => {
      state.entityVocabType = b.dataset.gotoVocab;
      navigate("entityVocab");
    });
    bindPagedTable(entityTableKey);
    bindPagedTable(attributeTableKey);
    content.querySelectorAll("[data-attr-filter]").forEach((button) => button.onclick = () => {
      const ts = tableStateFor(attributeTableKey);
      ts.filters.owner_entity_type = ts.filters.owner_entity_type === button.dataset.attrFilter ? "" : button.dataset.attrFilter;
      ts.page = 1;
      const select = document.querySelector(`.table-filter[data-table-filter="${attributeTableKey}"][data-filter-key="owner_entity_type"]`);
      if (select) select.value = ts.filters.owner_entity_type;
      renderTableBody(attributeTableKey);
      const mount = $("#attributePanel");
      if (mount) mount.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    $("#editTemplateMeta").onclick = () => openTemplateEditor(template);
    $("#addEntityType").onclick = () => openTemplateItemEditor("entity-type", null, false, { template });
    if ($("#addEntity")) $("#addEntity").onclick = () => openTemplateItemEditor("entity", null, true, { privateOnly: true, template });
    $("#addRelation").onclick = () => openTemplateItemEditor("relation", null, false, { template });
    $("#addAttribute").onclick = () => openTemplateItemEditor("attribute", null, false, { template });
    const handleStructureAction = async (button) => {
      const kind = button.dataset.structKind, key = button.dataset.structKey, mode = button.dataset.structMode;
      if (mode === "delete") {
        if (!confirm("确认删除该模板结构项？")) return;
        if (kind === "entity") {
          const [entityType, code] = key.split("/");
          await api(`/api/templates/${template.template_id}/entities/${entityType}/${code}`, { method: "DELETE" });
        } else {
          await api(`/api/templates/${template.template_id}/${kind === "entity-type" ? "entity-types" : `${kind}s`}/${encodeURIComponent(key)}`, { method: "DELETE" });
        }
        toast("模板结构项已删除");
        renderTemplateStructure();
      } else {
        const source = kind === "entity-type"
          ? template.entity_types.find((item) => item.entity_type === key)
          : kind === "entity"
            ? (() => { const [type, code] = key.split("/"); const owner = template.entity_types.find((item) => item.entity_type === type); return (owner.private_entries || owner.entries).find((item) => item.code === code); })()
            : template[`${kind}s`].find((item) => item[kind === "relation" ? "relation_code" : "attribute_code"] === key);
        openTemplateItemEditor(kind, kind === "entity" ? { ...source, __key: key } : source, false, { template });
      }
    };
    content.querySelectorAll("[data-struct-mode]").forEach((button) => {
      if (button.dataset.structKind === "entity") return;
      button.onclick = () => handleStructureAction(button);
    });
    const entityTableBody = document.querySelector(`[data-table-body="${entityTableKey}"]`);
    if (entityTableBody) entityTableBody.addEventListener("click", (event) => {
      const button = event.target.closest('[data-struct-kind="entity"][data-struct-mode]');
      if (button) handleStructureAction(button);
    });
  }

  function openTemplateItemEditor(kind, item, isNew = false, options = {}) {
    const value = item || {};
    isNew = isNew || !item;
    const option = (selected, current, label) => `<option value="${esc(current)}" ${selected === current ? "selected" : ""}>${esc(label)}</option>`;
    const structureTypes = state.templates.find((t) => t.template_id === state.templateStructureId)?.entity_types || [];
    const entityTypes = options.privateOnly ? structureTypes.filter((t) => !t.vocabulary) : structureTypes;
    const entityOptions = (selected) => entityTypes.map((t) => option(selected, t.entity_type, `${t.label} / ${t.entity_type}`)).join("");
    const conceptOptions = (selected = "") => `<option value="" ${!selected ? "selected" : ""}>不绑定概念</option>${state.vocab.map((v) => option(selected, v.code, `${v.name_cn} / ${v.code}`)).join("")}`;
    const label = { "entity-type": "实体类型", entity: "实体", relation: "关系", attribute: "属性" }[kind];
    let fields = "";
    const structureTemplate = options.template || state.templates.find((t) => t.template_id === state.templateStructureId);
    const templateAttributesOf = (entityType) => (structureTemplate?.attributes || []).filter((a) => a.owner_entity_type === entityType);
    if (kind === "entity-type") {
      fields = `
      <div class="field"><label>类型编码</label><input name="entity_type" value="${esc(value.entity_type || "")}" ${!isNew ? "readonly" : ""}></div>
      <div class="field"><label>显示名称</label><input name="label" value="${esc(value.label || "")}"></div>
      <div class="field"><label>编码字段</label><input name="code_field" value="${esc(value.code_field || "code")}"></div>
      <div class="field"><label>动态发现</label><select name="discovery"><option value="false" ${!value.discovery ? "selected" : ""}>否</option><option value="true" ${value.discovery ? "selected" : ""}>是（从指南文本自动发现）</option></select></div>
      <div class="field"><label>实体词表</label><select name="vocabulary"><option value="false" ${!value.vocabulary ? "selected" : ""}>模板私有（内嵌实体）</option><option value="true" ${value.vocabulary ? "selected" : ""}>共享词表（data/vocab/entities，跨模板共用）</option></select></div>
      <div class="field wide"><label>抽取提示</label><input name="extraction_hint" value="${esc(value.extraction_hint || "")}" placeholder="大模型抽取该类型实体时的指引说明"></div>
      <div class="field wide"><label>描述</label><input name="description" value="${esc(value.description || "")}" placeholder="该实体类型的用途说明"></div>`;
      if (!isNew) {
        const attrs = templateAttributesOf(value.entity_type);
        fields += `
      <div class="field wide"><label>该类型的属性定义（${attrs.length} 个）</label>${attrs.length ? table(["编码", "名称", "数据类型", "必填", "允许值", "操作"], attrs.map((a) => `
        <tr><td class="mono">${esc(a.attribute_code)}</td><td>${esc(a.name)}</td><td>${esc(a.data_type)}</td><td>${a.required ? "是" : "否"}</td><td class="mono">${esc((a.allowed_values || []).join(", ")) || "-"}</td><td><button type="button" class="btn sm" data-inline-attr="${esc(a.attribute_code)}">编辑属性</button></td></tr>`)) : '<div class="muted">该类型暂无属性定义；关闭对话框后在主页面「新增属性」并选择该实体类型</div>'}</div>`;
      }
    }
    if (kind === "entity") {
      const owner = templateOwnerByEntityKey(value.__key);
      fields = `
        <div class="field"><label>实体类型</label><select name="entity_type">${entityOptions(value.entity_type || owner || "disease")}</select></div>
        <div class="field"><label>实体编码</label><input name="code" value="${esc(value.code || "")}" ${!isNew ? "readonly" : ""}></div>
        <div class="field"><label>实体名称</label><input name="name" value="${esc(value.name || "")}"></div>
        <div class="field wide"><label>别名 / 正则（每行一个）</label><textarea name="aliases">${esc((value.aliases || []).join("\n"))}</textarea></div>
        <div class="field wide"><label>默认属性（抽取与导入时随实体写入，按该类型的属性契约填写）</label><div id="entityAttrInputs"></div></div>`;
    }
    if (kind === "relation") fields = `
      <div class="field"><label>关系编码</label><input name="relation_code" value="${esc(value.relation_code || "")}" ${!isNew ? "readonly" : ""}></div>
      <div class="field"><label>关系名称</label><input name="name" value="${esc(value.name || "")}"></div>
      <div class="field"><label>源实体类型</label><select name="source_entity_type">${entityOptions(value.source_entity_type)}</select></div>
      <div class="field"><label>目标实体类型</label><select name="target_entity_type">${entityOptions(value.target_entity_type)}</select></div>
      <div class="field"><label>源实体编码（可选）</label><input name="source_code" value="${esc(value.source_code || "")}"></div>
      <div class="field"><label>目标实体编码（可选）</label><input name="target_code" value="${esc(value.target_code || "")}"></div>
      <div class="field"><label>基数</label><input name="cardinality" value="${esc(value.cardinality || "N:M")}"></div>
      <div class="field"><label>证据概念</label><select name="concept_code">${conceptOptions(value.concept_code)}</select></div>
      <div class="field"><label>导入图谱证据</label><select name="generate_evidence"><option value="true" ${value.generate_evidence !== false ? "selected" : ""}>是</option><option value="false" ${value.generate_evidence === false ? "selected" : ""}>否</option></select></div>
      <div class="field wide"><label>抽取提示</label><input name="extraction_hint" value="${esc(value.extraction_hint || "")}"></div>`;
    if (kind === "attribute") fields = `
      <div class="field"><label>属性编码</label><input name="attribute_code" value="${esc(value.attribute_code || "")}" ${!isNew ? "readonly" : ""}></div>
      <div class="field"><label>属性名称</label><input name="name" value="${esc(value.name || "")}"></div>
      <div class="field"><label>所属实体类型</label><select name="owner_entity_type">${entityOptions(value.owner_entity_type)}</select></div>
      <div class="field"><label>数据类型</label><select name="data_type">${["string", "number", "boolean", "enum", "concept", "date"].map((v) => option(value.data_type || "string", v, v)).join("")}</select></div>
      <div class="field"><label>必填</label><select name="required"><option value="false" ${!value.required ? "selected" : ""}>否</option><option value="true" ${value.required ? "selected" : ""}>是</option></select></div>
      <div class="field"><label>允许值（逗号分隔）</label><input name="allowed_values" value="${esc((value.allowed_values || []).join(", "))}"></div>
      <div class="field"><label>绑定概念</label><select name="concept_code">${conceptOptions(value.concept_code)}</select></div>
      <div class="field wide"><label>说明</label><input name="description" value="${esc(value.description || "")}"></div>`;
    $("#editorBody").innerHTML = `<h3>${isNew ? `新增${label}` : `编辑${label}`}</h3><div class="form-grid">${fields}</div>`;
    const dialog = $("#editorDialog"); dialog.showModal();
    let entityAttrDefs = [];
    // 实体默认属性：跟随所选实体类型动态渲染输入控件（enum→下拉、boolean→下拉、number→数字）。
    const renderEntityAttrInputs = () => {
      const mount = $("#entityAttrInputs");
      if (!mount) return;
      const typeSelect = $("#editorBody").querySelector('select[name="entity_type"]');
      entityAttrDefs = templateAttributesOf(typeSelect ? typeSelect.value : "");
      if (!entityAttrDefs.length) {
        mount.innerHTML = '<div class="muted">该实体类型暂无属性定义</div>';
        return;
      }
      const currentAttrs = value.attributes || {};
      mount.innerHTML = entityAttrDefs.map((a) => {
        const raw = currentAttrs[a.attribute_code];
        const strVal = raw === undefined || raw === null ? "" : String(raw);
        const title = `${esc(a.name || a.attribute_code)} <span class="mono muted">${esc(a.attribute_code)} · ${esc(a.data_type)}</span>${a.required ? ' <span class="badge active">必填</span>' : ""}`;
        if (a.data_type === "enum") {
          return `<div class="field"><label>${title}</label><select name="attr_${esc(a.attribute_code)}"><option value="">（未设置）</option>${(a.allowed_values || []).map((v) => `<option value="${esc(v)}" ${strVal === v ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>`;
        }
        if (a.data_type === "boolean") {
          return `<div class="field"><label>${title}</label><select name="attr_${esc(a.attribute_code)}"><option value="">（未设置）</option><option value="true" ${strVal === "true" ? "selected" : ""}>true</option><option value="false" ${strVal === "false" ? "selected" : ""}>false</option></select></div>`;
        }
        const numberAttr = a.data_type === "number" ? ' type="number" step="any"' : "";
        return `<div class="field"><label>${title}</label><input name="attr_${esc(a.attribute_code)}"${numberAttr} value="${esc(strVal)}" placeholder="${esc(a.description || "")}"></div>`;
      }).join("");
    };
    if (kind === "entity") {
      renderEntityAttrInputs();
      const typeSelect = $("#editorBody").querySelector('select[name="entity_type"]');
      if (typeSelect) typeSelect.addEventListener("change", renderEntityAttrInputs);
    }
    // 实体类型对话框内嵌属性列表：跳转到属性编辑器。
    $("#editorBody").querySelectorAll("[data-inline-attr]").forEach((button) => {
      button.onclick = () => {
        const attr = templateAttributesOf(value.entity_type).find((a) => a.attribute_code === button.dataset.inlineAttr);
        if (!attr) return;
        dialog.close();
        openTemplateItemEditor("attribute", attr, false, options);
      };
    });
    $("#editorSave").onclick = async () => {
      const form = $("#editorBody");
      try {
        // Keep fields the form does not expose (entries, flags) from the
        // source item so editing one property does not wipe the others.
        const base = item && !isNew ? { ...item } : {};
        delete base.__key;
        const itemPayload = { ...base, ...Object.fromEntries([...form.querySelectorAll("input,select,textarea")].map((input) => [input.name, input.value])) };
        // 默认属性输入以 attr_ 前缀命名，需从扁平字段收敛回 attributes 字典；
        // 留空表示清除该默认属性，number/boolean 按契约转型。
        Object.keys(itemPayload).forEach((key) => { if (key.startsWith("attr_")) delete itemPayload[key]; });
        if (kind === "entity") {
          const attrs = {};
          for (const a of entityAttrDefs) {
            const input = form.querySelector(`[name="attr_${a.attribute_code}"]`);
            if (!input) continue;
            const raw = String(input.value ?? "").trim();
            if (raw === "") continue;
            attrs[a.attribute_code] = a.data_type === "number" ? Number(raw) : a.data_type === "boolean" ? raw === "true" : raw;
          }
          itemPayload.attributes = attrs;
        }
        if (itemPayload.aliases != null) itemPayload.aliases = itemPayload.aliases.split(/\n+/).map((v) => v.trim()).filter(Boolean);
        if (itemPayload.allowed_values != null) itemPayload.allowed_values = itemPayload.allowed_values.split(",").map((v) => v.trim()).filter(Boolean);
        if (itemPayload.required != null) itemPayload.required = itemPayload.required === "true";
        if (itemPayload.generate_evidence != null) itemPayload.generate_evidence = itemPayload.generate_evidence === "true";
        if (itemPayload.discovery != null) itemPayload.discovery = itemPayload.discovery === "true";
        if (itemPayload.vocabulary != null) itemPayload.vocabulary = itemPayload.vocabulary === "true";
        const paths = {
          "entity-type": ["entity-types", itemPayload.entity_type],
          entity: ["entities", `${itemPayload.entity_type}/${itemPayload.code}`],
          relation: ["relations", itemPayload.relation_code],
          attribute: ["attributes", itemPayload.attribute_code],
        };
        const [segment, key] = paths[kind];
        const url = isNew ? `/api/templates/${state.templateStructureId}/${segment}` : `/api/templates/${state.templateStructureId}/${segment}/${encodeURIComponent(key)}`;
        const request = { method: isNew ? "POST" : "PUT", json: { item: itemPayload } };
        await api(url, request);
        dialog.close(); toast("模板结构已保存"); renderTemplateStructure();
      } catch (error) { toast(error.message, true); }
    };
    $("#editorCancel").onclick = () => dialog.close();
  }

  function templateOwnerByEntityKey(key) {
    if (!key || !key.includes("/")) return "";
    return key.split("/")[0];
  }

  async function renderEntityVocab() {
    await loadBase();
    const dictionaries = await api("/api/vocab/entities");
    if (!state.entityVocabType || !dictionaries.some((d) => d.entity_type === state.entityVocabType)) {
      state.entityVocabType = dictionaries.find((d) => d.entity_type === "disease")?.entity_type
        || dictionaries[0]?.entity_type
        || "";
    }
    const selected = dictionaries.find((d) => d.entity_type === state.entityVocabType) || null;
    const entries = selected ? await api(`/api/vocab/entities/${encodeURIComponent(selected.entity_type)}`) : [];
    const totalEntries = dictionaries.reduce((sum, d) => sum + d.entry_count, 0);
    const templateCount = new Set(dictionaries.flatMap((d) => d.referencing_templates.map((t) => t.template_id))).size;
    const dictLabel = (type) => typeName[type] || type;
    const specialtiesOf = (item) => (item.specialties?.length ? item.specialties : [item.specialty || "未分类"]);
    const diseasesOf = (item) => (item.diseases?.length ? item.diseases : (item.disease_tags?.length ? item.disease_tags : ["未分类"]));
    const uniqueSorted = (values) => [...new Set(values.filter(Boolean).map(String))].sort((a, b) => a.localeCompare(b, "zh-Hans-CN"));
    const countBy = (values) => values.reduce((counts, value) => counts.set(value, (counts.get(value) || 0) + 1), new Map());
    const specialtyOptions = uniqueSorted(entries.flatMap(specialtiesOf));
    const diseaseOptions = uniqueSorted(entries.flatMap(diseasesOf));
    const domainOptions = uniqueSorted(entries.map((item) => item.domain === "tcm" ? "tcm" : item.domain === "western" ? "western" : "shared"));
    const specialtyCounts = countBy(entries.flatMap(specialtiesOf));
    const diseaseCounts = countBy(entries.flatMap(diseasesOf));
    const domainCounts = countBy(entries.map((item) => item.domain === "tcm" ? "tcm" : item.domain === "western" ? "western" : "shared"));
    const domainLabels = { western: "西医", tcm: "中医", shared: "共享" };
    const classifiedCount = entries.filter((item) => specialtiesOf(item).some((value) => value !== "未分类") || diseasesOf(item).some((value) => value !== "未分类")).length;
    const tableKey = `entity-vocab-${selected?.entity_type || "none"}`;
    const tagList = (values, limit = 2) => {
      const visible = values.slice(0, limit);
      const more = values.length - visible.length;
      return `${visible.map((value) => `<span class="badge">${esc(value)}</span>`).join(" ")}${more > 0 ? ` <span class="muted">+${more}</span>` : ""}`;
    };
    const entryItems = entries.map((item) => {
      const specialties = specialtiesOf(item);
      const diseases = diseasesOf(item);
      const domain = item.domain === "tcm" ? "tcm" : item.domain === "western" ? "western" : "shared";
      return {
        text: [item.code, item.name, ...(item.aliases || []), ...specialties, ...diseases, item.standard_system, item.standard_code].join(" "),
        values: { domain, specialty: specialties, disease: diseases },
        html: `<tr>
          <td class="mono">${esc(item.code)}</td>
          <td><strong>${esc(item.name)}</strong></td>
          <td>${badge(domain)}</td>
          <td>${tagList(specialties)}</td>
          <td>${tagList(diseases)}</td>
          <td>${esc(item.standard_system || "-")}${item.standard_code ? `<div class="mono muted">${esc(item.standard_code)}</div>` : ""}</td>
          <td><div class="quote">${esc((item.aliases || []).join(" / "))}</div></td>
          <td><div class="actions"><button class="btn sm" data-entry-edit="${esc(item.code)}">编辑</button><button class="btn sm danger" data-entry-delete="${esc(item.code)}">删除</button></div></td>
        </tr>`,
      };
    });
    const filters = [
      { key: "domain", label: "领域", options: domainOptions.map((value) => ({ value, label: `${domainLabels[value] || value}（${domainCounts.get(value) || 0}）` })) },
      { key: "specialty", label: "专科", options: specialtyOptions.map((value) => ({ value, label: `${value}（${specialtyCounts.get(value) || 0}）` })) },
    ];
    if (selected && selected.entity_type !== "disease") {
      filters.push({ key: "disease", label: "关联疾病", options: diseaseOptions.map((value) => ({ value, label: `${value}（${diseaseCounts.get(value) || 0}）` })) });
    }
    const activeFilters = filters.filter((filter) => filter.options.length > 1);
    content.innerHTML = `
      ${panel("三层架构", `<div class="pipeline">
        <div class="step done"><strong>Schema 层 · 解析模板</strong><span>实体类型、关系、属性契约</span></div>
        <div class="step done"><strong>术语层 · 实体词典（本页）</strong><span>共享实体字典 data/vocab/entities</span></div>
        <div class="step done"><strong>实例层 · 知识图谱</strong><span>文档导入后生成的节点与证据</span></div>
      </div>`, "", "模板只定义“有哪些类型和关系”（Schema 层）；具体实体统一存放在共享词典（术语层）；图谱节点按导入文档生成（实例层）")}
      <div class="grid c4">
        <div class="kpi"><span>词典数</span><strong>${dictionaries.length}</strong><small>按实体类型分文件</small></div>
        <div class="kpi"><span>实体总数</span><strong>${totalEntries}</strong><small>共享术语条目</small></div>
        <div class="kpi"><span>引用模板数</span><strong>${templateCount}</strong><small>通过共享词表引用词典</small></div>
        <div class="kpi"><span>当前词典</span><strong>${selected ? entries.length : 0}</strong><small>${esc(selected ? dictLabel(selected.entity_type) : "-")} 条目 · 已分类 ${classifiedCount}</small></div>
      </div>
      ${panel("词典选择", `
        <div class="inline-controls">
          <div class="field"><label>实体类型词典</label><select id="entityVocabSelector">${dictionaries.map((d) => `<option value="${esc(d.entity_type)}" ${d.entity_type === state.entityVocabType ? "selected" : ""}>${esc(dictLabel(d.entity_type))} / ${esc(d.entity_type)}（${d.entry_count} 实体 · ${d.referencing_templates.length} 模板引用）</option>`).join("")}</select></div>
        </div>
      `, "", "同一实体类型只有一个词典；所有勾选“共享词表”的模板都读取这里")}
      ${selected ? pagedPanel({
        key: tableKey,
        title: `${dictLabel(selected.entity_type)}词典（${entries.length}）`,
        description: `按专科、关联疾病筛选；已分类 ${classifiedCount}/${entries.length} 条。专科和疾病默认从图谱关联自动推断，可在编辑中手工覆盖。`,
        headers: ["编码", "名称", "领域", "专科", "关联疾病", "标准", "别名 / 正则", "操作"],
        items: entryItems,
        filters: activeFilters,
        placeholder: "搜索编码、名称、别名、专科或疾病…",
        emptyText: "没有匹配的实体",
        actions: '<button id="importIcd10" class="btn ghost">从标准库导入</button><button id="newEntityEntry" class="btn primary">新增实体</button>',
      }) : '<div class="empty">暂无词典；在解析模板中将实体类型设为共享词表后自动创建</div>'}
      ${selected && selected.referencing_templates.length ? panel("引用该词典的模板", table(["模板", "ID", "状态"], selected.referencing_templates.map((t) => `
        <tr><td><strong>${esc(t.name)}</strong></td><td class="mono">${esc(t.template_id)}</td><td>${badge(t.status)}</td></tr>`
      )), "", "这些模板的实体类型勾选了“共享词表”，词典修改后无需逐模板维护") : ""}
    `;
    $("#entityVocabSelector").onchange = (event) => { state.entityVocabType = event.target.value; renderEntityVocab(); };
    const newButton = $("#newEntityEntry");
    if (newButton && selected) newButton.onclick = () => openEntityEntryEditor(selected.entity_type, null);
    const importButton = $("#importIcd10");
    if (importButton && selected) importButton.onclick = () => openIcd10Importer(selected.entity_type);
    bindPagedTable(tableKey);
    const tableBody = document.querySelector(`[data-table-body="${tableKey}"]`);
    if (tableBody && selected) {
      tableBody.addEventListener("click", async (event) => {
        const editButton = event.target.closest("[data-entry-edit]");
        if (editButton) {
          const item = entries.find((row) => row.code === editButton.dataset.entryEdit);
          if (item) openEntityEntryEditor(selected.entity_type, item);
          return;
        }
        const deleteButton = event.target.closest("[data-entry-delete]");
        if (!deleteButton) return;
        const code = deleteButton.dataset.entryDelete;
        if (!confirm(`确认从共享词典删除 ${code}？`)) return;
        try {
          await api(`/api/vocab/entities/${encodeURIComponent(selected.entity_type)}/${encodeURIComponent(code)}`, { method: "DELETE" });
          toast("共享实体已删除"); renderEntityVocab();
        } catch (error) { toast(error.message, true); }
      });
    }
  }

  function openEntityEntryEditor(entityType, item, isNew = false) {
    const value = item || {
      code: "", name: "", aliases: [],
      domain: entityType === "disease" ? "western" : "",
      standard_system: entityType === "disease" ? "ICD-10" : "",
      standard_code: "",
      source: entityType === "disease" ? "ICD-10" : "",
      specialty: "",
      disease_tags: [],
    };
    isNew = isNew || !item;
    const isDisease = entityType === "disease";
    const domainField = isDisease
      ? `<div class="field"><label>领域</label><input value="西医" readonly></div>`
      : `<div class="field"><label>领域</label><select name="domain"><option value="" ${!value.domain ? "selected" : ""}>共享</option><option value="western" ${value.domain === "western" ? "selected" : ""}>西医</option><option value="tcm" ${value.domain === "tcm" ? "selected" : ""}>中医</option></select></div>`;
    const standardFields = isDisease
      ? [
          `<div class="field"><label>标准体系</label><input value="ICD-10" readonly></div>`,
          `<div class="field"><label>标准编码</label><input name="standard_code" value="${esc(value.code)}" readonly></div>`,
        ].join("")
      : [
          `<div class="field"><label>标准体系</label><input name="standard_system" value="${esc(value.standard_system || "")}" placeholder="标准体系编码"></div>`,
          `<div class="field"><label>标准编码</label><input name="standard_code" value="${esc(value.standard_code || "")}"></div>`,
        ].join("");
    const notice = isDisease
      ? `疾病字典按 ICD-10 编码和标准名称维护；标准编码自动等于疾病编码。`
      : `保存后立即对全部引用「${esc(typeName[entityType] || entityType)}」词典的同领域模板生效。`;
    const derivedSpecialty = (value.specialties || []).filter((entry) => entry && entry !== "未分类").join("、");
    const derivedDiseases = (value.diseases || []).filter((entry) => entry && entry !== "未分类");
    $("#editorBody").innerHTML = `
      <h3>${isNew ? "新增共享实体" : "编辑共享实体"} · ${esc(typeName[entityType] || entityType)}</h3>
      <div class="form-grid">
        <div class="field"><label>实体编码</label><input name="code" value="${esc(value.code)}" ${!isNew ? "readonly" : ""}></div>
        <div class="field"><label>实体名称</label><input name="name" value="${esc(value.name)}"></div>
        ${domainField}
        ${standardFields}
        <div class="field"><label>专科</label><input name="specialty" value="${esc(value.specialty || "")}" placeholder="${esc(derivedSpecialty || "例如：心血管内科")}"></div>
        <div class="field wide"><label>关联疾病（每行一个，留空自动归类）</label><textarea name="disease_tags" placeholder="${esc(derivedDiseases.join("\n") || "例如：急性心肌梗死")}">${esc((value.disease_tags || []).join("\n"))}</textarea></div>
        <div class="field wide"><label>别名 / 正则（每行一个）</label><textarea name="aliases">${esc((value.aliases || []).join("\n"))}</textarea></div>
        <div class="field wide muted">${notice} 专科和关联疾病留空时，列表会使用图谱关联自动归类；手工填写后以手工值为准。</div>
      </div>`;
    const dialog = $("#editorDialog"); dialog.showModal();
    $("#editorSave").onclick = async () => {
      const form = $("#editorBody");
      const payload = Object.fromEntries([...form.querySelectorAll("input,select,textarea")].map((input) => [input.name, input.value]));
      payload.aliases = (payload.aliases || "").split(/\n+/).map((v) => v.trim()).filter(Boolean);
      payload.disease_tags = (payload.disease_tags || "").split(/\n+/).map((v) => v.trim()).filter(Boolean);
      payload.specialty = (payload.specialty || "").trim();
      if (isDisease) {
        payload.domain = "western";
        payload.standard_system = "ICD-10";
        payload.standard_code = payload.code;
        payload.source = "ICD-10";
      }
      try {
        await (isNew
          ? api(`/api/vocab/entities/${encodeURIComponent(entityType)}`, { json: payload })
          : api(`/api/vocab/entities/${encodeURIComponent(entityType)}/${encodeURIComponent(value.code)}`, { method: "PUT", json: payload }));
        dialog.close(); toast("共享实体已保存"); renderEntityVocab();
      } catch (error) { toast(error.message, true); }
    };
    $("#editorCancel").onclick = () => dialog.close();
  }

  async function openIcd10Importer(entityType) {
    if (entityType !== "disease") {
      toast("目前标准库导入支持疾病词典", true);
      return;
    }
    const dialog = $("#editorDialog");
    const saveButton = $("#editorSave");
    saveButton.textContent = "导入选中条目";
    $("#editorBody").innerHTML = `
      <h3>从 ICD-10 标准库导入</h3>
      <p class="muted">标准库只提供标准编码和名称，不直接参与解析识别。勾选后导入到活跃疾病字典，解析时才会使用。</p>
      <div class="inline-controls">
        <div class="field"><label>搜索编码、名称或别名</label><input id="icd10Search" placeholder="例如：I10、高血压、淋巴瘤"></div>
      </div>
      <div id="icd10Results" class="table-wrap"><div class="empty">正在加载...</div></div>
    `;
    dialog.showModal();

    const renderResults = async (query = "") => {
      try {
        const result = await api(`/api/vocab/reference/icd10?q=${encodeURIComponent(query)}&limit=50`);
        const box = $("#icd10Results");
        if (!result.items?.length) {
          box.innerHTML = `<div class="empty">没有匹配的标准疾病</div>`;
          return;
        }
        box.innerHTML = `
          <table class="table">
            <thead><tr><th>选择</th><th>编码</th><th>标准名称</th><th>别名</th><th>状态</th></tr></thead>
            <tbody>
              ${result.items.map((item) => `
                <tr>
                  <td><input type="checkbox" class="icd10-select" value="${esc(item.code)}" ${item.in_active_dictionary ? "disabled" : ""}></td>
                  <td class="mono">${esc(item.code)}</td>
                  <td><strong>${esc(item.name)}</strong></td>
                  <td><div class="quote">${esc((item.aliases || []).join(" / "))}</div></td>
                  <td>${item.in_active_dictionary ? '<span class="badge active">已在活跃字典</span>' : '<span class="badge">可选</span>'}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        `;
      } catch (error) {
        $("#icd10Results").innerHTML = `<div class="empty">${esc(error.message)}</div>`;
      }
    };

    let searchTimer;
    $("#icd10Search").oninput = (event) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => renderResults(event.target.value), 250);
    };
    await renderResults();

    saveButton.onclick = async () => {
      const codes = [...document.querySelectorAll("#icd10Results .icd10-select:checked")].map((input) => input.value);
      if (!codes.length) {
        toast("请先勾选要导入的标准疾病", true);
        return;
      }
      try {
        const result = await api("/api/vocab/reference/icd10/import", { json: { codes } });
        dialog.close();
        saveButton.textContent = "保存";
        toast(`已导入 ${result.imported_count} 条；跳过 ${result.skipped_count} 条`);
        renderEntityVocab();
      } catch (error) {
        toast(error.message, true);
      }
    };

    $("#editorCancel").onclick = () => {
      dialog.close();
      saveButton.textContent = "保存";
    };
  }

  async function renderVocab() {
    await loadBase();
    const search = state.vocabSearch.trim().toLowerCase();
    const category = state.vocabCategory;
    const filtered = state.vocab.filter((item) => (!category || item.category === category) && (!search || `${item.code} ${item.name_cn} ${item.name_en} ${item.description}`.toLowerCase().includes(search)));
    const categories = [...new Set(state.vocab.map((item) => item.category))].sort();
    content.innerHTML = `
      <div class="grid c4">
        <div class="kpi"><span>概念总数</span><strong>${state.vocab.length}</strong><small>受控语义集合</small></div>
        <div class="kpi"><span>检验概念</span><strong>${state.vocab.filter((v) => v.category === "lab_concept").length}</strong><small>升高、降低、动态趋势等</small></div>
        <div class="kpi"><span>检查 / 症状概念</span><strong>${state.vocab.filter((v) => ["exam_concept", "symptom_concept"].includes(v.category)).length}</strong><small>存在、缺失、持续时间等</small></div>
        <div class="kpi"><span>中医概念</span><strong>${state.vocab.filter((v) => normalizeDomainClient(v.domain) === "tcm" || String(v.category).startsWith("tcm_")).length}</strong><small>证候判定谓词</small></div>
      </div>
      ${panel("概念词汇", table(["编码", "中文名称", "英文名称", "类别", "执行器", "说明", "状态", "操作"], filtered.map((item) => `
        <tr><td class="mono">${esc(item.code)}</td><td><strong>${esc(item.name_cn)}</strong></td><td>${esc(item.name_en || "-")}</td><td><span class="badge">${esc(item.category)}</span></td><td class="mono">${esc(item.evaluator_type)}</td><td><div class="quote">${esc(item.description)}</div></td><td>${item.is_active ? '<span class="badge active">启用</span>' : '<span class="badge retired">停用</span>'}</td><td><div class="actions"><button class="btn sm" data-concept-edit="${esc(item.code)}">编辑</button><button class="btn sm" data-concept-usage="${esc(item.code)}">引用</button><button class="btn sm danger" data-concept-delete="${esc(item.code)}">删除</button></div></td></tr>`
      )), `<div class="actions"><button id="newConcept" class="btn primary">新增概念</button><input id="vocabSearch" class="field" placeholder="搜索编码或名称" value="${esc(state.vocabSearch)}"><select id="vocabCategory"><option value="">全部类别</option>${categories.map((v) => `<option value="${v}" ${category === v ? "selected" : ""}>${v}</option>`).join("")}</select></div>`, "概念是规则、图谱、模板和医院标准之间的公共判断语言")}
    `;
    $("#newConcept").onclick = () => openConceptEditor(null);
    $("#vocabSearch").oninput = (event) => { state.vocabSearch = event.target.value; renderVocab(); };
    $("#vocabCategory").onchange = (event) => { state.vocabCategory = event.target.value; renderVocab(); };
    content.querySelectorAll("[data-concept-edit]").forEach((button) => button.onclick = () => openConceptEditor(state.vocab.find((item) => item.code === button.dataset.conceptEdit)));
    content.querySelectorAll("[data-concept-usage]").forEach((button) => button.onclick = () => openConceptUsage(button.dataset.conceptUsage));
    content.querySelectorAll("[data-concept-delete]").forEach((button) => button.onclick = async () => {
      const code = button.dataset.conceptDelete;
      const usage = await api(`/api/vocab/${code}/usage`);
      const count = Object.values(usage.usage).reduce((sum, value) => sum + value, 0);
      if (count) return openConceptUsage(code);
      if (!confirm(`确认删除概念 ${code}？`)) return;
      await api(`/api/vocab/${code}`, { method: "DELETE" });
      toast("概念已删除"); renderVocab();
    });
  }

  function openConceptEditor(item, isNew = false) {
    const value = item || { code: "", name_cn: "", name_en: "", category: "lab_concept", description: "", evaluator_type: "static_threshold", is_active: true };
    isNew = isNew || !item;
    const categories = ["lab_concept", "exam_concept", "symptom_concept"];
    const evaluators = ["static_threshold", "uln_multiplier", "dynamic_trend", "qualitative", "finding_exists", "duration_check"];
    $("#editorBody").innerHTML = `
      <h3>${isNew ? "新增概念词汇" : "编辑概念词汇"}</h3>
      <div class="form-grid">
        <div class="field"><label>概念编码</label><input name="code" value="${esc(value.code)}" ${!isNew ? "readonly" : ""}></div>
        <div class="field"><label>中文名称</label><input name="name_cn" value="${esc(value.name_cn)}"></div>
        <div class="field"><label>英文名称</label><input name="name_en" value="${esc(value.name_en)}"></div>
        <div class="field"><label>类别</label><select name="category">${categories.map((v) => `<option value="${v}" ${value.category === v ? "selected" : ""}>${v}</option>`).join("")}</select></div>
        <div class="field"><label>推理执行器</label><select name="evaluator_type">${evaluators.map((v) => `<option value="${v}" ${value.evaluator_type === v ? "selected" : ""}>${v}</option>`).join("")}</select></div>
        <div class="field"><label>状态</label><select name="is_active"><option value="true" ${value.is_active ? "selected" : ""}>启用</option><option value="false" ${!value.is_active ? "selected" : ""}>停用</option></select></div>
        <div class="field wide"><label>说明</label><input name="description" value="${esc(value.description)}"></div>
        <div class="field wide muted">具体阈值不在概念中配置；图谱和规则只引用这里的抽象概念编码。</div>
      </div>`;
    const dialog = $("#editorDialog"); dialog.showModal();
    $("#editorSave").onclick = async () => {
      const form = $("#editorBody");
      const payload = Object.fromEntries([...form.querySelectorAll("input,select,textarea")].map((input) => [input.name, input.value]));
      payload.is_active = payload.is_active === "true";
      try {
        await (isNew ? api("/api/vocab", { json: payload }) : api(`/api/vocab/${value.code}`, { method: "PUT", json: payload }));
        dialog.close(); toast("概念词汇已保存"); renderVocab();
      } catch (error) { toast(error.message, true); }
    };
    $("#editorCancel").onclick = () => dialog.close();
  }

  async function openConceptUsage(code) {
    const usage = await api(`/api/vocab/${code}/usage`);
    const total = Object.values(usage.usage).reduce((sum, value) => sum + value, 0);
    $("#editorBody").innerHTML = `
      <h3>概念引用：${esc(code)}</h3>
      <div class="result-banner ${total ? "warning" : "ok"}">${total ? `存在 ${total} 处引用，概念不可删除；请先迁移规则或停用概念。` : "当前没有引用，可以安全删除。"}</div>
      ${table(["引用范围", "名称", "定位"], usage.references.map((ref) => `<tr><td>${esc(ref.scope)}</td><td>${esc(ref.label)}</td><td class="mono">${esc(ref.detail)}</td></tr>`))}
    `;
    const dialog = $("#editorDialog"); dialog.showModal();
    $("#editorSave").onclick = () => dialog.close();
    $("#editorCancel").onclick = () => dialog.close();
  }

  async function saveExtractionConfig() {
    const payload = {
      default_mode: $("#defaultMode").value,
      default_template_id: $("#defaultTemplate").value,
      llm: {
        provider_name: state.extractionConfig.llm.provider_name,
        provider: $("#llmProvider").value,
        api_format: $("#llmApiFormat").value,
        base_url: $("#llmBaseUrl").value.trim(),
        model: $("#llmModel").value.trim(),
        host_header: $("#llmHostHeader").value.trim(),
        anthropic_auth: $("#llmAnthropicAuth").value,
        max_output_tokens: Number(state.extractionConfig.llm.max_output_tokens || 8192),
        api_key: ($("#llmApiKey")?.value || "").trim() || "__KEEP__",
        api_key_env: $("#llmKeyEnv").value.trim() || "OPENAI_API_KEY",
        temperature: Number($("#llmTemperature").value || 0),
        timeout_seconds: Number($("#llmTimeout").value || 120),
        max_chunk_chars: Number($("#llmMaxChars").value || 12000),
        max_chunks: Number($("#llmMaxChunks").value || 80),
      },
    };
    state.extractionConfig = await api("/api/extraction/config", { method: "PUT", json: { config: payload } });
    toast("解析配置已保存");
    renderTemplates();
  }

  function formatTime(value) {
    const date = value ? new Date(value) : null;
    return date && !isNaN(date.getTime()) ? date.toLocaleString("zh-CN", { hour12: false }) : "-";
  }

  async function generatePackage() {
    const doc = state.doc;
    if (!doc) return toast("请先上传或选择解析文档", true);
    try {
      const graphMode = $("#graphTargetMode");
      const graphTarget = graphMode ? {
        mode: graphMode.value,
        filename: graphMode.value === "existing"
          ? $("#graphTargetFilename")?.value || ""
          : ($("#newGraphName")?.value || "").trim(),
      } : undefined;
      const result = await api("/api/guide-packages/generate", { json: {
        doc_id: doc.doc_id,
        domain: doc.extraction?.domain || doc.domain || "",
        target_disease_code: doc.targetDisease || $("#targetDisease")?.value || "",
        template_id: doc.template_id || $("#uploadTemplate")?.value || "",
        status: "generated",
        ...(graphTarget ? { graph_target: graphTarget } : {}),
      } });
      state.guidePackageId = result.package.package_id;
      const failed = result.package.checks.filter((item) => item.status === "failed").length;
      toast(failed ? `知识包已生成，但有 ${failed} 项预检失败；请到知识包页处理` : `知识包已生成：${result.summary.rule_count} 条候选规则；请到知识包页审核`);
      navigate("packages");
    } catch (error) { toast(error.message, true); }
  }

  async function renderGuidePackages() {
    state.guidePackages = await api("/api/guide-packages");
    if (!state.guidePackageId && state.guidePackages.length) state.guidePackageId = state.guidePackages[0].package_id;
    let selected = null;
    if (state.guidePackageId) {
      try { selected = await api(`/api/guide-packages/${state.guidePackageId}`); }
      catch (_) { state.guidePackageId = ""; }
    }
    let packageItems = null;
    if (selected) {
      try { packageItems = await api(`/api/guide-packages/${selected.package_id}/items`); }
      catch (_) { packageItems = null; }
    }
    const checkBadge = (status) => `<span class="badge ${status === "passed" ? "active" : status === "warning" ? "draft" : "danger"}">${status === "passed" ? "通过" : status === "warning" ? "提醒" : "失败"}</span>`;
    const importStatuses = ["imported", "ready_for_test", "published"];
    const isImported = (item) => Boolean(item?.imported) || Boolean(item?.import_result?.success);
    const hasImportStateMismatch = (item) => Boolean(
      item && importStatuses.includes(item.status) && !isImported(item)
    );
    const packageStatusLabel = (item) => {
      const status = typeof item === "string" ? item : item?.status;
      if (typeof item === "object" && hasImportStateMismatch(item)) return "待导入（状态不一致）";
      return ({
      generated: "待审核",
      reviewed: "已审核",
      imported: "已导入（待修正）",
      ready_for_test: "已导入（可测试）",
      published: "已发布",
      failed: "导入失败",
      }[status] || status);
    };
    const packageConfirmed = selected && (isImported(selected) || selected.status === "reviewed");
    content.innerHTML = `
      <div class="pipeline">
        <div class="step done"><strong>1 审核预检</strong><span>查看自动检查与问题明细</span></div>
        <div class="step ${packageConfirmed ? "done" : ""}"><strong>2 确认知识包</strong><span>核对目标、模板和候选内容</span></div>
        <div class="step ${isImported(selected) ? "done" : "pending"}"><strong>3 导入图谱与草稿规则</strong><span>一次性入库，不自动启用</span></div>
        <div class="step pending"><strong>4 规则管理启用</strong><span>人工审核后逐条启用</span></div>
      </div>
      ${panel("指南知识包", table(["知识包 / 指南", "目标证候/诊断", "候选内容", "预检", "状态", "更新时间", "操作"], state.guidePackages.map((item) => {
        const check = item.check_summary || {};
        return `<tr><td><strong>${esc(item.package_id)}</strong><div class="muted">${esc(item.doc_title)}</div><div>${badge(normalizeDomainClient(item.domain))}</div></td><td class="mono">${esc(item.target_disease_code)}</td><td>${item.entity_count} 实体${item.syndrome_count ? `（证候 ${item.syndrome_count}）` : ""}<br>${item.relation_count} 关系 / ${item.rule_count} 规则</td><td>${check.passed || 0} 通过 / ${check.warning || 0} 提醒 / ${check.failed || 0} 失败</td><td><span class="badge ${esc(item.status)}">${esc(packageStatusLabel(item))}</span></td><td class="nowrap">${esc(formatTime(item.updated_at))}</td><td><div class="actions"><button class="btn sm" data-package-detail="${esc(item.package_id)}">查看 / 审核</button></div></td></tr>`;
      })), '<button id="refreshPackages" class="btn primary">刷新列表</button>', "先审核预检，再在详情中一次性导入图谱和草稿规则；导入不会启用规则")}
      ${selected ? `
        <div class="result-banner ${isImported(selected) ? "ok" : selected.status === "failed" ? "error" : "warning"}">
          <strong>${esc(selected.doc_title)}</strong> / ${esc(selected.package_id)} 状态：${esc(packageStatusLabel(selected))} ${badge(normalizeDomainClient(selected.domain))}
          <div class="muted">目标 ${esc(selected.target_anchor_type || "疾病")} ${esc(selected.target_disease_code)}；模板 ${esc(selected.template_id || "-")}；目标图谱 ${esc(selected.graph_filename || "stemi_graph.json")}。${normalizeDomainClient(selected.domain) === "tcm" ? "中医知识包不检查医院检验标准，重点检查证候构成、方剂组成和配伍禁忌。" : "图谱只保存抽象证据语义，医院阈值仍在标准管理中维护。"}</div>
        </div>
        ${normalizeDomainClient(selected.domain) === "tcm" ? panel("目标图谱配置", `
          <div class="inline-controls">
            <div class="field"><label>模式</label><select id="packageGraphMode">
              <option value="new" ${(!selected.graph_target?.mode || selected.graph_target?.mode === "new") ? "selected" : ""}>新建</option>
              <option value="existing" ${selected.graph_target?.mode === "existing" ? "selected" : ""}>追加既有</option>
            </select></div>
            <div class="field ${selected.graph_target?.mode === "existing" ? "" : "hidden"}" id="packageGraphExistingField">
              <label>既有图谱</label><select id="packageGraphFilename"><option value="">加载中</option></select>
            </div>
            <div class="field ${(!selected.graph_target?.mode || selected.graph_target?.mode === "new") ? "" : "hidden"}" id="packageGraphNewField">
              <label>新图谱文件名</label><input id="packageGraphNewName" value="${selected.graph_target?.mode !== "existing" ? esc(selected.graph_filename || "") : ""}" placeholder="留空自动生成">
            </div>
            <button id="saveGraphTarget" class="btn sm">保存目标图谱</button>
          </div>
        `, "", "导入前可自由变更；已导入的知识包变更目标后，需重新导入才会写入新图谱，原图谱内容不变") : ""}
        ${hasImportStateMismatch(selected) ? `
          <div class="result-banner warning"><strong>知识包尚未真正入库</strong><div>当前流程状态显示为已导入，但没有任何导入记录。请使用下方“导入图谱和草稿规则”完成入库；导入后 ${(selected.suggested_rules || []).length} 条候选规则会以 draft 状态出现。</div></div>
        ` : ""}
        ${isImported(selected) ? `
          <div class="result-banner ok"><strong>该知识包已导入</strong><div>图谱实体和关系已更新，规则已保存为草稿但未启用。下一步请到规则管理逐条审核启用。</div><div class="actions"><button id="goRules" class="btn primary">前往规则管理启用</button><button id="genTestCase" class="btn">生成测试病历</button><span class="muted" id="genTestCaseHint">${(selected.import_result?.test_case?.case?.name) ? `已自动生成：${esc(selected.import_result.test_case.case.name)}` : (selected.import_result?.test_case?.error ? `自动生成失败：${esc(selected.import_result.test_case.error)}` : "")}</span></div></div>
          <details class="debug-panel"><summary>高级：重新导入</summary><div class="actions"><button id="reimportPackage" class="btn danger">重新导入图谱和草稿规则</button></div></details>
        ` : `
          ${panel("下一步", `
            <div class="inline-controls">
              ${selected.status === "generated"
                ? '<button id="approvePackage" class="btn primary">确认审核通过</button><span class="muted">请先核对下方自动预检与候选内容；确认后才能进入导入。</span>'
                : '<button id="importSelectedPackage" class="btn primary">导入图谱和草稿规则</button><span class="muted">图谱将更新，规则保存为草稿，不会自动启用。</span>'}
              <div class="field"><label>导入模式</label><select id="packageImportMode">
                <option value="draft">全部导入为草稿</option>
                <option value="approved_only">仅导入已通过项</option>
              </select></div>
            </div>
          `, "", "知识包只在审核通过后入库一次；规则启用请到规则管理操作")}
        `}
        <details class="debug-panel"><summary>高级：手动修改流程状态</summary>
          <div class="inline-controls">
            <div class="field"><label>流程状态</label><select id="packageStatus">${!(["generated", "reviewed", "failed", ...(isImported(selected) ? importStatuses : [])]).includes(selected.status) ? `<option value="${esc(selected.status)}" selected disabled>${esc(packageStatusLabel(selected))}</option>` : ""}${["generated", "reviewed", "failed", ...(isImported(selected) ? importStatuses : [])].map((s) => `<option value="${s}" ${selected.status === s ? "selected" : ""}>${packageStatusLabel(s)}</option>`).join("")}</select></div>
            <button id="savePackageStatus" class="btn" ${(["generated", "reviewed", "failed", ...(isImported(selected) ? importStatuses : [])]).includes(selected.status) ? "" : "disabled"}>保存状态</button>
          </div>
        </details>
        ${panel("自动预检", table(["检查项", "结论", "问题明细"], (selected.checks || []).map((item) => `<tr><td><strong>${esc(item.title)}</strong><div class="mono muted">${esc(item.code)}</div></td><td>${checkBadge(item.status)}</td><td>${item.issues?.length ? `<ul class="issue-list">${item.issues.map((issue) => `<li>${esc(issue)}</li>`).join("")}</ul>` : '<span class="muted">未发现问题</span>'}</td></tr>`)))}
        ${panel("候选内容", table(["类别", "数量", "摘要"], [
          ...(normalizeDomainClient(selected.domain) === "tcm" ? [
            ["证候", (selected.entities || []).filter((item) => item.entity_type === "syndrome").length, (selected.entities || []).filter((item) => item.entity_type === "syndrome").map((item) => `${item.name} (${item.code})`).join("；") || "-"],
            ["症状 / 舌象 / 脉象 / 体征", (selected.entities || []).filter((item) => ["symptom", "tongue", "pulse", "sign"].includes(item.entity_type)).length, (selected.entities || []).filter((item) => ["symptom", "tongue", "pulse", "sign"].includes(item.entity_type)).map((item) => `${typeName[item.entity_type]}:${item.name}`).join("；") || "-"],
            ["治法 / 方剂 / 中药", (selected.entities || []).filter((item) => ["treatment_method", "formula", "herb"].includes(item.entity_type)).length, (selected.entities || []).filter((item) => ["treatment_method", "formula", "herb"].includes(item.entity_type)).map((item) => `${typeName[item.entity_type]}:${item.name}`).join("；") || "-"],
          ] : [
            ["疾病", (selected.entities || []).filter((item) => item.entity_type === "disease").length, (selected.entities || []).filter((item) => item.entity_type === "disease").map((item) => `${item.name} (${item.code})`).join("；") || "-"],
            ["检验 / 检查 / 发现 / 症状", (selected.entities || []).filter((item) => ["lab", "exam", "exam_finding", "symptom", "diagnostic_evidence"].includes(item.entity_type)).length, (selected.entities || []).filter((item) => ["lab", "exam", "exam_finding", "symptom", "diagnostic_evidence"].includes(item.entity_type)).map((item) => `${typeName[item.entity_type]}:${item.name} (${item.code})`).join("；") || "-"],
          ]),
          ["候选关系", (selected.suggested_relations || []).length, (selected.suggested_relations || []).map((item) => `${item.source_code} → ${item.relation_code} → ${item.target_code || item.target_concept_code}`).join("；") || "导入时按同片段结构兜底生成"],
          ["候选规则", (selected.suggested_rules || []).length, (selected.suggested_rules || []).map((item) => `${item.rule_id} → ${item.disease_code}`).join("；") || "-"],
        ].map((row) => `<tr><td>${esc(row[0])}</td><td>${row[1]}</td><td><div class="quote">${esc(row[2])}</div></td></tr>`)))}
        ${packageItems ? panel("包内预审", `
          <div class="review-toolbar">
            <div class="field"><label>审核人</label><input id="packageReviewer" value="${esc(localStorage.getItem("cdssReviewer") || "")}" placeholder="姓名"></div>
            <div class="field"><label>筛选</label><select id="packageItemStatus">
              <option value="">全部</option>
              <option value="draft">待预审</option>
              <option value="approved">预审通过</option>
              <option value="rejected">预审拒绝</option>
            </select></div>
            <input id="packageItemSearch" type="search" placeholder="搜索名称 / 编码 / 条目 ID…">
            <button id="packageBatchApprove" class="btn sm primary">批量通过当前筛选</button>
            <span class="muted">通过 ${packageItems.summary.approved} / 待审 ${packageItems.summary.draft} / 拒绝 ${packageItems.summary.rejected}；表格最多展示前 200 条。</span>
          </div>
          <div id="packageItemTable"></div>
        `, "", "只有预审通过的条目才会在「仅导入已通过项」模式下入库；未预审条目视为草稿") : ""}
        ${selected.import_result ? (() => {
          const imported = selected.import_result;
          const graph = imported.graph || {};
          const rules = imported.rules || {};
          const sync = rules.sync_check || {};
          const inconsistent = (sync.rules || []).filter((item) => item.semantic_status !== "consistent");
          return panel("导入结果", table(["范围", "结果", "说明"], [
            ["图谱", imported.graph ? "已入库" : "失败", `${esc(graph.graph_filename || selected.graph_filename || "-")}：${graph.diseases_added || 0} 疾病 / ${graph.syndromes_added || 0} 证候 / ${graph.evidences_added || 0} 证据 / ${graph.links_added || 0} 关联 / ${graph.nodes_added || 0} 语义节点 / ${graph.relations_added || 0} 语义关系 / ${graph.rule_semantics_added || 0} 规则补链`],
            ["规则", rules.errors?.length ? "部分失败" : "草稿已入库", `${rules.imported || 0} 新增 / ${rules.replaced || 0} 更新 / ${rules.errors?.length || 0} 失败；未自动启用`],
            ["图谱同步", sync.inconsistent_count ? `${sync.inconsistent_count} 条待修正` : "一致", `${sync.consistent_count || 0} / ${sync.total || 0} 条草稿规则可解析到图谱诊断链路`],
          ].map((row) => `<tr><td>${esc(row[0])}</td><td>${esc(row[1])}</td><td>${esc(row[2])}</td></tr>`)) + (inconsistent.length ? `<div class="section-title">待修正规则</div>${table(["规则", "状态", "图谱问题"], inconsistent.slice(0, 10).map((item) => `<tr><td class="mono">${esc(item.rule_id)}</td><td>${badge("draft")}</td><td><ul class="issue-list">${item.issues.map((issue) => `<li>${esc(issue)}</li>`).join("")}</ul></td></tr>`))}` : "") + `<details class="debug-panel"><summary>原始导入 JSON</summary><pre class="mono">${esc(JSON.stringify(imported, null, 2))}</pre></details>`);
        })() : ""}
      ` : '<div class="empty">暂无选中知识包；请先在指南导入页上传并生成。</div>'}
    `;
    const renderPackageItemTable = () => {
      const mount = $("#packageItemTable");
      if (!mount || !packageItems) return;
      const status = $("#packageItemStatus")?.value || "";
      const query = ($("#packageItemSearch")?.value || "").trim().toLowerCase();
      const items = (packageItems.items || []).filter((item) => {
        if (status && item.status !== status) return false;
        return !query || `${item.name || ""} ${item.code || ""} ${item.item_id}`.toLowerCase().includes(query);
      }).slice(0, 200);
      mount.innerHTML = table(["", "类型", "条目", "状态", "操作"], items.map((item) => `
        <tr>
          <td><input type="checkbox" data-package-select="${esc(item.item_id)}"></td>
          <td>${esc(item.item_type)}${item.entity_type ? ` / ${esc(item.entity_type)}` : ""}</td>
          <td><strong>${esc(item.name || item.rule_id || item.item_id)}</strong><div class="mono muted">${esc(item.code || item.item_id)}</div></td>
          <td>${reviewBadge(item.status)}</td>
          <td><div class="actions">
            <button class="btn sm primary" data-package-item="${esc(item.item_id)}" data-package-action="approve">通过</button>
            <button class="btn sm danger" data-package-item="${esc(item.item_id)}" data-package-action="reject">拒绝</button>
            <button class="btn sm" data-package-item="${esc(item.item_id)}" data-package-action="restore">恢复</button>
          </div></td>
        </tr>`));
      mount.querySelectorAll("[data-package-item]").forEach((button) => button.onclick = async () => {
        try {
          await api(`/api/guide-packages/${selected.package_id}/items/review`, { json: {
            items: [button.dataset.packageItem],
            action: button.dataset.packageAction,
            reviewer: ($("#packageReviewer")?.value || "").trim() || "未署名",
          }});
          localStorage.setItem("cdssReviewer", ($("#packageReviewer")?.value || "").trim());
          toast("预审状态已更新");
          renderGuidePackages();
        } catch (error) { toast(error.message, true); }
      });
    };
    renderPackageItemTable();
    $("#packageItemStatus")?.addEventListener("change", renderPackageItemTable);
    $("#packageItemSearch")?.addEventListener("input", renderPackageItemTable);
    $("#packageReviewer")?.addEventListener("change", (event) => {
      localStorage.setItem("cdssReviewer", event.target.value.trim());
    });
    $("#packageBatchApprove")?.addEventListener("click", async () => {
      const status = $("#packageItemStatus")?.value || "";
      const query = ($("#packageItemSearch")?.value || "").trim().toLowerCase();
      const ids = (packageItems.items || []).filter((item) => {
        if (status && item.status !== status) return false;
        return !query || `${item.name || ""} ${item.code || ""} ${item.item_id}`.toLowerCase().includes(query);
      }).map((item) => item.item_id);
      if (!ids.length) { toast("没有可预审的条目", true); return; }
      if (!confirm(`将通过当前筛选的 ${ids.length} 条条目？`)) return;
      try {
        await api(`/api/guide-packages/${selected.package_id}/items/review`, { json: {
          items: ids,
          action: "approve",
          reviewer: ($("#packageReviewer")?.value || "").trim() || "未署名",
        }});
        toast(`已通过 ${ids.length} 条`);
        renderGuidePackages();
      } catch (error) { toast(error.message, true); }
    });
    $("#refreshPackages").onclick = renderGuidePackages;
    content.querySelectorAll("[data-package-detail]").forEach((button) => button.onclick = () => {
      state.guidePackageId = button.dataset.packageDetail;
      renderGuidePackages();
    });
    if (!selected) return;
    const renderPackageGraphFields = () => {
      const mode = $("#packageGraphMode")?.value || "new";
      $("#packageGraphNewField")?.classList.toggle("hidden", mode !== "new");
      $("#packageGraphExistingField")?.classList.toggle("hidden", mode !== "existing");
    };
    const loadPackageGraphs = async () => {
      const selector = $("#packageGraphFilename");
      if (!selector) return;
      selector.innerHTML = '<option value="">加载中</option>';
      try {
        const graphs = availableTcmTargetGraphs(await api("/api/kg/graphs?domain=tcm"));
        state.targetGraphs = graphs;
        if (!graphs.length) {
          selector.innerHTML = '<option value="">暂无可用中医图谱</option>';
          return;
        }
        const current = selected.graph_target?.mode === "existing" ? selected.graph_filename : "";
        selector.innerHTML = graphs.map((item) => `<option value="${esc(item.graph_filename)}" ${item.graph_filename === current ? "selected" : ""}>${esc(item.graph_filename)}（证候 ${item.syndromes || 0}）</option>`).join("");
      } catch (error) {
        selector.innerHTML = '<option value="">加载失败</option>';
        toast(error.message, true);
      }
    };
    $("#packageGraphMode")?.addEventListener("change", () => {
      renderPackageGraphFields();
      if ($("#packageGraphMode").value === "existing") loadPackageGraphs();
    });
    $("#saveGraphTarget")?.addEventListener("click", async () => {
      const mode = $("#packageGraphMode")?.value || "new";
      const filename = mode === "existing"
        ? ($("#packageGraphFilename")?.value || "").trim()
        : ($("#packageGraphNewName")?.value || "").trim();
      if (mode === "existing" && !filename) { toast("请选择要追加的中医图谱", true); return; }
      const imported = Boolean(selected.import_result?.success);
      const changed = mode !== (selected.graph_target?.mode || "new") || (Boolean(filename) && filename !== selected.graph_filename);
      let reimportConfirmed = false;
      if (imported && changed) {
        const message = `该知识包已导入。确认将目标图谱改为「${filename || "自动生成"}」？保存后还需重新导入，才会写入新图谱。`;
        if (!confirm(message)) return;
        reimportConfirmed = true;
      }
      try {
        await api(`/api/guide-packages/${selected.package_id}`, {
          method: "PUT",
          json: { graph_target: { mode, filename }, reimport_confirmed: reimportConfirmed },
        });
        toast(reimportConfirmed ? "目标图谱已变更；请重新导入以写入新图谱" : "目标图谱已保存");
        renderGuidePackages();
      } catch (error) { toast(error.message, true); }
    });
    renderPackageGraphFields();
    if ($("#packageGraphMode")?.value === "existing") loadPackageGraphs();
    $("#savePackageStatus")?.addEventListener("click", async () => {
      await api(`/api/guide-packages/${selected.package_id}`, { method: "PUT", json: { status: $("#packageStatus").value } });
      toast("知识包状态已保存");
      renderGuidePackages();
    });
    $("#approvePackage")?.addEventListener("click", async () => {
      await api(`/api/guide-packages/${selected.package_id}`, { method: "PUT", json: { status: "reviewed" } });
      toast("已确认审核通过，可以导入图谱和草稿规则");
      renderGuidePackages();
    });
    $("#importSelectedPackage")?.addEventListener("click", () => importGuidePackage(selected.package_id));
    $("#reimportPackage")?.addEventListener("click", () => importGuidePackage(selected.package_id, true));
    $("#goRules")?.addEventListener("click", () => navigate("rules"));
    $("#genTestCase")?.addEventListener("click", async () => {
      try {
        const result = await api(`/api/guide-packages/${selected.package_id}/generate-test-case`, { json: {} });
        toast(`测试病历已生成：${result.case.name}；请到「病历测试」页选择该病例（规则启用后即可看到疑似诊断）`);
        renderGuidePackages();
      } catch (error) { toast(error.message, true); }
    });
  }

  async function importGuidePackage(packageId, force = false) {
    const mode = $("#packageImportMode")?.value || "draft";
    const message = force
      ? `确认重新导入该知识包（模式：${mode === "approved_only" ? "仅导入已通过项" : "全部导入为草稿"}）？图谱会按当前包内容重新写入，同名草稿规则会被更新，规则仍不会自动启用。`
      : `确认导入该知识包（模式：${mode === "approved_only" ? "仅导入已通过项" : "全部导入为草稿"}）？图谱实体和关系将入库，规则保存为草稿，不会自动启用。`;
    if (!confirm(message)) return;
    try {
      const result = await api(`/api/guide-packages/${packageId}/import`, { json: { reviewed_only: false, mode } });
      const ruleErrors = result.rules?.errors?.length || 0;
      toast(ruleErrors ? `导入完成，但 ${ruleErrors} 条规则失败，请查看知识包详情` : "导入完成：规则已保存为草稿，请到规则管理启用", Boolean(ruleErrors));
      state.guidePackageId = packageId;
      renderGuidePackages();
    } catch (error) { toast(error.message, true); }
  }

  async function renderGraph(options = {}) {
    await loadBase();
    if (!options.preserveTableState) resetPagedTables();
    state.graphs = (await api("/api/kg/graphs")).filter((item) => normalizeDomainClient(item.domain) === "western");
    if (!state.graphs.some((item) => item.graph_filename === state.graphFilename)) {
      state.graphFilename = state.graphs.find((item) => item.graph_filename === "KG-GENERIC_GUIDELINE_V1.json")?.graph_filename || state.graphs[0]?.graph_filename || "stemi_graph.json";
    }
    const graph = await api(`/api/kg/graph?graph_filename=${encodeURIComponent(state.graphFilename)}`);
    state.graph = graph;
    const semanticNodes = Object.values(graph.nodes || {});
    const semanticRelations = Object.values(graph.relations || {});
    const semanticNodeIndex = Object.fromEntries(semanticNodes.map((node) => [node.id, node]));
    const diseaseLinks = graph.disease_evidence || [];
    const semanticLabel = (id) => {
      const node = semanticNodeIndex[id];
      return node ? `<strong>${esc(node.name)}</strong><div class="mono muted">${esc(node.code || node.concept_code || id)}</div>` : `<span class="mono">${esc(id || "-")}</span>`;
    };
    const semanticRef = (refs) => refs && refs.length ? `<div class="mono">${esc(refs[0])}</div>` : "-";
    const relationIndex = {};
    semanticRelations.forEach((relation) => {
      relationIndex[`${relation.source_node_id}|${relation.relation_code}`] = relation;
    });
    const linkedPaths = [];
    const coveredEvidenceIds = new Set();
    semanticRelations.forEach((diseaseLink) => {
      if (!["DISEASE_HAS_LAB", "DISEASE_HAS_EXAM"].includes(diseaseLink.relation_code)) return;
      const middle = semanticNodeIndex[diseaseLink.target_node_id];
      if (!middle) return;
      const evidenceRelationCode = middle.node_type === "lab" ? "LAB_HAS_DIAGNOSTIC_EVIDENCE" : "EXAM_HAS_DIAGNOSTIC_EVIDENCE";
      const evidenceLink = relationIndex[`${middle.id}|${evidenceRelationCode}`];
      const leaf = evidenceLink && semanticNodeIndex[evidenceLink.target_node_id];
      if (!evidenceLink || !leaf) return;
      coveredEvidenceIds.add(leaf.id);
      linkedPaths.push({ diseaseLink, middle, evidenceLink, leaf });
    });
    const symptomPaths = semanticRelations
      .filter((diseaseLink) => diseaseLink.relation_code === "DISEASE_HAS_SYMPTOM")
      .map((diseaseLink) => ({ diseaseLink, middle: semanticNodeIndex[diseaseLink.target_node_id], evidenceLink: null, leaf: null }));
    const directEvidencePaths = semanticRelations
      .filter((diseaseLink) => diseaseLink.relation_code === "DISEASE_HAS_DIAGNOSTIC_EVIDENCE" && !coveredEvidenceIds.has(diseaseLink.target_node_id))
      .map((diseaseLink) => ({ diseaseLink, middle: null, evidenceLink: null, leaf: semanticNodeIndex[diseaseLink.target_node_id] }));
    const semanticPaths = [...symptomPaths, ...linkedPaths, ...directEvidencePaths];
    const pathNode = (node) => node || { name: "-", code: "", concept_code: "" };
    const pathText = (node) => esc(`${pathNode(node).name} (${pathNode(node).code || pathNode(node).concept_code || "-"})`);
    const pathRefs = (path) => {
      const refs = [...path.diseaseLink.source_refs || [], ...(path.evidenceLink?.source_refs || [])];
      return [...new Set(refs)].slice(0, 3).join(" / ");
    };

    const relationItems = semanticRelations.map((relation, index) => {
      const status = relation.review_status || "draft";
      const actions = [
        (status === "draft" || status === "stale")
          ? `<button class="btn sm primary" data-review-relation="${index}" data-review-action="approve">通过</button>
             <button class="btn sm danger" data-review-relation="${index}" data-review-action="reject">拒绝</button>`
          : "",
        status === "active"
          ? `<button class="btn sm" data-review-relation="${index}" data-review-action="retire">失效</button>`
          : "",
        (status === "rejected" || status === "retired")
          ? `<button class="btn sm" data-review-relation="${index}" data-review-action="restore">恢复</button>`
          : "",
      ].filter(Boolean).join(" ");
      return {
        text: `${relation.source_node_id} ${relation.relation_name || ""} ${relation.relation_code || ""} ${relation.target_node_id} ${status}`,
        values: { relation_code: relation.relation_code || "", review_status: status },
        html: `<tr>
          <td>${semanticLabel(relation.source_node_id)}</td>
          <td><strong>${esc(relation.relation_name || relation.relation_code)}</strong><div class="mono muted">${esc(relation.relation_code)}</div></td>
          <td>${semanticLabel(relation.target_node_id)}</td>
          <td>${semanticRef(relation.source_refs)}</td>
          <td>${reviewBadge(status)}</td>
          <td><div class="actions">${actions || '<span class="muted">无</span>'}</div></td>
        </tr>`,
      };
    });
    const relationOptions = [...new Map(
      semanticRelations
        .filter((relation) => relation.relation_code)
        .map((relation) => [relation.relation_code, { value: relation.relation_code, label: relation.relation_name || relation.relation_code }]),
    ).values()];

    const pathItems = semanticPaths.map((path) => ({
      text: [
        semanticNodeIndex[path.diseaseLink.source_node_id]?.name,
        semanticNodeIndex[path.diseaseLink.source_node_id]?.code,
        path.middle?.name,
        path.middle?.code,
        path.leaf?.name,
        path.leaf?.code,
        pathRefs(path),
      ].filter(Boolean).join(" "),
      values: { disease: path.diseaseLink.source_node_id || "" },
      html: `<tr>
        <td>${pathText(semanticNodeIndex[path.diseaseLink.source_node_id])}</td>
        <td>${pathText(path.middle)}</td>
        <td>${pathText(path.leaf)}</td>
        <td class="mono">${esc(pathRefs(path) || "-")}</td>
      </tr>`,
    }));
    const pathDiseaseOptions = [...new Map(
      semanticPaths
        .map((path) => path.diseaseLink.source_node_id)
        .filter(Boolean)
        .map((id) => [id, { value: id, label: semanticNodeIndex[id]?.name || id }]),
    ).values()];

    const nodeItems = semanticNodes.map((node) => {
      const status = node.review_status || "draft";
      const actions = [
        (status === "draft" || status === "stale")
          ? `<button class="btn sm primary" data-review-node="${esc(node.id)}" data-review-action="approve">通过</button>
             <button class="btn sm danger" data-review-node="${esc(node.id)}" data-review-action="reject">拒绝</button>`
          : "",
        status === "active"
          ? `<button class="btn sm" data-review-node="${esc(node.id)}" data-review-action="retire">失效</button>`
          : "",
        (status === "rejected" || status === "retired")
          ? `<button class="btn sm" data-review-node="${esc(node.id)}" data-review-action="restore">恢复</button>`
          : "",
        `<button class="btn sm ghost" data-node-detail="${esc(node.id)}">详情</button>`,
      ].filter(Boolean).join(" ");
      return {
        text: `${node.node_type} ${node.name} ${node.code || ""} ${node.concept_code || ""} ${status}`,
        values: { node_type: node.node_type || "", review_status: status },
        html: `<tr>
          <td>${esc(typeName[node.node_type] || node.node_type)}</td>
          <td><strong>${esc(node.name)}</strong></td>
          <td class="mono">${esc(node.code || node.concept_code || "-")}</td>
          <td>${semanticRef(node.source_refs)}</td>
          <td>${reviewBadge(status)}</td>
          <td><div class="actions">${actions}</div></td>
        </tr>`,
      };
    });
    const nodeTypeOptions = [...new Set(semanticNodes.map((node) => node.node_type).filter(Boolean))]
      .map((type) => ({ value: type, label: typeName[type] || type }));

    const evidenceItems = (graph.evidences || []).map((evidence) => {
      const src = (graph.sources || []).find((source) => source.chunk_id === (evidence.source_refs || [])[0]);
      const evidenceCode = evidence.data_type === "lab" && evidence.lab_code && evidence.concept_code
        ? `${evidence.lab_code}:${evidence.concept_code}`
        : (evidence.finding_code || evidence.symptom_code || evidence.lab_code || "-");
      return {
        text: `${evidence.description} ${evidence.id} ${evidence.data_type} ${evidenceCode}`,
        values: { data_type: evidence.data_type || "" },
        html: `<tr>
          <td><strong>${esc(evidence.description)}</strong><div class="mono muted">${esc(evidence.id)}</div></td>
          <td>${esc(evidence.data_type)}</td>
          <td>${evidenceConceptChip(evidence)}</td>
          <td class="mono">${esc(evidenceCode)}</td>
          <td>${src ? `<div class="mono">p${src.page_start}:l${src.line_start}-${src.line_end}</div><div class="quote">${esc(src.quote)}</div>` : "-"}</td>
        </tr>`,
      };
    });
    const evidenceTypeOptions = [...new Set((graph.evidences || []).map((evidence) => evidence.data_type).filter(Boolean))]
      .map((type) => ({ value: type, label: type }));

    const treatmentPlans = semanticNodes.filter((node) => node.node_type === "treatment_plan");
    const planDiseaseIndex = Object.fromEntries(
      semanticRelations
        .filter((relation) => relation.relation_code === "DISEASE_HAS_TREATMENT_PLAN")
        .map((relation) => [relation.target_node_id, relation.source_node_id])
    );
    const planTargets = (planId, code) => semanticRelations
      .filter((relation) => relation.relation_code === code && relation.source_node_id === planId)
      .map((relation) => semanticNodeIndex[relation.target_node_id])
      .filter(Boolean);
    const planNames = (nodes) => nodes.map((node) => node.name).join("、") || "-";
    const treatmentItems = treatmentPlans.map((plan) => {
      const status = plan.review_status || "draft";
      const disease = semanticNodeIndex[planDiseaseIndex[plan.id]];
      const meds = planTargets(plan.id, "TREATMENT_PLAN_INCLUDES_MEDICATION");
      const interventions = planTargets(plan.id, "TREATMENT_PLAN_INCLUDES_INTERVENTION");
      const monitoring = planTargets(plan.id, "TREATMENT_PLAN_HAS_MONITORING");
      const contras = planTargets(plan.id, "TREATMENT_PLAN_HAS_CONTRAINDICATION");
      return {
        text: `${plan.name} ${plan.code || ""} ${disease ? disease.name : ""} ${planNames(meds)} ${planNames(interventions)} ${status}`,
        values: { review_status: status },
        html: `<tr>
          <td><strong>${esc(plan.name)}</strong><div class="mono muted">${esc(plan.code || "-")}</div></td>
          <td>${disease ? `<strong>${esc(disease.name)}</strong><div class="mono muted">${esc(disease.code || disease.concept_code || "")}</div>` : "-"}</td>
          <td>${esc(planNames(meds))}</td>
          <td>${esc(planNames(interventions))}</td>
          <td>${esc(planNames(monitoring))}${contras.length ? `<div class="muted">禁忌：${esc(planNames(contras))}</div>` : ""}</td>
          <td>${semanticRef(plan.source_refs)}</td>
          <td>${reviewBadge(status)}</td>
        </tr>`,
      };
    });

    const linkItems = diseaseLinks.map((link) => {
      const disease = (graph.diseases || []).find((item) => item.code === link.disease_code);
      const evidence = (graph.evidences || []).find((item) => item.id === link.evidence_id);
      return {
        text: `${disease ? disease.name_cn : link.disease_code} ${link.disease_code} ${evidence ? evidence.description : link.evidence_id} ${link.evidence_level || ""}`,
        values: { disease: link.disease_code || "" },
        html: `<tr>
          <td>${esc(disease ? disease.name_cn : link.disease_code)}</td>
          <td>${esc(evidence ? evidence.description : link.evidence_id)}</td>
          <td>${link.weight}</td>
          <td>${esc(link.evidence_level)}</td>
          <td>${link.required ? "是" : "否"}</td>
        </tr>`,
      };
    });
    const linkDiseaseCodes = new Set(diseaseLinks.map((link) => link.disease_code).filter(Boolean));
    const linkDiseaseOptions = (graph.diseases || [])
      .filter((disease) => linkDiseaseCodes.has(disease.code))
      .map((disease) => ({ value: disease.code, label: disease.name_cn || disease.code }));

    const sourceItems = (graph.sources || []).map((source) => ({
      text: `${source.doc_title || ""} ${source.section || ""} ${source.quote || ""} p${source.page_start}:l${source.line_start}-${source.line_end}`,
      values: {},
      html: `<tr>
        <td><strong>${esc(source.doc_title)}</strong><div class="muted">${esc(source.section)}</div></td>
        <td class="mono nowrap">p${source.page_start}:l${source.line_start}-${source.line_end}</td>
        <td><div class="quote">${esc(source.quote)}</div></td>
      </tr>`,
    }));

    content.innerHTML = `
      ${panel("图谱选择", `
        <div class="inline-controls">
          <div class="field"><label>知识图谱</label><select id="graphSelector">${state.graphs.map((item) => `<option value="${esc(item.graph_filename)}" ${item.graph_filename === state.graphFilename ? "selected" : ""}>${esc(item.graph_filename)}</option>`).join("")}</select></div>
          <div class="field"><label>审核人</label><input id="reviewerName" value="${esc(localStorage.getItem("cdssReviewer") || "")}" placeholder="姓名"></div>
        </div>
      `, "", "不同疾病指南可入不同图谱；规则、推理和同步检查按规则所属图谱执行")}
      ${panel("危险操作（测试用）", `
        <div class="inline-controls">
          <div class="field"><label>目标图谱</label><select id="clearGraphSelect"><option value="">加载中…</option></select></div>
          <label><input type="checkbox" id="clearGraphContent" checked> 图谱内容</label>
          <label><input type="checkbox" id="clearGraphRules" checked> 关联规则</label>
          <button id="clearPreviewBtn" class="btn">预览清除范围</button>
          <button id="clearExecuteBtn" class="btn danger" hidden>确认清除</button>
        </div>
        <div id="clearPreviewBody" class="muted">先预览再执行；执行前自动备份（.bak-日期-manual-clear），被删规则保留历史快照。</div>
      `, "", "清空所选图谱的内容和/或该图谱名下的规则，方便重新导入测试；引用该图谱的推理与审核数据会失效")}
      <div class="grid c4">
        <div class="kpi"><span>疾病</span><strong>${graph.diseases.length}</strong></div>
        <div class="kpi"><span>证据</span><strong>${graph.evidences.length}</strong></div>
        <div class="kpi"><span>治疗方案</span><strong>${treatmentPlans.length}</strong></div>
        <div class="kpi"><span>语义节点</span><strong>${semanticNodes.length}</strong></div>
        <div class="kpi"><span>语义关系</span><strong>${semanticRelations.length}</strong></div>
        <div class="kpi"><span>关联</span><strong>${diseaseLinks.length}</strong></div>
        <div class="kpi"><span>指南出处</span><strong>${graph.sources.length}</strong></div>
      </div>
      ${panel("图谱可视化", `<div id="kgGraphView" class="kg-view-mount"></div>`, "", "点击节点查看详情；可按中心疾病聚焦，拖拽节点和空白区域，滚轮缩放", true)}
      ${pagedPanel({
        key: "relations",
        title: "语义关系",
        description: "检验、检查、症状和诊断证据之间的显式逻辑边",
        headers: ["源节点", "关系", "目标节点", "溯源", "审核状态", "操作"],
        items: relationItems,
        filters: [
          { key: "relation_code", label: "关系", options: relationOptions },
          { key: "review_status", label: "审核状态", options: [
            { value: "draft", label: "待审核" },
            { value: "active", label: "已通过" },
            { value: "rejected", label: "已拒绝" },
            { value: "retired", label: "已失效" },
            { value: "stale", label: "待复核" },
          ] },
        ],
        placeholder: "搜索节点、关系…",
      })}
      ${pagedPanel({
        key: "paths",
        title: "诊断逻辑链路",
        description: "按“疾病 → 检验/检查 → 诊断证据”合并展示；症状作为独立关联同步展示，便于核对完整诊断语义",
        headers: ["疾病", "检验·检查·症状", "诊断证据", "指南溯源"],
        items: pathItems,
        filters: [{ key: "disease", label: "疾病", options: pathDiseaseOptions }],
        placeholder: "搜索疾病、检验、检查、证据…",
      })}
      ${pagedPanel({
        key: "nodes",
        title: "语义节点",
        description: "节点编码与解析模板、概念词汇和规则条件共用同一语义",
        headers: ["类型", "名称", "编码", "来源", "审核状态", "操作"],
        items: nodeItems,
        filters: [
          { key: "node_type", label: "类型", options: nodeTypeOptions },
          { key: "review_status", label: "审核状态", options: [
            { value: "draft", label: "待审核" },
            { value: "active", label: "已通过" },
            { value: "rejected", label: "已拒绝" },
            { value: "retired", label: "已失效" },
            { value: "stale", label: "待复核" },
          ] },
        ],
        placeholder: "搜索名称、编码、类型…",
      })}
      ${pagedPanel({
        key: "evidences",
        title: "诊断证据",
        description: "检验和检查状态统一形成诊断证据；医院阈值仍在标准层维护",
        headers: ["证据 / 描述", "数据类型", "判定概念", "编码", "指南出处"],
        items: evidenceItems,
        filters: [{ key: "data_type", label: "数据类型", options: evidenceTypeOptions }],
        placeholder: "搜索描述、编码、数据类型…",
      })}
      ${pagedPanel({
        key: "treatmentPlans",
        title: "治疗方案",
        description: "疾病关联的治疗方案，及其包含的药物、措施、监测要求与禁忌",
        headers: ["治疗方案 / 编码", "关联疾病", "包含药物", "治疗措施", "监测 / 禁忌", "溯源", "审核状态"],
        items: treatmentItems,
        filters: [{ key: "review_status", label: "审核状态", options: [
          { value: "draft", label: "待审核" },
          { value: "active", label: "已通过" },
          { value: "rejected", label: "已拒绝" },
          { value: "retired", label: "已失效" },
          { value: "stale", label: "待复核" },
        ] }],
        placeholder: "搜索方案、疾病、药物、措施…",
      })}
      ${pagedPanel({
        key: "links",
        title: "疾病关联",
        description: "疾病采信的证据及其权重、等级、是否必需",
        headers: ["疾病", "证据", "权重", "等级", "必要"],
        items: linkItems,
        filters: [{ key: "disease", label: "疾病", options: linkDiseaseOptions }],
        placeholder: "搜索疾病、证据…",
      })}
      ${pagedPanel({
        key: "sources",
        title: "指南出处",
        description: "每条知识回溯到的指南原文位置",
        headers: ["文档 / 章节", "页行", "引文"],
        items: sourceItems,
        placeholder: "搜索文档、章节、引文…",
      })}
    `;
    const graphMount = document.getElementById("kgGraphView");
    if (graphMount && window.CdssGraphView) {
      window.CdssGraphView.destroy?.();
      window.CdssGraphView.mount(graphMount, graph, {
        defaultCenterCode: "",
        graphFilename: state.graphFilename,
        profile: graph.profile,
        anchorTypes: graph.profile?.anchor_types || ["disease"],
        domain: graph.profile?.domain || "western",
      });
    }
    ["relations", "paths", "nodes", "evidences", "treatmentPlans", "links", "sources"].forEach(bindPagedTable);
    $("#graphSelector").onchange = (e) => { state.graphFilename = e.target.value; renderGraph(); };
    $("#reviewerName").onchange = (event) => {
      localStorage.setItem("cdssReviewer", event.target.value.trim());
    };
    api("/api/kg/graphs").then((graphs) => {
      const sel = $("#clearGraphSelect");
      if (!sel) return;
      sel.innerHTML = graphs.map((item) => `<option value="${esc(item.graph_filename)}" ${item.graph_filename === state.graphFilename ? "selected" : ""}>${esc(item.graph_filename)}</option>`).join("");
    });
    $("#clearPreviewBtn").onclick = async () => {
      try {
        const payload = {
          graph_filename: $("#clearGraphSelect").value,
          clear_graph: $("#clearGraphContent").checked,
          clear_rules: $("#clearGraphRules").checked,
          dry_run: true,
        };
        if (!payload.clear_graph && !payload.clear_rules) return toast("至少勾选一项清除内容", true);
        const preview = await api("/api/kg/clear", { json: payload });
        const ruleList = preview.rule_ids.length
          ? `（${preview.rule_ids.slice(0, 8).join("、")}${preview.rule_ids.length > 8 ? " 等" : ""}）`
          : "";
        $("#clearPreviewBody").innerHTML = `将清除 <strong>${esc(preview.graph_filename)}</strong>：节点 ${preview.node_count}、关系 ${preview.relation_count}、规则 ${preview.rule_ids.length} 条${ruleList}`;
        $("#clearExecuteBtn").hidden = false;
      } catch (error) { toast(error.message, true); }
    };
    $("#clearExecuteBtn").onclick = async () => {
      const filename = $("#clearGraphSelect").value;
      const typed = prompt(`此操作将清除 ${filename} 的勾选内容（执行前自动备份）。请输入图谱文件名确认：`);
      if ((typed || "").trim() !== filename) return toast("文件名不匹配，已取消", true);
      try {
        const result = await api("/api/kg/clear", {
          json: {
            graph_filename: filename,
            clear_graph: $("#clearGraphContent").checked,
            clear_rules: $("#clearGraphRules").checked,
            dry_run: false,
          },
        });
        toast(`已清除：节点 ${result.node_count} / 关系 ${result.relation_count} / 规则 ${result.cleared_rules}；备份 ${result.backups.join("、") || "无"}`);
        renderGraph();
      } catch (error) { toast(error.message, true); }
    };

    const currentReviewer = () => {
      const value = ($("#reviewerName")?.value || "").trim();
      if (value) localStorage.setItem("cdssReviewer", value);
      return value || "未署名";
    };

    const submitNodeReview = async (nodeId, action) => {
      try {
        if (action === "reject" || action === "retire") {
          const impact = await api(`/api/kg/nodes/${encodeURIComponent(nodeId)}/impact?graph_filename=${encodeURIComponent(state.graphFilename)}`);
          const message = `该操作将影响 ${impact.incident_relations} 条关系、${impact.rules.total} 条规则（已启用 ${impact.rules.active} 条）。确认继续？`;
          if (!confirm(message)) return;
        }
        const note = prompt("审核意见（可选）") || "";
        await api(`/api/kg/nodes/${encodeURIComponent(nodeId)}/review?graph_filename=${encodeURIComponent(state.graphFilename)}`, {
          json: { action, reviewer: currentReviewer(), note },
        });
        toast("节点审核状态已更新");
        renderGraph({ preserveTableState: true });
      } catch (error) { toast(error.message, true); }
    };

    const submitRelationReview = async (index, action) => {
      const relation = semanticRelations[index];
      if (!relation) return;
      try {
        if (action === "reject" || action === "retire") {
          const params = new URLSearchParams({
            graph_filename: state.graphFilename,
            source_node_id: relation.source_node_id,
            relation_code: relation.relation_code,
            target_node_id: relation.target_node_id,
          });
          const impact = await api(`/api/kg/relations/impact?${params}`);
          if (!confirm(`该操作将影响 ${impact.rules.total} 条规则（已启用 ${impact.rules.active} 条）。确认继续？`)) return;
        }
        const note = prompt("审核意见（可选）") || "";
        await api(`/api/kg/relations/review?graph_filename=${encodeURIComponent(state.graphFilename)}`, {
          json: {
            action,
            reviewer: currentReviewer(),
            note,
            source_node_id: relation.source_node_id,
            relation_code: relation.relation_code,
            target_node_id: relation.target_node_id,
          },
        });
        toast("关系审核状态已更新");
        renderGraph({ preserveTableState: true });
      } catch (error) { toast(error.message, true); }
    };

    const openNodeDetail = async (nodeId) => {
      try {
        const detail = await api(`/api/kg/nodes/${encodeURIComponent(nodeId)}?graph_filename=${encodeURIComponent(state.graphFilename)}`);
        const rules = detail.dependents?.rules || [];
        const openImported = Boolean((detail.node.attributes || {}).open_import);
        $("#editorBody").innerHTML = `
          <h3>${esc(detail.node.name)} <span class="mono muted">${esc(detail.node.code || detail.node.id)}</span></h3>
          <p>类型：${esc(typeName[detail.node.node_type] || detail.node.node_type)}　状态：${reviewBadge(detail.node.review_status)}${openImported ? ' <span class="badge draft">开放导入</span>' : ""}</p>
          ${openImported ? '<div class="result-banner warning">该条目来自开放抽取（模板外类型或词库外实体）。确认无误后请「通过」审核，并建议加入实体词典，供后续解析直接命中。</div>' : ""}
          <h4>指南溯源</h4>
          <div class="quote">${esc((detail.node.source_refs || []).join("；") || "-")}</div>
          <h4>依赖规则（${rules.length}）</h4>
          ${table(["规则", "状态", "依赖状态"], rules.map((rule) => `<tr><td class="mono">${esc(rule.rule_id)}</td><td>${reviewBadge(rule.status)}</td><td>${esc(rule.dependency_status || "-")}</td></tr>`))}
          <div class="actions">
            <button class="btn sm primary" id="nodeAddVocab">加入实体词典</button>
            <span class="muted">审核确认后的实体沉淀到共享词典，后续本地/大模型解析可直接命中，不再依赖人工补录。</span>
          </div>
        `;
        const dialog = $("#editorDialog");
        const save = $("#editorSave");
        save.hidden = true;
        dialog.onclose = () => { save.hidden = false; dialog.onclose = null; };
        $("#editorCancel").onclick = () => dialog.close();
        $("#nodeAddVocab").onclick = async () => {
          try {
            const node = detail.node;
            await api(`/api/vocab/entities/${encodeURIComponent(node.node_type)}`, { json: {
              code: node.code || "",
              name: node.name || "",
              aliases: [],
              domain: node.node_type === "syndrome" || node.node_type === "tongue" || node.node_type === "pulse" ? "tcm" : "",
            }});
            toast(`已加入实体词典：${node.name}（${node.code}）`);
          } catch (error) { toast(error.message, true); }
        };
        dialog.showModal();
      } catch (error) { toast(error.message, true); }
    };

    content.onclick = (event) => {
      const nodeButton = event.target.closest("[data-review-node]");
      if (nodeButton) {
        submitNodeReview(nodeButton.dataset.reviewNode, nodeButton.dataset.reviewAction);
        return;
      }
      const relationButton = event.target.closest("[data-review-relation]");
      if (relationButton) {
        submitRelationReview(Number(relationButton.dataset.reviewRelation), relationButton.dataset.reviewAction);
        return;
      }
      const detailButton = event.target.closest("[data-node-detail]");
      if (detailButton) openNodeDetail(detailButton.dataset.nodeDetail);
    };
  }

  async function renderTcmGraph() {
    const graphs = await api("/api/kg/graphs?domain=tcm");
    if (!graphs.length) {
      content.innerHTML = panel(
        "中医图谱",
        '<div class="empty">尚未导入中医知识图谱。请先在「指南导入」选择中医模板并生成知识包，或在「知识包」导入。</div>',
      );
      return;
    }
    if (!state.tcmGraphFilename || !graphs.some((item) => item.graph_filename === state.tcmGraphFilename)) {
      const nonLegacyGraph = graphs.find((item) => item.graph_filename !== "tcm_graph.json");
      state.tcmGraphFilename = nonLegacyGraph?.graph_filename || graphs[0].graph_filename;
    }
    const graph = await api(`/api/kg/graph?graph_filename=${encodeURIComponent(state.tcmGraphFilename)}`);
    const nodes = Array.isArray(graph.nodes) ? graph.nodes : Object.values(graph.nodes || {});
    const relations = Array.isArray(graph.relations) ? graph.relations : Object.values(graph.relations || {});
    if (!nodes.length) {
      content.innerHTML = panel(
        "中医图谱",
        '<div class="empty">该图谱当前为空。请先在「指南导入」上传中医指南，选择中医模板，生成并导入知识包后，证候、舌脉、治法与方药节点会显示在这里。</div>',
        "",
        "图谱已清空，等待重新导入指南生成",
      );
      return;
    }
    const index = Object.fromEntries(nodes.map((node) => [node.id, node]));
    const profile = graph.profile || {};
    const nodeMeta = profile.node_meta || {};
    const typeLabel = (type) => nodeMeta[type]?.label || type;
    const label = (id) => index[id]
      ? `<strong>${esc(index[id].name)}</strong><div class="mono muted">${esc(index[id].code || "")}</div>`
      : `<span class="mono">${esc(id || "-")}</span>`;
    const count = (type) => nodes.filter((node) => node.node_type === type).length;
    const byRelation = (code) => relations.filter((relation) => relation.relation_code === code);
    const refs = (relation) => esc((relation.source_refs || []).join(" / ") || "-");

    const observationRelations = relations.filter((relation) => [
      "SYNDROME_HAS_SYMPTOM", "SYNDROME_HAS_TONGUE", "SYNDROME_HAS_PULSE",
      "SYNDROME_HAS_SIGN", "SYNDROME_HAS_PATHOGENESIS",
    ].includes(relation.relation_code));
    const syndromeMethods = byRelation("SYNDROME_TREATED_BY_METHOD");
    const methodFormulas = byRelation("METHOD_HAS_FORMULA");
    const formulaHerbs = byRelation("FORMULA_CONTAINS_HERB");
    const herbsByFormula = {};
    formulaHerbs.forEach((relation) => {
      (herbsByFormula[relation.target_node_id] = herbsByFormula[relation.target_node_id] || [])
        .push(index[relation.target_node_id]);
    });
    const formulaRows = [];
    syndromeMethods.forEach((sm) => {
      methodFormulas.filter((mf) => mf.source_node_id === sm.target_node_id).forEach((mf) => {
        const herbs = herbsByFormula[mf.target_node_id] || [];
        formulaRows.push(`<tr>
          <td>${label(sm.source_node_id)}</td>
          <td>${label(sm.target_node_id)}</td>
          <td>${label(mf.target_node_id)}</td>
          <td>${herbs.map((herb) => `${esc(herb?.name || "")}${herb?.attributes?.HERB_DOSE ? ` ${esc(herb.attributes.HERB_DOSE)}` : ""}${herb?.attributes?.HERB_ROLE ? `（${esc(herb.attributes.HERB_ROLE)}）` : ""}`).join("、") || "-"}</td>
          <td class="mono">${refs(mf)}</td>
        </tr>`);
      });
    });

    content.innerHTML = `
      ${panel("图谱选择", `
        <div class="inline-controls">
          <div class="field"><label>中医知识图谱</label><select id="tcmGraphSelector">${graphs.map((item) => `<option value="${esc(item.graph_filename)}" ${item.graph_filename === state.tcmGraphFilename ? "selected" : ""}>${esc(item.graph_filename)}（证候 ${item.syndromes || 0}）</option>`).join("")}</select></div>
        </div>
      `, "", "证候是中医推理的锚点；疾病、症状、舌脉、治法、方剂、中药共享同一语义层")}
      <div class="grid c4">
        <div class="kpi"><span>证候</span><strong>${count("syndrome")}</strong></div>
        <div class="kpi"><span>症状</span><strong>${count("symptom")}</strong></div>
        <div class="kpi"><span>舌象</span><strong>${count("tongue")}</strong></div>
        <div class="kpi"><span>脉象</span><strong>${count("pulse")}</strong></div>
        <div class="kpi"><span>治法</span><strong>${count("treatment_method")}</strong></div>
        <div class="kpi"><span>方剂</span><strong>${count("formula")}</strong></div>
        <div class="kpi"><span>中药</span><strong>${count("herb")}</strong></div>
        <div class="kpi"><span>语义关系</span><strong>${relations.length}</strong></div>
      </div>
      ${panel("图谱可视化", '<div id="kgGraphView" class="kg-view-mount"></div>', "", "证候为中心，左侧症状/舌脉，右侧治法/方剂/中药；可按证候聚焦", true)}
      ${panel("辨证链路", table(["证候", "关系", "症状/舌象/脉象/病机", "指南溯源"], observationRelations.map((relation) => `
        <tr>
          <td>${label(relation.source_node_id)}</td>
          <td><strong>${esc(relation.relation_name || relation.relation_code)}</strong><div class="mono muted">${esc(relation.relation_code)}</div></td>
          <td>${label(relation.target_node_id)}</td>
          <td class="mono">${refs(relation)}</td>
        </tr>`)), "", "证候的主症、次症、舌象、脉象与病机依据")}
      ${panel("治法方药链路", table(["证候", "治法", "方剂", "组成（剂量/君臣佐使）", "指南溯源"], formulaRows), "", "证候 → 治法 → 方剂 → 中药的完整处方路径")}
      ${panel("语义节点", table(["类型", "名称", "编码", "属性", "来源"], nodes.map((node) => `
        <tr>
          <td>${esc(typeLabel(node.node_type))}</td>
          <td><strong>${esc(node.name)}</strong></td>
          <td class="mono">${esc(node.code || "-")}</td>
          <td class="mono">${esc(Object.entries(node.attributes || {}).map(([key, value]) => `${key}=${value}`).join("；") || "-")}</td>
          <td class="mono">${esc((node.source_refs || []).join(" / ") || "-")}</td>
        </tr>`)), "", "节点编码与实体词典、解析模板、规则条件共用同一语义")}
      ${panel("语义关系", table(["源节点", "关系", "目标节点", "溯源"], relations.map((relation) => `
        <tr>
          <td>${label(relation.source_node_id)}</td>
          <td><strong>${esc(relation.relation_name || relation.relation_code)}</strong><div class="mono muted">${esc(relation.relation_code)}</div></td>
          <td>${label(relation.target_node_id)}</td>
          <td class="mono">${refs(relation)}</td>
        </tr>`)), "", "证候、观察项、治法、方剂之间的显式逻辑边")}
      ${panel("指南出处", table(["文档 / 章节", "页行", "引文"], (graph.sources || []).map((source) => `
        <tr><td><strong>${esc(source.doc_title)}</strong><div class="muted">${esc(source.section)}</div></td><td class="mono nowrap">p${source.page_start}:l${source.line_start}-${source.line_end}</td><td><div class="quote">${esc(source.quote)}</div></td></tr>`)))}
    `;
    const graphMount = document.getElementById("kgGraphView");
    if (graphMount && window.CdssGraphView) {
      window.CdssGraphView.destroy?.();
      window.CdssGraphView.mount(graphMount, graph, {
        graphFilename: state.tcmGraphFilename,
        profile,
        anchorTypes: profile.anchor_types || ["syndrome", "disease"],
        domain: "tcm",
      });
    }
    $("#tcmGraphSelector").onchange = (event) => {
      state.tcmGraphFilename = event.target.value;
      renderTcmGraph();
    };
  }

  function findingLabel(code) {
    const item = state.terms.exam_finding[code] || state.terms.diagnostic_evidence[code];
    return item && item.name ? `${item.name}（${code}）` : code;
  }

  function conditionText(node) {
    if (!node) return "-";
    if (node.label) return node.label.startsWith("(") && node.label.endsWith(")") ? node.label.slice(1, -1) : node.label;
    if (node.type === "group") return `${node.children.map(conditionText).join(` ${node.operator || "AND"} `)}`;
    if (node.type === "evidence") { const e = state.graph && state.graph.evidences.find((x) => x.id === node.evidence_id); return e ? e.description : node.evidence_id; }
    if (node.type === "lab_concept") return `${termLabel("lab", node.lab_code)} ${conceptName(node.concept_code)}`;
    if (node.type === "exam_finding") return `${termLabel("exam", node.exam_code)} · ${findingLabel(node.finding_code)}`;
    if (node.type === "symptom") return `${termLabel("symptom", node.symptom_code)}${node.duration_minutes ? ` ≥${node.duration_minutes}min` : ""}`;
    if (node.type === "observation") {
      const label = { symptom: "症状", tongue: "舌象", pulse: "脉象", sign: "体征", constitution: "体质" }[node.observation_type] || node.observation_type;
      return `${label}:${node.code}`;
    }
    if (node.type === "fact") return `已确认 ${node.fact_type || "证候"}:${node.code}`;
    return `${node.type} ${node.code || node.evidence_id || ""}`;
  }
  function ruleConceptChips(rule) {
    const codes = resolveRuleConceptCodes(rule.conditions);
    return codes.length ? codes.map((code) => conceptChip(code)).join(" ") : '<span class="muted">无概念条件</span>';
  }
  function ruleSyncBadge(ruleId) {
    const item = state.ruleSync && state.ruleSync.rules.find((rule) => rule.rule_id === ruleId);
    if (!item) return '<span class="badge draft">未校验</span>';
    const title = item.issues.length ? item.issues.join("\n") : "规则条件可解析到知识图谱诊断链路";
    const cls = item.semantic_status === "consistent" ? "active" : "danger";
    const label = item.semantic_status === "consistent" ? "图谱一致" : "图谱不一致";
    return `<span class="badge ${cls}" title="${esc(title)}">${label}</span>`;
  }
  function localDateTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }
  function isoDateTime(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
  }
  function numberValue(value) {
    if (value === "" || value === null || value === undefined) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  function codeList(value) {
    return String(value || "").split(/[\n,，;；]/).map((item) => item.trim()).filter(Boolean);
  }
  function caseRecordFromPatient(patient) {
    return {
      patient_id: patient.patient_id || `TEST-${Date.now()}`,
      hospital_id: patient.hospital_id || state.hospitalId || "XM_FIRST",
      age: patient.age ?? "",
      gender: patient.gender || "M",
      encounter_time: localDateTime(patient.encounter_time),
      chief_complaint: patient.chief_complaint || "持续压榨性胸痛 40 分钟，伴大汗。",
      history_present: patient.history_present || "患者晨起活动后出现胸骨后压榨性疼痛，休息后未缓解，伴大汗、恶心，急诊送入医院。",
      history_past: patient.history_past || "高血压 5 年，2 型糖尿病 3 年。",
      past_history_codes: (patient.past_history || []).join(","),
      current_medications: (patient.current_medications || []).join(","),
      allergies: (patient.allergies || []).join(","),
      symptoms: JSON.parse(JSON.stringify(patient.symptoms || [])),
      labs: JSON.parse(JSON.stringify(patient.labs || [])),
      exam_findings: (patient.exam_findings || []).map((item) => ({
        ...JSON.parse(JSON.stringify(item)),
        detail_summary: item.detail && item.detail.summary ? item.detail.summary : "",
      })),
      vitals: JSON.parse(JSON.stringify(patient.vitals || [])),
    };
  }
  function caseField(item, name, fallback = "") {
    return item && item[name] !== null && item[name] !== undefined ? item[name] : fallback;
  }
  function caseInput(field, item) {
    const rawValue = field.type === "datetime-local" ? localDateTime(caseField(item, field.name)) : caseField(item, field.name, field.fallback || "");
    const control = field.options
      ? `<select data-case-field="${field.name}">${field.options.map((option) => `<option value="${esc(option.value)}" ${String(rawValue) === String(option.value) ? "selected" : ""}>${esc(option.label)}</option>`).join("")}</select>`
      : `<input data-case-field="${field.name}" type="${field.type || "text"}" value="${esc(rawValue)}" ${field.step ? `step="${field.step}"` : ""} placeholder="${esc(field.placeholder || "")}">`;
    return `<div class="field"><label>${esc(field.label)}</label>${control}</div>`;
  }
  function caseMatrix(kind, items, fields) {
    const rows = (items || []).map((item, index) => `
      <div class="matrix-row" data-kind="${kind}" data-index="${index}">
        ${fields.map((field) => caseInput(field, item)).join("")}
        <div class="field"><label>操作</label><button type="button" class="btn sm danger" data-case-delete="${kind}:${index}">删除</button></div>
      </div>`).join("");
    const names = { symptoms: "症状", labs: "检验", exam_findings: "检查发现", vitals: "生命体征" };
    return `<div id="${kind}Rows" class="matrix">${rows || '<div class="empty">暂无数据，可新增一行</div>'}</div>
      <div class="actions"><button type="button" class="btn sm" data-case-add="${kind}">新增${esc(names[kind] || "数据")}</button></div>`;
  }
  function matrixOnly(kind, record) {
    return caseMatrix(kind, record[{ symptoms: "symptoms", labs: "labs", exam_findings: "exam_findings", vitals: "vitals" }[kind]], caseRowFields[kind]).replace(/<div class="actions">[\s\S]*$/, "");
  }
  const caseRowFields = {
    symptoms: [
      { name: "symptom_code", label: "症状编码", placeholder: "CHEST_PAIN_CRUSHING" },
      { name: "symptom_name", label: "症状名称", placeholder: "压榨性胸痛" },
      { name: "present", label: "是否存在", type: "select", options: [{ value: "true", label: "存在" }, { value: "false", label: "不存在" }] },
      { name: "duration_minutes", label: "持续分钟", type: "number", step: "1" },
      { name: "severity", label: "程度", type: "select", options: [{ value: "", label: "未记录" }, { value: "mild", label: "轻度" }, { value: "moderate", label: "中度" }, { value: "severe", label: "重度" }] },
    ],
    labs: [
      { name: "lab_code", label: "检验编码", placeholder: "MYO" },
      { name: "lab_name", label: "检验名称", placeholder: "肌红蛋白" },
      { name: "value", label: "结果值", type: "number", step: "0.001" },
      { name: "qualitative", label: "定性结果", type: "select", options: [{ value: "", label: "未记录" }, { value: "positive", label: "阳性" }, { value: "negative", label: "阴性" }] },
      { name: "unit", label: "单位", placeholder: "ng/mL" },
      { name: "reference_low", label: "参考下限", type: "number", step: "0.001" },
      { name: "reference_high", label: "参考上限", type: "number", step: "0.001" },
      { name: "collected_at", label: "采集时间", type: "datetime-local" },
      { name: "reported_at", label: "报告时间", type: "datetime-local" },
    ],
    exam_findings: [
      { name: "exam_code", label: "检查编码", placeholder: "ECG_12LEAD" },
      { name: "exam_name", label: "检查名称", placeholder: "12导联心电图" },
      { name: "finding_code", label: "发现编码", placeholder: "ST_CONVEX_ELEVATION" },
      { name: "finding_name", label: "发现名称", placeholder: "ST段弓背向上抬高" },
      { name: "present", label: "是否存在", type: "select", options: [{ value: "true", label: "存在" }, { value: "false", label: "不存在" }] },
      { name: "detail_summary", label: "细节描述", placeholder: "II III aVF 导联抬高 0.3mV" },
    ],
    vitals: [
      { name: "vital_code", label: "体征编码", placeholder: "sbp" },
      { name: "value", label: "结果值", type: "number", step: "0.1" },
      { name: "unit", label: "单位", placeholder: "mmHg" },
    ],
  };
  function readCaseMatrix(kind, mapper) {
    return [...content.querySelectorAll(`.matrix-row[data-kind="${kind}"]`)].map((row) => {
      const raw = {};
      row.querySelectorAll("[data-case-field]").forEach((input) => { raw[input.dataset.caseField] = input.value; });
      return mapper(raw);
    });
  }
  function collectCaseRecord() {
    return {
      patient_id: $("#casePatientId").value.trim(),
      hospital_id: $("#caseHospital").value.trim().toUpperCase(),
      age: numberValue($("#caseAge").value),
      gender: $("#caseGender").value,
      encounter_time: $("#caseEncounter").value,
      chief_complaint: $("#caseChief").value.trim(),
      history_present: $("#caseHistoryPresent").value.trim(),
      history_past: $("#caseHistoryPast").value.trim(),
      past_history_codes: $("#casePastCodes").value,
      current_medications: $("#caseMedications").value,
      allergies: $("#caseAllergies").value,
      symptoms: readCaseMatrix("symptoms", (raw) => ({
        symptom_code: raw.symptom_code.trim(), symptom_name: raw.symptom_name.trim(),
        present: raw.present === "true", duration_minutes: numberValue(raw.duration_minutes),
        severity: raw.severity || null,
      })),
      labs: readCaseMatrix("labs", (raw) => ({
        lab_code: raw.lab_code.trim().toUpperCase(), lab_name: raw.lab_name.trim(),
        value: numberValue(raw.value), qualitative: raw.qualitative || null, unit: raw.unit.trim(),
        reference_low: numberValue(raw.reference_low), reference_high: numberValue(raw.reference_high),
        collected_at: isoDateTime(raw.collected_at), reported_at: isoDateTime(raw.reported_at),
      })),
      exam_findings: readCaseMatrix("exam_findings", (raw) => ({
        exam_code: raw.exam_code.trim().toUpperCase(), exam_name: raw.exam_name.trim(),
        finding_code: raw.finding_code.trim().toUpperCase(), finding_name: raw.finding_name.trim(),
        present: raw.present === "true", detail: raw.detail_summary.trim() ? { summary: raw.detail_summary.trim() } : null,
      })),
      vitals: readCaseMatrix("vitals", (raw) => ({
        vital_code: raw.vital_code.trim(), value: numberValue(raw.value), unit: raw.unit.trim(),
      })),
    };
  }
  function buildPatientSnapshot(record) {
    if (!record.patient_id) throw new Error("患者 ID 必填");
    if (!record.hospital_id) throw new Error("执行医院必填");
    return {
      patient_id: record.patient_id, hospital_id: record.hospital_id,
      age: record.age, gender: record.gender, encounter_time: isoDateTime(record.encounter_time),
      labs: record.labs, exam_findings: record.exam_findings, symptoms: record.symptoms,
      vitals: record.vitals, past_history: codeList(record.past_history_codes),
      current_medications: codeList(record.current_medications), allergies: codeList(record.allergies),
    };
  }
  function applyCaseRecord(record) {
    $("#casePatientId").value = record.patient_id || "";
    $("#caseHospital").value = record.hospital_id || "";
    $("#caseAge").value = record.age ?? "";
    $("#caseGender").value = record.gender || "U";
    $("#caseEncounter").value = record.encounter_time || "";
    $("#caseChief").value = record.chief_complaint || "";
    $("#caseHistoryPresent").value = record.history_present || "";
    $("#caseHistoryPast").value = record.history_past || "";
    $("#casePastCodes").value = record.past_history_codes || "";
    $("#caseMedications").value = record.current_medications || "";
    $("#caseAllergies").value = record.allergies || "";
    $("#symptomsRows").innerHTML = matrixOnly("symptoms", record);
    $("#labsRows").innerHTML = matrixOnly("labs", record);
    $("#exam_findingsRows").innerHTML = matrixOnly("exam_findings", record);
    $("#vitalsRows").innerHTML = matrixOnly("vitals", record);
    $("#patientJson").value = JSON.stringify(buildPatientSnapshot(record), null, 2);
  }
  function saveCaseRecord(showNotice = true) {
    const record = collectCaseRecord();
    if (!record.patient_id || !record.hospital_id) throw new Error("患者 ID 和执行医院必填");
    state.caseRecord = record;
    state.caseSavedAt = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    localStorage.setItem(CASE_STORAGE_KEY, JSON.stringify(record));
    $("#patientJson").value = JSON.stringify(buildPatientSnapshot(record), null, 2);
    const savedAt = $("#caseSavedAt"); if (savedAt) savedAt.textContent = `已保存 ${state.caseSavedAt}`;
    const savedText = $("#caseSavedAtText"); if (savedText) savedText.textContent = `保存于 ${state.caseSavedAt}`;
    if (showNotice) toast("模拟病历已保存");
    return record;
  }

  // ==== 审核台：专业化条目显示 + 主从详情面板 ====
  const REVIEW_TYPE_LABELS = { graph_node: "节点", graph_relation: "关系", rule: "规则", package: "知识包" };
  const REVIEW_TABS = [
    ["graph", "待审图谱"], ["relations", "关系审核"], ["bydisease", "按疾病分组"], ["rules", "待审规则"], ["blocked", "被阻断"],
    ["stale", "待复核"], ["active", "已通过"], ["orphan", "孤儿条目"],
    ["dupes", "疑似重复"],
    ["audit", "审核记录"],
  ];
  const REJECT_REASONS = ["编码错误", "证据不足", "关系方向错误", "重复条目", "超出指南范围", "内容过期", "其他"];
  const RELATION_LABELS = {
    DISEASE_HAS_LAB: "需开展检验", LAB_HAS_DIAGNOSTIC_EVIDENCE: "检验形成证据",
    DISEASE_HAS_DIAGNOSTIC_EVIDENCE: "构成诊断依据", DISEASE_HAS_EXAM: "需开展检查",
    EXAM_HAS_DIAGNOSTIC_EVIDENCE: "检查形成证据", DISEASE_HAS_SYMPTOM: "症见",
    SYNDROME_HAS_SYMPTOM: "症见", SYNDROME_HAS_TONGUE: "舌象", SYNDROME_HAS_PULSE: "脉象",
    SYNDROME_HAS_SIGN: "体征", SYNDROME_HAS_PATHOGENESIS: "病机",
    SYNDROME_TREATED_BY_METHOD: "治以", METHOD_HAS_FORMULA: "选方", FORMULA_CONTAINS_HERB: "组成",
  };
  const FIELD_LABELS = {
    node_type: "类型", code: "编码", name: "名称", review_status: "审核状态",
    source_node_id: "源节点", relation_code: "关系编码", relation_name: "关系名称",
    target_node_id: "目标节点", source_refs: "指南溯源",
  };
  const FLAG_LABELS = {
    endpoint_inactive: "端点未通过", no_source_refs: "无溯源",
    direction_suspect: "方向可疑", self_loop: "自环", duplicate_pair: "重复连线",
  };
  function flagChipsHtml(item) {
    return (item.flags || []).map((flag) => `<span class="flag-chip" title="${esc(flag.message)}">⚠ ${esc(FLAG_LABELS[flag.code] || flag.code)}</span>`).join(" ");
  }
  function relationMatrixHtml(items) {
    const relItems = items.filter((item) => item.item_type === "graph_relation");
    if (!relItems.length) return "";
    const statuses = ["draft", "stale", "active", "rejected", "retired"];
    const byCode = new Map();
    relItems.forEach((item) => {
      const code = item.relation_code || "-";
      if (!byCode.has(code)) byCode.set(code, Object.fromEntries(statuses.map((s) => [s, 0])));
      byCode.get(code)[item.status] = (byCode.get(code)[item.status] || 0) + 1;
    });
    const rows = [...byCode.entries()].map(([code, counts]) =>
      `<tr><td>${esc(RELATION_LABELS[code] || code)}</td>${statuses.map((s) => `<td class="num ${counts[s] ? "" : "zero"}">${counts[s] || "·"}</td>`).join("")}</tr>`);
    return `<details class="relation-matrix"><summary>关系类型 × 状态概览（当前列表 ${relItems.length} 条）</summary>
      ${table(["关系类型", ...statuses.map((s) => REVIEW_LABELS[s] || s)], rows)}</details>`;
  }
  const ACTION_DONE_LABELS = { approve: "已通过", reject: "已拒绝", retire: "已失效", restore: "已恢复" };

  function reviewItemKey(item) {
    return `${item.item_type}:${item.graph_filename || ""}:${item.node_id || item.item_id || item.rule_id || item.package_id || ""}`;
  }
  function relationLabel(item) {
    return RELATION_LABELS[item.relation_code] || item.relation_name || item.name || item.relation_code;
  }
  function reviewItemTitle(item) {
    if (item.item_type === "rule") return item.name || item.rule_id || "未命名规则";
    if (item.item_type === "graph_relation") {
      return `${item.source_name || item.source_node_id} ─${relationLabel(item)}→ ${item.target_name || item.target_node_id}`;
    }
    if (item.item_type === "package") return `${item.package_id} · ${item.doc_title || ""}`;
    return `${item.name || item.node_id}（${item.code || "-"}）`;
  }
  function miniStatus(status) {
    const value = status || "draft";
    const label = value === "missing" ? "缺失" : (REVIEW_LABELS[value] || value);
    return `<span class="mini-status ${esc(value)}">${esc(label)}</span>`;
  }
  function shortTime(value) {
    const full = formatTime(value);
    return full === "-" ? "-" : full.slice(5, 16);
  }
  function reviewReviewer() {
    return (($("#reviewCenterReviewer")?.value || "") || localStorage.getItem("cdssReviewer") || "").trim() || "未署名";
  }
  function relationEndpointBlocked(item) {
    if (item.item_type !== "graph_relation") return false;
    return (item.source_status || "draft") !== "active" || (item.target_status || "draft") !== "active";
  }
  function itemPayload(item) {
    return {
      item_type: item.item_type,
      graph_filename: item.graph_filename || "",
      node_id: item.node_id || "",
      source_node_id: item.source_node_id || "",
      relation_code: item.relation_code || "",
      target_node_id: item.target_node_id || "",
      rule_id: item.rule_id || "",
    };
  }
  function relationSemHtml(item) {
    return `<span class="relation-sem">
      <span class="sem-endpoint">${esc(item.source_name || item.source_node_id)}${miniStatus(item.source_status)}</span>
      <span class="sem-arrow">${esc(relationLabel(item))}<span class="arrow-glyph">→</span></span>
      <span class="sem-endpoint">${esc(item.target_name || item.target_node_id)}${miniStatus(item.target_status)}</span>
    </span>`;
  }
  function reviewProgressHtml(summary) {
    if (!summary) return "";
    const sections = [["graph_nodes", "图谱节点"], ["graph_relations", "图谱关系"], ["rules", "规则"]];
    const blocks = sections.map(([key, label]) => {
      const counts = summary[key] || {};
      const total = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
      const done = Number(counts.active || 0) + Number(counts.rejected || 0) + Number(counts.retired || 0);
      const pct = total ? Math.round((done / total) * 100) : 0;
      const pending = total - done;
      return `<div class="progress-block">
        <div class="progress-label"><span>${esc(label)}</span><span class="muted">${done}/${total} 已处理${pending ? ` · 待处理 ${pending}` : " · 全部处理完毕"}</span></div>
        <div class="review-progress-track"><div class="review-progress-fill" style="width:${pct}%"></div></div>
      </div>`;
    });
    return `<div class="review-progress">${blocks.join("")}</div>`;
  }

  async function submitReviewAction(item, action, note) {
    const reviewer = reviewReviewer();
    if (item.item_type === "graph_node") {
      await api(`/api/kg/nodes/${encodeURIComponent(item.node_id)}/review?graph_filename=${encodeURIComponent(item.graph_filename)}`, {
        json: { action, reviewer, note },
      });
    } else if (item.item_type === "graph_relation") {
      await api(`/api/kg/relations/review?graph_filename=${encodeURIComponent(item.graph_filename)}`, {
        json: {
          action, reviewer, note,
          source_node_id: item.source_node_id,
          relation_code: item.relation_code,
          target_node_id: item.target_node_id,
        },
      });
    } else if (item.item_type === "rule") {
      await api(`/api/rules/${encodeURIComponent(item.rule_id)}/review`, { json: { action, reviewer, note } });
    } else {
      throw new Error("该条目类型暂不支持单条审核操作");
    }
  }

  async function quickReview(item, action) {
    try {
      if (action === "approve" && relationEndpointBlocked(item)) {
        toast("该关系的端点节点尚未全部「已通过」，请先审核两端节点", true);
        return;
      }
      await submitReviewAction(item, action, "");
      toast(`${ACTION_DONE_LABELS[action] || "已处理"}：${reviewItemTitle(item)}`);
      renderReviewCenter();
    } catch (error) { toast(error.message, true); }
  }

  function openReasonDialog(target, action) {
    const items = target.multi || [target];
    const dialog = $("#editorDialog");
    const save = $("#editorSave");
    const verb = action === "reject" ? "拒绝" : "失效";
    save.hidden = false;
    save.textContent = `确认${verb}`;
    $("#editorBody").innerHTML = `
      <h3>${verb}${items.length > 1 ? ` ${items.length} 条条目` : ""}：${esc(items.length > 1 ? "批量操作" : reviewItemTitle(items[0]))}</h3>
      <p class="muted">原因码会写入审核意见和审计记录，便于后续统计高频退回原因。</p>
      <div class="reason-chips" id="reasonChips">
        ${REJECT_REASONS.map((reason) => `<button type="button" class="reason-chip" data-reason="${esc(reason)}">${esc(reason)}</button>`).join("")}
      </div>
      <div class="field"><label>补充说明（可选）</label><textarea id="rejectNote" rows="3" placeholder="例如：证据等级仅 C 级，且新版指南已删除该推荐"></textarea></div>
    `;
    let reason = "";
    $("#reasonChips").onclick = (event) => {
      const chip = event.target.closest(".reason-chip");
      if (!chip) return;
      $("#reasonChips").querySelectorAll(".reason-chip").forEach((item) => item.classList.remove("selected"));
      chip.classList.add("selected");
      reason = chip.dataset.reason;
    };
    $("#editorCancel").onclick = () => dialog.close();
    save.onclick = async () => {
      if (!reason) { toast("请选择一个原因码", true); return; }
      const extra = $("#rejectNote").value.trim();
      const note = extra ? `[${reason}] ${extra}` : `[${reason}]`;
      try {
        if (target.multi) {
          const result = await api("/api/review/bulk", { json: {
            items: target.multi.map(itemPayload),
            action,
            reviewer: reviewReviewer(),
            note,
            reason,
          }});
          toast(`批量${verb}完成：成功 ${result.succeeded}，失败 ${result.failed}`, result.failed > 0);
        } else {
          await submitReviewAction(target, action, note);
          toast(`${ACTION_DONE_LABELS[action] || "已处理"}：${reviewItemTitle(target)}`);
        }
        dialog.close();
        renderReviewCenter();
      } catch (error) { toast(error.message, true); }
    };
    dialog.onclose = () => { save.hidden = false; save.textContent = "保存"; dialog.onclose = null; };
    dialog.showModal();
  }

  function fieldLabel(key) {
    if (key.startsWith("attributes.")) return `属性·${key.slice("attributes.".length)}`;
    return FIELD_LABELS[key] || key;
  }
  function flattenContent(contentValue) {
    const flat = {};
    Object.entries(contentValue || {}).forEach(([key, value]) => {
      if (key === "attributes" && value && typeof value === "object") {
        Object.entries(value).forEach(([attrKey, attrValue]) => { flat[`attributes.${attrKey}`] = String(attrValue ?? ""); });
      } else if (Array.isArray(value)) {
        flat[key] = value.map((entry) => String(entry)).join("；");
      } else if (value === null || value === undefined || typeof value !== "object") {
        flat[key] = String(value ?? "");
      }
    });
    return flat;
  }
  function diffTableHtml(oldContent, newContent) {
    const before = flattenContent(oldContent);
    const after = flattenContent(newContent);
    const skipKeys = ["id", "reviewed_at", "reviewed_by", "review_note", "orphaned_from", "content_hash", "open_import"];
    const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
      .filter((key) => !skipKeys.includes(key));
    if (!keys.length) return "";
    const rows = keys.map((key) => {
      const oldValue = before[key] ?? "";
      const newValue = after[key] ?? "";
      const changed = oldValue !== newValue;
      return `<tr class="${changed ? "diff-changed" : ""}">
        <td class="mono">${esc(fieldLabel(key))}</td>
        <td class="${changed ? "diff-old" : "muted"}">${esc(oldValue || "-")}</td>
        <td class="${changed ? "diff-new" : "muted"}">${esc(newValue || "-")}</td>
      </tr>`;
    });
    const changedCount = keys.filter((key) => (before[key] ?? "") !== (after[key] ?? "")).length;
    return `<p class="muted">${changedCount ? `${changedCount} 个字段发生变化（高亮行）` : "字段内容一致，仅审核状态需要复核"}</p>
      ${table(["字段", "上一版本", "当前版本"], rows)}`;
  }
  async function entityHistoryHtml(item, entityType, entityId, currentContent) {
    const params = new URLSearchParams({ graph_filename: item.graph_filename || "", entity_type: entityType, limit: "10" });
    const data = await api(`/api/kg/entities/${encodeURIComponent(entityId)}/history?${params}`);
    const rows = data.items || [];
    if (!rows.length) return "";
    const versions = table(["版本", "归档时间", "原因"], rows.map((row) => `<tr><td>#${esc(row.seq)}</td><td class="nowrap muted">${esc(formatTime(row.timestamp))}</td><td>${esc(row.reason || "-")}</td></tr>`));
    return `<div class="detail-section"><h4>变更对比（导入覆盖前 → 当前）</h4>${diffTableHtml(rows[0].content, currentContent)}</div>
      <div class="detail-section"><h4>历史版本（${rows.length}）</h4>${versions}</div>`;
  }
  // 汇总所有模板的属性契约，用于把图谱节点上的属性编码渲染为模板中文名。
  function templateAttrLabelMap() {
    const labels = {};
    state.templates.forEach((t) => (t.attributes || []).forEach((a) => {
      if (a.attribute_code && !labels[a.attribute_code]) labels[a.attribute_code] = a.name || a.attribute_code;
    }));
    return labels;
  }
  function attrTableHtml(attributes) {
    const entries = Object.entries(attributes || {});
    if (!entries.length) return '<div class="muted">无属性</div>';
    const labels = templateAttrLabelMap();
    return table(["属性", "值"], entries.map(([key, value]) => {
      const label = labels[key];
      const nameCell = label
        ? `${esc(label)} <span class="mono muted">${esc(key)}</span>`
        : `<span class="mono">${esc(key)}</span> <span class="badge draft" title="模板中未定义该属性">未定义</span>`;
      return `<tr><td>${nameCell}</td><td>${esc(String(value))}</td></tr>`;
    }));
  }
  function auditListHtml(records, limit = 5) {
    const rows = (records || []).slice(0, limit).map((row) => `<tr>
      <td class="nowrap muted">${esc(formatTime(row.ts))}</td>
      <td>${esc(row.action || "-")}${row.before_status ? ` <span class="mono muted">(${esc(row.before_status)}→${esc(row.after_status)})</span>` : ""}</td>
      <td>${esc(row.reviewer || "-")}</td>
      <td>${esc(row.note || "-")}</td>
    </tr>`);
    if (!rows.length) return '<div class="muted">暂无审核记录</div>';
    return table(["时间", "动作", "审核人", "意见"], rows);
  }
  function dependentRuleTable(rules) {
    return rules.length
      ? table(["规则", "状态", "依赖状态"], rules.map((rule) => `<tr><td><strong>${esc(rule.name || rule.rule_id)}</strong><div class="mono muted">${esc(rule.rule_id)}</div></td><td>${reviewBadge(rule.status)}</td><td>${esc(rule.dependency_status || "-")}</td></tr>`))
      : '<div class="muted">没有规则引用该条目</div>';
  }

  function detailHeadHtml(item) {
    return `<div class="detail-head">
      <div class="detail-title"><span class="type-chip">${esc(REVIEW_TYPE_LABELS[item.item_type] || item.item_type)}</span>
        <strong>${esc(reviewItemTitle(item))}</strong> ${reviewBadge(item.status)}
        ${item.orphaned_from?.length ? `<span class="badge danger" title="${esc(item.orphaned_from.join("、"))}">孤儿</span>` : ""}</div>
      <div class="detail-sub mono">${esc(item.graph_filename || "")}${item.updated_at ? ` · 最近审核 ${esc(formatTime(item.updated_at))}` : ""}</div>
    </div>`;
  }
  function detailActionsHtml(item) {
    const status = item.status || "draft";
    const buttons = [];
    if (["draft", "stale", "testing", "blocked"].includes(status)) buttons.push('<button class="btn sm primary" data-detail-action="approve">通过</button>');
    if (!["active", "rejected", "retired"].includes(status)) buttons.push('<button class="btn sm danger" data-detail-action="reject">拒绝</button>');
    if (status === "active") buttons.push('<button class="btn sm" data-detail-action="retire">失效</button>');
    if (["rejected", "retired"].includes(status)) buttons.push('<button class="btn sm" data-detail-action="restore">恢复</button>');
    return buttons.length ? `<div class="actions detail-actions">${buttons.join("")}</div>` : "";
  }

  // 溯源引用 "DOC-xxx_p3_l100-20" → { docId, page, start, end }；生成器会把
  // l100-120 截成 l100-20（end 丢首位），end < start 时按 start 前缀补全位数。
  function parseSourceRef(ref) {
    const match = /^([A-Za-z0-9-]+)_p(\d+)_l(\d+)-(\d+)$/.exec(String(ref || ""));
    if (!match) return null;
    const start = Number(match[3]);
    let end = Number(match[4]);
    if (end < start) {
      const scale = Math.pow(10, match[4].length);
      end = Math.floor(start / scale) * scale + end;
    }
    return { docId: match[1], page: Number(match[2]), start, end };
  }

  const sourceRefLineCache = {}; // "DOC-x:3" -> 该页行数组；null = 拉取失败（文档已删等）
  function refDocTitle(docId) {
    return (state.reviewDocOptions || []).find((d) => d.doc_id === docId)?.doc_title || docId;
  }
  async function fetchRefLines(docId, page) {
    const key = `${docId}:${page}`;
    if (!(key in sourceRefLineCache)) {
      try {
        const data = await api(`/api/pdf/${encodeURIComponent(docId)}/lines?page=${page}`);
        sourceRefLineCache[key] = data.lines || [];
      } catch (_) {
        sourceRefLineCache[key] = null;
      }
    }
    return sourceRefLineCache[key];
  }
  async function sourceRefBlock(ref) {
    const parsed = parseSourceRef(ref);
    if (!parsed) return `<div class="ref-context missing mono">${esc(ref)}</div>`;
    const head = `<div class="ref-head"><strong>${esc(refDocTitle(parsed.docId))}</strong><span class="muted">第 ${parsed.page} 页 · 第 ${parsed.start}–${parsed.end} 行</span></div>`;
    const lines = await fetchRefLines(parsed.docId, parsed.page);
    if (!lines) return `${head}<div class="ref-context missing">原文不可用（文档可能已删除）：<span class="mono">${esc(ref)}</span></div>`;
    const pad = 2;
    const render = (line, cls) => `<div class="ref-line ${cls}"><span class="ref-no">${line.line}</span><span class="ref-text">${esc(line.text)}</span></div>`;
    const body = [
      ...lines.filter((l) => l.line >= parsed.start - pad && l.line < parsed.start).map((l) => render(l, "ctx")),
      ...lines.filter((l) => l.line >= parsed.start && l.line <= parsed.end).map((l) => render(l, "hit")),
      ...lines.filter((l) => l.line > parsed.end && l.line <= parsed.end + pad).map((l) => render(l, "ctx")),
    ].join("");
    return `${head}<div class="ref-context">${body || '<div class="muted">该页未找到引用行</div>'}</div>`;
  }
  async function sourceRefsHtml(refs, { expand = 3 } = {}) {
    const list = refs || [];
    if (!list.length) return "";
    const blocks = [];
    for (const ref of list.slice(0, expand)) blocks.push(await sourceRefBlock(ref));
    const rest = list.slice(expand).map((ref) => {
      const parsed = parseSourceRef(ref);
      const label = parsed ? `P${parsed.page} L${parsed.start}-${parsed.end}` : ref;
      return `<button class="btn sm ref-chip" data-ref="${esc(ref)}" title="展开该段原文">${esc(label)}</button>`;
    }).join("");
    return `<div class="ref-list">${blocks.join("")}${rest ? `<div class="ref-more">${rest}</div>` : ""}</div>`;
  }

  function relatedItemsHtml(detail, item) {
    const relations = detail.relations || [];
    if (!relations.length) {
      return '<div class="detail-section"><h4>关联条目</h4><div class="muted">暂无关联条目</div></div>';
    }
    const row = (entry, asSource) => {
      const neighbor = asSource ? entry.target : entry.source;
      const label = relationLabel({ relation_code: entry.relation.relation_code, relation_name: entry.relation.relation_name });
      return `<div class="related-row">
        <button class="related-rel" data-related-relation="${esc(entry.key)}"
          data-relation-code="${esc(entry.relation.relation_code)}"
          data-source-node="${esc(entry.source.node_id)}"
          data-target-node="${esc(entry.target.node_id)}"
          data-relation-status="${esc(entry.relation.review_status || "")}"
          title="查看关系详情">${esc(asSource ? label : `← ${label}`)}</button>
        <button class="related-node" data-related-node="${esc(neighbor.node_id)}">
          <span class="type-chip">${esc(typeName[neighbor.node_type] || neighbor.node_type || "-")}</span>
          <strong>${esc(neighbor.name)}</strong>${miniStatus(neighbor.status)}
        </button>
      </div>`;
    };
    const asSource = relations.filter((e) => e.source.node_id === item.node_id);
    const asTarget = relations.filter((e) => e.target.node_id === item.node_id);
    const section = (title, list, flag) => {
      if (!list.length) return "";
      const visible = list.slice(0, 20).map((e) => row(e, flag)).join("");
      const extra = list.slice(20);
      const hidden = extra.length
        ? `<div class="related-extra" hidden>${extra.map((e) => row(e, flag)).join("")}</div>
           <div class="related-row"><button class="btn sm" data-related-expand>显示全部 ${list.length} 条</button></div>`
        : "";
      return `<div class="detail-section"><h4>${title}（${list.length}）</h4>
        <div class="related-list">${visible}${hidden}</div>
      </div>`;
    };
    return section("作为源", asSource, true) + section("作为目标", asTarget, false);
  }

  async function nodeDetailHtml(item) {
    const detail = await api(`/api/kg/nodes/${encodeURIComponent(item.node_id)}?graph_filename=${encodeURIComponent(item.graph_filename)}`);
    const node = detail.node || {};
    const rules = detail.dependents?.rules || [];
    let historyHtml = "";
    if (item.status === "stale") {
      try { historyHtml = await entityHistoryHtml(item, "node", item.node_id, node); } catch (_) { historyHtml = ""; }
    }
    const refsHtml = (node.source_refs || []).length ? await sourceRefsHtml(node.source_refs) : "";
    const relatedHtml = relatedItemsHtml(detail, item);
    return `
      <div class="detail-section endpoint-meta">
        <div><span class="muted">类型</span><strong>${esc(typeName[node.node_type] || node.node_type || "-")}</strong></div>
        <div><span class="muted">编码</span><span class="mono">${esc(node.code || "-")}</span></div>
      </div>
      ${refsHtml ? `<div class="detail-section"><h4>指南溯源</h4>${refsHtml}</div>` : ""}
      ${relatedHtml}
      ${(node.attribute_violations || []).length ? `<div class="detail-section"><h4>属性校验待复核（${node.attribute_violations.length}）</h4>${table(["属性", "问题"], node.attribute_violations.map((v) => `<tr><td class="mono">${esc(v.attribute_code)}</td><td>${esc(v.issue || "-")}</td></tr>`))}</div>` : ""}
      <div class="detail-section"><h4>属性</h4>${attrTableHtml(node.attributes)}</div>
      ${historyHtml}
      <div class="detail-section"><h4>依赖规则（${rules.length}）</h4>${dependentRuleTable(rules)}</div>
      <div class="detail-section"><h4>审核记录</h4>${auditListHtml(detail.audit)}</div>`;
  }

  async function relationDetailHtml(item) {
    const detail = await api(`/api/kg/relations/${encodeURIComponent(item.item_id)}?graph_filename=${encodeURIComponent(item.graph_filename)}`);
    const relation = detail.relation || {};
    const endpointCard = (endpoint, role) => `
      <div class="endpoint-card">
        <div class="muted endpoint-role">${esc(role)}</div>
        <strong>${esc(endpoint.name)}</strong>
        <div class="mono muted">${esc(endpoint.code || endpoint.node_id)}</div>
        <div>${miniStatus(endpoint.status)}</div>
      </div>`;
    let historyHtml = "";
    if (item.status === "stale") {
      try { historyHtml = await entityHistoryHtml(item, "relation", item.item_id, relation); } catch (_) { historyHtml = ""; }
    }
    const rulesInfo = detail.rules || { total: 0, items: [] };
    return `
      <div class="detail-section">
        <div class="relation-flow">
          ${endpointCard(detail.source, "源节点")}
          <div class="relation-arrow"><span class="rel-name">${esc(relationLabel(item))}</span><span class="mono muted">${esc(item.relation_code)}</span></div>
          ${endpointCard(detail.target, "目标节点")}
        </div>
      </div>
      <div class="detail-section"><h4>关系属性</h4>${attrTableHtml(relation.attributes)}</div>
      ${(relation.source_refs || []).length ? `<div class="detail-section"><h4>指南溯源</h4>${await sourceRefsHtml(relation.source_refs)}</div>` : ""}
      ${historyHtml}
      <div class="detail-section"><h4>依赖规则（${rulesInfo.total}）</h4>${dependentRuleTable(rulesInfo.items)}</div>
      <div class="detail-section"><h4>审核记录</h4>${auditListHtml(detail.audit)}</div>`;
  }

  async function ruleDetailHtml(item) {
    const [rule, deps, history] = await Promise.all([
      api(`/api/rules/${encodeURIComponent(item.rule_id)}`),
      api(`/api/rules/${encodeURIComponent(item.rule_id)}/graph-dependencies`),
      api(`/api/rules/${encodeURIComponent(item.rule_id)}/history`),
    ]);
    const refs = deps.references || [];
    const issues = deps.issues || [];
    const historyRows = (history || []).slice(0, 8);
    return `
      <div class="detail-section endpoint-meta">
        <div><span class="muted">类型</span><strong>${esc(rule.rule_type || "-")}</strong> ${badge(normalizeDomainClient(rule.domain))}</div>
        <div><span class="muted">版本</span>v${esc(rule.version ?? "-")} · 优先级 ${esc(rule.priority ?? "-")}</div>
        <div><span class="muted">目标</span>${esc(rule.disease_name || rule.disease_code || "-")}${rule.disease_code ? ` <span class="mono muted">(${esc(rule.disease_code)})</span>` : ""}</div>
        <div><span class="muted">所属图谱</span><span class="mono">${esc(rule.graph_filename || "-")}</span></div>
      </div>
      <div class="detail-section"><h4>触发条件</h4><div class="quote">${esc(rule.condition_text || "-")}</div><div class="concept-chips">${ruleConceptChips(rule)}</div></div>
      ${rule.source?.quote ? `<div class="detail-section"><h4>指南溯源</h4>
        <div class="ref-head"><strong>${esc(rule.source.guideline_title || rule.source.guideline_id || "-")}</strong><span class="muted">${rule.source.page_start ? `第 ${rule.source.page_start} 页` : ""}${rule.source.section ? ` · ${esc(rule.source.section)}` : ""}</span></div>
        <div class="ref-context"><div class="ref-line hit"><span class="ref-text">${esc(rule.source.quote)}</span></div></div>
      </div>` : ""}
      ${issues.length
        ? `<div class="detail-section"><h4>图谱校验问题</h4><ul class="issue-list">${issues.map((issue) => `<li>${esc(issue)}</li>`).join("")}</ul></div>`
        : '<div class="detail-section"><h4>图谱校验</h4><span class="badge active">语义一致</span></div>'}
      <div class="detail-section"><h4>图谱依赖（${refs.length}）</h4>${refs.length ? table(["类型", "条目", "状态"], refs.map((ref) => `<tr><td>${ref.kind === "node" ? "节点" : "关系"}</td><td class="mono">${esc(ref.kind === "node" ? ref.node_id : `${ref.source_node_id} ─${ref.relation_code}→ ${ref.target_node_id}`)}</td><td>${ref.status === "active" ? '<span class="badge active">已通过</span>' : miniStatus(ref.status)}</td></tr>`)) : '<div class="muted">无图谱依赖</div>'}</div>
      <div class="detail-section"><h4>版本历史${(history || []).length ? `（${history.length}）` : ""}</h4>${historyRows.length ? table(["版本", "时间", "操作", "操作人"], historyRows.map((row) => `<tr><td>#${esc(row.seq)}</td><td class="nowrap muted">${esc(formatTime(row.timestamp))}</td><td>${esc(row.action || "-")}</td><td>${esc(row.actor || "-")}</td></tr>`)) : '<div class="muted">暂无版本历史</div>'}</div>`;
  }

  let reviewDetailToken = 0;
  function markSelectedRow() {
    content.querySelectorAll(".review-row").forEach((row) => {
      row.classList.toggle("selected", row.dataset.reviewKey === state.reviewSelectedKey);
    });
    const row = content.querySelector(`.review-row[data-review-key="${CSS.escape(state.reviewSelectedKey)}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }
  async function loadReviewDetail(item) {
    const mount = $("#reviewDetail");
    if (!mount) return;
    state.reviewSelectedKey = reviewItemKey(item);
    markSelectedRow();
    const token = ++reviewDetailToken;
    mount.innerHTML = '<div class="empty">加载详情…</div>';
    try {
      let body = "";
      if (item.item_type === "graph_node") body = await nodeDetailHtml(item);
      else if (item.item_type === "graph_relation") body = await relationDetailHtml(item);
      else if (item.item_type === "rule") body = await ruleDetailHtml(item);
      else body = '<div class="empty">该条目类型暂不支持详情展示</div>';
      if (token !== reviewDetailToken) return;
      mount.innerHTML = `${detailHeadHtml(item)}${detailActionsHtml(item)}<div class="detail-body">${body}</div>`;
      mount.querySelectorAll(".ref-chip[data-ref]").forEach((chip) => chip.onclick = async () => {
        chip.disabled = true;
        const wrap = document.createElement("div");
        wrap.innerHTML = await sourceRefBlock(chip.dataset.ref);
        chip.replaceWith(...wrap.childNodes);
      });
      mount.querySelectorAll("[data-related-node]").forEach((btn) => btn.onclick = () => {
        loadReviewDetail({ item_type: "graph_node", node_id: btn.dataset.relatedNode, graph_filename: item.graph_filename, status: "" });
      });
      mount.querySelectorAll("[data-related-relation]").forEach((btn) => btn.onclick = () => {
        loadReviewDetail({
          item_type: "graph_relation",
          item_id: btn.dataset.relatedRelation,
          graph_filename: item.graph_filename,
          relation_code: btn.dataset.relationCode,
          source_node_id: btn.dataset.sourceNode,
          target_node_id: btn.dataset.targetNode,
          status: btn.dataset.relationStatus || "",
        });
      });
      mount.querySelectorAll("[data-related-expand]").forEach((btn) => btn.onclick = () => {
        const extra = btn.closest(".related-list")?.querySelector(".related-extra");
        if (extra) { extra.hidden = false; btn.closest(".related-row").remove(); }
      });
    } catch (error) {
      if (token !== reviewDetailToken) return;
      mount.innerHTML = `<div class="empty">详情加载失败：${esc(error.message)}</div>`;
    }
  }

  async function bulkReview(action) {
    let selected = [...content.querySelectorAll("[data-review-select]:checked")]
      .map((input) => state.reviewItems.find((item) => reviewItemKey(item) === input.dataset.reviewSelect))
      .filter(Boolean);
    if (!selected.length) { toast("请先勾选条目", true); return; }
    if (action === "approve") {
      const blockedCount = selected.filter(relationEndpointBlocked).length;
      if (blockedCount) {
        const rest = selected.length - blockedCount;
        const proceed = confirm(
          `${blockedCount} 条关系的端点节点还不是「已通过」，关系审核要求两端节点先通过，直接提交会被拒绝。` +
          (rest ? `\n\n确定后仅提交其余 ${rest} 条；` : "\n\n当前没有可提交条目；") + "取消则不做操作。"
        );
        if (!proceed) return;
        selected = selected.filter((item) => !relationEndpointBlocked(item));
        if (!selected.length) { toast("没有可提交的条目，请先审核相关端点节点", true); return; }
      }
    }
    if (action === "reject" || action === "retire") {
      openReasonDialog({ multi: selected }, action);
      return;
    }
    try {
      const result = await api("/api/review/bulk", { json: {
        items: selected.map(itemPayload),
        action,
        reviewer: reviewReviewer(),
        note: "",
      }});
      const firstError = (result.results || []).find((entry) => !entry.ok);
      const detail = firstError
        ? `；首个失败：${typeof firstError.error === "string" ? firstError.error : JSON.stringify(firstError.error)}`
        : "";
      toast(`批量完成：成功 ${result.succeeded}，失败 ${result.failed}${detail}`, result.failed > 0);
      renderReviewCenter();
    } catch (error) { toast(error.message, true); }
  }

  function bindReviewTabs() {
    content.querySelectorAll("[data-review-tab]").forEach((button) => button.onclick = () => {
      state.reviewTab = button.dataset.reviewTab;
      state.reviewSelectedKey = "";
      renderReviewCenter();
    });
  }

  function bindDupeMerge(graphFilename) {
    if (!graphFilename) return;
    content.querySelectorAll("[data-dupe-merge]").forEach((button) => button.onclick = async () => {
      const groupEl = button.closest(".dupe-group");
      const checked = groupEl.querySelector("input[type=radio]:checked");
      if (!checked) { toast("请选择保留节点", true); return; }
      const survivor = checked.value;
      const labels = [...groupEl.querySelectorAll(".dupe-node")].map((el) => ({
        id: el.querySelector("input[type=radio]").value,
        code: el.querySelector(".mono").textContent,
      }));
      const survivorCode = labels.find((l) => l.id === survivor).code;
      const absorbed = labels.filter((l) => l.id !== survivor);
      if (!confirm(`保留 ${survivorCode}，吸收：${absorbed.map((l) => l.code).join("、")}？\n关联关系将重挂到保留节点，引用被吸收编码的规则会自动改写（合并前自动备份）。`)) return;
      button.disabled = true;
      try {
        const result = await api("/api/kg/merge-nodes", { json: {
          graph_filename: graphFilename, survivor_node_id: survivor,
          absorbed_node_ids: absorbed.map((l) => l.id),
        }});
        toast(`合并完成：重挂关系 ${result.merged_relations}、去重 ${result.deduped_relations}、改写规则 ${result.updated_rules.length} 条`);
        renderReviewCenter();
      } catch (error) { toast(error.message, true); button.disabled = false; }
    });
  }

  async function renderReviewCenter() {
    // 节点/关系详情的属性表依赖模板契约渲染中文名，直接进入审核台时也需加载模板数据。
    await loadExtractionSettings();
    const tab = state.reviewTab || "graph";
    if (tab === "bydisease") { await renderByDisease(); return; }
    const reviewer = localStorage.getItem("cdssReviewer") || "";
    const query = (state.reviewQuery || "").trim();
    const filters = state.reviewFilters || { graph: "", sourceDoc: "", nodeType: "", relationCode: "" };
    const queueItemType = tab === "rules" || tab === "blocked"
      ? "rule"
      : tab === "relations"
        ? "graph_relation"
        : tab === "graph"
          ? "graph"
          : (["stale", "orphan"].includes(tab) ? "graph" : "");
    const filterParams = [
      filters.graph ? `&graph_filename=${encodeURIComponent(filters.graph)}` : "",
      filters.sourceDoc ? `&source_doc=${encodeURIComponent(filters.sourceDoc)}` : "",
      filters.nodeType ? `&node_type=${encodeURIComponent(filters.nodeType)}` : "",
      filters.relationCode ? `&relation_code=${encodeURIComponent(filters.relationCode)}` : "",
    ].join("");
    const queueUrl = `/api/review/queue?limit=300${queueItemType ? `&item_type=${queueItemType}` : ""}${filterParams}${query ? `&q=${encodeURIComponent(query)}` : ""}`;
    const [queue, audit, graphs, docs] = await Promise.all([
      api(queueUrl),
      tab === "audit" ? api("/api/review/audit?limit=100") : Promise.resolve({ items: [] }),
      state.reviewGraphOptions === null
        ? api("/api/kg/graphs").then((items) => { state.reviewGraphOptions = items; }).catch(() => { state.reviewGraphOptions = []; })
        : Promise.resolve(),
      state.reviewDocOptions === null
        ? api("/api/pdf/documents").then((items) => { state.reviewDocOptions = items; }).catch(() => { state.reviewDocOptions = []; })
        : Promise.resolve(),
    ]);
    const dupesData = tab === "dupes" && filters.graph
      ? await api(`/api/kg/duplicates?graph_filename=${encodeURIComponent(filters.graph)}`).catch(() => ({ groups: [] }))
      : { groups: [] };
    const allItems = queue.items || [];
    if (!filters.nodeType) {
      const types = new Set();
      allItems.forEach((item) => {
        if (item.item_type === "graph_relation") {
          if (item.source_node_type) types.add(item.source_node_type);
          if (item.target_node_type) types.add(item.target_node_type);
        } else if (item.item_type === "graph_node" && item.node_type) {
          types.add(item.node_type);
        }
      });
      state.reviewNodeTypeOptions = [...types].sort();
    }
    if (!filters.relationCode) {
      const counts = new Map();
      allItems.filter((item) => item.item_type === "graph_relation").forEach((item) => {
        const code = item.relation_code || "";
        if (!code) return;
        const entry = counts.get(code) || { value: code, label: RELATION_LABELS[code] || item.name || code, count: 0 };
        entry.count += 1;
        counts.set(code, entry);
      });
      state.reviewRelationCodeOptions = [...counts.values()].sort((a, b) => b.count - a.count);
    }
    const items = allItems.filter((item) => {
      if (tab === "graph") return item.item_type === "graph_node" && ["draft", "stale"].includes(item.status);
      if (tab === "relations") return item.item_type === "graph_relation" && ["draft", "stale"].includes(item.status);
      if (tab === "rules") return item.item_type === "rule" && ["draft", "testing"].includes(item.status);
      if (tab === "blocked") return item.item_type === "rule" && item.status === "blocked";
      if (tab === "stale") return item.status === "stale";
      if (tab === "active") return item.status === "active" && ["graph_node", "graph_relation", "rule"].includes(item.item_type);
      if (tab === "orphan") return Boolean(item.orphaned_from && item.orphaned_from.length);
      return false;
    }).filter((item) => {
      if (tab === "relations" && state.reviewSuspectOnly && !(item.flags || []).length) return false;
      if (!query) return true;
      const haystack = `${reviewItemTitle(item)} ${item.code || ""} ${item.rule_id || ""} ${item.graph_filename || ""}`.toLowerCase();
      return haystack.includes(query.toLowerCase());
    });
    state.reviewItems = items;
    if (state.reviewSelectedKey && !items.some((item) => reviewItemKey(item) === state.reviewSelectedKey)) {
      state.reviewSelectedKey = "";
    }
    const relationPending = Number(queue.summary?.graph_relations?.draft || 0)
      + Number(queue.summary?.graph_relations?.stale || 0);
    const dupesBadge = (tab === "dupes" && dupesData.groups?.length) ? ` ${dupesData.groups.length}` : "";
    const tabHtml = REVIEW_TABS.map(([key, label]) => `<button class="review-tab ${tab === key ? "active" : ""}" data-review-tab="${key}">${label}${key === "relations" && relationPending ? `（${relationPending}）` : ""}${key === "dupes" ? dupesBadge : ""}</button>`).join("");
    const trunc = queue.truncated || {};
    const truncParts = [];
    if (trunc.graph_nodes || trunc.graph_relations) truncParts.push("图谱条目");
    if (trunc.rules) truncParts.push("规则");
    if (trunc.packages) truncParts.push("知识包");
    const truncated = truncParts.length
      ? `<div class="muted trunc-note">${esc(truncParts.join("、"))}超出展示上限，请用搜索缩小范围。</div>`
      : "";

    if (tab === "audit") {
      const statusBadge = (value) => value ? reviewBadge(value) : '<span class="muted">-</span>';
      const auditRows = (audit.items || []).map((row) => `<tr>
        <td class="nowrap">${esc(formatTime(row.ts))}</td>
        <td>${esc(row.entity_type || "-")}</td>
        <td class="mono">${esc(row.entity_id || "-")}</td>
        <td>${esc(row.action || "-")}</td>
        <td>${statusBadge(row.before_status)} → ${statusBadge(row.after_status)}</td>
        <td>${esc(row.reviewer || "-")}</td>
        <td>${esc(row.note || "-")}</td>
      </tr>`);
      content.innerHTML = `<div class="review-tabs">${tabHtml}</div>
        ${panel("审核记录", table(["时间", "类型", "条目", "动作", "状态变化", "审核人", "意见"], auditRows), "", "全量审核动作的追加式日志；拒绝原因码会记录在意见前缀 [原因码] 中")}`;
      bindReviewTabs();
      return;
    }

    if (tab === "dupes") {
      const graphFilename = filters.graph;
      const groups = dupesData.groups || [];
      const dupeCards = !graphFilename
        ? '<div class="empty">请先在下方选择目的图谱</div>'
        : (groups.map((group, groupIndex) => `
          <div class="dupe-group">
            <div class="dupe-head">
              <span class="type-chip">${esc(typeName[group.node_type] || group.node_type)}</span>
              <strong>${esc(group.name)}</strong>
              <span class="muted">${group.nodes.length} 个同名节点</span>
            </div>
            <div class="dupe-nodes">${group.nodes.map((n) => `
              <label class="dupe-node">
                <input type="radio" name="dupe-group-${groupIndex}" value="${esc(n.node_id)}" ${n.node_id === group.recommended_survivor ? "checked" : ""}>
                <span class="mono">${esc(n.code)}</span>
                ${n.is_standard_code ? '<span class="badge active">标准码</span>' : '<span class="badge">候选码</span>'}
                ${reviewBadge(n.review_status)}
                <span class="muted">关系 ${n.relation_count} · 引用 ${n.source_ref_count}</span>
              </label>`).join("")}</div>
            <div class="actions"><button class="btn sm primary" data-dupe-merge>合并为一</button></div>
          </div>`).join("") || '<div class="empty">未检测到同类型同名重复</div>');
      content.innerHTML = `<div class="review-tabs">${tabHtml}</div>
        <div class="review-list" style="max-width:none">
          <div class="review-toolbar compact review-filterbar">
            <select id="reviewFilterGraph" title="按目的图谱筛选">
              <option value="">请选择图谱</option>
              ${(state.reviewGraphOptions || []).map((g) => `<option value="${esc(g.graph_filename)}" ${filters.graph === g.graph_filename ? "selected" : ""}>${esc(g.graph_filename)}</option>`).join("")}
            </select>
            <span class="muted">同类型同名节点分组；默认勾选推荐保留节点（标准码优先），「合并为一」吸收其余节点并自动改写规则引用（合并前自动备份）。</span>
          </div>
          ${dupeCards}
        </div>`;
      bindReviewTabs();
      $("#reviewFilterGraph")?.addEventListener("change", (event) => {
        state.reviewFilters.graph = event.target.value;
        renderReviewCenter();
      });
      bindDupeMerge(graphFilename);
      return;
    }

    const dependencyCount = (item) => item.dependent_rule_count ?? item.dependency_summary?.total ?? "-";
    const rows = items.map((item) => {
      const key = reviewItemKey(item);
      const subParts = item.item_type === "rule"
        ? [item.rule_id, item.graph_filename || ""]
        : item.item_type === "graph_relation"
          ? [item.relation_code, item.graph_filename || ""]
          : [item.code || item.node_id, typeName[item.node_type] || item.node_type || "", item.graph_filename || ""];
      const issues = item.item_type === "rule" ? (item.issues || []) : [];
      return `<tr class="review-row ${key === state.reviewSelectedKey ? "selected" : ""}" data-review-key="${esc(key)}">
        <td class="col-check"><input type="checkbox" data-review-select="${esc(key)}"></td>
        <td class="review-item-cell">
          <div class="review-item-title"><span class="type-chip">${esc(REVIEW_TYPE_LABELS[item.item_type] || item.item_type)}</span>
            ${item.item_type === "graph_relation" ? relationSemHtml(item) : `<strong>${esc(reviewItemTitle(item))}</strong>`}
          </div>
          <div class="review-item-sub mono">${esc(subParts.filter(Boolean).join(" · "))}</div>
          ${item.flags?.length ? `<div class="flag-line">${flagChipsHtml(item)}</div>` : ""}
          ${item.orphaned_from?.length ? `<div class="muted">孤儿来源：${esc(item.orphaned_from.join("、"))}</div>` : ""}
          ${issues.length ? `<div class="issue-inline" title="${esc(issues.join("\n"))}">${esc(issues[0])}</div>` : ""}
        </td>
        <td>${reviewBadge(item.status)}</td>
        <td class="nowrap">${esc(String(dependencyCount(item)))}</td>
        <td class="nowrap muted">${esc(shortTime(item.updated_at))}</td>
      </tr>`;
    });
    content.innerHTML = `
      <div class="review-tabs">${tabHtml}</div>
      ${reviewProgressHtml(queue.summary)}
      ${truncated}
      ${tab === "relations" ? relationMatrixHtml(items) : ""}
      <div class="review-layout">
        <div class="review-list">
          <div class="review-toolbar compact">
            <input id="reviewCenterReviewer" value="${esc(reviewer)}" placeholder="审核人（可署名）" title="审核人署名，写入审计记录">
            <input id="reviewSearch" type="search" value="${esc(state.reviewQuery || "")}" placeholder="搜索名称 / 编码 / 规则 ID…">
          </div>
          <div class="review-toolbar compact review-filterbar">
            <select id="reviewFilterGraph" title="按目的图谱筛选当前页签的条目">
              <option value="">全部图谱</option>
              ${(state.reviewGraphOptions || []).map((g) => `<option value="${esc(g.graph_filename)}" ${filters.graph === g.graph_filename ? "selected" : ""}>${esc(g.graph_filename)}</option>`).join("")}
            </select>
            <select id="reviewFilterSourceDoc" title="按来源指南筛选：图谱条目按溯源、规则按 guideline_id">
              <option value="">全部指南</option>
              ${(state.reviewDocOptions || []).map((d) => `<option value="${esc(d.doc_id)}" ${filters.sourceDoc === d.doc_id ? "selected" : ""}>${esc(d.doc_title || d.doc_id)}</option>`).join("")}
            </select>
            <select id="reviewFilterNodeType" title="按实体类型筛选图谱条目（配合全选可批量审核同一类型）">
              <option value="">全部类型</option>
              ${state.reviewNodeTypeOptions.map((t) => `<option value="${esc(t)}" ${filters.nodeType === t ? "selected" : ""}>${esc(typeName[t] || t)}</option>`).join("")}
            </select>
            ${tab === "relations" ? `<select id="reviewFilterRelationCode" title="按关系语义类型筛选（配合全选可批量审核同一语义的关系）">
              <option value="">全部关系类型</option>
              ${(state.reviewRelationCodeOptions || []).map((option) => `<option value="${esc(option.value)}" ${filters.relationCode === option.value ? "selected" : ""}>${esc(option.label)}（${option.count}）</option>`).join("")}
            </select>` : ""}
            ${tab === "relations" ? `<label class="suspect-toggle" title="只显示系统预检标记了可疑项的关系">
              <input type="checkbox" id="reviewSuspectOnly" ${state.reviewSuspectOnly ? "checked" : ""}> 只看可疑（${allItems.filter((item) => item.item_type === "graph_relation" && ["draft", "stale"].includes(item.status) && (item.flags || []).length).length}）
            </label>` : ""}
            ${filterParams ? '<button class="btn sm" id="reviewFilterClear">清除筛选</button>' : ""}
          </div>
          <div class="review-bulkbar">
            <label class="nowrap"><input type="checkbox" id="reviewSelectAll"> 全选</label>
            <span class="muted" id="reviewSelectionCount">已选 0 条</span>
            <div class="actions">
              <button class="btn sm primary" data-review-bulk="approve">通过</button>
              <button class="btn sm danger" data-review-bulk="reject">拒绝</button>
              <button class="btn sm" data-review-bulk="retire">失效</button>
              <button class="btn sm" data-review-bulk="restore">恢复</button>
            </div>
            <span class="kbd-hint">J/K 切换 · X 勾选 · A 通过 · R 拒绝</span>
          </div>
          <div class="review-table-wrap"><table>
            <thead><tr><th class="col-check"></th><th>条目</th><th>状态</th><th>依赖</th><th>更新</th></tr></thead>
            <tbody>${rows.join("") || '<tr><td colspan="5"><div class="empty">当前页签没有待办条目</div></td></tr>'}</tbody>
          </table></div>
        </div>
        <div class="review-detail" id="reviewDetail">
          <div class="empty">在左侧选择条目查看详情<br><span class="muted">类型、属性、依赖规则与审核记录会在这里展示</span></div>
        </div>
      </div>`;

    const reviewerInput = $("#reviewCenterReviewer");
    if (reviewerInput) {
      reviewerInput.onchange = (event) => localStorage.setItem("cdssReviewer", event.target.value.trim());
    }
    let reviewSearchTimer = null;
    const searchInput = $("#reviewSearch");
    if (searchInput) {
      searchInput.oninput = (event) => {
        clearTimeout(reviewSearchTimer);
        reviewSearchTimer = setTimeout(() => {
          state.reviewQuery = event.target.value;
          renderReviewCenter();
        }, 250);
      };
    }
    const bindReviewFilter = (id, key) => {
      $(id)?.addEventListener("change", (event) => {
        state.reviewFilters[key] = event.target.value;
        renderReviewCenter();
      });
    };
    bindReviewFilter("#reviewFilterGraph", "graph");
    bindReviewFilter("#reviewFilterSourceDoc", "sourceDoc");
    bindReviewFilter("#reviewFilterNodeType", "nodeType");
    bindReviewFilter("#reviewFilterRelationCode", "relationCode");
    $("#reviewFilterClear")?.addEventListener("click", () => {
      state.reviewFilters = { graph: "", sourceDoc: "", nodeType: "", relationCode: "" };
      renderReviewCenter();
    });
    $("#reviewSuspectOnly")?.addEventListener("change", (event) => {
      state.reviewSuspectOnly = event.target.checked;
      renderReviewCenter();
    });
    const selectionCount = () => {
      const count = content.querySelectorAll("[data-review-select]:checked").length;
      const el = $("#reviewSelectionCount");
      if (el) el.textContent = `已选 ${count} 条`;
    };
    $("#reviewSelectAll")?.addEventListener("change", (event) => {
      content.querySelectorAll("[data-review-select]").forEach((input) => { input.checked = event.target.checked; });
      selectionCount();
    });
    content.querySelectorAll("[data-review-select]").forEach((input) => input.addEventListener("change", selectionCount));
    content.onclick = (event) => {
      const actionButton = event.target.closest("[data-detail-action]");
      if (actionButton) {
        const item = state.reviewItems.find((entry) => reviewItemKey(entry) === state.reviewSelectedKey);
        if (!item) return;
        const action = actionButton.dataset.detailAction;
        if (action === "reject" || action === "retire") openReasonDialog(item, action);
        else quickReview(item, action);
        return;
      }
      if (event.target.closest("input[type=checkbox]")) return;
      const row = event.target.closest(".review-row");
      if (row) {
        const item = state.reviewItems.find((entry) => reviewItemKey(entry) === row.dataset.reviewKey);
        if (item) loadReviewDetail(item);
      }
    };
    content.querySelectorAll("[data-review-bulk]").forEach((button) => button.onclick = () => bulkReview(button.dataset.reviewBulk));
    bindReviewTabs();
    const selected = items.find((item) => reviewItemKey(item) === state.reviewSelectedKey);
    if (selected) loadReviewDetail(selected);
  }

  // ==== 按疾病分组：知识卡片视图（单图谱）====
  async function renderByDisease() {
    const tab = state.reviewTab || "graph";
    const filters = state.reviewFilters || {};
    const graphName = filters.graph || state.graphFilename || "";
    state.byDisease = state.byDisease || { subject: "", search: "", composing: false };
    // 重渲染会重建搜索框，先记录焦点与光标，渲染后恢复（防抖过滤不打断输入）
    state.byDisease.composing = false;
    const prevSearch = content.querySelector("#byDiseaseSearch");
    const searchHadFocus = Boolean(prevSearch && document.activeElement === prevSearch);
    const searchSelection = searchHadFocus
      ? { start: prevSearch.selectionStart, end: prevSearch.selectionEnd }
      : null;
    const tabHtml = REVIEW_TABS.map(([key, label]) => `<button class="review-tab ${tab === key ? "active" : ""}" data-review-tab="${key}">${label}</button>`).join("");
    if (!graphName) {
      content.innerHTML = `<div class="review-tabs">${tabHtml}</div><div class="empty">按疾病分组视图按单图谱展示，请先在筛选中选择目的图谱。</div>`;
      bindReviewTabs();
      return;
    }
    let groups;
    try {
      groups = await api(`/api/kg/relations/groups?graph_filename=${encodeURIComponent(graphName)}&subject_type=disease`);
    } catch (error) {
      content.innerHTML = `<div class="review-tabs">${tabHtml}</div><div class="empty">加载分组失败：${esc(error.message)}</div>`;
      bindReviewTabs();
      return;
    }
    const search = (state.byDisease.search || "").trim().toLowerCase();
    const subjects = (groups.subjects || []).filter((s) => !search || `${s.name} ${s.code}`.toLowerCase().includes(search));
    const hint = filters.graph ? "" : `<div class="muted trunc-note">按疾病分组视图按单图谱展示，当前：${esc(graphName)}</div>`;
    content.innerHTML = `<div class="review-tabs">${tabHtml}</div>${hint}
      <div class="by-disease-layout">
        <div class="subject-list">
          <input id="byDiseaseSearch" type="search" value="${esc(state.byDisease.search || "")}" placeholder="搜索疾病 / 证候 / 编码…">
          <div class="subject-list-scroll">
            ${subjects.map(subjectCardHtml).join("") || '<div class="empty">没有匹配的主体节点</div>'}
          </div>
        </div>
        <div class="review-detail" id="byDiseaseCard">
          <div class="empty">在左侧选择疾病 / 证候，核对它的知识网<br><span class="muted">症见、检验、检查、诊断依据、用药会按关系类型分组展示</span></div>
        </div>
      </div>`;
    bindReviewTabs();
    const searchInput = $("#byDiseaseSearch");
    if (searchInput) {
      let searchTimer = null;
      const scheduleSearch = () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
          state.byDisease.search = searchInput.value;
          renderByDisease();
        }, 250);
      };
      // 输入法合成期间跳过防抖重渲染，避免打断 IME；合成结束后再过滤一次。
      // composition 事件用 addEventListener 绑定：Chrome 对（未受信任派发的）
      // 合成事件不触发 on* 属性处理器，addEventListener 两种路径都覆盖。
      searchInput.oninput = () => {
        if (state.byDisease.composing) return;
        scheduleSearch();
      };
      searchInput.addEventListener("compositionstart", () => { state.byDisease.composing = true; });
      searchInput.addEventListener("compositionend", () => {
        state.byDisease.composing = false;
        scheduleSearch();
      });
      if (searchHadFocus) {
        searchInput.focus();
        if (searchSelection) {
          const max = searchInput.value.length;
          searchInput.setSelectionRange(
            Math.min(searchSelection.start, max),
            Math.min(searchSelection.end, max),
          );
        }
      }
    }
    content.querySelectorAll(".subject-card").forEach((card) => card.onclick = () => loadByDiseaseCard(card.dataset.subject));
    if (state.byDisease.subject && subjects.some((s) => s.node_id === state.byDisease.subject)) {
      await loadByDiseaseCard(state.byDisease.subject);
    } else {
      state.byDisease.subject = "";
    }
  }

  function subjectCardHtml(s) {
    return `<div class="subject-card ${state.byDisease?.subject === s.node_id ? "selected" : ""}" data-subject="${esc(s.node_id)}">
      <strong>${esc(s.display_name || s.name)}</strong>
      <div class="sub-meta">
        <span>${esc(typeName[s.node_type] || s.node_type || "-")}</span>
        ${miniStatus(s.review_status)}
        <span>出边 ${s.out_relation_count}</span>
        ${s.pending_count ? `<span class="pending-chip">待审 ${s.pending_count}</span>` : '<span class="muted">已清</span>'}
        ${s.suspect_count ? `<span class="pending-chip warn">⚠ ${s.suspect_count}</span>` : ""}
      </div>
    </div>`;
  }

  async function loadByDiseaseCard(nodeId) {
    const filters = state.reviewFilters || {};
    const graphName = filters.graph || state.graphFilename || "";
    state.byDisease.subject = nodeId;
    content.querySelectorAll(".subject-card").forEach((card) => card.classList.toggle("selected", card.dataset.subject === nodeId));
    const mount = $("#byDiseaseCard");
    if (!mount) return;
    mount.innerHTML = '<div class="empty">加载知识卡片…</div>';
    let detail;
    try {
      detail = await api(`/api/kg/relations/groups/${encodeURIComponent(nodeId)}?graph_filename=${encodeURIComponent(graphName)}`);
    } catch (error) {
      mount.innerHTML = `<div class="empty">加载失败：${esc(error.message)}</div>`;
      return;
    }
    const sections = detail.sections || [];
    state.byDiseaseSections = sections;
    state.byDiseaseItems = new Map();
    sections.forEach((section) => {
      (section.items || []).forEach((item) => state.byDiseaseItems.set(item.item_id, item));
      (section.containers || []).forEach((container) => (container.sections || []).forEach((nested) => (nested.items || []).forEach((item) => state.byDiseaseItems.set(item.item_id, item))));
    });
    const subject = detail.subject || {};
    mount.innerHTML = `
      ${detailHeadHtml({ item_type: "graph_node", node_id: nodeId, name: subject.display_name || subject.name || nodeId, code: subject.code || "", status: subject.review_status || "draft", graph_filename: detail.graph_filename || graphName, node_type: subject.node_type || "" })}
      <div class="detail-sub mono muted">出边 ${detail.totals?.total ?? 0} 条 · 待审 ${detail.totals?.pending ?? 0} · 可疑 ${detail.totals?.suspect ?? 0}</div>
      ${sections.map((section) => diseaseSectionHtml(section)).join("") || '<div class="empty">该节点没有出边关系</div>'}`;
    bindByDiseaseCardActions(mount);
  }

  function diseaseSectionHtml(section) {
    const relItems = section.items || [];
    const pending = relItems.filter((item) => ["draft", "stale"].includes(item.status));
    const containers = (section.containers || []).map((container) => `
      <details class="container-block">
        <summary>${esc(container.display_name || container.container_name)} · ${container.sections.length} 组</summary>
        ${(container.sections || []).map((nested) => nestedSectionHtml(container, nested)).join("")}
      </details>`).join("");
    return `<div class="kg-section">
      <div class="kg-section-head">
        <h4>${esc(relationLabel(section))}</h4>
        <span class="mono muted">${esc(section.relation_code)}</span>
        <span class="muted">${section.total} 条 · 待审 ${pending.length}</span>
        <div class="actions">
          ${pending.length ? `<button class="btn sm primary" data-section-action="approve" data-section-key="${esc(section.section_key)}">整组通过</button>
          <button class="btn sm danger" data-section-action="reject" data-section-key="${esc(section.section_key)}">整组拒绝</button>` : '<span class="muted">无待审</span>'}
        </div>
      </div>
      <table><tbody>${relItems.map((item) => diseaseRelationRowHtml(item)).join("")}</tbody></table>
      ${containers}
    </div>`;
  }

  function nestedSectionHtml(container, nested) {
    const pending = (nested.items || []).filter((item) => ["draft", "stale"].includes(item.status));
    if (!pending.length && !(nested.items || []).length) return "";
    return `<div class="kg-section nested">
      <div class="kg-section-head">
        <h4>${esc(container.display_name || container.container_name)} · ${esc(relationLabel(nested))}</h4>
        <span class="mono muted">${esc(nested.relation_code)}</span>
        <span class="muted">${nested.total} 条 · 待审 ${pending.length}</span>
        <div class="actions">
          ${pending.length ? `<button class="btn sm primary" data-section-action="approve" data-section-key="${esc(nested.section_key)}">整组通过</button>
          <button class="btn sm danger" data-section-action="reject" data-section-key="${esc(nested.section_key)}">整组拒绝</button>` : ""}
        </div>
      </div>
      <table><tbody>${(nested.items || []).map((item) => diseaseRelationRowHtml(item)).join("")}</tbody></table>
    </div>`;
  }

  function diseaseRelationRowHtml(item) {
    const actions = [];
    if (["draft", "stale"].includes(item.status)) {
      actions.push(`<button class="btn sm primary" data-rel-action="approve" data-rel-id="${esc(item.item_id)}">通过</button>`);
      actions.push(`<button class="btn sm danger" data-rel-action="reject" data-rel-id="${esc(item.item_id)}">拒绝</button>`);
    } else if (item.status === "active") {
      actions.push(`<button class="btn sm" data-rel-action="retire" data-rel-id="${esc(item.item_id)}">失效</button>`);
    } else {
      actions.push(`<button class="btn sm" data-rel-action="restore" data-rel-id="${esc(item.item_id)}">恢复</button>`);
    }
    return `<tr>
      <td>${relationSemHtml(item)}</td>
      <td>${reviewBadge(item.status)}</td>
      <td>${flagChipsHtml(item)}</td>
      <td class="nowrap"><div class="actions">${actions.join(" ")}</div></td>
    </tr>`;
  }

  function collectSectionItems(sectionKey) {
    for (const section of state.byDiseaseSections || []) {
      if (section.section_key === sectionKey) {
        const items = [...(section.items || [])];
        (section.containers || []).forEach((container) => (container.sections || []).forEach((nested) => items.push(...(nested.items || []))));
        return items;
      }
      for (const container of section.containers || []) {
        for (const nested of container.sections || []) {
          if (nested.section_key === sectionKey) return [...(nested.items || [])];
        }
      }
    }
    return [];
  }

  function bindByDiseaseCardActions(mount) {
    const refresh = async () => {
      await renderByDisease();
    };
    mount.onclick = async (event) => {
      const single = event.target.closest("[data-rel-action]");
      if (single) {
        const item = state.byDiseaseItems?.get(single.dataset.relId);
        if (!item) return;
        const action = single.dataset.relAction;
        if (action === "reject" || action === "retire") { openReasonDialog(item, action); return; }
        try {
          if (action === "approve" && relationEndpointBlocked(item)) {
            toast("该关系的端点节点尚未全部「已通过」，请先审核两端节点", true);
            return;
          }
          await submitReviewAction(item, action, "");
          toast(`${ACTION_DONE_LABELS[action] || "已处理"}：${reviewItemTitle(item)}`);
          await refresh();
        } catch (error) { toast(error.message, true); }
        return;
      }
      const sectionBtn = event.target.closest("[data-section-action]");
      if (sectionBtn) {
        const action = sectionBtn.dataset.sectionAction;
        let pending = collectSectionItems(sectionBtn.dataset.sectionKey)
          .filter((item) => ["draft", "stale"].includes(item.status));
        if (!pending.length) return;
        if (action === "reject") { openReasonDialog({ multi: pending }, "reject"); return; }
        const blocked = pending.filter(relationEndpointBlocked);
        if (blocked.length) {
          const rest = pending.length - blocked.length;
          const proceed = confirm(
            `${blocked.length} 条关系的端点节点还不是「已通过」，直接提交会被拒绝。` +
            (rest ? `\n\n确定后仅提交其余 ${rest} 条；` : "\n\n当前没有可提交条目；") + "取消则不做操作。"
          );
          if (!proceed) return;
          pending = pending.filter((item) => !relationEndpointBlocked(item));
          if (!pending.length) { toast("没有可提交的条目，请先审核相关端点节点", true); return; }
        }
        try {
          const result = await api("/api/review/bulk", { json: {
            items: pending.map(itemPayload),
            action,
            reviewer: reviewReviewer(),
            note: "",
          }});
          toast(`整组完成：成功 ${result.succeeded}，失败 ${result.failed}`, result.failed > 0);
          await refresh();
        } catch (error) { toast(error.message, true); }
      }
    };
  }

  async function renderRules() {
    await loadBase();
    const [graph, sync] = await Promise.all([api("/api/kg/graph"), api("/api/rules/sync-check")]);
    state.graph = graph; state.ruleSync = sync;
    const domainRules = state.ruleDomain
      ? state.rules.filter((rule) => normalizeDomainClient(rule.domain) === state.ruleDomain)
      : state.rules;
    const diseaseMap = new Map();
    domainRules.forEach((rule) => {
      if (rule.disease_code) diseaseMap.set(rule.disease_code, rule.disease_name || rule.disease_code);
    });
    if (state.ruleDisease && !diseaseMap.has(state.ruleDisease)) state.ruleDisease = "";
    const visibleRules = state.ruleDisease
      ? domainRules.filter((rule) => rule.disease_code === state.ruleDisease)
      : domainRules;
    const syncStatus = `<span class="badge ${sync.inconsistent_count ? "danger" : "active"}" title="${esc(`一致 ${sync.consistent_count} 条；不一致 ${sync.inconsistent_count} 条`)}">图谱语义同步 ${sync.consistent_count}/${sync.total}</span>`;
    const domainFilter = `<select id="ruleDomainFilter">
      <option value="" ${state.ruleDomain === "" ? "selected" : ""}>全部领域</option>
      <option value="western" ${state.ruleDomain === "western" ? "selected" : ""}>西医</option>
      <option value="tcm" ${state.ruleDomain === "tcm" ? "selected" : ""}>中医</option>
    </select>`;
    const diseaseFilter = `<select id="ruleDiseaseFilter">
      <option value="">全部疾病 / 证候</option>
      ${[...diseaseMap.entries()].sort((a, b) => a[1].localeCompare(b[1], "zh-Hans-CN")).map(([code, name]) => `<option value="${esc(code)}" ${state.ruleDisease === code ? "selected" : ""}>${esc(name)}（${esc(code)}）</option>`).join("")}
    </select>`;
    const inconsistentIds = new Set(
      (state.ruleSync?.rules || []).filter((item) => item.semantic_status !== "consistent").map((item) => item.rule_id)
    );
    content.innerHTML = panel("规则列表", table(["规则 / 结论", "目标证候/诊断", "条件逻辑", "关联概念", "图谱语义", "图谱依赖", "所属图谱", "优先级", "状态", "操作"], visibleRules.map((r) => {
      const disease = graph.diseases.find((d) => d.code === r.disease_code);
      const dependency = r.dependency_summary || {};
      const dependencyTone = dependency.blocked ? "danger" : (dependency.total && dependency.active === dependency.total ? "active" : "draft");
      const dependencyLabel = dependency.blocked
        ? `${dependency.total - dependency.active} 项未通过`
        : `${dependency.active || 0}/${dependency.total || 0} 已审核`;
      const dependencyCell = dependency.total
        ? `<button class="btn sm ${dependencyTone}" data-rule-deps="${esc(r.rule_id)}">${dependencyLabel}</button>`
        : (dependency.blocked ? '<span class="badge danger">图谱无效</span>' : '<span class="muted">无依赖</span>');
      const ruleAction = r.status === "active"
        ? { action: "retire", label: "停用" }
        : (r.status === "rejected" || r.status === "retired"
          ? { action: "restore", label: "恢复" }
          : { action: "approve", label: "启用" });
      const completeButton = inconsistentIds.has(r.rule_id)
        ? `<button class="btn sm" data-rule-complete="${esc(r.rule_id)}">补全条件</button>`
        : "";
      const approveDepsButton = dependency.blocked
        ? `<button class="btn sm primary" data-rule-approve-deps="${esc(r.rule_id)}">补审依赖</button>`
        : "";
      return `<tr><td><strong>${esc(r.name)}</strong><div class="mono muted">${esc(r.rule_id)} v${r.version}</div><div>${badge(r.rule_type || "diagnosis")} ${badge(normalizeDomainClient(r.domain))}</div></td><td>${esc(disease ? disease.name_cn : r.disease_name || r.disease_code)}</td><td><div class="quote">${esc(conditionText(r.conditions))}</div></td><td>${ruleConceptChips(r)}</td><td>${ruleSyncBadge(r.rule_id)}</td><td>${dependencyCell}</td><td class="mono muted">${esc(r.graph_filename || "stemi_graph.json")}</td><td>${r.priority}</td><td>${reviewBadge(r.status)}</td><td><div class="actions">${completeButton}${approveDepsButton}<button class="btn sm" data-rule-edit="${esc(r.rule_id)}">编辑</button><button class="btn sm" data-rule-history="${esc(r.rule_id)}">历史</button><button class="btn sm" data-rule-status="${esc(r.rule_id)}" data-rule-action="${ruleAction.action}">${ruleAction.label}</button><button class="btn sm danger" data-rule-delete="${esc(r.rule_id)}">删除</button></div></td></tr>`;
    })), `<span class="nowrap">${domainFilter}${diseaseFilter}${syncStatus}<span id="engineChip" class="nowrap"></span><button id="ruleWizard" class="btn primary">规则向导</button><button id="completeConditions" class="btn">批量补全条件</button><button id="activateConsistent" class="btn">批量启用一致规则</button><button id="newRule" class="btn primary">新建规则</button></span>`, "条件引用概念词汇，执行时按患者所属医院解析标准；启用前检查疾病、实体、概念链路和图谱审核状态");
    $("#newRule").onclick = () => openRuleEditor(null);
    $("#ruleWizard").onclick = () => openRuleWizard();
    renderEngineChip(content.querySelector("#engineChip"));
    $("#activateConsistent").onclick = async () => {
      if (!confirm("将启用当前筛选范围内图谱语义一致且通过质量检查的草稿规则，确认继续？")) return;
      try {
        const result = await api("/api/rules/activate-consistent", { json: {
          domain: state.ruleDomain || undefined,
          rule_ids: state.ruleDisease ? visibleRules.map((rule) => rule.rule_id) : undefined,
          quality_only: true,
        } });
        toast(`已启用 ${result.activated} 条，跳过 ${result.skipped} 条`);
        renderRules();
      } catch (error) { toast(error.message, true); }
    };
    $("#completeConditions").onclick = async () => {
      const ids = visibleRules.filter((rule) => inconsistentIds.has(rule.rule_id)).map((rule) => rule.rule_id);
      if (!ids.length) { toast("当前筛选范围内没有图谱不一致的规则", true); return; }
      if (!confirm(`将按图谱证据为 ${ids.length} 条不一致规则自动补全概念条件（写入草稿条件，可再编辑）。确认继续？`)) return;
      try {
        const result = await api("/api/rules/complete-conditions", { json: {
          rule_ids: ids,
          reviewer: localStorage.getItem("cdssReviewer") || "未署名",
        }});
        toast(`补全 ${result.succeeded} 条，跳过 ${result.failed} 条`, result.failed > 0);
        renderRules();
      } catch (error) { toast(error.message, true); }
    };
    content.querySelectorAll("[data-rule-complete]").forEach((button) => button.onclick = async () => {
      try {
        const result = await api(`/api/rules/${encodeURIComponent(button.dataset.ruleComplete)}/complete-conditions`, {
          json: { reviewer: localStorage.getItem("cdssReviewer") || "未署名" },
        });
        toast(`条件已按图谱补全（语义${result.semantic_status === "consistent" ? "已一致" : "仍不一致"}，依赖${result.dependency_status === "passed" ? "已通过" : "待审核"}）`, result.semantic_status !== "consistent");
        renderRules();
      } catch (error) { toast(error.message, true); }
    });
    $("#ruleDiseaseFilter").onchange = (event) => {
      state.ruleDisease = event.target.value;
      renderRules();
    };
    $("#ruleDomainFilter").onchange = (event) => {
      state.ruleDomain = event.target.value;
      state.ruleDisease = "";
      renderRules();
    };
    content.querySelectorAll("[data-rule-edit]").forEach((b) => b.onclick = () => openRuleEditor(state.rules.find((r) => r.rule_id === b.dataset.ruleEdit)));
    content.querySelectorAll("[data-rule-status]").forEach((b) => b.onclick = async () => {
      try {
        await api(`/api/rules/${encodeURIComponent(b.dataset.ruleStatus)}/review`, {
          json: {
            action: b.dataset.ruleAction,
            reviewer: localStorage.getItem("cdssReviewer") || "未署名",
          },
        });
        toast("状态已更新");
        render();
      } catch (error) { toast(error.message, true); }
    });
    const approveRuleDependencies = async (ruleId) => {
      try {
        const result = await api(`/api/rules/${encodeURIComponent(ruleId)}/approve-dependencies`, {
          json: { reviewer: localStorage.getItem("cdssReviewer") || "未署名" },
        });
        const parts = [`节点 ${result.approved_nodes} 个`, `关系 ${result.approved_relations} 条`];
        if (result.skipped?.length) parts.push(`跳过 ${result.skipped.length}（曾拒绝/失效需人工）`);
        if (result.missing?.length) parts.push(`缺失 ${result.missing.length}（需先补概念）`);
        const blocked = result.rule_status === "blocked" || result.gate_error;
        toast(
          `依赖补审完成：${parts.join("，")}；规则状态：${REVIEW_LABELS[result.rule_status] || result.rule_status}`
          + (result.gate_error ? `；${String(result.gate_error).slice(0, 120)}` : ""),
          blocked || Boolean(result.missing?.length),
        );
        renderRules();
      } catch (error) { toast(error.message, true); }
    };
    content.querySelectorAll("[data-rule-approve-deps]").forEach((button) => button.onclick = () => {
      if (!confirm("将自动通过该规则的全部待审依赖（先节点后关系；被拒绝/失效/缺失项会跳过并提示），并恢复被阻断的规则。确认继续？")) return;
      approveRuleDependencies(button.dataset.ruleApproveDeps);
    });
    content.querySelectorAll("[data-rule-deps]").forEach((button) => {
      button.onclick = async () => {
        try {
          const result = await api(`/api/rules/${encodeURIComponent(button.dataset.ruleDeps)}/graph-dependencies`);
          const refs = result.references || [];
          const missingCount = refs.filter((ref) => ref.status === "missing").length;
          const manualCount = refs.filter((ref) => ["rejected", "retired"].includes(ref.status)).length;
          $("#editorBody").innerHTML = `
            <h3>图谱依赖 <span class="mono muted">${esc(result.rule_id)}</span></h3>
            <p>依赖状态：${result.dependency_status === "passed" ? reviewBadge("active") : reviewBadge("blocked")}</p>
            ${table(["类型", "条目", "状态"], refs.map((ref) => `<tr>
              <td>${ref.kind === "node" ? "节点" : "关系"}</td>
              <td class="mono">${esc(ref.node_id || `${ref.source_node_id} -${ref.relation_code}-> ${ref.target_node_id}`)}</td>
              <td>${reviewBadge(ref.status === "active" ? "active" : "draft")} <span class="muted">${esc(ref.status)}</span></td>
            </tr>`))}
            ${(result.issues || []).length ? `<div class="result-banner error"><strong>图谱语义问题</strong>${result.issues.map((issue) => `<div>${esc(issue)}</div>`).join("")}</div>` : ""}
            ${(result.review_issues || []).length ? `<div class="result-banner warning">${result.review_issues.map((issue) => `<div>${esc(issue)}</div>`).join("")}</div>` : ""}
            ${missingCount ? `<div class="result-banner error">有 ${missingCount} 个依赖在图谱中不存在，无法自动通过；请先补全概念或修正规则条件。</div>` : ""}
            ${manualCount ? `<div class="result-banner warning">有 ${manualCount} 个依赖曾被拒绝/失效，需要人工处理，一键补审会跳过它们。</div>` : ""}
            ${result.dependency_status !== "passed" && !missingCount ? `<div class="actions"><button class="btn sm primary" data-deps-approve="${esc(result.rule_id)}">一键通过待审依赖${result.status === "blocked" ? "并恢复启用" : ""}</button></div>` : ""}
          `;
          const dialog = $("#editorDialog");
          const save = $("#editorSave");
          save.hidden = true;
          dialog.onclose = () => { save.hidden = false; dialog.onclose = null; };
          $("#editorCancel").onclick = () => dialog.close();
          $("#editorBody").querySelector("[data-deps-approve]")?.addEventListener("click", async () => {
            dialog.close();
            await approveRuleDependencies(result.rule_id);
          });
          dialog.showModal();
        } catch (error) { toast(error.message, true); }
      };
    });
    content.querySelectorAll("[data-rule-delete]").forEach((b) => b.onclick = async () => {
      if (!confirm("确认删除该规则？")) return;
      await api(`/api/rules/${b.dataset.ruleDelete}`, { method: "DELETE" }); toast("规则已删除"); render();
    });
    content.querySelectorAll("[data-rule-history]").forEach((button) => {
      button.onclick = () => openRuleHistory(button.dataset.ruleHistory);
    });
  }

  // 条件树编辑器的字典（跨弹窗缓存一次）
  let editorDictPromise = null;
  function loadEditorDict() {
    if (!editorDictPromise) {
      editorDictPromise = Promise.all([
        api("/api/vocab/entities/disease").catch(() => []),
        api("/api/vocab/entities/syndrome").catch(() => []),
        api("/api/vocab/entities/lab").catch(() => []),
        api("/api/vocab/entities/symptom").catch(() => []),
        api("/api/vocab/entities/exam").catch(() => []),
        api("/api/vocab/entities/exam_finding").catch(() => []),
      ]).then(([diseases, syndromes, labs, symptoms, exams, findings]) => ({
        disease: diseases || [],
        syndrome: syndromes || [],
        lab: labs || [],
        symptom: symptoms || [],
        exam: exams || [],
        exam_finding: findings || [],
        lab_concept: (state.vocab || []).filter((item) => item.category === "lab_concept"),
      }));
    }
    return editorDictPromise;
  }

  const CT_LEAF_LABELS = {
    lab_concept: "检验概念", exam_finding: "检查发现", symptom: "症状", vital: "生命体征",
    demographic: "人口学", past_history: "既往史", medication: "用药", observation: "观察（舌/脉/征）",
    attribute: "属性",
  };

  function openRuleEditor(rule) {
    const base = rule || { rule_id: `CUSTOM-${Date.now()}`, version: 1, domain: "western", anchor_type: "disease", rule_type: "diagnosis", name: "", disease_code: "", disease_name: "", priority: 50, status: "draft", department: "心血管内科", conditions: { type: "group", operator: "AND", children: [] }, conclusion: { diagnosis: "", confidence: "medium", message: "", action: "" }, source: {}, exclusions: [], tags: [] };
    const ruleTypes = ["diagnosis", "treatment", "tcm_pattern", "tcm_treatment", "tcm_prescription", "tcm_contraindication"];
    $("#editorBody").innerHTML = `
      <h3>${rule ? "编辑规则" : "新建规则"}</h3>
      <div class="form-grid">
        <div class="field"><label>规则 ID</label><input name="rule_id" value="${esc(base.rule_id)}" ${rule ? "readonly" : ""}></div>
        <div class="field"><label>名称</label><input name="name" value="${esc(base.name)}"></div>
        <div class="field"><label>领域</label><select name="domain"><option value="western" ${normalizeDomainClient(base.domain) === "western" ? "selected" : ""}>西医</option><option value="tcm" ${normalizeDomainClient(base.domain) === "tcm" ? "selected" : ""}>中医</option></select></div>
        <div class="field"><label>规则类型</label><select name="rule_type">${ruleTypes.map((type) => `<option value="${type}" ${base.rule_type === type ? "selected" : ""}>${type}</option>`).join("")}</select></div>
        <div class="field"><label>锚点类型</label><select name="anchor_type"><option value="disease" ${base.anchor_type === "disease" ? "selected" : ""}>疾病</option><option value="syndrome" ${base.anchor_type === "syndrome" ? "selected" : ""}>证候</option></select></div>
        <div class="field"><label>目标编码（疾病/证候）</label><input name="disease_code" value="${esc(base.disease_code)}"></div>
        <div class="field"><label>目标名称</label><input name="disease_name" value="${esc(base.disease_name)}"></div>
        <div class="field"><label>状态</label><select name="status">${["draft", "testing", "active", "retired"].map((s) => `<option value="${s}" ${base.status === s ? "selected" : ""}>${s}</option>`).join("")}</select></div>
        <div class="field"><label>优先级</label><input name="priority" type="number" value="${base.priority}"></div>
        <div class="field"><label>科室</label><input name="department" value="${esc(base.department || "")}"></div>
        <div class="field"><label>置信度</label><select name="confidence">${["high", "medium", "low"].map((s) => `<option value="${s}" ${base.conclusion.confidence === s ? "selected" : ""}>${s}</option>`).join("")}</select></div>
        <div class="field half"><label>提示</label><input name="message" value="${esc(base.conclusion.message)}"></div>
        <div class="field half"><label>动作</label><input name="action" value="${esc(base.conclusion.action || "")}"></div>
        <div class="field wide"><label>条件树 <span id="drlBadge"></span><button type="button" id="treeModeToggle" class="btn sm ct-toggle">JSON 模式</button></label><div id="conditionTreeHost" class="condition-tree"></div><textarea name="conditions" class="code" hidden>${esc(JSON.stringify(base.conditions, null, 2))}</textarea><div id="drlPreview" class="drl-preview" hidden><pre></pre></div></div>
        <div class="field wide"><label>条件引用概念</label><div id="ruleConceptCheck" class="actions">${ruleConceptChips(base)}</div></div>
        <div class="field wide"><label>结论扩展 JSON（中医治法/方剂/中药）</label><textarea name="conclusion_extra" class="code">${esc(JSON.stringify({ ...base.conclusion, diagnosis: undefined, confidence: undefined, message: undefined, action: undefined }, null, 2))}</textarea></div>
        <div class="field wide"><label>溯源 JSON</label><textarea name="source" class="code">${esc(JSON.stringify(base.source || {}, null, 2))}</textarea></div>
      </div>`;
    const dialog = $("#editorDialog"); dialog.showModal();
    // ---- 可视化条件树（隐藏 textarea 始终与树同步，保存路径零改动）----
    const ctree = window.CdssConditionTree;
    const editorForm = $("#editorBody");
    const conditionsField = editorForm.querySelector("[name=conditions]");
    let editorTree = ctree.sanitizeTree(base.conditions);
    let treeMode = true;
    let editorDict = null;
    let drlTimer = null;
    let canvasSelectedPath = [];

    const fieldWidgetHtml = (node, field, path, readonly) => {
      const key = ctree.pathKey(path);
      const value = node[field.key];
      if (field.widget === "enum" && field.readonly) {
        return `<span class="ct-fixed">${esc(value || "")}</span>`;
      }
      const common = `data-ct-field="${field.key}" data-path="${key}" data-widget="${field.widget}"`;
      if (field.widget === "dict") {
        const items = (editorDict && editorDict[field.dict]) || [];
        const options = ['<option value="">请选择…</option>'].concat(items.map((item) => {
          const name = item.name || item.name_cn || item.code;
          return `<option value="${esc(item.code)}" ${value === item.code ? "selected" : ""}>${esc(name)}（${esc(item.code)}）</option>`;
        }));
        return `<select ${common}>${options.join("")}</select>`;
      }
      if (field.widget === "enum") {
        const options = ['<option value="">--</option>'].concat((field.options || []).map((opt) =>
          `<option value="${esc(opt)}" ${value === opt ? "selected" : ""}>${esc(opt)}</option>`));
        return `<select ${common}>${options.join("")}</select>`;
      }
      if (field.widget === "number") return `<input type="number" step="any" ${common} value="${value ?? ""}">`;
      if (field.widget === "bool") return `<input type="checkbox" ${common} ${value ? "checked" : ""}>`;
      return `<input type="text" ${common} value="${esc(value ?? "")}">`;
    };

    const leafToolsHtml = (node, path, readonly) => {
      if (readonly) return "";
      const key = ctree.pathKey(path);
      const moveBtns = path.length
        ? `<button type="button" class="btn sm" data-ct-act="move" data-delta="-1" data-path="${key}" title="上移">↑</button><button type="button" class="btn sm" data-ct-act="move" data-delta="1" data-path="${key}" title="下移">↓</button>`
        : "";
      return `<button type="button" class="btn sm" data-ct-act="add-sibling" data-path="${key}">+同级</button>${moveBtns}<button type="button" class="btn sm danger" data-ct-act="remove" data-path="${key}">删除</button>`;
    };

    const kindSelectHtml = (node, path, readonly) => {
      const key = ctree.pathKey(path);
      return `<select data-ct-kind data-path="${key}" ${readonly ? "disabled" : ""}>${ctree.SUPPORTED_LEAF_TYPES.map((kind) =>
        `<option value="${kind}" ${node.type === kind ? "selected" : ""}>${CT_LEAF_LABELS[kind] || kind}</option>`).join("")}</select>`;
    };

    const opSelectHtml = (node, path, readonly) => {
      const key = ctree.pathKey(path);
      const op = String(node.operator || "AND").toUpperCase();
      return `<select data-ct-op data-path="${key}" ${readonly ? "disabled" : ""}>${ctree.GROUP_OPERATORS.map((candidate) => {
        const label = candidate === "AND" ? "且（AND）" : candidate === "OR" ? "或（OR）" : "非（NOT）";
        return `<option value="${candidate}" ${op === candidate ? "selected" : ""}>${label}</option>`;
      }).join("")}</select>`;
    };

    const groupToolsHtml = (node, path, readonly) => {
      if (readonly) return "";
      const key = ctree.pathKey(path);
      return [
        `<button type="button" class="btn sm" data-ct-act="add-leaf" data-path="${key}">+条件</button>`,
        `<button type="button" class="btn sm" data-ct-act="add-group" data-path="${key}">+子组</button>`,
        ...(path.length ? [
          `<button type="button" class="btn sm" data-ct-act="move" data-delta="-1" data-path="${key}" title="上移">↑</button>`,
          `<button type="button" class="btn sm" data-ct-act="move" data-delta="1" data-path="${key}" title="下移">↓</button>`,
          `<button type="button" class="btn sm danger" data-ct-act="remove" data-path="${key}">删除</button>`,
        ] : []),
      ].join("");
    };

    // 详情面板：画布负责结构，这里负责选中节点的字段编辑与增删
    const detailHtml = (node, path) => {
      if (!node) {
        return '<div class="ct-detail-title">条件详情</div><div class="ct-detail-hint">点击画布卡片查看并编辑条件字段；直接拖拽卡片可排序或在分组间移动。</div>';
      }
      // 祖先链上出现只读节点则整条子树只读
      let readonly = false;
      let walker = editorTree;
      for (let i = 0; i <= path.length; i += 1) {
        if (i > 0) walker = (walker.children || [])[path[i - 1]];
        if (!walker) break;
        if (ctree.READONLY_TYPES.includes(walker.type)) { readonly = true; break; }
      }
      if (node.type === "group") {
        return `<div class="ct-detail-title">条件组${path.length ? ` #${path.join(".")}` : "（根）"}</div>
          <div class="ct-detail-row"><em>组合方式</em>${opSelectHtml(node, path, readonly)}</div>
          <div class="ct-detail-tools">${groupToolsHtml(node, path, readonly)}</div>
          <div class="ct-detail-hint">把画布上的卡片拖进该组即可加入子条件。</div>`;
      }
      if (readonly) {
        const badge = '<span class="badge noncompilable">仅原生引擎</span>';
        if (node.type === "weighted_group") {
          const children = (node.children || []).map((child, i) =>
            `<div class="ct-ro-row"><b>${i + 1}.</b> ${esc(ctree.describeNode(child) || child.type)}</div>`).join("");
          return `<div class="ct-detail-title">加权评分组 ${badge}</div><div class="ct-detail-sub">scoring=${esc(node.scoring || "weighted_sum")} · threshold=${esc(node.threshold ?? "-")} · 必要子项 ${(node.required_children || []).length} 个</div>${children ? `<div class="ct-ro-list">${children}</div>` : ""}`;
        }
        return `<div class="ct-detail-title">${esc(CT_LEAF_LABELS[node.type] || node.type)} ${badge}</div><div class="ct-detail-sub">${esc(node.label || ctree.treeToText(node))}</div>`;
      }
      const fields = (ctree.LEAF_FIELDS[node.type] || []).map((field) =>
        `<div class="ct-detail-row"><em>${esc(field.label)}</em>${fieldWidgetHtml(node, field, path, false)}</div>`).join("");
      return `<div class="ct-detail-title">条件详情</div>
        <div class="ct-detail-row"><em>条件类型</em>${kindSelectHtml(node, path, false)}</div>
        ${fields}
        <div class="ct-detail-tools">${leafToolsHtml(node, path, false)}</div>`;
    };

    const renderDetail = () => {
      const panel = $("#ctDetail");
      if (!panel) return;
      panel.innerHTML = detailHtml(ctree.getNode(editorTree, canvasSelectedPath), canvasSelectedPath);
    };

    const renderBadge = (reason, result) => {
      const badge = $("#drlBadge");
      const preview = $("#drlPreview");
      if (!badge || !preview) return;
      if (!treeMode) { badge.innerHTML = ""; preview.hidden = true; return; }
      if (reason) {
        badge.innerHTML = `<span class="badge noncompilable" title="${esc(reason)}">不可编译</span>`;
        preview.hidden = false;
        preview.querySelector("pre").textContent = `// ${reason}`;
        return;
      }
      badge.innerHTML = '<span class="badge compilable">Drools 可编译</span>';
      preview.hidden = false;
      preview.querySelector("pre").textContent = result
        ? `// ${result.leaf_count} 个原子条件\n${result.drl || ""}`
        : "// 编译中…";
    };

    const scheduleDrlPreview = () => {
      const activeDialog = $("#editorDialog");
      if (!activeDialog || !activeDialog.open) return;
      if (drlTimer) clearTimeout(drlTimer);
      const reason = ctree.compilabilityReason(editorTree);
      renderBadge(reason, null);
      if (reason) return; // 本地已判定不可编译，无需请求
      drlTimer = setTimeout(async () => {
        drlTimer = null;
        const live = $("#editorDialog");
        if (!live || !live.open || !treeMode) return;
        const pendingReason = ctree.compilabilityReason(editorTree);
        if (pendingReason) { renderBadge(pendingReason, null); return; }
        try {
          const result = await api("/api/engine/drl-preview", { json: { conditions: editorTree } });
          if (!treeMode || !live.open) return;
          renderBadge(ctree.compilabilityReason(editorTree), result);
        } catch (error) {
          const badge = $("#drlBadge");
          if (badge && treeMode) badge.innerHTML = `<span class="badge noncompilable" title="${esc(error.message)}">预览失败</span>`;
        }
      }, 400);
    };

    const renderTree = () => {
      const host = $("#conditionTreeHost");
      if (!host) return;
      if (!treeMode) {
        host.hidden = true;
        conditionsField.hidden = false;
        $("#drlPreview").hidden = true;
        return;
      }
      conditionsField.hidden = true;
      host.hidden = false;
      const canvasApi = window.CdssTreeCanvas;
      if (!canvasApi || !editorDict) {
        host.innerHTML = '<div class="ct-empty">字典加载中…</div>';
        return;
      }
      host.innerHTML = '<div class="ctc-layout"><div class="ctc-mount"></div><div class="ct-detail" id="ctDetail"></div></div>';
      canvasApi.render(host.querySelector(".ctc-mount"), editorTree, {
        selectedPath: canvasSelectedPath,
        labels: CT_LEAF_LABELS,
        esc,
        onSelect: (pathKey) => {
          canvasSelectedPath = ctree.parsePathKey(pathKey);
          renderDetail();
        },
        onMove: (srcKey, targetKey, index) => {
          const next = ctree.moveSubtree(
            editorTree, ctree.parsePathKey(srcKey), ctree.parsePathKey(targetKey), index,
          );
          if (!next) { toast("无法移动到该位置", true); return; }
          canvasSelectedPath = [];
          mutate(() => next);
        },
      });
      renderDetail();
    };

    const mutate = (fn) => {
      try {
        editorTree = fn();
      } catch (error) { toast(error.message, true); return; }
      conditionsField.value = ctree.treeToJson(editorTree);
      renderTree();
      scheduleDrlPreview();
    };

    const host = $("#conditionTreeHost");
    host.onchange = (event) => {
      const target = event.target;
      const path = ctree.parsePathKey(target.dataset.path);
      if (target.matches("[data-ct-op]")) {
        mutate(() => ctree.updateNode(editorTree, path, { operator: target.value, label: null }));
      } else if (target.matches("[data-ct-kind]")) {
        mutate(() => ctree.replaceNode(editorTree, path, ctree.newLeaf(target.value)));
      } else if (target.matches("[data-ct-field]")) {
        const widget = target.dataset.widget;
        let value = target.value;
        if (widget === "number") value = target.value === "" ? null : Number(target.value);
        else if (widget === "bool") value = target.checked;
        mutate(() => ctree.updateNode(editorTree, path, { [target.dataset.ctField]: value, label: null }));
      }
    };
    host.onclick = (event) => {
      const button = event.target.closest("[data-ct-act]");
      if (!button) return;
      const action = button.dataset.ctAct;
      const path = ctree.parsePathKey(button.dataset.path);
      if (action === "add-leaf") {
        mutate(() => ctree.addChild(editorTree, path, ctree.newLeaf("lab_concept")));
      } else if (action === "add-group") {
        mutate(() => ctree.addGroup(editorTree, path));
      } else if (action === "add-sibling") {
        const node = ctree.getNode(editorTree, path);
        const kind = node && ctree.SUPPORTED_LEAF_TYPES.includes(node.type) ? node.type : "lab_concept";
        mutate(() => ctree.addSibling(editorTree, path, ctree.newLeaf(kind)));
      } else if (action === "remove") {
        const node = ctree.getNode(editorTree, path);
        if (ctree.countLeaves(node) > 1 && !confirm("删除该条件（含其全部子条件）？")) return;
        if (canvasSelectedPath.join("-") === path.join("-")) canvasSelectedPath = [];
        mutate(() => ctree.removeNode(editorTree, path));
      } else if (action === "move") {
        mutate(() => ctree.moveNode(editorTree, path, Number(button.dataset.delta)));
      }
    };
    $("#treeModeToggle").onclick = () => {
      const toggle = $("#treeModeToggle");
      if (treeMode) {
        conditionsField.value = ctree.treeToJson(editorTree);
        treeMode = false;
        toggle.textContent = "树模式";
        renderTree();
        return;
      }
      try {
        editorTree = ctree.jsonToTree(conditionsField.value);
      } catch (error) {
        toast(`JSON 不合法，已停留 JSON 模式：${error.message}`, true);
        return;
      }
      treeMode = true;
      canvasSelectedPath = [];
      toggle.textContent = "JSON 模式";
      renderTree();
      scheduleDrlPreview();
    };

    conditionsField.value = ctree.treeToJson(editorTree);
    renderTree();
    loadEditorDict().then((dict) => {
      editorDict = dict;
      if ($("#editorDialog").open && treeMode) { renderTree(); scheduleDrlPreview(); }
    }).catch(() => {
      editorDict = {};
      if ($("#editorDialog").open && treeMode) renderTree();
    });
    $("#editorSave").onclick = async () => {
      const form = $("#editorBody");
      try {
        const conditions = JSON.parse(form.querySelector("[name=conditions]").value);
        const source = JSON.parse(form.querySelector("[name=source]").value || "{}");
        const conclusionExtra = JSON.parse(form.querySelector("[name=conclusion_extra]").value || "{}");
        const unknownConcepts = resolveRuleConceptCodes(conditions).filter((code) => !state.vocab.some((item) => item.code === code));
        if (unknownConcepts.length) throw new Error(`条件引用的概念词汇未定义: ${unknownConcepts.join(", ")}`);
        const payload = {
          ...base,
          rule_id: form.querySelector("[name=rule_id]").value,
          version: base.version,
          domain: form.querySelector("[name=domain]").value,
          rule_type: form.querySelector("[name=rule_type]").value,
          anchor_type: form.querySelector("[name=anchor_type]").value,
          name: form.querySelector("[name=name]").value,
          disease_code: form.querySelector("[name=disease_code]").value,
          disease_name: form.querySelector("[name=disease_name]").value,
          status: form.querySelector("[name=status]").value,
          priority: Number(form.querySelector("[name=priority]").value),
          department: form.querySelector("[name=department]").value,
          conditions,
          source,
          conclusion: {
            ...conclusionExtra,
            diagnosis: form.querySelector("[name=disease_code]").value,
            confidence: form.querySelector("[name=confidence]").value,
            message: form.querySelector("[name=message]").value,
            action: form.querySelector("[name=action]").value,
          },
        };
        if (rule) await api(`/api/rules/${rule.rule_id}`, { method: "PUT", json: payload });
        else await api("/api/rules", { json: payload });
        dialog.close(); toast("规则已保存"); render();
      } catch (error) { toast(error.message, true); }
    };
    $("#editorCancel").onclick = () => dialog.close();
  }

  async function openRuleWizard() {
    await loadBase();
    const [diseases, syndromes, labs, symptoms, exams, findings] = await Promise.all([
      api("/api/vocab/entities/disease").catch(() => []),
      api("/api/vocab/entities/syndrome").catch(() => []),
      api("/api/vocab/entities/lab").catch(() => []),
      api("/api/vocab/entities/symptom").catch(() => []),
      api("/api/vocab/entities/exam").catch(() => []),
      api("/api/vocab/entities/exam_finding").catch(() => []),
    ]);
    const dict = { disease: diseases, syndrome: syndromes, lab: labs, symptom: symptoms, exam: exams, exam_finding: findings };
    const labConcepts = state.vocab.filter((v) => v.category === "lab_concept");
    const rows = [];

    const dictOptions = (items, selected = "") => ["", ...items.map((item) => item.code)]
      .map((code) => `<option value="${esc(code)}" ${code === selected ? "selected" : ""}>${esc(code ? `${nameOf(items, code)}（${code}）` : "请选择")}</option>`).join("");
    const nameOf = (items, code) => (items.find((item) => item.code === code) || {}).name || code;
    const conceptOptions = (selected = []) => labConcepts
      .map((v) => `<option value="${esc(v.code)}" ${selected.includes(v.code) ? "selected" : ""}>${esc(v.name_cn)}（${esc(v.code)}）</option>`).join("");

    function rowHtml(row, index) {
      const kindSelect = `<select data-row="${index}" data-field="kind">${(window.CdssRuleWizard.CONDITION_KINDS).map((k) => `<option value="${k.value}" ${row.kind === k.value ? "selected" : ""}>${k.label}</option>`).join("")}</select>`;
      let fields = "";
      if (row.kind === "lab_concept") {
        fields = `
          <div class="field"><label>检验</label><select data-row="${index}" data-field="lab_code">${dictOptions(dict.lab, row.lab_code || "")}</select></div>
          <div class="field"><label>概念（可多选=OR）</label><select data-row="${index}" data-field="concept_codes" multiple size="3">${conceptOptions(row.concept_codes || [])}</select></div>`;
      } else if (row.kind === "symptom") {
        fields = `<div class="field"><label>症状</label><select data-row="${index}" data-field="symptom_code">${dictOptions(dict.symptom, row.symptom_code || "")}</select></div>`;
      } else if (row.kind === "exam_finding") {
        fields = `
          <div class="field"><label>检查</label><select data-row="${index}" data-field="exam_code">${dictOptions(dict.exam, row.exam_code || "")}</select></div>
          <div class="field"><label>发现</label><select data-row="${index}" data-field="finding_code">${dictOptions(dict.exam_finding, row.finding_code || "")}</select></div>`;
      } else {
        fields = `
          <div class="field"><label>观察类型</label><select data-row="${index}" data-field="observation_type">${["symptom", "tongue", "pulse", "sign"].map((t) => `<option value="${t}" ${row.observation_type === t ? "selected" : ""}>${t}</option>`).join("")}</select></div>
          <div class="field"><label>编码</label><input data-row="${index}" data-field="code" value="${esc(row.code || "")}" placeholder="选择类型后输入编码或从图谱页复制"></div>`;
      }
      return `<div class="inline-controls" data-row-wrap="${index}">${kindSelect}${fields}<button class="btn sm danger" data-row-remove="${index}">删除行</button></div>`;
    }

    function renderRows() {
      $("#wizardRows").innerHTML = rows.map((row, index) => rowHtml(row, index)).join("") || '<div class="empty">尚未添加条件行</div>';
      $("#wizardPreview").textContent = window.CdssRuleWizard.describeTree(window.CdssRuleWizard.buildConditionTree(collectRows())) || "（空条件：治疗类规则的合法写法）";
      bindRowEvents();
    }

    function collectRows() {
      return rows.map((row, index) => {
        const collected = { kind: row.kind };
        document.querySelectorAll(`[data-row-wrap="${index}"] [data-row="${index}"]`).forEach((input) => {
          const field = input.dataset.field;
          if (field === "concept_codes") {
            collected[field] = [...input.selectedOptions].map((option) => option.value);
          } else {
            collected[field] = input.value;
          }
        });
        if (collected.kind === "lab_concept" && collected.concept_codes == null) collected.concept_codes = [];
        return collected;
      });
    }

    function bindRowEvents() {
      document.querySelectorAll("[data-row-wrap]").forEach((wrap) => {
        const index = Number(wrap.dataset.rowWrap);
        wrap.querySelectorAll("select[data-field], input[data-field]").forEach((input) => {
          input.onchange = () => {
            if (input.dataset.field === "kind") {
              rows[index] = { kind: input.value };
            } else {
              rows[index][input.dataset.field] = input.dataset.field === "concept_codes"
                ? [...input.selectedOptions].map((option) => option.value)
                : input.value;
            }
            renderRows();
          };
        });
        wrap.querySelector("[data-row-remove]").onclick = () => {
          rows.splice(index, 1);
          renderRows();
        };
      });
      $("#wizardAddRow").onclick = () => {
        rows.push({ kind: "lab_concept" });
        renderRows();
      };
    }

    $("#editorBody").innerHTML = `
      <h3>规则向导（按字典选择，无需写 JSON）</h3>
      <div class="form-grid">
        <div class="field"><label>领域</label><select id="wizardDomain"><option value="western">西医</option><option value="tcm">中医</option></select></div>
        <div class="field"><label>规则类型</label><select id="wizardRuleType"><option value="diagnosis">诊断</option><option value="treatment">治疗</option></select></div>
        <div class="field"><label>目标（疾病/证候）</label><select id="wizardAnchor">${dictOptions(diseases)}${dictOptions(syndromes).replace('<option value="">请选择</option>', "")}</select></div>
        <div class="field"><label>规则名称</label><input id="wizardName" placeholder="例如：STEMI 肌钙蛋白诊断"></div>
        <div class="field"><label>科室（可空）</label><input id="wizardDepartment"></div>
        <div class="field"><label>优先级</label><input id="wizardPriority" type="number" value="50"></div>
        <div class="field half"><label>结论提示</label><input id="wizardMessage"></div>
        <div class="field half"><label>建议动作</label><input id="wizardAction"></div>
      </div>
      <h4>条件行（行间 AND；同一检验选多个概念 = OR）</h4>
      <div id="wizardRows"></div>
      <div class="actions"><button id="wizardAddRow" class="btn">添加条件行</button></div>
      <h4>条件预览</h4>
      <div id="wizardPreview" class="mono muted"></div>`;
    rows.push({ kind: "lab_concept" });
    renderRows();
    const dialog = $("#editorDialog");
    dialog.showModal();
    $("#editorSave").onclick = async () => {
      try {
        const domain = $("#wizardDomain").value;
        const anchorCode = $("#wizardAnchor").value;
        const anchorType = domain === "tcm" ? "syndrome" : "disease";
        if (!anchorCode) throw new Error("请选择目标疾病/证候");
        const conditions = window.CdssRuleWizard.buildConditionTree(collectRows());
        const entry = (dict[anchorType] || []).find((item) => item.code === anchorCode) || {};
        const payload = {
          rule_id: `CUSTOM-${Date.now()}`,
          version: 1,
          domain,
          anchor_type: anchorType,
          rule_type: domain === "tcm" ? "tcm_pattern" : $("#wizardRuleType").value,
          name: $("#wizardName").value || `向导创建: ${entry.name || anchorCode}`,
          disease_code: anchorCode,
          disease_name: entry.name || anchorCode,
          priority: Number($("#wizardPriority").value || 50),
          status: "draft",
          department: $("#wizardDepartment").value || null,
          conditions,
          conclusion: {
            diagnosis: anchorCode,
            confidence: "medium",
            message: $("#wizardMessage").value || "",
            action: $("#wizardAction").value || "",
          },
          source: {},
          exclusions: [],
          tags: ["wizard"],
          created_by: localStorage.getItem("cdssReviewer") || "未署名",
        };
        await api("/api/rules", { json: payload });
        dialog.close();
        toast("规则已保存为草稿，可在列表中审核启用");
        renderRules();
      } catch (error) {
        toast(error.message, true);
      }
    };
    $("#editorCancel").onclick = () => dialog.close();
  }

  async function renderEngineChip(container) {
    if (!container) return;
    try {
      const [status, config] = await Promise.all([
        api("/api/engine/status"),
        api("/api/engine/config"),
      ]);
      const active = config.active === "drools" ? "drools" : "native";
      const next = active === "native" ? "drools" : "native";
      const unreachable = active === "drools" && !Boolean(status.drools?.reachable);
      const label = `引擎：${active === "drools" ? "Drools" : "原生"}${unreachable ? "（不可达，已自动回退原生）" : ""}`;
      const title = unreachable
        ? `sidecar 探测：${String(status.drools?.detail || "不可达")}；点击切回原生引擎`
        : `点击切换为 ${next === "drools" ? "Drools（sidecar 不可达时自动回退原生）" : "原生引擎"}`;
      container.innerHTML = `<button id="engineSwitch" class="btn ${active === "drools" ? "primary" : ""}" title="${esc(title)}">${esc(label)}</button>`;
      $("#engineSwitch").onclick = async () => {
        try {
          await api("/api/engine/config", { method: "PUT", json: { config: { active: next } } });
          toast(`推理引擎已切换为 ${next === "drools" ? "Drools（sidecar 不可达时自动回退原生）" : "原生引擎"}`);
          renderEngineChip(container);
        } catch (error) {
          toast(error.message, true);
        }
      };
    } catch (error) {
      container.innerHTML = '<span class="muted">引擎状态不可用</span>';
    }
  }

  async function openRuleHistory(ruleId) {
    const items = await api(`/api/rules/${encodeURIComponent(ruleId)}/history`);
    $("#editorBody").innerHTML = `
      <h3>规则历史 <span class="mono muted">${esc(ruleId)}</span></h3>
      ${items.length ? table(["版本", "时间", "动作", "操作人", "名称", "操作"], items.map((item) => `<tr>
        <td>v${item.seq}</td>
        <td class="mono">${esc((item.timestamp || "").replace("T", " ").slice(0, 19))}</td>
        <td>${esc(item.action)}</td>
        <td>${esc(item.actor)}</td>
        <td>${esc((item.rule || {}).name || "")}</td>
        <td><button class="btn sm" data-history-restore="${item.seq}">恢复此版本</button></td>
      </tr>`)) : '<div class="empty">暂无历史（保存规则后自动记录）</div>'}`;
    const dialog = $("#editorDialog");
    const save = $("#editorSave");
    save.hidden = true;
    dialog.onclose = () => { save.hidden = false; dialog.onclose = null; };
    dialog.showModal();
    $("#editorCancel").onclick = () => dialog.close();
    $("#editorBody").querySelectorAll("[data-history-restore]").forEach((button) => {
      button.onclick = async () => {
        if (!confirm(`恢复到 v${button.dataset.historyRestore}？当前内容会另存为新版本，可再次回滚。`)) return;
        try {
          await api(`/api/rules/${encodeURIComponent(ruleId)}/history/${button.dataset.historyRestore}/restore`, {
            method: "POST",
            json: { reviewer: localStorage.getItem("cdssReviewer") || "未署名" },
          });
          dialog.close();
          toast("已恢复历史版本");
          renderRules();
        } catch (error) {
          toast(error.message, true);
        }
      };
    });
  }

  async function renderStandards() {
    await loadBase();
    const hospital = state.hospitalId || (state.hospitals[0] && state.hospitals[0].hospital_id) || "";
    state.hospitalId = hospital;
    state.standards = hospital ? await api(`/api/standards/${hospital}`) : [];
    const labRequirements = new Map();
    const collectLabRequirements = (node) => {
      if (!node || typeof node !== "object") return;
      if (node.type === "lab_concept" && node.lab_code && node.concept_code) {
        labRequirements.set(`${node.lab_code}:${node.concept_code}`, {
          lab_code: node.lab_code,
          concept_code: node.concept_code,
        });
      }
      (node.children || []).forEach(collectLabRequirements);
    };
    state.rules
      .filter((rule) => normalizeDomainClient(rule.domain) === "western")
      .forEach((rule) => collectLabRequirements(rule.conditions));
    const activeStandardKeys = new Set(state.standards
      .filter((standard) => standard.status === "active")
      .map((standard) => `${standard.lab_code}:${standard.concept_code}`));
    const requiredStandards = [...labRequirements.values()]
      .sort((a, b) => `${a.lab_code}:${a.concept_code}`.localeCompare(`${b.lab_code}:${b.concept_code}`, "zh-Hans-CN"));
    const missingStandardCount = requiredStandards.filter(
      (item) => !activeStandardKeys.has(`${item.lab_code}:${item.concept_code}`)
    ).length;
    const editing = state.standardEditIndex >= 0 ? state.standards[state.standardEditIndex] : null;
    const formValue = (field, fallback = "") => {
      const value = editing && editing[field] !== null && editing[field] !== undefined ? editing[field] : fallback;
      return esc(value);
    };
    content.innerHTML = `
      ${panel("医院上下文", `
        <div class="inline-controls">
          <div class="field"><label>当前医院</label><select id="hospitalSelect">${state.hospitals.map((h) => `<option value="${esc(h.hospital_id)}" ${h.hospital_id === hospital ? "selected" : ""}>${esc(h.hospital_id)}（${h.standard_count}）</option>`).join("")}</select></div>
          <div class="field"><label>新医院 ID</label><input id="newHospital" placeholder="例如 MY_HOSPITAL"></div>
          <button id="loadNewHospital" class="btn">切换 / 创建</button>
        </div>
      `, "", "每家医院可维护不同检验标准；规则仍然只引用概念")}
      ${panel("规则标准覆盖", requiredStandards.length ? table(["检验", "抽象概念", "覆盖状态"], requiredStandards.map((item) => {
        const covered = activeStandardKeys.has(`${item.lab_code}:${item.concept_code}`);
        return `<tr><td class="mono">${esc(item.lab_code)}</td><td>${esc(conceptName(item.concept_code))}</td><td>${covered ? '<span class="badge active">已覆盖</span>' : '<span class="badge danger">缺失</span>'}</td></tr>`;
      })) : '<div class="empty">当前没有规则引用检验标准。</div>', "", missingStandardCount ? `当前医院缺少 ${missingStandardCount} 项规则所需标准` : "当前医院规则引用的检验概念均已覆盖")}
      ${panel(editing ? `编辑标准 #${state.standardEditIndex + 1}` : "新增标准", `
        <div class="form-grid">
          <div class="field"><label>检验编码</label><input id="stdLabCode" value="${formValue("lab_code")}" placeholder="MYO"></div>
          <div class="field"><label>检验名称</label><input id="stdLabName" value="${formValue("lab_name")}"></div>
          <div class="field"><label>抽象概念</label><select id="stdConcept">${state.vocab.filter((v) => v.category === "lab_concept").map((v) => `<option value="${v.code}" ${editing && editing.concept_code === v.code ? "selected" : ""}>${v.name_cn} / ${v.code}</option>`).join("")}</select></div>
          <div class="field"><label>操作符</label><select id="stdOperator">${[">", ">=", "<", "<=", "="].map((op) => `<option value="${op}" ${editing && editing.operator === op ? "selected" : ""}>${op}</option>`).join("")}</select></div>
          <div class="field"><label>阈值</label><input id="stdThreshold" type="number" step="0.001" value="${editing && editing.threshold_value != null ? editing.threshold_value : ""}"></div>
          <div class="field"><label>单位</label><input id="stdUnit" value="${formValue("threshold_unit", "ng/mL")}"></div>
          <div class="field"><label>ULN 倍数</label><input id="stdUln" type="number" step="0.1" value="${editing && editing.uln_multiplier != null ? editing.uln_multiplier : ""}"></div>
          <div class="field"><label>标准来源</label><select id="stdRefSource"><option value="STATIC">STATIC</option><option value="LIS_REALTIME">LIS_REALTIME</option></select></div>
          <div class="field"><label>适用性别</label><select id="stdGender"><option value="">不限</option><option value="M">男</option><option value="F">女</option></select></div>
          <div class="field"><label>年龄下限</label><input id="stdAgeMin" type="number" value="${editing && editing.context_age_min != null ? editing.context_age_min : ""}"></div>
          <div class="field"><label>年龄上限</label><input id="stdAgeMax" type="number" value="${editing && editing.context_age_max != null ? editing.context_age_max : ""}"></div>
          <div class="field"><label>状态</label><select id="stdStatus">${["active", "draft", "retired"].map((s) => `<option value="${s}" ${editing && editing.status === s ? "selected" : ""}>${s}</option>`).join("")}</select></div>
          <div class="field wide"><label>说明</label><input id="stdNote" value="${formValue("source_note")}"></div>
        </div>
        <div class="actions"><button id="stdSave" class="btn primary">${editing ? "保存修改" : "新增标准"}</button>${editing ? '<button id="stdCancel" class="btn ghost">取消编辑</button>' : ""}</div>
      `)}
      ${panel(`${hospital || "未选择医院"} 标准列表`, table(["检验", "抽象概念", "判定标准", "上下文", "版本", "状态", "操作"], state.standards.map((s, i) => `<tr><td><strong>${esc(s.lab_name || s.lab_code)}</strong><div class="mono">${esc(s.lab_code)}</div></td><td>${esc(conceptName(s.concept_code))}</td><td class="mono">${s.threshold_value != null ? `${esc(s.operator)}${s.threshold_value} ${esc(s.threshold_unit)}` : "-"}${s.uln_multiplier ? `<div>ULN×${s.uln_multiplier}</div>` : ""}</td><td>${s.context_gender || "不限"} ${s.context_age_min || s.context_age_max ? `${s.context_age_min || "-"}~${s.context_age_max || "-"}` : ""}</td><td>v${s.version}</td><td>${badge(s.status)}</td><td><div class="actions"><button class="btn sm" data-std-edit="${i}">编辑</button><button class="btn sm" data-std-active="${i}">启用</button><button class="btn sm danger" data-std-delete="${i}">删除</button></div></td></tr>`)))}
    `;
    $("#hospitalSelect").onchange = (e) => { state.hospitalId = e.target.value; state.standardEditIndex = -1; renderStandards(); };
    $("#loadNewHospital").onclick = () => { const id = $("#newHospital").value.trim().toUpperCase(); if (!id) return toast("请输入医院 ID", true); state.hospitalId = id; state.standardEditIndex = -1; toast("已切换，新增标准后医院将自动创建"); renderStandards(); };
    $("#stdSave").onclick = saveStandard;
    const cancel = $("#stdCancel"); if (cancel) cancel.onclick = () => { state.standardEditIndex = -1; renderStandards(); };
    content.querySelectorAll("[data-std-edit]").forEach((b) => b.onclick = () => { state.standardEditIndex = Number(b.dataset.stdEdit); renderStandards(); });
    content.querySelectorAll("[data-std-active]").forEach((b) => b.onclick = async () => { await api(`/api/standards/${hospital}/${b.dataset.stdActive}/activate`, { method: "POST" }); toast("标准已启用"); renderStandards(); });
    content.querySelectorAll("[data-std-delete]").forEach((b) => b.onclick = async () => { if (!confirm("确认删除该标准？")) return; await api(`/api/standards/${hospital}/${b.dataset.stdDelete}`, { method: "DELETE" }); toast("标准已删除"); state.standardEditIndex = -1; renderStandards(); });
  }

  async function saveStandard() {
    const hospital = state.hospitalId; if (!hospital) return toast("请先选择医院", true);
    const num = (id) => { const v = $(id).value; return v === "" ? null : Number(v); };
    const payload = {
      hospital_id: hospital, lab_code: $("#stdLabCode").value.trim().toUpperCase(), lab_name: $("#stdLabName").value.trim(),
      concept_code: $("#stdConcept").value, operator: $("#stdOperator").value, threshold_value: num("#stdThreshold"),
      threshold_unit: $("#stdUnit").value.trim(), uln_multiplier: num("#stdUln"), ref_source: $("#stdRefSource").value,
      context_gender: $("#stdGender").value || null, context_age_min: num("#stdAgeMin"), context_age_max: num("#stdAgeMax"),
      status: $("#stdStatus").value, source_note: $("#stdNote").value.trim(), version: 1,
    };
    if (!payload.lab_code) return toast("检验编码必填", true);
    const editing = state.standards[state.standardEditIndex];
    if (editing) payload.version = editing.version + 1;
    if (state.standardEditIndex >= 0) await api(`/api/standards/${hospital}/${state.standardEditIndex}`, { method: "PUT", json: payload });
    else await api(`/api/standards/${hospital}`, { json: payload });
    state.standardEditIndex = -1; toast("标准已保存"); renderStandards();
  }

  async function renderInference() {
    await loadBase();
    state.demoCases = await api("/api/inference/demo-patients?domain=western");
    if (!state.demoCaseId) {
      state.demoCaseId = state.demoCases.find((item) => item.default)?.case_id
        || state.demoCases[0]?.case_id
        || "";
    }
    if (!state.caseRecord) {
      try { state.caseRecord = JSON.parse(localStorage.getItem(CASE_STORAGE_KEY) || "null"); } catch (_) { state.caseRecord = null; }
    }
    if (!state.caseRecord) {
      const selectedCase = state.demoCases.find((item) => item.case_id === state.demoCaseId);
      const patient = selectedCase
        ? await api(`/api/inference/demo-patient?case_id=${encodeURIComponent(state.demoCaseId)}`)
        : await api("/api/inference/demo-patient");
      state.caseRecord = caseRecordFromPatient(patient);
    }
    const record = state.caseRecord;
    const basicFields = `
      <div class="form-grid">
        <div class="field"><label>患者 ID</label><input id="casePatientId" value="${esc(record.patient_id || "")}"></div>
        <div class="field"><label>执行医院</label><input id="caseHospital" list="hospitalOptions" value="${esc(record.hospital_id || "")}"><datalist id="hospitalOptions">${state.hospitals.map((h) => `<option value="${esc(h.hospital_id)}"></option>`).join("")}</datalist></div>
        <div class="field"><label>年龄</label><input id="caseAge" type="number" min="0" max="130" value="${esc(record.age ?? "")}"></div>
        <div class="field"><label>性别</label><select id="caseGender"><option value="M" ${record.gender === "M" ? "selected" : ""}>男</option><option value="F" ${record.gender === "F" ? "selected" : ""}>女</option><option value="U" ${record.gender === "U" ? "selected" : ""}>未知</option></select></div>
        <div class="field half"><label>就诊时间</label><input id="caseEncounter" type="datetime-local" value="${esc(record.encounter_time || "")}"></div>
        <div class="field half"><label>保存状态</label><div id="caseSavedAt" class="readonly-value">${state.caseSavedAt ? `已保存 ${esc(state.caseSavedAt)}` : "未保存"}</div></div>
      </div>`;
    const narrativeField = (id, label, value, rows = 3) => `<div class="field wide"><label>${esc(label)}</label><textarea id="${id}" class="narrative" rows="${rows}">${esc(value || "")}</textarea></div>`;
    const selectedCase = state.demoCases.find((item) => item.case_id === state.demoCaseId);
    content.innerHTML = `
      ${panel("模拟病历测试", '<div class="readonly-value">结构化病历会转换为 PatientSnapshot；医院阈值只在执行层解析。</div>', `
        <span class="muted" id="caseSavedAtText">${state.caseSavedAt ? `保存于 ${esc(state.caseSavedAt)}` : "未保存"}</span>
        <button id="saveCase" class="btn">保存病历</button>
        <button id="runInference" class="btn primary">保存并执行推荐</button>
      `)}
      ${panel("选择模拟病历", `
        <div class="inline-controls">
          <div class="field"><label>病例</label><select id="demoCaseSelect">${state.demoCases.map((item) => `<option value="${esc(item.case_id)}" ${item.case_id === state.demoCaseId ? "selected" : ""}>${esc(item.name)}</option>`).join("")}</select></div>
          <button id="loadCase" class="btn primary">载入病例</button>
          <span class="muted" id="demoCaseDesc">${esc(selectedCase ? `${selectedCase.category || ""} · ${selectedCase.description || ""}` : "选择病例后载入")}</span>
        </div>
      `, "", "切换病例会覆盖当前表单；可修改后再保存或执行推荐")}
      ${panel("1. 患者基本信息", basicFields)}
      ${panel("2. 主诉", `<div class="form-grid">${narrativeField("caseChief", "主诉", record.chief_complaint, 2)}</div><div class="section-title">结构化症状</div>${caseMatrix("symptoms", record.symptoms, caseRowFields.symptoms)}`)}
      ${panel("3. 现病史", `<div class="form-grid">${narrativeField("caseHistoryPresent", "现病史", record.history_present, 4)}${narrativeField("caseMedications", "当前用药（编码，逗号分隔）", record.current_medications, 2)}${narrativeField("caseAllergies", "过敏史（编码，逗号分隔）", record.allergies, 2)}</div>`)}
      ${panel("4. 既往史", `<div class="form-grid">${narrativeField("caseHistoryPast", "既往史", record.history_past, 3)}${narrativeField("casePastCodes", "参与推理的疾病编码（逗号分隔）", record.past_history_codes, 3)}</div>`)}
      ${panel("5. 本次检验、检查数据", `
        <div class="section-title">检验</div>${caseMatrix("labs", record.labs, caseRowFields.labs)}
        <div class="section-title">检查发现</div>${caseMatrix("exam_findings", record.exam_findings, caseRowFields.exam_findings)}
        <div class="section-title">生命体征</div>${caseMatrix("vitals", record.vitals, caseRowFields.vitals)}
      `)}
      <details class="debug-panel"><summary>推理输入 JSON</summary><textarea id="patientJson" class="code" readonly></textarea></details>
      <div id="inferResult"></div>
    `;
    $("#caseSavedAtText").textContent = state.caseSavedAt ? `保存于 ${state.caseSavedAt}` : "未保存";
    applyCaseRecord(record);
    const updateCaseDescription = () => {
      const item = state.demoCases.find((entry) => entry.case_id === state.demoCaseId);
      const desc = $("#demoCaseDesc");
      if (desc) desc.textContent = item ? `${item.category || ""} · ${item.description || ""}` : "选择病例后载入";
    };
    $("#demoCaseSelect").onchange = (event) => {
      state.demoCaseId = event.target.value;
      updateCaseDescription();
    };
    $("#loadCase").onclick = () => loadDemoPatient($("#demoCaseSelect").value);
    $("#saveCase").onclick = () => { try { saveCaseRecord(); } catch (error) { toast(error.message, true); } };
    $("#runInference").onclick = runInference;
    updateCaseDescription();
    if (state.inferenceResult) renderInferenceResult();
  }

  async function loadDemoPatient(caseId = "", notice = true) {
    const query = caseId ? `?case_id=${encodeURIComponent(caseId)}` : "";
    const patient = await api(`/api/inference/demo-patient${query}`);
    patient.hospital_id = state.caseRecord ? state.caseRecord.hospital_id || state.hospitalId : state.hospitalId || patient.hospital_id;
    state.demoCaseId = caseId || state.demoCaseId;
    state.caseRecord = caseRecordFromPatient(patient);
    state.inferenceResult = null;
    applyCaseRecord(state.caseRecord);
    const resultBox = $("#inferResult"); if (resultBox) resultBox.innerHTML = "";
    if (notice) toast(`已载入病例：${patient.display_name || patient.patient_id}`);
  }

  function handleCaseClick(event) {
    const add = event.target.closest("[data-case-add]");
    if (add) {
      const kind = add.dataset.caseAdd;
      state.caseRecord = collectCaseRecord();
      const key = { symptoms: "symptoms", labs: "labs", exam_findings: "exam_findings", vitals: "vitals" }[kind];
      state.caseRecord[key].push(kind === "labs" ? { unit: "ng/mL" } : { present: true });
      applyCaseRecord(state.caseRecord);
      return;
    }
    const remove = event.target.closest("[data-case-delete]");
    if (remove) {
      const [kind, index] = remove.dataset.caseDelete.split(":");
      state.caseRecord = collectCaseRecord();
      state.caseRecord[{ symptoms: "symptoms", labs: "labs", exam_findings: "exam_findings", vitals: "vitals" }[kind]].splice(Number(index), 1);
      applyCaseRecord(state.caseRecord);
    }
  }

  async function runInference() {
    try {
      const record = saveCaseRecord(false);
      const patient = buildPatientSnapshot(record);
      $("#patientJson").value = JSON.stringify(patient, null, 2);
      state.inferenceResult = await api("/api/inference/run", { json: { patient } });
      toast(`保存完成，疑似诊断推荐 ${state.inferenceResult.conclusion_count} 条`);
      renderInferenceResult();
    } catch (error) { toast(error.message, true); }
  }

  async function renderTcmInference() {
    const [symptoms, tongues, pulses, signs, constitutions] = await Promise.all([
      api("/api/vocab/entities/symptom"),
      api("/api/vocab/entities/tongue"),
      api("/api/vocab/entities/pulse"),
      api("/api/vocab/entities/sign"),
      api("/api/vocab/entities/constitution"),
    ]);
    state.tcmTestCases = await api("/api/inference/demo-patients?domain=tcm");
    const optionGroup = (title, type, items, description) => panel(
      title,
      `<div class="tcm-options">${items.map((item) => `
        <label class="tcm-option">
          <input type="checkbox" data-obs-type="${esc(type)}" data-obs-code="${esc(item.code)}" data-obs-key="${esc(type)}:${esc(item.code)}" data-obs-name="${esc(item.name)}">
          <span>${esc(item.name)}</span><small class="mono">${esc(item.code)}</small>
        </label>`).join("")}</div>`,
      "",
      description,
    );
    content.innerHTML = `
      ${panel("中医辨证运行", `
        <div class="inline-controls">
          <button id="tcmRunBtn" class="btn primary">运行辨证</button>
          <button id="tcmDemoBtn" class="btn secondary">载入示例病历</button>
          <button id="tcmTestBtn" class="btn secondary">运行用例测试</button>
          <div class="field"><label>中医测试用例</label><select id="tcmTestCaseSelector">
            <option value="">选择用例</option>
            ${state.tcmTestCases.map((item) => `<option value="${esc(item.case_id)}" ${item.case_id === state.tcmTestCaseId ? "selected" : ""}>${esc(item.name || item.case_id)}</option>`).join("")}
          </select></div>
          <span class="muted">示例病历：气阴两虚证（胸闷、气短、乏力、自汗，舌淡红、苔薄白，脉细弱）</span>
        </div>`, "", "用例测试只在本次请求中评估草稿辨证规则，不会改写规则状态")}
      <div id="tcmForm">
        ${optionGroup("症状（主症/次症）", "symptom", symptoms, "勾选病历中记录的症状")}
        ${optionGroup("舌象", "tongue", tongues, "舌质与舌苔分别勾选")}
        ${optionGroup("脉象", "pulse", pulses, "可多选复合脉象")}
      </div>
      <div id="tcmInferenceResult">${state.tcmInferenceResult ? "" : '<div class="empty">选择观察项后运行辨证</div>'}</div>
      <div id="tcmTestResult">${state.tcmTestResult ? "" : '<div class="empty">选择测试用例后运行断言比对</div>'}</div>
    `;
    $("#tcmRunBtn").onclick = runTcmInference;
    $("#tcmDemoBtn").onclick = async () => {
      try {
        const demo = await api("/api/inference/demo-patient?domain=tcm");
        const selected = new Set((demo.observations || []).map((item) => `${item.observation_type}:${item.code}`));
        document.querySelectorAll("#tcmForm input[data-obs-key]").forEach((input) => {
          input.checked = selected.has(input.dataset.obsKey);
        });
        toast("已载入中医示例病历");
      } catch (error) { toast(error.message, true); }
    };
    $("#tcmTestBtn").onclick = runTcmTestCase;
    $("#tcmTestCaseSelector").onchange = async (event) => {
      state.tcmTestCaseId = event.target.value;
      await loadTcmTestCase();
    };
    if (state.tcmInferenceResult) renderTcmInferenceResult();
    if (state.tcmTestResult) renderTcmTestResult();
  }

  async function loadTcmTestCase() {
    if (!state.tcmTestCaseId) {
      state.tcmExpected = null;
      return;
    }
    try {
      const payload = await api(`/api/inference/demo-case?case_id=${encodeURIComponent(state.tcmTestCaseId)}`);
      const selected = new Set((payload.patient.observations || []).map((item) => `${item.observation_type}:${item.code}`));
      document.querySelectorAll("#tcmForm input[data-obs-key]").forEach((input) => {
        input.checked = selected.has(input.dataset.obsKey);
      });
      state.tcmExpected = payload.case.expected || null;
      toast(`已载入用例：${payload.case.name || payload.case.case_id}`);
    } catch (error) { toast(error.message, true); }
  }

  async function runTcmInference() {
    const observations = [...document.querySelectorAll("#tcmForm input[data-obs-key]:checked")].map((input) => ({
      observation_type: input.dataset.obsType,
      code: input.dataset.obsCode,
      name: input.dataset.obsName,
      present: true,
    }));
    if (!observations.length) { toast("请至少选择一个观察项", true); return; }
    const patient = {
      patient_id: "TCM-WEB-DEMO",
      hospital_id: "TCM_DEMO",
      age: 60,
      gender: "U",
      observations,
      labs: [],
      exam_findings: [],
      symptoms: [],
      vitals: [],
      past_history: [],
      current_medications: [],
      allergies: [],
    };
    try {
      state.tcmInferenceResult = await api("/api/inference/run", {
        json: { patient, domain: "tcm" },
      });
      renderTcmInferenceResult();
    } catch (error) { toast(error.message, true); }
  }

  async function runTcmTestCase() {
    if (!state.tcmTestCaseId) { toast("请先选择中医测试用例", true); return; }
    if (!state.tcmExpected) await loadTcmTestCase();
    const observations = collectTcmObservations();
    if (!observations.length) { toast("请至少选择一个观察项", true); return; }
    const patient = {
      patient_id: `TCM-TEST-${state.tcmTestCaseId}`,
      hospital_id: "TCM_DEMO",
      age: 60,
      gender: "U",
      observations,
      labs: [],
      exam_findings: [],
      symptoms: [],
      vitals: [],
      past_history: [],
      current_medications: [],
      allergies: [],
    };
    try {
      state.tcmTestResult = await api("/api/inference/test-case", {
        json: {
          patient,
          domain: "tcm",
          case_id: state.tcmTestCaseId,
          expected: state.tcmExpected,
          include_draft_patterns: true,
        },
      });
      renderTcmTestResult();
    } catch (error) { toast(error.message, true); }
  }

  function collectTcmObservations() {
    return [...document.querySelectorAll("#tcmForm input[data-obs-key]:checked")].map((input) => ({
      observation_type: input.dataset.obsType,
      code: input.dataset.obsCode,
      name: input.dataset.obsName,
      present: true,
    }));
  }

  function renderTcmInferenceResult() {
    const result = state.tcmInferenceResult;
    const mount = $("#tcmInferenceResult");
    if (!mount || !result) return;
    const conclusions = result.conclusions || [];
    const patterns = conclusions.filter((item) => item.rule_type === "tcm_pattern");
    const treatments = conclusions.filter((item) => item.rule_type === "tcm_treatment");
    const prescriptions = conclusions.filter((item) => item.rule_type === "tcm_prescription");
    const contra = conclusions.filter((item) => item.rule_type === "tcm_contraindication");

    const patternRows = patterns.map((item) => `
      <tr>
        <td><strong>${esc(item.anchor_name || item.disease_name)}</strong><div class="mono muted">${esc(item.anchor_code || item.disease_code)}</div></td>
        <td>${item.score != null ? `${(item.score * 100).toFixed(1)}%` : "-"}<div class="muted">${item.matched_conditions}/${item.total_conditions}</div></td>
        <td>${badge(item.confidence || "medium")}</td>
        <td>${(item.evidence || []).map((trace) => `<div>${esc(trace.description)}${trace.source_ref ? `<div class="mono muted">${esc(trace.source_ref)}</div>` : ""}</div>`).join("") || "-"}</td>
        <td class="mono">${esc(item.guideline_source || "-")}</td>
      </tr>`);
    const treatmentRows = treatments.map((item) => `
      <tr>
        <td>${esc(item.anchor_name || item.disease_name)}</td>
        <td><strong>${esc(item.treatment_name || item.treatment_method_code || "-")}</strong></td>
        <td class="mono">${esc((item.triggered_by || []).join(", ") || "-")}</td>
        <td class="mono">${esc(item.guideline_source || "-")}</td>
      </tr>`);
    const prescriptionRows = prescriptions.map((item) => `
      <tr>
        <td>${esc(item.anchor_name || item.disease_name)}</td>
        <td><strong>${esc(item.formula_name || item.formula_code || "-")}</strong></td>
        <td>${(item.herbs || []).map((herb) => `${esc(herb.name || herb.code)}${herb.dose ? ` ${esc(herb.dose)}` : ""}${herb.role ? `（${esc(herb.role)}）` : ""}`).join("、") || "-"}</td>
        <td class="mono">${esc(item.guideline_source || "-")}</td>
      </tr>`);

    mount.innerHTML = `
      <div class="result-banner ${conclusions.length ? "ok" : "warning"}">共 ${conclusions.length} 条结论：证候 ${patterns.length} / 治法 ${treatments.length} / 方药 ${prescriptions.length} / 禁忌 ${contra.length}</div>
      ${panel("证候结论", table(["证候", "评分", "置信度", "证据链", "指南溯源"], patternRows), "", "评分 = 命中的主症/次症/舌脉条目 ÷ 规则中的全部条目")}
      ${panel("治法建议", table(["证候", "治法", "触发证候规则", "指南溯源"], treatmentRows))}
      ${panel("方药建议", table(["证候", "方剂", "组成（剂量/君臣佐使）", "指南溯源"], prescriptionRows))}
      ${contra.length ? panel("禁忌", table(["证候", "禁忌", "指南溯源"], contra.map((item) => `<tr><td>${esc(item.anchor_name)}</td><td>${esc(item.message)}</td><td class="mono">${esc(item.guideline_source || "-")}</td></tr>`))) : ""}
      ${!conclusions.length ? '<div class="result-banner warning">未匹配到证候规则：请确认规则已启用，且观察项覆盖主症/舌脉条件。</div>' : ""}
    `;
  }

  function renderTcmTestResult() {
    const result = state.tcmTestResult;
    const mount = $("#tcmTestResult");
    if (!mount || !result) return;
    const checks = result.checks || [];
    const candidates = result.ranked_syndromes || [];
    const dependencies = result.dependencies || [];
    const categoryText = (item) => Object.entries((item.score_breakdown || {}).categories || {})
      .map(([type, value]) => `${type}:${Math.round((value.score || 0) * 100)}%`).join("、") || "-";
    const checkRows = checks.map((item) => `
      <tr>
        <td>${badge(item.passed ? "high" : "low")} ${esc(item.label)}</td>
        <td><div class="mono">${esc(JSON.stringify(item.expected))}</div></td>
        <td><div class="mono">${esc(JSON.stringify(item.actual))}</div></td>
        <td>${esc(item.reason || "-")}</td>
      </tr>`);
    const candidateRows = candidates.map((item) => {
      const conclusion = item.conclusion || {};
      return `
        <tr>
          <td>${badge(item.matched ? "high" : "low")} ${esc(conclusion.anchor_name || conclusion.disease_name || "-")}<div class="mono muted">${esc(conclusion.anchor_code || "-")}</div></td>
          <td>${conclusion.score != null ? `${(conclusion.score * 100).toFixed(1)}%` : "-"}<div class="muted">阈值 ${esc(((conclusion.score_breakdown || {}).threshold ?? "-"))}</div></td>
          <td>${esc(categoryText(conclusion))}</td>
          <td>${(conclusion.matched_items || []).map((x) => `<div>${esc(x.code)}</div>`).join("") || "-"}</td>
          <td>${(conclusion.missing_items || []).map((x) => `<div>${esc(x.code)}</div>`).join("") || "-"}</td>
          <td><div>${esc(item.condition_text || "-")}</div><div class="mono muted">${esc(item.graph_filename || "-")}</div></td>
        </tr>`;
    });
    mount.innerHTML = `
      <div class="result-banner ${result.passed ? "ok" : "warning"}">
        ${result.passed ? "用例通过" : "用例未通过"}：${checks.filter((item) => item.passed).length}/${checks.length} 项断言通过；候选证候 ${candidates.length} 条。
      </div>
      ${panel("预期与实际", table(["断言", "预期", "实际", "原因"], checkRows), "", "断言覆盖首选证候、最低评分、代表方剂和禁止触发证候")}
      ${panel("候选证候排序", table(["证候", "加权评分", "分类覆盖", "命中证据", "缺失证据", "规则与图谱"], candidateRows), "", "候选包含 active/testing/draft 规则；缺失表示病历未采集，未采集证据不进入评分分子")}
      ${dependencies.length ? panel("测试依赖", table(["规则", "类型", "状态", "图谱", "指南溯源"], dependencies.map((item) => `
        <tr>
          <td>${esc(item.rule_name || item.rule_id)}<div class="mono muted">${esc(item.rule_id)}</div></td>
          <td class="mono">${esc(item.rule_type)}</td>
          <td>${badge(item.status)}</td>
          <td class="mono">${esc(item.graph_filename || "-")}</td>
          <td class="mono">${esc((item.source || {}).guideline_title || "-")}</td>
        </tr>`))) : ""}
    `;
  }

  function renderInferenceResult() {
    const result = state.inferenceResult; const box = $("#inferResult"); if (!box || !result) return;
    const diagnosisList = result.diagnosis_conclusions || result.conclusions.filter((c) => c.rule_type !== "treatment");
    const treatmentList = result.treatment_conclusions || result.conclusions.filter((c) => c.rule_type === "treatment");
    const diagnostics = result.diagnostics || {};
    const diagRows = [
      ["诊断规则", `启用 ${diagnostics.active_diagnosis_rules ?? "-"} / 草稿 ${diagnostics.draft_diagnosis_rules ?? "-"}`],
      ["治疗规则", `启用 ${diagnostics.active_treatment_rules ?? "-"} / 草稿 ${diagnostics.draft_treatment_rules ?? "-"}`],
      ["推理图谱", (diagnostics.graph_files || result.graph_files || []).join("、") || "未加载"],
    ];
    const reasons = diagnostics.reasons || [];
    const diagHtml = panel("规则诊断", table(["项目", "状态"], diagRows.map((row) => `<tr><td>${esc(row[0])}</td><td>${esc(row[1])}</td></tr>`)), `<button class="btn sm" data-quick="rules">去规则管理</button>`, reasons.length ? reasons.join("；") : "规则已加载") + (reasons.length ? `<div class="result-banner warning"><strong>未产生推荐的原因</strong><ul class="issue-list">${reasons.map((reason) => `<li>${esc(reason)}</li>`).join("")}</ul></div>` : "");
    const dxHtml = diagnosisList.length ? `<div class="entity-grid">${diagnosisList.map((c) => `
      <article class="evidence-card"><strong>${esc(c.disease_name)} / ${esc(c.disease_code)}</strong><div class="muted">${badge(c.confidence)} ${esc(c.rule_id)} 优先级 ${c.priority}</div><p>${esc(c.message)}</p>${c.suggested_action ? `<div class="mono">动作：${esc(c.suggested_action)}</div>` : ""}${c.guideline_source ? `<div class="trace">来源：${esc(c.guideline_source)}</div>` : ""}
      <div style="margin-top:10px">${c.evidence.map((e) => `<div class="evidence-card"><strong>${esc(e.description)}</strong><div class="trace">${esc(e.patient_value_display || "")}</div>${e.standard_used ? `<div class="trace">标准：${esc(e.standard_used)}</div>` : ""}${e.source_ref ? `<div class="trace">溯源：${esc(e.source_ref)}</div>` : ""}</div>`).join("")}</div></article>`).join("")}</div>` : '<div class="empty">当前患者数据未命中诊断规则</div>';
    const txHtml = treatmentList.length ? `<div class="entity-grid">${treatmentList.map((c) => `
      <article class="evidence-card"><strong>治疗方案：${esc(c.treatment_name || c.treatment_plan_id || "")}</strong><div class="muted">${badge(c.confidence)} ${esc(c.rule_id)}${c.priority_label ? ` · ${esc(c.priority_label)}` : ""}</div>${c.treatment_plan_id ? `<div class="mono">方案 ID：${esc(c.treatment_plan_id)}</div>` : ""}<p>${esc(c.message)}</p>${(c.treatment_items || []).length ? `<div class="trace">方案组成：${c.treatment_items.map((item) => `${esc(item.name || item.code)}（${esc(item.code)}）`).join("、")}</div>` : ""}${c.suggested_action ? `<div class="mono">动作：${esc(c.suggested_action)}</div>` : ""}${c.triggered_by && c.triggered_by.length ? `<div class="trace">由诊断触发：${esc(c.triggered_by.join("、"))}</div>` : ""}${c.guideline_source ? `<div class="trace">来源：${esc(c.guideline_source)}</div>` : ""}
      <div style="margin-top:10px">${c.evidence.map((e) => `<div class="evidence-card"><strong>${esc(e.description)}</strong><div class="trace">${esc(e.patient_value_display || "")}</div></div>`).join("")}</div></article>`).join("")}</div>` : '<div class="empty">无治疗方案推荐</div>';
    box.innerHTML = diagHtml + panel(`疑似诊断推荐：${diagnosisList.length} 条`, dxHtml) + panel(`治疗方案推荐（候选草稿）：${treatmentList.length} 条`, txHtml);
  }

  async function runSmokeTest() {
    const checks = [];
    const add = async (name, fn) => { try { const value = await fn(); checks.push([name, typeof value === "string" ? value : "通过", true]); } catch (e) { checks.push([name, e.message, false]); } };
    toast("正在执行页面自检...");
    await add("健康检查", async () => (await api("/api/health")).status === "ok" ? "通过" : "失败");
    await add("图谱统计", async () => `疾病 ${(await api("/api/kg/stats")).diseases}`);
    await add("规则加载", async () => `${(await api("/api/rules")).length} 条`);
    await add("概念词汇", async () => `${(await api("/api/vocab")).length} 个`);
    await add("医院标准", async () => `${(await api("/api/standards/hospitals")).length} 家`);
    await add("示例患者", async () => (await api("/api/inference/demo-patient")).patient_id);
    await add("推理执行", async () => {
      const patient = await api("/api/inference/demo-patient");
      return `命中 ${(await api("/api/inference/run", { json: { patient } })).conclusion_count} 条`;
    });
    content.innerHTML = panel("页面自检结果", table(["检查项", "结果", "状态"], checks.map(([name, detail, ok]) => `<tr><td>${esc(name)}</td><td>${esc(detail)}</td><td>${ok ? '<span class="badge ok">通过</span>' : '<span class="badge danger" data-smoke-failure="1">失败</span>'}</td></tr>`))) + content.innerHTML;
    toast(checks.some((c) => !c[2]) ? "自检发现失败项" : "自检全部通过", checks.some((c) => !c[2]));
  }

  document.querySelectorAll(".nav-item").forEach((button) => button.onclick = () => navigate(button.dataset.route));
  $("#refreshBtn").onclick = () => render();
  $("#smokeTestBtn").onclick = () => { state.route = "dashboard"; document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.route === "dashboard")); runSmokeTest(); };
  content.addEventListener("click", (event) => {
    const vocabDetail = event.target.closest("[data-vocab-detail]");
    if (vocabDetail) {
      state.route = "vocab";
      state.vocabSearch = vocabDetail.dataset.vocabDetail;
      document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.route === "vocab"));
      const [title, desc] = titles.vocab;
      $("#pageTitle").textContent = title;
      $("#pageDesc").textContent = desc;
      render();
      return;
    }
    const quick = event.target.closest("[data-quick]"); if (!quick) return;
    navigate(quick.dataset.quick);
  });
  content.addEventListener("click", handleCaseClick);
  // 审核台键盘导航：J/K 上下选择，X 勾选，A 通过，R 拒绝（输入框和弹窗打开时忽略）
  document.addEventListener("keydown", (event) => {
    if (state.route !== "review") return;
    if ($("#editorDialog")?.open) return;
    const tag = (event.target.tagName || "").toLowerCase();
    if (["input", "textarea", "select"].includes(tag) || event.target.isContentEditable) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const items = state.reviewItems || [];
    if (!items.length) return;
    const currentIndex = items.findIndex((item) => reviewItemKey(item) === state.reviewSelectedKey);
    if (event.key === "j" || event.key === "J" || event.key === "ArrowDown") {
      event.preventDefault();
      loadReviewDetail(items[Math.min(items.length - 1, (currentIndex < 0 ? -1 : currentIndex) + 1)]);
    } else if (event.key === "k" || event.key === "K" || event.key === "ArrowUp") {
      event.preventDefault();
      loadReviewDetail(items[Math.max(0, (currentIndex < 0 ? 1 : currentIndex) - 1)]);
    } else if (currentIndex < 0) {
      return;
    } else if (event.key === "x" || event.key === "X") {
      const input = content.querySelector(`[data-review-select="${CSS.escape(reviewItemKey(items[currentIndex]))}"]`);
      if (input) {
        input.checked = !input.checked;
        input.dispatchEvent(new Event("change"));
      }
    } else if (event.key === "a" || event.key === "A") {
      quickReview(items[currentIndex], "approve");
    } else if (event.key === "r" || event.key === "R") {
      openReasonDialog(items[currentIndex], "reject");
    }
  });
  (async function init() {
    try { await api("/api/health"); $("#apiStatus").textContent = "服务已连接"; $(".status-dot").classList.add("online"); }
    catch (_) { $("#apiStatus").textContent = "服务不可用"; $(".status-dot").classList.add("offline"); }
    navigate("dashboard");
  })();
})();

