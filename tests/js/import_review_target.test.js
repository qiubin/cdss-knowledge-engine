'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../../src/api/static/app.js'),'utf8');
const block = source.slice(source.indexOf('  function renderImportResult()'),source.indexOf('  async function renderImportTab()'));
for (const empty of [true,false]) {
 test(`review inherits preset graph and ${empty?'explains empty result':'shows actual syndrome'}`,()=>{
  const elements=new Map();
  const make=(id)=>({value:id==='#graphTargetMode'?'legacy':'',innerHTML:'',classList:{toggle(){}},querySelectorAll(){return[];}});
  const $=(id)=>{if(!elements.has(id))elements.set(id,make(id));return elements.get(id);};
  const state={doc:{doc_id:'D',doc_title:'T',domain:'tcm',graph_filename:'PRESET.json',entities:empty?[]:[{entity_type:'syndrome',code:'S',name:'气虚血瘀证'}],suggested_rules:[],chunks:[],warnings:[],provider_metadata:{coverage:{complete:!empty,total:2,processed:empty?0:2}}}};
  const context=vm.createContext({state,$,normalizeDomainClient:x=>x,esc:x=>String(x??''),panel:(title,body)=>body,renderImportTab(){},generatePackage(){},toast(){}});
  vm.runInContext(block+'\nrenderImportResult();',context);
  const html=$('#importResult').innerHTML;
  assert.match(html,/沿用已设置图谱：PRESET.json/);
  assert.equal(state.doc.graphTarget.filename,'PRESET.json');
  if(empty){assert.match(html,/未识别到可用目标，请先重新解析/);assert.match(html,/id="generatePackage"[^>]*disabled/);}
  else assert.match(html,/气虚血瘀证/);
 });
}
