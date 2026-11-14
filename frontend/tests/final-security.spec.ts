import {test,expect} from '@playwright/test'
import fs from 'node:fs'
test.skip(process.env.NOC94_SECURITY_BROWSER!=='true','Requires final isolated security acceptance')
test('administrator can persistently stop NOC collection without equipment actions',async({page})=>{
 const env=Object.fromEntries(fs.readFileSync(`${process.env.NOC_ACCEPTANCE_STATE}/acceptance.env`,'utf8').trim().split('\n').map(l=>{const i=l.indexOf('=');return [l.slice(0,i),l.slice(i+1)]}))
 const external:string[]=[];await page.route('**/*',async route=>{if(new URL(route.request().url()).origin!=='http://127.0.0.1:18090'){external.push(route.request().url());await route.abort()}else await route.continue()})
 await page.goto('/');await page.locator('.loginCard input').nth(0).fill('acceptance');await page.locator('input[type=password]').fill(env.ADMIN_PASSWORD);await page.getByRole('button',{name:'Sign in',exact:true}).click()
 await page.locator('nav').getByRole('button',{name:'RF Operations',exact:true}).click();await page.getByRole('button',{name:'monitoring health',exact:true}).click()
 const panel=page.getByRole('article',{name:'Monitoring service health'})
 await panel.getByRole('button',{name:'Stop NOC collection',exact:true}).click()
 await expect(panel).toContainText('ACTIVE — new collection blocked')
 await expect(panel.getByRole('button',{name:'Stop NOC collection',exact:true})).toBeDisabled()
 await page.reload();await page.locator('nav').getByRole('button',{name:'RF Operations',exact:true}).click();await page.getByRole('button',{name:'monitoring health',exact:true}).click();await expect(panel).toContainText('ACTIVE — new collection blocked')
 expect(external).toEqual([])
})
