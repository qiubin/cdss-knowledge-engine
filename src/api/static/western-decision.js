(function (root) {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const KINDS = {medication:'药物',intervention:'介入／手术评估',exam:'检查',lab:'检验',differential:'鉴别诊断',monitoring:'监测',referral:'转诊',care:'一般处理'};
  const VERBS = {start:'开始',continue:'继续',adjust:'调整',switch:'替换',add:'联合增加',stop:'停止',avoid:'避免',assess:'评估',order:'安排',monitor:'监测'};
  const STATUS = {ready:'当前可推荐',strategy_only:'仅治疗策略',partial:'部分条件满足',needs_information:'需补充资料',pending_diagnosis:'尚需诊断依据',contraindicated:'存在禁忌',not_applicable:'当前不适用',needs_review:'需医生评估',conditional:'组合条件尚未满足'};
  const options = (items, selected) => Object.entries(items).map(([value,label]) => `<option value="${esc(value)}" ${value===selected?'selected':''}>${esc(label)}</option>`).join('');
  const DATA = {'clinical_attributes.weight_kg':'体重','clinical_attributes.egfr':'肾功能（eGFR）','clinical_attributes.allergies_reviewed':'已核实过敏史','clinical_attributes.medications_reviewed':'已核实当前用药','clinical_attributes.prior_treatment_response':'既往治疗反应'};
  function explanation() {
    return `<h2>西医诊疗决策说明</h2><p>系统围绕当前患者回答“下一步怎么诊疗”。命中疑似诊断后进入相关诊疗流程；检查、治疗、手术和监测分别判断适用条件。急症处置可按其自身条件触发，不机械等待确诊。</p>
    <h3>医生需要回答的问题</h3><ol><li>是否存在危险，是否需要立即处理或转诊？</li><li>最可能是什么病，还需要鉴别什么？</li><li>还需哪些检查用于确诊、分型或排除危险情况？</li><li>当前应开始、继续、调整、替换、联合还是停止治疗？</li><li>如何评价疗效、监测风险、安排复查和随访？</li></ol>
    <h3>系统实现路径</h3><p>指南解析 → 知识图谱草稿 → 图谱与判定标准审核 → 诊疗依据整理及审核 → 规则草稿 → 病例验证及发布 → 接入患者信息 → 判断和推荐。</p>
    <h3>每条建议依据什么</h3><p>诊断阶段、人群和分型、当前病情、既往治疗与反应、现用药、必要安全资料及禁忌。推荐项目和用法绑定指南原文；替代、联合、序贯关系必须明确。</p>
    <h3>结果如何理解</h3><ul><li>当前可推荐：必要条件满足，仍由医生确认实施。</li><li>仅治疗策略：可考虑治疗方向，但缺少明确用法或个体化剂量资料，不生成具体用量。</li><li>需补充资料：显示缺失信息；“没有记录”不等于“明确没有”。</li><li>当前不适用／存在禁忌：显示具体原因。</li><li>手术、鉴别和转诊：区分评估建议与具体实施决策。</li></ul>
    <p>指南未明确的剂量、频次、疗程不自行补齐；已在使用的药物不重复推荐开始。建议不是自动医嘱。西医与中医诊疗流程分别维护，并可按临床需要协同参考。</p>`;
  }
  function tcmExplanation() {
    return `<h2>中医诊疗决策说明</h2><p>系统围绕当前患者回答“中医如何辨病、辨证与施治”。系统综合症状、体征、舌象、脉象、病因病机及兼夹情况，形成中医病名和证候判断，并结合诊疗指南、专家共识及院内规范提供治法、方药和调护建议。</p>
    <p>中医辨证结果用于辅助医生判断，不替代医生四诊合参。遇有急危重症或需要排除器质性疾病的情况，应优先启动相应的急救、检查、转诊或中西医协同诊疗流程。</p>
    <h3>医生需要回答的问题</h3><ol><li>是否存在急危重症表现，是否需要立即处置、检查或转诊？</li><li>当前属于什么中医病名，是否需要结合西医检查明确疾病性质？</li><li>患者的主证是什么，存在哪些兼证、证素或病机变化？</li><li>症状、舌象、脉象等辨证依据是否充分，是否还需补充四诊信息？</li><li>当前适宜采用什么治法、方剂、中成药或非药物疗法？</li><li>是否需要随证加减，是否存在配伍、剂量、禁忌或特殊人群风险？</li><li>如何观察证候变化、评价疗效，并安排复诊、调护和随访？</li></ol>
    <h3>系统实现路径</h3><p>中医指南解析 → 中医知识图谱草稿 → 病名、症状、舌象、脉象、证候、治法和方药关系审核 → 诊疗依据整理及审核 → 辨证与治疗规则草稿 → 病例验证及发布 → 接入患者四诊信息 → 辨病辨证和诊疗建议。</p>
    <h3>中医知识图谱的结构</h3><p>主干是「病 → 证 → 法 → 方 → 药」：中医疾病（如心衰）名下列出证候（如气虚血瘀证，分主证、次证），证候对应治法（如益气活血），治法落实为方剂（如补阳还五汤），方剂拆解为中药组成（含君臣佐使与剂量）。疾病是范围锚——界定「辨什么病、适用哪部指南」；证候是推理锚——治法与方药全部挂在证候上，同病异证则治法方药各异，此即同病异治。</p>
    <p>主干之外有三条支撑分支：①辨证证据分支——证候关联主症、次症、舌象、脉象，是辨证的依据来源；推理从患者的症状、舌象、脉象出发，沿此分支加权归到证候，而不是从疾病直接推治法。②病机证素分支——证候关联病机与证素，解释「为什么这么辨」，支撑审核与回顾，不直接参与推理。③安全分支——证候与方剂关联配伍禁忌与慎用提示，在推荐方药时反向拦截。另有中西医对照分支（中医病名 ↔ 西医疾病，如心衰 ↔ 慢性心力衰竭），仅作对照与中西医协同参考，不参与辨证推理。</p>
    <h3>每条建议依据什么</h3><p>每条建议综合考虑中医病名及当前诊疗阶段，主症、次症及症状组合，舌质、舌苔和脉象，病因、病位、病性及证素，主证、兼证和证候演变，特殊人群情况，既往治疗、当前用药及治疗反应，以及方药配伍、禁忌、不良反应和中西药联用风险。</p>
    <p>治法、方剂、药物组成、剂量、用法、疗程及随证加减应与审核通过的原文依据绑定。系统不得仅凭单个症状直接确定证候，也不得在依据不足时自行补全处方内容。</p>
    <h3>结果如何理解</h3><ul><li>辨证依据充分：当前四诊信息与证候判定条件基本相符，可作为医生辨证参考。</li><li>可能证候：部分条件符合，但仍存在其他证候可能，需要结合完整四诊信息鉴别。</li><li>兼证提示：在主证基础上存在兼夹证候，需由医生判断其主次关系。</li><li>需补充四诊信息：缺少关键症状、舌象、脉象或其他辨证资料；“没有记录”不等于“明确没有”。</li><li>治法建议：可提示治疗方向，但不等同于已经形成完整处方。</li><li>方药建议：适用条件和必要安全信息满足时，展示经审核的方剂或中成药建议。</li><li>仅提供方药策略：指南支持治疗方向，但缺少明确组成、剂量、用法或个体化资料，不生成具体用量。</li><li>不适用或存在禁忌：显示不适用条件、配伍风险或特殊人群风险。</li><li>证候发生变化：复诊信息提示证候演变时，应重新辨证，不机械沿用原有治法和方药。</li></ul>
    <p>系统遵循辨病与辨证结合、四诊合参、同病异治和异病同治原则。指南未明确的药味、剂量、频次、疗程和加减方案不自行补齐；已有治疗不重复推荐开始。所有建议均为临床决策支持，不自动形成处方或医嘱，最终诊疗方案由医生审核确认。</p>`;
  }
  function showExplanation() {
    const dialog=document.createElement('dialog'); dialog.className='dialog';
    dialog.innerHTML=`<div class="dialog-topbar"><strong>诊疗决策说明</strong><button type="button" class="btn ghost" data-close aria-label="关闭说明">× 关闭</button></div><div style="max-height:min(75vh,calc(100dvh - 200px));overflow:auto;padding:20px">${explanation()}<hr>${tcmExplanation()}</div><div style="display:flex;justify-content:flex-end;padding:0 20px 20px"><button type="button" class="btn primary" data-close>关闭说明</button></div>`;
    document.body.append(dialog);dialog.querySelectorAll('[data-close]').forEach(el=>el.onclick=()=>dialog.close());
    dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.showModal();
  }
  function editCondition(initial, onSave) {
    let tree = initial ? JSON.parse(JSON.stringify(initial)) : null;
    const dialog=document.createElement('dialog');dialog.className='dialog';document.body.append(dialog);
    const TYPES={group:'条件组',attribute:'患者属性／治疗反应',symptom:'症状',vital:'生命体征',exam_finding:'检查发现',lab_concept:'检验判定',medication:'当前用药',past_history:'既往疾病',demographic:'年龄及性别'};
    const FIELDS={attribute:[['attribute_code','患者属性编码'],['operator','比较方式'],['attribute_value','预期值']],symptom:[['symptom_code','症状编码']],vital:[['vital_code','体征编码'],['operator','比较方式'],['threshold_value','阈值']],exam_finding:[['exam_code','检查编码'],['finding_code','发现编码'],['concept_code','判定概念']],lab_concept:[['lab_code','检验编码'],['concept_code','判定概念']],medication:[['code','药品编码']],past_history:[['code','疾病编码']],demographic:[['age_min','年龄下限'],['age_max','年龄上限'],['gender','性别（M/F）']]};
    const at=path=>path.reduce((n,i)=>n.children[i],tree);
    function nodeHtml(node,path=[]) {
      const key=path.join('.');return `<fieldset><label>条件类型<select data-kind="${key}">${options({...TYPES,...(!TYPES[node.type]?{[node.type]:'已带入条件（保留）'}:{})},node.type)}</select></label>${node.type==='group'?`<label>组合关系<select data-op="${key}"><option value="">请选择</option>${options({AND:'全部满足',OR:'任意满足',NOT:'不满足'},node.operator)}</select></label>${(node.children||[]).map((c,i)=>nodeHtml(c,[...path,i])).join('')}<button type="button" class="btn" data-child="${key}">增加子条件</button>`:(FIELDS[node.type]||[]).map(([field,label])=>`<label>${label}<input data-node="${key}" data-key="${field}" value="${esc(node[field])}"></label>`).join('')}<button type="button" class="btn" data-delete="${key}">删除条件</button></fieldset>`;
    }
    function render() {
      dialog.innerHTML=`<div class="dialog-topbar"><strong>编辑推荐动作条件</strong><button type="button" class="btn ghost" data-cancel aria-label="关闭条件编辑">× 关闭</button></div><div style="padding:16px;max-height:min(75vh,calc(100dvh - 200px));overflow:auto"><p>缺少记录按未知处理。禁忌组中任一已知禁忌命中时不推荐该动作。数值检查须使用已审核的判定概念及标准。</p>${tree?nodeHtml(tree):'<button type="button" class="btn" data-root>增加条件</button>'}<button type="button" class="btn primary" data-save>保存条件</button><button type="button" class="btn" data-cancel>取消</button></div>`;
      const path=value=>value===''?[]:value.split('.').map(Number);
      dialog.querySelector('[data-root]')?.addEventListener('click',()=>{tree={type:'group',operator:'',children:[]};render();});
      dialog.querySelectorAll('[data-op]').forEach(el=>el.onchange=()=>{at(path(el.dataset.op)).operator=el.value;});
      dialog.querySelectorAll('[data-kind]').forEach(el=>el.onchange=()=>{const node=at(path(el.dataset.kind));Object.keys(node).forEach(k=>delete node[k]);node.type=el.value;if(node.type==='group'){node.operator='';node.children=[];}render();});
      dialog.querySelectorAll('[data-key]').forEach(el=>el.oninput=()=>{let value=el.value.trim();if(value==='true'||value==='false')value=value==='true';else if(value!==''&&['threshold_value','age_min','age_max','attribute_value'].includes(el.dataset.key)&&!Number.isNaN(Number(value)))value=Number(value);at(path(el.dataset.node))[el.dataset.key]=value===''?null:value;});
      dialog.querySelectorAll('[data-child]').forEach(el=>el.onclick=()=>{at(path(el.dataset.child)).children.push({type:'attribute',attribute_code:'',operator:'=',attribute_value:null});render();});
      dialog.querySelectorAll('[data-delete]').forEach(el=>el.onclick=()=>{const p=path(el.dataset.delete);if(!p.length)tree=null;else{const i=p.pop();at(p).children.splice(i,1);}render();});
      dialog.querySelector('[data-save]').onclick=()=>{onSave(tree);dialog.close();};dialog.querySelectorAll('[data-cancel]').forEach(el=>el.onclick=()=>dialog.close());
    }
    dialog.addEventListener('close',()=>dialog.remove(),{once:true});render();dialog.showModal();
  }
  function mountEditor(host, base, source) {
    let rec = base ? JSON.parse(JSON.stringify(base)) : {schema_version:1,diagnosis_stage:'confirmed',clinical_question:'',setting:'',combination:'single',selection_group:'',scope_confirmed:false,actions:[]};
    let enabled=!!base;
    const input=(key,label,value)=>`<label>${label}<input data-field="${key}" value="${esc(value)}"></label>`;
    function read() {
      if (!enabled) return null;
      ['clinical_question','setting','selection_group','diagnosis_stage','combination'].forEach(k=>{rec[k]=host.querySelector(`[data-rec="${k}"]`).value;});
      rec.scope_confirmed=host.querySelector('[data-confirm]').checked;
      host.querySelectorAll('[data-action-row]').forEach(row=>{
        const action=rec.actions[Number(row.dataset.actionRow)];
        row.querySelectorAll('[data-field]').forEach(el=>{if(el.dataset.field.startsWith('regimen.')) {action.regimen ||= {}; action.regimen[el.dataset.field.slice(8)]=el.value.trim();} else if(el.dataset.field.startsWith('source.')) {action.source ||= {...(source||{})};const key=el.dataset.field.slice(7);action.source[key]=['page_start','page_end','line_start','line_end'].includes(key)?(el.value===''?null:Number(el.value)):el.value.trim();} else action[el.dataset.field]=el.value.trim();});
        action.monitoring=row.querySelector('[data-monitoring]').value.split('\n').map(v=>v.trim()).filter(Boolean);
        const retained=(action.required_data||[]).filter(d=>!Object.hasOwn(DATA,d.path));
        row.querySelectorAll('[data-datum]:checked').forEach(el=>retained.push({path:el.dataset.datum,label:DATA[el.dataset.datum],purpose:el.dataset.datum.endsWith('reviewed')?'selection':'dose',...(el.dataset.datum.endsWith('reviewed')?{expected_value:true}:{})}));
        action.required_data=retained;
      });
      return JSON.parse(JSON.stringify(rec));
    }
    function render() {
      host.innerHTML=`<h4>西医诊疗推荐</h4><label><input type="checkbox" data-enabled ${enabled?'checked':''}>使用结构化诊疗推荐（诊断条件在下方条件树维护）</label>${enabled?`<div class="form-grid"><label>诊断阶段<select data-rec="diagnosis_stage">${options({suspected:'疑似或确诊',confirmed:'需确诊',independent:'按自身条件触发，不依赖确诊'},rec.diagnosis_stage)}</select></label><label>临床问题<input data-rec="clinical_question" value="${esc(rec.clinical_question)}"></label><label>场景<input data-rec="setting" value="${esc(rec.setting)}" placeholder="如门诊初诊、住院、复诊"></label><label>动作关系<select data-rec="combination">${options({single:'单项',alternatives:'替代，选择其中之一',combined:'联合，需整体满足',sequential:'序贯，需确认前序治疗条件'},rec.combination)}</select></label><label>替代方案选择组<input data-rec="selection_group" value="${esc(rec.selection_group)}"></label></div>
      ${rec.actions.map((a,i)=>`<fieldset data-action-row="${i}"><legend>推荐动作 ${i+1}</legend><div class="form-grid"><label>项目类型<select data-field="kind">${options(KINDS,a.kind)}</select></label><label>动作<select data-field="verb">${options(VERBS,a.verb)}</select></label>${input('name','名称',a.name)}${input('code','项目编码',a.code)}${['dose','route','frequency','course','titration'].map((k,j)=>input('regimen.'+k,['剂量（仅填原文）','途径','频次','疗程','调整／滴定方法'][j],a.regimen?.[k])).join('')}</div><p>必要资料（原文要求时勾选）</p>${Object.entries(DATA).map(([path,label])=>`<label style="margin-right:12px"><input type="checkbox" data-datum="${path}" ${(a.required_data||[]).some(d=>d.path===path)?'checked':''}>${label}</label>`).join('')}<label>监测与随访（一行一项）<textarea data-monitoring>${esc((a.monitoring||[]).join('\n'))}</textarea></label><details><summary>动作原文与定位</summary><div class="form-grid">${[['chunk_id','原文片段'],['page_start','起始页'],['page_end','结束页'],['line_start','起始行'],['line_end','结束行']].map(([key,label])=>input('source.'+key,label,a.source?.[key]??source?.[key])).join('')}</div><label>逐字引文<textarea data-field="source.quote">${esc(a.source?.quote||source?.quote||'')}</textarea></label></details><p>独立适用条件：${a.conditions?'已带入':'沿用下方条件树'}；禁忌条件：${(a.contraindications||[]).length} 项。</p><button type="button" class="btn" data-criteria="${i}">查看／编辑附加适用条件</button><button type="button" class="btn" data-contra="${i}">查看／编辑禁忌条件</button><button type="button" class="btn" data-remove="${i}">移除此动作</button></fieldset>`).join('')}<button type="button" class="btn" data-add>增加推荐动作</button><p><label><input type="checkbox" data-confirm ${rec.scope_confirmed?'checked':''}>已核对诊断阶段、适用范围、用法原文及方案组合关系</label></p><p class="muted">修改后须重新确认；缺少明确用法时只提供策略，不生成具体用量。</p>`:''}`;
      host.querySelector('[data-enabled]').onchange=e=>{enabled=e.target.checked;render();};
      host.querySelector('[data-add]')?.addEventListener('click',()=>{read();rec.actions.push({kind:'medication',verb:'assess',name:'',code:'',regimen:{},monitoring:[],source:source||{}});rec.scope_confirmed=false;render();});
      host.querySelectorAll('[data-criteria],[data-contra]').forEach(button=>button.onclick=()=>{
        read();const isContra=button.hasAttribute('data-contra');const index=Number(isContra?button.dataset.contra:button.dataset.criteria);const action=rec.actions[index];
        const initial=isContra?((action.contraindications||[]).length?{type:'group',operator:'OR',children:action.contraindications}:null):action.conditions;
        editCondition(initial,condition=>{if(isContra)action.contraindications=condition?[condition]:[];else action.conditions=condition;rec.scope_confirmed=false;render();});
      });
      host.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{read();rec.actions.splice(Number(b.dataset.remove),1);rec.scope_confirmed=false;render();});
      host.querySelectorAll('input,textarea,select').forEach(el=>{if(el.matches('[data-confirm],[data-enabled]'))return;el.addEventListener('input',()=>{const box=host.querySelector('[data-confirm]');if(box)box.checked=false;});});
    }
    render();return {read};
  }
  function conditionSummary(node) {
    if(!node)return '无附加条件';
    if(node.label)return node.label;
    if(node.type==='group')return '('+(node.children||[]).map(conditionSummary).join(({AND:' 且 ',OR:' 或 ',NOT:' 不满足 '})[node.operator]||' 待确认 ')+')';
    return [node.attribute_code||node.symptom_code||node.finding_code||node.lab_code||node.vital_code||node.code||node.type,node.operator,node.attribute_value??node.threshold_value??node.concept_code].filter(v=>v!==undefined&&v!==null).join(' ');
  }
  function resultHtml(items) {
    if (!items?.length) return '';
    return `<section><h3>西医患者级诊疗建议</h3>${items.map(r=>`<article class="evidence-card"><h4>${esc(r.clinical_question||r.name)} · ${esc(STATUS[r.status]||r.status)}</h4><p>方案关系：${esc({single:'单项',alternatives:'替代选择，不可视为联合',combined:'联合',sequential:'序贯'}[r.combination]||'')} ${esc(r.selection_group||'')}</p>${(r.reasons||[]).map(v=>`<p>${esc(v)}</p>`).join('')}${(r.actions||[]).map(a=>`<div class="evidence-card"><strong>${esc(KINDS[a.kind])} · ${esc(VERBS[a.verb])}${esc(a.name)} · ${esc(STATUS[a.status]||a.status)}</strong><p>${esc((a.reasons||[]).join('；'))}</p>${Object.keys(a.regimen||{}).length?`<p>用法：${Object.entries(a.regimen).filter(([,v])=>v).map(([k,v])=>esc(({dose:'剂量',route:'途径',frequency:'频次',course:'疗程',titration:'调整'})[k]||k)+'：'+esc(v)).join('；')}</p>`:''}<p>${(a.missing_data||[]).map(d=>'需补充：'+esc(d.label)).join('；')}</p><p>${esc((a.monitoring||[]).join('；'))}</p><details><summary>判断依据与原文</summary>${a.applicability?`<p>共同条件：${esc(conditionSummary(a.applicability.shared_conditions))}</p><p>动作条件：${esc(conditionSummary(a.applicability.action_conditions))}</p><p>禁忌：${(a.applicability.contraindications||[]).map(conditionSummary).map(esc).join('；')||'本条未配置'}</p>`:''}<p>${esc(a.source?.quote||r.source?.quote||'')}</p></details></div>`).join('')}<small>${esc(r.rule_id)}</small></article>`).join('')}</section>`;
  }
  const api={explanation,tcmExplanation,showExplanation,mountEditor,resultHtml};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.WesternDecision=api;
  if (typeof document !== 'undefined') document.getElementById('globalDecisionHelp')?.addEventListener('click', showExplanation);
})(typeof window!=='undefined'?window:globalThis);
