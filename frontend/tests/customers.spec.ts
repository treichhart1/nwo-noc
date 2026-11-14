import {test,expect} from '@playwright/test'
import fs from 'node:fs'
test.skip(process.env.NOC95_CUSTOMER_BROWSER!=='true','Requires an explicitly seeded offline synthetic customer dataset')
test('operator reviews private customer mappings, consent and maintenance or incident impact without sending',async({page,request})=>{
 test.setTimeout(120000)
 const state=process.env.NOC_ACCEPTANCE_STATE!
 const env=Object.fromEntries(fs.readFileSync(`${state}/acceptance.env`,'utf8').trim().split('\n').map(l=>{const i=l.indexOf('=');return [l.slice(0,i),l.slice(i+1)]}))
 const fixture=JSON.parse(fs.readFileSync(`${state}/customer-fixture.json`,'utf8'))
 const external:string[]=[]
 await page.route('**/*',async r=>{if(new URL(r.request().url()).origin!=='http://127.0.0.1:18090'){external.push(r.request().url());await r.abort()}else await r.continue()})
 await page.goto('/');await page.locator('.loginCard input').nth(0).fill('acceptance');await page.locator('input[type=password]').fill(env.ADMIN_PASSWORD);await page.getByRole('button',{name:'Sign in',exact:true}).click()
 await page.locator('nav').getByRole('button',{name:'Customer Impact',exact:true}).click()
 const region=page.getByRole('region',{name:'Customer impact',exact:true})
 await expect(region).toContainText('Synchronized customers');await expect(region).toContainText('200')
 await expect(region).toContainText('Live disabled')
 const search=page.getByLabel('Customer, account, service, tower or access point',{exact:true})
 await search.fill('Synthetic Powercode Customer 0001');await page.getByRole('button',{name:'Search customers',exact:true}).click()
 await expect(region.getByRole('row').filter({hasText:'NOC95-C0001'})).toContainText('***0001')
 await expect(region).not.toContainText('+15555550001')
 await region.getByRole('row').filter({hasText:'NOC95-C0001'}).getByRole('button',{name:/Review service/}).click()
 await page.getByLabel('Mapping evidence note',{exact:true}).fill('Synthetic mapping checked for browser acceptance')
 await page.getByRole('button',{name:'Save reviewed mapping',exact:true}).click();await expect(region).toContainText('NOC customer record saved')
 await region.getByRole('row').filter({hasText:'NOC95-C0001'}).getByRole('button',{name:/Review service/}).click()
 await page.getByLabel('SMS consent',{exact:true}).selectOption('granted');await page.getByLabel('Communication preference',{exact:true}).selectOption('sms');await page.getByLabel('Consent evidence reference (required for granted)',{exact:true}).fill('SYN-NOC95-BROWSER-CONSENT')
 await page.getByRole('button',{name:'Save communication preferences',exact:true}).click();await expect(region).toContainText('NOC customer record saved')
 await page.getByLabel('Impact target type',{exact:true}).selectOption('maintenance_id');await page.getByLabel('Impact target',{exact:true}).selectOption(String(fixture.maintenance_id));await page.getByRole('button',{name:'Preview customer impact',exact:true}).click()
 const preview=page.getByRole('region',{name:'Customer impact preview',exact:true});await expect(preview).toContainText('confirmed');await expect(preview).toContainText('SMS-eligible preview accounts on this page: 1');await expect(preview).toContainText('no notifications sent')
 await page.getByLabel('Impact target type',{exact:true}).selectOption('incident_id');await page.getByLabel('Impact target',{exact:true}).selectOption(String(fixture.incident_id));await page.getByRole('button',{name:'Preview customer impact',exact:true}).click();await page.getByRole('button',{name:'Save incident customer evidence',exact:true}).click();await expect(region).toContainText('Incident-to-service evidence saved')
 const viewer=await request.post('/api/auth/login',{form:{username:'noc95-viewer',password:'synthetic-customer-viewer-only'}});expect(viewer.ok()).toBeTruthy();const token=(await viewer.json()).access_token
 const denied=await request.get('/api/customers',{headers:{Authorization:`Bearer ${token}`}});expect(denied.status()).toBe(403);expect(await denied.text()).not.toContain('Synthetic Powercode Customer')
 expect(external).toEqual([])
})
