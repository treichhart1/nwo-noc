import {test,expect} from '@playwright/test'
import fs from 'node:fs'
test.skip(process.env.NOC98_TOWER_BROWSER!=='true','Requires isolated synthetic tower fixture')
test('tower health distinguishes capacity, missing power and technician-only draft',async({page})=>{
 const state=process.env.NOC_ACCEPTANCE_STATE!;const env=Object.fromEntries(fs.readFileSync(`${state}/acceptance.env`,'utf8').trim().split('\n').map(l=>{const i=l.indexOf('=');return[l.slice(0,i),l.slice(i+1)]}));const f=JSON.parse(fs.readFileSync(`${state}/tower-health-fixture.json`,'utf8'));const external:string[]=[]
 await page.route('**/*',async r=>{if(new URL(r.request().url()).origin!=='http://127.0.0.1:18090'){external.push(r.request().url());await r.abort()}else await r.continue()})
 await page.goto('/');await page.locator('.loginCard input').nth(0).fill('acceptance');await page.locator('input[type=password]').fill(env.ADMIN_PASSWORD);await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.locator('nav').getByRole('button',{name:'Tower Health',exact:true}).click()
 const health=page.getByRole('region',{name:'Tower health and capacity',exact:true});await expect(health).toContainText('Unknown data is not healthy')
 await page.getByRole('button',{name:new RegExp(`^Inspect tower #${f.site_id}:`)}).click();const detail=page.getByRole('article',{name:'Tower investigation',exact:true});await expect(detail).toContainText('Provisioned plans are not measured demand');await expect(detail).toContainText('AC: Unavailable');await expect(detail).toContainText('Last successful telemetry collection:');await expect(detail).toContainText('Interface traffic alone is not RF capacity')
 await page.getByRole('button',{name:'Load capacity history',exact:true}).click();await expect(page.getByRole('region',{name:'Historical capacity analysis',exact:true})).toContainText('No guaranteed demand or future failure')
 await page.getByRole('button',{name:`Inspect maintenance #${f.finding_id}`,exact:true}).click();const record=page.getByRole('article',{name:'Maintenance record',exact:true});await page.getByLabel('Technician investigation note',{exact:true}).fill('Synthetic technician reviewing measured tower evidence');await page.getByRole('button',{name:'Acknowledge finding',exact:true}).click();await expect(record).toContainText('acknowledged')
 await page.getByRole('button',{name:'Prepare technician notification preview',exact:true}).click();await expect(health).toContainText('No customer outage is asserted.');await expect(record).not.toContainText('+1555');expect(external).toEqual([])
})
