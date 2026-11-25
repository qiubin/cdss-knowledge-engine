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
    importHistory: { page: 1, page_size: 10 },
    graph: null,
    graphs: [],
    graphFilename: "KG-GENERIC_GUIDELINE_V1.json",
    tcmGraphFilename: "",
    graphFilter: "",
    rules: [],
    ruleSync: null,
    ruleDomain: "",
    ruleDisease: "",
    ruleClinicalIntent: "",
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
    reviewFilters: { graph: "", sourceDoc: "", nodeType: "", relationCode: "", itemType: "", clinicalIntent: "" },
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

  // ==== Keep-alive 页面容器 ====
  // 每个路由一个常驻 DOM 容器：首次访问挂载，之后切换只做挂载/摘除，
  // 保留筛选词、滚动位置、输入草稿与图谱视口。摘除的节点缓存在
  // pageRegistry 中（脱离文档但保留监听器与输入状态），因此所有
  // document 级查询天然只命中当前活动页，不会跨页串扰。
  const pageRegistry = new Map();   // route -> { el, loading }
  const scrollMem = {};             // route -> 离开时 .content 的 scrollTop
  const stalePages = new Set();     // 数据已变更、需要重挂载的路由
  let pageEl = null;                // 当前正在渲染/交互的页面容器（renderXxx 的写入目标）
  const pageRenderers = {
    dashboard: renderDashboard, import: renderImport, packages: renderGuidePackages,
    templates: renderTemplates, entityVocab: renderEntityVocab, vocab: renderVocab,
    graph: renderGraph, graphVisual: renderGraphVisual, graphClear: renderGraphClear,
    tcmGraph: renderTcmGraph, rules: renderRules, review: renderReviewCenter,
    standards: renderStandards, tcmInference: renderTcmInference, inference: renderInference,
  };

  async function navigate(route, { reload = false } = {}) {
    if (!pageRenderers[route]) route = "dashboard";
    const prev = state.route;
    if (prev && prev !== route) scrollMem[prev] = content.scrollTop;
    state.route = route;
    document.querySelectorAll(".nav-item").forEach((b) => {
      b.classList.toggle("active", b.dataset.route === route);
      b.classList.toggle("opened", openedTabs.includes(b.dataset.route));
    });
    const [title, desc] = titles[route] || ["概览", ""];
    $("#pageTitle").textContent = title;
    $("#pageDesc").textContent = desc;
    if (!openedTabs.includes(route)) openedTabs.push(route);
    renderTabs();
    if (location.hash !== `#${route}`) location.hash = route;
    if (stalePages.has(route)) reload = true;
    let page = pageRegistry.get(route);
    if (page && reload) {
      page.el.remove();
      pageRegistry.delete(route);
      stalePages.delete(route);
      page = null;
    }
    if (!page) {
      const el = document.createElement("div");
      el.className = "page";
      el.dataset.page = route;
      el.innerHTML = '<div class="empty">加载中...</div>';
      page = { el, loading: null };
      pageRegistry.set(route, page);
      content.replaceChildren(el);
      pageEl = el;
      stalePages.delete(route);
      page.loading = mountPage(route, el);
      await page.loading;
      page.loading = null;
      content.scrollTop = 0;
      return;
    }
    content.replaceChildren(page.el);
    pageEl = page.el;
    if (page.loading) { await page.loading; return; } // 首次挂载进行中：等它完成
    content.scrollTop = scrollMem[route] || 0;
  }

  // 强制重渲染当前页（原 render() 的语义）
  function reload() { return navigate(state.route, { reload: true }); }
  // 数据变更后标记受影响页面失效：已挂载的页在下次进入时重挂载；
  // 当前活动页不在此处理，由调用方自行 reload() 或内部重渲染
  function markStale(...routes) {
    routes.forEach((route) => {
      if (!pageRenderers[route] || route === state.route) return;
      if (pageRegistry.has(route)) stalePages.add(route);
    });
  }

  // ==== 页签栏 ====
  // 点菜单 = 打开页签（页面常驻），点 × 关闭并销毁该页；概览为常驻页签。
  const openedTabs = ["dashboard"];           // 页签打开顺序
  const pinnedTabs = new Set(["dashboard"]);  // 不可关闭的常驻页签

  function renderTabs() {
    const bar = $("#pagetabs");
    if (!bar) return;
    bar.replaceChildren(...openedTabs.map((route) => {
      const tab = document.createElement("div");
      tab.className = "pagetab" + (route === state.route ? " active" : "");
      tab.dataset.route = route;
      tab.title = (titles[route] || [route])[1] || "";
      tab.onclick = () => navigate(route);
      const label = document.createElement("span");
      label.className = "pagetab-label";
      label.textContent = (titles[route] || [route])[0];
      tab.appendChild(label);
      if (!pinnedTabs.has(route)) {
        const close = document.createElement("button");
        close.className = "pagetab-close";
        close.type = "button";
        close.title = `关闭「${(titles[route] || [route])[0]}」页签`;
        close.setAttribute("aria-label", `关闭「${(titles[route] || [route])[0]}」页签`);
        close.textContent = "×";
        close.onclick = (event) => { event.stopPropagation(); closeTab(route); };
        tab.appendChild(close);
      }
      return tab;
    }));
  }

  function closeTab(route) {
    if (pinnedTabs.has(route)) return;
    const index = openedTabs.indexOf(route);
    if (index < 0) return;
    openedTabs.splice(index, 1);
    delete scrollMem[route];
    const page = pageRegistry.get(route);
    if (page) {
      page.el.remove();
      pageRegistry.delete(route);
      stalePages.delete(route);
    }
    if (state.route === route) {
      // 关闭当前页签：激活相邻（优先左侧）页签
      const next = openedTabs[Math.max(0, index - 1)] || openedTabs[0] || "dashboard";
      navigate(next);
    } else {
      renderTabs();
    }
  }

  const titles = {
    dashboard: ["概览", "知识资产、流程状态与快速验证入口"],
    import: ["指南导入", "上传并解析指南，审核候选内容后生成知识包"],
    packages: ["知识包", "审核预检结果，一次性导入图谱和草稿规则；规则仍需人工启用"],
    templates: ["解析模板", "Schema 层：管理本地与大模型共用的抽取契约（实体类型、关系、属性）"],
    entityVocab: ["实体词典", "术语层：共享实体字典，具体临床实体与别名跨模板复用"],
    vocab: ["概念词汇", "管理规则生成与推理判断使用的抽象语义"],
    graph: ["知识图谱", "管理疾病、诊断证据和指南出处"],
    graphVisual: ["图谱可视", "选择知识图谱，查看实体、关系和指南溯源"],
    graphClear: ["图谱清除", "清空所选图谱内容及关联规则，保留指南原文与解析资料"],
    tcmGraph: ["中医图谱", "证候为中心的辨证、治法与方药知识图谱"],
    rules: ["规则管理", "规则使用抽象证据概念，不保存医院阈值"],
    review: ["审核台", "跨图谱与规则的待办、阻断、待审核与审核记录"],
    standards: ["标准管理", "按医院维护检查与检验的判定方式、适用范围和来源"],
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
    retired: "已失效", stale: "待审核", blocked: "已阻断", testing: "测试中",
    approved: "预审通过",
  };
  const CLINICAL_INTENT_LABELS = {
    suspected_diagnosis: "疑似诊断", diagnosis_confirmation: "确诊判断",
    differential_diagnosis: "鉴别诊断", diagnosis_exclusion: "排除诊断",
    risk_stratification: "风险分层", severity_assessment: "严重程度/分期评估",
    treatment_recommendation: "治疗方案推荐", treatment_selection: "方案选择/调整",
    contraindication_check: "禁忌与慎用判断", dose_adjustment: "剂量调整",
    monitoring_plan: "监测计划", follow_up_plan: "随访与复查计划",
    syndrome_identification: "辨证判断",
  };
  const CLINICAL_INTENT_OPTIONS = Object.entries(CLINICAL_INTENT_LABELS);
  function defaultClinicalIntent(ruleType) {
    if (ruleType === "diagnosis") return "diagnosis_confirmation";
    if (ruleType === "tcm_pattern") return "syndrome_identification";
    if (["treatment", "tcm_treatment", "tcm_prescription"].includes(ruleType)) return "treatment_recommendation";
    if (["contraindication", "tcm_contraindication"].includes(ruleType)) return "contraindication_check";
    if (ruleType === "monitoring") return "monitoring_plan";
    return "diagnosis_confirmation";
  }
  const clinicalIntentLabel = (value, ruleType = "diagnosis") => (
    CLINICAL_INTENT_LABELS[value] || CLINICAL_INTENT_LABELS[defaultClinicalIntent(ruleType)]
  );
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
  // 实体类型中文名的唯一前端来源；新增实体类型时必须同步维护，
  // tests/js/type_name_parity.test.js 会校验它与模板类型全集、后端
  // precheck.NODE_TYPE_LABELS 双向一致（concept/exam_finding 为遗留类型豁免）。
  const typeName = {
    clinical_rule: "临床判据与方案",
    disease: "疾病", syndrome: "证候", symptom: "症状", sign: "体征",
    lab: "检验", exam: "检查", exam_finding: "检查发现", diagnostic_evidence: "诊断依据",
    treatment_plan: "治疗方案", medication: "药品", intervention: "干预操作",
    contraindication: "禁忌", monitoring_requirement: "监测要求",
    care_pathway: "临床路径", pathway_phase: "路径阶段", order_set: "医嘱组", order_item: "医嘱项",
    treatment_method: "治法", formula: "方剂", herb: "中药", external_therapy: "外治疗法",
    care_guidance: "调护", tongue: "舌象", pulse: "脉象", pathogenesis: "病机",
    syndrome_element: "证素", acupoint: "穴位", meridian: "经络", constitution: "体质",
    etiology: "病因", western_disease: "西医疾病", risk_factor: "危险因素", stage: "分期",
    pathology: "病理", histology: "组织学", biomarker: "生物标志物", molecular_test: "分子检测",
    concept: "抽象概念",
  };

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

  // 页面首次挂载入口：renderXxx 写入自己的页面容器 pageEl（而非全局 content）
  async function mountPage(route, el) {
    pageEl = el;
    try { await pageRenderers[route](); }
    catch (error) { el.innerHTML = `<div class="result-banner error">${esc(error.message)}</div>`; }
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
    const [{ rules }, docs, graphs, standardOverview, ruleSync] = await Promise.all([
      loadBase(),
      api("/api/pdf/documents"),
      api("/api/kg/graphs"),
      api("/api/standards/all"),
      api("/api/rules/sync-check"),
    ]);
    if (state.route !== "dashboard") return;
    state.graphs = graphs.filter((item) => normalizeDomainClient(item.domain) === "western");
    state.rules = rules;
    state.ruleSync = ruleSync;
    const selected = graphs.find((item) => item.graph_filename === (state.dashboardGraphFilename || state.graphFilename)) ||
      graphs.find((item) => item.semantic_nodes > 0) || graphs[0];
    const graphFilename = selected?.graph_filename || "";
    state.dashboardGraphFilename = graphFilename;
    const query = `graph_filename=${encodeURIComponent(graphFilename)}`;
    const [rawGraph, reviewQueue] = graphFilename ? await Promise.all([
      api(`/api/kg/graph?${query}`),
      api(`/api/review/queue?${query}&limit=1`).catch(() => ({summary: {}})),
    ]) : [{}, {summary: {}}];
    if (state.route !== "dashboard" || state.dashboardGraphFilename !== graphFilename) return;
    const graph = approvedKnowledgeGraph(rawGraph);
    const tcm = normalizeDomainClient(selected?.domain || graph.domain) === "tcm";
    if (!tcm) state.graph = rawGraph;
    const selectedRules = rules.filter((rule) => (rule.graph_filename || "KG-GENERIC_GUIDELINE_V1.json") === graphFilename);
    const activeRules = selectedRules.filter((rule) => rule.status === "active");
    const reviewSummary = reviewQueue.summary || {};
    const sumStatuses = (counter, statuses) => statuses.reduce((sum, s) => sum + (counter?.[s] || 0), 0);
    const pendingReview =
      sumStatuses(reviewSummary.graph_nodes, ["draft", "stale"])
      + sumStatuses(reviewSummary.graph_relations, ["draft", "stale"])
      + sumStatuses(reviewSummary.rules, ["draft", "testing", "blocked"]);
    const count = (...types) => graph.nodes.filter((node) => types.includes(node.node_type)).length;
    const graphMetrics = tcm ? [
      ["中医疾病", count("disease")],
      ["证候", count("syndrome")],
      ["辨证观察", count("symptom", "tongue", "pulse", "sign"), `${count("symptom")} 症见 / ${count("tongue")} 舌象 / ${count("pulse")} 脉象 / ${count("sign")} 体征`],
      ["病机", count("pathogenesis")],
      ["治法、方剂与中药", count("treatment_method", "formula", "herb"), `${count("treatment_method")} 治法 / ${count("formula")} 方剂 / ${count("herb")} 中药`],
      ["语义关系", graph.relations.length],
    ] : [
      ["疾病节点", count("disease", "western_disease")],
      ["诊断证据", count("diagnostic_evidence"), `${graph.relations.filter((edge) => (edge.relation_code || "").startsWith("DISEASE_HAS")).length} 条已通过疾病关联`],
      ["治疗方案", count("treatment_plan"), `${graph.relations.filter((edge) => edge.relation_code === "DISEASE_HAS_TREATMENT_PLAN").length} 条已通过疾病关联`],
      ["药物与措施", count("medication", "intervention"), `${count("medication")} 药物 / ${count("intervention")} 措施`],
    ];
    const steps = [
      ["上传指南", `${docs.length} 份文档`, docs.length ? "done" : "pending"],
      ["知识包生成", "候选图谱与规则", "pending"],
      ["人工导入", "草稿进入图谱与规则", "pending"],
      ["图谱入库", `${graph.nodes.length} 个实体已通过审核`, graph.nodes.length ? "done" : "pending"],
      ["规则配置", `${activeRules.length} 条已启用`, activeRules.length ? "done" : "pending"],
      ["医院标准", `${standardOverview.active_count} 条已启用`, standardOverview.active_count ? "done" : "pending"],
    ];
    pageEl.innerHTML = `
      ${panel("医生诊疗决策说明", '<p>查看医生诊疗决策顺序、指南到规则的实现流程，以及推荐结果的含义。</p><button class="btn primary" id="clinicalDecisionHelp">查看诊疗决策说明</button>')}
      ${panel("知识图谱上下文", `
        <div class="inline-controls">
          <div class="field"><label for="dashboardGraph">当前图谱</label><select id="dashboardGraph" ${graphFilename ? "" : "disabled"}>${knowledgeGraphOptions(graphs, graphFilename) || '<option>暂无知识图谱</option>'}</select></div>
        </div>
      `, "", "统计口径：当前图谱中已通过审核（active）的数据；规则执行仍按规则自身配置的图谱加载")}
      <div class="grid c4">
        ${graphMetrics.map(([label, value, detail]) => `<div class="kpi"><span>${esc(label)}</span><strong>${value}</strong>${detail ? `<small>${esc(detail)}</small>` : ""}</div>`).join("")}
        <div class="kpi"><span>推理规则</span><strong>${activeRules.length}</strong><small>已通过审核并启用 · 共 ${selectedRules.length} 条</small></div>
        <div class="kpi"><span>医院标准</span><strong>${standardOverview.active_count}</strong><small>已启用 · ${standardOverview.hospital_count} 家机构</small></div>
      </div>
      ${panel("全流程状态", `<div class="pipeline">${steps.map((s) => `<div class="step ${s[2]}"><strong>${s[0]}</strong><span>${s[1]}</span></div>`).join("")}</div>`)}
      ${panel("快速操作", `
        <div class="actions">
          <button class="btn primary" data-quick="import">上传 PDF 指南</button>
          <button class="btn ${pendingReview ? "primary" : ""}" data-quick="review">进入审核台${pendingReview ? `（${pendingReview} 条待办）` : ""}</button>
          <button class="btn" data-quick="packages">管理知识包</button>
          <button class="btn" id="dashboardOpenGraph" ${graphFilename ? "" : "disabled"}>查看${tcm ? "中医" : ""}知识图谱</button>
          <button class="btn" data-quick="standards">维护医院标准</button>
          <button class="btn" data-quick="inference">病历测试</button>
          <button class="btn" data-quick="tcmInference">中医辨证</button>
        </div>`, "", "按 引入 → 审核 → 验证 的主线组织；示例患者已内置在病历测试页")}
      ${panel("已上传文档", table(["文档", "ID", "页数", "实体", "上传时间"], docs.map((d) => `<tr><td>${esc(d.doc_title)}</td><td class="mono">${esc(d.doc_id)}</td><td>${d.total_pages}</td><td>${d.entity_count}</td><td class="nowrap">${esc(formatTime(d.uploaded_at))}</td></tr>`)))}
    `;
    $("#clinicalDecisionHelp").onclick = () => window.WesternDecision.showExplanation();
    $("#dashboardGraph").onchange = (event) => {
      state.dashboardGraphFilename = event.target.value;
      renderDashboard().catch((error) => toast(error.message, true));
    };
    $("#dashboardOpenGraph").onclick = () => {
      if (tcm) state.tcmGraphFilename = graphFilename;
      else state.graphFilename = graphFilename;
      navigate(tcm ? "tcmGraph" : "graph", {reload: true});
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

  function refreshUploadGraphOptions(suffix) {
    const sel = $(`#uploadGraph${suffix}`);
    if (!sel) return;
    const template = state.templates.find((t) => t.template_id === $(`#uploadTemplate${suffix}`)?.value);
    const domain = template ? normalizeDomainClient(template.domain) : "";
    const graphs = (state.uploadGraphs || []).filter((g) => !domain || normalizeDomainClient(g.domain) === domain);
    const choice = state.uploadGraphChoice?.[suffix] || "follow";
    sel.innerHTML = [
      `<option value="follow">跟随模板（${esc(templateGraphFilename(template || {}))}）</option>`,
      ...graphs.map((g) => `<option value="${esc(g.graph_filename)}">${esc(g.graph_filename)}</option>`),
      `<option value="__new__">＋ 新建图谱…</option>`,
    ].join("");
    sel.value = choice === "follow" || choice === "__new__" || graphs.some((g) => g.graph_filename === choice) ? choice : "follow";
    $(`#uploadGraphNew${suffix}`).hidden = sel.value !== "__new__";
  }

  // 中西医导入彻底分开：各自独立的模板、目的图谱与上传区，互不混选。
  const IMPORT_PANELS = [
    { key: "western", suffix: "West", title: "西医指南导入", label: "西医",
      defaultTemplate: "GENERIC_GUIDELINE_V1",
      hint: "统一走两阶段临床陈述流水线（实体关系产出最全，开放术语与 LOCAL 码）；不做领域自动识别，写入图谱严格跟随本面板选择" },
    { key: "tcm", suffix: "Tcm", title: "中医指南导入", label: "中医",
      defaultTemplate: "TCM_GUIDELINE_V3",
      hint: "使用中医专用模板（病-证-法-方药结构）；辨病辨证结果与西医图谱分开维护，可通过中西医对照关联参考" },
  ];

  function importPanelHtml(cfg) {
    const templates = state.templates.filter((t) => normalizeDomainClient(t.domain) === cfg.key);
    if (!templates.length) return "";
    return panel(cfg.title, `
      <div class="inline-controls">
        <div class="field"><label>抽取模板（${cfg.label}）</label><select id="uploadTemplate${cfg.suffix}">${templates.map((t) => `<option value="${esc(t.template_id)}">${esc(t.name)}${t.template_id === cfg.defaultTemplate ? "（推荐）" : ""} / ${esc(t.template_id)}</option>`).join("")}</select></div>
        <div class="field"><label>目的图谱</label><div><select id="uploadGraph${cfg.suffix}"></select><input id="uploadGraphNew${cfg.suffix}" placeholder="新图谱文件名，如 KG-RESPIRATORY_V1.json" hidden></div></div>
      </div>
      <div id="dropzone${cfg.suffix}" class="dropzone"><strong>点击或拖拽 ${cfg.label}指南 PDF / Word (.docx) 到此处</strong><p class="muted">严格按本面板所选模板解析，生成实体、关系、知识包和候选规则，写入所选目的图谱</p></div>
      <input id="pdfInput${cfg.suffix}" type="file" accept=".pdf,.docx" hidden>
    `, "", cfg.hint);
  }

  function wireImportPanel(cfg) {
    const drop = $(`#dropzone${cfg.suffix}`), input = $(`#pdfInput${cfg.suffix}`);
    if (!drop) return;
    const templateSel = $(`#uploadTemplate${cfg.suffix}`);
    templateSel.value = state.templates.find((t) => t.template_id === cfg.defaultTemplate && normalizeDomainClient(t.domain) === cfg.key)?.template_id
      || templateSel.querySelector("option")?.value || "";
    templateSel.onchange = () => { state.uploadGraphChoice[cfg.suffix] = "follow"; refreshUploadGraphOptions(cfg.suffix); };
    $(`#uploadGraph${cfg.suffix}`).onchange = (e) => {
      state.uploadGraphChoice[cfg.suffix] = e.target.value;
      $(`#uploadGraphNew${cfg.suffix}`).hidden = e.target.value !== "__new__";
    };
    drop.onclick = () => input.click();
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("drag"); };
    drop.ondragleave = () => drop.classList.remove("drag");
    drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("drag"); if (e.dataTransfer.files[0]) prepareAutoUpload(e.dataTransfer.files[0], cfg); };
    input.onchange = () => input.files[0] && prepareAutoUpload(input.files[0], cfg);
  }

  async function renderImport() {
    await loadBase();
    await loadExtractionSettings();
    if (!state.doc) {
      const docs = await api("/api/pdf/documents");
      if (docs.length) state.doc = await api(`/api/pdf/${docs[0].doc_id}/result`);
    }
    const doc = state.doc;
    state.uploadGraphChoice ||= {};
    pageEl.innerHTML = `
      <div id="pendingUploadPanel"></div>
      <div id="importJobPanel"></div>
      ${IMPORT_PANELS.map(importPanelHtml).join("")}
      ${state.autoResult ? `
        <div id="autoResultPanel" class="result-banner ok">
          <strong>自动生成并已入库：${esc(state.autoResult.doc_title || state.autoResult.doc_id)}</strong>
          <div>实体 ${state.autoResult.entity_count} / 关系 ${state.autoResult.relation_count} / 待审诊疗依据 ${state.autoResult.rule_count}/ LOCAL 术语 ${state.autoResult.local_term_count}</div>
          ${extractionCoverageHtml(state.autoResult.coverage || state.autoResult.metadata?.coverage)}
          <div class="muted">图谱和待审诊疗依据已写入；请在审核台依次审核实体、关系和诊疗依据，通过后生成规则草稿。</div>
          <div class="mono">图谱：${esc(state.autoResult.graph_filename)} · 知识包：${esc(state.autoResult.package_id)}</div>
          <div class="actions"><button id="autoOpenPackage" class="btn sm primary">查看知识包</button><button id="autoOpenGraph" class="btn sm">查看图谱</button><button id="autoOpenRules" class="btn sm">审核诊疗依据</button></div>
        </div>` : ""}
      <div id="importResult"></div>
      ${panel("导入历史", '<div id="importHistoryBody" class="muted">加载中…</div>', "", "记录每次上传（含失败留痕）的成功与失败；新文档使用新编号，不会覆盖历史记录")}
    `;
    $("#autoOpenPackage")?.addEventListener("click", () => {
      state.guidePackageId = state.autoResult.package_id;
      navigate("packages", { reload: true });
    });
    $("#autoOpenGraph")?.addEventListener("click", () => {
      state.graphFilename = state.autoResult.graph_filename;
      navigate("graph", { reload: true });
    });
    $("#autoOpenRules")?.addEventListener("click", () => { state.reviewTab = "bases"; state.reviewFilters.graph = state.autoResult.graph_filename; navigate("review", { reload: true }); });
    IMPORT_PANELS.forEach(wireImportPanel);
    api("/api/kg/graphs").then((graphs) => {
      state.uploadGraphs = graphs;
      IMPORT_PANELS.forEach((cfg) => refreshUploadGraphOptions(cfg.suffix));
    }).catch(() => { state.uploadGraphs = []; });
    if (activeImportJob()) setImportBusy(true); else setImportBusy(false);
    renderImportJob();
    if (doc) renderImportResult();
    loadImportHistory();
  }

  // 一次导入会产生 started/success/failed 等多条事件，按 job_id 归并为一条历史，
  // 展示最终状态（success/failed/stopped 优先，否则取最新事件）。
  // 早期留痕没有 job_id，但每次上传都会生成新的 doc_id，按 doc_id 归并同样对应一次尝试；
  // 不能用 doc_id-ts 兜底——各事件的写入时间不同，会把同一次尝试拆成多行，且该组合键
  // 无法通过后端的删除标识校验（曾导致“导入任务标识无效”）。
  function groupImportLogs(entries) {
    const byJob = new Map();
    for (const entry of entries) {
      const key = entry.job_id || entry.doc_id;
      if (!byJob.has(key)) byJob.set(key, []);
      byJob.get(key).push(entry);
    }
    return [...byJob.entries()].map(([key, events]) => {
      const sorted = [...events].sort((a, b) => (a.ts || "").localeCompare(b.ts || ""));
      const last = sorted[sorted.length - 1];
      const display = sorted.find((e) => e.status === "failed" || e.status === "stopped")
        || sorted.find((e) => e.status === "success") || last;
      return {
        job_id: last.job_id || key,
        doc_id: last.doc_id || "",
        doc_title: last.doc_title || display.doc_title || "",
        mode: last.mode || display.mode || "",
        domain: sorted.map((e) => e.domain).find(Boolean) || "",
        display,
      };
    });
  }

  async function loadImportHistory() {
    const body = $("#importHistoryBody");
    if (!body) return;
    state.importHistory ||= { page: 1, page_size: 10 };
    try {
      const data = await api(`/api/pdf/import-logs?page=${state.importHistory.page}&page_size=${state.importHistory.page_size}`);
      const totalPages = Math.max(1, Math.ceil(data.total / data.page_size));
      state.importHistory.page = Math.min(Math.max(1, state.importHistory.page), totalPages);
      const attempts = groupImportLogs(data.items);
      body.className = "";
      if (!attempts.length) {
        body.innerHTML = '<div class="empty">暂无导入记录；每次上传（含失败）都会在这里留痕。</div>';
        return;
      }
      body.innerHTML = `
        ${table(["时间", "文档", "领域", "方式", "结果", "数量", "失败原因", "操作"], attempts.map((attempt) => `<tr>
          <td class="nowrap mono">${esc(formatTime(attempt.display.ts))}</td>
          <td><a href="#" data-import-log-open="${esc(attempt.doc_id)}"><strong>${esc(attempt.doc_title || "-")}</strong></a><div class="mono muted">${esc(attempt.doc_id || "")}</div></td>
          <td>${attempt.domain === "tcm" ? "中医" : attempt.domain === "western" ? "西医" : "-"}</td>
          <td>${attempt.mode === "auto" ? "按模板导入" : attempt.mode === "llm" ? "大模型" : attempt.mode === "local" ? "本地模板" : esc(attempt.mode || "-")}</td>
          <td>${importLogStatusBadge(attempt.display.status)}</td>
          <td class="muted">${attempt.display.status === "success" ? `${attempt.display.entity_count ?? "-"} 实体 / ${attempt.display.relation_count ?? "-"} 关系 / ${attempt.display.rule_count ?? "-"} 规则` : "-"}</td>
          <td>${esc(attempt.display.error || "-")}</td>
          <td><div class="actions"><button class="btn sm" data-import-log-open="${esc(attempt.doc_id)}">查看</button>${attempt.job_id ? `<button class="btn sm danger" data-import-log-delete="${esc(attempt.job_id)}">删除</button>` : '<span class="muted">无法删除</span>'}</div></td>
        </tr>`))}
        <div class="inline-controls muted">
          <span>共 ${data.total} 条 · 第 ${state.importHistory.page} / ${totalPages} 页</span>
          <button id="importHistoryPrev" class="btn sm" ${state.importHistory.page <= 1 ? "disabled" : ""}>上一页</button>
          <button id="importHistoryNext" class="btn sm" ${state.importHistory.page >= totalPages ? "disabled" : ""}>下一页</button>
        </div>`;
      const openDoc = async (docId) => {
        if (!docId) return;
        try {
          state.doc = await api(`/api/pdf/${encodeURIComponent(docId)}/result`);
          renderImportResult();
          $("#importResult")?.scrollIntoView({ behavior: "smooth", block: "start" });
        } catch (error) { toast(error.message, true); }
      };
      body.querySelectorAll("[data-import-log-open]").forEach((el) => el.onclick = (event) => {
        event.preventDefault();
        openDoc(el.dataset.importLogOpen);
      });
      body.querySelectorAll("[data-import-log-delete]").forEach((btn) => btn.onclick = async () => {
        if (!confirm("删除该次导入的历史记录，并联动删除对应的已上传文档？已生成的知识包与图谱不受影响。")) return;
        btn.disabled = true;
        try {
          const result = await api(`/api/pdf/import-logs/${encodeURIComponent(btn.dataset.importLogDelete)}`, { method: "DELETE" });
          toast(`已删除导入记录${result.deleted_doc_ids?.length ? `及对应文档 ${result.deleted_doc_ids.join("、")}` : ""}`);
          if (result.deleted_doc_ids?.includes(state.doc?.doc_id)) { state.doc = null; }
          await loadImportHistory();
          if (!state.doc) renderImport();
        } catch (error) { toast(error.message, true); btn.disabled = false; }
      });
      $("#importHistoryPrev").onclick = () => { if (state.importHistory.page > 1) { state.importHistory.page -= 1; loadImportHistory(); } };
      $("#importHistoryNext").onclick = () => { if (state.importHistory.page < totalPages) { state.importHistory.page += 1; loadImportHistory(); } };
    } catch (error) {
      body.innerHTML = `<div class="empty">导入历史加载失败：${esc(error.message)}</div>`;
    }
  }

  function renderPendingUpload() {
    const box = $("#pendingUploadPanel");
    if (!box) return;
    const pending = state.pendingUpload;
    if (!pending || activeImportJob()) { box.innerHTML = ""; return; }
    const domainLabel = pending.domain === "tcm" ? "中医" : "西医";
    const summary = `${domainLabel}指南导入 · 模板「${esc(pending.templateName || pending.templateId || "-")}」 · 目的图谱：${pending.graphFilename ? esc(pending.graphFilename) : "跟随模板默认"}；抽取 → 图谱与诊疗依据 → 人工审核 → 规则草稿`;
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
    const clause = job.mode === "auto" ? `${state.pendingUpload?.domain === "tcm" ? "中医" : "西医"}指南导入（解析 + 图谱 + 诊疗依据）`
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
    if (state.importJob?.status === "failed" && state.importRetry?.file === pending.file && state.importRetry.jobId === state.importJob.job_id) {
      form.append("retry_doc_id", state.importRetry.docId);
    }
    // 手动路径：template_id 与目的图谱随任务显式提交；服务端 _run_auto_job
    // 收到 template_id 后直接按该模板执行全流程，不再做领域自动识别。
    form.append("template_id", pending.templateId || "");
    form.append("auto_activate", "false");
    form.append("domain", pending.domain || "auto");
    if (pending.graphFilename) form.append("graph_filename", pending.graphFilename);
    let created;
    try {
      created = await api("/api/import/jobs", { method: "POST", body: form });
    } catch (error) {
      toast(`任务提交失败：${error.message}`, true);
      return;
    } finally {
      importSubmitting = false; // 请求 settle（成功/失败）即解除提交锁
    }
    state.importRetry = {jobId: created.job_id, docId: created.doc_id, file: pending.file};
    state.importJob = {
      job_id: created.job_id,
      mode: pendingUploadMode(),
      doc_title: pending.file.name.replace(/\.(pdf|docx)$/i, ""),
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
        state.autoResult.entity_count ??= state.autoResult.entities?.length || 0;
        state.autoResult.relation_count ??= state.autoResult.relations?.length || 0;
        state.autoResult.rule_count ??= state.autoResult.rules?.length || 0;
        state.autoResult.local_term_count ??= state.autoResult.metadata?.local_terms || 0;
        state.guidePackageId = state.autoResult.package_id || state.guidePackageId;
        if (state.route === "import") reload();
        else markStale("import");
        markStale("dashboard", "packages", "graph", "review", "rules");
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
    IMPORT_PANELS.forEach((cfg) => {
      [`#pdfInput${cfg.suffix}`, `#uploadTemplate${cfg.suffix}`, `#uploadGraph${cfg.suffix}`].forEach((selector) => {
        const el = $(selector);
        if (el) el.disabled = busy;
      });
      const drop = $(`#dropzone${cfg.suffix}`);
      if (drop) {
        drop.style.pointerEvents = busy ? "none" : "auto";
        drop.style.opacity = busy ? ".6" : "1";
      }
    });
  }

  // 手动路径：每个领域面板独立选模板与目的图谱，选文件时严格读取本面板的选择。
  // 模板即领域；不读任何自动识别结果。
  // 曾因忽略模板下拉、走领域自动识别，导致指定西医模板仍生成中医图谱。
  function prepareAutoUpload(file, cfg) {
    const uploadName = file.name.toLowerCase();
    if (uploadName.endsWith(".doc")) return toast("旧版 .doc 格式不支持，请在 Word 中另存为 .docx 后重新上传", true);
    if (!uploadName.endsWith(".pdf") && !uploadName.endsWith(".docx")) return toast("仅支持 PDF 或 Word (.docx) 文件", true);
    if (activeImportJob()) return toast("已有导入任务正在进行，请等待完成或停止当前任务", true);
    const templateId = $(`#uploadTemplate${cfg.suffix}`)?.value || "";
    const template = state.templates.find((t) => t.template_id === templateId);
    if (!template) return toast(`请先在「${cfg.title}」面板选择抽取模板`, true);
    const graphSel = $(`#uploadGraph${cfg.suffix}`);
    let graphFilename = "";
    if (graphSel?.value === "__new__") {
      graphFilename = ($(`#uploadGraphNew${cfg.suffix}`)?.value || "").trim();
      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*(\.json)?$/.test(graphFilename) || graphFilename.includes("..")) {
        return toast("新图谱文件名无效：仅限字母、数字、点、下划线、连字符", true);
      }
    } else if (graphSel?.value && graphSel.value !== "follow") {
      graphFilename = graphSel.value;
    }
    state.pendingUpload = {
      file, kind: "auto", mode: "auto",
      templateId: template.template_id,
      templateName: template.name,
      graphFilename,
      domain: normalizeDomainClient(template.domain),
    };
    renderPendingUpload();
    toast(`已选择 ${file.name}；将按${cfg.label}模板「${template.name}」导入`);
  }

  function renderImportResult() {
    const box = $("#importResult"); if (!box || !state.doc) return;
    const doc = state.doc;
    const coverage = doc.provider_metadata?.coverage;
    const domain = normalizeDomainClient(doc.extraction?.domain || doc.domain);
    const anchorKinds = domain === "tcm" ? (doc.entities.some((e) => e.entity_type === "syndrome") ? ["syndrome"] : ["disease"]) : ["disease"];
    const anchors = [...new Map(doc.entities.filter((e) => anchorKinds.includes(e.entity_type) && e.code && !(e.discovered_from === "relation" && e.name === e.code)).map((e) => [e.code, e])).values()];
    const target = anchors.some((e) => e.code === doc.targetDisease) ? doc.targetDisease : (anchors[0]?.code || "");
    const configuredGraph = doc.graph_filename || doc.extraction?.graph_filename || "";
    doc.graphTarget ||= configuredGraph ? {mode: "legacy", filename: configuredGraph} : {mode: "new", filename: ""};
    const incomplete = coverage?.complete === false;
    const empty = !doc.entities.length;
    doc.targetDisease = target;
    box.innerHTML = `
      <div class="result-banner ${incomplete || empty ? "warning" : "ok"}">${esc(doc.doc_title)} ${empty ? "抽取结果为空" : incomplete ? "解析未完成" : "解析完成"}：${doc.total_pages} 页，${doc.total_lines} 行。<div class="muted">方式：${doc.extraction_mode === "llm" ? "大模型解析" : "本地模板解析"}；领域：${domain === "tcm" ? "中医" : "西医"}；模板：${esc(doc.template_name || doc.template_id || "-")}${coverage ? `；片段覆盖 ${coverage.processed}/${coverage.total}${coverage.excluded ? `，另有 ${coverage.excluded} 个参考文献或纯标题片段不参与抽取` : ""}` : ""}</div></div>
      <div class="pipeline">
        <div class="step done"><strong>1 上传并解析</strong><span>原文定位与解析留痕</span></div>
        <div class="step"><strong>2 生成知识包</strong><span>生成后进入知识包页</span></div>
        <div class="step pending"><strong>3 审核候选内容</strong><span>在知识包页核对实体、关系和候选规则</span></div>
        <div class="step pending"><strong>4 在知识包页入库</strong><span>预检后统一导入图谱和草稿规则</span></div>
      </div>
      ${(doc.warnings || []).length ? (() => {
        const list = doc.warnings;
        const render = (items) => `<ul class="issue-list">${items.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`;
        return `<div class="result-banner warning"><strong>解析告警 ${list.length} 条</strong><div class="muted">${incomplete || empty ? "存在解析失败或空结果，可能遗漏诊疗依据；请先重试并核对覆盖报告。" : "以下为修正或忽略记录，请核对相关内容。"}</div>${render(list.slice(0, 8))}${list.length > 8 ? `<details class="debug-panel"><summary>展开其余 ${list.length - 8} 条</summary>${render(list.slice(8))}</details>` : ""}</div>`;
      })() : ""}
      ${incomplete || empty ? '<button id="retryExtraction" class="btn primary">重新解析未完成内容</button>' : ""}
      ${panel("审核与生成知识包", `
        <div class="inline-controls">
          <div class="field"><label>主目标${domain === "tcm" && anchorKinds.includes("syndrome") ? "证候" : "疾病"}（预检锚点）</label><select id="targetDisease" ${anchors.length ? "" : "disabled"}>${!anchors.length ? '<option value="">未识别到可用目标，请先重新解析</option>' : ""}${anchors.map((d) => `<option value="${esc(d.code)}" ${d.code === target ? "selected" : ""}>${esc(d.name)} ${esc(d.code)}</option>`).join("")}</select></div>
          <div class="muted" style="flex-basis:100%">${anchors.length > 1
            ? `本次将导入包内全部 ${anchors.length} 个${domain === "tcm" ? "证候" : "疾病"}：${esc(anchors.map((d) => `${d.name}（${d.code}）`).join("、"))}。主目标仅用于预检锚点与诊断链路定位，不是唯一导入对象。`
            : "主目标仅用于预检锚点与诊断链路定位；包内实体、关系和待审诊疗依据会导入。"}</div>
          ${domain === "tcm" ? `
          <div class="field"><label>目标图谱</label><select id="graphTargetMode">
            ${configuredGraph ? `<option value="legacy" ${doc.graphTarget.mode === "legacy" ? "selected" : ""}>沿用已设置图谱：${esc(configuredGraph)}</option>` : ""}
            <option value="new" ${doc.graphTarget.mode === "new" ? "selected" : ""}>新建独立中医图谱</option>
            <option value="existing" ${doc.graphTarget.mode === "existing" ? "selected" : ""}>更换为其他既有中医图谱</option>
          </select></div>
          <div class="field hidden" id="newGraphNameField"><label>图谱文件名（可选）</label><input id="newGraphName" value="${esc(doc.graphTarget.mode === "new" ? doc.graphTarget.filename : "")}" placeholder="留空自动生成 KG-TCM-{doc_id}.json"></div>
          <div class="field hidden" id="graphTargetField"><label>既有图谱</label><select id="graphTargetFilename"><option value="">加载中</option></select></div>
          <div class="field hidden" id="legacyGraphField"><label>模板绑定图谱</label><input id="legacyGraphName" value="${esc(doc.graph_filename || doc.extraction?.graph_filename || "")}" readonly></div>` : ""}
          <button id="generatePackage" class="btn primary" ${incomplete || empty || !anchors.length ? "disabled" : ""}>生成知识包并进入审核</button>
        </div>
        <p class="muted">生成后进入「知识包」页查看自动预检并完成入库；此处不再单独导入图谱或规则。</p>
      `, "", "生成的是候选知识包；图谱和草稿规则只在知识包页审核后统一入库，不会自动启用")}
      <section class="panel"><div class="panel-head"><div><h3>原文定位</h3><p>按页查看指南原文，用于核对解析结果；实体、关系与规则的明细在知识包页审核</p></div></div><div id="importTabBody" class="panel-body"></div></section>
    `;
    $("#targetDisease").onchange = (e) => { doc.targetDisease = e.target.value; };
    const graphMode = $("#graphTargetMode");
    if (graphMode) {
      const renderGraphTargetFields = () => {
        $("#newGraphNameField")?.classList.toggle("hidden", graphMode.value !== "new");
        $("#graphTargetField")?.classList.toggle("hidden", graphMode.value !== "existing");
        $("#legacyGraphField")?.classList.toggle("hidden", graphMode.value !== "legacy");
      };
      const loadExistingTargets = async () => {
        if (graphMode.value !== "existing") return;
        try {
          state.targetGraphs = availableTcmTargetGraphs(await api("/api/kg/graphs?domain=tcm"));
          const selector = $("#graphTargetFilename");
          selector.innerHTML = state.targetGraphs.map((item) => `<option value="${esc(item.graph_filename)}" ${item.graph_filename === doc.graphTarget.filename ? "selected" : ""}>${esc(item.graph_filename)}（证候 ${item.syndromes || 0}）</option>`).join("");
          doc.graphTarget.filename = selector.value;
        } catch (error) { toast(error.message, true); }
      };
      graphMode.onchange = async () => {
        doc.graphTarget = {mode: graphMode.value, filename: graphMode.value === "legacy" ? configuredGraph : ""};
        renderGraphTargetFields();
        await loadExistingTargets();
      };
      if (graphMode.value === "existing") loadExistingTargets();
      $("#newGraphName").oninput = (event) => { doc.graphTarget.filename = event.target.value.trim(); };
      $("#graphTargetFilename").onchange = (event) => { doc.graphTarget.filename = event.target.value; };
      renderGraphTargetFields();
    }
    if ($("#retryExtraction")) $("#retryExtraction").onclick = async (event) => {
      const button = event.currentTarget;
      button.disabled = true; button.textContent = "正在重新解析，请稍候…";
      try {
        state.doc = await api(`/api/pdf/${encodeURIComponent(doc.doc_id)}/reparse`, {json: {}});
        renderImportResult();
      } catch (error) { toast(error.message, true); button.disabled = false; button.textContent = "重新解析未完成内容"; }
    };
    $("#generatePackage").onclick = generatePackage;
    renderImportTab();
  }

  async function renderImportTab() {
    const body = $("#importTabBody"); const doc = state.doc; if (!body || !doc) return;
    const pages = [...new Set(doc.chunks.map((c) => c.page_start))].sort((a, b) => a - b);
    const page = doc.linePage || pages[0] || 1;
    body.innerHTML = `<div class="inline-controls"><div class="field"><label>页码</label><select id="pageSelect">${pages.map((p) => `<option value="${p}" ${p === page ? "selected" : ""}>第 ${p} 页</option>`).join("")}</select></div></div><div id="lineList" class="table-wrap"><div class="empty">加载中</div></div>`;
    $("#pageSelect").onchange = async (e) => { doc.linePage = Number(e.target.value); renderImportTab(); };
    const requestToken = ++state.importRequestToken;
    const data = await api(`/api/pdf/${doc.doc_id}/lines?page=${page}`);
    const lineList = $("#lineList");
    if (requestToken !== state.importRequestToken || !lineList) return;
    lineList.innerHTML = table(["页", "行", "正文", "定位"], data.lines.map((l) => `<tr><td>${l.page}</td><td>${l.line}</td><td>${esc(l.text)}</td><td class="mono muted">p${l.page}:l${l.line}</td></tr>`));
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
    pageEl.innerHTML = `
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
          <div class="field"><label>单次字符上限（自动管线上限 6000）</label><input id="llmMaxChars" type="number" min="1000" value="${config.llm.max_chunk_chars}"></div>
          <div class="field"><label>单批最大片段数（全量分批）</label><input id="llmMaxChunks" type="number" min="1" value="${config.llm.max_chunks}"></div>
          <div class="field"><label>抽取并发数（1-8）</label><input id="llmConcurrency" type="number" min="1" max="8" value="${config.llm.extraction_concurrency || 4}"></div>
          <div class="field"><label>阶段二编译批量（条/批）</label><input id="llmCompileBatch" type="number" min="1" max="8" value="${config.llm.western_compile_batch_size || 4}"></div>
        </div>
        <div class="actions"><button id="saveExtraction" class="btn primary">保存解析配置</button><button id="testLlm" class="btn">测试连接</button><button id="clearLlmKey" class="btn ghost">清除 API Key</button></div>
      `, "", "API Key 可保存在本地配置中，也可回退到环境变量；不会回显完整 Key")}
      ${panel("模板列表", table(["模板", "范围", "目标图谱", "结构规模", "状态", "操作"], state.templates.map(templateRow)), '<button id="newTemplate" class="btn primary">新建模板</button>', "本地解析读取别名/正则；大模型解析读取同一结构、提示指令和输出契约")}
    `;
    $("#newTemplate").onclick = () => openTemplateEditor(null);
    pageEl.querySelectorAll("[data-template-structure]").forEach((b) => b.onclick = () => {
      state.templateStructureId = b.dataset.templateStructure;
      reload();
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
    pageEl.querySelectorAll("[data-template-edit]").forEach((b) => b.onclick = () => openTemplateEditor(state.templates.find((t) => t.template_id === b.dataset.templateEdit)));
    pageEl.querySelectorAll("[data-template-copy]").forEach((b) => b.onclick = () => {
      const source = state.templates.find((t) => t.template_id === b.dataset.templateCopy);
      const copied = JSON.parse(JSON.stringify(source));
      copied.template_id = `${source.template_id}_COPY_${Date.now().toString(36).toUpperCase()}`;
      copied.name = `${source.name} 副本`;
      copied.is_default = false;
      copied.version = 1;
      openTemplateEditor(copied, true);
    });
    pageEl.querySelectorAll("[data-template-default]").forEach((b) => b.onclick = async () => {
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
    pageEl.querySelectorAll("[data-template-delete]").forEach((b) => b.onclick = async () => {
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
    pageEl.innerHTML = `
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
    $("#templateBack").onclick = () => { state.templateStructureId = ""; reload(); };
    pageEl.querySelectorAll("[data-goto-vocab]").forEach((b) => b.onclick = () => {
      state.entityVocabType = b.dataset.gotoVocab;
      navigate("entityVocab", { reload: true });
    });
    bindPagedTable(entityTableKey);
    bindPagedTable(attributeTableKey);
    pageEl.querySelectorAll("[data-attr-filter]").forEach((button) => button.onclick = () => {
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
    pageEl.querySelectorAll("[data-struct-mode]").forEach((button) => {
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
    const terminologySummary = await api("/api/vocab/terminology/summary");
    if (!state.entityVocabType || !dictionaries.some((d) => d.entity_type === state.entityVocabType)) {
      state.entityVocabType = dictionaries.find((d) => d.entity_type === "disease")?.entity_type
        || dictionaries[0]?.entity_type
        || "";
    }
    const selected = dictionaries.find((d) => d.entity_type === state.entityVocabType) || null;
    if (selected?.entity_type === "concept") { await renderVocab(dictionaries); return; }
    const entries = selected ? await api(`/api/vocab/entities/${encodeURIComponent(selected.entity_type)}`) : [];
    const totalEntries = dictionaries.reduce((sum, d) => sum + d.entry_count, 0);
    const templateCount = new Set(dictionaries.flatMap((d) => d.referencing_templates.map((t) => t.template_id))).size;
    const dictLabel = (type) => dictionaries.find((item) => item.entity_type === type)?.label || typeName[type] || type;
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
    pageEl.innerHTML = `
      ${panel("三层架构", `<div class="pipeline">
        <div class="step done"><strong>Schema 层 · 解析模板</strong><span>实体类型、关系、属性契约</span></div>
        <div class="step done"><strong>术语层 · 实体词典（本页）</strong><span>共享实体字典 data/vocab/entities</span></div>
        <div class="step done"><strong>实例层 · 知识图谱</strong><span>文档导入后生成的节点与证据</span></div>
      </div>`, "", "模板只定义“有哪些类型和关系”（Schema 层）；具体实体统一存放在共享词典（术语层）；图谱节点按导入文档生成（实例层）")}
      ${entityDictionaryPicker(dictionaries, state.entityVocabType)}
      <div class="grid c4">
        ${selected?.entity_type === "disease" ? `
          <div class="kpi"><span>启用疾病实体</span><strong>${terminologySummary.active_entities}</strong><small>参与解析与规则生成</small></div>
          <div class="kpi"><span>标准参考术语</span><strong>${terminologySummary.reference_terms}</strong><small>只读参考库，审核后启用</small></div>
          <div class="kpi"><span>待确认术语</span><strong>${terminologySummary.pending_candidates}</strong><small>抽取发现、尚未入库</small></div>
          <div class="kpi"><span>已审核院内映射</span><strong>${terminologySummary.approved_mappings}</strong><small>院内码 → 标准实体</small></div>
        ` : `
          <div class="kpi"><span>词典数</span><strong>${dictionaries.length}</strong><small>按实体类型分文件</small></div>
          <div class="kpi"><span>实体总数</span><strong>${totalEntries}</strong><small>共享术语条目</small></div>
          <div class="kpi"><span>引用模板数</span><strong>${templateCount}</strong><small>通过共享词表引用词典</small></div>
          <div class="kpi"><span>当前词典</span><strong>${selected ? entries.length : 0}</strong><small>${esc(selected ? dictLabel(selected.entity_type) : "-")} 条目 · 已分类 ${classifiedCount}</small></div>
        `}
      </div>
      ${selected ? pagedPanel({
        key: tableKey,
        title: `${dictLabel(selected.entity_type)}词典（${entries.length}）`,
        description: `按专科、关联疾病筛选；已分类 ${classifiedCount}/${entries.length} 条。专科和疾病默认从图谱关联自动推断，可在编辑中手工覆盖。`,
        headers: ["编码", "名称", "领域", "专科", "关联疾病", "标准", "别名 / 正则", "操作"],
        items: entryItems,
        filters: activeFilters,
        placeholder: "搜索编码、名称、别名、专科或疾病…",
        emptyText: "没有匹配的实体",
        actions: `${selected.entity_type === "disease" ? '<button id="termCandidates" class="btn ghost">待确认术语</button><button id="hospitalMappings" class="btn ghost">院内映射</button>' : ''}${["disease", "exam", "lab", "medication", "intervention"].includes(selected.entity_type) ? '<button id="importIcd10" class="btn ghost">标准术语库</button>' : ''}<button id="newEntityEntry" class="btn primary">新增实体</button>`,
      }) : '<div class="empty">暂无词典；在解析模板中将实体类型设为共享词表后自动创建</div>'}
      ${selected && selected.referencing_templates.length ? panel("引用该词典的模板", table(["模板", "ID", "状态"], selected.referencing_templates.map((t) => `
        <tr><td><strong>${esc(t.name)}</strong></td><td class="mono">${esc(t.template_id)}</td><td>${badge(t.status)}</td></tr>`
      )), "", "这些模板的实体类型勾选了“共享词表”，词典修改后无需逐模板维护") : ""}
    `;
    bindEntityDictionaryPicker();
    const newButton = $("#newEntityEntry");
    if (newButton && selected) newButton.onclick = () => openEntityEntryEditor(selected.entity_type, null);
    const importButton = $("#importIcd10");
    if (importButton && selected) importButton.onclick = async () => {
      try {
        if (selected.entity_type === "disease") await openIcd10Importer(selected.entity_type);
        else await openReferenceImporter(selected.entity_type);
      } catch (error) { toast(error.message, true); }
    };
    const candidatesButton = $("#termCandidates");
    if (candidatesButton) candidatesButton.onclick = () => openTerminologyCandidates();
    const mappingsButton = $("#hospitalMappings");
    if (mappingsButton) mappingsButton.onclick = () => openHospitalMappings(entries);
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

  async function openTerminologyCandidates() {
    const dialog = $("#editorDialog");
    const saveButton = $("#editorSave");
    saveButton.style.display = "none";
    const render = async () => {
      const items = await api("/api/vocab/terminology/candidates?entity_type=disease&status=pending");
      $("#editorBody").innerHTML = `
        <h3>待确认疾病术语</h3>
        <p class="muted">模糊匹配只提供候选，不会直接写入启用字典。请选择正确标准编码后审核通过，或拒绝无效术语。</p>
        ${items.length ? `<div class="table-wrap"><table class="table">
          <thead><tr><th>抽取术语</th><th>出现</th><th>来源</th><th>建议标准术语</th><th>操作</th></tr></thead>
          <tbody>${items.map((item) => `<tr>
            <td><strong>${esc(item.name)}</strong><div class="mono muted">${esc(item.local_code)}</div><div class="quote">${esc((item.aliases || []).join(" / "))}</div></td>
            <td>${item.occurrences || 1}</td>
            <td>${esc((item.source_doc_ids || []).join("、") || "-")}</td>
            <td><input data-candidate-code="${esc(item.candidate_id)}" list="suggestions-${esc(item.candidate_id)}" placeholder="输入 ICD-10 编码">
              <datalist id="suggestions-${esc(item.candidate_id)}">${(item.suggested_mappings || []).map((mapping) => `<option value="${esc(mapping.code)}">${esc(mapping.name)}</option>`).join("")}</datalist>
            </td>
            <td><div class="actions"><button class="btn sm primary" data-candidate-approve="${esc(item.candidate_id)}">通过</button><button class="btn sm danger" data-candidate-reject="${esc(item.candidate_id)}">拒绝</button></div></td>
          </tr>`).join("")}</tbody>
        </table></div>` : '<div class="empty">暂无待确认疾病术语</div>'}`;
      $("#editorBody").querySelectorAll("[data-candidate-approve]").forEach((button) => button.onclick = async () => {
        const id = button.dataset.candidateApprove;
        const standardCode = $("#editorBody").querySelector(`[data-candidate-code="${id}"]`).value;
        if (!standardCode) return toast("请选择标准疾病编码", true);
        try {
          await api(`/api/vocab/terminology/candidates/${encodeURIComponent(id)}/review`, { json: { status: "approved", standard_code: standardCode } });
          toast("候选术语已审核并加入启用字典");
          await reload();
        } catch (error) { toast(error.message, true); }
      });
      $("#editorBody").querySelectorAll("[data-candidate-reject]").forEach((button) => button.onclick = async () => {
        try {
          await api(`/api/vocab/terminology/candidates/${encodeURIComponent(button.dataset.candidateReject)}/review`, { json: { status: "rejected" } });
          toast("候选术语已拒绝");
          await reload();
        } catch (error) { toast(error.message, true); }
      });
    };
    dialog.showModal();
    await reload();
    $("#editorCancel").onclick = () => {
      dialog.close();
      saveButton.style.display = "";
      renderEntityVocab();
    };
  }

  async function openHospitalMappings(diseases) {
    const dialog = $("#editorDialog");
    const saveButton = $("#editorSave");
    const mappings = await api("/api/vocab/terminology/mappings?entity_type=disease");
    saveButton.style.display = "";
    saveButton.textContent = "新增映射";
    $("#editorBody").innerHTML = `
      <h3>疾病院内码映射</h3>
      <p class="muted">只有“已审核”映射会进入病历标准化；草稿可继续维护，不影响推理。</p>
      <div class="form-grid">
        <div class="field"><label>医院编码</label><input name="hospital_id" placeholder="例如 XM_FIRST"></div>
        <div class="field"><label>院内诊断码</label><input name="local_code"></div>
        <div class="field"><label>院内名称</label><input name="local_name"></div>
        <div class="field"><label>标准疾病</label><select name="canonical_code"><option value="">请选择</option>${diseases.map((item) => `<option value="${esc(item.code)}">${esc(item.code)} · ${esc(item.name)}</option>`).join("")}</select></div>
        <div class="field"><label>状态</label><select name="status"><option value="draft">草稿</option><option value="approved">已审核</option></select></div>
      </div>
      ${mappings.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>医院</th><th>院内术语</th><th>标准实体</th><th>关系</th><th>状态</th></tr></thead><tbody>
        ${mappings.map((item) => `<tr><td>${esc(item.hospital_id)}</td><td><span class="mono">${esc(item.local_code)}</span><div>${esc(item.local_name || "-")}</div></td><td class="mono">${esc(item.canonical_code)}</td><td>${esc(item.relation)}</td><td>${badge(item.status)}</td></tr>`).join("")}
      </tbody></table></div>` : '<div class="empty">暂无疾病院内码映射</div>'}`;
    dialog.showModal();
    saveButton.onclick = async () => {
      const form = $("#editorBody");
      const payload = Object.fromEntries([...form.querySelectorAll("input,select")].map((input) => [input.name, input.value]));
      payload.entity_type = "disease";
      payload.relation = "exact";
      payload.source = "manual";
      try {
        await api("/api/vocab/terminology/mappings", { json: payload });
        dialog.close();
        saveButton.textContent = "保存";
        toast("院内疾病映射已保存");
        renderEntityVocab();
      } catch (error) { toast(error.message, true); }
    };
    $("#editorCancel").onclick = () => {
      dialog.close();
      saveButton.textContent = "保存";
    };
  }

  function openEntityEntryEditor(entityType, item, isNew = false) {
    const isTcmDisease = entityType === "tcm_disease";
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
      : isTcmDisease
        ? '<div class="field"><label>领域</label><input value="中医" readonly></div>'
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
      : isTcmDisease ? "编码使用 TCM_DIS_ 前缀。标准编码与出处经核实后填写。"
        : `保存后立即对全部引用「${esc(typeName[entityType] || entityType)}」词典的同领域模板生效。`;
    const derivedSpecialty = (value.specialties || []).filter((entry) => entry && entry !== "未分类").join("、");
    const derivedDiseases = (value.diseases || []).filter((entry) => entry && entry !== "未分类");
    $("#editorBody").innerHTML = `
      <h3>${isNew ? "新增共享实体" : "编辑共享实体"} · ${esc(isTcmDisease ? "中医疾病" : typeName[entityType] || entityType)}</h3>
      <div class="form-grid">
        <div class="field"><label>实体编码</label><input name="code" value="${esc(value.code)}" ${!isNew ? "readonly" : ""}></div>
        <div class="field"><label>实体名称</label><input name="name" value="${esc(value.name)}"></div>
        ${domainField}
        ${standardFields}
        ${isTcmDisease ? `<div class="field wide"><label>出处</label><input name="source" value="${esc(value.source || "")}"></div>` : ""}
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
      if (isTcmDisease) {
        payload.domain = "tcm";
        payload.standard_mappings = value.standard_mappings || [];
        payload.attributes = value.attributes || {};
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
    const bundles = await api("/api/vocab/reference/icd10/bundles");
    saveButton.textContent = "导入选中条目";
    $("#editorBody").innerHTML = `
      <h3>从 ICD-10 标准库导入</h3>
      <p class="muted">标准库只提供标准编码和名称，不直接参与解析识别。勾选后导入到活跃疾病字典，解析时才会使用。</p>
      <h4>按专科批量启用</h4>
      <div class="table-wrap"><table class="table">
        <thead><tr><th>疾病包</th><th>参考条目</th><th>已启用</th><th>可启用</th><th>操作</th></tr></thead>
        <tbody>${bundles.map((bundle) => `<tr>
          <td><strong>${esc(bundle.name)}</strong><div class="muted">${esc(bundle.description || "")}</div></td>
          <td>${bundle.reference_count}</td><td>${bundle.active_count}</td><td>${bundle.available_count}</td>
          <td><button type="button" class="btn sm" data-activate-bundle="${esc(bundle.bundle_id)}" ${bundle.available_count ? "" : "disabled"}>启用缺失条目</button></td>
        </tr>`).join("")}</tbody>
      </table></div>
      <h4>导入授权标准库文件</h4>
      <div class="form-grid">
        <div class="field wide"><label>JSON / CSV 文件（最大 20MB）</label><input id="catalogFile" type="file" accept=".json,.csv"></div>
        <div class="field"><label>标准版本</label><input id="catalogVersion" placeholder="例如：国家临床版 2.0（2022汇总版）"></div>
        <div class="field"><label>数据来源</label><input id="catalogSource" placeholder="授权文件名称或发布机构"></div>
        <div class="field wide"><div class="actions"><button type="button" id="validateCatalog" class="btn ghost">仅校验</button><button type="button" id="importCatalog" class="btn">校验并合并</button></div></div>
        <div id="catalogReport" class="field wide muted">正式合并前可先校验；不会自动启用新导入的参考术语。</div>
      </div>
      <h4>搜索并选择启用</h4>
      <div class="inline-controls">
        <div class="field"><label>搜索编码、名称或别名</label><input id="icd10Search" placeholder="例如：I10、高血压、淋巴瘤"></div>
      </div>
      <div id="icd10Results" class="table-wrap"><div class="empty">正在加载...</div></div>
    `;
    dialog.showModal();

    $("#editorBody").querySelectorAll("[data-activate-bundle]").forEach((button) => button.onclick = async () => {
      const bundle = bundles.find((item) => item.bundle_id === button.dataset.activateBundle);
      if (!confirm(`确认将“${bundle?.name || button.dataset.activateBundle}”中尚未启用的 ${bundle?.available_count || 0} 条疾病加入实体字典？`)) return;
      try {
        const result = await api(`/api/vocab/reference/icd10/bundles/${encodeURIComponent(button.dataset.activateBundle)}/activate`, { json: {} });
        dialog.close();
        saveButton.textContent = "保存";
        toast(`专科包已启用 ${result.imported_count} 条；跳过 ${result.skipped_count} 条`);
        renderEntityVocab();
      } catch (error) { toast(error.message, true); }
    });

    const submitCatalog = async (dryRun) => {
      const file = $("#catalogFile").files[0];
      if (!file) return toast("请选择标准术语 JSON 或 CSV 文件", true);
      const form = new FormData();
      form.append("file", file);
      form.append("version", $("#catalogVersion").value.trim());
      form.append("source", $("#catalogSource").value.trim());
      form.append("mode", "merge");
      form.append("dry_run", String(dryRun));
      try {
        const report = await api("/api/vocab/reference/icd10/catalog/import", { method: "POST", body: form });
        const box = $("#catalogReport");
        box.innerHTML = report.valid
          ? `<span class="badge active">校验通过</span> 文件 ${report.incoming_count} 条；新增 ${report.added_count} 条；更新 ${report.updated_count} 条；警告 ${(report.warnings || []).length} 条${dryRun ? "（尚未写入）" : `；合并后 ${report.result_count} 条`}`
          : `<span class="badge rejected">校验失败</span> ${(report.errors || []).slice(0, 5).map((item) => `第 ${item.row} 行：${esc(item.message)}`).join("；")}`;
        if (!dryRun && report.valid) toast("标准参考库已合并；新条目需审核或按专科启用");
      } catch (error) { toast(error.message, true); }
    };
    $("#validateCatalog").onclick = () => submitCatalog(true);
    $("#importCatalog").onclick = () => submitCatalog(false);

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

  async function openReferenceImporter(entityType) {
    const catalog = { exam: "exam", lab: "lab", medication: "medication", intervention: "surgery" }[entityType];
    if (!catalog) return;
    const metadata = (await api("/api/vocab/reference/catalogs")).find((item) => item.catalog === catalog);
    if (!metadata) return toast("未找到所选参考库", true);
    const endpoint = `/api/vocab/reference/catalogs/${catalog}`;
    const dialog = $("#editorDialog");
    const saveButton = $("#editorSave");
    saveButton.textContent = "导入选中条目";
    $("#editorBody").innerHTML = `
      <h3>${esc(metadata.label)}标准参考库</h3>
      <p>编码体系：${esc(metadata.standard)} · 版本：${esc(metadata.version)} · 参考条目：${metadata.count}</p>
      <p>${esc(metadata.scope || "")}</p><p>${esc(metadata.description || "")}</p>
      <p class="muted">数据来源：${esc(metadata.source || "")}。选中条目加入${esc(typeName[entityType] || entityType)}词典后，使用共享词表的模板才会读取。</p>
      <h4>导入授权标准库文件</h4>
      <div class="form-grid">
        <div class="field wide"><label>JSON / CSV 文件（最大 20MB）</label><input id="catalogFile" type="file" accept=".json,.csv"></div>
        <div class="field"><label>标准版本</label><input id="catalogVersion" placeholder="填写导入文件对应版本"></div>
        <div class="field"><label>数据来源</label><input id="catalogSource" placeholder="授权文件名称或发布机构"></div>
        <div class="field wide"><div class="actions"><button type="button" id="validateCatalog" class="btn ghost">仅校验</button><button type="button" id="importCatalog" class="btn">校验并合并</button></div></div>
        <div id="catalogReport" class="field wide muted">正式合并前可先校验；不会自动启用新导入的参考术语。</div>
      </div>
      <h4>搜索并选择启用</h4>
      <div class="inline-controls">
        <div class="field"><label>搜索编码、名称或别名</label><input id="icd10Search" placeholder="搜索标准编码、名称或别名"></div>
      </div>
      <div id="icd10Results" class="table-wrap"><div class="empty">正在加载...</div></div>
    `;
    dialog.showModal();

    const submitCatalog = async (dryRun) => {
      const file = $("#catalogFile").files[0];
      if (!file) return toast("请选择标准术语 JSON 或 CSV 文件", true);
      const form = new FormData();
      form.append("file", file);
      form.append("version", $("#catalogVersion").value.trim());
      form.append("source", $("#catalogSource").value.trim());
      form.append("mode", "merge");
      form.append("dry_run", String(dryRun));
      try {
        const report = await api(`${endpoint}/import`, { method: "POST", body: form });
        if (!dryRun && report.valid) await renderResults($("#icd10Search").value);
        const box = $("#catalogReport");
        box.innerHTML = report.valid
          ? `<span class="badge active">校验通过</span> 文件 ${report.incoming_count} 条；新增 ${report.added_count} 条；更新 ${report.updated_count} 条；警告 ${(report.warnings || []).length} 条${dryRun ? "（尚未写入）" : `；合并后 ${report.result_count} 条`}`
          : `<span class="badge rejected">校验失败</span> ${(report.errors || []).slice(0, 5).map((item) => `第 ${item.row} 行：${esc(item.message)}`).join("；")}`;
        if (!dryRun && report.valid) toast("标准参考库已合并；新条目需选用后才进入实体字典");
      } catch (error) { toast(error.message, true); }
    };
    $("#validateCatalog").onclick = () => submitCatalog(true);
    $("#importCatalog").onclick = () => submitCatalog(false);

    const renderResults = async (query = "") => {
      try {
        const result = await api(`${endpoint}?q=${encodeURIComponent(query)}&limit=50`);
        const box = $("#icd10Results");
        if (!result.items?.length) {
          box.innerHTML = `<div class="empty">没有匹配的参考术语</div>`;
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
                  <td><strong>${esc(item.name)}</strong><div class="muted">${esc(item.standard_system)} · ${esc(item.standard_code)}</div><div class="muted">${esc(item.source || "")}</div></td>
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
        toast("请先勾选要导入的参考术语", true);
        return;
      }
      try {
        const result = await api(`${endpoint}/activate`, { json: { codes } });
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

  function entityDictionaryPicker(dictionaries, selectedType) {
    const label = (entry) => entry.label || typeName[entry.entity_type] || entry.entity_type;
    const diseaseDictionaries = ["tcm_disease", "disease"]
      .map((type) => dictionaries.find((entry) => entry.entity_type === type))
      .filter(Boolean);
    return panel("词典选择", `
      <div class="inline-controls">
        <div class="actions" aria-label="疾病词典">
          ${diseaseDictionaries.map((entry) => `
            <button type="button" class="btn ${entry.entity_type === selectedType ? "primary" : ""}"
              data-entity-dictionary="${esc(entry.entity_type)}" aria-pressed="${entry.entity_type === selectedType}">
              ${esc(label(entry))}（${entry.entry_count}）
            </button>`).join("")}
        </div>
        <div class="field"><label for="entityVocabSelector">全部实体词典</label>
          <select id="entityVocabSelector">${dictionaries.map((entry) => `<option value="${esc(entry.entity_type)}" ${entry.entity_type === selectedType ? "selected" : ""}>${esc(label(entry))}（${entry.entry_count}）</option>`).join("")}</select>
        </div>
      </div>`);
  }

  function bindEntityDictionaryPicker() {
    const select = (type) => {
      state.entityVocabType = type;
      return renderEntityVocab();
    };
    $("#entityVocabSelector").onchange = (event) => select(event.target.value);
    pageEl.querySelectorAll("[data-entity-dictionary]").forEach((button) => {
      button.onclick = () => select(button.dataset.entityDictionary);
    });
  }

  async function renderVocab(dictionaries = null) {
    if (state.route !== "entityVocab") { state.entityVocabType = "concept"; navigate("entityVocab"); return; }
    await loadBase();
    dictionaries = dictionaries || await api("/api/vocab/entities");
    const search = state.vocabSearch.trim().toLowerCase();
    const category = state.vocabCategory;
    const filtered = state.vocab.filter((item) => (!category || item.category === category) && (!search || `${item.code} ${item.name_cn} ${item.name_en} ${(item.aliases || []).join(" ")} ${item.description}`.toLowerCase().includes(search)));
    const categories = [...new Set(state.vocab.map((item) => item.category))].sort();
    pageEl.innerHTML = `
      ${entityDictionaryPicker(dictionaries, "concept")}
      <div class="grid c4">
        <div class="kpi"><span>概念总数</span><strong>${state.vocab.length}</strong><small>受控语义集合</small></div>
        <div class="kpi"><span>检验概念</span><strong>${state.vocab.filter((v) => v.category === "lab_concept").length}</strong><small>升高、降低、动态趋势等</small></div>
        <div class="kpi"><span>检查 / 症状概念</span><strong>${state.vocab.filter((v) => ["exam_concept", "symptom_concept"].includes(v.category)).length}</strong><small>存在、缺失、持续时间等</small></div>
        <div class="kpi"><span>中医概念</span><strong>${state.vocab.filter((v) => normalizeDomainClient(v.domain) === "tcm" || String(v.category).startsWith("tcm_")).length}</strong><small>证候判定谓词</small></div>
      </div>
      ${panel("抽象概念词典", table(["编码", "中文名称", "识别别名", "英文名称", "类别", "执行器", "说明", "状态", "操作"], filtered.map((item) => `
        <tr><td class="mono">${esc(item.code)}</td><td><strong>${esc(item.name_cn)}</strong></td><td>${esc((item.aliases || []).join(" / "))}</td><td>${esc(item.name_en || "-")}</td><td><span class="badge">${esc(item.category)}</span></td><td class="mono">${esc(item.evaluator_type)}</td><td><div class="quote">${esc(item.description)}</div></td><td>${item.is_active ? '<span class="badge active">启用</span>' : '<span class="badge retired">停用</span>'}</td><td><div class="actions"><button class="btn sm" data-concept-edit="${esc(item.code)}">编辑</button><button class="btn sm" data-concept-usage="${esc(item.code)}">引用</button><button class="btn sm danger" data-concept-delete="${esc(item.code)}">删除</button></div></td></tr>`
      )), `<div class="actions"><button id="newConcept" class="btn primary">新增概念</button><input id="vocabSearch" class="field" placeholder="搜索编码或名称" value="${esc(state.vocabSearch)}"><select id="vocabCategory"><option value="">全部类别</option>${categories.map((v) => `<option value="${v}" ${category === v ? "selected" : ""}>${v}</option>`).join("")}</select></div>`, "概念是规则、图谱、模板和医院标准之间的公共判断语言")}
    `;
    bindEntityDictionaryPicker();
    $("#newConcept").onclick = () => openConceptEditor(null);
    $("#vocabSearch").oninput = (event) => { state.vocabSearch = event.target.value; renderVocab(); };
    $("#vocabCategory").onchange = (event) => { state.vocabCategory = event.target.value; renderVocab(); };
    pageEl.querySelectorAll("[data-concept-edit]").forEach((button) => button.onclick = () => openConceptEditor(state.vocab.find((item) => item.code === button.dataset.conceptEdit)));
    pageEl.querySelectorAll("[data-concept-usage]").forEach((button) => button.onclick = () => openConceptUsage(button.dataset.conceptUsage));
    pageEl.querySelectorAll("[data-concept-delete]").forEach((button) => button.onclick = async () => {
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
    const categories = [...new Set(["lab_concept", "exam_concept", "symptom_concept", ...state.vocab.map((v) => v.category), value.category])];
    const evaluators = [...new Set(["static_threshold", "uln_multiplier", "dynamic_trend", "qualitative", "finding_exists", "duration_check", "observation_present", ...state.vocab.map((v) => v.evaluator_type), value.evaluator_type])];
    $("#editorBody").innerHTML = `
      <h3>${isNew ? "新增抽象概念" : "编辑抽象概念"}</h3>
      <div class="form-grid">
        <div class="field"><label>概念编码</label><input name="code" value="${esc(value.code)}" ${!isNew ? "readonly" : ""}></div>
        <div class="field"><label>中文名称</label><input name="name_cn" value="${esc(value.name_cn)}"></div>
        <div class="field wide"><label>识别别名（每行一个，支持正则）</label><textarea name="aliases">${esc((value.aliases || []).join("\n"))}</textarea></div>
        <div class="field"><label>适用领域</label><select name="domain">${["western", "tcm", "shared"].map((v) => `<option value="${v}" ${(value.domain || "western") === v ? "selected" : ""}>${({ western: "西医", tcm: "中医", shared: "共享" })[v]}</option>`).join("")}</select></div>
        <div class="field"><label>英文名称</label><input name="name_en" value="${esc(value.name_en)}"></div>
        <div class="field"><label>类别</label><select name="category">${categories.map((v) => `<option value="${v}" ${value.category === v ? "selected" : ""}>${v}</option>`).join("")}</select></div>
        <div class="field"><label>推理执行器</label><select name="evaluator_type">${evaluators.map((v) => `<option value="${v}" ${value.evaluator_type === v ? "selected" : ""}>${v}</option>`).join("")}</select></div>
        <div class="field"><label>状态</label><select name="is_active"><option value="true" ${value.is_active ? "selected" : ""}>启用</option><option value="false" ${!value.is_active ? "selected" : ""}>停用</option></select></div>
        <div class="field wide"><label>说明</label><input name="description" value="${esc(value.description)}"></div>
        <div class="field wide muted">具体阈值不在概念中配置；图谱和规则只引用这里的抽象概念编码。</div>
      </div>`;
    $("#editorSave").hidden = false;
    $("#editorSave").textContent = "保存";
    const dialog = $("#editorDialog"); dialog.showModal();
    $("#editorSave").onclick = async () => {
      const form = $("#editorBody");
      const payload = Object.fromEntries([...form.querySelectorAll("input,select,textarea")].map((input) => [input.name, input.value]));
      payload.aliases = payload.aliases.split(/\r?\n/).map((v) => v.trim()).filter(Boolean);
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
        extraction_concurrency: Number($("#llmConcurrency").value || 4),
        western_compile_batch_size: Number($("#llmCompileBatch").value || 4),
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
          : graphMode.value === "legacy" ? (doc.graph_filename || doc.extraction?.graph_filename || "") : ($("#newGraphName")?.value || "").trim(),
      } : undefined;
      const result = await api("/api/guide-packages/generate", { json: {
        doc_id: doc.doc_id,
        domain: doc.extraction?.domain || doc.domain || "",
        target_disease_code: doc.targetDisease || $("#targetDisease")?.value || "",
        template_id: doc.template_id || doc.extraction?.template_id || "",
        status: "generated",
        ...(graphTarget ? { graph_target: graphTarget } : {}),
      } });
      state.guidePackageId = result.package.package_id;
      const failed = result.package.checks.filter((item) => item.status === "failed").length;
      toast(failed ? `知识包已生成，但有 ${failed} 项预检失败；请到知识包页处理` : `知识包已生成：${result.summary.rule_count} 条候选规则；请到知识包页审核`);
      navigate("packages", { reload: true });
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
    const importedGraph = selected?.import_result?.graph?.graph_filename || selected?.graph_filename || "";
    const graphAvailable = isImported(selected) && importedGraph
      && (await api("/api/kg/graphs")).some((item) => item.graph_filename === importedGraph);
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
    pageEl.innerHTML = `
      <div class="pipeline">
        <div class="step done"><strong>1 审核预检</strong><span>查看自动检查与问题明细</span></div>
        <div class="step ${packageConfirmed ? "done" : ""}"><strong>2 确认知识包</strong><span>核对目标、模板和候选内容</span></div>
        <div class="step ${isImported(selected) ? "done" : "pending"}"><strong>3 导入图谱与草稿规则</strong><span>一次性入库，不自动启用</span></div>
        <div class="step pending"><strong>4 诊疗依据及规则审核</strong><span>人工审核后逐条启用</span></div>
      </div>
      ${panel("指南知识包", table(["知识包 / 指南", "目标证候/诊断", "候选内容", "预检", "状态", "更新时间", "操作"], state.guidePackages.map((item) => {
        const check = item.check_summary || {};
        return `<tr class="package-row ${state.guidePackageId === item.package_id ? "selected" : ""}" data-package-row="${esc(item.package_id)}"><td><strong>${esc(item.package_id)}</strong><div class="muted">${esc(item.doc_title)}</div><div>${badge(normalizeDomainClient(item.domain))}</div></td><td class="mono">${esc(item.target_disease_code)}</td><td>${item.entity_count} 实体${item.syndrome_count ? `（证候 ${item.syndrome_count}）` : ""}<br>${item.relation_count} 关系 / ${item.rule_count} 规则</td><td>${check.passed || 0} 通过 / ${check.warning || 0} 提醒 / ${check.failed || 0} 失败</td><td><span class="badge ${esc(item.status)}">${esc(packageStatusLabel(item))}</span></td><td class="nowrap">${esc(formatTime(item.updated_at))}</td><td><div class="actions"><button class="btn sm ${state.guidePackageId === item.package_id ? "primary" : ""}" data-package-detail="${esc(item.package_id)}">${state.guidePackageId === item.package_id ? "正在审核 ✓" : "查看 / 审核"}</button><button class="btn sm danger" data-package-delete="${esc(item.package_id)}" title="删除知识包文件；已导入的图谱与规则不受影响">删除</button></div></td></tr>`;
      })), '<button id="refreshPackages" class="btn primary">刷新列表</button>', "预检后导入图谱及待审诊疗依据；在审核台通过依据审核后，才能生成规则草稿")}
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
          <div class="result-banner ok"><strong>该知识包已导入</strong><div>${graphAvailable ? `已写入 ${esc(importedGraph)}。请在审核工作台核对实体、关系和诊疗依据。` : "当前没有可打开的已导入图谱。"}</div><div class="actions">${graphAvailable ? '<button id="reviewPackageGraph" class="btn primary">在审核工作台打开图谱</button>' : ""}<button id="genTestCase" class="btn">生成测试病历</button><span class="muted" id="genTestCaseHint">${(selected.import_result?.test_case?.case?.name) ? `已自动生成：${esc(selected.import_result.test_case.case.name)}` : (selected.import_result?.test_case?.error ? `自动生成失败：${esc(selected.import_result.test_case.error)}` : "")}</span></div></div>
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
            ["中医疾病", (selected.entities || []).filter((item) => item.entity_type === "disease").length, (selected.entities || []).filter((item) => item.entity_type === "disease").map((item) => `${item.name} (${item.code})${item.candidate_status === "pending" ? " · 待登记" : ""}`).join("；") || "-"],
            ["证候", (selected.entities || []).filter((item) => item.entity_type === "syndrome").length, (selected.entities || []).filter((item) => item.entity_type === "syndrome").map((item) => `${item.name} (${item.code})`).join("；") || "-"],
            ["症状 / 舌象 / 脉象 / 体征", (selected.entities || []).filter((item) => ["symptom", "tongue", "pulse", "sign"].includes(item.entity_type)).length, (selected.entities || []).filter((item) => ["symptom", "tongue", "pulse", "sign"].includes(item.entity_type)).map((item) => `${typeName[item.entity_type]}:${item.name}`).join("；") || "-"],
            ["治法 / 方剂 / 中药", (selected.entities || []).filter((item) => ["treatment_method", "formula", "herb"].includes(item.entity_type)).length, (selected.entities || []).filter((item) => ["treatment_method", "formula", "herb"].includes(item.entity_type)).map((item) => `${typeName[item.entity_type]}:${item.name}`).join("；") || "-"],
          ] : [
            ["疾病", (selected.entities || []).filter((item) => item.entity_type === "disease").length, (selected.entities || []).filter((item) => item.entity_type === "disease").map((item) => `${item.name} (${item.code})`).join("；") || "-"],
            ["检验 / 检查 / 发现 / 症状", (selected.entities || []).filter((item) => ["lab", "exam", "exam_finding", "symptom", "diagnostic_evidence"].includes(item.entity_type)).length, (selected.entities || []).filter((item) => ["lab", "exam", "exam_finding", "symptom", "diagnostic_evidence"].includes(item.entity_type)).map((item) => `${typeName[item.entity_type]}:${item.name} (${item.code})`).join("；") || "-"],
          ]),
          ["候选关系", (selected.suggested_relations || []).length, (selected.suggested_relations || []).map((item) => `${item.source_code} → ${item.relation_code} → ${item.target_code || item.target_concept_code}`).join("；") || "导入时按同片段结构兜底生成"],
          ["候选规则", (selected.suggested_rules || []).length, (selected.suggested_rules || []).map((item) => `${item.rule_id} → ${clinicalIntentLabel(item.clinical_intent, item.rule_type)} → ${item.disease_code}`).join("；") || "-"],
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
          <td>${esc(item.item_type)}${item.entity_type ? ` / ${esc(item.entity_type)}` : ""}${item.item_type === "rule" ? ` / ${esc(clinicalIntentLabel(item.clinical_intent, item.rule_type))}` : ""}</td>
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
    pageEl.querySelectorAll("[data-package-detail]").forEach((button) => button.onclick = () => {
      state.guidePackageId = button.dataset.packageDetail;
      renderGuidePackages();
      // 重渲染后把选中的知识包行滚入视野，明确联动到正在审核的包。
      requestAnimationFrame(() => {
        pageEl.querySelector(`[data-package-row="${CSS.escape(state.guidePackageId)}"]`)
          ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
    });
    pageEl.querySelectorAll("[data-package-delete]").forEach((button) => button.onclick = async () => {
      const packageId = button.dataset.packageDelete;
      const item = state.guidePackages.find((entry) => entry.package_id === packageId);
      const imported = Boolean(item?.imported) || Boolean(item?.import_result?.success);
      const message = imported
        ? `「${item?.doc_title || packageId}」已导入图谱${item?.graph_filename ? `（${item.graph_filename}）` : ""}。\n\n删除只会移除知识包文件本身，已导入的图谱、规则和诊疗依据不受影响，但导入前的候选记录将无法找回。\n\n确认删除？`
        : `确认删除知识包「${item?.doc_title || packageId}」？\n\n删除后不可恢复。`;
      if (!confirm(message)) return;
      button.disabled = true;
      try {
        await api(`/api/guide-packages/${encodeURIComponent(packageId)}?confirm=true`, { method: "DELETE" });
        toast("知识包已删除");
        if (state.guidePackageId === packageId) state.guidePackageId = "";
        renderGuidePackages();
      } catch (error) {
        button.disabled = false;
        toast(error.message, true);
      }
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
    $("#reviewPackageGraph")?.addEventListener("click", () => {
      state.reviewGraphOptions = null;
      state.reviewTab = "bydisease";
      state.reviewFilters.graph = importedGraph;
      state.byDisease = { subject: "", search: "", composing: false, targetCode: selected.target_disease_code };
      navigate("review", { reload: true });
    });
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
      state.reviewGraphOptions = null;
      markStale("dashboard", "graph", "graphVisual", "review", "rules");
      renderGuidePackages();
    } catch (error) { toast(error.message, true); }
  }

  async function renderGraphVisual() {
    const graphs = await api("/api/kg/graphs");
    if (!graphs.length) { pageEl.innerHTML = '<div class="empty">暂无知识图谱，请先导入指南。</div>'; return; }
    const filename = graphs.some((g) => g.graph_filename === state.visualGraphFilename)
      ? state.visualGraphFilename : graphs[0].graph_filename;
    state.visualGraphFilename = filename;
    const graph = await api(`/api/kg/graph?graph_filename=${encodeURIComponent(filename)}`);
    if (state.route !== "graphVisual") return;
    pageEl.innerHTML = panel("图谱选择", `<div class="field"><label for="visualGraphSelect">知识图谱</label>
      <select id="visualGraphSelect">${graphs.map((g) => `<option value="${esc(g.graph_filename)}" ${g.graph_filename === filename ? "selected" : ""}>${esc(g.graph_filename)}</option>`).join("")}</select></div>`)
      + panel("图谱可视", '<div id="kgGraphView" class="kg-view-mount"></div>', "", "仅展示审核通过的实体；关系及两端实体均通过后才显示连线。支持中心实体聚焦、拖拽与缩放", true);
    $("#visualGraphSelect").onchange = (event) => {
      state.visualGraphFilename = event.target.value;
      window.CdssGraphView?.destroy?.();
      renderGraphVisual().catch((error) => toast(error.message, true));
    };
    if (window.CdssGraphView) {
      window.CdssGraphView.destroy?.();
      window.CdssGraphView.mount($("#kgGraphView"), graph, {
        graphFilename: filename, profile: graph.profile, reviewedOnly: true,
        anchorTypes: graph.profile?.anchor_types || ["disease"],
        domain: graph.profile?.domain || graph.domain || "western",
      });
    }
  }

  async function renderGraphClear() {
    const graphs = await api("/api/kg/graphs");
    if (!graphs.length) { pageEl.innerHTML = '<div class="empty">暂无可清除的知识图谱。</div>'; return; }
    const filename = graphs.some((g) => g.graph_filename === state.clearGraphFilename)
      ? state.clearGraphFilename : graphs[0].graph_filename;
    state.clearGraphFilename = filename;
    pageEl.innerHTML = panel("选择清除范围", `
      <div class="field"><label for="clearGraphSelect">目标图谱</label><select id="clearGraphSelect">${graphs.map((g) => `<option value="${esc(g.graph_filename)}" ${g.graph_filename === filename ? "selected" : ""}>${esc(g.graph_filename)}</option>`).join("")}</select></div>
      <div class="inline-controls">
        <label><input type="checkbox" id="clearGraphContent" checked> 清空图谱内容（实体、关系、证据及图谱内溯源）</label>
        <label><input type="checkbox" id="clearGraphRules" checked> 删除绑定此图谱的全部规则</label>
      </div>
      <p>清除范围包含草稿、已审核、已启用、已拒绝和已失效的数据，不按审核状态保留。</p>
      <p>多个指南汇入同一图谱时，将清除该图谱中所有指南汇入的所选内容。</p>
      <p>清空后的图谱会从审核台和图谱列表隐藏。保留指南 PDF、解析结果和知识包供重新导入，已删规则可查历史快照。</p>
      <button id="clearPreviewBtn" class="btn">预览清除范围</button>
    `) + panel("清除预览", '<div id="clearPreviewBody">请先选择范围并预览。</div><button id="clearExecuteBtn" class="btn danger" hidden>执行清除</button>');
    let previewPayload = null;
    const executeButton = $("#clearExecuteBtn");
    const previewBody = $("#clearPreviewBody");
    const previewButton = $("#clearPreviewBtn");
    const selectedPayload = () => ({
      graph_filename: $("#clearGraphSelect").value,
      clear_graph: $("#clearGraphContent").checked,
      clear_rules: $("#clearGraphRules").checked,
    });
    const invalidate = () => {
      previewPayload = null;
      executeButton.hidden = true;
      previewBody.textContent = "清除范围已变更，请重新预览。";
      state.clearGraphFilename = $("#clearGraphSelect").value;
    };
    ["#clearGraphSelect", "#clearGraphContent", "#clearGraphRules"].forEach((id) => $(id).addEventListener("change", invalidate));
    previewButton.onclick = async () => {
      const payload = selectedPayload();
      previewPayload = null;
      executeButton.hidden = true;
      if (!payload.clear_graph && !payload.clear_rules) return toast("至少勾选一项清除内容", true);
      previewButton.disabled = true;
      try {
        const preview = await api("/api/kg/clear", { json: { ...payload, dry_run: true } });
        if (state.route !== "graphClear" || JSON.stringify(payload) !== JSON.stringify(selectedPayload())) return;
        previewPayload = payload;
        previewBody.innerHTML = `<p>目标图谱：<strong>${esc(preview.graph_filename)}</strong></p>
          ${payload.clear_graph ? `<p>将清空实体 ${preview.node_count} 个、关系 ${preview.relation_count} 条，以及该图谱的其他内容。</p>` : '<p>保留图谱内容。</p>'}
          ${payload.clear_rules ? `<p>将删除关联规则 ${preview.rule_ids.length} 条（包含已审核、已启用规则）。</p><div class="mono">${preview.rule_ids.map(esc).join("、") || "无关联规则"}</div>` : '<p>保留关联规则。</p>'}`;
        executeButton.hidden = false;
      } catch (error) { toast(error.message, true); }
      finally { previewButton.disabled = false; }
    };
    executeButton.onclick = async () => {
      if (!previewPayload || JSON.stringify(previewPayload) !== JSON.stringify(selectedPayload())) return invalidate();
      const payload = { ...previewPayload };
      const typed = prompt(`将清除 ${payload.graph_filename} 的所选内容，包含已审核数据。请输入完整图谱文件名确认：`);
      if ((typed || "").trim() !== payload.graph_filename) return toast("文件名不匹配，已取消", true);
      executeButton.disabled = true;
      previewButton.disabled = true;
      try {
        const result = await api("/api/kg/clear", { json: { ...payload, dry_run: false } });
        previewPayload = null;
        executeButton.hidden = true;
        state.reviewGraphOptions = null;
        if (payload.clear_graph && state.reviewFilters?.graph === payload.graph_filename) {
          state.reviewFilters.graph = "";
        }
        if (payload.clear_graph) {
          if (state.autoResult?.graph_filename === payload.graph_filename) state.autoResult = null;
          const index = graphs.findIndex((graph) => graph.graph_filename === payload.graph_filename);
          if (index >= 0) graphs.splice(index, 1);
          const selector = $("#clearGraphSelect");
          selector.innerHTML = graphs.map((graph) => `<option value="${esc(graph.graph_filename)}">${esc(graph.graph_filename)}</option>`).join("");
          state.clearGraphFilename = graphs[0]?.graph_filename || "";
          selector.value = state.clearGraphFilename;
        }
        previewBody.innerHTML = `<p>清除完成：实体 ${payload.clear_graph ? result.node_count : 0} 个、关系 ${payload.clear_graph ? result.relation_count : 0} 条、规则 ${result.cleared_rules} 条。</p>
          <p>备份：${result.backups.map(esc).join("、") || "无"}</p><p>指南原文、解析结果等资料已保留。</p>`;
        toast("图谱清除完成");
        markStale("dashboard", "graph", "graphVisual", "tcmGraph", "review", "rules", "import", "packages");
      } catch (error) { toast(error.message, true); }
      finally { executeButton.disabled = false; previewButton.disabled = !graphs.length; }
    };
  }

  function approvedKnowledgeGraph(graph) {
    const nodes = Object.values(graph.nodes || {}).filter(node => node.review_status === "active");
    const ids = new Set(nodes.map(node => node.id));
    const relations = Object.values(graph.relations || {}).filter(relation => relation.review_status === "active"
      && ids.has(relation.source_node_id) && ids.has(relation.target_node_id));
    const diseaseCodes = new Set(nodes.filter(node => node.node_type === "disease").map(node => node.code));
    const evidences = (graph.evidences || []).filter(item => item.review_status === "active");
    const evidenceIds = new Set(evidences.map(item => item.id));
    const disease_evidence = (graph.disease_evidence || []).filter(item => item.review_status === "active"
      && diseaseCodes.has(item.disease_code) && evidenceIds.has(item.evidence_id));
    const refs = new Set([...nodes, ...relations, ...evidences].flatMap(item => item.source_refs || []));
    return { ...graph, nodes, relations, evidences, disease_evidence,
      diseases: (graph.diseases || []).filter(item => diseaseCodes.has(item.code)),
      sources: (graph.sources || []).filter(item => refs.has(item.chunk_id)),
    };
  }

  function knowledgeGraphOptions(graphs, selected) {
    return [["western", "西医图谱"], ["tcm", "中医图谱"]].map(([domain, label]) => {
      const items = graphs.filter((item) => normalizeDomainClient(item.domain) === domain);
      return items.length ? `<optgroup label="${label}">${items.map((item) =>
        `<option value="${esc(item.graph_filename)}" ${item.graph_filename === selected ? "selected" : ""}>${esc(item.graph_filename)}</option>`
      ).join("")}</optgroup>` : "";
    }).join("");
  }

  function bindKnowledgeGraphSelector(selector, graphs, route) {
    $(selector).onchange = (event) => {
      const selected = graphs.find((item) => item.graph_filename === event.target.value);
      if (!selected) return;
      const tcm = normalizeDomainClient(selected.domain) === "tcm";
      if (tcm) state.tcmGraphFilename = selected.graph_filename;
      else state.graphFilename = selected.graph_filename;
      const next = tcm ? "tcmGraph" : "graph";
      if (next !== route) return navigate(next, {reload: true});
      return (tcm ? renderTcmGraph() : renderGraph()).catch((error) => toast(error.message, true));
    };
  }

  async function renderGraph(options = {}) {
    await loadBase();
    if (!options.preserveTableState) resetPagedTables();
    const graphs = await api("/api/kg/graphs");
    state.graphs = graphs.filter((item) => normalizeDomainClient(item.domain) === "western");
    if (!state.graphs.some((item) => item.graph_filename === state.graphFilename)) {
      state.graphFilename = state.graphs.find((item) => item.graph_filename === "KG-GENERIC_GUIDELINE_V1.json")?.graph_filename || state.graphs[0]?.graph_filename || "";
    }
    if (!state.graphFilename) {
      if (graphs.some((item) => normalizeDomainClient(item.domain) === "tcm")) return navigate("tcmGraph", {reload: true});
      pageEl.innerHTML = '<div class="empty">暂无知识图谱。</div>';
      return;
    }
    const graph = approvedKnowledgeGraph(await api(`/api/kg/graph?graph_filename=${encodeURIComponent(state.graphFilename)}`));
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

    pageEl.innerHTML = `
      ${panel("图谱选择", `
        <div class="inline-controls">
          <div class="field"><label for="graphSelector">知识图谱</label><select id="graphSelector">${knowledgeGraphOptions(graphs, state.graphFilename)}</select></div>
          <div class="field"><label>审核人</label><input id="reviewerName" value="${esc(localStorage.getItem("cdssReviewer") || "")}" placeholder="姓名"></div>
        </div>
      `, "", "仅展示审核通过的知识；关系及两端实体均通过后才显示。待审、拒绝、失效及待审核内容请到审核台处理。")}
      <div class="actions"><button id="openGraphVisual" class="btn">打开图谱可视</button><button id="openGraphClear" class="btn">图谱清除</button></div>
      <div class="grid c4">
        <div class="kpi"><span>疾病</span><strong>${graph.diseases.length}</strong></div>
        <div class="kpi"><span>证据</span><strong>${graph.evidences.length}</strong></div>
        <div class="kpi"><span>治疗方案</span><strong>${treatmentPlans.length}</strong></div>
        <div class="kpi"><span>语义节点</span><strong>${semanticNodes.length}</strong></div>
        <div class="kpi"><span>语义关系</span><strong>${semanticRelations.length}</strong></div>
        <div class="kpi"><span>关联</span><strong>${diseaseLinks.length}</strong></div>
        <div class="kpi"><span>指南出处</span><strong>${graph.sources.length}</strong></div>
      </div>

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
    ["relations", "paths", "nodes", "evidences", "treatmentPlans", "links", "sources"].forEach(bindPagedTable);
    bindKnowledgeGraphSelector("#graphSelector", graphs, "graph");
    $("#reviewerName").onchange = (event) => {
      localStorage.setItem("cdssReviewer", event.target.value.trim());
    };
    $("#openGraphVisual").onclick = () => { state.visualGraphFilename = state.graphFilename; navigate("graphVisual", { reload: true }); };
    $("#openGraphClear").onclick = () => { state.clearGraphFilename = state.graphFilename; navigate("graphClear", { reload: true }); };

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
        markStale("dashboard", "review", "rules");
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
            link_endpoints: true,
            reviewer: currentReviewer(),
            note,
            source_node_id: relation.source_node_id,
            relation_code: relation.relation_code,
            target_node_id: relation.target_node_id,
          },
        });
        toast("关系审核状态已更新");
        markStale("dashboard", "review", "rules");
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
          ${(detail.node.created_at || detail.node.last_import_at) ? `<p class="muted">生成时间：${detail.node.created_at ? `首次导入 ${formatTime(detail.node.created_at)}` : "历史导入"}${detail.node.last_import_at && detail.node.last_import_at !== detail.node.created_at ? ` · 最近导入命中 ${formatTime(detail.node.last_import_at)}` : ""}</p>` : ""}
          <h4>引用本实体的推理规则（${rules.length}）</h4>
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

    pageEl.onclick = (event) => {
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
    resetPagedTables();
    const graphs = await api("/api/kg/graphs");
    const tcmGraphs = graphs.filter((item) => normalizeDomainClient(item.domain) === "tcm");
    if (!tcmGraphs.length) {
      pageEl.innerHTML = panel("中医图谱", '<div class="empty">尚未导入中医知识图谱。请在「指南导入」选择中医模板，或在「知识包」导入。</div>')
        + (graphs.length ? panel("图谱选择", `<select id="tcmGraphSelector" aria-label="知识图谱"><option value="">选择已有图谱</option>${knowledgeGraphOptions(graphs)}</select>`) : "");
      if (graphs.length) bindKnowledgeGraphSelector("#tcmGraphSelector", graphs, "tcmGraph");
      return;
    }
    if (!tcmGraphs.some((item) => item.graph_filename === state.tcmGraphFilename)) {
      state.tcmGraphFilename = (tcmGraphs.find((item) => item.semantic_nodes > 0) || tcmGraphs[0]).graph_filename;
    }
    const filename = state.tcmGraphFilename;
    const graph = approvedKnowledgeGraph(await api(`/api/kg/graph?graph_filename=${encodeURIComponent(filename)}`));
    if (state.route !== "tcmGraph" || state.tcmGraphFilename !== filename) return;
    const {nodes, relations} = graph;
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
      (herbsByFormula[relation.source_node_id] = herbsByFormula[relation.source_node_id] || [])
        .push(index[relation.target_node_id]);
    });
    const formulaRows = [];
    syndromeMethods.forEach((sm) => {
      methodFormulas.filter((mf) => mf.source_node_id === sm.target_node_id).forEach((mf) => {
        const herbs = herbsByFormula[mf.target_node_id] || [];
        formulaRows.push({
          text: [sm.source_node_id, sm.target_node_id, mf.target_node_id].map((id) => index[id]?.name)
            .concat(herbs.map((herb) => herb?.name)).join(" "),
          html: `<tr>
          <td>${label(sm.source_node_id)}</td>
          <td>${label(sm.target_node_id)}</td>
          <td>${label(mf.target_node_id)}</td>
          <td>${herbs.map((herb) => `${esc(herb?.name || "")}${herb?.attributes?.HERB_DOSE ? ` ${esc(herb.attributes.HERB_DOSE)}` : ""}${herb?.attributes?.HERB_ROLE ? `（${esc(herb.attributes.HERB_ROLE)}）` : ""}`).join("、") || "-"}</td>
          <td class="mono">${refs(mf)}</td>
        </tr>`,
        });
      });
    });
    const relationRows = (edges) => edges.map((relation) => {
      const name = relationLabel(relation);
      return {
        text: `${index[relation.source_node_id]?.name} ${name} ${relation.relation_code} ${index[relation.target_node_id]?.name} ${refs(relation)}`,
        values: {relation: relation.relation_code},
        html: `<tr><td>${label(relation.source_node_id)}</td><td><strong>${esc(name)}</strong><div class="mono muted">${esc(relation.relation_code)}</div></td>
          <td>${label(relation.target_node_id)}</td><td class="mono">${refs(relation)}</td></tr>`,
      };
    });
    const relationFilter = (edges) => [{key: "relation", label: "关系类型",
      options: [...new Map(edges.map((edge) => [edge.relation_code, {
        value: edge.relation_code, label: relationLabel(edge),
      }])).values()]}];

    pageEl.innerHTML = `<div class="actions"><button id="openTcmGraphVisual" class="btn">打开图谱可视</button><button id="openTcmGraphClear" class="btn">图谱清除</button></div>
      ${panel("图谱选择", `
        <div class="inline-controls">
          <div class="field"><label for="tcmGraphSelector">知识图谱</label><select id="tcmGraphSelector">${knowledgeGraphOptions(graphs, filename)}</select></div>
        </div>
      `, "", "仅展示审核通过的实体，以及关系和两端实体均已通过的连线。")}
      ${nodes.length ? "" : '<div class="empty">当前图谱暂无审核通过的实体。</div>'}
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

      ${pagedPanel({key: "tcmObservations", title: "辨证链路",
        headers: ["证候", "关系", "症状 / 舌象 / 脉象 / 病机", "指南溯源"],
        items: relationRows(observationRelations), filters: relationFilter(observationRelations),
        placeholder: "搜索证候、观察项、出处…"})}
      ${pagedPanel({key: "tcmFormulas", title: "治法方药链路",
        headers: ["证候", "治法", "方剂", "组成（剂量/君臣佐使）", "指南溯源"],
        items: formulaRows, placeholder: "搜索证候、治法、方剂、中药…"})}
      ${pagedPanel({key: "tcmNodes", title: "语义实体", headers: ["类型", "名称", "编码", "属性", "来源"],
        filters: [{key: "type", label: "实体类型", options: [...new Set(nodes.map((node) => node.node_type))]
          .map((type) => ({value: type, label: typeLabel(type)}))}],
        placeholder: "搜索实体名称、类型、编码…",
        items: nodes.map((node) => ({
          text: `${node.name} ${typeLabel(node.node_type)} ${node.code || ""}`,
          values: {type: node.node_type},
          html: `<tr>
          <td>${esc(typeLabel(node.node_type))}</td>
          <td><strong>${esc(node.name)}</strong></td>
          <td class="mono">${esc(node.code || "编码缺失，需补充")}</td>
          <td class="mono">${esc(Object.entries(node.attributes || {}).map(([key, value]) => `${key}=${value}`).join("；") || "-")}</td>
          <td class="mono">${esc((node.source_refs || []).join(" / ") || "-")}</td>
        </tr>`,
        })),
      })}
      ${pagedPanel({key: "tcmRelations", title: "语义关系",
        headers: ["源节点", "关系", "目标节点", "溯源"], items: relationRows(relations),
        filters: relationFilter(relations), placeholder: "搜索关系、实体、出处…"})}
      ${pagedPanel({key: "tcmSources", title: "指南出处", headers: ["文档 / 章节", "页行", "引文"],
        placeholder: "搜索指南、章节、引文…", items: graph.sources.map((source) => ({
          text: `${source.doc_title} ${source.section} ${source.quote}`,
          html: `<tr><td><strong>${esc(source.doc_title)}</strong><div class="muted">${esc(source.section)}</div></td><td class="mono nowrap">p${source.page_start}:l${source.line_start}-${source.line_end}</td><td><div class="quote">${esc(source.quote)}</div></td></tr>`,
        })),
      })}
    `;
    $("#openTcmGraphVisual").onclick = () => { state.visualGraphFilename = state.tcmGraphFilename; navigate("graphVisual", { reload: true }); };
    $("#openTcmGraphClear").onclick = () => { state.clearGraphFilename = state.tcmGraphFilename; navigate("graphClear", { reload: true }); };
    ["tcmObservations", "tcmFormulas", "tcmNodes", "tcmRelations", "tcmSources"].forEach(bindPagedTable);
    bindKnowledgeGraphSelector("#tcmGraphSelector", graphs, "tcmGraph");
  }

  function findingLabel(code) {
    const item = state.terms.exam_finding[code] || state.terms.diagnostic_evidence[code];
    return item && item.name ? `${item.name}（${code}）` : code;
  }

  function conditionText(node) {
    if (!node) return "-";
    if (node.type === "count_group") return `至少满足 ${node.min_count} 项（${(node.children || []).map(conditionText).join("；")}）`;
    if (node.type === "weighted_group") {
      const threshold = node.threshold == null ? "阈值待确认" :
        `达到 ${(node.threshold * 100).toLocaleString("zh-CN", {maximumFractionDigits: 4})}%`;
      return `加权评分${threshold}（${(node.children || []).map((child) =>
        `${conditionText(child)}：权重 ${child.weight ?? 1}`).join("；")}）`;
    }
    if (node.type === "group") return `(${(node.children || []).map(conditionText).join(` ${node.operator || "[组合待确认]"} `)})`;
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
      clinical_attributes: JSON.parse(JSON.stringify(patient.clinical_attributes || {})),
      diagnoses: JSON.parse(JSON.stringify(patient.diagnoses || [])),
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
      { name: "present", label: "是否存在", type: "select", options: [{ value: "", label: "未判定" }, { value: "true", label: "存在" }, { value: "false", label: "不存在" }] },
      { name: "value", label: "检查测量值", type: "number", step: "any" },
      { name: "unit", label: "测量单位", placeholder: "填写报告中的单位" },
      { name: "category", label: "分级/定性结果", placeholder: "填写结构化结果" },
      { name: "method", label: "检查方法", placeholder: "与本院标准一致" },
      { name: "report_text", label: "报告原文（供查阅）", placeholder: "原文不会自动判定为阳性" },
      { name: "detail_summary", label: "细节描述", placeholder: "II III aVF 导联抬高 0.3mV" },
    ],
    vitals: [
      { name: "vital_code", label: "体征编码", placeholder: "sbp" },
      { name: "value", label: "结果值", type: "number", step: "0.1" },
      { name: "unit", label: "单位", placeholder: "mmHg" },
    ],
  };
  function readCaseMatrix(kind, mapper) {
    return [...pageEl.querySelectorAll(`.matrix-row[data-kind="${kind}"]`)].map((row) => {
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
      clinical_attributes: {...(state.caseRecord?.clinical_attributes || {}),
        weight_kg: numberValue($("#caseWeight")?.value || ""), egfr: numberValue($("#caseEgfr")?.value || ""),
        medications_reviewed: $("#caseMedicationsReviewed")?.checked || false,
        allergies_reviewed: $("#caseAllergiesReviewed")?.checked || false,
        prior_treatment_response: $("#casePriorResponse")?.value.trim() || null},
      diagnoses: $("#caseWorkingDiagnosis")?.value.trim() ? [{code:$("#caseWorkingDiagnosis").value.trim(), certainty:$("#caseDiagnosisCertainty").value,clinical_status:"active",source:"clinician"}] : (state.caseRecord?.diagnoses || []),
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
        present: raw.present === "" ? null : raw.present === "true", value: numberValue(raw.value), unit: raw.unit.trim(),
        category: raw.category.trim() || null, method: raw.method.trim(), report_text: raw.report_text.trim(),
        detail: raw.detail_summary.trim() ? { summary: raw.detail_summary.trim() } : null,
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
      clinical_attributes: record.clinical_attributes || {}, diagnoses: record.diagnoses || [],
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
    const clinicalFields = {caseWeight: record.clinical_attributes?.weight_kg ?? "", caseEgfr: record.clinical_attributes?.egfr ?? "", casePriorResponse: record.clinical_attributes?.prior_treatment_response || "", caseWorkingDiagnosis: record.diagnoses?.[0]?.code || "", caseDiagnosisCertainty: record.diagnoses?.[0]?.certainty || "suspected"};
    Object.entries(clinicalFields).forEach(([id,value])=>{const el=$("#"+id);if(el)el.value=value;});
    if ($("#caseMedicationsReviewed")) $("#caseMedicationsReviewed").checked = record.clinical_attributes?.medications_reviewed === true;
    if ($("#caseAllergiesReviewed")) $("#caseAllergiesReviewed").checked = record.clinical_attributes?.allergies_reviewed === true;
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
  const REVIEW_TYPE_LABELS = { graph_node: "实体", graph_relation: "关系", rule: "规则", package: "知识包" };
  const REVIEW_TABS = [
    ["bydisease", "疾病审核工作台"], ["graph", "实体审核"], ["relations", "关系审核"], ["bases", "诊疗依据审核"], ["rules", "规则审核"], ["standards", "判定标准审核"], ["blocked", "被阻断"],
    ["revisions", "待审修订"], ["active", "已通过"], ["orphan", "孤儿条目"],
    ["dupes", "疑似重复实体"],
    ["audit", "审核记录"],
  ];
  const REJECT_REASONS = ["编码错误", "证据不足", "关系方向错误", "重复条目", "超出指南范围", "内容过期", "其他"];
  const RELATION_LABELS = {
    DISEASE_HAS_LAB: "需开展检验", LAB_HAS_DIAGNOSTIC_EVIDENCE: "检验形成证据",
    DISEASE_HAS_DIAGNOSTIC_EVIDENCE: "构成诊断依据", DISEASE_HAS_EXAM: "需开展检查",
    EXAM_HAS_DIAGNOSTIC_EVIDENCE: "检查形成证据", DISEASE_HAS_SYMPTOM: "症见",
    SYNDROME_HAS_SYMPTOM: "症见", SYNDROME_HAS_TONGUE: "舌象", SYNDROME_HAS_PULSE: "脉象",
    SYNDROME_HAS_SIGN: "体征", SYNDROME_HAS_PATHOGENESIS: "病机",
    SYNDROME_TREATED_BY_METHOD: "治法", METHOD_HAS_FORMULA: "选方", FORMULA_CONTAINS_HERB: "组成",
    TREATMENT_PLAN_INCLUDES_MEDICATION: "含药品", TREATMENT_PLAN_INCLUDES_INTERVENTION: "含干预操作",
    TREATMENT_PLAN_HAS_MONITORING: "监测要求", TREATMENT_PLAN_HAS_CONTRAINDICATION: "禁忌",
    TREATMENT_PLAN_MATERIALIZED_AS: "物化为医嘱组", ORDER_SET_INCLUDES_ITEM: "含医嘱项",
    PHASE_USES_ORDER_SET: "使用医嘱组", DISEASE_HAS_CARE_PATHWAY: "临床路径",
    CARE_PATHWAY_HAS_PHASE: "路径阶段", PATHOLOGY_HAS_DIAGNOSTIC_EVIDENCE: "病理形成证据",
    BIOMARKER_HAS_DIAGNOSTIC_EVIDENCE: "生物标志物形成证据", MOLECULAR_TEST_HAS_DIAGNOSTIC_EVIDENCE: "分子检测形成证据",
    DISEASE_HAS_PATHOLOGY: "病理", DISEASE_HAS_HISTOLOGY: "组织学", DISEASE_HAS_STAGE: "分期",
    DISEASE_HAS_RISK_FACTOR: "危险因素", DISEASE_HAS_BIOMARKER: "生物标志物", DISEASE_HAS_MOLECULAR_TEST: "分子检测",
    DISEASE_HAS_SIGN: "体征", DISEASE_HAS_MONITORING: "监测要求", DISEASE_HAS_CONTRAINDICATION: "禁忌",
  };
  const FIELD_LABELS = {
    node_type: "类型", code: "编码", name: "名称", review_status: "审核状态",
    source_node_id: "源节点", relation_code: "关系编码", relation_name: "关系名称",
    target_node_id: "目标节点", source_refs: "指南溯源",
  };
  const FLAG_LABELS = {
    endpoint_inactive: "端点未通过", no_source_refs: "无溯源",
    direction_suspect: "方向可疑", self_loop: "自环", duplicate_pair: "疑似重复关系（同一对实体有多种连线）",
  };
  function flagChipsHtml(item) {
    return (item.flags || []).map((flag) => `<span class="flag-chip" title="${esc(flag.message)}">⚠ ${esc(FLAG_LABELS[flag.code] || flag.code)}</span>`).join(" ");
  }
  function relationMatrixHtml(items) {
    const relItems = items.filter((item) => item.item_type === "graph_relation");
    if (!relItems.length) return "";
    const statuses = ["draft", "active", "rejected", "retired"];
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
  // 生成时间列：created_at 是节点/关系首次由指南导入生成的时间，
  // last_import_at 是最近一次导入再次命中它的时间。last_import_at 与
  // 图级 last_import_at 相同说明是「本次导入」产生的变化。
  function generationCellHtml(item) {
    if (item.item_type !== "graph_node" && item.item_type !== "graph_relation") {
      return '<td class="nowrap muted">-</td>';
    }
    const parts = [];
    if (item.created_at) parts.push(`创建 ${formatTime(item.created_at)}`);
    if (item.last_import_at && item.last_import_at !== item.created_at) parts.push(`最近导入 ${formatTime(item.last_import_at)}`);
    const thisImport = item.last_import_at && item.graph_last_import_at && item.last_import_at >= item.graph_last_import_at;
    const badge = thisImport && item.created_at && item.created_at >= item.graph_last_import_at
      ? '<span class="badge active" title="本次指南导入新生成">新</span>'
      : (thisImport && item.created_at ? '<span class="badge" title="本次指南导入再次引用">更</span>' : "");
    const text = item.created_at ? esc(shortTime(item.created_at)) : "-";
    return `<td class="nowrap muted" title="${esc(parts.join(" · ") || "历史导入，无时间记录")}">${badge ? `${badge} ` : ""}${text}</td>`;
  }
  function reviewReviewer() {
    return (($("#reviewCenterReviewer")?.value || "") || localStorage.getItem("cdssReviewer") || "").trim() || "未署名";
  }
  // 联动审核语义：通过关系时两端 draft/stale 实体随关系一并通过（后端
  // link_endpoints）。真正挡住关系的只剩被拒绝/已失效/缺失的端点——那是
  // 明确的人工决定或数据缺口，必须先恢复/补全实体，不能随关系静默激活。
  function relationEndpointBlocked(item) {
    if (item.item_type !== "graph_relation") return false;
    return [item.source_status, item.target_status].some((status) =>
      ["rejected", "retired", "missing"].includes(status)
    );
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
          link_endpoints: true,
        },
      });
    } else if (item.item_type === "rule") {
      await api(`/api/rules/${encodeURIComponent(item.rule_id)}/review`, { json: { action, reviewer, note } });
    } else {
      throw new Error("该条目类型暂不支持单条审核操作");
    }
    // 审核动作影响概览统计、图谱节点状态与规则队列
    markStale("dashboard", "graph", "graphVisual", "rules");
  }

  async function quickReview(item, action) {
    try {
      if (action === "approve" && relationEndpointBlocked(item)) {
        const sourceBlocked = ["rejected", "retired", "missing"].includes(item.source_status);
        const nodeId = sourceBlocked ? item.source_node_id : item.target_node_id;
        toast("该关系的端点实体被拒绝、失效或缺失，请先恢复/补全该实体再审核关系", true);
        if (nodeId) await loadReviewDetail({
          item_type: "graph_node", node_id: nodeId,
          graph_filename: item.graph_filename, status: sourceBlocked ? item.source_status : item.target_status,
        });
        return;
      }
      await submitReviewAction(item, action, "");
      toast(`${ACTION_DONE_LABELS[action] || "已处理"}：${reviewItemTitle(item)}`);
      await renderReviewCenter();
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
      ${action === "reject" && items.every((item) => item.approves_endpoints) ? '<p class="muted">拒绝只作用于所选辨证分型关系。疾病、证候实体及其他分型关系保留各自的审核状态。</p>' : ""}
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

  async function openNodeNameEditor(item, { startWithAi = false, initialPrompt = "", suggestedName = "" } = {}) {
    const dialog = $("#editorDialog");
    const save = $("#editorSave");
    save.hidden = false;
    save.textContent = "保存修改";
    $("#editorBody").innerHTML = '<div class="empty">正在加载实体…</div>';
    $("#editorCancel").onclick = () => dialog.close();
    dialog.showModal();
    try {
      const detail = await api(`/api/kg/nodes/${encodeURIComponent(item.node_id)}?graph_filename=${encodeURIComponent(item.graph_filename)}`);
      const node = detail.node || {};
      $("#editorBody").innerHTML = `
        <h3>修正实体名称</h3>
        <div class="result-banner warning"><strong>编码保持不变</strong><p>修改名称后，已通过的实体会回到“待审核”，相关关系也会重新标记为待审核。保存后仍需人工点击通过。</p></div>
        <div class="field"><label>稳定编码</label><input value="${esc(node.code || node.concept_code || item.node_id)}" readonly></div>
        <div class="field"><label>当前名称</label><input value="${esc(node.name || item.name || "")}" readonly></div>
        <div class="field"><label>修正后的中文名称</label><input id="nodeCorrectedName" maxlength="200" value="${esc(suggestedName || node.name || item.name || "")}" placeholder="例如：血乳酸阳性"></div>
        <div class="detail-section">
          <h4>让 AI 给出修正建议（可选）</h4>
          <p class="muted">写明希望如何纠正。AI 只填入候选名称，不会自动保存或通过。</p>
          <div class="field"><label>提示词</label><textarea id="nodeCorrectionPrompt" maxlength="2000" rows="3" placeholder="例如：结合实体编码和属性，改成规范中文医学名称；保留阳性结果含义">${esc(initialPrompt)}</textarea></div>
          <button type="button" class="btn" id="nodeAiSuggest">生成修正建议</button>
          <div id="nodeAiSuggestion" class="muted"></div>
        </div>
        <div class="field"><label>修改说明</label><textarea id="nodeCorrectionNote" rows="2" placeholder="例如：英文候选名改为规范中文表述"></textarea></div>`;
      const suggest = $("#nodeAiSuggest");
      suggest.onclick = async () => {
        suggest.disabled = true;
        suggest.textContent = "AI 修正中…";
        $("#nodeAiSuggestion").textContent = "正在根据编码、类型和属性生成建议…";
        try {
          const result = await api(`/api/kg/nodes/${encodeURIComponent(item.node_id)}/ai-correction?graph_filename=${encodeURIComponent(item.graph_filename)}`, {
            json: { prompt: $("#nodeCorrectionPrompt").value.trim() },
          });
          $("#nodeCorrectedName").value = result.suggested_name;
          const confidence = Number.isFinite(Number(result.confidence)) ? ` · 置信度 ${Math.round(Number(result.confidence) * 100)}%` : "";
          $("#nodeAiSuggestion").innerHTML = `<div class="result-banner ${result.needs_human_review ? "warning" : "ok"}"><strong>建议：${esc(result.suggested_name)}</strong><p>${esc(result.reason || "请人工核对后保存")}${confidence}</p></div>`;
          $("#nodeCorrectedName").focus();
          $("#nodeCorrectedName").select();
        } catch (error) {
          $("#nodeAiSuggestion").textContent = `生成失败：${error.message}`;
          toast(error.message, true);
        } finally {
          suggest.disabled = false;
          suggest.textContent = "重新生成建议";
        }
      };
      save.onclick = async () => {
        const name = $("#nodeCorrectedName").value.trim();
        if (!name) { toast("请填写修正后的实体名称", true); return; }
        save.disabled = true;
        save.textContent = "保存中…";
        try {
          await api(`/api/kg/nodes/${encodeURIComponent(item.node_id)}?graph_filename=${encodeURIComponent(item.graph_filename)}`, {
            method: "PUT",
            json: { name, reviewer: reviewReviewer(), note: $("#nodeCorrectionNote").value.trim() || "人工修正实体名称" },
          });
          dialog.close();
          markStale("dashboard", "graph", "graphVisual", "rules");
          toast(`实体名称已修改为“${name}”，请重新审核`);
          await renderReviewCenter();
        } catch (error) { toast(error.message, true); }
        finally { save.disabled = false; save.textContent = "保存修改"; }
      };
      if (startWithAi) {
        $("#nodeCorrectionPrompt").focus();
        $("#nodeCorrectionPrompt").scrollIntoView({ block: "center" });
      } else {
        $("#nodeCorrectedName").focus();
        $("#nodeCorrectedName").select();
      }
    } catch (error) {
      $("#editorBody").innerHTML = `<div class="empty">实体加载失败：${esc(error.message)}</div>`;
      save.hidden = true;
    }
    dialog.onclose = () => {
      save.hidden = false;
      save.disabled = false;
      save.textContent = "保存";
      dialog.onclose = null;
    };
  }

  function relationAiHasCorrection(result) {
    return Boolean(
      result?.suggested_source_name || result?.suggested_target_name ||
      result?.suggested_relation_code || result?.reverse_direction
    );
  }

  async function applyRelationAiCorrection(item, result) {
    const response = await api(`/api/kg/relation-ai-correction?graph_filename=${encodeURIComponent(item.graph_filename)}`, {
      json: {
        source_node_id: item.source_node_id,
        relation_code: item.relation_code,
        target_node_id: item.target_node_id,
        reviewer: reviewReviewer(),
        note: `[AI审核建议] ${result.summary || "采纳结构化修正建议"}`,
        suggested_source_name: result.suggested_source_name || "",
        suggested_target_name: result.suggested_target_name || "",
        suggested_relation_code: result.suggested_relation_code || "",
        reverse_direction: Boolean(result.reverse_direction),
      },
    });
    if (result.suggested_source_name) item.source_name = result.suggested_source_name;
    if (result.suggested_target_name) item.target_name = result.suggested_target_name;
    if (result.reverse_direction) {
      [item.source_node_id, item.target_node_id] = [item.target_node_id, item.source_node_id];
      [item.source_name, item.target_name] = [item.target_name, item.source_name];
      [item.source_status, item.target_status] = [item.target_status, item.source_status];
      [item.source_node_type, item.target_node_type] = [item.target_node_type, item.source_node_type];
    }
    if (result.suggested_relation_code) item.relation_code = result.suggested_relation_code;
    item.approves_endpoints = response.approves_endpoints;
    item.item_id = response.relation_id || item.item_id;
    item.status = "draft";
    markStale("dashboard", "graph", "graphVisual", "rules");
    return response;
  }

  function openRelationAiReview(item, { initialPrompt = "" } = {}) {
    const dialog = $("#editorDialog");
    const save = $("#editorSave");
    save.hidden = false;
    save.textContent = "关闭";
    $("#editorCancel").onclick = () => dialog.close();
    $("#editorBody").innerHTML = `
      <h3>AI 审核关系</h3>
      <div class="quote">${esc(item.source_name || item.source_node_id)} → ${esc(relationLabel(item))} → ${esc(item.target_name || item.target_node_id)}</div>
      <p class="muted">AI 会核对实体类型、关系方向、医学语义和指南原文。结论仅作建议，不会自动通过、拒绝或修改图谱。</p>
      <div class="field"><label>补充审核或修正要求（可选）</label><textarea id="relationAiPrompt" maxlength="2000" rows="3" placeholder="例如：重点核对原文是否明确要求开展该检查；将全英文实体名改成规范中文">${esc(initialPrompt)}</textarea></div>
      <div class="actions"><button type="button" class="btn primary" id="runRelationAiReview">开始 AI 审核</button><button type="button" class="btn danger" id="stopRelationAiReview" hidden>停止</button></div>
      <div id="relationAiResult" class="detail-section"><div class="muted">尚未开始审核</div></div>`;
    save.onclick = () => dialog.close();
    const run = $("#runRelationAiReview");
    const stop = $("#stopRelationAiReview");
    let controller = null;
    stop.onclick = () => {
      controller?.abort();
      stop.disabled = true;
      stop.textContent = "正在停止…";
    };
    run.onclick = async () => {
      controller = new AbortController();
      run.disabled = true;
      run.textContent = "AI 审核中…";
      stop.hidden = false;
      stop.disabled = false;
      stop.textContent = "停止";
      $("#relationAiResult").innerHTML = '<div class="empty">正在核对关系方向、语义和指南原文…</div>';
      try {
        const result = await api(`/api/kg/relation-ai-review?graph_filename=${encodeURIComponent(item.graph_filename)}`, {
          json: {
            source_node_id: item.source_node_id,
            relation_code: item.relation_code,
            target_node_id: item.target_node_id,
            prompt: $("#relationAiPrompt").value.trim(),
          },
          signal: controller.signal,
        });
        const labels = {
          approve: ["建议通过", "ok"], reject: ["建议拒绝", "danger"],
          revise: ["建议修正", "warning"], insufficient_evidence: ["证据不足", "warning"],
          precheck: ["模型知识预审：未发现明显问题", "warning"],
        };
        const [label, tone] = labels[result.assessment] || [result.assessment, "warning"];
        const issues = (result.issues || []).map((issue) => `<li><strong>${esc(issue.area || "问题")}</strong>：${esc(issue.problem || "-")}<br><span class="muted">依据：${esc(issue.evidence || "未提供")}</span><br>建议：${esc(issue.suggestion || "人工复核")}</li>`).join("");
        const correction = [
          result.suggested_relation_code ? `关系类型：${result.suggested_relation_code}` : "",
          result.suggested_direction ? `方向：${result.suggested_direction}` : "",
          result.reverse_direction ? "结构化操作：反转关系方向" : "",
          result.suggested_source_name ? `起点实体名称：${result.suggested_source_name}` : "",
          result.suggested_target_name ? `目标实体名称：${result.suggested_target_name}` : "",
          result.suggested_statement ? `推荐表述：${result.suggested_statement}` : "",
        ].filter(Boolean);
        const checklist = (result.reviewer_checklist || []).map((entry) => `<li>${esc(entry)}</li>`).join("");
        const confidence = Number.isFinite(Number(result.confidence)) ? `${Math.round(Number(result.confidence) * 100)}%` : "-";
        const canReject = ["draft", "active"].includes(item.status || "draft");
        const canApprove = ["draft", "stale"].includes(item.status || "draft");
        const endpointsBlocked = relationEndpointBlocked(item);
        const controls = [
          canApprove ? `<button type="button" class="btn primary" data-single-ai-approve ${result.assessment === "approve" ? "" : "hidden"} ${endpointsBlocked ? "disabled title=\"请先恢复端点实体\"" : ""}>${endpointsBlocked ? "端点未通过" : "按建议通过"}</button>` : "",
          ["reject", "revise", "insufficient_evidence"].includes(result.assessment) && canReject ? `<button type="button" class="btn danger" data-single-ai-reject>${item.status === "active" ? "标记失效" : (result.assessment === "reject" ? "按建议拒绝" : (result.assessment === "insufficient_evidence" ? "因证据不足拒绝" : "拒绝此关系"))}</button>` : "",
          result.assessment === "revise" && relationAiHasCorrection(result) ? '<button type="button" class="btn primary" data-single-ai-apply>一键应用修正</button>' : "",
        ].filter(Boolean).join("");
        $("#relationAiResult").innerHTML = `
          <div class="result-banner ${tone}"><strong>${esc(label)}</strong><p>${esc(result.summary || "请人工核对")}</p></div>
          <div class="muted">模型：${esc(result.model || "-")} · 置信度：${confidence} · 审核依据：${result.source_verified ? "已读取指南原文" : "模型知识预审，未核验指南原文"}</div>
          ${issues ? `<h4>发现的问题与修正建议</h4><ul class="issue-list">${issues}</ul>` : '<p class="muted">AI 未列出具体问题。</p>'}
          ${correction.length ? `<h4>建议修正</h4><div class="quote">${correction.map(esc).join("<br>")}</div>` : ""}
          ${checklist ? `<h4>人工复核清单</h4><ul>${checklist}</ul>` : ""}
          ${!result.source_verified ? '<div class="result-banner warning">缺少原文：以下是模型知识预审，不能证明关系来自指定指南。请补充原文并人工核对后再通过。</div>' : ""}
          ${controls ? `<div class="actions">${controls}</div>` : ""}
          <div class="result-banner warning"><strong>仍需人工决定</strong><p>请根据上方建议使用本页的“通过”或“拒绝”；AI 不会替代审核人操作。</p></div>`;
        $("#relationAiResult").querySelector("[data-single-ai-approve]")?.addEventListener("click", async (event) => {
          const button = event.currentTarget;
          if (relationEndpointBlocked(item)) { toast("请先恢复端点实体", true); return; }
          if (!confirm("确认根据 AI 建议通过这条关系？")) return;
          button.disabled = true;
          button.textContent = "提交中…";
          try {
            await submitReviewAction(item, "approve", `[AI审核建议] ${result.summary || "建议通过"}`);
            item.status = "active";
            button.textContent = "已通过";
          } catch (error) { button.disabled = false; button.textContent = "按建议通过"; toast(error.message, true); }
        });
        $("#relationAiResult").querySelector("[data-single-ai-reject]")?.addEventListener("click", async (event) => {
          const button = event.currentTarget;
          const action = item.status === "active" ? "retire" : "reject";
          if (!confirm(`确认根据 AI 建议${action === "retire" ? "标记失效" : "拒绝"}这条关系？`)) return;
          button.disabled = true;
          try {
            const reason = result.assessment === "insufficient_evidence" ? "[证据不足]" : "[AI审核建议]";
            await submitReviewAction(item, action, `${reason} ${result.summary || "建议拒绝"}`);
            item.status = action === "retire" ? "retired" : "rejected";
            button.textContent = action === "retire" ? "已标记失效" : "已拒绝";
          } catch (error) { button.disabled = false; toast(error.message, true); }
        });
        $("#relationAiResult").querySelector("[data-single-ai-apply]")?.addEventListener("click", async (event) => {
          const button = event.currentTarget;
          if (!confirm("确认一键应用 AI 给出的实体名称、关系类型及方向修正？应用后关系将回到待审核。")) return;
          button.disabled = true;
          button.textContent = "应用中…";
          try {
            const applied = await applyRelationAiCorrection(item, result);
            button.textContent = `已应用 ${applied.changes.length} 项修正`;
            const approve = $("#relationAiResult").querySelector("[data-single-ai-approve]");
            if (approve) {
              approve.hidden = false;
              approve.disabled = relationEndpointBlocked(item);
              approve.textContent = relationEndpointBlocked(item) ? "端点被拒/失效" : "通过修正后的关系";
            }
          } catch (error) { button.disabled = false; button.textContent = "一键应用修正"; toast(error.message, true); }
        });
      } catch (error) {
        const stopped = error.name === "AbortError";
        $("#relationAiResult").innerHTML = `<div class="result-banner ${stopped ? "warning" : "danger"}"><strong>${stopped ? "已停止" : "AI 审核失败"}</strong><p>${stopped ? "本次请求已停止，可修改提示词后继续审核。" : esc(error.message)}</p></div>`;
      } finally {
        controller = null;
        run.disabled = false;
        run.textContent = "重新审核";
        stop.hidden = true;
      }
    };
    dialog.onclose = () => {
      save.hidden = false;
      save.disabled = false;
      save.textContent = "保存";
      dialog.onclose = null;
    };
    dialog.showModal();
  }

  function openBatchRelationAiReview(items, sectionName = "本组关系") {
    const queue = (items || []).filter((item) => item?.source_node_id && item?.target_node_id);
    if (!queue.length) { toast("本组没有可审核的关系", true); return; }
    const dialog = $("#editorDialog");
    const save = $("#editorSave");
    save.hidden = false;
    save.textContent = "关闭";
    $("#editorCancel").onclick = () => dialog.close();
    $("#editorBody").innerHTML = `
      <h3>批量 AI 审核：${esc(sectionName)}</h3>
      <p class="muted">将逐条核对 ${queue.length} 条关系的端点、方向、语义和指南原文。AI 只生成质量报告，不自动改变审核状态。</p>
      <div class="field"><label>统一审核要求（可选）</label><textarea id="batchRelationAiPrompt" maxlength="2000" rows="3" placeholder="例如：重点检查是否把仅提及的检查误判为必须开展"></textarea></div>
      <div class="actions"><button type="button" class="btn primary" id="runBatchRelationAi">开始批量审核</button><button type="button" class="btn danger" id="stopBatchRelationAi" hidden>停止</button></div>
      <div id="batchRelationAiSummary" class="detail-section"><div class="muted">尚未开始审核</div></div>
      <div class="detail-section"><table><thead><tr><th>关系</th><th>AI 结论</th><th>摘要 / 修正建议</th></tr></thead><tbody>
        ${queue.map((item, index) => `<tr data-batch-ai-row="${index}"><td>${esc(item.source_name || item.source_node_id)} → ${esc(relationLabel(item))} → ${esc(item.target_name || item.target_node_id)}</td><td class="batch-ai-status">待执行</td><td class="batch-ai-advice muted">-</td></tr>`).join("")}
      </tbody></table></div>`;
    save.onclick = () => dialog.close();
    const run = $("#runBatchRelationAi");
    const stop = $("#stopBatchRelationAi");
    let cancelled = false;
    let stopped = false;
    let controller = null;
    const completed = new Set();
    const counts = { approve: 0, reject: 0, revise: 0, insufficient_evidence: 0, failed: 0 };
    stop.onclick = () => {
      stopped = true;
      controller?.abort();
      stop.disabled = true;
      stop.textContent = "正在停止…";
    };
    run.onclick = async () => {
      cancelled = false;
      stopped = false;
      if (completed.size === queue.length) {
        completed.clear();
        Object.keys(counts).forEach((key) => { counts[key] = 0; });
        $("#editorBody").querySelectorAll("[data-batch-ai-row]").forEach((row) => {
          row.querySelector(".batch-ai-status").textContent = "待执行";
          row.querySelector(".batch-ai-advice").innerHTML = "-";
          row.querySelector(".batch-ai-advice").classList.add("muted");
        });
      }
      run.disabled = true;
      save.disabled = true;
      stop.hidden = false;
      stop.disabled = false;
      stop.textContent = "停止";
      const labels = {
        approve: ["建议通过", "active"], reject: ["建议拒绝", "rejected"],
        revise: ["建议修正", "draft"], insufficient_evidence: ["证据不足", "draft"],
        precheck: ["知识预审，原文未核验", "draft"],
      };
      const prompt = $("#batchRelationAiPrompt").value.trim();
      for (let index = 0; index < queue.length; index += 1) {
        if (completed.has(index)) continue;
        if (cancelled || stopped || !dialog.open) break;
        const item = queue[index];
        const row = $("#editorBody").querySelector(`[data-batch-ai-row="${index}"]`);
        row.querySelector(".batch-ai-status").textContent = `审核中 ${index + 1}/${queue.length}`;
        $("#batchRelationAiSummary").innerHTML = `<div class="muted">正在审核 ${index + 1} / ${queue.length} 条，请勿关闭窗口…</div>`;
        try {
          controller = new AbortController();
          const result = await api(`/api/kg/relation-ai-review?graph_filename=${encodeURIComponent(item.graph_filename)}`, {
            json: {
              source_node_id: item.source_node_id,
              relation_code: item.relation_code,
              target_node_id: item.target_node_id,
              prompt,
            },
            signal: controller.signal,
          });
          controller = null;
          if (cancelled || stopped || !dialog.open) break;
          counts[result.assessment] = (counts[result.assessment] || 0) + 1;
          completed.add(index);
          const [label, status] = labels[result.assessment] || [result.assessment, "draft"];
          row.querySelector(".batch-ai-status").innerHTML = miniStatus(status).replace(REVIEW_LABELS[status] || status, label);
          let currentResult = result;
          const issueLines = (result.issues || []).map((issue) => `${issue.area || "问题位置"}：${issue.problem || "需复核"}；建议：${issue.suggestion || "人工核对"}`);
          const suggestions = [
            result.source_verified ? result.summary : "模型知识预审，未核验指南原文；请补充原文并人工核对。" ,
            result.summary,
            ...issueLines,
            result.suggested_statement ? `推荐表述：${result.suggested_statement}` : "",
            result.suggested_relation_code ? `建议关系：${result.suggested_relation_code}` : "",
            result.suggested_direction ? `建议方向：${result.suggested_direction}` : "",
            result.suggested_source_name ? `建议起点名称：${result.suggested_source_name}` : "",
            result.suggested_target_name ? `建议目标名称：${result.suggested_target_name}` : "",
          ].filter(Boolean);
          const canReject = ["draft", "active"].includes(item.status || "draft");
          const canApprove = ["draft", "stale"].includes(item.status || "draft");
          const endpointsBlocked = relationEndpointBlocked(item);
          const actionButtons = [
            canApprove ? `<button type="button" class="btn sm primary" data-ai-direct-approve ${result.assessment === "approve" ? "" : "hidden"} ${endpointsBlocked ? "disabled title=\"请先恢复端点实体\"" : ""}>${endpointsBlocked ? "端点未通过" : "按建议通过"}</button>` : "",
            canReject ? `<button type="button" class="btn sm danger" data-ai-direct-reject ${["reject", "revise", "insufficient_evidence"].includes(result.assessment) ? "" : "hidden"}>${item.status === "active" ? "标记失效" : (result.assessment === "reject" ? "按建议拒绝" : (result.assessment === "insufficient_evidence" ? "因证据不足拒绝" : "拒绝此关系"))}</button>` : "",
            `<button type="button" class="btn sm primary" data-ai-apply ${result.assessment === "revise" ? "" : "hidden"} ${relationAiHasCorrection(result) ? "" : "disabled"}>${relationAiHasCorrection(result) ? "一键应用修正" : "暂无可执行修正"}</button>`,
            `<button type="button" class="btn sm" data-ai-refine ${result.assessment === "revise" ? "" : "hidden"}>输入提示词进一步修正</button>`,
          ].filter(Boolean).join("");
          const advice = row.querySelector(".batch-ai-advice");
          advice.innerHTML = `${suggestions.length ? `<ul data-ai-suggestion-list>${suggestions.map((line) => `<li>${esc(line)}</li>`).join("")}</ul>` : '<ul data-ai-suggestion-list><li>请人工复核</li></ul>'}${actionButtons ? `<div class="actions">${actionButtons}</div>` : ""}${result.assessment === "revise" ? '<div data-ai-refine-panel hidden><textarea rows="2" maxlength="2000" placeholder="例如：把全英文名称转换为规范中文，并给出可一键应用的结构化修正"></textarea><button type="button" class="btn sm" data-ai-rerun-one>重新审核本条</button></div>' : ""}`;
          advice.classList.remove("muted");
          advice.querySelector("[data-ai-direct-approve]")?.addEventListener("click", async (event) => {
            const button = event.currentTarget;
            if (relationEndpointBlocked(item)) { toast("请先恢复端点实体", true); return; }
            if (!confirm(`确认根据 AI 建议通过这条关系？\n\n${currentResult.summary || "AI 建议通过"}`)) return;
            button.disabled = true;
            button.textContent = "提交中…";
            try {
              await submitReviewAction(item, "approve", `[AI审核建议] ${currentResult.summary || "建议通过"}`);
              item.status = "active";
              button.textContent = "已通过";
              row.querySelector(".batch-ai-status").innerHTML = miniStatus("active");
            } catch (error) {
              button.disabled = false;
              button.textContent = "按建议通过";
              toast(error.message, true);
            }
          });
          advice.querySelector("[data-ai-direct-reject]")?.addEventListener("click", async (event) => {
            const button = event.currentTarget;
            const action = item.status === "active" ? "retire" : "reject";
            const verb = action === "retire" ? "标记失效" : "拒绝";
            if (!confirm(`确认${verb}这条关系？\n\n${currentResult.summary || "AI 审核建议"}`)) return;
            button.disabled = true;
            button.textContent = "提交中…";
            try {
              const reason = currentResult.assessment === "insufficient_evidence" ? "[证据不足]" : "[AI审核建议]";
              await submitReviewAction(item, action, `${reason} ${currentResult.summary || "建议拒绝"}`);
              item.status = action === "retire" ? "retired" : "rejected";
              button.textContent = action === "retire" ? "已标记失效" : "已拒绝";
              toast(`${action === "retire" ? "已失效" : "已拒绝"}：${reviewItemTitle(item)}`);
            } catch (error) {
              button.disabled = false;
              button.textContent = verb;
              toast(error.message, true);
            }
          });
          advice.querySelector("[data-ai-apply]")?.addEventListener("click", async (event) => {
            const button = event.currentTarget;
            if (!relationAiHasCorrection(currentResult)) return;
            if (!confirm("确认一键应用 AI 给出的全部结构化修正？应用后关系将回到待审核。")) return;
            button.disabled = true;
            button.textContent = "应用中…";
            try {
              const applied = await applyRelationAiCorrection(item, currentResult);
              button.textContent = `已应用 ${applied.changes.length} 项修正`;
              row.querySelector("td").textContent = `${item.source_name || item.source_node_id} → ${relationLabel(item)} → ${item.target_name || item.target_node_id}`;
              row.querySelector(".batch-ai-status").innerHTML = miniStatus("stale").replace(REVIEW_LABELS.stale, "已修正待审核");
              const approve = advice.querySelector("[data-ai-direct-approve]");
              if (approve) {
                approve.hidden = false;
                approve.disabled = relationEndpointBlocked(item);
                approve.textContent = relationEndpointBlocked(item) ? "端点被拒/失效" : "通过修正后的关系";
              }
            } catch (error) { button.disabled = false; button.textContent = "一键应用修正"; toast(error.message, true); }
          });
          advice.querySelector("[data-ai-refine]")?.addEventListener("click", () => {
            const panel = advice.querySelector("[data-ai-refine-panel]");
            panel.hidden = !panel.hidden;
            if (!panel.hidden) panel.querySelector("textarea").focus();
          });
          advice.querySelector("[data-ai-rerun-one]")?.addEventListener("click", async (event) => {
            const button = event.currentTarget;
            const panel = advice.querySelector("[data-ai-refine-panel]");
            const extraPrompt = panel.querySelector("textarea").value.trim();
            if (!extraPrompt) { toast("请先输入进一步修正要求", true); return; }
            button.disabled = true;
            button.textContent = "修正中…";
            stop.hidden = false;
            stop.disabled = false;
            stop.textContent = "停止";
            stopped = false;
            controller = new AbortController();
            try {
              currentResult = await api(`/api/kg/relation-ai-review?graph_filename=${encodeURIComponent(item.graph_filename)}`, {
                json: { source_node_id: item.source_node_id, relation_code: item.relation_code, target_node_id: item.target_node_id, prompt: extraPrompt },
                signal: controller.signal,
              });
              const revisedIssues = (currentResult.issues || []).map((issue) => `${issue.area || "问题位置"}：${issue.problem || "需复核"}；建议：${issue.suggestion || "人工核对"}`);
              const revisedSuggestions = [currentResult.summary, ...revisedIssues, currentResult.suggested_statement ? `推荐表述：${currentResult.suggested_statement}` : "", currentResult.suggested_relation_code ? `建议关系：${currentResult.suggested_relation_code}` : "", currentResult.suggested_direction ? `建议方向：${currentResult.suggested_direction}` : "", currentResult.suggested_source_name ? `建议起点名称：${currentResult.suggested_source_name}` : "", currentResult.suggested_target_name ? `建议目标名称：${currentResult.suggested_target_name}` : ""].filter(Boolean);
              advice.querySelector("[data-ai-suggestion-list]").innerHTML = revisedSuggestions.map((line) => `<li>${esc(line)}</li>`).join("");
              const nextLabel = labels[currentResult.assessment] || [currentResult.assessment, "draft"];
              row.querySelector(".batch-ai-status").innerHTML = miniStatus(nextLabel[1]).replace(REVIEW_LABELS[nextLabel[1]] || nextLabel[1], nextLabel[0]);
              const approve = advice.querySelector("[data-ai-direct-approve]");
              if (approve) {
                approve.hidden = currentResult.assessment !== "approve";
                approve.disabled = relationEndpointBlocked(item);
                approve.textContent = relationEndpointBlocked(item) ? "端点被拒/失效" : "按建议通过";
              }
              const reject = advice.querySelector("[data-ai-direct-reject]");
              if (reject) {
                reject.hidden = !["reject", "revise", "insufficient_evidence"].includes(currentResult.assessment);
                reject.textContent = item.status === "active" ? "标记失效" : (currentResult.assessment === "reject" ? "按建议拒绝" : (currentResult.assessment === "insufficient_evidence" ? "因证据不足拒绝" : "拒绝此关系"));
              }
              const apply = advice.querySelector("[data-ai-apply]");
              apply.hidden = currentResult.assessment !== "revise";
              apply.disabled = !relationAiHasCorrection(currentResult);
              apply.textContent = relationAiHasCorrection(currentResult) ? "一键应用修正" : "暂无可执行修正";
              const refine = advice.querySelector("[data-ai-refine]");
              refine.hidden = currentResult.assessment !== "revise";
              if (currentResult.assessment !== "revise") panel.hidden = true;
            } catch (error) {
              if (error.name !== "AbortError") toast(error.message, true);
            } finally {
              controller = null;
              button.disabled = false;
              button.textContent = "重新审核本条";
              stop.hidden = true;
            }
          });
        } catch (error) {
          controller = null;
          if (cancelled || stopped || !dialog.open || error.name === "AbortError") {
            row.querySelector(".batch-ai-status").textContent = "已停止";
            break;
          }
          counts.failed += 1;
          row.querySelector(".batch-ai-status").textContent = "失败";
          row.querySelector(".batch-ai-advice").textContent = error.message;
        }
      }
      if (cancelled || !dialog.open) return;
      const finished = completed.size === queue.length;
      const batchHeading = finished ? "批量 AI 审核完成" : (stopped ? "批量 AI 审核已停止" : "批量 AI 审核未全部完成");
      $("#batchRelationAiSummary").innerHTML = `<div class="result-banner ${finished && !counts.reject && !counts.revise && !counts.failed ? "ok" : "warning"}"><strong>${batchHeading}</strong><p>已完成 ${completed.size}/${queue.length} · 建议通过 ${counts.approve} · 建议拒绝 ${counts.reject} · 建议修正 ${counts.revise} · 证据不足 ${counts.insufficient_evidence} · 失败 ${counts.failed}</p></div><p class="muted">${finished ? "结果已保留，可直接执行拒绝或一键修正。" : "已完成结果不会丢失，点击继续可从未完成项接着审核。"}</p>`;
      run.disabled = false;
      save.disabled = false;
      stop.hidden = true;
      run.textContent = finished ? "重新批量审核" : "继续剩余审核";
    };
    dialog.onclose = () => {
      cancelled = true;
      save.hidden = false;
      save.disabled = false;
      save.textContent = "保存";
      dialog.onclose = null;
    };
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
    const timeParts = [];
    if (item.created_at) timeParts.push(`首次生成 ${formatTime(item.created_at)}`);
    if (item.last_import_at && item.last_import_at !== item.created_at) timeParts.push(`最近导入 ${formatTime(item.last_import_at)}`);
    return `<div class="detail-head">
      <div class="detail-title"><span class="type-chip">${esc(REVIEW_TYPE_LABELS[item.item_type] || item.item_type)}</span>
        <strong>${esc(reviewItemTitle(item))}</strong> ${reviewBadge(item.status)}
        ${item.orphaned_from?.length ? `<span class="badge danger" title="${esc(item.orphaned_from.join("、"))}">孤儿</span>` : ""}</div>
      <div class="detail-sub mono">${esc(item.graph_filename || "")}${item.updated_at ? ` · 最近审核 ${esc(formatTime(item.updated_at))}` : ""}${timeParts.length ? ` · ${esc(timeParts.join(" · "))}` : ""}</div>
    </div>`;
  }
  function detailActionsHtml(item) {
    const status = item.status || "draft";
    const buttons = [];
    if (item.item_type === "graph_node") {
      buttons.push('<button class="btn sm" data-node-name-edit>修改名称</button>');
      buttons.push('<button class="btn sm" data-node-name-ai>AI 修正</button>');
    }
    if (item.item_type === "graph_relation") {
      buttons.push('<button class="btn sm" data-relation-ai-review>AI 审核</button>');
    }
    if (["draft", "stale", "testing", "blocked"].includes(status)) buttons.push('<button class="btn sm primary" data-detail-action="approve">通过</button>');
    if (!["active", "rejected", "retired"].includes(status)) buttons.push('<button class="btn sm danger" data-detail-action="reject">拒绝</button>');
    if (status === "active") buttons.push('<button class="btn sm" data-detail-action="retire">失效</button>');
    if (["rejected", "retired"].includes(status)) buttons.push('<button class="btn sm" data-detail-action="restore">恢复</button>');
    return buttons.length ? `<div class="actions detail-actions">${buttons.join("")}</div>` : "";
  }

  // 溯源引用 "DOC-xxx_p3_l100-20" → { docId, page, start, end }；生成器会把
  // l100-120 截成 l100-20（end 丢首位），end < start 时按 start 前缀补全位数。
  function parseSourceRef(ref) {
    const match = /^([A-Za-z0-9-]+)_p(\d+)_l(\d+)-(\d+)(?:_part\d+)?(?:_s\d+)?$/.exec(String(ref || ""));
    if (!match) return null;
    const start = Number(match[3]);
    let end = Number(match[4]);
    if (end < start) {
      const scale = Math.pow(10, match[4].length);
      end = Math.floor(start / scale) * scale + end;
    }
    return { docId: match[1], page: Number(match[2]), start, end };
  }

  const sourceRefLineCache = {}; // "DOC-x:3" -> 该页行数组
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
        return null;
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
    return section("从本实体出发的关系", asSource, true) + section("指向本实体的关系", asTarget, false);
  }

  async function nodeDetailHtml(item) {
    const detail = await api(`/api/kg/nodes/${encodeURIComponent(item.node_id)}?graph_filename=${encodeURIComponent(item.graph_filename)}`);
    const node = detail.node || {};
    item.status = node.review_status || item.status || "draft";
    item.name = node.name || item.name;
    const rules = detail.dependents?.rules || [];
    let historyHtml = "";
    if (item.status === "stale") {
      try { historyHtml = await entityHistoryHtml(item, "node", item.node_id, node); } catch (_) { historyHtml = ""; }
    }
    const refsHtml = (node.source_refs || []).length ? await sourceRefsHtml(node.source_refs) : "";
    const relatedHtml = relatedItemsHtml(detail, item);
    return `
      <div class="detail-section"><h4>实体审核重点</h4><p>逐项核对名称、类型、编码、属性和出处；有值不代表医学含义已经正确。</p></div>
      <div class="detail-section endpoint-meta">
        <div><span class="muted">名称</span><strong>${esc(node.name || "名称缺失，需补充")}</strong></div>
        <div><span class="muted">类型</span><strong>${esc(typeName[node.node_type] || node.node_type || "类型缺失，需补充")}</strong></div>
        <div><span class="muted">编码</span><span class="mono">${esc(node.code || "-")}</span></div>
      </div>
      <div class="detail-section"><h4>出处（指南原文与上下文）</h4>${refsHtml || '<div class="result-banner warning">出处缺失：尚无指南引用，不能仅凭实体名称确认其依据，请补充原文定位。</div>'}${refsHtml && !(node.source_refs || []).some(parseSourceRef) ? '<div class="result-banner warning">现有出处引用无法解析到页行，尚不能核对原文上下文，请补充或修复定位。</div>' : ""}</div>
      ${relatedHtml}
      ${(node.attribute_violations || []).length ? `<div class="detail-section"><h4>属性校验待审核（${node.attribute_violations.length}）</h4>${table(["属性", "问题"], node.attribute_violations.map((v) => `<tr><td class="mono">${esc(v.attribute_code)}</td><td>${esc(v.issue || "-")}</td></tr>`))}</div>` : ""}
      <div class="detail-section"><h4>属性（核对完整性与原文一致性）</h4>${Object.keys(node.attributes || {}).length ? attrTableHtml(node.attributes) : '<div class="muted">尚无已提取属性。请核对模板要求及原文；属性为空不直接等于实体识别错误。</div>'}</div>
      ${historyHtml}
      <div class="detail-section"><h4>引用本实体的推理规则（${rules.length}）</h4>${dependentRuleTable(rules)}</div>
      <div class="detail-section"><h4>审核记录</h4>${auditListHtml(detail.audit)}</div>`;
  }

  async function relationDetailHtml(item) {
    const detail = await api(`/api/kg/relations/${encodeURIComponent(item.item_id)}?graph_filename=${encodeURIComponent(item.graph_filename)}`);
    const relation = detail.relation || {};
    item.status = relation.review_status || item.status || "draft";
    item.source_status = detail.source?.status || "missing";
    item.target_status = detail.target?.status || "missing";
    const endpointCard = (endpoint, role) => `
      <div class="endpoint-card">
        <div class="muted endpoint-role">${esc(role)}</div>
        <strong>${esc(endpoint.name)}</strong>
        <div class="mono muted">${esc(endpoint.code || endpoint.node_id)}</div>
        <div>${miniStatus(endpoint.status)}</div>
        ${endpoint.status !== "active" && endpoint.node_id && endpoint.status !== "missing" ? `<button class="btn sm primary" data-relation-endpoint="${esc(endpoint.node_id)}" data-endpoint-status="${esc(endpoint.status)}">前往审核此实体</button>` : ""}
      </div>`;
    let historyHtml = "";
    if (item.status === "stale") {
      try { historyHtml = await entityHistoryHtml(item, "relation", item.item_id, relation); } catch (_) { historyHtml = ""; }
    }
    const rulesInfo = detail.rules || { total: 0, items: [] };
    return `
      <div class="detail-section"><h4>关系审核重点</h4><p>核对关联对象、关系方向、医学含义、关系属性及出处；端点通过不代表这条关系成立。</p></div>
      ${["rejected", "retired", "missing"].some((s) => [item.source_status, item.target_status].includes(s)) ? '<div class="result-banner warning">端点实体被拒绝、失效或缺失：请先恢复/补全对应实体，再审核这条关系。待审端点会随关系一并通过，无需先审实体。</div>' : ""}
      <div class="detail-section"><h4>关联对象与方向</h4>
        <div class="relation-flow">
          ${endpointCard(detail.source, "起点实体（关系从这里出发）")}
          <div class="relation-arrow"><span class="rel-name">${esc(relationLabel(item))}</span><span class="mono muted">${esc(item.relation_code)}</span></div>
          ${endpointCard(detail.target, "终点实体（关系指向这里）")}
        </div>
      </div>
      <div class="detail-section"><h4>医学含义（需对照原文确认）</h4><div class="quote">${esc(detail.source?.name || "起点缺失")} → ${esc(relationLabel({ ...item, ...relation }) || "关系含义未定义")} → ${esc(detail.target?.name || "终点缺失")}</div><p class="muted">核对该陈述是否准确、方向是否正确，是否遗漏适用条件或否定含义；关系名称本身不是审核结论。</p></div>
      <div class="detail-section"><h4>关系属性</h4>${attrTableHtml(relation.attributes)}</div>
      <div class="detail-section"><h4>出处（指南原文与上下文）</h4>${(relation.source_refs || []).length ? await sourceRefsHtml(relation.source_refs) : '<div class="result-banner warning">出处缺失：请补充支持该关系及方向的原文。</div>'}</div>
      ${historyHtml}
      <div class="detail-section"><h4>引用本关系的推理规则（${rulesInfo.total}）</h4>${dependentRuleTable(rulesInfo.items)}</div>
      <div class="detail-section"><h4>审核记录</h4>${auditListHtml(detail.audit)}</div>`;
  }

  async function ruleDetailHtml(item) {
    const [rule, deps, history] = await Promise.all([
      api(`/api/rules/${encodeURIComponent(item.rule_id)}`),
      api(`/api/rules/${encodeURIComponent(item.rule_id)}/graph-dependencies`),
      api(`/api/rules/${encodeURIComponent(item.rule_id)}/history`),
    ]);
    const refs = deps.references || [];
    const issues = [...new Set([...(deps.issues || []), ...(deps.review_issues || [])])];
    const blocked = issues.length > 0 || deps.semantic_status === "inconsistent" || deps.dependency_status === "blocked";
    const condition = rule.conditions || {};
    const implicitTreatment = rule.rule_type === "treatment" && condition.type === "group" && !(condition.children || []).length && (!condition.operator || condition.operator === "AND");
    const conditionSummary = implicitTreatment
      ? "内置前置条件：同一疾病的诊断规则已命中后，才评估此治疗建议。请核对是否还应增加分型、时限或其他限制。"
      : conditionText(condition);
    const source = rule.source || {};
    const conclusion = rule.conclusion || {};
    const historyRows = (history || []).slice(0, 8);
    return `
      <div class="detail-section" data-rule-approval-blocked="${blocked}"><h4>规则审核重点与检查结果</h4>
        <button class="btn primary" data-rule-review-edit="${esc(rule.rule_id)}">编辑补充后再审核</button>
        ${table(["审核重点", "已提供内容 / 待核对事项"], [
          ["名称、用途与目标", `${rule.name || "名称缺失"}；${clinicalIntentLabel(rule.clinical_intent, rule.rule_type)}；${rule.disease_name || rule.disease_code || "目标缺失"}`],
          ["条件完整性与组合逻辑", blocked ? "存在阻断项，需补充后再审核，详见下方问题" : "结构及图谱检查未发现阻断；仍须对照原文核对逻辑"],
          ["适用范围与排除条件", "核对条件树中的人群、阶段、前置条件、时间要求及下方排除疾病；未单独填写不代表没有限制"],
          ["结论与建议", conclusion.message || conclusion.diagnosis || "结论缺失，需补充"],
          ["出处与原文", source.quote ? "已提供引文，请核对原文、页行定位及结论是否一致" : "出处缺失：尚无支持本规则的原文引文，请补充"],
          ["图谱依赖", `${refs.length} 项依赖；核对所有依赖是否已审核通过`],
          ["医院标准与病例验证", "图谱校验不代表标准覆盖或病例测试已通过，请在标准管理和病历测试中分别验证"],
        ].map(([label, value]) => `<tr><td><strong>${esc(label)}</strong></td><td>${esc(value)}</td></tr>`))}
      </div>
      <div class="detail-section endpoint-meta">
        <div><span class="muted">名称</span><strong>${esc(rule.name || "名称缺失")}</strong></div>
        <div><span class="muted">规则编码</span><span class="mono">${esc(rule.rule_id)}</span></div>
        <div><span class="muted">用途与类型</span>${badge(clinicalIntentLabel(rule.clinical_intent, rule.rule_type))} ${esc(rule.rule_type || "-")} ${badge(normalizeDomainClient(rule.domain))}</div>
        <div><span class="muted">版本</span>v${esc(rule.version ?? "-")} · 优先级 ${esc(rule.priority ?? "-")}</div>
        <div><span class="muted">目标疾病 / 证候</span>${esc(rule.disease_name || rule.disease_code || "目标缺失")}</div>
        <div><span class="muted">所属图谱</span><span class="mono">${esc(rule.graph_filename || "-")}</span></div>
      </div>
      <div class="detail-section"><h4>条件与组合逻辑（且 / 或 / 非 / 评分）</h4><div class="quote">${esc(conditionSummary)}</div><div class="concept-chips">${ruleConceptChips(rule)}</div></div>
      <div class="detail-section"><h4>适用范围与排除条件</h4><p>适用人群、疾病分型、前置诊断及时间要求以条件树为准，请核对是否完整表达指南要求。</p><strong>明确排除的疾病：</strong>${esc((rule.exclusions || []).join("、") || "未单独配置，请对照原文确认是否需要补充")}</div>
      <div class="detail-section"><h4>结论与建议</h4>${table(["项目", "内容"], [
        ["结论目标", conclusion.diagnosis], ["提示说明", conclusion.message], ["建议动作", conclusion.action],
        ["治疗方案", conclusion.treatment_name || conclusion.formula_name], ["置信度", conclusion.confidence],
      ].map(([key, value]) => `<tr><td>${esc(key)}</td><td>${esc(value || "未填写")}</td></tr>`))}</div>
      <div class="detail-section"><h4>出处（指南原文与定位）</h4>
        <div class="ref-head"><strong>${esc(source.guideline_title || source.guideline_id || "指南名称 / 标识缺失")}</strong><span class="muted">第 ${esc(source.page_start || "未定位")} 页 · ${esc(source.line_start || "未定位")}–${esc(source.line_end || "未定位")} 行</span></div>
        ${source.quote ? `<div class="ref-context"><div class="ref-line hit"><span class="ref-text">${esc(source.quote)}</span></div></div>` : '<div class="result-banner warning">原文引文缺失，请补充支持该判断及结论的指南依据。</div>'}
      </div>
      <div class="detail-section"><h4>阻断问题与处理建议</h4>${blocked
        ? `<div class="result-banner warning">暂不能通过。缺少子条件或必要编码通常属于待补充，应先编辑修订；不要仅因信息不全直接判为医学错误。确认抽取错误、与指南相反或无依据时再拒绝。</div><ul class="issue-list">${issues.map((issue) => `<li>${esc(issue)}</li>`).join("")}</ul>`
        : '<div class="muted">未发现结构或图谱依赖阻断项，不代表医学正确性、标准覆盖和病例验证已经通过。</div>'}
      </div>
      <div class="detail-section"><h4>图谱依赖（${refs.length}）</h4>${refs.length ? table(["类型", "条目", "审核状态"], refs.map((ref) => `<tr><td>${ref.kind === "node" ? "实体" : "关系"}</td><td class="mono">${esc(ref.kind === "node" ? ref.node_id : `${ref.source_node_id} ─${ref.relation_code}→ ${ref.target_node_id}`)}</td><td>${ref.status === "active" ? '<span class="badge active">已通过</span>' : miniStatus(ref.status)}</td></tr>`)) : '<div class="muted">无可核对的图谱依赖</div>'}</div>
      <div class="detail-section"><h4>版本历史${history?.length ? `（${history.length}）` : ""}</h4>${historyRows.length ? table(["版本", "时间", "操作", "操作人"], historyRows.map((row) => `<tr><td>#${esc(row.seq)}</td><td>${esc(formatTime(row.timestamp))}</td><td>${esc(row.action || "-")}</td><td>${esc(row.actor || "-")}</td></tr>`)) : '<div class="muted">暂无版本历史</div>'}</div>`;
  }

  let reviewDetailToken = 0;
  function markSelectedRow() {
    pageEl.querySelectorAll(".review-row").forEach((row) => {
      row.classList.toggle("selected", row.dataset.reviewKey === state.reviewSelectedKey);
    });
    const row = pageEl.querySelector(`.review-row[data-review-key="${CSS.escape(state.reviewSelectedKey)}"]`);
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
      // Related details may be outside the filtered queue. Act on this detail's
      // item, rather than looking it up in state.reviewItems.
      mount.querySelectorAll("[data-detail-action]").forEach((button) => button.onclick = async (event) => {
        event.stopPropagation();
        const action = button.dataset.detailAction;
        if (action === "reject" || action === "retire") { openReasonDialog(item, action); return; }
        button.disabled = true;
        const label = button.textContent;
        button.textContent = "提交中…";
        try { await quickReview(item, action); }
        finally { button.disabled = false; button.textContent = label; }
      });
      mount.querySelector("[data-node-name-edit]")?.addEventListener("click", () => openNodeNameEditor(item));
      mount.querySelector("[data-node-name-ai]")?.addEventListener("click", () => openNodeNameEditor(item, { startWithAi: true }));
      mount.querySelector("[data-relation-ai-review]")?.addEventListener("click", () => openRelationAiReview(item));
      if (mount.querySelector('[data-rule-approval-blocked="true"]')) {
        mount.querySelectorAll('[data-detail-action="approve"]').forEach((button) => {
          button.disabled = true; button.title = "存在阻断问题，请先编辑补充后再审核";
        });
      }
      mount.querySelectorAll("[data-rule-review-edit]").forEach((button) => button.onclick = async () => {
        try { openRuleEditor(await api(`/api/rules/${encodeURIComponent(button.dataset.ruleReviewEdit)}`), { reviewRevision: true }); }
        catch (error) { toast(error.message, true); }
      });
      mount.querySelectorAll(".ref-chip[data-ref]").forEach((chip) => chip.onclick = async () => {
        chip.disabled = true;
        const wrap = document.createElement("div");
        wrap.innerHTML = await sourceRefBlock(chip.dataset.ref);
        chip.replaceWith(...wrap.childNodes);
      });
      mount.querySelectorAll("[data-related-node]").forEach((btn) => btn.onclick = () => {
        loadReviewDetail({ item_type: "graph_node", node_id: btn.dataset.relatedNode, graph_filename: item.graph_filename, status: "" });
      });
      mount.querySelectorAll("[data-relation-endpoint]").forEach((btn) => btn.onclick = () => {
        loadReviewDetail({
          item_type: "graph_node", node_id: btn.dataset.relationEndpoint,
          graph_filename: item.graph_filename, status: btn.dataset.endpointStatus || "",
        });
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
    let selected = [...pageEl.querySelectorAll("[data-review-select]:checked")]
      .map((input) => state.reviewItems.find((item) => reviewItemKey(item) === input.dataset.reviewSelect))
      .filter(Boolean);
    if (!selected.length) { toast("请先勾选条目", true); return; }
    if (action === "approve") {
      const blockedCount = selected.filter(relationEndpointBlocked).length;
      if (blockedCount) {
        const rest = selected.length - blockedCount;
        const proceed = confirm(
          `${blockedCount} 条关系的端点实体被拒绝、已失效或缺失，系统不会连带恢复，直接提交会被拒绝。` +
          (rest ? `\n\n确定后仅提交其余 ${rest} 条（待审端点实体将随关系一并通过）；` : "\n\n当前没有可提交条目；") + "取消则不做操作。"
        );
        if (!proceed) return;
        selected = selected.filter((item) => !relationEndpointBlocked(item));
        if (!selected.length) { toast("没有可提交的条目：相关端点被拒绝或失效，请先恢复实体", true); return; }
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
        link_endpoints: true,
      }});
      const linked = (result.results || []).reduce((sum, entry) => sum + ((entry.result?.linked_endpoints) || []).length, 0);
      const firstError = (result.results || []).find((entry) => !entry.ok);
      const detail = firstError
        ? `；首个失败：${typeof firstError.error === "string" ? firstError.error : JSON.stringify(firstError.error)}`
        : "";
      markStale("graphVisual");
      toast(`批量完成：成功 ${result.succeeded}，失败 ${result.failed}${linked ? `，连带通过端点实体 ${linked} 个` : ""}${detail}`, result.failed > 0);
      renderReviewCenter();
    } catch (error) { toast(error.message, true); }
  }

  function bindReviewTabs() {
    pageEl.querySelectorAll("[data-review-tab]").forEach((button) => button.onclick = () => {
      state.reviewTab = button.dataset.reviewTab;
      state.reviewSelectedKey = "";
      renderReviewCenter();
    });
  }

  function reviewQueueItemType(tab, filters = {}) {
    if (tab === "standards") return "standard";
    if (tab === "rules" || tab === "blocked") return "rule";
    if (tab === "relations") return "graph_relation";
    if (tab === "graph") return "graph";
    if (["stale", "orphan"].includes(tab)) return "graph";
    if (tab === "active") return filters.itemType || "";
    return "";
  }

  function bindDupeMerge(graphFilename) {
    if (!graphFilename) return;
    pageEl.querySelectorAll("[data-dupe-merge]").forEach((button) => button.onclick = async () => {
      const groupEl = button.closest(".dupe-group");
      const checked = groupEl.querySelector("input[type=radio]:checked");
      if (!checked) { toast("请选择保留节点", true); return; }
      const survivor = checked.value;
      const labels = [...groupEl.querySelectorAll(".dupe-node")].map((el) => ({
        id: el.querySelector("input[type=radio]").value,
        code: el.querySelector(".mono").textContent,
        name: el.querySelector("strong")?.textContent || "",
      }));
      const survivorNode = labels.find((l) => l.id === survivor);
      const survivorCode = `${survivorNode.name}（${survivorNode.code}）`;
      const absorbed = labels.filter((l) => l.id !== survivor);
      if (!confirm(`保留 ${survivorCode}，吸收：${absorbed.map((l) => `${l.name}（${l.code}）`).join("、")}？\n关联关系将重挂到保留节点，引用被吸收编码的规则会自动改写（合并前自动备份）。`)) return;
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

  function reviewCategoriesHtml() {
    return `<div class="grid c4">${[
      ["graph", "1 · 实体审核", "核对名称、类型、编码、属性及出处"],
      ["relations", "2 · 关系审核", "核对连接对象、方向、关系含义及出处"],
      ["bases", "3 · 诊疗依据审核", "核对条件组合、适用范围、排除条件和原文；通过后生成规则草稿"],
      ["standards", "4 · 判定标准审核", "核对检查、检验的阈值、单位、适用条件及原文；通过后发布到选定医院"],
      ["rules", "5 · 规则审核", "核对执行逻辑、依赖及病例验证；审核后启用"],
    ].map(([key, title, description]) => `<button class="btn ${state.reviewTab === key ? "primary" : ""}" data-review-tab="${key}" style="white-space:normal;text-align:left;display:block;padding:12px"><strong>${title}</strong><div style="font-weight:normal;margin-top:6px">${description}</div></button>`).join("")}</div>`;
  }

  function extractionCoverageHtml(coverage) {
    if (!coverage) return '<p class="muted">此导入暂无覆盖报告，不能据此认定已完整解析。</p>';
    return `<div class="result-banner ${coverage.complete ? "ok" : "warning"}"><strong>解析覆盖：${coverage.processed}/${coverage.total} 个片段已处理</strong><p>失败 ${coverage.failed} · 未安排 ${coverage.not_scheduled}。已处理不等于医学内容已完整核对。</p><details><summary>逐章节覆盖明细</summary>${table(["章节", "页码", "状态"], (coverage.items || []).map((item) => `<tr><td>${esc(item.section)}</td><td>${esc(item.page_start)}–${esc(item.page_end)}</td><td>${esc({processed: "已处理", failed: "抽取失败", not_scheduled: "未安排（达到处理上限）"}[item.status] || item.status)}</td></tr>`))}</details></div>`;
  }

  function clinicalStatementHtml(basis) {
    const validation = basis.validation || {};
    const statement = validation.statement || {};
    const source = validation.provenance || {};
    const original = basis.specification?.source || {};
    const pdfLink = original.guideline_id ? `<a class="btn sm" target="_blank" rel="noopener" href="/api/pdf/${encodeURIComponent(original.guideline_id)}/file#page=${encodeURIComponent(original.page_start || 1)}">查看PDF原页（表格／脚注）</a>` : "";
    return `<div class="statement-review-grid"><section><h4>指南原文核验</h4>${pdfLink}<div class="badge ${source.status === "verified" ? "active" : "danger"}">${source.status === "verified" ? "引文匹配实际页行" : "原文待修复／无法核验"}</div><div class="quote">${esc(source.text || "暂无可核验的实际原文")}</div><p class="muted">文字匹配只证明出处存在，仍须核对医学含义。</p></section><section><h4>临床陈述</h4>${table(["核对内容", "当前值"], [["场景", clinicalIntentLabel(statement.scene)], ["目标", statement.target], ["人群", statement.population], ["分型", statement.subtype], ["阶段", statement.phase], ["例外", (statement.exceptions || []).join("；") || "待核对"], ["推荐等级", statement.recommendation_strength], ["证据等级", statement.evidence_grade], ["适用范围核对", statement.scope_summary || "待填写"]].map(([key, value]) => `<tr><td>${esc(key)}</td><td>${esc(value || "待核对")}</td></tr>`))}<p>${esc(conditionText(statement.conditions))}</p></section><section><h4>需要处理的问题</h4>${(validation.issues || []).map((issue) => `<p class="result-banner warning">${esc(issue)}</p>`).join("") || '<p>结构检查通过；请继续核对临床含义。</p>'}${(statement.missing_information || []).map((item) => `<p>${esc(item)}</p>`).join("")}</section></div>`;
  }

  function clinicalBasisIdentity(basis, subjects = {}) {
    const spec = basis.specification || {};
    const context = spec.clinical_context?.suspected_tcm_diagnosis;
    const tcm = normalizeDomainClient(spec.domain) === "tcm";
    const syndrome = tcm && spec.anchor_type === "syndrome";
    const diseaseAnchor = !tcm || !spec.anchor_type || spec.anchor_type === "disease";
    const anchorCode = spec.disease_code;
    const directDiseaseCode = basis.scenario_review?.disease_code || context?.disease_code ||
      (diseaseAnchor ? anchorCode : "");
    const memberships = syndrome && !directDiseaseCode ? (subjects.disease_groups || [])
      .filter((group) => group.syndromes.some((item) => item.code === anchorCode)) : [];
    const confirmed = memberships.filter((group) => group.syndromes.some((item) =>
      item.code === anchorCode && item.membership_status === "active"));
    const owners = confirmed.length ? confirmed : memberships;
    const diseaseCodes = directDiseaseCode ? [directDiseaseCode] : owners.map((group) => group.disease.code);
    const lookupName = (type, code) => (subjects.subjects || [])
      .find((item) => item.node_type === type && item.code === code)?.display_name;
    const diseaseName = directDiseaseCode
      ? lookupName("disease", directDiseaseCode) || context?.disease_name ||
        (diseaseAnchor && spec.disease_name) || directDiseaseCode
      : [...new Set(owners.map((group) => group.disease.display_name || group.disease.name))].join("／");
    const syndromeName = syndrome ? lookupName("syndrome", anchorCode) || spec.disease_name || anchorCode : "";
    const intent = spec.clinical_intent || defaultClinicalIntent(spec.rule_type);
    const scene = intent === "treatment_recommendation" ? "治疗方案" : clinicalIntentLabel(intent, spec.rule_type);
    const title = [diseaseName || "疾病待关联", ...(tcm ? [syndromeName || "证候未指定"] : []), scene].join(" · ");
    return {title, diseaseCodes};
  }

  async function renderClinicalBases() {
    await loadBase();
    const graphs = await api("/api/kg/graphs");
    const filename = graphs.some((graph) => graph.graph_filename === state.reviewFilters.graph) ? state.reviewFilters.graph : (graphs[0]?.graph_filename || "");
    state.reviewFilters.graph = filename;
    const query = `?graph_filename=${encodeURIComponent(filename)}`;
    const [result, subjects] = filename ? await Promise.all([
      api(`/api/review/bases${query}&reviewable_only=true`),
      api(`/api/kg/relations/groups${query}&subject_type=disease`).catch(() => ({})),
    ]) : [{items: []}, {}];
    state.basisTitleSubjects = {filename, subjects};
    const identities = new Map(result.items.map((basis) => [basis.basis_id, clinicalBasisIdentity(basis, subjects)]));
    const diseaseCode = state.basisDiseaseCode || "";
    const diseaseBases = result.items.filter((basis) => !diseaseCode || basis.specification.disease_code === diseaseCode ||
      identities.get(basis.basis_id).diseaseCodes.includes(diseaseCode));
    const visibleBases = diseaseBases.filter((basis) =>
      (!state.basisQualityOnly || (basis.disposition !== "discarded" && basis.validation.issues.length)) &&
      (!state.basisStatusFilter || (state.basisStatusFilter === "discarded" ? basis.disposition === "discarded" : basis.disposition !== "discarded" && basis.validation.status === state.basisStatusFilter)));
    // 诊疗依据表改用通用分页组件：行数据（含搜索文本/状态值/隐藏标记）预构建，
    // 翻页与搜索只重绘 tbody，行内动作走容器事件委托。
    const basisRows = visibleBases.map((basis) => {
      const identity = identities.get(basis.basis_id);
      return {
        text: `${identity.title} ${basis.name || ""} ${basis.specification.source?.guideline_title || ""} ${(basis.validation.issues || []).join(" ")}`,
        values: { status: basis.disposition === "discarded" ? "discarded" : basis.validation.status },
        html: `<tr><td><strong>${esc(identity.title)}</strong>${basis.name && basis.name !== identity.title ? `<div>${esc(basis.name)}</div>` : ""}<div class="muted">${esc(basis.specification.source?.guideline_title || "-")}</div></td><td>${basis.disposition === "discarded" ? badge("不采纳") : reviewBadge(basis.validation.status)}</td><td>${basis.disposition === "discarded" ? `不生成规则${basis.disposition_reason ? "：" + esc(basis.disposition_reason) : ""}` : basis.validation.issues.length ? esc(basis.validation.issues.join("；")) : "图谱依赖完整，待人工核对逻辑"}${basis.generated_rule_id ? `<div>已生成：${esc(basis.generated_rule_id)}</div>` : ""}</td><td>${basis.disposition === "discarded" ? "" : basis.generated_rule_id ? `<button class="btn primary" data-basis-revise="${esc(basis.basis_id)}" title="基于最新诊断依据创建修订，核对条件后再生成待审规则">修订规则条件</button>` : `<button class="btn primary" data-basis-edit="${esc(basis.basis_id)}" title="带入审核通过的诊断依据，可使用 AI 建议或手动组合条件">配置规则条件</button>`}<button class="btn" data-basis-ai="${esc(basis.basis_id)}">大模型辅助审核</button><button class="btn" data-basis-detail="${esc(basis.basis_id)}">审核详情</button>${!basis.generated_rule_id ? `<button class="btn" data-basis-disposition="${esc(basis.basis_id)}" data-action="${basis.disposition === "discarded" ? "restore" : "discard"}">${basis.disposition === "discarded" ? "恢复待审" : "不采纳"}</button>` : ""}</td></tr>`,
      };
    });
    const tabHtml = REVIEW_TABS.map(([key, label]) => `<button class="review-tab ${key === "bases" ? "active" : ""}" data-review-tab="${key}">${label}</button>`).join("");
    pageEl.innerHTML = `<div class="review-tabs">${tabHtml}</div>${reviewCategoriesHtml()}
      <div class="result-banner"><strong>按知识图谱管理诊疗依据</strong><div class="inline-controls"><label>知识图谱 <select id="basisGraphFilter">${graphs.map((g) => `<option value="${esc(g.graph_filename)}" ${filename === g.graph_filename ? "selected" : ""}>${esc(g.source || g.graph_filename)} · ${esc(g.graph_filename)}</option>`).join("")}</select></label><label>状态 <select id="basisStatusFilter">${[["", "全部状态"], ["draft", "待审"], ["active", "已通过"], ["revisions", "待审修订"], ["rejected", "已退回"], ["discarded", "不采纳"]].map(([value, label]) => `<option value="${value}" ${state.basisStatusFilter === value ? "selected" : ""}>${label}</option>`).join("")}</select></label></div><p>当前图谱有 ${result.items.length} 份依据的实体和关系已通过审核；显示 ${visibleBases.length} 份。</p></div>
      <div class="result-banner">已有 ${visibleBases.length} 份候选依据${diseaseCode ? `（疾病 ${esc(diseaseCode)}）` : ""}。候选已保留解析条件、结论和原文；图谱审核状态自动复用，无须重新录入。核对组合逻辑后，点击“确认依据并生成待审规则”。${diseaseCode ? '<button class="btn sm" id="allDiseaseBases">查看全部疾病</button>' : ""}</div>
      <div class="result-banner ${diseaseBases.some((basis) => basis.disposition !== "discarded" && basis.validation.issues.length) ? "warning" : "ok"}"><strong>质量复核：${diseaseBases.filter((basis) => basis.disposition !== "discarded" && basis.validation.issues.length).length} 份存在待处理问题</strong><p>引文须与实际指南页行一致。原有审核记录保留，存在问题时须修正后重新确认。</p><label><input type="checkbox" id="basisQualityOnly" ${state.basisQualityOnly ? "checked" : ""}> 仅显示有问题的依据</label></div>
      ${panel("诊疗依据审核", `<p>审核对象是一组完整的诊疗判断，不是单个实体或一条关系。实体与关系通过审核后，还须确认组合逻辑及适用条件；只有通过的依据组才能生成规则草稿。</p>
        <div class="inline-controls"><div class="field"><label>知识图谱</label><select id="basisGraph">${graphs.map((g) => `<option value="${esc(g.graph_filename)}" ${filename === g.graph_filename ? "selected" : ""}>${esc(g.graph_filename)}</option>`).join("")}</select></div>
        <button class="btn" id="prepareBases" ${filename ? "" : "disabled"}>整理已有候选为待审依据</button><button class="btn primary" id="newClinicalBasis" ${filename ? "" : "disabled"}>新增诊疗依据</button></div>
        <p class="muted">整理操作读取此图谱的知识包候选和已有规则，不猜测实体连线的且/或关系，不修改或停用已有规则。</p>`)}
      ${pagedPanel({ key: "clinicalBases", title: "诊疗依据组", description: "分页浏览；支持按名称、指南、待处理问题搜索，行内动作不受翻页影响。", headers: ["疾病、证候与场景 / 指南", "审核状态", "生成前检查", "操作"], items: basisRows, placeholder: "搜索依据 / 指南 / 待处理问题…", emptyText: "暂无符合当前筛选、且图谱依赖已通过的依据" })}
      <div id="basisDetail"></div>`;
    if (diseaseCode) {
      $("#allDiseaseBases").onclick = () => { state.basisDiseaseCode = ""; renderClinicalBases(); };
    }
    bindReviewTabs();
    bindPagedTable("clinicalBases");
    // 行内动作统一走 tbody 容器委托：翻页/搜索重建行后依然有效
    const basisBody = pageEl.querySelector('[data-table-body="clinicalBases"]');
    if (basisBody) basisBody.addEventListener("click", async (event) => {
      const detailButton = event.target.closest("[data-basis-detail]");
      if (detailButton) {
        const basis = result.items.find((item) => item.basis_id === detailButton.dataset.basisDetail);
        if (basis) await showClinicalBasis(basis, filename);
        return;
      }
      const aiButton = event.target.closest("[data-basis-ai]");
      if (aiButton) {
        const basis = result.items.find((item) => item.basis_id === aiButton.dataset.basisAi);
        if (!basis) return;
        await window.openBasisAiReview(basis, filename, {api, esc, edit: async () => {
          if (basis.generated_rule_id) await showClinicalBasis(basis, filename);
          else await openCompletedBasisEditor(basis, filename, basis.scope_summary || "");
        }});
        return;
      }
      const dispositionButton = event.target.closest("[data-basis-disposition]");
      if (dispositionButton) {
        const action = dispositionButton.dataset.action;
        const reason = action === "discard" ? prompt("不采纳后，该依据不用生成规则。可填写原因（选填）：", "") : "";
        if (reason === null) return;
        dispositionButton.disabled = true;
        try {
          await api(`/api/review/bases/${encodeURIComponent(dispositionButton.dataset.basisDisposition)}/disposition?graph_filename=${encodeURIComponent(filename)}`, {json: {action, reason}});
          await renderClinicalBases();
        } catch (error) { toast(error.message, true); dispositionButton.disabled = false; }
        return;
      }
      const editButton = event.target.closest("[data-basis-edit]");
      if (editButton) {
        const basis = result.items.find((item) => item.basis_id === editButton.dataset.basisEdit);
        if (basis) await openCompletedBasisEditor(basis, filename, basis.scope_summary || "", editButton);
        return;
      }
      const reviseButton = event.target.closest("[data-basis-revise]");
      if (reviseButton) {
        reviseButton.disabled = true;
        reviseButton.textContent = "正在汇总最新关联…";
        try {
          const reviseResult = await api(`/api/review/bases/${encodeURIComponent(reviseButton.dataset.basisRevise)}/revise-from-reviewed-graph?graph_filename=${encodeURIComponent(filename)}`, { json: {} });
          toast(`已创建修订依据并带入 ${reviseResult.used.length} 个已审条件，请核对后生成新待审规则`);
          await renderClinicalBases();
          await showClinicalBasis(reviseResult.basis, filename);
        } catch (error) { toast(error.message, true); reviseButton.disabled = false; reviseButton.textContent = "修订规则条件"; }
      }
    });
    $("#basisQualityOnly").onchange = (event) => { state.basisQualityOnly = event.target.checked; renderClinicalBases(); };
    const switchBasisGraph = (event) => {
      state.reviewFilters.graph = event.target.value;
      state.basisDiseaseCode = "";
      state.basisQualityOnly = false;
      state.basisStatusFilter = "";
      renderClinicalBases();
    };
    $("#basisGraph").onchange = switchBasisGraph;
    $("#basisGraphFilter").onchange = switchBasisGraph;
    $("#basisStatusFilter").onchange = (event) => { state.basisStatusFilter = event.target.value; renderClinicalBases(); };
    $("#newClinicalBasis").onclick = () => openRuleEditor(null, { clinicalBasis: true, onSave: (specification) => api(`/api/review/bases?graph_filename=${encodeURIComponent(filename)}`, { json: { specification, scope_summary: "" } }) });
    $("#prepareBases").onclick = async () => {
      try { await api(`/api/review/bases/prepare?graph_filename=${encodeURIComponent(filename)}`, { json: {} }); await renderClinicalBases(); }
      catch (error) { toast(error.message, true); }
    };
  }

  function conditionAtLogicPath(specification, path) {
    let node = specification.conditions;
    const indexes = [...String(path || "").matchAll(/children\[(\d+)\]/g)].map((match) => Number(match[1]));
    for (const index of indexes) node = node?.children?.[index];
    return node;
  }

  function chooseConditionLogic(question, aiContext = null) {
    return new Promise((resolve) => {
      const dialog = $("#editorDialog");
      const save = $("#editorSave");
      let selected = "";
      save.hidden = true;
      $("#editorBody").innerHTML = `
        <h3>这些条件如何构成判断？</h3>
        ${aiContext ? '<button type="button" class="btn" id="logicAiSuggest">AI 建议</button><div id="logicAiAdvice"></div>' : ""}
        <p class="muted">不需要编辑条件树。请根据指南原文选择最符合医学含义的一项。</p>
        <div class="detail-section"><ol>${(question.children || []).map((child) => `<li>${esc(conditionText(child))}</li>`).join("")}</ol></div>
        <div class="actions">
          <button type="button" class="btn" data-logic-choice="RESTRUCTURE">重组条件（嵌套且／或）</button><button type="button" class="btn primary" data-logic-choice="AND">必须全部满足（且）</button>
          <button type="button" class="btn primary" data-logic-choice="OR">满足任意一项（或）</button>
        </div>
        <p class="muted">如果原文没有说明组合关系，请取消并补充原文，不要猜测。</p>`;
      if (aiContext) $("#logicAiSuggest").onclick = async () => {
        const button = $("#logicAiSuggest");
        button.disabled = true; button.textContent = "正在分析原文…";
        try {
          const advice = await api(`/api/review/bases/${encodeURIComponent(aiContext.basisId)}/logic-advice?graph_filename=${encodeURIComponent(aiContext.filename)}`, {json: {specification: aiContext.specification, path: question.path}});
          $("#logicAiAdvice").innerHTML = `<div class="detail-section"><strong>建议组合</strong><div class="quote">${esc(advice.conditions ? conditionText(advice.conditions) : "依据不足，暂不能生成组合")}</div>${advice.conditions ? '<button type="button" class="btn primary" id="logicAiConfirm">采纳并编辑</button>' : ""}<details><summary>查看依据与排除项</summary><p>${esc(advice.reason)}</p><blockquote>${esc(advice.evidence_quote || "")}</blockquote><p>未纳入：${esc((advice.excluded_conditions || []).map(conditionText).join("；") || "无")}</p></details></div>`;
          if (advice.conditions) $("#logicAiConfirm").onclick = () => { selected = {conditions: advice.conditions}; dialog.close(); };
        } catch (error) { toast(error.message, true); }
        finally { button.disabled = false; button.textContent = "AI 建议"; }
      }
      $("#editorCancel").onclick = () => dialog.close();
      $("#editorBody").querySelectorAll("[data-logic-choice]").forEach((choice) => {
        choice.onclick = () => {
          selected = choice.dataset.logicChoice;
          dialog.close();
        };
      });
      dialog.onclose = () => {
        save.hidden = false;
        save.textContent = "保存";
        dialog.onclose = null;
        resolve(selected);
      };
      dialog.showModal();
    });
  }

  async function resolveBasisLogic(specification, questions, aiContext = null) {
    const resolved = JSON.parse(JSON.stringify(specification));
    for (const question of questions || []) {
      const operator = await chooseConditionLogic(question, aiContext ? {...aiContext, specification: resolved} : null);
      if (!operator) return null;
      if (typeof operator === "object" && operator.conditions) {
        const target = conditionAtLogicPath(resolved, question.path);
        Object.keys(target).forEach((key) => delete target[key]);
        Object.assign(target, operator.conditions);
        return resolved;
      }
      if (operator === "RESTRUCTURE") return resolved;
      const node = conditionAtLogicPath(resolved, question.path);
      if (node?.type === "group") node.operator = operator;
    }
    return resolved;
  }

  async function openCompletedBasisEditor(basis, filename, scopeSummary, button = null) {
    const endpoint = `/api/review/bases/${encodeURIComponent(basis.basis_id)}`;
    const oldLabel = button?.textContent;
    if (button) { button.disabled = true; button.textContent = "正在整理已审关联…"; }
    try {
      const completed = await api(endpoint + `/complete-from-reviewed-graph?graph_filename=${encodeURIComponent(filename)}`, { json: {} });
      const prepared = completed.basis || basis;
      if (completed.changed) {
        toast(`已从审核通过的关联补入 ${completed.used.length} 个候选条件，请核对且／或组合`);
      } else {
        toast(completed.reason || "现有条件已完整，已保留原配置");
      }
      if (completed.ready === false && !(completed.logic_questions || []).length) {
        toast(completed.reason || "当前没有可生成规则的已审诊断条件，请先补充并审核关系", true);
        return;
      }
      const resolvedSpecification = await resolveBasisLogic(
        prepared.specification, completed.logic_questions || [], {basisId: basis.basis_id, filename}
      );
      if (!resolvedSpecification) {
        toast("已取消：未修改诊疗依据");
        return;
      }
      if ((completed.logic_questions || []).length) {
        toast("组合方式已明确，可继续核对中文预览并保存");
      } else if (completed.logic_normalized) {
        toast("已自动整理旧版条件结构，可继续核对并保存");
      }
      openRuleEditor(resolvedSpecification, {
        clinicalBasis: true,
        onSave: (specification) => api(endpoint + `?graph_filename=${encodeURIComponent(filename)}`, {
          method: "PUT", json: { specification, scope_summary: scopeSummary },
        }),
      });
      const guidance = document.createElement("div");
      guidance.className = "result-banner ok";
      guidance.innerHTML = `<strong>条件已整理完成</strong><p>${(completed.logic_questions || []).length ? "可在下方重组条件并确认各组的且／或关系。" : "系统已整理旧版条件结构。"}可在下方条件树点击“+子组”，每组独立设置且／或；拖动条件卡片进入对应分组。例如：（e 或 f）或（C 且 D 且 E）。核对后保存。</p>`;
      $("#editorBody").prepend(guidance);
      if (completed.candidates?.length) {
        const notice = document.createElement("div");
        notice.className = "result-banner warning";
        notice.innerHTML = `<strong>已审关联候选（${completed.candidates.length} 项）</strong><p>以下项目已自动关联。请对照原文确定哪些属于诊断条件及其且／或关系；尚未加入执行条件。</p><div>${completed.candidates.map((candidate) => esc(conditionText(candidate))).join("；")}</div>`;
        $("#editorBody").prepend(notice);
      }
      if (completed.treatment_links) {
        const links = completed.treatment_links;
        const notice = document.createElement("div");
        notice.className = "result-banner";
        notice.innerHTML = `<strong>已自动关联的治疗图谱</strong><p>治疗项目：${esc(links.items.map((item) => item.name || item.code).join("、") || "当前依据未关联到已审方案项目")}</p><p>疾病关联检查检验（待核对是否适用于本方案）：${esc(links.investigations.map((item) => item.name || item.code).join("、") || "暂无")}</p><p>这些项目无需重复录入诊断条件。请核对原文中的适用人群、阶段及禁忌限制。</p>`;
        $("#editorBody").prepend(notice);
      }
    } catch (error) {
      toast(error.message, true);
    } finally {
      if (button) { button.disabled = false; button.textContent = oldLabel; }
    }
  }

  async function showClinicalBasis(basis, filename) {
    if (basis.validation.dependencies_approved === false) {
      toast("图谱依赖尚未全部审核通过，请先到疾病审核台补审", true);
      return;
    }
    const labels = { entities: "实体与关系：目标、条件对象和语义链路正确", logic: "组合逻辑：且/或/非、满足数量、评分和时间要求符合原文", scope: "适用与排除：已完整写入可执行条件及 exclusions，不只是填写下方说明", source: "指南依据：原文、页行定位、适用版本和结论含义一致" };
    const spec = basis.specification;
    const source = spec.source || {};
    const context = source.chunk_id ? await sourceRefsHtml([source.chunk_id]) : "";
    const subjects = state.basisTitleSubjects?.filename === filename ? state.basisTitleSubjects.subjects : {};
    const {title} = clinicalBasisIdentity(basis, subjects);
    $("#basisDetail").innerHTML = panel(title, `
      ${basis.name && basis.name !== title ? `<p>${esc(basis.name)}</p>` : ""}
      <p class="mono muted">依据编号：${esc(basis.basis_id)}</p>
      ${clinicalStatementHtml(basis)}
      ${basis.generated_rule_id ? '<button id="basisReviseLogic" class="btn primary">修订规则条件</button>' : '<button id="basisEditLogic" class="btn primary" title="带入审核通过的诊断依据，可使用 AI 建议或手动组合条件">配置规则条件</button>'}
      <h4>条件逻辑</h4><div class="quote">${esc(conditionText(spec.conditions))}</div>
      <h4>结论及排除条件</h4><pre>${esc(JSON.stringify({ conclusion: spec.conclusion, exclusions: spec.exclusions || [] }, null, 2))}</pre>
      <h4>指南原文</h4><div>${esc(source.guideline_title || source.guideline_id || "-")} · 第 ${esc(source.page_start || "-")} 页 · ${esc(source.line_start || "-")}–${esc(source.line_end || "-")} 行</div><div class="quote">${esc(source.quote || "缺少引文，不能通过")}</div>${context}
      <h4>生成前检查</h4>${basis.validation.issues.length ? `<div class="result-banner warning">${esc(basis.validation.issues.join("；"))}</div>` : '<div class="result-banner ok">依赖实体和关系均已通过；仍需人工核对以下四类内容。</div>'}
      <h4>沿用的图谱审核结果</h4><p class="muted">已通过项不需要重审。候选可能还引用“检验／检查 → 诊断依据”等后续链路，未必都在疾病的直接关联中。</p>
      ${table(["依赖", "状态", "操作"], (basis.validation.references || []).map((ref, index) => `<tr><td class="mono">${esc(ref.kind === "node" ? ref.node_id : `${ref.source_node_id} → ${RELATION_LABELS[ref.relation_code] || ref.relation_code} → ${ref.target_node_id}`)}</td><td>${miniStatus(ref.status)}</td><td>${ref.status !== "active" ? `<button class="btn sm" data-basis-dependency="${index}">查看并补审</button>` : "已沿用"}</td></tr>`))}
      <h4>适用范围核对说明</h4><textarea id="basisScope" rows="3" ${basis.generated_rule_id ? "readonly" : ""}>${esc(basis.scope_summary || "")}</textarea>
      <p class="muted">本说明仅作审核记录。实际限制必须体现在下方结构化条件中；无额外范围限制时也请明确说明。</p>
      <details><summary>结构化诊疗条件（修改后必须先保存，再重新审核）</summary><textarea id="basisSpec" rows="20" class="mono" style="width:100%" ${basis.generated_rule_id ? "readonly" : ""}>${esc(JSON.stringify(spec, null, 2))}</textarea></details>
      <div class="field"><label>审核人</label><input id="basisReviewer" value="${esc(localStorage.getItem("cdssReviewer") || "")}"></div>
      ${Object.entries(labels).map(([key, label]) => `<div><label><input type="checkbox" data-basis-check="${key}"> ${esc(label)}</label></div>`).join("")}
      <div class="field"><label>审核意见（退回时必填）</label><textarea id="basisNote"></textarea></div>
      <div class="actions"><button id="basisSave" class="btn" ${basis.generated_rule_id ? "disabled" : ""}>保存待审依据</button><button id="basisApprove" class="btn primary">确认依据通过</button><button id="basisReject" class="btn">退回修订</button><button id="basisGenerate" class="btn primary" ${basis.validation.status === "active" && !basis.validation.issues.length ? "" : "disabled"}>生成规则草稿</button></div>`);
    const endpoint = `/api/review/bases/${encodeURIComponent(basis.basis_id)}`;
    if (!basis.generated_rule_id) {
      const repair = document.createElement("section");
      repair.className = "detail-section";
      repair.innerHTML = `<h4>修正引文与定位</h4><p>将支持本条依据的原句填写到引文中。若下面定位原文不对，请先对照 PDF 修改页行并保存，再核对新定位原文。</p><h5>当前定位的实际原文</h5><div class="quote">${esc(basis.validation.provenance?.text || "未找到定位原文，请核对指南和页行。")}</div><div class="field"><label>指南标识</label><input data-source-field="guideline_id" value="${esc(source.guideline_id || "")}"></div>${[["page_start", "起始页"], ["page_end", "结束页"], ["line_start", "起始行"], ["line_end", "结束行"]].map(([key, label]) => `<label>${label}<input type="number" min="1" data-source-field="${key}" value="${esc(source[key] || (key === "page_end" ? source.page_start : "") || "")}"></label>`).join(" ")}<div class="field"><label>支持本条依据的原句（引文）</label><textarea data-source-field="quote" rows="5">${esc(source.quote || "")}</textarea></div><button type="button" class="btn primary" data-source-save>保存引文并重新校验</button><p class="muted">请从实际原文中复制支持当前判断的连续原句；文字匹配后仍需确认医学含义。</p>`;
      $("#basisDetail").prepend(repair);
      repair.querySelector("[data-source-save]").onclick = async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        try {
          const specification = JSON.parse($("#basisSpec").value);
          specification.source = {...specification.source};
          repair.querySelectorAll("[data-source-field]").forEach((input) => {
            const key = input.dataset.sourceField;
            specification.source[key] = ["page_start", "page_end", "line_start", "line_end"].includes(key) ? Number(input.value) : input.value.trim();
          });
          await api(endpoint + `?graph_filename=${encodeURIComponent(filename)}`, {method: "PUT", json: {specification, scope_summary: $("#basisScope").value}});
          const result = await api(`/api/review/bases?graph_filename=${encodeURIComponent(filename)}`);
          const updated = result.items.find((item) => item.basis_id === basis.basis_id);
          await showClinicalBasis(updated, filename);
          toast(updated.validation.provenance.status === "verified" ? "引文已匹配实际原文，请继续核对医学含义" : "已保存，请查看新的定位原文和剩余问题");
        } catch (error) { toast(error.message, true); }
        finally { button.disabled = false; }
      };
    }
    pageEl.querySelectorAll("[data-basis-dependency]").forEach((button) => button.onclick = async () => {
      try {
        const ref = basis.validation.references[Number(button.dataset.basisDependency)];
        const item = ref.kind === "node"
          ? { ...ref, item_type: "graph_node", graph_filename: filename }
          : { ...ref, item_type: "graph_relation", graph_filename: filename, item_id: `${ref.source_node_id}|${ref.relation_code}|${ref.target_node_id}` };
        state.reviewTab = ref.kind === "node" ? "graph" : "relations";
        state.reviewQuery = "";
        state.reviewFilters = { graph: filename, sourceDoc: "", nodeType: "", relationCode: "", itemType: "", clinicalIntent: "" };
        await renderReviewCenter();
        await loadReviewDetail(item);
        $("#reviewDetail")?.scrollIntoView({ block: "start" });
      } catch (error) { toast(error.message, true); }
    });
    if ($("#basisEditLogic")) $("#basisEditLogic").onclick = async () => {
      const scopeSummary = $("#basisScope").value;
      await openCompletedBasisEditor(basis, filename, scopeSummary, $("#basisEditLogic"));
    };
    if ($("#basisReviseLogic")) $("#basisReviseLogic").onclick = async () => {
      const button = $("#basisReviseLogic");
      button.disabled = true; button.textContent = "正在汇总最新关联…";
      try {
        const result = await api(endpoint + `/revise-from-reviewed-graph?graph_filename=${encodeURIComponent(filename)}`, { json: {} });
        toast(`已创建修订依据并带入 ${result.used.length} 个已审条件，请重新审核`);
        await renderClinicalBases();
        await showClinicalBasis(result.basis, filename);
      } catch (error) { toast(error.message, true); button.disabled = false; button.textContent = "修订规则条件"; }
    };
    const query = `?graph_filename=${encodeURIComponent(filename)}`;
    const changed = () => $("#basisScope").value !== (basis.scope_summary || "") || $("#basisSpec").value !== JSON.stringify(spec, null, 2);
    const run = (id, action) => $(id).onclick = async () => { try { await action(); await renderClinicalBases(); } catch (error) { toast(error.message, true); } };
    run("#basisSave", () => api(endpoint + query, { method: "PUT", json: { specification: JSON.parse($("#basisSpec").value), scope_summary: $("#basisScope").value } }));
    const review = async (action) => {
      const reviewer = $("#basisReviewer").value.trim();
      localStorage.setItem("cdssReviewer", reviewer);
      const checks = Object.fromEntries([...pageEl.querySelectorAll("[data-basis-check]")].map((input) => [input.dataset.basisCheck, input.checked]));
      if (changed()) {
        if (action !== "approve") throw new Error("内容已修改，请先保存，再审核");
        await api(endpoint + query, { method: "PUT", json: { specification: JSON.parse($("#basisSpec").value), scope_summary: $("#basisScope").value } });
      }
      return api(endpoint + "/review" + query, { json: { action, reviewer, note: $("#basisNote").value, checks } });
    };
    $("#basisApprove").textContent = "确认依据并生成待审规则";
    $("#basisApprove").onclick = async () => {
      const button = $("#basisApprove");
      button.disabled = true;
      button.textContent = "正在校验并生成…";
      try {
        await review("approve");
        const generated = await api(endpoint + "/generate" + query, { json: {} });
        toast(`已生成待审规则：${generated.rule_id}`);
        state.reviewTab = "rules";
        state.reviewQuery = generated.rule_id;
        state.reviewFilters = { graph: filename, sourceDoc: "", nodeType: "", relationCode: "", itemType: "", clinicalIntent: "" };
        markStale("rules", "dashboard");
        await renderReviewCenter();
      } catch (error) {
        toast(error.message, true);
        button.disabled = false;
        button.textContent = "确认依据并生成待审规则";
      }
    };
    run("#basisReject", () => review("reject"));
    run("#basisGenerate", async () => {
      if (changed()) throw new Error("内容已修改，请先保存并重新审核");
      const generated = await api(endpoint + "/generate" + query, { json: {} });
      toast(`规则 ${generated.rule_id} 已进入草稿，须在规则审核中验证后启用`);
    });
    $("#basisDetail").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function renderStandardReview() {
    await loadBase();
    const filters = state.reviewFilters || {};
    const params = new URLSearchParams({ item_type: "standard", limit: "1000" });
    if (filters.graph) params.set("graph_filename", filters.graph);
    if (filters.sourceDoc) params.set("source_doc", filters.sourceDoc);
    if (state.standardReviewQuery) params.set("q", state.standardReviewQuery);
    const queue = await api(`/api/review/queue?${params}`);
    const items = (queue.items || []).filter((item) => !state.standardReviewStatus || item.status === state.standardReviewStatus);
    const selected = items.find((item) => item.item_id === state.standardReviewId) || items[0];
    const tabs = REVIEW_TABS.map(([key, label]) => `<button class="review-tab ${key === "standards" ? "active" : ""}" data-review-tab="${key}">${label}</button>`).join("");
    const s = selected?.standard || {}, source = selected?.source || {};
    const input = (key, label, type = "text") => `<div class="field"><label>${label}</label><input data-standard-field="${key}" type="${type}" ${type === "number" ? 'step="any"' : ""} value="${esc(s[key] ?? "")}"></div>`;
    const detail = selected ? `<h3>${esc(selected.name)} · ${reviewBadge(selected.status)}</h3>
      <p class="mono">${esc(selected.item_id)} · v${selected.version} · ${esc(s.concept_code)}</p>
      <p>对象：${esc(s.item_type === "exam" ? "检查" : "检验")} · ${esc(s.exam_code || s.lab_code)} · ${esc(s.finding_code || "")}</p>
      <div class="form-grid">${input("operator", "比较方式（>、>=、<、<=、=、BETWEEN、OUTSIDE）")}${input("threshold_value", "阈值 / 下界", "number")}${input("threshold_upper", "上界", "number")}${input("threshold_unit", "单位")}${input("context_age_min", "年龄下限", "number")}${input("context_age_max", "年龄上限", "number")}${input("context_gender", "适用性别（M/F，留空不限）")}${input("method", "适用方法")}${input("source_note", "来源说明")}</div>
      <h4>适用条件及关联规则</h4><pre>${esc(JSON.stringify(selected.clinical_context || {}, null, 2))}</pre>
      <p>${esc((selected.rule_ids || []).join("、") || "本院标准 / 未绑定规则")}</p>
      <h4>指南原文</h4><p>${esc(source.guideline_title || s.source_note || "待补充")}</p>
      <p>${esc(source.chunk_id || "")} · 页 ${esc(source.page_start || source.page || "待补充")}～${esc(source.page_end || source.page_start || "")} · 行 ${esc(source.line_start || "待补充")}～${esc(source.line_end || "")}</p>
      <div class="quote">${esc(source.quote || "暂无逐字引文，请核对来源说明")}</div>
      <div id="standardSourceContext"></div>
      ${[...(selected.source_issues || []), ...(selected.configuration_issues || [])].map((issue) => `<p class="result-banner warning">${esc(issue)}</p>`).join("")}
      <p class="muted">修改参数后先保存草稿，再审核通过。退回或驳回会停止该标准的使用。</p>
      ${selected.origin === "guideline" ? `<label><input id="standardScopeConfirmed" type="checkbox">已核对人群、例外与关联规则条件，适用范围已落实</label><div class="field"><label>发布医院（逗号分隔）</label><input id="standardPublishHospitals" value="${esc(state.hospitalId || "")}" placeholder="XM_FIRST,FZ_HOSPITAL"></div>` : `<p>执行医院：${esc(s.hospital_id)}</p>`}
      <div class="field"><label>审核人</label><input id="standardReviewer" value="${esc(localStorage.getItem("cdssReviewer") || "")}"></div>
      <div class="field"><label>审核意见 / 驳回原因</label><textarea id="standardReviewNote"></textarea></div>
      <div class="actions"><button class="btn" data-standard-action="save">保存修改为草稿</button><button class="btn primary" data-standard-action="approve">审核通过并启用</button><button class="btn danger" data-standard-action="reject">驳回</button><button class="btn" data-standard-action="return">退回待审</button></div>` : '<div class="empty">当前筛选下暂无判定标准</div>';
    pageEl.innerHTML = `<div class="review-tabs">${tabs}</div>${reviewCategoriesHtml()}
      ${panel("判定标准审核", `<div class="inline-controls"><input id="standardReviewSearch" placeholder="搜索指标、编码或指南" value="${esc(state.standardReviewQuery || "")}"><select id="standardReviewStatus">${[["", "全部状态"], ["draft", "待审核"], ["active", "已通过"], ["rejected", "已驳回"]].map(([key,label]) => `<option value="${key}" ${state.standardReviewStatus === key ? "selected" : ""}>${label}</option>`).join("")}</select><button id="standardReviewRefresh" class="btn">查询 / 刷新</button><button id="standardReviewClear" class="btn">清除图谱及指南筛选</button></div><p>包含指南自动提取标准和本院标准。草稿不参与正式推理；指南标准通过后发布到指定医院。</p>
      ${queue.truncated?.standard ? '<p class="result-banner warning">超过展示上限，请缩小搜索范围。</p>' : ""}
      <select id="standardReviewSelect" style="width:100%">${items.map((item) => `<option value="${esc(item.item_id)}" ${item === selected ? "selected" : ""}>${esc(item.name)} · ${esc(item.standard.exam_code || item.standard.lab_code)} · ${esc(item.standard.operator)} ${esc(item.standard.threshold_value ?? "待补充")} ${esc(item.standard.threshold_unit || "")} · ${esc(item.standard.hospital_id || "指南候选")} · ${esc(item.status)}</option>`).join("")}</select>`)}${panel("标准详情与审核", detail)}`;
    bindReviewTabs();
    $("#standardReviewRefresh").onclick = () => { state.standardReviewQuery = $("#standardReviewSearch").value; state.standardReviewStatus = $("#standardReviewStatus").value; renderStandardReview(); };
    $("#standardReviewClear").onclick = () => { state.reviewFilters.graph = ""; state.reviewFilters.sourceDoc = ""; renderStandardReview(); };
    $("#standardReviewSelect").onchange = (event) => { state.standardReviewId = event.target.value; renderStandardReview(); };
    if (selected && source.chunk_id) {
      try { $("#standardSourceContext").innerHTML = await sourceRefsHtml([source.chunk_id]); }
      catch (error) { $("#standardSourceContext").textContent = `上下文暂不可用：${error.message}`; }
    }
    pageEl.querySelectorAll("[data-standard-action]").forEach((button) => button.onclick = async () => {
      button.disabled = true;
      try {
        const standard = {};
        pageEl.querySelectorAll("[data-standard-field]").forEach((input) => {
          standard[input.dataset.standardField] = input.type === "number" ? (input.value === "" ? null : Number(input.value)) : input.value.trim();
        });
        const reviewer = $("#standardReviewer").value.trim();
        const result = await api(`/api/review/standards/${encodeURIComponent(selected.item_id)}/review`, { method: "POST", json: {
          action: button.dataset.standardAction, version: selected.version, reviewer, standard,
          note: $("#standardReviewNote").value.trim(), scope_confirmed: $("#standardScopeConfirmed")?.checked || false,
          hospital_ids: ($("#standardPublishHospitals")?.value || "").split(/[,，]+/).map((v) => v.trim()).filter(Boolean),
        }});
        localStorage.setItem("cdssReviewer", reviewer);
        toast(`标准已${({ active: "通过并发布", draft: "保存为待审", rejected: "驳回" })[result.status]}`);
        await renderStandardReview();
      } catch (error) { toast(error.message, true); }
      finally { button.disabled = false; }
    });
  }

  async function renderPendingRevisions() {
    const graphs = await api("/api/kg/graphs");
    const filename = graphs.find((graph) => graph.graph_filename === state.reviewFilters.graph)?.graph_filename
      || graphs[0]?.graph_filename || "";
    state.reviewFilters.graph = filename;
    const result = filename ? await api(`/api/kg/pending-revisions?graph_filename=${encodeURIComponent(filename)}`) : {items: []};
    const fieldNames = {name: "名称", code: "编码", node_type: "实体类型", attributes: "属性", source_node_id: "起点", target_node_id: "终点", relation_code: "关系类型", relation_name: "关系名称"};
    pageEl.innerHTML = `<div class="review-tabs">${REVIEW_TABS.map(([key, label]) => `<button class="review-tab ${key === "revisions" ? "active" : ""}" data-review-tab="${key}">${esc(label)}</button>`).join("")}</div><h3>待审修订</h3><p>已通过版本继续保留。审核下面的内容变化后，新版本才会替换旧版本。</p><select id="revisionGraph">${graphs.map((g) => `<option value="${esc(g.graph_filename)}" ${filename === g.graph_filename ? "selected" : ""}>${esc(g.graph_filename)}</option>`).join("")}</select>${result.items.map((item, index) => panel(`${item.kind === "node" ? "实体" : "关系"}：${item.name}`, table(["变更字段", "已审核版本", "待审版本"], Object.keys(item.after).filter((key) => JSON.stringify(item.before[key]) !== JSON.stringify(item.after[key])).map((key) => `<tr><td>${esc(fieldNames[key] || key)}</td><td>${esc(JSON.stringify(item.before[key]))}</td><td>${esc(JSON.stringify(item.after[key]))}</td></tr>`)) + `<button class="btn primary" data-revision="${index}" data-action="approve">通过修订</button><button class="btn" data-revision="${index}" data-action="reject">不采纳修订</button>`)).join("") || '<div class="empty">当前图谱没有待审修订</div>'}`;
    bindReviewTabs();
    $("#revisionGraph").onchange = (event) => { state.reviewFilters.graph = event.target.value; renderPendingRevisions(); };
    pageEl.querySelectorAll("[data-revision]").forEach((button) => button.onclick = async () => {
      const reviewer = prompt("审核人", localStorage.getItem("cdssReviewer") || "");
      if (!reviewer?.trim()) return;
      const item = result.items[Number(button.dataset.revision)];
      try {
        await api(`/api/kg/pending-revisions/decision?graph_filename=${encodeURIComponent(filename)}`, {json: {kind: item.kind, item_id: item.item_id, revision_hash: item.revision_hash, action: button.dataset.action, reviewer}});
        await renderPendingRevisions();
      } catch (error) { toast(error.message, true); }
    });
  }

  async function renderReviewCenter() {
    // 节点/关系详情的属性表依赖模板契约渲染中文名，直接进入审核台时也需加载模板数据。
    await loadExtractionSettings();
    const tab = state.reviewTab === "stale" ? "graph" : (state.reviewTab || "bydisease");
    if (tab === "revisions") { await renderPendingRevisions(); return; }
    if (tab === "standards") { await renderStandardReview(); return; }
    if (tab === "bases") { await renderClinicalBases(); return; }
    if (tab === "bydisease") { await renderByDisease(); return; }
    const reviewer = localStorage.getItem("cdssReviewer") || "";
    const query = (state.reviewQuery || "").trim();
    const filters = state.reviewFilters || { graph: "", sourceDoc: "", nodeType: "", relationCode: "", itemType: "", clinicalIntent: "" };
    if (state.reviewGraphOptions === null) state.reviewGraphOptions = await api("/api/kg/graphs");
    if (filters.graph && !state.reviewGraphOptions.some((graph) => graph.graph_filename === filters.graph)) filters.graph = "";
    const queueItemType = reviewQueueItemType(tab, filters);
    const showClinicalIntentFilter = ["rules", "blocked"].includes(tab)
      || (tab === "active" && (!filters.itemType || filters.itemType === "rule"));
    const filterParams = [
      filters.graph ? `&graph_filename=${encodeURIComponent(filters.graph)}` : "",
      filters.sourceDoc ? `&source_doc=${encodeURIComponent(filters.sourceDoc)}` : "",
      filters.nodeType ? `&node_type=${encodeURIComponent(filters.nodeType)}` : "",
      filters.relationCode ? `&relation_code=${encodeURIComponent(filters.relationCode)}` : "",
      showClinicalIntentFilter && filters.clinicalIntent ? `&clinical_intent=${encodeURIComponent(filters.clinicalIntent)}` : "",
    ].join("");
    const queueUrl = `/api/review/queue?limit=300${queueItemType ? `&item_type=${queueItemType}` : ""}${filterParams}${query ? `&q=${encodeURIComponent(query)}` : ""}`;
    const [queue, audit] = await Promise.all([
      api(queueUrl),
      tab === "audit" ? api("/api/review/audit?limit=300") : Promise.resolve({ items: [] }),
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
      // 日志 entity_type 与队列 item_type 键不同（node/relation/rule/clinical_basis/graph_node）。
      const auditTypeLabel = { node: "实体", relation: "关系", rule: "规则", clinical_basis: "诊疗依据", graph_node: "实体" };
      // 列顺序：意见放中间（条目之后）；条目列可开关隐藏。
      const showSubject = state.auditShowSubject !== false;
      const auditHeaders = ["时间", "类型", ...(showSubject ? ["条目"] : []), "意见", "动作", "状态变化", "审核人"];
      const buildAuditItems = (withSubject) => (audit.items || []).map((row) => {
        const typeLabel = row.entity_label || auditTypeLabel[row.entity_type] || row.entity_type || "-";
        const subjectHtml = row.entity_name
          ? `<strong>${esc(row.entity_name)}</strong>${row.entity_code ? `<span class="mono muted"> ${esc(row.entity_code)}</span>` : ""}<div class="mono muted" style="font-size:11px">${esc(row.entity_id || "-")}</div>`
          : `<span class="mono">${esc(row.entity_id || "-")}</span>`;
        const cells = [
          `<td class="nowrap">${esc(formatTime(row.ts))}</td>`,
          `<td><span class="type-chip">${esc(typeLabel)}</span></td>`,
          ...(withSubject ? [`<td>${subjectHtml}</td>`] : []),
          `<td>${esc(row.note || "-")}</td>`,
          `<td>${esc(row.action || "-")}</td>`,
          `<td>${statusBadge(row.before_status)} → ${statusBadge(row.after_status)}</td>`,
          `<td>${esc(row.reviewer || "-")}</td>`,
        ];
        return {
          text: [typeLabel, row.entity_name, row.entity_code, row.entity_id, row.action, row.note, row.reviewer].filter(Boolean).join(" "),
          html: `<tr>${cells.join("")}</tr>`,
        };
      });
      pageEl.innerHTML = `<div class="review-tabs">${tabHtml}</div>${reviewCategoriesHtml()}
        ${pagedPanel({
          key: "reviewAudit",
          title: "审核记录",
          description: "全量审核动作的追加式日志；条目显示实体类型与名称（名称下为原始 ID），拒绝原因码记录在意见前缀 [原因码] 中",
          headers: auditHeaders,
          items: buildAuditItems(showSubject),
          actions: `<label class="muted"><input type="checkbox" id="auditShowSubject" ${showSubject ? "checked" : ""}> 显示条目</label>`,
        })}`;
      bindReviewTabs();
      bindPagedTable("reviewAudit");
      $("#auditShowSubject")?.addEventListener("change", (event) => {
        state.auditShowSubject = event.target.checked;
        const withSubject = Boolean(event.target.checked);
        pagedTableData.reviewAudit.headers = ["时间", "类型", ...(withSubject ? ["条目"] : []), "意见", "动作", "状态变化", "审核人"];
        pagedTableData.reviewAudit.items = buildAuditItems(withSubject);
        renderTableBody("reviewAudit");
      });
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
                <strong>${esc(n.name || group.name)}</strong><span class="type-chip">${esc(typeName[group.node_type] || group.node_type)}</span><span class="mono">${esc(n.code)}</span>
                ${n.is_standard_code ? '<span class="badge active">标准码</span>' : '<span class="badge">候选码</span>'}
                ${reviewBadge(n.review_status)}
                <span class="muted">关系 ${n.relation_count} · 引用 ${n.source_ref_count}</span>
              </label>`).join("")}</div>
            <div class="actions"><button class="btn sm primary" data-dupe-merge>合并为一</button></div>
          </div>`).join("") || '<div class="empty">未检测到同类型同名的重复实体</div>');
      pageEl.innerHTML = `<div class="review-tabs">${tabHtml}</div>${reviewCategoriesHtml()}
        <div class="review-list" style="max-width:none">
          <div class="review-toolbar compact review-filterbar">
            <select id="reviewFilterGraph" title="按目的图谱筛选">
              <option value="">请选择图谱</option>
              ${(state.reviewGraphOptions || []).map((g) => `<option value="${esc(g.graph_filename)}" ${filters.graph === g.graph_filename ? "selected" : ""}>${esc(g.graph_filename)}</option>`).join("")}
            </select>
            <span class="muted">这里检查实体重复：同一图谱内、同类型、同中文名称的多个实体；不是关系重复。同名不一定同义，请核对后合并。默认勾选推荐保留节点（标准码优先），「合并为一」吸收其余节点并自动改写规则引用（合并前自动备份）。</span>
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
        ? [item.rule_id, clinicalIntentLabel(item.clinical_intent, item.rule_type), item.graph_filename || ""]
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
        ${generationCellHtml(item)}
        <td class="nowrap muted">${esc(shortTime(item.updated_at))}</td>
      </tr>`;
    });
    pageEl.innerHTML = `
      <div class="review-tabs">${tabHtml}</div>${reviewCategoriesHtml()}
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
            ${tab === "active" ? `<select id="reviewFilterItemType" title="按已通过资产类型筛选">
              <option value="">全部资产类型</option>
              <option value="graph_node" ${filters.itemType === "graph_node" ? "selected" : ""}>实体节点</option>
              <option value="graph_relation" ${filters.itemType === "graph_relation" ? "selected" : ""}>图谱关系</option>
              <option value="rule" ${filters.itemType === "rule" ? "selected" : ""}>规则</option>
            </select>` : ""}
            ${showClinicalIntentFilter ? `<select id="reviewFilterClinicalIntent" title="按规则临床用途筛选">
              <option value="">全部规则用途</option>
              ${CLINICAL_INTENT_OPTIONS.map(([value, label]) => `<option value="${esc(value)}" ${filters.clinicalIntent === value ? "selected" : ""}>${esc(label)}</option>`).join("")}
            </select>` : ""}
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
            ${(filterParams || (tab === "active" && filters.itemType)) ? '<button class="btn sm" id="reviewFilterClear">清除筛选</button>' : ""}
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
            <thead><tr><th class="col-check"></th><th>条目</th><th>状态</th><th>依赖</th><th>生成</th><th>更新</th></tr></thead>
            <tbody>${rows.join("") || '<tr><td colspan="6"><div class="empty">当前页签没有待办条目</div></td></tr>'}</tbody>
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
    $("#reviewFilterItemType")?.addEventListener("change", (event) => {
      state.reviewFilters.itemType = event.target.value;
      if (event.target.value && event.target.value !== "rule") state.reviewFilters.clinicalIntent = "";
      renderReviewCenter();
    });
    bindReviewFilter("#reviewFilterClinicalIntent", "clinicalIntent");
    bindReviewFilter("#reviewFilterNodeType", "nodeType");
    bindReviewFilter("#reviewFilterRelationCode", "relationCode");
    $("#reviewFilterClear")?.addEventListener("click", () => {
      state.reviewFilters = { graph: "", sourceDoc: "", nodeType: "", relationCode: "", itemType: "", clinicalIntent: "" };
      renderReviewCenter();
    });
    $("#reviewSuspectOnly")?.addEventListener("change", (event) => {
      state.reviewSuspectOnly = event.target.checked;
      renderReviewCenter();
    });
    const selectionCount = () => {
      const count = pageEl.querySelectorAll("[data-review-select]:checked").length;
      const el = $("#reviewSelectionCount");
      if (el) el.textContent = `已选 ${count} 条`;
    };
    $("#reviewSelectAll")?.addEventListener("change", (event) => {
      pageEl.querySelectorAll("[data-review-select]").forEach((input) => { input.checked = event.target.checked; });
      selectionCount();
    });
    pageEl.querySelectorAll("[data-review-select]").forEach((input) => input.addEventListener("change", selectionCount));
    pageEl.onclick = (event) => {
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
    pageEl.querySelectorAll("[data-review-bulk]").forEach((button) => button.onclick = () => bulkReview(button.dataset.reviewBulk));
    bindReviewTabs();
    const selected = items.find((item) => reviewItemKey(item) === state.reviewSelectedKey);
    if (selected) loadReviewDetail(selected);
  }

  // ==== 按疾病分组：知识卡片视图（单图谱）====
  async function renderByDisease() {
    const tab = state.reviewTab || "graph";
    const filters = state.reviewFilters || {};
    if (state.reviewGraphOptions == null) {
      try { state.reviewGraphOptions = await api("/api/kg/graphs"); }
      catch (error) { toast(`加载图谱列表失败：${error.message}`, true); }
    }
    const graphOptions = state.reviewGraphOptions || [];
    const graphName = [filters.graph, state.graphFilename].find((name) =>
      graphOptions.some((graph) => graph.graph_filename === name)
    ) || graphOptions[0]?.graph_filename || "";
    state.byDisease = state.byDisease || { subject: "", search: "", composing: false };
    // 重渲染会重建搜索框，先记录焦点与光标，渲染后恢复（防抖过滤不打断输入）
    state.byDisease.composing = false;
    const prevSearch = pageEl.querySelector("#byDiseaseSearch");
    const searchHadFocus = Boolean(prevSearch && document.activeElement === prevSearch);
    const searchSelection = searchHadFocus
      ? { start: prevSearch.selectionStart, end: prevSearch.selectionEnd }
      : null;
    const tabHtml = REVIEW_TABS.map(([key, label]) => `<button class="review-tab ${tab === key ? "active" : ""}" data-review-tab="${key}">${label}</button>`).join("");
    const graphFilterHtml = `<div class="field"><label for="byDiseaseGraphFilter">知识图谱</label>
      <select id="byDiseaseGraphFilter">
        <option value="" ${!graphName ? "selected" : ""} disabled>请选择图谱</option>
        ${graphOptions.map((g) => `<option value="${esc(g.graph_filename)}" ${g.graph_filename === graphName ? "selected" : ""}>${esc(g.graph_filename)}</option>`).join("")}
      </select></div>`;
    const bindGraphFilter = () => {
      $("#byDiseaseGraphFilter")?.addEventListener("change", (event) => {
        state.reviewFilters = { ...(state.reviewFilters || {}), graph: event.target.value };
        state.byDisease.subject = "";
        renderByDisease();
      });
    };
    if (!graphName) {
      pageEl.innerHTML = `<div class="review-tabs">${tabHtml}</div>${reviewCategoriesHtml()}${graphFilterHtml}<div class="empty">按疾病分组视图按单图谱展示，请先在筛选中选择目的图谱。</div>`;
      bindReviewTabs();
      bindGraphFilter();
      return;
    }
    state.reviewFilters = { ...filters, graph: graphName };
    let groups;
    try {
      groups = await api(`/api/kg/relations/groups?graph_filename=${encodeURIComponent(graphName)}&subject_type=disease`);
    } catch (error) {
      pageEl.innerHTML = `<div class="review-tabs">${tabHtml}</div>${reviewCategoriesHtml()}${graphFilterHtml}<div class="empty">加载分组失败：${esc(error.message)}</div>`;
      bindReviewTabs();
      bindGraphFilter();
      return;
    }
    const search = (state.byDisease.search || "").trim().toLowerCase();
    const graphLastImport = groups.last_import_at || "";
    // 以疾病为锚点的分组视图：疾病组头 + 名下证候（带主证/次证角色），
    // 无疾病连接的证候进「未归属证候」区，保证任何主体都有审核入口。
    // 病→证分组是中医图谱特有结构：图谱里没有任何证候归属时（西医图谱
    // 只有疾病主体），退回平铺列表，不显示空组头。
    // 疾病组头与平铺列表都不再显示被拒/退役疾病（终态噪声，名下关系已永久
    // 阻塞）；证候仍仅 active 可见（草稿证候经疾病卡片内的关系审核处理）。
    const hiddenSubjectStatuses = ["rejected", "retired"];
    const visibleSubject = (subject) => subject.node_type === "syndrome"
      ? subject.review_status === "active"
      : !hiddenSubjectStatuses.includes(subject.review_status);
    const diseaseGroups = (groups.disease_groups || []).filter((group) => visibleSubject(group.disease)).map((group) => {
      const syndromes = group.syndromes.filter((item) => visibleSubject(item) && item.membership_status === "active");
      return {
        ...group,
        syndromes,
        pending_total: group.disease.pending_count + syndromes.reduce((sum, item) => sum + item.pending_count, 0),
      };
    });
    const orphanSyndromes = (groups.orphan_syndromes || []).filter(visibleSubject);
    const hasSyndromeMembers = (groups.disease_groups || []).some((group) => group.syndromes.length > 0) || orphanSyndromes.length > 0;
    const grouped = hasSyndromeMembers && diseaseGroups.length > 0;
    const matchSearch = (s) => !search || `${s.name} ${s.display_name || ""} ${s.code}`.toLowerCase().includes(search);
    const visibleGroups = diseaseGroups
      .map((group) => {
        const headMatch = matchSearch(group.disease);
        const syndromes = headMatch ? group.syndromes : group.syndromes.filter(matchSearch);
        return headMatch || syndromes.length ? { ...group, syndromes } : null;
      })
      .filter(Boolean);
    const visibleOrphans = orphanSyndromes.filter(matchSearch);
    state.byDisease.collapsedGroups ||= new Set();
    const collapsed = state.byDisease.collapsedGroups;
    // 疾病组一键全审用：组头 -> [组头 + 名下证候] 的主体节点清单
    state.byDisease.groupMembers = Object.fromEntries(
      diseaseGroups.map((group) => [group.disease.node_id, [group.disease.node_id, ...group.syndromes.map((m) => m.node_id)]]),
    );
    const groupedSyndromes = new Set(diseaseGroups.flatMap((group) => group.syndromes.map((item) => item.node_id)));
    const subjects = (groups.subjects || []).filter((item) => visibleSubject(item) &&
      (!grouped || item.node_type !== "syndrome" || groupedSyndromes.has(item.node_id) ||
        orphanSyndromes.some((orphan) => orphan.node_id === item.node_id)));
    if (state.byDisease.targetCode) {
      const target = subjects.find((s) => s.code === state.byDisease.targetCode);
      if (target) {
        state.byDisease.subject = target.node_id;
        // 展开目标证候所在的疾病组，选中项不被折叠隐藏。
        const owner = diseaseGroups.find((g) => g.syndromes.some((m) => m.node_id === target.node_id));
        if (owner) collapsed.delete(owner.disease.node_id);
      }
      delete state.byDisease.targetCode;
    }
    const groupHtml = (group) => {
      const head = group.disease;
      const isCollapsed = collapsed.has(head.node_id);
      return `<div class="subject-group" data-subject-group="${esc(head.node_id)}">
        ${subjectCardHtml(head, graphLastImport, {
          groupHead: true,
          collapsed: isCollapsed,
          memberCount: group.syndromes.length,
          pendingTotal: group.pending_total,
        })}
        ${!isCollapsed ? `<div class="subject-sublist">${group.syndromes.map((s) => subjectCardHtml(s, graphLastImport, { role: s.role, membershipStatus: s.membership_status })).join("") || '<div class="empty">名下暂无证候</div>'}</div>` : ""}
      </div>`;
    };
    const listHtml = grouped
      ? `${visibleGroups.map(groupHtml).join("")}
         ${visibleOrphans.length ? `<div class="subject-group"><div class="subject-group-label">未归属证候（未与任何中医疾病建立关系）</div><div class="subject-sublist">${visibleOrphans.map((s) => subjectCardHtml(s, graphLastImport)).join("")}</div></div>` : ""}`
      : (subjects.filter(matchSearch).map((s) => subjectCardHtml(s, graphLastImport)).join("") || '<div class="empty">没有匹配的主体节点</div>');
    const hint = `<div class="result-banner"><strong>疾病审核工作台</strong><p>1. 确认疾病与关联实体 → 2. 审核关系 → 3. 按场景核对用途和依据 → 4. 生成并验证规则。已有审核结果自动沿用。</p>当前图谱：${esc(graphName)}</div>`;
    pageEl.innerHTML = `<div class="review-tabs">${tabHtml}</div>${reviewCategoriesHtml()}${graphFilterHtml}${hint}
      <div class="by-disease-layout">
        <div class="subject-list">
          <input id="byDiseaseSearch" type="search" value="${esc(state.byDisease.search || "")}" placeholder="搜索疾病 / 证候 / 编码…">
          <div class="subject-list-scroll">
            ${listHtml || '<div class="empty">没有匹配的主体节点</div>'}
          </div>
        </div>
        <div class="review-detail" id="byDiseaseCard">
          <div class="empty">在左侧选择疾病 / 证候，核对它的知识网<br><span class="muted">症见、检验、检查、诊断依据、用药会按关系类型分组展示</span></div>
        </div>
      </div>`;
    bindReviewTabs();
    bindGraphFilter();
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
    pageEl.querySelectorAll(".subject-card").forEach((card) => card.onclick = () => loadByDiseaseCard(card.dataset.subject));
    pageEl.querySelectorAll("[data-group-toggle]").forEach((button) => button.onclick = (event) => {
      event.stopPropagation();
      const id = button.dataset.groupToggle;
      const collapsed = state.byDisease.collapsedGroups;
      if (collapsed.has(id)) collapsed.delete(id);
      else collapsed.add(id);
      renderByDisease();
    });
    pageEl.querySelectorAll("[data-group-approve-all]").forEach((button) => button.onclick = async (event) => {
      event.stopPropagation();
      const memberIds = (state.byDisease.groupMembers || {})[button.dataset.groupApproveAll];
      if (memberIds?.length) await approveGroupRelations(memberIds, button);
    });
    if (state.byDisease.subject && subjects.some((s) => s.node_id === state.byDisease.subject)) {
      await loadByDiseaseCard(state.byDisease.subject);
    } else {
      state.byDisease.subject = "";
    }
  }

  // 疾病组一键全审：拉取「组头疾病 + 名下全部证候」的待审出边，确认后
  // 走 /api/review/bulk（link_endpoints）。端点被拒/失效的关系先跳过。
  async function approveGroupRelations(memberIds, button) {
    const graphName = (state.reviewFilters || {}).graph || state.graphFilename || "";
    if (!graphName) { toast("请先选择图谱", true); return; }
    let batch;
    try {
      batch = await api("/api/kg/relations/pending-batch", { json: { graph_filename: graphName, subject_node_ids: memberIds } });
    } catch (error) { toast(error.message, true); return; }
    const pending = (batch.items || []).filter((item) => ["draft", "stale"].includes(item.status));
    if (!pending.length) { toast("该疾病名下没有待审关系", true); return; }
    const blocked = pending.filter(relationEndpointBlocked);
    const submit = pending.filter((item) => !relationEndpointBlocked(item));
    if (!submit.length) { toast(`共 ${pending.length} 条待审关系，但端点全部被拒绝或失效，请先恢复实体`, true); return; }
    const linkedHint = "待审端点实体会随关系一并通过";
    const proceed = confirm(blocked.length
      ? `该疾病名下共 ${pending.length} 条待审关系：${blocked.length} 条端点被拒绝/失效将跳过，提交其余 ${submit.length} 条（${linkedHint}）。确认通过？`
      : `将该疾病名下 ${submit.length} 条待审关系全部通过（${linkedHint}）。确认？`);
    if (!proceed) return;
    const originalLabel = button.textContent;
    button.disabled = true;
    button.textContent = "提交中…";
    try {
      const result = await api("/api/review/bulk", { json: {
        items: submit.map(itemPayload),
        action: "approve",
        reviewer: reviewReviewer(),
        note: "按疾病组一键通过",
        link_endpoints: true,
      }});
      const linked = (result.results || []).reduce((sum, entry) => sum + ((entry.result?.linked_endpoints) || []).length, 0);
      const firstError = (result.results || []).find((entry) => !entry.ok);
      const detail = firstError
        ? `；首个失败：${String(typeof firstError.error === "string" ? firstError.error : JSON.stringify(firstError.error)).slice(0, 120)}`
        : "";
      markStale("dashboard", "graph", "graphVisual", "rules");
      toast(`一键通过完成：成功 ${result.succeeded}，失败 ${result.failed}${linked ? `，连带通过端点实体 ${linked} 个` : ""}${detail}`, result.failed > 0);
      await renderByDisease();
    } catch (error) { toast(error.message, true); }
    finally {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  }

  function subjectCardHtml(s, graphLastImport, options = {}) {
    // 本次导入新生成/再次命中的疾病卡片打「新」徽标，悬停显示完整时间。
    const thisImport = s.last_import_at && graphLastImport && s.last_import_at >= graphLastImport;
    const isNew = thisImport && s.created_at && s.created_at >= graphLastImport;
    const timeTitle = [
      s.created_at ? `首次生成 ${formatTime(s.created_at)}` : "历史导入",
      s.last_import_at && s.last_import_at !== s.created_at ? `最近导入 ${formatTime(s.last_import_at)}` : "",
    ].filter(Boolean).join(" · ");
    // 卡片可见时间显示最近导入（再次命中的时间）；只有首次导入过则显示首次生成。
    const latestAt = (s.last_import_at && s.last_import_at !== s.created_at) ? s.last_import_at : s.created_at;
    const timeHtml = latestAt ? `<span class="muted" title="${esc(timeTitle)}">${esc(shortTime(latestAt))}</span>` : "";
    if (options.groupHead) {
      return `<div class="subject-card subject-group-head ${state.byDisease?.subject === s.node_id ? "selected" : ""}" data-subject="${esc(s.node_id)}">
        <button class="group-toggle" data-group-toggle="${esc(s.node_id)}" title="${options.collapsed ? "展开名下证候" : "收起名下证候"}">${options.collapsed ? "▸" : "▾"}</button>
        <strong>${esc(s.display_name || s.name)}</strong>
        <span class="type-chip">${esc(typeName[s.node_type] || s.node_type || "-")}</span>
        ${miniStatus(s.review_status)}
        ${isNew ? `<span class="pending-chip" title="${esc(timeTitle)}">新</span>` : (thisImport ? `<span class="pending-chip warn" title="${esc(timeTitle)}">更新</span>` : "")}
        <span class="muted">证候 ${options.memberCount ?? 0}</span>
        ${(options.pendingTotal ?? s.pending_count) ? `<span class="pending-chip">待审 ${options.pendingTotal ?? s.pending_count}</span>` : '<span class="muted">已清</span>'}
        ${s.suspect_count ? `<span class="pending-chip warn">⚠ ${s.suspect_count}</span>` : ""}
        ${timeHtml}
        ${(options.pendingTotal ?? s.pending_count) ? `<button class="btn sm primary" data-group-approve-all="${esc(s.node_id)}" title="一键通过该疾病名下全部待审关系（含名下证候卡片的出边）；待审端点实体会随关系一并通过，端点被拒绝/失效的会跳过">通过全部</button>` : ""}
      </div>`;
    }
    return `<div class="subject-card subject-member ${state.byDisease?.subject === s.node_id ? "selected" : ""}" data-subject="${esc(s.node_id)}">
      <strong>${esc(s.display_name || s.name)}</strong>
      ${options.role ? `<span class="type-chip ${options.membershipStatus === "active" ? "" : "muted-chip"}" title="${options.membershipStatus === "active" ? "疾病归属关系已审核通过" : "疾病归属关系待审核，确认前只是候选归属"}">${esc(options.role)}${options.membershipStatus === "active" ? "" : "·待审"}</span>` : `<span class="type-chip">${esc(typeName[s.node_type] || s.node_type || "-")}</span>`}
      <div class="sub-meta">
        ${miniStatus(s.review_status)}
        ${isNew ? `<span class="pending-chip" title="${esc(timeTitle)}">新</span>` : (thisImport ? `<span class="pending-chip warn" title="${esc(timeTitle)}">更新</span>` : "")}
        <span>出边 ${s.out_relation_count}</span>
        ${s.pending_count ? `<span class="pending-chip">待审 ${s.pending_count}</span>` : '<span class="muted">已清</span>'}
        ${s.suspect_count ? `<span class="pending-chip warn">⚠ ${s.suspect_count}</span>` : ""}
        ${timeHtml}
      </div>
    </div>`;
  }

  async function loadByDiseaseCard(nodeId) {
    const filters = state.reviewFilters || {};
    const graphName = filters.graph || state.graphFilename || "";
    state.byDisease.subject = nodeId;
    pageEl.querySelectorAll(".subject-card").forEach((card) => card.classList.toggle("selected", card.dataset.subject === nodeId));
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
      <button class="btn" data-disease-review>第 1 步：核对疾病名称、分型与出处</button>
      <div class="detail-sub mono muted">出边 ${detail.totals?.total ?? 0} 条 · 待审 ${detail.totals?.pending ?? 0} · 可疑 ${detail.totals?.suspect ?? 0}</div>
      <div class="result-banner"><strong>本疾病的审核顺序</strong><p>先核对下方实体与关系，再在场景区整理诊断、治疗和用药依据。</p><button class="btn" data-disease-bases>查看本疾病全部诊疗依据</button></div>
      ${diseaseEndpointReviewHtml([...state.byDiseaseItems.values()], detail.graph_filename || graphName, subject)}
      ${sections.map((section) => diseaseSectionHtml(section)).join("") || '<div class="empty">该节点没有出边关系</div>'}
      <div id="diseaseScenarioWorkbench" class="detail-section"><div class="empty">正在整理场景依据…</div></div>`;
    bindByDiseaseCardActions(mount);
    const sceneMount = mount.querySelector("#diseaseScenarioWorkbench");
    try {
      await window.mountScenarioWorkbench(sceneMount, {
        api, esc, graphFilename: graphName, relationLabels: RELATION_LABELS,
        diseaseCode: subject.node_type === "syndrome" ? "" : subject.code,
        syndromeCode: subject.node_type === "syndrome" ? subject.code : "",
        sourceRefBlock, parseSourceRef, fetchRefLines, refDocTitle,
        openBasis: async (basisId, {configureLogic = false} = {}) => {
          state.reviewFilters.graph = graphName;
          state.basisDiseaseCode = subject.code;
          state.reviewTab = "bases";
          await renderClinicalBases();
          const result = await api(`/api/review/bases?graph_filename=${encodeURIComponent(graphName)}`);
          const basis = result.items.find((item) => item.basis_id === basisId);
          if (basis) {
            await showClinicalBasis(basis, graphName);
            if (configureLogic) await openCompletedBasisEditor(basis, graphName, basis.scope_summary || "");
          }
        },
      });
    } catch (error) { sceneMount.textContent = `场景加载失败：${error.message}`; }
  }

  function diseaseEndpointReviewHtml(items, graphFilename, subject) {
    const endpoints = new Map();
    if (subject?.node_id) {
      endpoints.set(subject.node_id, {
        item_type: "graph_node", node_id: subject.node_id, graph_filename: graphFilename,
        name: subject.display_name || subject.name || subject.node_id,
        node_type: subject.node_type, status: subject.review_status || "draft",
      });
    }
    items.forEach((item) => {
      for (const side of ["source", "target"]) {
        const id = item[`${side}_node_id`];
        const status = item[`${side}_status`] || "missing";
        if (id && !endpoints.has(id)) endpoints.set(id, {
          item_type: "graph_node", node_id: id, graph_filename: graphFilename,
          name: item[`${side}_name`] || id, node_type: item[`${side}_node_type`] || "", status,
        });
      }
    });
    state.byDiseaseEndpoints = endpoints;
    const indexed = [...endpoints.values()].map((item, index) => ({item, index}));
    const pending = indexed.filter(({item}) => item.status !== "active");
    const approved = indexed.filter(({item}) => item.status === "active");
    const viewButton = (index) => `<button class="btn sm" data-endpoint-detail="${index}">查看原文与属性</button>`;
    // 卡片级关系批量：跨所有关系分组勾选待审关系（含嵌套小节），
    // 与第 1 步实体勾选同款交互；分组内仍有各自的小批量按钮。
    const pendingRels = items.filter((item) => item.item_type === "graph_relation" && ["draft", "stale"].includes(item.status));
    return `<div class="detail-section" id="diseaseEndpointReview"><h4>第 1 步：核对关系涉及的实体</h4>
      <p>实体与关系联动审核：通过任一关系时，两端待审实体会一并通过（此处无需先手工过一遍）。第 1 步用于需要单独处理实体的场合——查看原文、修改名称、拒绝错误实体，或恢复被拒绝的端点。</p>
      ${pending.length ? `<div class="actions"><label><input type="checkbox" data-endpoint-select-all> 全选可审核实体</label><span class="muted" data-endpoint-selection-count>已选 0 项</span></div>${table(["选择", "尚未通过的实体", "实体类型", "状态", "核对与处理"], pending.map(({item, index}) => `<tr><td><input type="checkbox" data-endpoint-select="${esc(item.node_id)}" ${["draft", "stale"].includes(item.status) ? "" : "disabled"}></td><td>${esc(item.name)}</td><td><span class="type-chip">${esc(typeName[item.node_type] || item.node_type || "类型缺失，请核对")}</span></td><td>${miniStatus(item.status)}</td><td><div class="actions">${viewButton(index)}<button class="btn sm" data-endpoint-edit="${index}">修改</button><button class="btn sm" data-endpoint-ai="${index}">AI 修正</button>${["draft", "stale"].includes(item.status) ? `<button class="btn sm danger" data-endpoint-reject="${index}">拒绝</button>` : ""}${["rejected", "retired"].includes(item.status) ? `<button class="btn sm" data-endpoint-restore="${index}" title="变回待审核：实体会回到待审核，相关关系也会重新变为待审核">恢复</button>` : ""}</div></td></tr>`))}<div class="actions"><button class="btn sm primary" data-endpoint-approve>通过勾选的实体</button><button class="btn sm danger" data-endpoint-reject-selected>拒绝勾选的实体</button></div>` : '<div class="result-banner ok">相关实体均已通过，可直接审核下方关系。</div>'}
      ${approved.length ? `<details><summary>已通过的实体（${approved.length} 个）</summary>${table(
        ["实体名称", "实体类型", "状态", "操作"],
        approved.map(({item, index}) => `<tr><td>${esc(item.name)}</td><td>${esc(typeName[item.node_type] || item.node_type || "-")}</td><td>${miniStatus(item.status)}</td><td>${viewButton(index)}</td></tr>`),
      )}</details>` : ""}
      <h4>第 2 步：审核下方关系</h4><p>核对关系的方向、医学含义和原文后，可勾选逐条，也可整组或整卡批量处理。通过关系时，两端待审实体会随之连带通过；只有端点被拒绝或失效的关系需要先恢复实体。</p>
      ${pendingRels.length ? `<div class="actions rel-batch-bar"><label><input type="checkbox" data-rel-select-all> 全选待审关系（${pendingRels.length} 条）</label><span class="muted" data-rel-selection-count>已选 0 / ${pendingRels.length} 项</span><button class="btn sm primary" data-rel-approve-selected>通过勾选的关系</button><button class="btn sm danger" data-rel-reject-selected>拒绝勾选的关系</button><button class="btn sm primary" data-rel-approve-all title="一次性提交本卡片全部待审关系；涉及未通过实体且不能连带通过的关系会先提示">一键通过全部待审关系</button></div>` : '<div class="muted">本卡待审关系 0 条。</div>'}</div>`;
  }

  async function openEndpointSource(item, trigger) {
    const dialog = document.createElement("dialog");
    dialog.className = "dialog endpoint-source-dialog";
    dialog.setAttribute("aria-label", "实体原文与属性");
    dialog.innerHTML = `<div class="endpoint-source-head"><h3>${esc(item.name || item.node_id)} · 原文与属性</h3><button type="button" class="btn" data-source-close autofocus>关闭</button></div><div class="endpoint-source-body"><div class="empty">正在加载指南原文…</div></div>`;
    document.body.append(dialog);
    dialog.querySelector("[data-source-close]").onclick = () => dialog.close();
    dialog.addEventListener("close", () => { dialog.remove(); trigger?.focus({ preventScroll: true }); }, { once: true });
    dialog.showModal();
    const body = dialog.querySelector(".endpoint-source-body");
    try {
      const detail = await api(`/api/kg/nodes/${encodeURIComponent(item.node_id)}?graph_filename=${encodeURIComponent(item.graph_filename)}`);
      const node = detail.node || {};
      const refs = await sourceRefsHtml(node.source_refs || []);
      if (!dialog.open) return;
      body.innerHTML = `<div class="detail-section"><h4>实体信息</h4><strong>${esc(node.name || item.name || item.node_id)}</strong><div class="muted">实体类型：${esc(typeName[node.node_type] || node.node_type || "类型缺失，请核对")} · 编码：${esc(node.code || "-")}</div></div><div class="detail-section"><h4>指南原文与上下文</h4>${refs || '<div class="muted">暂无原文引用，请补充出处后核对。</div>'}</div><div class="detail-section"><h4>实体属性</h4>${attrTableHtml(node.attributes)}</div>`;
      body.querySelectorAll(".ref-chip[data-ref]").forEach((button) => button.onclick = async () => {
        button.disabled = true;
        try {
          const wrap = document.createElement("div");
          wrap.innerHTML = await sourceRefBlock(button.dataset.ref);
          button.replaceWith(...wrap.childNodes);
        } catch (error) { button.disabled = false; toast(error.message, true); }
      });
    } catch (error) { if (dialog.open) body.textContent = `原文加载失败：${error.message}`; }
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
          ${relItems.length ? `<button class="btn sm" data-section-ai="${esc(section.section_key)}">批量 AI 审核</button>` : ""}
          ${pending.length ? `<button class="btn sm primary" data-section-action="approve" data-section-key="${esc(section.section_key)}">批量通过本组关系</button>
          <button class="btn sm danger" data-section-action="reject" data-section-key="${esc(section.section_key)}">批量拒绝本组关系</button>` : '<span class="muted">无待审</span>'}
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
          ${(nested.items || []).length ? `<button class="btn sm" data-section-ai="${esc(nested.section_key)}">批量 AI 审核</button>` : ""}
          ${pending.length ? `<button class="btn sm primary" data-section-action="approve" data-section-key="${esc(nested.section_key)}">批量通过本组关系</button>
          <button class="btn sm danger" data-section-action="reject" data-section-key="${esc(nested.section_key)}">批量拒绝本组关系</button>` : ""}
        </div>
      </div>
      <table><tbody>${(nested.items || []).map((item) => diseaseRelationRowHtml(item)).join("")}</tbody></table>
    </div>`;
  }

  function diseaseRelationRowHtml(item) {
    const pendingRow = ["draft", "stale"].includes(item.status);
    const actions = [`<button class="btn sm" data-rel-ai="${esc(item.item_id)}">AI 审核</button>`];
    if (pendingRow) {
      actions.push(`<button class="btn sm primary" data-rel-action="approve" data-rel-id="${esc(item.item_id)}">通过</button>`);
      actions.push(`<button class="btn sm danger" data-rel-action="reject" data-rel-id="${esc(item.item_id)}">拒绝</button>`);
    } else if (item.status === "active") {
      actions.push(`<button class="btn sm" data-rel-action="retire" data-rel-id="${esc(item.item_id)}">失效</button>`);
    } else {
      actions.push(`<button class="btn sm" data-rel-action="restore" data-rel-id="${esc(item.item_id)}">恢复</button>`);
    }
    return `<tr>
      <td>${pendingRow ? `<input type="checkbox" data-rel-select="${esc(item.item_id)}" title="勾选后可在上方批量通过 / 拒绝">` : ""}</td>
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

  function collectSectionDirectItems(sectionKey) {
    for (const section of state.byDiseaseSections || []) {
      if (section.section_key === sectionKey) return [...(section.items || [])];
      for (const container of section.containers || []) {
        for (const nested of container.sections || []) {
          if (nested.section_key === sectionKey) return [...(nested.items || [])];
        }
      }
    }
    return [];
  }

  function bindByDiseaseCardActions(mount) {
    mount.onchange = (event) => {
      // 关系勾选：跨分组批量选择的联动（全选 / 半选态 / 计数）
      if (event.target.matches("[data-rel-select-all], [data-rel-select]")) {
        const inputs = [...mount.querySelectorAll("[data-rel-select]")];
        const all = mount.querySelector("[data-rel-select-all]");
        if (event.target.matches("[data-rel-select-all]")) {
          inputs.forEach((input) => { input.checked = event.target.checked; });
        }
        const count = inputs.filter((input) => input.checked).length;
        if (all) {
          all.checked = inputs.length > 0 && count === inputs.length;
          all.indeterminate = count > 0 && count < inputs.length;
        }
        const summary = mount.querySelector("[data-rel-selection-count]");
        if (summary) summary.textContent = `已选 ${count} / ${inputs.length} 项`;
        return;
      }
      if (!event.target.matches("[data-endpoint-select-all], [data-endpoint-select]")) return;
      const inputs = [...mount.querySelectorAll("[data-endpoint-select]:not(:disabled)")];
      const all = mount.querySelector("[data-endpoint-select-all]");
      if (event.target.matches("[data-endpoint-select-all]")) {
        inputs.forEach((input) => { input.checked = event.target.checked; });
      }
      const count = inputs.filter((input) => input.checked).length;
      if (all) {
        all.checked = inputs.length > 0 && count === inputs.length;
        all.indeterminate = count > 0 && count < inputs.length;
      }
      const summary = mount.querySelector("[data-endpoint-selection-count]");
      if (summary) summary.textContent = `已选 ${count} / ${inputs.length} 项`;
    };
    // 卡片级关系批量通过：与分组批量同一确认流程——先剔除端点阻塞项，
    // 有阻塞时提示先去第 1 步核对实体（归属关系可连带通过端点，不算阻塞）。
    const bulkApproveRelations = async (candidates, button) => {
      // 联动审核：draft/stale 端点实体随关系一并提交（link_endpoints），
      // 仅被拒绝/已失效/缺失端点仍需先恢复实体，不能静默激活。
      const pending = candidates.filter((item) => ["draft", "stale"].includes(item.status));
      if (!pending.length) { toast("所选条目里没有待审关系", true); return; }
      let submit = pending;
      const blocked = pending.filter(relationEndpointBlocked);
      if (blocked.length) {
        $("#diseaseEndpointReview")?.scrollIntoView({ block: "start" });
        const rest = pending.length - blocked.length;
        const proceed = confirm(
          `${blocked.length} 条关系的端点实体被拒绝、已失效或缺失，系统不会连带恢复。` +
          (rest ? `\n\n确定后仅提交其余 ${rest} 条（其余关系的待审端点实体会随关系一并通过）；` : "\n\n当前没有可提交条目；") + "取消则不做操作。"
        );
        if (!proceed) return;
        submit = pending.filter((item) => !relationEndpointBlocked(item));
        if (!submit.length) { toast("没有可提交的条目：相关端点被拒绝或失效，请先恢复实体", true); return; }
      }
      const originalLabel = button?.textContent || "";
      if (button) { button.disabled = true; button.textContent = "提交中…"; }
      try {
        const result = await api("/api/review/bulk", { json: {
          items: submit.map(itemPayload),
          action: "approve",
          reviewer: reviewReviewer(),
          note: "",
          link_endpoints: true,
        }});
        const linked = (result.results || []).reduce((sum, entry) => sum + ((entry.result?.linked_endpoints) || []).length, 0);
        const firstError = (result.results || []).find((entry) => !entry.ok);
        const detail = firstError
          ? `；首个失败：${String(typeof firstError.error === "string" ? firstError.error : JSON.stringify(firstError.error)).slice(0, 120)}`
          : "";
        markStale("dashboard", "graph", "graphVisual", "rules");
        toast(`关系批量通过：成功 ${result.succeeded}，失败 ${result.failed}${linked ? `，连带通过端点实体 ${linked} 个` : ""}${detail}`, result.failed > 0);
        await loadByDiseaseCard(state.byDisease.subject);
      } catch (error) { toast(error.message, true); }
      finally {
        if (button) { button.disabled = false; button.textContent = originalLabel; }
      }
    };
    const refresh = async () => {
      await renderByDisease();
    };
    mount.onclick = async (event) => {
      if (event.target.closest("[data-disease-review]")) {
        const item = { item_type: "graph_node", node_id: state.byDisease.subject, graph_filename: state.reviewFilters.graph };
        state.reviewTab = "graph";
        await renderReviewCenter();
        await loadReviewDetail(item);
        return;
      }
      if (event.target.closest("[data-disease-bases]")) {
        try {
          const detail = await api(`/api/kg/nodes/${encodeURIComponent(state.byDisease.subject)}?graph_filename=${encodeURIComponent(state.reviewFilters.graph)}`);
          state.basisDiseaseCode = detail.node?.code || "";
          state.reviewTab = "bases";
          await renderClinicalBases();
        } catch (error) { toast(error.message, true); }
        return;
      }
      const endpointDetail = event.target.closest("[data-endpoint-detail]");
      if (endpointDetail) {
        const item = [...(state.byDiseaseEndpoints?.values() || [])][Number(endpointDetail.dataset.endpointDetail)];
        if (!item) return;
        await openEndpointSource(item, endpointDetail);
        return;
      }
      const endpointEdit = event.target.closest("[data-endpoint-edit], [data-endpoint-ai]");
      if (endpointEdit) {
        const index = Number(endpointEdit.dataset.endpointEdit ?? endpointEdit.dataset.endpointAi);
        const item = [...(state.byDiseaseEndpoints?.values() || [])][index];
        if (item) await openNodeNameEditor(item, { startWithAi: endpointEdit.hasAttribute("data-endpoint-ai") });
        return;
      }
      const endpointReject = event.target.closest("[data-endpoint-reject]");
      if (endpointReject) {
        const item = [...(state.byDiseaseEndpoints?.values() || [])][Number(endpointReject.dataset.endpointReject)];
        if (item) openReasonDialog(item, "reject");
        return;
      }
      const endpointRestore = event.target.closest("[data-endpoint-restore]");
      if (endpointRestore) {
        const item = [...(state.byDiseaseEndpoints?.values() || [])][Number(endpointRestore.dataset.endpointRestore)];
        if (!item) return;
        try {
          // 拒绝/失效实体的反悔入口：恢复到待审核（draft），受影响关系随之重置。
          await submitReviewAction(item, "restore", "");
          toast(`${ACTION_DONE_LABELS.restore || "已恢复"}：${reviewItemTitle(item)}`);
          await refresh();
        } catch (error) { toast(error.message, true); }
        return;
      }
      const rejectSelected = event.target.closest("[data-endpoint-reject-selected]");
      if (rejectSelected) {
        const selected = [...mount.querySelectorAll("[data-endpoint-select]:checked")]
          .map((input) => state.byDiseaseEndpoints.get(input.dataset.endpointSelect)).filter(Boolean);
        if (!selected.length) { toast("请先勾选需要拒绝的实体", true); return; }
        openReasonDialog({ multi: selected }, "reject");
        return;
      }
      const approveEndpoints = event.target.closest("[data-endpoint-approve]");
      if (approveEndpoints) {
        const selected = [...mount.querySelectorAll("[data-endpoint-select]:checked")]
          .map((input) => state.byDiseaseEndpoints.get(input.dataset.endpointSelect)).filter(Boolean);
        if (!selected.length) { toast("请先查看并勾选确认正确的实体", true); return; }
        approveEndpoints.disabled = true;
        approveEndpoints.textContent = "提交中…";
        try {
          const result = await api("/api/review/bulk", { json: { items: selected.map(itemPayload), action: "approve", reviewer: reviewReviewer(), note: "按疾病查看：人工核对后通过勾选实体" } });
          const failure = (result.results || []).find((entry) => !entry.ok);
          toast(`实体审核：成功 ${result.succeeded}，失败 ${result.failed}${failure ? `；${JSON.stringify(failure.error)}` : ""}`, result.failed > 0);
          markStale("dashboard", "graph", "graphVisual", "rules");
          await loadByDiseaseCard(state.byDisease.subject);
        } catch (error) { toast(error.message, true); }
        finally { approveEndpoints.disabled = false; approveEndpoints.textContent = "通过勾选的实体"; }
        return;
      }
      const relationAi = event.target.closest("[data-rel-ai]");
      if (relationAi) {
        const item = state.byDiseaseItems?.get(relationAi.dataset.relAi);
        if (item) openRelationAiReview(item);
        return;
      }
      const single = event.target.closest("[data-rel-action]");
      if (single) {
        const item = state.byDiseaseItems?.get(single.dataset.relId);
        if (!item) return;
        const action = single.dataset.relAction;
        if (action === "reject" || action === "retire") { openReasonDialog(item, action); return; }
        try {
          if (action === "approve" && relationEndpointBlocked(item)) {
            $("#diseaseEndpointReview")?.scrollIntoView({ block: "start" });
            toast("这条关系的端点实体被拒绝、失效或缺失，请先在上方恢复/补全实体后再审核", true);
            return;
          }
          await submitReviewAction(item, action, "");
          toast(`${ACTION_DONE_LABELS[action] || "已处理"}：${reviewItemTitle(item)}`);
          await refresh();
        } catch (error) { toast(error.message, true); }
        return;
      }
      const sectionAi = event.target.closest("[data-section-ai]");
      if (sectionAi) {
        const items = collectSectionDirectItems(sectionAi.dataset.sectionAi);
        const label = items[0] ? relationLabel(items[0]) : "本组关系";
        openBatchRelationAiReview(items, label);
        return;
      }
      const sectionBtn = event.target.closest("[data-section-action]");
      if (sectionBtn) {
        const action = sectionBtn.dataset.sectionAction;
        const pending = collectSectionItems(sectionBtn.dataset.sectionKey)
          .filter((item) => ["draft", "stale"].includes(item.status));
        if (!pending.length) return;
        if (action === "reject") { openReasonDialog({ multi: pending }, "reject"); return; }
        // 通过走统一联动流程（draft/stale 端点随关系连带通过）
        await bulkApproveRelations(pending, sectionBtn);
      }
      // —— 第 2 步工具栏：卡片级关系批量 ——
      const relApproveSelected = event.target.closest("[data-rel-approve-selected]");
      if (relApproveSelected) {
        const selected = [...mount.querySelectorAll("[data-rel-select]:checked")]
          .map((input) => state.byDiseaseItems.get(input.dataset.relSelect)).filter(Boolean);
        if (!selected.length) { toast("请先勾选需要通过的关系", true); return; }
        await bulkApproveRelations(selected, relApproveSelected);
        return;
      }
      const relApproveAll = event.target.closest("[data-rel-approve-all]");
      if (relApproveAll) {
        const pending = [...(state.byDiseaseItems?.values() || [])]
          .filter((item) => item.item_type === "graph_relation" && ["draft", "stale"].includes(item.status));
        if (!pending.length) { toast("本卡没有待审关系", true); return; }
        await bulkApproveRelations(pending, relApproveAll);
        return;
      }
      const relRejectSelected = event.target.closest("[data-rel-reject-selected]");
      if (relRejectSelected) {
        const selected = [...mount.querySelectorAll("[data-rel-select]:checked")]
          .map((input) => state.byDiseaseItems.get(input.dataset.relSelect)).filter(Boolean);
        if (!selected.length) { toast("请先勾选需要拒绝的关系", true); return; }
        openReasonDialog({ multi: selected }, "reject");
        return;
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
    const purposeRules = state.ruleClinicalIntent
      ? domainRules.filter((rule) => (rule.clinical_intent || defaultClinicalIntent(rule.rule_type)) === state.ruleClinicalIntent)
      : domainRules;
    const diseaseMap = new Map();
    purposeRules.forEach((rule) => {
      if (rule.disease_code) diseaseMap.set(rule.disease_code, rule.disease_name || rule.disease_code);
    });
    if (state.ruleDisease && !diseaseMap.has(state.ruleDisease)) state.ruleDisease = "";
    const visibleRules = state.ruleDisease
      ? purposeRules.filter((rule) => rule.disease_code === state.ruleDisease)
      : purposeRules;
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
    const clinicalIntentFilter = `<select id="ruleClinicalIntentFilter">
      <option value="">全部规则用途</option>
      ${CLINICAL_INTENT_OPTIONS.map(([value, label]) => `<option value="${esc(value)}" ${state.ruleClinicalIntent === value ? "selected" : ""}>${esc(label)}</option>`).join("")}
    </select>`;
    const inconsistentIds = new Set(
      (state.ruleSync?.rules || []).filter((item) => item.semantic_status !== "consistent").map((item) => item.rule_id)
    );
    pageEl.innerHTML = panel("规则列表", table(["规则 / 结论", "目标证候/诊断", "条件逻辑", "关联概念", "图谱语义", "图谱依赖", "所属图谱", "优先级", "状态", "操作"], visibleRules.map((r) => {
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
      return `<tr><td><strong>${esc(r.name)}</strong><div class="mono muted">${esc(r.rule_id)} v${r.version}</div><div>${badge(r.rule_type || "diagnosis")} ${badge(clinicalIntentLabel(r.clinical_intent, r.rule_type))} ${badge(normalizeDomainClient(r.domain))}</div></td><td>${esc(disease ? disease.name_cn : r.disease_name || r.disease_code)}</td><td><div class="quote">${esc(conditionText(r.conditions))}</div></td><td>${ruleConceptChips(r)}</td><td>${ruleSyncBadge(r.rule_id)}</td><td>${dependencyCell}</td><td class="mono muted">${esc(r.graph_filename || "stemi_graph.json")}</td><td>${r.priority}</td><td>${reviewBadge(r.status)}</td><td><div class="actions">${completeButton}${approveDepsButton}<button class="btn sm" data-rule-edit="${esc(r.rule_id)}">编辑补充后再审核</button><button class="btn sm" data-rule-history="${esc(r.rule_id)}">历史</button><button class="btn sm" data-rule-status="${esc(r.rule_id)}" data-rule-action="${ruleAction.action}">${ruleAction.label}</button><button class="btn sm danger" data-rule-delete="${esc(r.rule_id)}">删除</button></div></td></tr>`;
    })), `<span class="nowrap">${domainFilter}${clinicalIntentFilter}${diseaseFilter}${syncStatus}<span id="engineChip" class="nowrap"></span><button id="ruleWizard" class="btn primary">规则向导</button><button id="completeConditions" class="btn">批量补全条件</button><button id="activateConsistent" class="btn">批量启用一致规则</button><button id="newRule" class="btn primary">新建规则</button></span>`);
    $("#newRule").onclick = () => openRuleEditor(null);
    $("#ruleWizard").onclick = () => openRuleWizard();
    renderEngineChip(pageEl.querySelector("#engineChip"));
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
    pageEl.querySelectorAll("[data-rule-complete]").forEach((button) => button.onclick = async () => {
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
    $("#ruleClinicalIntentFilter").onchange = (event) => {
      state.ruleClinicalIntent = event.target.value;
      state.ruleDisease = "";
      renderRules();
    };
    $("#ruleDomainFilter").onchange = (event) => {
      state.ruleDomain = event.target.value;
      state.ruleDisease = "";
      renderRules();
    };
    pageEl.querySelectorAll("[data-rule-edit]").forEach((b) => b.onclick = () => openRuleEditor(state.rules.find((r) => r.rule_id === b.dataset.ruleEdit), { reviewRevision: true }));
    pageEl.querySelectorAll("[data-rule-status]").forEach((b) => b.onclick = async () => {
      try {
        await api(`/api/rules/${encodeURIComponent(b.dataset.ruleStatus)}/review`, {
          json: {
            action: b.dataset.ruleAction,
            reviewer: localStorage.getItem("cdssReviewer") || "未署名",
          },
        });
        toast("状态已更新");
        markStale("dashboard", "review");
        reload();
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
    pageEl.querySelectorAll("[data-rule-approve-deps]").forEach((button) => button.onclick = () => {
      if (!confirm("将自动通过该规则的全部待审依赖（先节点后关系；被拒绝/失效/缺失项会跳过并提示），并恢复被阻断的规则。确认继续？")) return;
      approveRuleDependencies(button.dataset.ruleApproveDeps);
    });
    pageEl.querySelectorAll("[data-rule-deps]").forEach((button) => {
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
    pageEl.querySelectorAll("[data-rule-delete]").forEach((b) => b.onclick = async () => {
      if (!confirm("确认删除该规则？")) return;
      await api(`/api/rules/${b.dataset.ruleDelete}`, { method: "DELETE" }); toast("规则已删除"); markStale("dashboard", "review"); reload();
    });
    pageEl.querySelectorAll("[data-rule-history]").forEach((button) => {
      button.onclick = () => openRuleHistory(button.dataset.ruleHistory);
    });
  }

  // 条件树编辑器的字典（跨弹窗缓存一次）
  let editorDictPromise = null;
  function mergeConditionEntries(shared, graphEntries) {
    const byCode = new Map();
    [...(shared || []), ...(graphEntries || [])].forEach((item) => {
      if (!item?.code) return;
      const previous = byCode.get(item.code) || {};
      byCode.set(item.code, { ...previous, ...item, name: item.name || item.name_cn || previous.name || item.code });
    });
    return [...byCode.values()];
  }

  async function loadEditorDict(graphFilename = "") {
    const kinds = ["disease", "syndrome", "lab", "symptom", "exam", "exam_finding", "medication", "tongue", "pulse", "sign", "constitution", "pathogenesis"];
    if (!editorDictPromise) {
      editorDictPromise = Promise.all(kinds.map((kind) => api(`/api/vocab/entities/${kind}`).catch(() => [])))
        .then((items) => Object.fromEntries(kinds.map((kind, index) => [kind, items[index] || []])));
    }
    const shared = await editorDictPromise;
    const dict = Object.fromEntries(Object.entries(shared).map(([key, entries]) => [key, [...entries]]));
    dict.exam_concept = (state.vocab || []).filter((item) => item.is_active !== false && (["finding_exists", "static_threshold", "qualitative"].includes(item.evaluator_type) || item.category === "severity"));
    dict.lab_concept = (state.vocab || []).filter((item) => item.category === "lab_concept" && item.is_active !== false);
    if (graphFilename) {
      try {
        const graph = await api(`/api/kg/graph?graph_filename=${encodeURIComponent(graphFilename)}`);
        const nodes = Array.isArray(graph.nodes) ? graph.nodes : Object.values(graph.nodes || {});
        kinds.forEach((kind) => {
          dict[kind] = mergeConditionEntries(dict[kind], nodes.filter((node) => node.node_type === kind));
        });
        dict.exam_finding = mergeConditionEntries(dict.exam_finding, (graph.evidences || [])
          .filter((entry) => entry.finding_code)
          .map((entry) => ({ code: entry.finding_code, name: entry.description || entry.finding_code, exam_code: entry.exam_code })));
      } catch (error) { toast(`当前图谱项目加载失败，暂使用共享词典：${error.message}`, true); }
    }
    return dict;
  }

  const CT_LEAF_LABELS = {
    lab_concept: "检验概念", exam_finding: "检查发现", symptom: "症状", vital: "生命体征",
    demographic: "人口学", past_history: "既往史", medication: "用药", observation: "观察（舌/脉/征）",
    attribute: "属性",
  };

  function openRuleEditor(rule, options = {}) {
    const base = rule || { rule_id: `CUSTOM-${Date.now()}`, version: 1, domain: "western", anchor_type: "disease", rule_type: "diagnosis", clinical_intent: "diagnosis_confirmation", name: "", disease_code: "", disease_name: "", priority: 50, status: "draft", department: "心血管内科", conditions: { type: "group", operator: "AND", children: [] }, conclusion: { diagnosis: "", confidence: "medium", message: "", action: "" }, source: {}, exclusions: [], tags: [] };
    let westernEditor = null;
    const ruleTypes = ["diagnosis", "treatment", "contraindication", "monitoring", "tcm_pattern", "tcm_treatment", "tcm_prescription", "tcm_contraindication"];
    const selectedIntent = base.clinical_intent || defaultClinicalIntent(base.rule_type);
    $("#editorBody").innerHTML = `
      <h3>${options.clinicalBasis ? (rule ? "编辑诊疗依据" : "新增诊疗依据") : (rule ? "编辑规则" : "新建规则")}</h3>
      <div class="form-grid">
        <div class="field"><label>${options.clinicalBasis ? "依据编号" : "规则 ID"}</label><input name="rule_id" value="${esc(base.rule_id)}" ${rule ? "readonly" : ""}></div>
        <div class="field"><label>名称</label><input name="name" value="${esc(base.name)}"></div>
        <div class="field"><label>领域</label><select name="domain"><option value="western" ${normalizeDomainClient(base.domain) === "western" ? "selected" : ""}>西医</option><option value="tcm" ${normalizeDomainClient(base.domain) === "tcm" ? "selected" : ""}>中医</option></select></div>
        <div class="field"><label>${options.clinicalBasis ? "诊疗依据类型" : "规则类型"}</label><select name="rule_type">${ruleTypes.map((type) => `<option value="${type}" ${base.rule_type === type ? "selected" : ""}>${type}</option>`).join("")}</select></div>
        <div class="field"><label>临床用途</label><select name="clinical_intent">${CLINICAL_INTENT_OPTIONS.map(([value, label]) => `<option value="${value}" ${selectedIntent === value ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></div>
        <div class="field"><label>锚点类型</label><select name="anchor_type"><option value="disease" ${base.anchor_type === "disease" ? "selected" : ""}>疾病</option><option value="syndrome" ${base.anchor_type === "syndrome" ? "selected" : ""}>证候</option></select></div>
        <div class="field"><label>目标编码（疾病/证候）</label><input name="disease_code" value="${esc(base.disease_code)}"></div>
        <div class="field"><label>目标名称</label><input name="disease_name" value="${esc(base.disease_name)}"></div>
        <div class="field"><label>状态</label><select name="status" ${options.clinicalBasis || options.reviewRevision ? "disabled" : ""}>${["draft", "testing", "active", "retired"].map((s) => `<option value="${s}" ${base.status === s ? "selected" : ""}>${s}</option>`).join("")}</select></div>
        <div class="field"><label>优先级</label><input name="priority" type="number" value="${base.priority}"></div>
        <div class="field"><label>科室</label><input name="department" value="${esc(base.department || "")}"></div>
        <div class="field"><label>置信度</label><select name="confidence">${["high", "medium", "low"].map((s) => `<option value="${s}" ${base.conclusion.confidence === s ? "selected" : ""}>${s}</option>`).join("")}</select></div>
        <div class="field half"><label>提示</label><input name="message" value="${esc(base.conclusion.message)}"></div>
        <div class="field half"><label>动作</label><input name="action" value="${esc(base.conclusion.action || "")}"></div>
        ${normalizeDomainClient(base.domain) === "western" ? '<div class="field wide" id="westernRecommendationEditor"></div>' : ''}
        ${base.rule_type === "treatment" ? `<div class="field wide"><label>已带入的治疗方案与项目</label><div class="result-banner"><strong>${esc(base.conclusion?.treatment_name || "尚未关联具体治疗方案")}</strong><div class="mono">${esc(base.conclusion?.treatment_plan_id || "")}</div>${(base.conclusion?.treatment_items || []).length ? table(["项目", "类型", "编码"], base.conclusion.treatment_items.map((item) => `<tr><td>${esc(item.name || item.code)}</td><td>${esc(typeName[item.entity_type] || item.entity_type || "-")}</td><td>${esc(item.code)}</td></tr>`)) : '<p>当前依据没有已带入的治疗项目。请核对是否已关联治疗方案，以及方案和项目的关系是否审核通过。</p>'}<p>${esc(base.conclusion?.message || "")}</p></div><div class="result-banner warning">下方条件树表示“什么情况下使用本方案”，上方治疗项目表示“执行什么”。子条件为空时，执行引擎仅在同一疾病诊断规则命中后评估此方案；如原文有分型、人群、阶段或禁忌限制，仍须在条件中明确表达。</div></div>` : ""}
        <div class="field wide"><label>${base.rule_type === "treatment" ? "治疗适用条件" : "条件树"} <span id="drlBadge"></span><button type="button" id="treeModeToggle" class="btn sm ct-toggle">JSON 模式</button></label><div id="conditionTreeHost" class="condition-tree"></div><textarea name="conditions" class="code" hidden>${esc(JSON.stringify(base.conditions, null, 2))}</textarea><div id="drlPreview" class="drl-preview" hidden><pre></pre></div></div>
        <div class="field wide"><label>排除疾病编码（逗号分隔）</label><input name="exclusions" value="${esc((base.exclusions || []).join(","))}"></div>
        <div class="field wide"><label>条件引用概念</label><div id="ruleConceptCheck" class="actions">${ruleConceptChips(base)}</div></div>
        ${["population", "subtype", "phase", "recommendation_strength", "evidence_grade"].map((key) => `<div class="field half"><label>${esc({population: "适用人群", subtype: "疾病分型", phase: "病程／治疗阶段", recommendation_strength: "推荐等级（按原文）", evidence_grade: "证据等级（按原文）"}[key])}</label><input name="context_${key}" value="${esc(base.clinical_context?.[key] || "")}" placeholder="原文未说明时留空，核对时明确记录"></div>`).join("")}
        <div class="field half"><label>例外与禁忌说明（每行一项，实际限制还须写入条件）</label><textarea name="context_exceptions">${esc((base.clinical_context?.exceptions || []).join("\n"))}</textarea></div>
        ${base.clinical_context?.schema_version === 3 && base.clinical_context?.logic_origin === "ai_suggested" ? '<div class="field wide"><label><input type="checkbox" name="confirm_tcm_logic">我已核对原文并确认当前AI建议组合，作为人工确认逻辑保存</label></div>' : ""}
        <div class="field half"><label>待补信息（核对解决后移除，每行一项）</label><textarea name="context_missing">${esc((base.clinical_context?.missing_information || []).join("\n"))}</textarea></div>
        <div class="field wide"><label>结论扩展 JSON（治疗方案／中医治法／方剂／中药）</label><textarea name="conclusion_extra" class="code">${esc(JSON.stringify({ ...base.conclusion, diagnosis: undefined, confidence: undefined, message: undefined, action: undefined }, null, 2))}</textarea></div>
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
    let multiSelection = null;

    const fieldWidgetHtml = (node, field, path, readonly) => {
      const key = ctree.pathKey(path);
      const value = node[field.key];
      if (field.widget === "enum" && field.readonly) {
        return `<span class="ct-fixed">${esc(value || "")}</span>`;
      }
      const common = `data-ct-field="${field.key}" data-path="${key}" data-widget="${field.widget}"`;
      if (field.widget === "dict") {
        const dictionary = field.dict === "observation" ? node.observation_type : field.dict;
        const items = ((editorDict && editorDict[dictionary]) || []).filter((item) =>
          field.key !== "finding_code" || !node.exam_code || !item.exam_code || item.exam_code === node.exam_code);
        const currentOnly = value && !items.some((item) => item.code === value)
          ? `<option value="${esc(value)}" selected>${esc(value)}（当前编码，未在可选项目中找到）</option>` : "";
        const options = ['<option value="">请选择…</option>', currentOnly].concat(items.map((item) => {
          const name = item.name || item.name_cn || item.code;
          return `<option value="${esc(item.code)}" ${value === item.code ? "selected" : ""}>${esc(name)}（${esc(item.code)}）</option>`;
        }));
        return `<div class="ct-dict-picker"><select ${common}>${options.join("")}</select>${!readonly && field.key !== "exam_code" ? `<button type="button" class="btn sm" data-ct-multi-open="${esc(field.key)}" data-path="${key}">多选</button>` : ""}</div>`;
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
      const op = String(node.operator || "").toUpperCase();
      return `<select data-ct-op data-path="${key}" ${readonly ? "disabled" : ""}>${!op ? '<option value="" selected disabled>请选择组合关系</option>' : ""}${ctree.GROUP_OPERATORS.map((candidate) => {
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
      if (node.type === "group" || node.type === "count_group") {
        const multi = ctree.fieldSelectionInfo(node);
        const multiHtml = multi ? `<div class="ct-detail-hint">${esc(multi.field.label)}已选 ${multi.values.length} 项 · ${node.operator === "AND" ? "全部满足" : "任一满足"}</div><button type="button" class="btn sm" data-ct-multi-open="${esc(multi.field.key)}" data-path="${ctree.pathKey(path)}">修改多选项目</button>` : "";
        return `<div class="ct-detail-title">条件组${path.length ? ` #${path.join(".")}` : "（根）"}</div>${multiHtml}
          <div class="ct-detail-row"><em>组合方式</em><select data-ct-group-kind data-path="${ctree.pathKey(path)}"><option value="group" ${node.type === "group" ? "selected" : ""}>且／或／非</option><option value="count_group" ${node.type === "count_group" ? "selected" : ""}>至少满足指定数量</option></select>${node.type === "count_group" ? `<label>至少满足 <input type="number" min="1" max="${node.children.length}" data-ct-field="min_count" data-widget="number" data-path="${ctree.pathKey(path)}" value="${node.min_count || 1}"> 项</label>` : opSelectHtml(node, path, readonly)}</div>
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
        `<div class="ct-detail-row"><em>${esc(field.dict === "observation" ? (typeName[node.observation_type] || "观察项目") : field.label)}</em>${fieldWidgetHtml(node, field, path, false)}</div>`).join("");
      return `<div class="ct-detail-title">条件详情</div>
        <div class="ct-detail-row"><em>条件类型</em>${kindSelectHtml(node, path, false)}</div>
        ${fields}
        <div class="ct-detail-tools">${leafToolsHtml(node, path, false)}</div>`;
    };

    const renderMultiPicker = () => {
      const panel = $("#ctDetail");
      panel.querySelector(".ct-multi-picker")?.remove();
      if (!multiSelection) { panel.classList.remove("ct-multi-open"); return; }
      const selected = multiSelection.selected;
      const items = mergeConditionEntries(multiSelection.items, [...selected].filter((code) => !multiSelection.items.some((item) => item.code === code)).map((code) => ({ code, name: `${code}（当前项目）` })));
      panel.classList.add("ct-multi-open");
      panel.scrollTop = 0;
      panel.insertAdjacentHTML("beforeend", `<div class="ct-multi-picker">
        <div class="ct-multi-heading"><div><span class="ct-multi-eyebrow">项目选择</span><h4>多选${esc(multiSelection.field.label)}</h4></div><button type="button" class="ct-multi-back" data-ct-multi-cancel aria-label="返回条件详情">返回</button></div>
        <div class="ct-multi-search"><span aria-hidden="true">⌕</span><input type="search" data-ct-multi-search placeholder="搜索名称或编码" aria-label="搜索多选项目"></div>
        <div class="ct-multi-list-head"><span>可选项目 · ${items.length}</span><span class="ct-multi-count" data-ct-multi-count>已选 ${selected.size} 项</span></div>
        <div class="ct-multi-options">${items.map((item) => `<label class="ct-multi-option" data-ct-multi-option data-search="${esc(`${item.name || item.name_cn || ""} ${item.code} ${(item.aliases || []).join(" ")}`.toLowerCase())}"><input type="checkbox" data-ct-multi-choice value="${esc(item.code)}" ${selected.has(item.code) ? "checked" : ""}><span class="ct-multi-term"><span class="ct-multi-name">${esc(item.name || item.name_cn || item.code)}</span><span class="ct-multi-code">${esc(item.code)}</span></span></label>`).join("")}
          <div class="ct-multi-empty" data-ct-multi-empty ${items.length ? "hidden" : ""}>没有匹配的项目</div></div>
        <div class="ct-multi-footer"><div class="ct-multi-mode-title">这些项目之间的关系</div><div class="ct-multi-modes">
          <label class="ct-multi-mode"><input type="radio" name="ctMultiMode" data-ct-multi-operator value="AND" ${multiSelection.operator === "AND" ? "checked" : ""}><span><strong>全部满足</strong><small>且 · AND</small></span></label>
          <label class="ct-multi-mode"><input type="radio" name="ctMultiMode" data-ct-multi-operator value="OR" ${multiSelection.operator === "OR" ? "checked" : ""}><span><strong>任一满足</strong><small>或 · OR</small></span></label></div>
          <div class="ct-multi-note">多个项目须选择组合方式，其他参数沿用当前条件。</div>
          <div class="ct-multi-actions"><button type="button" class="btn" data-ct-multi-cancel>取消</button><button type="button" class="btn primary" data-ct-multi-apply ${!selected.size || (selected.size > 1 && !multiSelection.operator) ? "disabled" : ""}>确认选择</button></div>
        </div></div>`);
      panel.querySelector("[data-ct-multi-search]").focus();
    };

    const renderDetail = () => {
      const panel = $("#ctDetail");
      if (!panel) return;
      multiSelection = null;
      panel.classList.remove("ct-multi-open");
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
      const westernHost = $("#westernRecommendationEditor");
    if (westernHost && window.WesternDecision) westernEditor = window.WesternDecision.mountEditor(westernHost, base.clinical_context?.western_recommendation, base.source);
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
      // Constrain the containing field as well as the canvas: an auto-sized
      // grid track otherwise inherits the SVG world's intrinsic width.
      host.closest(".field").style.gridTemplateColumns = "minmax(0, 1fr)";
      host.innerHTML = '<div class="ctc-layout" style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,380px);width:100%;min-width:0;overflow:hidden"><div class="ctc-mount" style="min-width:0;overflow:hidden"></div><div class="ct-detail" id="ctDetail" style="min-width:0;width:100%;box-sizing:border-box;overflow:auto"></div></div>';
      if (window.innerWidth <= 1000) host.firstElementChild.style.gridTemplateColumns = "minmax(0,1fr)";
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
    const handleConditionChange = (event) => {
      const target = event.target;
      const path = ctree.parsePathKey(target.dataset.path);
      if (target.matches("[data-ct-multi-choice]")) {
        if (!multiSelection) return;
        if (target.checked) multiSelection.selected.add(target.value); else multiSelection.selected.delete(target.value);
        $("#ctDetail [data-ct-multi-count]").textContent = `已选 ${multiSelection.selected.size} 项`;
        $("#ctDetail [data-ct-multi-apply]").disabled = !multiSelection.selected.size || (multiSelection.selected.size > 1 && !multiSelection.operator);
        return;
      }
      if (target.matches("[data-ct-multi-operator]")) {
        if (multiSelection) {
          multiSelection.operator = target.value;
          $("#ctDetail [data-ct-multi-apply]").disabled = !multiSelection.selected.size;
        }
        return;
      }
      if (target.matches("[data-ct-group-kind]")) {
        mutate(() => ctree.updateNode(editorTree, path, {type: target.value, min_count: target.value === "count_group" ? 1 : null, operator: target.value === "group" ? "AND" : null, label: null}));
      } else if (target.matches("[data-ct-op]")) {
        mutate(() => ctree.updateNode(editorTree, path, { operator: target.value, label: null }));
      } else if (target.matches("[data-ct-kind]")) {
        const kind = target.value;
        if (ctree.getNode(editorTree, path)?.type === kind) { renderDetail(); return; }
        mutate(() => ctree.replaceNode(editorTree, path, ctree.newLeaf(kind)));
      } else if (target.matches("[data-ct-field]")) {
        const widget = target.dataset.widget;
        let value = target.value;
        if (widget === "number") value = target.value === "" ? null : Number(target.value);
        else if (widget === "bool") value = target.checked;
        const patch = { [target.dataset.ctField]: value, label: null };
        if (target.dataset.ctField === "observation_type") patch.code = null;
        if (target.dataset.ctField === "exam_code") patch.finding_code = null;
        mutate(() => ctree.updateNode(editorTree, path, patch));
      }
    };
    host.onchange = handleConditionChange;
    // Native select controls can emit input before change; update dependent fields immediately.
    host.oninput = (event) => {
      if (event.target.matches("[data-ct-multi-search]")) {
        const query = event.target.value.trim().toLowerCase();
        let visibleCount = 0;
        host.querySelectorAll("[data-ct-multi-option]").forEach((option) => { option.hidden = !option.dataset.search.includes(query); if (!option.hidden) visibleCount += 1; });
        host.querySelector("[data-ct-multi-empty]").hidden = visibleCount > 0;
        return;
      }
      if (event.target.matches('[data-ct-kind], [data-ct-field="observation_type"], [data-ct-field="exam_code"]')) handleConditionChange(event);
    };
    host.onclick = (event) => {
      const multiButton = event.target.closest("[data-ct-multi-open], [data-ct-multi-apply], [data-ct-multi-cancel]");
      if (multiButton) {
        if (multiButton.hasAttribute("data-ct-multi-cancel")) { multiSelection = null; renderMultiPicker(); return; }
        if (multiButton.hasAttribute("data-ct-multi-apply")) {
          if (!multiSelection) return;
          const selection = multiSelection;
          mutate(() => ctree.replaceNode(editorTree, selection.path,
            ctree.combineFieldSelection(selection.node, selection.field.key, [...selection.selected], selection.operator)));
          return;
        }
        const path = ctree.parsePathKey(multiButton.dataset.path);
        const current = ctree.getNode(editorTree, path);
        const existing = ctree.fieldSelectionInfo(current);
        const node = existing?.node || current;
        const field = (ctree.LEAF_FIELDS[node.type] || []).find((item) => item.key === multiButton.dataset.ctMultiOpen);
        if (!field) return;
        const dict = field.dict === "observation" ? node.observation_type : field.dict;
        const items = ((editorDict && editorDict[dict]) || []).filter((item) => field.key !== "finding_code" || !node.exam_code || !item.exam_code || item.exam_code === node.exam_code);
        multiSelection = { node, field, path, items, selected: new Set(existing?.values || (node[field.key] ? [node[field.key]] : [])), operator: existing?.operator || "" };
        renderMultiPicker();
        return;
      }
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
    loadEditorDict(base.graph_filename || (options.clinicalBasis ? state.reviewFilters.graph : state.graphFilename)).then((dict) => {
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
          clinical_intent: form.querySelector("[name=clinical_intent]").value,
          anchor_type: form.querySelector("[name=anchor_type]").value,
          name: form.querySelector("[name=name]").value,
          disease_code: form.querySelector("[name=disease_code]").value,
          disease_name: form.querySelector("[name=disease_name]").value,
          status: form.querySelector("[name=status]").value,
          priority: Number(form.querySelector("[name=priority]").value),
          department: form.querySelector("[name=department]").value,
          conditions,
          clinical_context: {
            ...(base.clinical_context || {}),
            ...Object.fromEntries(["population", "subtype", "phase", "recommendation_strength", "evidence_grade"].map((key) => [key, form.querySelector(`[name="context_${key}"]`).value.trim()])),
            exceptions: form.querySelector('[name="context_exceptions"]').value.split("\n").map((item) => item.trim()).filter(Boolean),
            missing_information: form.querySelector('[name="context_missing"]').value.split("\n").map((item) => item.trim()).filter(Boolean),
          },
          exclusions: form.querySelector("[name=exclusions]").value.split(/[,，]/).map((v) => v.trim()).filter(Boolean),
          source,
          conclusion: {
            ...conclusionExtra,
            diagnosis: form.querySelector("[name=disease_code]").value,
            confidence: form.querySelector("[name=confidence]").value,
            message: form.querySelector("[name=message]").value,
            action: form.querySelector("[name=action]").value,
          },
        };
        const western = westernEditor?.read();
        if (western) {
          payload.clinical_context.western_recommendation = western;
          payload.rule_type = "treatment";
          payload.diagnosis_prerequisite = western.diagnosis_stage === "independent" ? null : western.diagnosis_stage;
          payload.conclusion.treatment_items = western.actions.map(action => ({...action, entity_type:action.kind}));
          payload.conclusion.treatment_plan_id ||= `TP-${payload.rule_id}`;
          payload.conclusion.treatment_name ||= payload.name;
        } else if (westernEditor) delete payload.clinical_context.western_recommendation;
        if (payload.clinical_context.schema_version === 3 && form.querySelector('[name="confirm_tcm_logic"]')?.checked) {
          payload.clinical_context.logic_origin = "human_defined";
          payload.clinical_context.missing_information = payload.clinical_context.missing_information.filter((item) => item !== "AI建议组合尚未经人工确认");
        }
        if (options.reviewRevision) payload.status = "draft";
        if (options.onSave) {
          payload.status = "draft";
          const savedBasis = await options.onSave(payload);
          dialog.close(); toast("诊疗依据已保存，须重新核对并审核");
          await renderClinicalBases();
          if (savedBasis?.basis_id) {
            const filename = savedBasis.specification?.graph_filename || payload.graph_filename;
            const latest = await api(`/api/review/bases?graph_filename=${encodeURIComponent(filename)}`);
            const current = latest.items.find((item) => item.basis_id === savedBasis.basis_id);
            if (current) await showClinicalBasis(current, filename);
          }
          return;
        }
        if (rule) await api(`/api/rules/${rule.rule_id}`, { method: "PUT", json: payload });
        else await api("/api/rules", { json: payload });
        dialog.close(); toast("规则已保存"); markStale("dashboard", "review", "graph"); reload();
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

  function collectStandardForm() {
    const value = (id) => $(id)?.value ?? "";
    const number = (id) => value(id) === "" ? null : Number(value(id));
    const type = value("#stdType") || "lab";
    return { hospital_id: state.hospitalId, item_type: type,
      lab_code: type === "lab" ? value("#stdLabCode").trim() : "", lab_name: type === "lab" ? value("#stdLabName").trim() : "",
      exam_code: type === "exam" ? value("#stdLabCode").trim() : "", exam_name: type === "exam" ? value("#stdLabName").trim() : "",
      finding_code: type === "exam" ? value("#stdFindingCode").trim() : "", finding_name: type === "exam" ? value("#stdFindingName").trim() : "",
      concept_code: value("#stdConcept"), interpretation_type: value("#stdMode") || "auto",
      operator: value("#stdOperator") || ">", threshold_value: number("#stdThreshold"), threshold_upper: number("#stdUpper"),
      threshold_unit: value("#stdUnit").trim(), uln_multiplier: number("#stdUln"),
      accepted_values: value("#stdAccepted").split(/[,，;；\n]+/).map((item) => item.trim()).filter(Boolean),
      method: value("#stdMethod").trim(), ref_source: value("#stdRefSource") || "STATIC",
      context_gender: value("#stdGender") || null, context_age_min: number("#stdAgeMin"), context_age_max: number("#stdAgeMax"),
      effective_from: value("#stdFrom"), effective_to: value("#stdTo") || null, source_note: value("#stdNote").trim(), status: "draft",
    };
  }

  async function renderStandards() {
    await loadBase();
    const hospital = state.hospitalId || state.hospitals[0]?.hospital_id || "";
    state.hospitalId = hospital;
    const [rows, coverage] = hospital ? await Promise.all([api(`/api/standards/${encodeURIComponent(hospital)}`), api(`/api/standards/${encodeURIComponent(hospital)}/coverage`)]) : [[], { items: [] }];
    const hcmTemplates = await api("/api/standards/templates/hcm");
    state.standards = rows;
    const editing = state.standardEditIndex >= 0 ? rows[state.standardEditIndex] : null;
    const form = state.pendingStandard || editing || { item_type: "lab", interpretation_type: "auto", concept_code: "ELEVATED" };
    const exam = form.item_type === "exam";
    const mode = form.interpretation_type || (exam ? "presence" : "auto");
    const concept = state.vocab.find((item) => item.code === form.concept_code);
    const numeric = mode === "numeric" || (mode === "auto" && ["static_threshold", "uln_multiplier"].includes(concept?.evaluator_type));
    const concepts = state.vocab.filter((item) => item.is_active !== false && (exam
      ? ["finding_exists", "static_threshold", "qualitative"].includes(item.evaluator_type) || item.category === "severity"
      : item.category === "lab_concept"));
    if (form.concept_code && !concepts.some((item) => item.code === form.concept_code)) {
      concepts.unshift({ code: form.concept_code, name_cn: "当前概念（请核对字典）" });
    }
    const field = (key, fallback = "") => esc(form[key] ?? fallback);
    const statusLabels = { missing: "缺失", inactive: "待启用", incomplete: "参数不完整 / 已失效", limited: "适用范围有限", available: "存在可用配置", structured: "按输入形式判定", guideline: "指南明确条件" };
    const readiness = coverage.items || [];
    const examItems = new Map(Object.values(state.terms.exam).map((item) => [item.code, item]));
    const findingItems = new Map(Object.values(state.terms.exam_finding).map((item) => [item.code, item]));
    readiness.filter((item) => item.item_type === "exam").forEach((item) => {
      if (!examItems.has(item.exam_code)) examItems.set(item.exam_code, { code: item.exam_code, name: item.exam_code });
      if (!findingItems.has(item.finding_code)) findingItems.set(item.finding_code, { code: item.finding_code, name: item.finding_code });
    });
    const standardDescription = (row) => row.interpretation_type === "categorical" ? `满足值：${(row.accepted_values || []).join("、")}`
      : row.interpretation_type === "presence" ? "使用明确的存在 / 不存在结果"
      : row.uln_multiplier ? `参考上限 × ${row.uln_multiplier}` : row.threshold_value != null ? `${row.operator} ${row.threshold_value}${row.threshold_upper != null ? ` ～ ${row.threshold_upper}` : ""} ${row.threshold_unit || ""}` : "按概念执行器判定";
    pageEl.innerHTML = `
      ${panel("医院与判定范围", `<div class="inline-controls"><div class="field"><label>当前医院</label><select id="hospitalSelect">${state.hospitals.map((item) => `<option value="${esc(item.hospital_id)}" ${item.hospital_id === hospital ? "selected" : ""}>${esc(item.hospital_id)}</option>`).join("")}${hospital && !state.hospitals.some((item) => item.hospital_id === hospital) ? `<option selected>${esc(hospital)}</option>` : ""}</select></div><div class="field"><label>新医院 ID</label><input id="newHospital"></div><button id="loadNewHospital" class="btn">切换 / 创建</button></div><p>上方检查规则和待审依据需要哪些判定；下方维护本院如何判定。指南明确的条件优先保留在规则中，不用医院参考范围替换。</p>`)}
      ${pagedPanel({ key: "standard-requirements", title: "规则所需判定标准", headers: ["对象", "项目 / 发现", "判定概念", "配置状态", "关联规则 / 依据", "操作"],
        description: "包含直接条件、图谱证据和必要子条件；区分已启用规则、草稿及待审诊疗依据。存在配置不代表全部患者均适用。",
        items: readiness.map((item, index) => ({ text: `${item.lab_code || item.exam_code} ${item.finding_code || ""} ${conceptName(item.concept_code)} ${item.references.map((ref) => ref.name).join(" ")}`, values: { type: item.item_type },
          html: `<tr><td>${item.item_type === "exam" ? "检查" : "检验"}</td><td class="mono">${esc(item.lab_code || item.exam_code)}${item.finding_code ? `<div>${esc(item.finding_code)}</div>` : ""}</td><td>${esc(conceptName(item.concept_code))}</td><td><strong>${esc(statusLabels[item.status])}</strong><div class="muted">${esc(item.message)}</div></td><td>${item.references.map((ref) => `<div>${esc(ref.category)} · ${esc(ref.name)}</div>`).join("")}</td><td><button class="btn sm" data-standard-requirement="${index}">${item.standard_indices.length ? "查看配置" : "配置标准"}</button></td></tr>` })),
        filters: [{ key: "type", label: "对象", options: [{ value: "lab", label: "检验" }, { value: "exam", label: "检查" }] }], emptyText: "尚无规则或待审依据引用检查、检验条件" })}
      ${panel("HCM检查数值标准", `<p>成人标准：梗阻判断用30 mmHg；50 mmHg用于结合症状评估进一步治疗。壁厚阈值需结合病因、家族及遗传证据。载入后保存为草稿，不自动启用或修改诊断规则。</p><div class="inline-controls"><select id="stdHcmTemplate">${hcmTemplates.map((item, index) => `<option value="${index}">${esc(item.exam_name)} · ${esc(item.finding_name)} · ${esc(item.operator)} ${item.threshold_value} ${esc(item.threshold_unit)}</option>`).join("")}</select><button id="loadHcmStandard" class="btn">载入数值标准</button></div>`)}
      <div class="actions"><button id="newExamStandard" class="btn primary">新增检查标准</button><button id="newLabStandard" class="btn">新增检验标准</button><button id="reviewStandards" class="btn">去审核台统一审核</button></div>
      ${panel(editing ? "编辑本院判定标准（保存后重新启用）" : "新增本院判定标准", `<div class="form-grid" id="standardForm">
        <div class="field"><label>对象类型</label><select id="stdType"><option value="lab" ${exam ? "" : "selected"}>检验</option><option value="exam" ${exam ? "selected" : ""}>检查</option></select></div>
        ${exam ? `<div class="field"><label>选择检查项目</label><select id="stdExamPicker"><option value="">请选择检查项目…</option>${[...examItems.values()].map((item) => `<option value="${esc(item.code)}" ${form.exam_code === item.code ? "selected" : ""}>${esc(item.name || item.code)} (${esc(item.code)})</option>`).join("")}</select></div><div class="field"><label>选择检查发现 / 测量指标</label><select id="stdFindingPicker"><option value="">请选择指标…</option>${[...findingItems.values()].map((item) => `<option value="${esc(item.code)}" ${form.finding_code === item.code ? "selected" : ""}>${esc(item.name || item.code)} (${esc(item.code)})</option>`).join("")}</select><small>来自实体词典及规则引用；需确认该指标属于所选检查。未收录的指标可在下方手工填写编码和名称。</small></div>` : ""}
        <div class="field"><label>${exam ? "检查" : "检验"}项目编码</label><input id="stdLabCode" value="${field(exam ? "exam_code" : "lab_code")}"></div><div class="field"><label>项目名称</label><input id="stdLabName" value="${field(exam ? "exam_name" : "lab_name")}"></div>
        ${exam ? `<div class="field"><label>检查发现编码</label><input id="stdFindingCode" value="${field("finding_code")}"></div><div class="field"><label>检查发现名称</label><input id="stdFindingName" value="${field("finding_name")}"></div>` : ""}
        <div class="field"><label>判定概念</label><select id="stdConcept">${concepts.map((item) => `<option value="${esc(item.code)}" ${form.concept_code === item.code ? "selected" : ""}>${esc(item.name_cn)} / ${esc(item.code)}</option>`).join("")}</select></div>
        <div class="field"><label>判定方式</label><select id="stdMode">${(exam ? [["numeric", "数值比较"], ["categorical", "分级 / 定性结果"], ["presence", "明确存在 / 不存在"]] : [["auto", "按检验概念执行器"]]).map(([key, label]) => `<option value="${key}" ${mode === key ? "selected" : ""}>${label}</option>`).join("")}</select></div>
        ${numeric ? `<div class="field"><label>比较方式</label><select id="stdOperator">${[">", ">=", "<", "<=", "=", "BETWEEN", "OUTSIDE"].map((op) => `<option value="${op}" ${(form.operator || ">") === op ? "selected" : ""}>${({ BETWEEN: "在闭区间内", OUTSIDE: "在区间外" })[op] || esc(op)}</option>`).join("")}</select></div><div class="field"><label>阈值 / 下界</label><input id="stdThreshold" type="number" step="any" value="${field("threshold_value")}"></div><div class="field"><label>上界（范围比较时）</label><input id="stdUpper" type="number" step="any" value="${field("threshold_upper")}"></div><div class="field"><label>单位（无量纲填写 1）</label><input id="stdUnit" value="${field("threshold_unit")}"></div>` : ""}
        ${!exam && concept?.evaluator_type === "uln_multiplier" ? `<div class="field"><label>正常上限倍数</label><input id="stdUln" type="number" step="any" value="${field("uln_multiplier")}"></div>` : ""}
        ${mode === "categorical" ? `<div class="field wide"><label>满足该概念的结果编码 / 等级（逗号分隔，精确匹配）</label><input id="stdAccepted" value="${esc((form.accepted_values || []).join(","))}"></div>` : ""}
        <div class="field"><label>适用方法（留空不限）</label><input id="stdMethod" value="${field("method")}" ${exam ? "" : "disabled"}></div><div class="field"><label>参考来源</label><select id="stdRefSource"><option value="STATIC" ${form.ref_source !== "LIS_REALTIME" ? "selected" : ""}>固定配置</option><option value="LIS_REALTIME" ${form.ref_source === "LIS_REALTIME" ? "selected" : ""}>患者检验参考范围</option></select></div>
        <div class="field"><label>适用性别</label><select id="stdGender">${[["", "不限"], ["M", "男"], ["F", "女"]].map(([key, label]) => `<option value="${key}" ${(form.context_gender || "") === key ? "selected" : ""}>${label}</option>`).join("")}</select></div>
        <div class="field"><label>年龄下限</label><input id="stdAgeMin" type="number" value="${field("context_age_min")}"></div><div class="field"><label>年龄上限</label><input id="stdAgeMax" type="number" value="${field("context_age_max")}"></div><div class="field"><label>生效日期</label><input id="stdFrom" type="date" value="${field("effective_from")}"></div><div class="field"><label>失效日期</label><input id="stdTo" type="date" value="${field("effective_to")}"></div>
        <div class="field wide"><label>来源与判定依据</label><input id="stdNote" value="${field("source_note")}" placeholder="维护来源、适用范围或引用依据"></div></div>
        <p class="muted">保存为草稿，启用前校验参数。分级须提供结构化结果；原始报告文字不会自动判为阳性。单位不一致时不自动换算。</p><div class="actions"><button id="stdSave" class="btn primary">保存草稿</button><button id="stdCancel" class="btn">取消编辑</button></div>`)}
      ${panel(`${hospital || "未选择医院"} · 本院判定标准配置`, table(["对象", "项目 / 发现", "概念", "判定方式与参数", "适用范围", "版本 / 状态", "操作"], rows.map((row, index) => `<tr><td>${row.item_type === "exam" ? "检查" : "检验"}</td><td>${esc(row.lab_name || row.exam_name || row.lab_code || row.exam_code)}${row.finding_code ? `<div>${esc(row.finding_name || row.finding_code)}</div>` : ""}</td><td>${esc(conceptName(row.concept_code))}</td><td>${esc(standardDescription(row))}</td><td>${esc(row.context_gender || "不限性别")} · 年龄 ${esc(row.context_age_min ?? "不限")}～${esc(row.context_age_max ?? "不限")}<div>${esc(row.method || "不限方法")}</div></td><td>v${row.version || 1} ${badge(row.status)}</td><td><div class="actions"><button class="btn sm" data-std-edit="${index}">编辑</button><button class="btn sm" data-std-active="${index}">校验并启用</button><button class="btn sm danger" data-std-delete="${index}">删除</button></div></td></tr>`)))}
    `;
    bindPagedTable("standard-requirements");
    const reset = () => { state.pendingStandard = null; state.standardEditIndex = -1; };
    const openNewStandard = async (type) => {
      reset();
      state.pendingStandard = { item_type: type, interpretation_type: type === "exam" ? "presence" : "auto", concept_code: type === "exam" ? "PRESENT" : "ELEVATED" };
      await renderStandards();
      $("#standardForm")?.closest(".panel")?.scrollIntoView({ block: "start" });
    };
    $("#loadHcmStandard").onclick = async () => {
      reset();
      state.pendingStandard = { ...hcmTemplates[Number($("#stdHcmTemplate").value)], hospital_id: hospital };
      await renderStandards();
      $("#standardForm")?.closest(".panel")?.scrollIntoView({ block: "start" });
    };
    $("#newExamStandard").onclick = () => openNewStandard("exam").catch((error) => toast(error.message, true));
    $("#newLabStandard").onclick = () => openNewStandard("lab").catch((error) => toast(error.message, true));
    $("#reviewStandards").onclick = () => { state.reviewTab = "standards"; navigate("review"); };
    if (exam) {
      $("#stdExamPicker").onchange = (event) => {
        const item = examItems.get(event.target.value);
        if (item) { $("#stdLabCode").value = item.code; $("#stdLabName").value = item.name || item.code; }
      };
      $("#stdFindingPicker").onchange = (event) => {
        const item = findingItems.get(event.target.value);
        if (item) { $("#stdFindingCode").value = item.code; $("#stdFindingName").value = item.name || item.code; }
      };
    }
    $("#hospitalSelect").onchange = (event) => { state.hospitalId = event.target.value; reset(); renderStandards(); };
    $("#loadNewHospital").onclick = () => { const id = $("#newHospital").value.trim().toUpperCase(); if (!id) return; state.hospitalId = id; reset(); renderStandards(); };
    $("#stdType").onchange = (event) => { const type = event.target.value; state.pendingStandard = { item_type: type, interpretation_type: type === "exam" ? "presence" : "auto", concept_code: type === "exam" ? "PRESENT" : "ELEVATED" }; state.standardEditIndex = -1; renderStandards(); };
    $("#stdMode").onchange = () => { state.pendingStandard = collectStandardForm(); renderStandards(); };
    $("#stdConcept").onchange = () => {
      const next = collectStandardForm();
      const selected = state.vocab.find((item) => item.code === next.concept_code);
      if (next.item_type === "exam") {
        if (selected?.evaluator_type === "static_threshold") next.interpretation_type = "numeric";
        else if (selected?.evaluator_type === "finding_exists") next.interpretation_type = "presence";
      }
      state.pendingStandard = next;
      renderStandards();
    };
    $("#stdSave").onclick = () => saveStandard().catch((error) => toast(error.message, true));
    $("#stdCancel").onclick = () => { reset(); renderStandards(); };
    // Delegate to the page: filtering and pagination replace requirement buttons.
    pageEl.onclick = async (event) => {
      const button = event.target.closest("[data-std-edit], [data-standard-requirement]");
      if (!button) return;
      try {
        if (button.hasAttribute("data-std-edit")) {
          state.pendingStandard = null;
          state.standardEditIndex = Number(button.dataset.stdEdit);
        } else {
          const requirement = readiness[Number(button.dataset.standardRequirement)];
          state.standardEditIndex = requirement.standard_indices[0] ?? -1;
          state.pendingStandard = state.standardEditIndex >= 0 ? null : { ...requirement,
            ...(requirement.parameter?.interpretation || {}),
            interpretation_type: requirement.parameter?.interpretation?.interpretation_type || (requirement.item_type === "exam"
              ? (state.vocab.find((item) => item.code === requirement.concept_code)?.evaluator_type === "static_threshold" ? "numeric" : "presence") : "auto") };
        }
        await renderStandards();
        const form = $("#standardForm");
        form?.closest(".panel")?.scrollIntoView({ block: "start", behavior: "instant" });
        $("#stdLabCode")?.focus({ preventScroll: true });
        toast(state.standardEditIndex >= 0 ? "已打开配置，可编辑后保存草稿" : "已打开标准配置，请补充判定参数");
      } catch (error) { toast(error.message, true); }
    };
    pageEl.querySelectorAll("[data-std-active]").forEach((button) => {
      button.textContent = "启用";
      const validate = document.createElement("button");
      validate.className = "btn sm";
      validate.textContent = "校验（含AI）";
      button.before(validate);
      validate.onclick = async () => {
        const dialog = document.createElement("dialog");
        dialog.className = "dialog";
        dialog.innerHTML = '<div class="dialog-topbar"><strong>本院判定标准校验</strong><button type="button" class="btn ghost" data-close aria-label="关闭校验窗口">× 关闭</button></div><div style="padding:18px;max-height:min(75vh,calc(100dvh - 200px));overflow:auto"><div data-validation>正在进行参数检查与AI校验…</div><button type="button" class="btn" data-close>关闭</button></div>';
        document.body.append(dialog);
        dialog.querySelectorAll("[data-close]").forEach((el) => (el.onclick = () => dialog.close()));
        dialog.addEventListener("close", () => dialog.remove(), {once: true});
        dialog.showModal();
        validate.disabled = true;
        try {
          const result = await api(`/api/standards/${hospital}/${button.dataset.stdActive}/validate`, {method: "POST"});
          const advice = result.advice || {};
          const list = (items) => (Array.isArray(items) ? items : []).map((item) => `<li>${esc(typeof item === "string" ? item : JSON.stringify(item))}</li>`).join("");
          dialog.querySelector("[data-validation]").innerHTML = `<h4>参数检查：${result.passed ? "通过" : "需修正"}</h4><ul>${list(result.issues)}</ul><h4>AI校验建议</h4><p>${esc(result.ai_error || advice.summary || "暂无建议")}</p><ul>${list(advice.findings)}</ul><h4>修改建议</h4><ul>${list(advice.suggestions)}</ul><p class="muted">共用解析模板中的模型配置。校验不会启用标准；请核对建议，通过“编辑”修正并保存后重新校验，确认后单独点击“启用”。</p>`;
        } catch (error) { dialog.querySelector("[data-validation]").textContent = error.message; }
        finally { validate.disabled = false; }
      };
      button.onclick = async () => { try { await api(`/api/standards/${hospital}/${button.dataset.stdActive}/activate`, { method: "POST" }); toast("标准已启用"); markStale("dashboard"); renderStandards(); } catch (error) { toast(error.message, true); } };
    });
    pageEl.querySelectorAll("[data-std-delete]").forEach((button) => button.onclick = async () => { if (!confirm("确认删除此标准？")) return; try { await api(`/api/standards/${hospital}/${button.dataset.stdDelete}`, { method: "DELETE" }); reset(); markStale("dashboard"); renderStandards(); } catch (error) { toast(error.message, true); } });
  }

  async function saveStandard() {
    if (!state.hospitalId) throw new Error("请先选择医院");
    const payload = collectStandardForm();
    if (!(payload.item_type === "exam" ? payload.exam_code && payload.finding_code : payload.lab_code)) throw new Error("请填写项目编码，检查还须填写发现编码");
    if (!payload.concept_code) throw new Error("请选择判定概念");
    const editing = state.standards[state.standardEditIndex];
    payload.version = editing ? (editing.version || 1) + 1 : 1;
    if (editing) await api(`/api/standards/${state.hospitalId}/${state.standardEditIndex}`, { method: "PUT", json: payload });
    else await api(`/api/standards/${state.hospitalId}`, { json: payload });
    state.pendingStandard = null; state.standardEditIndex = -1;
    toast("已保存草稿，请先校验，确认后单独启用"); await renderStandards();
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
    pageEl.innerHTML = `
      ${panel("模拟病历测试", '<div class="readonly-value">结构化病历会转换为 PatientSnapshot；医院阈值只在执行层解析。</div>', `
        <span class="muted" id="caseSavedAtText">${state.caseSavedAt ? `保存于 ${esc(state.caseSavedAt)}` : "未保存"}</span>
        <button id="saveCase" class="btn">保存病历</button>
        <label><input type="checkbox" id="inferenceDraftMode"> 测试候选规则（包含草稿，仅供核对）</label>
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
      ${panel("西医治疗决策资料", `<div class="form-grid"><div class="field"><label>体重（kg）</label><input id="caseWeight" type="number" step="any" value="${esc(record.clinical_attributes?.weight_kg ?? "")}"></div><div class="field"><label>eGFR（mL/min/1.73m²）</label><input id="caseEgfr" type="number" step="any" value="${esc(record.clinical_attributes?.egfr ?? "")}"></div><label><input id="caseMedicationsReviewed" type="checkbox" ${record.clinical_attributes?.medications_reviewed ? "checked" : ""}>已核实当前用药</label><label><input id="caseAllergiesReviewed" type="checkbox" ${record.clinical_attributes?.allergies_reviewed ? "checked" : ""}>已核实过敏史</label><div class="field"><label>既往治疗与反应</label><input id="casePriorResponse" value="${esc(record.clinical_attributes?.prior_treatment_response || "")}"></div><div class="field"><label>医生当前工作诊断编码（可留空由规则推断）</label><input id="caseWorkingDiagnosis" value="${esc(record.diagnoses?.[0]?.code || "")}"></div><div class="field"><label>诊断确定程度</label><select id="caseDiagnosisCertainty"><option value="suspected">疑似</option><option value="confirmed" ${record.diagnoses?.[0]?.certainty === "confirmed" ? "selected" : ""}>已确诊</option></select></div></div><p>未核实或未采集的信息不视为阴性；医生记录和规则推断分别保留来源。</p>`)}
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
      const testMode = !!$("#inferenceDraftMode")?.checked;
      state.inferenceResult = await api("/api/inference/run", { json: { patient, test_mode: testMode } });
      if (testMode) toast("当前是候选规则测试，结果不代表已发布的诊疗建议");
      toast(`保存完成，疑似诊断推荐 ${state.inferenceResult.conclusion_count} 条`);
      renderInferenceResult();
    } catch (error) { toast(error.message, true); }
  }

  async function renderTcmInference() {
    const [symptoms, tongues, pulses, signs, constitutions, pathogenesis] = await Promise.all([
      api("/api/vocab/entities/symptom"),
      api("/api/vocab/entities/tongue"),
      api("/api/vocab/entities/pulse"),
      api("/api/vocab/entities/sign"),
      api("/api/vocab/entities/constitution"),
      api("/api/vocab/entities/pathogenesis"),
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
    pageEl.innerHTML = `
      ${panel("中医病历文本", `<div class="field"><label>病历内容</label><textarea id="tcmRecordText" rows="5" placeholder="输入症状、舌象、脉象等病历内容"></textarea></div><button id="tcmParseRecord" class="btn primary">解析并核对观察项</button><div id="tcmParseSummary" class="muted">解析后核对下方勾选项，再运行辨证。</div>`)}
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
        ${optionGroup("已记录的病机", "pathogenesis", pathogenesis, "勾选病历中已独立判断并明确记录的病机")}
      </div>
      <div id="tcmInferenceResult">${state.tcmInferenceResult ? "" : '<div class="empty">选择观察项后运行辨证</div>'}</div>
      <div id="tcmTestResult">${state.tcmTestResult ? "" : '<div class="empty">选择测试用例后运行断言比对</div>'}</div>
    `;
    $("#tcmRunBtn").onclick = runTcmInference;
    $("#tcmParseRecord").onclick = async () => {
      try {
        const parsed = await api("/api/inference/tcm/parse-record", {json: {text: $("#tcmRecordText").value}});
        const selected = new Set(parsed.observations.map((item) => `${item.observation_type}:${item.code}`));
        document.querySelectorAll("#tcmForm input[data-obs-key]").forEach((input) => { input.checked = selected.has(input.dataset.obsKey); });
        $("#tcmParseSummary").textContent = `识别到 ${parsed.observations.length} 项肯定表现、${parsed.negated.length} 项否定表现。请核对勾选项；未勾选的内容不会参与推理。`;
      } catch (error) { toast(error.message, true); }
    };
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
        <td>${item.anchor_type === "disease" && item.suspected_disease_name ? "—" : `${item.clinical_intent === "suspected_diagnosis" ? "疑似 " : ""}<strong>${esc(item.anchor_name || item.disease_name)}</strong><div class="mono muted">${esc(item.anchor_code || item.disease_code)}</div>`}</td>
        <td>${item.suspected_disease_name ? `疑似 ${esc(item.suspected_disease_name)}` : "—"}</td>
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
      ${panel("辨病辨证结果", table(["证候", "病名", "评分", "置信度", "证据链", "指南溯源"], patternRows), "", "评分 = 命中的主症/次症/舌脉条目 ÷ 规则中的全部条目")}
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
          <td>${badge(item.matched ? "high" : "low")} ${esc(conclusion.anchor_name || conclusion.disease_name || "-")}<div class="mono muted">${esc(conclusion.anchor_code || "-")}</div>${conclusion.suspected_disease_name && conclusion.anchor_type === "syndrome" ? `<div>候选病名 ${esc(conclusion.suspected_disease_name)}</div>` : ""}</td>
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
    box.dataset.testMode = String(!!result.test_mode);
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
    box.innerHTML = (window.WesternDecision?.resultHtml(result.recommendation_assessments) || "") + diagHtml + panel(`疑似诊断推荐：${diagnosisList.length} 条`, dxHtml) + panel(`传统治疗规则结果：${treatmentList.length} 条`, txHtml);
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
    const host = pageRegistry.get("dashboard")?.el;
    if (host) host.insertAdjacentHTML("afterbegin", panel("页面自检结果", table(["检查项", "结果", "状态"], checks.map(([name, detail, ok]) => `<tr><td>${esc(name)}</td><td>${esc(detail)}</td><td>${ok ? '<span class="badge ok">通过</span>' : '<span class="badge danger" data-smoke-failure="1">失败</span>'}</td></tr>`))));
    toast(checks.some((c) => !c[2]) ? "自检发现失败项" : "自检全部通过", checks.some((c) => !c[2]));
  }

  $("#dialogTopClose").onclick = () => $("#editorDialog").close();
  document.querySelectorAll(".nav-item").forEach((button) => button.onclick = () => navigate(button.dataset.route));
  $("#refreshBtn").onclick = () => reload();
  $("#smokeTestBtn").onclick = async () => { await navigate("dashboard", { reload: true }); runSmokeTest(); };
  content.addEventListener("click", (event) => {
    const vocabDetail = event.target.closest("[data-vocab-detail]");
    if (vocabDetail) {
      state.vocabSearch = vocabDetail.dataset.vocabDetail;
      navigate("vocab", { reload: true });
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
      const input = pageEl.querySelector(`[data-review-select="${CSS.escape(reviewItemKey(items[currentIndex]))}"]`);
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
  // hash 路由：刷新/前进后退后停留在对应页面（navigate 用 location.hash 写入会触发本事件，route 相同时跳过）
  window.addEventListener("hashchange", () => {
    const route = location.hash.replace(/^#/, "");
    if (pageRenderers[route] && route !== state.route) navigate(route);
  });
  (async function init() {
    try { await api("/api/health"); $("#apiStatus").textContent = "服务已连接"; $(".status-dot").classList.add("online"); }
    catch (_) { $("#apiStatus").textContent = "服务不可用"; $(".status-dot").classList.add("offline"); }
    const initial = location.hash.replace(/^#/, "");
    navigate(pageRenderers[initial] ? initial : "dashboard");
  })();
})();

