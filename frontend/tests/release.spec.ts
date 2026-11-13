import {test, expect} from '@playwright/test'
import fs from 'node:fs'
const state = process.env.NOC_ACCEPTANCE_STATE
if (!state) throw new Error('NOC_ACCEPTANCE_STATE must identify the generated isolated stack')
const env = Object.fromEntries(fs.readFileSync(`${state}/acceptance.env`,'utf8').trim().split('\n').map(line=>{const i=line.indexOf('=');return [line.slice(0,i),line.slice(i+1)]}))
test.beforeEach(async ({page})=>{
  const external:string[]=[]
  await page.route('**/*', async route=>{
    if (new URL(route.request().url()).origin !== 'http://127.0.0.1:18090') {
      external.push(route.request().url()); await route.abort()
    } else await route.continue()
  })
  page.on('close',()=>expect(external).toEqual([]))
  await page.goto('/')
  await page.locator('.loginCard input').nth(0).fill('acceptance')
  await page.locator('input[type=password]').fill(env.ADMIN_PASSWORD)
  await page.getByRole('button',{name:'Sign in',exact:true}).click()
  await expect(page.locator('h1')).toHaveText('Dashboard')
})
for (const title of ['Dashboard','Towers & POPs','Devices','Topology','Operations','Alert Center','Incidents','Network Map']) {
  test(`renders ${title}`,async({page})=>{
    await page.locator('nav').getByRole('button',{name:new RegExp('^'+title)}).click()
    await expect(page.locator('h1')).toHaveText(title)
    await expect(page.locator('.error')).toHaveCount(0)
    if(title==='Devices') await expect(page.getByText('NOC84 Synthetic Radio',{exact:true})).toBeVisible()
    if(title==='Dashboard') {
      await expect(page.getByRole('heading',{name:'Monitoring attention'})).toBeVisible()
      await expect(page.getByRole('region',{name:'Operational readiness'})).toContainText('Last successful check:')
    }
  })
}
test('RF diagnostics displays synthetic telemetry',async({page})=>{
  await page.locator('nav').getByRole('button',{name:'RF Diagnostics',exact:true}).click()
  await page.getByLabel('Device',{exact:true}).selectOption({label:'NOC84 Synthetic Radio'})
  await expect(page.getByText('3 RF samples in the selected window')).toBeVisible()
  await expect(page.locator('.error')).toHaveCount(0)
})
test('logout clears session',async({page})=>{
  await page.getByRole('button',{name:'Log out'}).click()
  await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeVisible()
  expect(await page.evaluate(()=>localStorage.getItem('nwo_token'))).toBeNull()
})
