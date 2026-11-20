'use strict';
const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');
const ui=require('../../src/api/static/western-decision.js');
test('overview explanation documents the clinical questions and audited pipeline',()=>{
 const text=ui.explanation();for(const expected of ['危险','鉴别','继续','监测','指南解析','诊疗依据','病例验证','没有记录','西医'])assert.ok(text.includes(expected));
 const app=fs.readFileSync('src/api/static/app.js','utf8');assert.ok(app.includes('id="clinicalDecisionHelp"'));assert.ok(app.includes('WesternDecision.showExplanation()'));
});
test('patient recommendation results distinguish strategies and do not invent dose',()=>{
 const html=ui.resultHtml([{rule_id:'R',name:'<script>bad</script>',status:'partial',combination:'alternatives',selection_group:'G',actions:[{kind:'medication',verb:'start',name:'药物',status:'strategy_only',regimen:{},missing_data:[{label:'体重'}],reasons:['剂量资料不足'],source:{quote:'原文'}}]}]);
 assert.ok(html.includes('仅治疗策略'));assert.ok(html.includes('替代选择'));assert.ok(html.includes('需补充：体重'));assert.ok(!html.includes('用法：'));assert.ok(!html.includes('<script>'));
});
test('editor roundtrip retains source, action criteria and required patient data',()=>{
 const base={schema_version:1,diagnosis_stage:'suspected',clinical_question:'用药选择',setting:'门诊',combination:'single',selection_group:'',scope_confirmed:false,actions:[{kind:'medication',verb:'continue',name:'药',code:'M',regimen:{dose:'原文剂量'},source:{quote:'原文'},conditions:{type:'attribute',attribute_code:'stage',attribute_value:'A'},contraindications:[{type:'attribute',attribute_code:'allergy',attribute_value:true}],required_data:[{path:'clinical_attributes.custom',label:'自定义资料'}]}]};
 const selectors=new Map();function element(value=''){return {value,checked:false,addEventListener(){}};}
 for(const k of ['clinical_question','setting','selection_group','diagnosis_stage','combination'])selectors.set(`[data-rec="${k}"]`,element(base[k]));
 selectors.set('[data-confirm]',element());selectors.set('[data-enabled]',element());selectors.set('[data-add]',element());
 const row={dataset:{actionRow:'0'},querySelectorAll:()=>[],querySelector:()=>element('监测血压')};
 const host={innerHTML:'',querySelector:s=>selectors.get(s)||null,querySelectorAll:s=>s==='[data-action-row]'?[row]:[]};
 const editor=ui.mountEditor(host,base,{});const result=editor.read();
 assert.equal(result.actions[0].conditions.attribute_code,'stage');assert.equal(result.actions[0].source.quote,'原文');assert.equal(result.actions[0].contraindications.length,1);assert.equal(result.actions[0].required_data[0].label,'自定义资料');assert.equal(result.actions[0].monitoring[0],'监测血压');
});
