import {test,expect} from '@playwright/test'
import fs from 'node:fs'
test.skip(process.env.NOC87_RF_BROWSER!=='true','Requires explicit offline Ubiquiti/Cambium fixture')
test('radio history displays degradation, original units and unavailable capabilities',async({page})=>{
 const state=process.env.NOC_ACCEPTANCE_STATE!
 const env=Object.fromEntries(fs.readFileSync(`${state}/acceptance.env`,'utf8').trim().split('\n').map(line=>{const i=line.indexOf('=');return [line.slice(0,i),line.slice(i+1)]}))
 const ids=JSON.parse(fs.readFileSync(`${state}/rf-radio-devices.json`,'utf8')).device_ids
 const external:string[]=[]
 await page.route('**/*',async route=>{if(new URL(route.request().url()).origin!=='http://127.0.0.1:18090'){external.push(route.request().url());await route.abort()}else await route.continue()})
 await page.goto('/')
 await page.locator('.loginCard input').nth(0).fill('acceptance')
 await page.locator('input[type=password]').fill(env.ADMIN_PASSWORD)
 await page.getByRole('button',{name:'Sign in',exact:true}).click()
 await page.locator('nav').getByRole('button',{name:'RF Diagnostics',exact:true}).click()
 const panel=page.getByRole('region',{name:'Ubiquiti and Cambium radio telemetry'})
 for(const [vendor,id] of Object.entries(ids)){
  await page.getByLabel('Device',{exact:true}).selectOption(String(id))
  await expect(panel).toContainText(`Source: ${vendor}`)
  await expect(panel).toContainText('Radio health: degraded')
  await expect(panel).toContainText('low_snr')
  await expect(panel).toContainText('increasing_noise')
  await expect(panel).toContainText('Last successful telemetry collection:')
  await expect(panel).toContainText('Not available (unsupported)')
  await expect(panel.getByRole('img',{name:'Noise floor (dBm) history',exact:true})).toBeVisible()
  await panel.locator('summary').click()
  await expect(panel.locator('pre')).toContainText('5800000000')
  await expect(panel.locator('pre')).toContainText('"hz"')
 }
 expect(external).toEqual([])
})
