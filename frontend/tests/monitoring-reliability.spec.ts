import {test,expect} from '@playwright/test'
import fs from 'node:fs'
test.skip(process.env.NOC92_MONITORING_BROWSER!=='true','Requires isolated synthetic scheduler fixture')
test('monitoring health shows durable jobs, worker health and recovery without equipment controls',async({page})=>{
 const env=Object.fromEntries(fs.readFileSync(`${process.env.NOC_ACCEPTANCE_STATE}/acceptance.env`,'utf8').trim().split('\n').map(l=>{const i=l.indexOf('=');return [l.slice(0,i),l.slice(i+1)]}))
 const external:string[]=[]
 await page.route('**/*',async route=>{if(new URL(route.request().url()).origin!=='http://127.0.0.1:18090'){external.push(route.request().url());await route.abort()}else await route.continue()})
 await page.goto('/');await page.locator('.loginCard input').nth(0).fill('acceptance');await page.locator('input[type=password]').fill(env.ADMIN_PASSWORD);await page.getByRole('button',{name:'Sign in',exact:true}).click()
 await page.locator('nav').getByRole('button',{name:'RF Operations',exact:true}).click()
 await page.getByRole('button',{name:'monitoring health',exact:true}).click()
 const panel=page.getByRole('article',{name:'Monitoring service health'})
 await expect(panel).toContainText('Scheduler enabled');await expect(panel).toContainText('synthetic:');await expect(panel).toContainText('heartbeat fresh');await expect(panel).toContainText('monitoring.redis_recovered');await expect(panel).toContainText('monitoring.database_recovered')
 await panel.getByRole('button',{name:'Refresh monitoring health',exact:true}).click();await expect(panel).toContainText('simulation-only')
 expect(external).toEqual([])
})
