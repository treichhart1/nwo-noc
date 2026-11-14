import {test,expect} from '@playwright/test'
import fs from 'node:fs'
test.skip(process.env.NOC97_DOWN_DETECTOR_BROWSER!=='true','Requires isolated synthetic detector fixture')
test('operator investigates correlated outage and prepares only a draft notification',async({page})=>{
 const state=process.env.NOC_ACCEPTANCE_STATE!;const env=Object.fromEntries(fs.readFileSync(`${state}/acceptance.env`,'utf8').trim().split('\n').map(l=>{const i=l.indexOf('=');return[l.slice(0,i),l.slice(i+1)]}));const f=JSON.parse(fs.readFileSync(`${state}/down-detector-fixture.json`,'utf8'));const external:string[]=[]
 await page.route('**/*',async r=>{if(new URL(r.request().url()).origin!=='http://127.0.0.1:18090'){external.push(r.request().url());await r.abort()}else await r.continue()})
 await page.goto('/');await page.locator('.loginCard input').nth(0).fill('acceptance');await page.locator('input[type=password]').fill(env.ADMIN_PASSWORD);await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.locator('nav').getByRole('button',{name:'Down Detector',exact:true}).click()
 const detector=page.getByRole('region',{name:'Down Detector',exact:true});await expect(detector).toContainText('Probable causes require investigation')
 await page.getByLabel('link_id',{exact:true}).fill(String(f.link_id));await page.getByRole('button',{name:'Apply outage filters',exact:true}).click();await expect(detector).toContainText('1 episodes')
 await page.getByRole('button',{name:`Inspect outage #${f.episode_id}`,exact:true}).click();const detail=page.getByRole('article',{name:'Outage investigation',exact:true});await expect(detail).toContainText('backhaul_outage');await expect(detail).toContainText('Potential customers:');await expect(detail).not.toContainText('+1555')
 await page.getByRole('button',{name:'Prepare outage preview',exact:true}).click();await expect(detector).toContainText('Review and explicitly approve simulation in Notifications')
 await page.getByLabel('Operator explanation',{exact:true}).fill('Synthetic technician reviewing recorded backhaul evidence');await page.getByRole('button',{name:'acknowledge outage assessment',exact:true}).click();await expect(detail).toContainText('acknowledge')
 await page.getByRole('button',{name:`Open shared incident #${f.incident_id}`,exact:true}).click();await expect(page.getByRole('article',{name:'Technician investigation',exact:true})).toContainText('Down Detector')
 expect(external).toEqual([])
})
