import {test,expect} from '@playwright/test'
test.skip(process.env.NOC101_FIELD_BROWSER!=='true','Requires isolated field browser acceptance')

test('failed deferred tower documents preserve the primary field inspection and draft',async({page})=>{
 await page.addInitScript(()=>localStorage.setItem('nwo_token','synthetic-field-module-token'))
 const work={id:41,number:'SYN-WO-41',description:'Synthetic module isolation',priority:'normal',status:'in_progress',revision:2,site_id:1,technician_id:9876,required_equipment:[],inspections:[],attachments:[],events:[],next_offset:null,template:{id:3,name:'Synthetic inspection',version:1,items:[]}}
 let release!:()=>void,arrived!:()=>void
 const gate=new Promise<void>(resolve=>{release=resolve}),seen=new Promise<void>(resolve=>{arrived=resolve})
 await page.route(/MaintenanceOps[^/]*\.(?:js|tsx)(?:\?.*)?$/,async route=>{arrived();await gate;await route.abort('failed')})
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname
  let value:any={items:[],next_offset:null}
  if(path==='/api/field/me')value={id:9876,username:'synthetic-field-technician',role:'technician'}
  if(path==='/api/field/work-orders')value={items:[work],next_offset:null}
  if(path==='/api/field/work-orders/41')value=work
  if(path==='/api/field/templates')value={items:[],default_items:[]}
  if(path==='/api/field/towers/1')value={id:1,name:'Synthetic tower',monitoring:{state:'unknown',fresh:false},assets:[],alarms:[],work_orders:[],next_offset:null}
  if(path==='/api/field/work-orders/41/resources')value={spares:[],removed_locations:[]}
  if(path==='/api/applications/work-context/41')return route.fulfill({status:404,json:{detail:'No application'}})
  return route.fulfill({status:200,json:value})
 })
 try{
  await page.goto('/?field=home&work=41');await seen
  const note=page.getByLabel('Inspection work notes',{exact:true})
  await note.fill('Synthetic private draft survives a failed optional document module')
  const reason=page.getByLabel('Field change reason',{exact:true});await reason.fill('Synthetic operator reviewed documentation only')
  release()
  await expect(page.getByRole('status').filter({hasText:'Tower documents unavailable'})).toBeVisible()
  await expect(note).toHaveValue('Synthetic private draft survives a failed optional document module')
  await expect(reason).toHaveValue('Synthetic operator reviewed documentation only')
  expect(await page.evaluate(()=>Boolean(localStorage.getItem('nwo_token')))).toBe(true)
  const saved=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('noc101-draft:9876:41')||'{}'))
  expect(saved.notes).toContain('Synthetic private draft survives')
 }finally{release()}
})
