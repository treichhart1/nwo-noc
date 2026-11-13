import {test,expect} from '@playwright/test'
import fs from 'node:fs'
test.skip(process.env.NOC86_RF_BROWSER!=='true','Requires the explicit offline four-vendor RF fixture')
test('four vendor RF observations show normalized units and provenance',async({page})=>{
 const state=process.env.NOC_ACCEPTANCE_STATE!
 const env=Object.fromEntries(fs.readFileSync(`${state}/acceptance.env`,'utf8').trim().split('\n').map(line=>{const i=line.indexOf('=');return [line.slice(0,i),line.slice(i+1)]}))
 const ids=JSON.parse(fs.readFileSync(`${state}/rf-vendor-devices.json`,'utf8')).device_ids
 const external:string[]=[]
 await page.route('**/*',async route=>{if(new URL(route.request().url()).origin!=='http://127.0.0.1:18090'){external.push(route.request().url());await route.abort()}else await route.continue()})
 await page.goto('/')
 await page.locator('.loginCard input').nth(0).fill('acceptance')
 await page.locator('input[type=password]').fill(env.ADMIN_PASSWORD)
 await page.getByRole('button',{name:'Sign in',exact:true}).click()
 await page.locator('nav').getByRole('button',{name:'RF Diagnostics',exact:true}).click()
 const foundation=page.getByRole('region',{name:'RF monitoring foundation'})
 for(const [vendor,id] of Object.entries(ids)){
  await page.getByLabel('Device',{exact:true}).selectOption(String(id))
  await expect(foundation).toContainText(`Source: ${vendor}`)
  await expect(foundation).toContainText('synthetic_export')
  await expect(foundation.locator('article').filter({hasText:'Noise floor (dBm)'})).toContainText('-95')
  await expect(foundation.locator('article').filter({hasText:'Frequency (MHz)'})).toContainText('5800')
  await expect(foundation.locator('article').filter({hasText:'Link capacity (Mbps)'})).toContainText('500')
  await expect(foundation).toContainText('Last successful ingest:')
 }
 expect(external).toEqual([])
})
