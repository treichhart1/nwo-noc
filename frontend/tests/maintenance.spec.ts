import {test,expect} from '@playwright/test'
import fs from 'node:fs'
test.skip(process.env.NOC102_MAINTENANCE_BROWSER!=='true','Requires isolated maintenance/filesystem fixtures')
function fixture(){const state=process.env.NOC_ACCEPTANCE_STATE!;const env=Object.fromEntries(fs.readFileSync(`${state}/acceptance.env`,'utf8').trim().split('\n').map(l=>{const n=l.indexOf('=');return[l.slice(0,n),l.slice(n+1)]}));return {env,f:JSON.parse(fs.readFileSync(`${state}/maintenance-fixture.json`,'utf8'))}}
async function login(page:any,url:string,user:string,password:string){await page.goto(url);await page.locator('.loginCard input').nth(0).fill(user);await page.locator('input[type=password]').fill(password);await page.getByRole('button',{name:'Sign in',exact:true}).click()}
test('administrator reviews calendar and costs, versions a document and approves explicit clean revision',async({page})=>{
 const{env,f}=fixture();await login(page,'/?maintenance=home',f.admin_username||'acceptance',env.ADMIN_PASSWORD)
 const app=page.getByRole('region',{name:'Preventive maintenance dashboard',exact:true});await expect(app).toBeVisible();await page.getByRole('combobox',{name:/^Maintenance tower/}).selectOption(String(f.site_id));await expect(app).toContainText('NOC102 Synthetic preventive radio inspection')
 await page.getByLabel('Maintenance change reason',{exact:true}).fill('Synthetic administrator reviewed preventive maintenance and documents');await page.getByLabel('I approve internal maintenance documentation only.',{exact:true}).check()
 await page.getByRole('button',{name:'Show maintenance calendar',exact:true}).click();await expect(page.getByRole('region',{name:'Maintenance calendar',exact:true})).toContainText('Open existing work order')
 await page.getByLabel('Include restricted financial data',{exact:true}).check();await page.getByRole('button',{name:'Generate maintenance report',exact:true}).click();await expect(page.getByRole('region',{name:'Printable maintenance report',exact:true})).toContainText('19.50')
 await page.getByRole('button',{name:`Open document #${f.document_id}`,exact:true}).click();await page.getByLabel('Document revision description',{exact:true}).fill('Updated synthetic tower report retaining original revision')
 await page.getByLabel('Upload document revision',{exact:true}).setInputFiles({name:'service-report-v2.txt',mimeType:'text/plain',buffer:Buffer.from('Updated synthetic static service report without credentials')});await expect(page.getByRole('region',{name:'Document version history',exact:true})).toContainText('Version2')
 await page.getByRole('button',{name:'Approve document version2',exact:true}).click();await expect(page.getByRole('region',{name:'Document version history',exact:true})).toContainText('approved version2');await expect(page.getByRole('button',{name:'Download document version1',exact:true})).toBeEnabled()
 await page.getByRole('button',{name:'hold document',exact:true}).click();await expect(page.getByRole('region',{name:'Document version history',exact:true})).toContainText('LEGAL HOLD')
})
test('technician tower QR exposes only approved permitted documents and hides financial service values',async({page})=>{
 const{env,f}=fixture();await page.setViewportSize({width:390,height:844});const external:string[]=[];await page.route('**/*',async r=>{if(new URL(r.request().url()).origin!=='http://127.0.0.1:18090'){external.push(r.request().url());await r.abort()}else await r.continue()})
 await login(page,`/?qr=${f.tower_qr}`,f.tech_username||'field-acceptance',env.ADMIN_PASSWORD)
 const section=page.getByRole('region',{name:'Tower maintenance and documents',exact:true});await expect(section).toContainText('NOC102 Synthetic approved tower service report');await expect(section).not.toContainText('Private purchase invoice')
 await section.getByRole('button',{name:`Open document #${f.document_id}`,exact:true}).click();await expect(section.getByRole('button',{name:'Download document version2',exact:true})).toBeEnabled();await expect(section.getByRole('button',{name:'Download document version1',exact:true})).toBeDisabled();await expect(section).not.toContainText('19.50')
 const status=await page.evaluate(async id=>{const r=await fetch('/api/maintenance-ops/documents/'+id,{headers:{Authorization:`Bearer ${localStorage.getItem('nwo_token')}`}});return r.status},f.private_document_id);expect(status).toBe(403)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(external).toEqual([])
})
