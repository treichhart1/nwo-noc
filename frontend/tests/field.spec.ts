import {test,expect} from '@playwright/test'
import fs from 'node:fs'
test.skip(process.env.NOC101_FIELD_BROWSER!=='true','Requires isolated field and equipment fixtures')
function fixture(){const state=process.env.NOC_ACCEPTANCE_STATE!;const env=Object.fromEntries(fs.readFileSync(`${state}/acceptance.env`,'utf8').trim().split('\n').map(l=>{const i=l.indexOf('=');return[l.slice(0,i),l.slice(i+1)]}));return {env,f:JSON.parse(fs.readFileSync(`${state}/field-fixture.json`,'utf8'))}}
async function login(page:any,url:string,user:string,password:string){await page.goto(url);await page.locator('.loginCard input').nth(0).fill(user);await page.locator('input[type=password]').fill(password);await page.getByRole('button',{name:'Sign in',exact:true}).click()}
test('phone technician scans authenticated tower, preserves draft, retries once and completes inspection',async({page,context})=>{
 const{env,f}=fixture();await page.setViewportSize({width:390,height:844});const external:string[]=[]
 await page.route('**/*',async r=>{if(new URL(r.request().url()).origin!=='http://127.0.0.1:18090'){external.push(r.request().url());await r.abort()}else await r.continue()})
 await login(page,`/?qr=${f.tower_qr}`,'field-acceptance',env.ADMIN_PASSWORD)
 const app=page.getByRole('main',{name:'Field technician dashboard'});await expect(app).toContainText('field-acceptance');await expect(app).toContainText(`tower#${f.site_id}`)
 const bypass=await page.evaluate(async()=>{const r=await fetch('/api/devices',{headers:{Authorization:`Bearer ${localStorage.getItem('nwo_token')}`}});return r.status});expect(bypass).toBe(403)
 await page.getByRole('button',{name:`Open work order #${f.work_id}`,exact:true}).click()
 await page.getByLabel('Field change reason',{exact:true}).fill('Synthetic mobile technician approved site inspection documentation');await page.getByLabel('I approve documentation changes only.',{exact:true}).check()
 await page.getByLabel('Work performed or technician note',{exact:true}).fill('Started synthetic tower inspection after authorized site verification')
 // A dropdown option is not evidence that the preceding mutation was acknowledged.
 // Hold the committed status response so this test cannot race a reload with its retry envelope.
 let acknowledgeStatus!:()=>void, statusCommitted!:()=>void
 const statusGate=new Promise<void>(resolve=>{acknowledgeStatus=resolve})
 const statusArrived=new Promise<void>(resolve=>{statusCommitted=resolve})
 await page.route(`**/api/field/work-orders/${f.work_id}/status`,async route=>{const response=await route.fetch();statusCommitted();await statusGate;await route.fulfill({response})})
 await page.getByRole('button',{name:'Save work status',exact:true}).click();await statusArrived
 const savedStatus=page.getByRole('region',{name:'Field work order',exact:true}).locator('p').first()
 try{await expect(savedStatus).not.toContainText('in_progress')}finally{acknowledgeStatus()}
 await expect(savedStatus).toContainText('in_progress')
 await expect(page.getByRole('region',{name:'Pending field submission',exact:true})).toHaveCount(0)
 await page.getByLabel('Inspection work notes',{exact:true}).fill('Synthetic mobile draft survives reload without background synchronization')
 await page.reload();await expect(page.getByLabel('Inspection work notes',{exact:true})).toHaveValue('Synthetic mobile draft survives reload without background synchronization')
 await context.setOffline(true);await expect(app).toContainText('Offline — submissions disabled');await expect(page.getByRole('button',{name:'Submit completed inspection',exact:true})).toBeDisabled();await context.setOffline(false)
 await page.getByLabel('Field change reason',{exact:true}).fill('Synthetic mobile technician approved site inspection documentation');await page.getByLabel('I approve documentation changes only.',{exact:true}).check()
 await page.getByLabel('Inspection local date and time',{exact:true}).fill('2026-01-01T10:00')
 for(const select of await page.locator('[aria-label="Inspection checklist"] select').all())await select.selectOption('pass')
 let requests=0
 await page.route(`**/api/field/work-orders/${f.work_id}/inspections`,async route=>{requests++;if(requests===1){const response=await route.fetch();expect(response.ok(),await response.text()).toBe(true);await route.abort('failed')}else await route.continue()})
 await page.getByRole('button',{name:'Submit completed inspection',exact:true}).click();await expect(page.getByRole('region',{name:'Pending field submission',exact:true})).toBeVisible()
 await page.getByRole('button',{name:'Retry saved submission',exact:true}).click();await expect(page.getByRole('region',{name:'Pending field submission',exact:true})).toHaveCount(0)
 const work=page.getByRole('region',{name:'Field work order',exact:true});await expect(work).toContainText('Inspection#');expect(requests).toBe(2);expect(await work.locator('article').filter({hasText:/Inspection#/}).count()).toBe(1)
 const file=page.getByLabel('Attach inspection photograph or report',{exact:true});await expect(file).toBeEnabled();await file.setInputFiles({name:'field-report.txt',mimeType:'text/plain',buffer:Buffer.from('Synthetic tower field inspection report, no live equipment')});await expect(page.getByRole('button',{name:/Download field-report.txt/})).toBeVisible()
 await page.getByRole('combobox',{name:/^Work order status/}).selectOption('completed');await page.getByLabel('Work performed or technician note',{exact:true}).fill('Completed synthetic visual inspection and recorded all checklist evidence');await page.getByRole('button',{name:'Save work status',exact:true}).click();await expect(work).toContainText('completed')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(external).toEqual([])
 await page.getByRole('button',{name:'Sign out of field session',exact:true}).click();await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeVisible();expect(await page.evaluate(()=>Object.keys(sessionStorage).filter(k=>k.startsWith('noc101-')))).toEqual([])
})
test('administrator prints actual SVG QR label and revokes it without deleting work history',async({page})=>{
 const{env,f}=fixture()
 // Username readiness precedes QR resolution. Hold the real tower response to
 // exercise that ordering, then require the identified tower before mutation.
 let releaseTower!:()=>void,towerArrived!:()=>void
 const towerGate=new Promise<void>(resolve=>{releaseTower=resolve})
 const towerRequested=new Promise<void>(resolve=>{towerArrived=resolve})
 await page.route('**/api/field/towers/**',async route=>{
  if(new URL(route.request().url()).pathname!=='/api/field/towers/'+f.site_id){await route.continue();return}
  const response=await route.fetch();expect(response.ok()).toBe(true);towerArrived()
  await towerGate;await route.fulfill({response})
 })
 await login(page,`/?qr=${f.tower_qr}`,'acceptance',env.ADMIN_PASSWORD)
 await expect(page.getByRole('main',{name:'Field technician dashboard'})).toContainText('acceptance')
 const identifiedTower=page.getByRole('region',{name:'Authorized towers',exact:true}).getByRole('heading',{level:3,name:new RegExp('tower#'+f.site_id+'$')})
 await towerRequested
 try{await expect(identifiedTower).toHaveCount(0)}finally{releaseTower()}
 await expect(identifiedTower).toBeVisible()
 await page.getByLabel('Field change reason',{exact:true}).fill('Synthetic administrator approved QR lifecycle documentation');await page.getByLabel('I approve documentation changes only.',{exact:true}).check()
 const selectedTowerLabels=page.waitForResponse(response=>{const url=new URL(response.url());return url.pathname==='/api/field/labels'&&url.searchParams.get('site_id')===String(f.site_id)})
 await page.getByRole('button',{name:'Provision labels for all towers',exact:true}).click()
 expect((await selectedTowerLabels).ok()).toBe(true)
 await expect(page.getByRole('button',{name:`Prepare printable label #${f.label_id}`,exact:true})).toBeVisible()
 await page.getByRole('button',{name:'Browse all QR labels',exact:true}).click();await expect(page.getByRole('button',{name:'Next QR labels',exact:true})).toBeEnabled();await page.getByRole('button',{name:'Next QR labels',exact:true}).click();await page.getByRole('button',{name:`Prepare printable label #${f.label_id}`,exact:true}).click();await expect(page.getByRole('region',{name:'Printable QR label'})).toContainText('https://noc.example.invalid/?qr=');await expect(page.getByRole('img',{name:'Authenticated NOC QR code'})).toBeVisible()
 await page.getByRole('button',{name:`Revoke label #${f.label_id}`,exact:true}).click();await page.goto(`/?qr=${f.tower_qr}`);await expect(page.getByRole('alert')).toContainText('unavailable');await expect(page.getByRole('button',{name:`Open work order #${f.work_id}`,exact:true})).toBeVisible()
})
test('technician documents scanned equipment replacement and closes the work order atomically',async({page})=>{
 const{env,f}=fixture();const stock=JSON.parse(fs.readFileSync(`${process.env.NOC_ACCEPTANCE_STATE}/equipment-fixture.json`,'utf8'));await page.setViewportSize({width:390,height:844})
 await login(page,`/?qr=${f.asset_qr}`,'field-acceptance',env.ADMIN_PASSWORD)
 await page.getByRole('button',{name:`Open work order #${f.replacement_work_id}`,exact:true}).click()
 await page.getByLabel('Field change reason',{exact:true}).fill('Synthetic technician approved physical replacement documentation only');await page.getByLabel('I approve documentation changes only.',{exact:true}).check()
 await page.getByLabel('Work performed or technician note',{exact:true}).fill('Started approved synthetic equipment replacement and verified identity');await page.getByRole('button',{name:'Save work status',exact:true}).click()
 const work=page.getByRole('region',{name:'Field work order',exact:true});await expect(work).toContainText('in_progress')
 await page.getByText('Document equipment replacement',{exact:true}).click();await page.getByRole('combobox',{name:/^Replacement spare/}).selectOption(String(f.spare_id));await page.getByRole('combobox',{name:/^Removed equipment location/}).selectOption(String(stock.repair_location_id))
 await page.getByLabel('Installation details',{exact:true}).fill('Installed verified synthetic spare and retained existing logical device association');await page.getByLabel('Removal details',{exact:true}).fill('Removed synthetic old radio to repair stock for qualified inspection');await page.getByLabel('Complete work order in the same transaction',{exact:true}).check()
 await page.getByRole('button',{name:'Document field replacement',exact:true}).click();await expect(work).toContainText('completed');await expect(work).toContainText('equipment_replaced')
 const result=await page.evaluate(async({asset,spare})=>{const headers={Authorization:`Bearer ${localStorage.getItem('nwo_token')}`};const a=await fetch('/api/field/assets/'+asset,{headers});const b=await fetch('/api/field/assets/'+spare,{headers});return {old:await a.json(),new:await b.json()}},{asset:f.asset_id,spare:f.spare_id});expect(result.old.state).toBe('removed');expect(result.new.state).toBe('installed');expect(result.new.availability.state).toBe('unknown')
})

test('optional network context authorization failure preserves the primary field session and inspection draft',async({page})=>{
 const {env,f}=fixture();let release!:()=>void;const proceed=new Promise<void>(resolve=>{release=resolve})
 await page.route('**/api/field/work-orders/*/unified-context',async route=>{await proceed;await route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({detail:'Synthetic auxiliary authorization unavailable'})})})
 await login(page,'/?field=home&work='+f.context_work_id,'field-context-acceptance',env.ADMIN_PASSWORD)
 const note=page.getByLabel('Inspection work notes',{exact:true});await note.fill('Synthetic inspection draft must survive optional context failure')
 release();await expect(page.getByRole('region',{name:'Assigned network components',exact:true})).toContainText('unavailable')
 expect(await page.evaluate(()=>Boolean(localStorage.getItem('nwo_token')))).toBe(true)
 await expect(note).toHaveValue('Synthetic inspection draft must survive optional context failure')
 const status=await page.evaluate(async id=>{const r=await fetch('/api/field/work-orders/'+id,{headers:{Authorization:'Bearer '+localStorage.getItem('nwo_token')}});return r.status},f.context_work_id);expect(status).toBe(200)
})
