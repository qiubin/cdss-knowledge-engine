'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('src/api/static/app.js', 'utf8');
const source = app.slice(app.indexOf('  function collectStandardForm()'), app.indexOf('  async function renderInference()'));
function setup() {
  const elements = new Map();
  const $ = id => {
    if (!elements.has(id)) elements.set(id, {value: '', closest: () => ({scrollIntoView() {}})});
    return elements.get(id);
  };
  const template = {item_type:'exam', exam_code:'ECHO', exam_name:'超声心动图', finding_code:'LVOT_PEAK_GRADIENT_REST',
    finding_name:'静息左室流出道峰值压差', concept_code:'GE_30', interpretation_type:'numeric', operator:'>=',
    threshold_value:30, threshold_unit:'mmHg', source_note:'指南 §1.1', status:'draft', context_age_min:18};
  const state = {hospitalId:'H', hospitals:[{hospital_id:'H'}], standardEditIndex:-1, pendingStandard:null,
    terms:{exam:{}, exam_finding:{}}, vocab:[{code:'GE_30',name_cn:'大于等于30',category:'exam_concept',evaluator_type:'static_threshold'},
      {code:'PRESENT',name_cn:'存在',category:'exam_concept',evaluator_type:'finding_exists'}]};
  const calls = [];
  const pageEl = {innerHTML:'', querySelectorAll:() => []};
  const api = async (url, options) => {
    calls.push([url, options]);
    if (url.endsWith('/templates/hcm')) return [template];
    if (url.endsWith('/coverage')) return {items:[]};
    return [];
  };
  const context = vm.createContext({state, $, pageEl, api, loadBase:async()=>{}, esc:String,
    panel:(title,body)=>title+body, pagedPanel:()=>'', table:()=>'', bindPagedTable(){}, toast(){}, markStale(){}, conceptName:String});
  vm.runInContext(source, context);
  return {context,state,$,pageEl,calls,template};
}
test('HCM template fills numeric fields and saves a draft without activation', async () => {
  const h = setup(); await h.context.renderStandards();
  h.$('#stdHcmTemplate').value = '0';
  await h.$('#loadHcmStandard').onclick();
  assert.equal(h.state.pendingStandard.threshold_value,30);
  assert.match(h.pageEl.innerHTML, /id="stdThreshold"/);
  assert.match(h.pageEl.innerHTML, /指南 §1.1/);
  const fields = {stdType:'exam',stdLabCode:'ECHO',stdFindingCode:h.template.finding_code,stdConcept:'GE_30',
    stdMode:'numeric',stdOperator:'>=',stdThreshold:'30',stdUnit:'mmHg',stdAgeMin:'18',stdNote:'指南 §1.1'};
  Object.entries(fields).forEach(([id,value])=>{h.$('#'+id).value=value;});
  await h.context.saveStandard();
  const saved = h.calls.find(([url,options])=>url==='/api/standards/H' && options?.json);
  assert.equal(saved[1].json.status,'draft');
  assert.equal(saved[1].json.threshold_value,30);
  assert.equal(saved[1].json.source_note,'指南 §1.1');
  assert.ok(!h.calls.some(([url])=>url.endsWith('/activate')));
});
test('choosing a numeric concept switches presence mode to numeric', async () => {
  const h = setup(); await h.context.renderStandards();
  h.$('#stdType').value='exam'; h.$('#stdConcept').value='GE_30'; h.$('#stdMode').value='presence';
  h.$('#stdConcept').onchange();
  assert.equal(h.state.pendingStandard.interpretation_type,'numeric');
});
