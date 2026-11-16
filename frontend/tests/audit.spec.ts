import {test,expect} from '@playwright/test'

test.skip(process.env.NOC111_AUDIT_BROWSER!=='true','Isolated audit acceptance required')

test('login inputs have associated labels and password-manager semantics',async({page})=>{
  await page.goto('/')
  await expect(page.getByLabel('Username',{exact:true})).toHaveAttribute('autocomplete','username')
  await expect(page.getByLabel('Password',{exact:true})).toHaveAttribute('autocomplete','current-password')
  await expect(page.getByLabel('Password',{exact:true})).toHaveAttribute('type','password')
})

test('rate-limited login reports an accessible error and does not create a session',async({page})=>{
  await page.route('**/api/auth/login',route=>route.fulfill({status:429,json:{detail:'Login request budget exceeded; retry in one minute'},headers:{'Retry-After':'60','Cache-Control':'no-store'}}))
  await page.goto('/')
  await page.getByLabel('Username',{exact:true}).fill('synthetic-audit-user')
  await page.getByLabel('Password',{exact:true}).fill('synthetic-only-password')
  await page.getByRole('button',{name:'Sign in',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('retry in one minute')
  expect(await page.evaluate(()=>localStorage.getItem('nwo_token'))).toBeNull()
  await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeVisible()
})
