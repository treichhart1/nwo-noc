import {test,expect} from '@playwright/test'
import fs from 'node:fs'
test.skip(process.env.NOC85_WISP_BROWSER!=='true','Requires explicit isolated 400-device WISP fixture')
test('operator sees correlated rural WISP outage and recovery context',async({page})=>{
 const state=process.env.NOC_ACCEPTANCE_STATE!
 const env=Object.fromEntries(fs.readFileSync(`${state}/acceptance.env`,'utf8').trim().split('\n').map(line=>{const i=line.indexOf('=');return [line.slice(0,i),line.slice(i+1)]}))
 const external:string[]=[]
 await page.route('**/*',async route=>{if(new URL(route.request().url()).origin!=='http://127.0.0.1:18090'){external.push(route.request().url());await route.abort()}else await route.continue()})
 await page.goto('/')
 await page.locator('.loginCard input').nth(0).fill('acceptance')
 await page.locator('input[type=password]').fill(env.ADMIN_PASSWORD)
 await page.getByRole('button',{name:'Sign in',exact:true}).click()
 const region=page.getByRole('region',{name:'Operational readiness'})
 await expect(region).toContainText('389/400 monitored devices confirmed up')
 await expect(region).toContainText('Tower/POP outages: 1')
 await expect(region).toContainText('Root outages')
 await expect(region).toContainText('Synthetic Tower 01')
 await expect(region).toContainText('critical: MikroTik tower-router 0000')
 await page.locator('nav').getByRole('button',{name:'Incidents',exact:true}).click()
 await expect(page.getByRole('heading',{name:/SIMULATION:/})).toHaveCount(3)
 expect(external).toEqual([])
})
