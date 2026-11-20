'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/api/static/app.js','utf8');
const fn = source.slice(source.indexOf('  async function renderStandardReview()'), source.indexOf('  async function renderReviewCenter()'));
function setup() {
  const nodes = new Map();
  const $ = id => {if (!nodes.has(id)) nodes.set(id,{value:'',checked:false});return nodes.get(id);};
  const buttons = ['approve','save','reject','return'].map(action=>({dataset:{standardAction:action}}));
  const fields = [{dataset:{standardField:'threshold_value'},type:'number',value:'30'}];
  const pageEl = {innerHTML:'',querySelectorAll(selector) {return selector==='[data-standard-action]'?buttons:fields;}};
  const item = {item_id:'STD-1',version:1,origin:'guideline',status:'draft',name:'静息压差',
    source:{chunk_id:'DOC-T_p1_l1-2',quote:'压差≥30 mmHg',page_start:1,line_start:1,line_end:2},
    standard:{item_type:'exam',exam_code:'ECHO',finding_code:'REST',operator:'>=',threshold_value:30,threshold_unit:'mmHg'}};
  const calls=[];
  const context=vm.createContext({state:{reviewFilters:{},hospitalId:'H'},pageEl,$,esc:String,
    loadBase:async()=>{}, REVIEW_TABS:[['standards','判定标准审核']], reviewBadge:String,reviewCategoriesHtml:()=>'',
    panel:(title,body)=>title+body, bindReviewTabs(){},URLSearchParams,
    sourceRefsHtml:async()=>'<p>原文上下文</p>',localStorage:{getItem:()=> '审核医生',setItem(){}},toast(){},
    api:async(url,options)=>{calls.push([url,options]);return options?{status:'active'}:{items:[item]};}});
  vm.runInContext(fn,context);
  return {context,$,buttons,pageEl,calls};
}
test('review displays source, numeric fields and publishes with reviewer and version',async()=>{
  const h=setup();await h.context.renderStandardReview();
  assert.match(h.pageEl.innerHTML,/压差≥30 mmHg/);
  assert.match(h.pageEl.innerHTML,/data-standard-field="threshold_unit"/);
  h.$('#standardReviewer').value='审核医生';h.$('#standardPublishHospitals').value='H,H2';h.$('#standardScopeConfirmed').checked=true;
  await h.buttons[0].onclick();
  const call=h.calls.find(([url,options])=>options);
  assert.equal(call[0],'/api/review/standards/STD-1/review');
  assert.equal(call[1].json.action,'approve');assert.equal(call[1].json.version,1);
  assert.equal(call[1].json.scope_confirmed,true);
  assert.deepEqual(Array.from(call[1].json.hospital_ids),['H','H2']);
});
test('review navigation routes to unified standard review',()=>{
  assert.match(source,/\["standards", "判定标准审核"\]/);
  assert.match(source,/if \(tab === "standards"\) \{ await renderStandardReview\(\); return; \}/);
});
