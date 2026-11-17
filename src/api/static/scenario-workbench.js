(function () {
  "use strict";
  window.mountScenarioWorkbench = async function (mount, ctx) {
    const { api, esc, graphFilename, diseaseCode, openBasis, sourceRefBlock, parseSourceRef, fetchRefLines, refDocTitle } = ctx;
    const query = `?graph_filename=${encodeURIComponent(graphFilename)}`;
    const data = await api(`/api/review/scenarios${query}&disease_code=${encodeURIComponent(diseaseCode)}`);
    if (!mount.isConnected) return;
    let scene = data.scenes[0].key;
    let source = null;
    mount.innerHTML = `<h3>第 3 步：按场景核对依据</h3><p>沿用已经通过的实体和关系。选择本次判断使用的关联，再核对原文、适用范围和组合逻辑；生成后进入依据审核。</p>
      <div class="scenario-tabs">${data.scenes.map((item) => `<button type="button" class="btn" data-scene="${esc(item.key)}">${esc(item.name)} · ${item.bases.length} 份依据</button>`).join("")}</div>
      <div data-scene-existing></div>
      <details><summary>从已审关系整理新的场景依据</summary>
      <p>仅勾选当前指南段落支持的项目。检验／检查项目本身用于建议；其结果或诊断发现才可作为判断条件。</p>
      <div class="actions"><label><input type="checkbox" data-scene-filter checked> 优先显示本场景候选</label><input data-scene-search type="search" placeholder="搜索症状、指标、方案或药品"></div>
      <div class="scenario-table">${data.items.length ? `<table><thead><tr><th>选择</th><th>已审关联</th><th>本场景用途</th><th>状态／出处</th></tr></thead><tbody>${data.items.map((item, index) => `<tr data-scene-row="${index}"><td><input type="checkbox" data-choice="${index}" ${item.approved ? "" : "disabled"}></td><td>${esc(item.source)} → ${esc(ctx.relationLabels[item.relation_code] || item.relation_code)} → ${esc(item.target)}</td><td><select data-role="${index}"><option value="condition" ${item.suggested_role === "condition" ? "selected" : ""}>判断条件</option><option value="recommendation" ${item.suggested_role === "recommendation" ? "selected" : ""}>建议项目（治疗／检查／用药）</option></select></td><td>${item.approved ? "已通过，自动沿用" : "先完成实体和关系审核"}<div>${(item.source_refs || []).length} 处指南引用</div></td></tr>`).join("")}</tbody></table>` : '<div class="empty">暂无关联，请先审核图谱</div>'}</div>
      <div class="form-grid">
      <div class="field half"><label>本次场景依据名称</label><input data-scene-name placeholder="例如：某分型患者的某药选择"></div>
      <div class="field half"><label>所选条件的组合方式</label><select data-scene-op><option value="">请选择</option><option value="AND">全部满足（且）</option><option value="OR">满足任一（或）</option></select></div>
      <div class="field half" data-treatment-gate><label>治疗／用药需要的诊断状态</label><select data-scene-gate><option value="confirmed">同疾病确诊判断已命中</option><option value="suspected">疑似或确诊判断已命中（须有原文依据）</option></select></div>
      <div class="field half"><label>核对人</label><input data-scene-reviewer value="${esc(localStorage.getItem("cdssReviewer") || "")}"></div>
      <div class="field wide"><label>当前原文定位（来自勾选关联）</label><select data-scene-ref><option value="">先勾选关联，再选择本次原文</option></select><div data-scene-source></div></div>
      <div class="field wide"><label>本场景原文（自动带入，可核对修正）</label><textarea data-scene-quote rows="4"></textarea></div>
      <div class="field wide"><label>适用范围、分型及限制核对说明</label><textarea data-scene-scope rows="3" placeholder="说明适用人群、分型、阶段及禁忌；实际限制仍须写入条件"></textarea></div>
      </div><div class="actions"><button type="button" class="btn primary" data-scene-prepare>整理为待审场景依据</button><span data-scene-feedback role="status"></span></div></details>`;
    const $ = (selector) => mount.querySelector(selector);
    const filterRows = () => {
      const search = $("[data-scene-search]").value.trim().toLowerCase();
      const limited = $("[data-scene-filter]").checked;
      mount.querySelectorAll("[data-scene-row]").forEach((row) => {
        const item = data.items[Number(row.dataset.sceneRow)];
        row.hidden = (limited && !(item.suggested_scenes || []).includes(scene)) ||
          (search && !`${item.source} ${item.target} ${item.code}`.toLowerCase().includes(search));
      });
    };
    $("[data-scene-search]").oninput = filterRows;
    $("[data-scene-filter]").onchange = filterRows;
    const updateScene = () => {
      const current = data.scenes.find((item) => item.key === scene);
      mount.querySelectorAll("[data-scene]").forEach((button) => button.classList.toggle("primary", button.dataset.scene === scene));
      $("[data-treatment-gate]").hidden = !["treatment_recommendation", "treatment_selection"].includes(scene);
      $("[data-scene-existing]").innerHTML = `<h4>${esc(current.name)} · 已有依据</h4>${current.bases.length ? current.bases.map((basis) => `<div class="scenario-basis"><strong>${esc(basis.name)}</strong><span>${esc(basis.status)}${basis.generated_rule_id ? " · 已生成规则" : " · 待核对"}</span><button type="button" class="btn sm" data-open-basis="${esc(basis.basis_id)}">${basis.generated_rule_id ? "查看／修订" : "核对依据并生成规则"}</button></div>`).join("") : '<p class="muted">暂无此场景依据，可从下方已审关联整理。禁忌、剂量和监测可在现有依据编辑器中单独建立相应规则。</p>'}`;
      $("[data-scene-existing]").querySelectorAll("[data-open-basis]").forEach((button) => button.onclick = () => openBasis(button.dataset.openBasis));
      filterRows();
    };
    mount.querySelectorAll("[data-scene]").forEach((button) => button.onclick = () => {
      scene = button.dataset.scene;
      mount.querySelectorAll("[data-choice]").forEach((input) => { input.checked = false; });
      source = null; sourceToken += 1;
      $("[data-scene-ref]").innerHTML = '<option value="">请为当前场景选择关联和原文</option>';
      $("[data-scene-quote]").value = ""; $("[data-scene-source]").innerHTML = "";
      updateScene();
    });
    const choices = () => [...mount.querySelectorAll("[data-choice]:checked")].map((input) => Number(input.dataset.choice));
    mount.querySelectorAll("[data-choice]").forEach((input) => input.onchange = () => {
      sourceToken += 1;
      const refs = [...new Set(choices().flatMap((index) => data.items[index].source_refs || []))].filter(parseSourceRef);
      source = null;
      $("[data-scene-quote]").value = "";
      $("[data-scene-source]").innerHTML = "";
      $("[data-scene-ref]").innerHTML = '<option value="">请选择同一场景的原文</option>' + refs.map((ref) => `<option value="${esc(ref)}">${esc(ref)}</option>`).join("");
    });
    let sourceToken = 0;
    $("[data-scene-ref]").onchange = async (event) => {
      const token = ++sourceToken;
      const ref = event.target.value;
      source = null;
      $("[data-scene-quote]").value = "";
      const parsed = parseSourceRef(ref);
      if (!parsed) return;
      try {
        const [html, lines] = await Promise.all([sourceRefBlock(ref), fetchRefLines(parsed.docId, parsed.page)]);
        if (token !== sourceToken) return;
        source = {guideline_id: parsed.docId, guideline_title: refDocTitle(parsed.docId), chunk_id: ref,
                  page_start: parsed.page, line_start: parsed.start, line_end: parsed.end};
        $("[data-scene-source]").innerHTML = html;
        $("[data-scene-quote]").value = (lines || []).filter((line) => line.line >= parsed.start && line.line <= parsed.end).map((line) => line.text).join("\n");
      } catch (error) { $("[data-scene-feedback]").textContent = error.message; }
    };
    $("[data-scene-prepare]").onclick = async () => {
      const button = $("[data-scene-prepare]");
      button.disabled = true;
      try {
        if (!source) throw new Error("请选择原文定位");
        if (!$("[data-scene-op]").value) throw new Error("请明确条件的且／或组合方式");
        const result = await api(`/api/review/scenarios/prepare${query}`, { json: {
          disease_code: diseaseCode, scene, selections: choices().map((index) => ({relation_id: data.items[index].id, role: $(`[data-role="${index}"]`).value})),
          operator: $("[data-scene-op]").value, source: {...source, quote: $("[data-scene-quote]").value},
          scope_summary: $("[data-scene-scope]").value, reviewer: $("[data-scene-reviewer]").value,
          name: $("[data-scene-name]").value, diagnosis_prerequisite: $("[data-scene-gate]").value,
        }});
        await openBasis(result.basis.basis_id);
      } catch (error) { $("[data-scene-feedback]").textContent = error.message; }
      finally { button.disabled = false; }
    };
    updateScene();
  };
})();
