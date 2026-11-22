'use strict';
const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');
const ui=require('../../src/api/static/western-decision.js');
test('overview explanation documents the clinical questions and audited pipeline',()=>{
 const text=ui.explanation();for(const expected of ['危险','鉴别','继续','监测','指南解析','诊疗依据','病例验证','没有记录','西医'])assert.ok(text.includes(expected));
 const app=fs.readFileSync('src/api/static/app.js','utf8');assert.ok(app.includes('id="clinicalDecisionHelp"'));assert.ok(app.includes('WesternDecision.showExplanation()'));
});
test('tcm explanation documents four examinations, pattern differentiation and safe recommendations',()=>{
 const text=ui.tcmExplanation();
 for(const expected of ['中医诊疗决策说明','四诊合参','舌象','脉象','主证','兼证','治法','方药','中医知识图谱','急危重症','没有记录','不自动形成处方'])assert.ok(text.includes(expected));
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

test('fixed top button opens and closes the decision explanation independently of dashboard loading',()=>{
 const vm=require('node:vm');let handler;let shown=false;let removed=false;
 const closeButton={};const events={};
 const dialog={innerHTML:'',className:'',querySelector:()=>closeButton,addEventListener:(event,callback)=>{events[event]=callback;},showModal:()=>{shown=true;},close:()=>{events.close();},remove:()=>{removed=true;}};
 const document={getElementById:id=>id==='globalDecisionHelp'?{addEventListener:(event,callback)=>{handler=callback;}}:null,createElement:()=>dialog,body:{append(){}}};
 vm.runInNewContext(fs.readFileSync('src/api/static/western-decision.js','utf8'),{document});
 assert.equal(typeof handler,'function');handler();
 assert.equal(shown,true);assert.ok(dialog.innerHTML.includes('西医诊疗决策说明'));assert.ok(dialog.innerHTML.includes('中医诊疗决策说明'));
 closeButton.onclick();assert.equal(removed,true);
 const index=fs.readFileSync('src/api/static/index.html','utf8');assert.ok(index.includes('id="globalDecisionHelp"'));
});
