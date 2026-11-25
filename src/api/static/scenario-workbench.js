(function () {
  "use strict";
  window.mountScenarioWorkbench = async function (mount, ctx) {
    const { api, esc, graphFilename, syndromeCode, openBasis, sourceRefBlock, parseSourceRef, fetchRefLines, refDocTitle } = ctx;
    const query = `?graph_filename=${encodeURIComponent(graphFilename)}`;
    const scope = `&disease_code=${encodeURIComponent(ctx.diseaseCode || "")}&syndrome_code=${encodeURIComponent(syndromeCode || "")}`;
    const data = await api(`/api/review/scenarios${query}${scope}`);
    if (!mount.isConnected) return;
    const diseaseCode = data.disease_code ?? ctx.diseaseCode;
    const items = data.items.filter((item) => item.approved);
    if (syndromeCode && !Array.isArray(data.owners)) {
      mount.innerHTML = '<h3>第 3 步：疑似诊断</h3><p>当前服务还未加载证候场景接口。请重启 API 服务并刷新页面。</p>';
      return;
    }
    if (syndromeCode && !data.owners.length) {
      mount.innerHTML = '<h3>第 3 步：疑似诊断</h3><p>此证候尚未关联中医病名。请在对应疾病卡片建立并审核该证候的归属关系，再用当前证候的已审观察项整理疑似诊断依据。</p>';
      return;
    }
    const suspectedGroups = new Map();
    if (data.domain === "tcm") {
      items.forEach((item, index) => {
        if (!item.observation_type || !item.source_node?.id) return;
        const anchor = item.source_node;
        const group = suspectedGroups.get(anchor.id) || {name: item.source, indexes: []};
        group.indexes.push(index);
        suspectedGroups.set(anchor.id, group);
      });
    }
    const suspectedCandidates = [...suspectedGroups.values()];
    let scene = data.scenes[0].key;
    let source = null;
    mount.innerHTML = `<h3>第 3 步：按场景核对依据</h3><p>沿用已经通过的实体和关系。选择本次判断使用的关联，再核对原文、适用范围和组合逻辑；生成后进入依据审核。</p>
      ${data.domain === "tcm" ? "<p>已审的症见、体征、舌象、脉象和病机可用于疑似诊断。病机条件匹配病历中独立记录的病机。</p>" : ""}
      ${syndromeCode ? `<label>所属病名 <select data-scene-owner>${data.owners.map((owner) => `<option value="${esc(owner.code)}" ${owner.code === diseaseCode ? "selected" : ""}>${esc(owner.name)}${owner.approved ? "" : "（归属未通过）"}</option>`).join("")}</select></label>` : ""}
      <div class="scenario-tabs">${data.scenes.map((item) => `<button type="button" class="btn" data-scene="${esc(item.key)}">${esc(item.name)} · ${item.key === "suspected_diagnosis" && suspectedCandidates.length ? `${suspectedCandidates.length} 组候选 · ` : ""}${item.bases.length} 份依据</button>`).join("")}</div>
      <div data-suspected-candidates hidden>${suspectedCandidates.length ? `<h4>已审观察项形成的疑似诊断候选</h4>${suspectedCandidates.map((group, index) => `
        <div class="scenario-basis"><strong>${esc(group.name)}</strong><span>${group.indexes.length} 条已审观察</span>
        <button type="button" class="btn sm primary" data-suspected-candidate="${index}">带入观察项和原文</button>
        <div class="muted">${esc(group.indexes.map((itemIndex) => items[itemIndex].target).join("、"))}</div></div>`).join("")}` : ""}</div>
      <div data-scene-existing></div>
      <details data-scene-prepare-form ${syndromeCode ? "open" : ""}><summary>勾选已审关联并核对指南原文</summary>
      <p>勾选已审条目后，这里会带入指南原文。</p>
      <div class="actions"><label><input type="checkbox" data-scene-filter checked> 优先显示本场景候选</label><input data-scene-search type="search" placeholder="${data.domain === "tcm" ? "搜索证候、症见、舌象或脉象" : "搜索症状、指标、方案或药品"}"></div>
      <div class="scenario-table">${items.length ? `<table><thead><tr><th>勾选</th><th>已审关联</th><th>本场景用途</th><th>出处</th></tr></thead><tbody>${items.map((item, index) => `<tr data-scene-row="${index}"><td><input type="checkbox" data-choice="${index}" aria-label="勾选 ${esc(item.target)}"></td><td>${esc(item.source)} → ${esc(ctx.relationLabels[item.relation_code] || item.relation_code)} → ${esc(item.target)}</td><td><select data-role="${index}"><option value="condition" ${item.suggested_role === "condition" ? "selected" : ""}>判断条件</option><option value="recommendation" ${item.suggested_role === "recommendation" ? "selected" : ""}>建议项目（治疗／检查／用药）</option></select></td><td>${(item.source_refs || []).length} 处指南引用</td></tr>`).join("")}</tbody></table>` : '<div class="empty">暂无可用于整理依据的已审关联。请先在上方审核关系及其两端实体。</div>'}</div>
      <section class="detail-section" data-scene-provenance>
        <h4>指南原文</h4>
        <fieldset data-scene-ref hidden><legend>选择原文出处</legend></fieldset>
        <div class="muted" data-scene-ref-hint role="status">勾选上方观察项或关联后，这里会直接显示指南原文。</div>
        <div data-scene-source></div>
        <details data-scene-quote-editor hidden><summary>核对或修正带入的引文</summary><div class="field"><label for="scene-source-quote">本场景原文</label><textarea id="scene-source-quote" data-scene-quote rows="4"></textarea></div></details>
      </section>
      <div class="form-grid">
      <div class="field half"><label>本次场景依据名称</label><input data-scene-name placeholder="例如：某分型患者的某药选择"></div>
      <div class="field half"><label>所选条件的组合方式</label><select data-scene-op><option value="">请选择</option><option value="AND">全部满足（且）</option><option value="OR">满足任一（或）</option><option value="COUNT">至少满足 N 项</option><option value="CUSTOM">自定义分组</option><option value="WEIGHTED">加权评分</option></select></div>
      <div class="half" data-scene-count-field hidden><div class="field"><label>至少满足几项</label><input type="number" min="1" step="1" data-scene-count></div></div>
      <div class="wide" data-scene-weight-field hidden><div class="field"><label>加权评分阈值（%）</label><input type="number" min="0.01" max="100" step="any" data-scene-threshold><div data-scene-weights></div><p class="muted">评分为命中项有效权重之和除以全部条件权重。中医观察项有轻度或中度记录时，有效权重分别乘以 0.6 或 0.8。</p></div></div>
      <p class="field wide muted" data-scene-logic-preview role="status"></p>
      <div class="field half" data-treatment-gate><label>治疗／用药需要的诊断状态</label><select data-scene-gate><option value="confirmed">同疾病确诊判断已命中</option><option value="suspected">疑似或确诊判断已命中（须有原文依据）</option></select></div>
      <div class="field half"><label>核对人</label><input data-scene-reviewer value="${esc(localStorage.getItem("cdssReviewer") || "")}"></div>
      <div class="field wide"><label>适用范围、分型及限制核对说明</label><textarea data-scene-scope rows="3" placeholder="说明适用人群、分型、阶段及禁忌；实际限制仍须写入条件"></textarea></div>
      <details class="field wide"><summary>场景触发与复评契约（可选，填写后参与执行）</summary>
      <label>触发事件（逗号分隔）<input data-contract-events placeholder="diagnosis_updated,result_ready"></label>
      <label>必需病历属性（逗号分隔）<input data-contract-required></label>
      <label>建议动作（逗号分隔）<input data-contract-actions></label>
      <label>复评事件（逗号分隔）<input data-contract-reassess></label>
      </details></div><div class="actions"><button type="button" class="btn primary" data-scene-prepare>整理为待审场景依据</button><span data-scene-feedback role="status"></span></div></details>`;
    const $ = (selector) => mount.querySelector(selector);
    const ownerSelect = $("[data-scene-owner]");
    if (ownerSelect) ownerSelect.onchange = async () => {
      ownerSelect.disabled = true;
      try {
        await window.mountScenarioWorkbench(mount, {...ctx, diseaseCode: ownerSelect.value});
      } catch (error) {
        $("[data-scene-feedback]").textContent = error.message;
        ownerSelect.value = diseaseCode;
        ownerSelect.disabled = false;
      }
    };
    const isSceneCandidate = (item) => data.domain === "tcm" && scene === "suspected_diagnosis"
      ? Boolean(item.observation_type)
      : (item.suggested_scenes || []).includes(scene);
    const filterRows = () => {
      const search = $("[data-scene-search]").value.trim().toLowerCase();
      const fixedFilter = data.domain === "tcm" && scene === "suspected_diagnosis";
      $("[data-scene-filter]").disabled = fixedFilter;
      const limited = fixedFilter || $("[data-scene-filter]").checked;
      mount.querySelectorAll("[data-scene-row]").forEach((row) => {
        const item = items[Number(row.dataset.sceneRow)];
        row.hidden = (limited && !isSceneCandidate(item)) ||
          (search && !`${item.source} ${item.target} ${item.code}`.toLowerCase().includes(search));
      });
    };
    $("[data-scene-search]").oninput = filterRows;
    $("[data-scene-filter]").onchange = filterRows;
    const updateScene = () => {
      const current = data.scenes.find((item) => item.key === scene);
      mount.querySelectorAll("[data-scene]").forEach((button) => button.classList.toggle("primary", button.dataset.scene === scene));
      $("[data-suspected-candidates]").hidden = scene !== "suspected_diagnosis" || !suspectedCandidates.length;
      $("[data-scene-prepare-form]").hidden = Boolean(current.existing_only);
      $("[data-treatment-gate]").hidden = !["treatment_recommendation", "treatment_selection"].includes(scene);
      const available = items.filter(isSceneCandidate).length;
      const empty = current.existing_only ? "暂无此场景的依据草稿。" :
        `已整理 0 份依据。${available ? `下方有 ${available} 条已审关联可用于整理。` : "当前没有可用于本场景的已审关联。"}`;
      $("[data-scene-existing]").innerHTML = `<h4>${esc(current.name)} · 已有依据</h4>${current.bases.length ? current.bases.map((basis) => `<div class="scenario-basis"><strong>${esc(basis.name)}</strong><span>${esc({draft: "待核对", active: "已通过", rejected: "已退回"}[basis.status] || basis.status)}${basis.generated_rule_id ? " · 已生成规则" : ""}</span><button type="button" class="btn sm" data-open-basis="${esc(basis.basis_id)}">${basis.generated_rule_id ? "查看／修订" : "核对依据并生成规则"}</button></div>`).join("") : `<p class="muted">${esc(empty)}</p>`}`;
      $("[data-scene-existing]").querySelectorAll("[data-open-basis]").forEach((button) => button.onclick = () => openBasis(button.dataset.openBasis));
      filterRows();
    };
    const choices = () => [...mount.querySelectorAll("[data-choice]:checked")].map((input) => Number(input.dataset.choice));
    const operatorInput = $("[data-scene-op]");
    const minCountInput = $("[data-scene-count]");
    const thresholdInput = $("[data-scene-threshold]");
    const weightsMount = $("[data-scene-weights]");
    const weights = new Map();
    const conditionIndexes = () => choices().filter((index) => $(`[data-role="${index}"]`).value === "condition");
    const updateLogicPreview = () => {
      const operator = operatorInput.value;
      const conditions = conditionIndexes();
      const names = conditions.map((index) => items[index].target);
      const values = names.join("、");
      $("[data-scene-count-field]").hidden = operator !== "COUNT";
      $("[data-scene-weight-field]").hidden = operator !== "WEIGHTED";
      minCountInput.max = conditions.length;
      $("[data-scene-prepare]").textContent = operator === "CUSTOM" ? "保存草稿并配置条件树" : "整理为待审场景依据";
      const descriptions = {
        AND: () => values ? `以下条件全部满足：${values}` : "仅沿用所选诊断前提（适用于治疗／用药场景）",
        OR: () => values ? `以下条件至少满足 1 项：${values}` : "请勾选判断条件。",
        COUNT: () => values ? `以下条件至少满足 ${minCountInput.value || "N"} 项：${values}` : "请勾选判断条件。",
        CUSTOM: () => values ? `进入条件树配置以下 ${names.length} 项的分组：${values}` : "请勾选判断条件。",
        WEIGHTED: () => `评分达到 ${thresholdInput.value || "待填写"}% 时命中。${conditions.map((index) =>
          `${items[index].target} 权重 ${weights.get(index) || "待填写"}`).join("、")}`,
      };
      $("[data-scene-logic-preview]").textContent = descriptions[operator]?.() || "";
    };
    const updateLogicInputs = () => {
      const weighted = operatorInput.value === "WEIGHTED";
      weightsMount.innerHTML = weighted ? conditionIndexes().map((index) =>
        `<label>${esc(items[index].target)}权重 <input type="number" min="0" step="any" data-scene-weight="${index}" value="${esc(weights.get(index) || "")}"></label>`
      ).join("") : "";
      updateLogicPreview();
    };
    operatorInput.onchange = updateLogicInputs;
    minCountInput.oninput = thresholdInput.oninput = updateLogicPreview;
    weightsMount.oninput = (event) => {
      weights.set(Number(event.target.dataset.sceneWeight), event.target.value);
      updateLogicPreview();
    };
    mount.querySelectorAll("[data-role]").forEach((input) => input.onchange = updateLogicInputs);
    const refSelect = $("[data-scene-ref]");
    const refHint = $("[data-scene-ref-hint]");
    const quoteEditor = $("[data-scene-quote-editor]");
    let sourceToken = 0;
    let manualRef = "";
    const loadSource = async (ref) => {
      const token = ++sourceToken;
      source = null;
      $("[data-scene-quote]").value = "";
      quoteEditor.hidden = true;
      quoteEditor.open = false;
      $("[data-scene-source]").innerHTML = "";
      const parsed = parseSourceRef(ref);
      if (!parsed) return;
      refHint.textContent = "正在读取指南原文…";
      try {
        const lines = await fetchRefLines(parsed.docId, parsed.page);
        const html = await sourceRefBlock(ref);
        if (token !== sourceToken || !mount.isConnected) return;
        const quote = (lines || []).filter((line) => line.line >= parsed.start && line.line <= parsed.end)
          .map((line) => line.text).join("\n").trim();
        $("[data-scene-source]").innerHTML = html + `<a class="btn sm" target="_blank" rel="noopener" href="/api/pdf/${encodeURIComponent(parsed.docId)}/file#page=${parsed.page}">打开指南 PDF 第 ${parsed.page} 页</a>`;
        if (!quote) {
          refHint.textContent = lines === null
            ? `无法读取《${refDocTitle(parsed.docId)}》的解析原文。恢复该指南的解析资料后重新勾选即可重试。`
            : "该页没有读到引用行。请切换出处，或打开 PDF 核对原页。";
          return;
        }
        source = {guideline_id: parsed.docId, guideline_title: refDocTitle(parsed.docId), chunk_id: ref,
                  page_start: parsed.page, line_start: parsed.start, line_end: parsed.end};
        $("[data-scene-quote]").value = quote;
        quoteEditor.hidden = false;
        refHint.textContent = "已带入原文。请核对这段文字是否支持所选条件及其组合方式。";
      } catch (error) {
        if (token === sourceToken) refHint.textContent = `原文读取失败：${error.message}`;
      }
    };
    const updateSourceChoices = () => {
      updateLogicInputs();
      const selected = choices().map((index) => items[index]);
      $("[data-scene-feedback]").textContent = "";
      const located = selected.map((item) => [...new Set(item.source_refs || [])]
        .map((ref) => ({ref, location: parseSourceRef(ref)})).filter(({location}) => location));
      const refs = new Map();
      for (const locations of located) {
        for (const {ref, location} of locations) {
          if (!located.every((entries) => entries.some((entry) => entry.location.docId === location.docId))) continue;
          const candidate = refs.get(ref) || {ref, location, count: 0};
          candidate.count += 1;
          refs.set(ref, candidate);
        }
      }
      const ranked = [...refs.values()].sort((a, b) => b.count - a.count ||
        a.location.page - b.location.page || a.location.start - b.location.start);
      if (!refs.has(manualRef)) manualRef = "";
      const chosenRef = manualRef || ranked[0]?.ref;
      refSelect.hidden = ranked.length < 2;
      refSelect.innerHTML = ranked.length < 2 ? "" : '<legend>选择原文出处</legend>' +
        ranked.map(({ref, location, count}, index) =>
          `<div><label><input type="radio" name="scene-source-ref" value="${esc(ref)}" ${ref === chosenRef ? "checked" : ""}> ${esc(`${index === 0 ? "推荐 · " : ""}${refDocTitle(location.docId)} · 第 ${location.page} 页第 ${location.start}–${location.end} 行 · ${count}/${selected.length} 条关联引用`)}</label></div>`
        ).join("");
      if (!ranked.length) {
        loadSource("");
        const missing = selected.filter((_, index) => !located[index].length);
        refHint.textContent = !selected.length ? "勾选上方观察项或关联后，这里会直接显示指南原文。" : missing.length
          ? `这些条目缺少可定位的指南出处：${missing.map((item) => item.target).join("、")}。请在关系详情核对出处。`
          : "所选关联没有共同的指南出处，请按指南分别整理依据。";
        return;
      }
      if (source?.chunk_id !== chosenRef) loadSource(chosenRef);
    };
    refSelect.onchange = (event) => {
      manualRef = event.target.value;
      return loadSource(manualRef);
    };
    mount.querySelectorAll("[data-choice]").forEach((input) => input.onchange = updateSourceChoices);
    mount.querySelectorAll("[data-suspected-candidate]").forEach((button) => button.onclick = () => {
      const group = suspectedCandidates[Number(button.dataset.suspectedCandidate)];
      const selection = new Set(group.indexes);
      mount.querySelectorAll("[data-choice]").forEach((input) => {
        input.checked = selection.has(Number(input.dataset.choice));
      });
      $("[data-scene-search]").value = "";
      $("[data-scene-op]").value = "";
      $("[data-scene-name]").value = `${group.name} · 疑似诊断`;
      $("[data-scene-prepare-form]").open = true;
      manualRef = "";
      updateSourceChoices();
      filterRows();
      $("[data-scene-provenance]").scrollIntoView({block: "nearest"});
    });
    mount.querySelectorAll("[data-scene]").forEach((button) => button.onclick = () => {
      scene = button.dataset.scene;
      mount.querySelectorAll("[data-choice]").forEach((input) => { input.checked = false; });
      updateSourceChoices();
      updateScene();
    });
    $("[data-scene-prepare]").onclick = async () => {
      const button = $("[data-scene-prepare]");
      button.disabled = true;
      try {
        const picked = choices();
        if (!picked.length) throw new Error("请先勾选要整理的关联");
        if (!source) {
          $("[data-scene-provenance]").scrollIntoView({block: "nearest"});
          throw new Error(refHint.textContent || "还没有可用的引文，请先勾选有指南出处的关联");
        }
        const operator = operatorInput.value;
        if (!operator) throw new Error("请选择条件的组合方式");
        const conditions = conditionIndexes();
        const minCount = Number(minCountInput.value);
        const thresholdPercent = Number(thresholdInput.value);
        if (operator === "COUNT" && (!Number.isInteger(minCount) || minCount < 1 || minCount > conditions.length)) {
          throw new Error("至少满足的数量须介于 1 与所选判断条件数之间");
        }
        if (operator === "WEIGHTED" && (!(thresholdPercent > 0 && thresholdPercent <= 100) ||
            conditions.some((index) => !Number.isFinite(Number(weights.get(index))) || Number(weights.get(index)) <= 0))) {
          throw new Error("请填写大于 0 且不超过 100% 的阈值，并为每个判断条件填写正数权重");
        }
        const result = await api(`/api/review/scenarios/prepare${query}`, { json: {
          disease_code: diseaseCode, scene, selections: picked.map((index) => {
            const role = $(`[data-role="${index}"]`).value;
            return {relation_id: items[index].id, role,
              ...(operator === "WEIGHTED" && role === "condition" ? {weight: Number(weights.get(index))} : {})};
          }),
          operator, ...(operator === "COUNT" ? {min_count: minCount} : {}),
          ...(operator === "WEIGHTED" ? {threshold: thresholdPercent / 100} : {}),
          ...($("[data-contract-events]").value.trim() ? {scenario_contract: {
            trigger_events: $("[data-contract-events]").value.split(",").map(x => x.trim()).filter(Boolean),
            required_attributes: $("[data-contract-required]").value.split(",").map(x => x.trim()).filter(Boolean),
            actions: $("[data-contract-actions]").value.split(",").map(x => x.trim()).filter(Boolean),
            reassessment_events: $("[data-contract-reassess]").value.split(",").map(x => x.trim()).filter(Boolean),
          }} : {}),
          source: {...source, quote: $("[data-scene-quote]").value},
          scope_summary: $("[data-scene-scope]").value, reviewer: $("[data-scene-reviewer]").value,
          name: $("[data-scene-name]").value, diagnosis_prerequisite: $("[data-scene-gate]").value,
        }});
        await openBasis(result.basis.basis_id, {configureLogic: operator === "CUSTOM"});
      } catch (error) { $("[data-scene-feedback]").textContent = error.message; }
      finally { button.disabled = false; }
    };
    updateScene();
  };
})();
