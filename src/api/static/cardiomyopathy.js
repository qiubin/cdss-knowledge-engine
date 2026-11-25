'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const panels = [...document.querySelectorAll('section')];
  const tabs = document.querySelector('.workflow-tabs');
  panels.forEach((panel, index) => {
    const button = document.createElement('button');
    button.textContent = panel.querySelector('h2').textContent;
    button.setAttribute('aria-selected', String(index === 0));
    panel.hidden = index !== 0;
    button.onclick = () => {
      panels.forEach((p, i) => { p.hidden = i !== index; });
      [...tabs.children].forEach((b, i) => b.setAttribute('aria-selected', String(i === index)));
    };
    tabs.appendChild(button);
  });
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const value = id => $(id).value.trim();
  const context = () => {
    if (!value('hospital') || !value('patient')) throw new Error('请填写医院和患者');
    return {hospital_id: value('hospital'), patient_id: value('patient')};
  };
  const patient = () => ({...JSON.parse(value('snapshot')), ...context(), age: value('age') ? Number(value('age')) : null});
  const query = () => new URLSearchParams(context()).toString();
  async function api(path, body, method = 'POST') {
    if (body && !value('actor')) throw new Error('请填写操作人');
    const suffix = body ? `${path.includes('?') ? '&' : '?'}actor=${encodeURIComponent(value('actor'))}` : '';
    const response = await fetch(`/api/cardiomyopathy/${path}${suffix}`, body ? {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
    return result;
  }
  const show = (id, result) => { $(id).textContent = JSON.stringify(result, null, 2); };
  let followups = [];
  async function listFollow() {
    followups = await api(`follow-ups?${query()}`);
    $('follow-ups').innerHTML = followups.map((r,i) => `<p>${esc(r.title)} · ${esc(r.due_at)} · ${r.status === 'completed' ? '已完成' : r.overdue ? '已到期' : esc(r.status)} ${r.status === 'pending' ? `<button data-complete="${i}">记录完成</button>` : ''}</p>`).join('') || '暂无复查任务';
  }
  const actions = {
    async coverage() {
      const q = new URLSearchParams({graph_filename:value('graph'),disease_code:value('disease')});
      const response = await fetch(`/api/review/scenarios/coverage?${q}`);
      const result = await response.json();
      if (!response.ok) throw new Error(JSON.stringify(result.detail));
      $('coverage').innerHTML = `<table><tr><th>场景</th><th>有原文</th><th>结构化</th><th>已审核</th><th>可执行</th><th>已验证</th></tr>${result.scenes.map(r => `<tr><td>${esc(r.name)}</td>${['has_source','structured','approved','executable','validated'].map(k=>`<td>${r[k]}</td>`).join('')}</tr>`).join('')}</table><p>${esc(result.validation_note)}</p>`;
    },
    async saveSnapshot() { show('history', await api('patient-snapshots', {...patient(), encounter_time:new Date().toISOString()}, 'PUT')); },
    async history() { show('history', await api(`patient-snapshots?${query()}`)); },
    async recommend() {
      const result = await api('recommendations', {patient:patient()});
      show('history', {diagnostics:result.diagnostics,safety_alerts:result.safety_alerts});
      await actions.listRecommendations();
    },
    async listRecommendations() {
      const rows = await api(`recommendations?${query()}`);
      $('recommendations').innerHTML = rows.map(r => `<article><h3>${esc(r.rule_id)} · ${esc(r.status)}</h3><ul>${r.items.map(i=>`<li>${esc(i.name || i.code)} · ${esc(i.local_code || i.blocked_reason)}</li>`).join('')}</ul><details><summary>指南证据</summary><pre>${esc(JSON.stringify(r.evidence,null,2))}</pre></details>${r.status === 'pending' ? `<button data-feedback="${esc(r.recommendation_id)}" data-state="accepted">采纳为本地草稿</button><button data-feedback="${esc(r.recommendation_id)}" data-state="rejected">拒绝</button>` : ''}</article>`).join('') || '暂无建议；可先核对规则审核状态与病历数据。';
    },
    async loadModels() {
      const rows = await api('risk-models');
      $('risk-model').innerHTML = rows.filter(r=>r.status==='active').map(r=>`<option value="${esc(r.model_id)}" data-version="${r.version}">${esc(r.name)} · v${r.version}</option>`).join('') || '<option value="">暂无已审核模型</option>';
    },
    async saveModel() {
      show('risk-result', await api('risk-models', JSON.parse(value('model-definition')), 'PUT'));
      await actions.loadModels();
    },
    async risk() {
      if (!value('risk-model')) throw new Error('请先选择已审核的模型');
      show('risk-result', await api('risk-assessments', {patient:patient(),model_id:value('risk-model'),version:Number($('risk-model').selectedOptions[0].dataset.version)}));
    },
    async saveFollow() {
      if (!value('follow-due')) throw new Error('请填写计划时间');
      await api('follow-ups', {...context(), task_id:crypto.randomUUID(), disease_code:value('disease'), title:value('follow-title'), due_at:new Date(value('follow-due')).toISOString(),source:value('follow-source'),reassessment_events:value('follow-events').split(',').map(x=>x.trim()).filter(Boolean)}, 'PUT');
      await listFollow();
    },
    listFollow,
    async saveMember() {
      await api('family-members', {hospital_id:context().hospital_id,index_patient_id:context().patient_id,family_id:value('family'),member_id:value('member'),relationship:value('relationship'),patient_id:value('member-patient') || null,variant:value('variant'),interpretation_version:value('variant-version'),classification:value('classification'),genetic_status:value('genetic'),source:value('family-source'),consent_recorded:$('consent').checked}, 'PUT');
      await actions.listMembers();
    },
    async screenMember() {
      if (!value('screen-due')) throw new Error('请填写筛查时间');
      show('family-result', await api('family-screening-tasks', {hospital_id:context().hospital_id,
        family_id:value('family'),member_id:value('member'),task_id:crypto.randomUUID(),disease_code:value('disease'),
        title:value('screen-title'),due_at:new Date(value('screen-due')).toISOString(),source:value('family-source')}));
    },
    async listMembers() { show('family-result', await api(`family-members?${new URLSearchParams({hospital_id:context().hospital_id,family_id:value('family')})}`)); },
    async saveMapping() {
      await api('order-mappings', {hospital_id:context().hospital_id,code:value('order-code'),local_code:value('local-code'),name:value('order-name'),source:value('order-source'),status:value('mapping-status'),reviewer:value('actor')}, 'PUT');
      await actions.listMappings();
    },
    async listMappings() { show('mapping-result', await api(`order-mappings?hospital_id=${encodeURIComponent(context().hospital_id)}`)); },
  };
  document.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button) return;
    button.disabled = true;
    $('status').textContent = '处理中…';
    try {
      if (button.dataset.action) await actions[button.dataset.action]();
      else if (button.dataset.complete) {
        const row = followups[Number(button.dataset.complete)];
        await api('follow-ups',{...row,status:'completed',completed_at:new Date().toISOString()},'PUT');
        await listFollow();
      } else if (button.dataset.feedback) {
        await api(`recommendations/${encodeURIComponent(button.dataset.feedback)}/feedback`, {...context(),status:button.dataset.state});
        await actions.listRecommendations();
      }
      $('status').textContent = '已完成';
    } catch (error) { $('status').textContent = error.message; }
    finally { button.disabled = false; }
  });
})();
