import {test,expect} from '@playwright/test'
import fs from 'node:fs'
test.skip(process.env.NOC88_MICROWAVE_BROWSER!=='true','Requires explicit offline microwave fixture')
test('dual and multi-carrier microwave history separates RF, Ethernet and collection',async({page})=>{
 const state=process.env.NOC_ACCEPTANCE_STATE!
 const env=Object.fromEntries(fs.readFileSync(`${state}/acceptance.env`,'utf8').trim().split('\n').map(line=>{const i=line.indexOf('=');return [line.slice(0,i),line.slice(i+1)]}))
 const ids=JSON.parse(fs.readFileSync(`${state}/microwave-devices.json`,'utf8')).device_ids
 const external:string[]=[]
 await page.route('**/*',async route=>{if(new URL(route.request().url()).origin!=='http://127.0.0.1:18090'){external.push(route.request().url());await route.abort()}else await route.continue()})
 await page.goto('/')
 await page.locator('.loginCard input').nth(0).fill('acceptance')
 await page.locator('input[type=password]').fill(env.ADMIN_PASSWORD)
 await page.getByRole('button',{name:'Sign in',exact:true}).click()
 await page.locator('nav').getByRole('button',{name:'RF Diagnostics',exact:true}).click()
 const panel=page.getByRole('region',{name:'Microwave backhaul telemetry'})
 for(const [vendor,id] of Object.entries(ids)){
  await page.getByLabel('Device',{exact:true}).selectOption(String(id))
  await expect(panel).toContainText(`Source: ${vendor}`)
  await expect(panel).toContainText('Microwave health: degraded')
  await expect(panel).toContainText('RF: degraded · Ethernet: up · Telemetry: fresh')
  await expect(panel).toContainText('Last successful microwave collection:')
  await expect(panel.getByRole('region',{name:/Microwave carrier c/})).toHaveCount(vendor==='racom'?2:4)
  const first=panel.getByRole('region',{name:'Microwave carrier c1',exact:true})
  await expect(first).toContainText('Carrier c1 · down')
  await expect(first).toContainText('link_loss: active')
  await expect(first).toContainText('matching engineered modulation/bandwidth reference required')
  await expect(first.getByRole('img',{name:'c1 microwave RSL history',exact:true})).toBeVisible()
  await expect(first.locator('article').filter({hasText:'Bandwidth (MHz)'})).toContainText('80')
  await expect(first.locator('article').filter({hasText:'Remote RSL (dBm)'})).toContainText('-62')
  await panel.locator('summary').click()
  await expect(panel.locator('pre')).toContainText('80000')
  await expect(panel.locator('pre')).toContainText('"khz"')
 }
 await page.locator('nav').getByRole('button',{name:'Dashboard',exact:true}).click()
 await expect(page.getByText('Microwave: Synthetic NOC88 racom microwave',{exact:true})).toBeVisible()
 await expect(page.getByText('Microwave: Synthetic NOC88 aviat microwave',{exact:true})).toBeVisible()
 expect(external).toEqual([])
})
