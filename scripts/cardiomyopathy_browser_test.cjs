// Run against an isolated development server with CDSS_WORKFLOW_DB set.
const {chromium}=require('playwright');
if (!process.env.CDSS_BASE_URL) throw new Error('Set CDSS_BASE_URL to the isolated test server');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(process.env.CDSS_BASE_URL + '/static/cardiomyopathy.html');
 for(const [id,v] of Object.entries({hospital:'TEST-H',patient:'TEST-P-'+Date.now(),actor:'browser-test',disease:'TEST-HCM'})) await page.locator('#'+id).fill(v);
 await page.getByRole('button',{name:'当前病历与治疗建议',exact:true}).click();
 await page.locator('#age').fill('40');
 await page.locator('[data-action=saveSnapshot]').click();await page.getByText('已完成',{exact:true}).waitFor();
 await page.locator('[data-action=history]').click();await page.getByText('已完成',{exact:true}).waitFor();
 if(!(await page.locator('#history').textContent()).includes('TEST-P'))throw Error('snapshot missing');
 await page.getByRole('button',{name:'复查与再评估',exact:true}).click();
 for(const [id,v] of Object.entries({'follow-title':'测试复查','follow-due':'2027-01-01T10:00','follow-source':'browser fixture'}))await page.locator('#'+id).fill(v);
 await page.locator('[data-action=saveFollow]').click();await page.locator('#follow-ups').getByText(/测试复查/).waitFor();
 await page.locator('[data-complete="0"]').click();await page.locator('#follow-ups').getByText(/已完成/).waitFor();
 await page.getByRole('button',{name:'风险评估',exact:true}).click();
 await page.locator('[data-action=loadModels]').click();await page.getByText('已完成',{exact:true}).waitFor();
 if (process.env.CDSS_SCREENSHOT) await page.screenshot({path:process.env.CDSS_SCREENSHOT,fullPage:true});
 if(errors.length)throw Error(errors.join('\n'));
 console.log('PASS: load, snapshot, history, follow-up create/complete, model empty state; no browser errors');
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
