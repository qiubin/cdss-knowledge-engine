(function () {
  "use strict";
  window.openBasisAiReview = async function (basis, filename, ctx) {
    const {api, esc} = ctx;
    const dialog = document.createElement("dialog");
    dialog.className = "dialog ai-basis-dialog";
    dialog.setAttribute("aria-label", "大模型辅助审核建议");
    dialog.innerHTML = `<div class="dialog-topbar"><strong>大模型辅助审核</strong><button type="button" class="btn" data-ai-close>× 关闭</button></div><div class="ai-basis-body"><h3>${esc(basis.name)}</h3><p class="muted">使用当前配置的模型，结合规则、指南实际原文和图谱依赖给出建议。建议不改变审核状态。</p><div class="actions"><button type="button" class="btn primary" data-ai-run>重新分析</button><button type="button" class="btn" data-ai-edit>${basis.generated_rule_id ? "查看依据／修订" : "编辑并核对依据"}</button></div><div data-ai-content role="status">正在读取已有建议…</div></div>`;
    document.body.append(dialog);
    dialog.querySelector("[data-ai-close]").onclick = () => dialog.close();
    dialog.addEventListener("close", () => dialog.remove(), {once: true});
    dialog.querySelector("[data-ai-edit]").onclick = async () => { dialog.close(); await ctx.edit(); };
    dialog.showModal();
    const configHint = document.createElement("p");
    configHint.className = "muted";
    configHint.textContent = "模型配置与指南解析共用，请在“解析模板 → 解析服务”中统一设置。";
    dialog.querySelector(".actions").after(configHint);
    const content = dialog.querySelector("[data-ai-content]");
    const run = dialog.querySelector("[data-ai-run]");
    run.textContent = "开始分析";
    run.disabled = true;
    const endpoint = `/api/review/bases/${encodeURIComponent(basis.basis_id)}/ai-review?graph_filename=${encodeURIComponent(filename)}`;
    const list = (items) => (items || []).map((item) => `<li>${esc(item)}</li>`).join("");
    const render = (report) => {
      if (!dialog.isConnected) return;
      const advice = report.advice;
      content.innerHTML = `${report.stale ? '<div class="result-banner warning">依据或原文已变化，这份建议已过期，请重新分析。</div>' : ""}<div class="result-banner ${advice.assessment === "no_obvious_issue" ? "ok" : "warning"}"><strong>${esc({needs_revision: "建议修订", insufficient_evidence: "证据不足，需补充原文或信息", no_obvious_issue: "未发现明显问题，仍需人工确认"}[advice.assessment] || "待核对")}</strong><p>${esc(advice.summary)}</p></div><p class="muted">模型：${esc(report.model)} · ${esc(report.created_at)}</p>
        ${(report.automated_issues || []).length ? `<h4>系统检查发现的问题</h4><ul>${list(report.automated_issues)}</ul>` : ""}
        <h4>逐项问题及修改建议</h4>${advice.findings.length ? advice.findings.map((item) => `<section class="ai-finding"><strong>${esc({critical: "重点处理", warning: "需核对", info: "提示"}[item.severity])} · ${esc(item.area)}</strong><p>${esc(item.problem)}</p>${item.evidence ? `<div class="quote">原文依据／待核对：${esc(item.evidence)}</div>` : ""}<p><strong>建议：</strong>${esc(item.suggestion)}</p></section>`).join("") : '<p>模型未列出具体问题，请结合系统检查与原文核对。</p>'}
        ${advice.suggested_logic ? `<h4>建议的组合逻辑</h4><div class="quote">${esc(advice.suggested_logic)}</div>` : ""}
        ${advice.missing_information.length ? `<h4>需要补充的信息</h4><ul>${list(advice.missing_information)}</ul>` : ""}
        ${advice.reviewer_checklist.length ? `<h4>知识专员核对清单</h4><ul>${list(advice.reviewer_checklist)}</ul>` : ""}`;
    };
    const analyze = async () => {
      run.disabled = true;
      run.textContent = "正在分析…";
      content.innerHTML = '<div class="empty">正在分析原文、规则和依赖，通常需要一些时间；可以关闭窗口，稍后回来查看。</div>';
      try { const result = await api(endpoint, {json: {}}); render(result.report); }
      catch (error) { if (dialog.isConnected) content.textContent = error.message; }
      finally { run.disabled = false; run.textContent = "开始分析"; }
    };
    run.onclick = analyze;
    try {
      const result = await api(endpoint);
      if (!dialog.isConnected) return;
      if (result.report) render(result.report);
      else content.textContent = "暂无审核建议，点击“开始分析”获取大模型辅助审核建议。";
    } catch (error) { if (dialog.isConnected) content.textContent = error.message; }
    finally { if (dialog.isConnected) run.disabled = false; }
  };
})();
