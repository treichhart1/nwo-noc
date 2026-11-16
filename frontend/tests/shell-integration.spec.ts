import { test, expect } from '@playwright/test'
import fs from 'node:fs'

test.skip(process.env.NOC110_INTEGRATION_BROWSER !== 'true', 'Isolated integration acceptance required')

async function signIn(page: any) {
  const state = process.env.NOC_ACCEPTANCE_STATE!
  const env = Object.fromEntries(fs.readFileSync(state + '/acceptance.env', 'utf8').trim().split('\n').map(l => {
    const i = l.indexOf('='); return [l.slice(0, i), l.slice(i + 1)]
  }))
  await page.goto('/')
  await page.locator('.loginCard input').nth(0).fill(process.env.NOC110_TEST_USERNAME || 'acceptance')
  await page.locator('input[type=password]').fill(env.ADMIN_PASSWORD)
  await page.getByRole('button', {name: 'Sign in', exact: true}).click()
}

test('initial API failure is actionable and retry restores the shared shell', async ({page}) => {
  await page.route('**/api/dashboard', route => route.fulfill({status: 503, json: {detail: 'Synthetic database interruption'}}))
  await signIn(page)
  await expect(page.getByRole('alert')).toContainText('Synthetic database interruption')
  await page.unroute('**/api/dashboard')
  await page.getByRole('button', {name: 'Retry dashboard', exact: true}).click()
  await expect(page.locator('h1')).toHaveText('Dashboard')
  const labels = await page.locator('nav button').allTextContents()
  expect(new Set(labels).size).toBe(labels.length)
  await expect(page.locator('nav').getByRole('button', {name: 'Network ROI', exact: true})).toBeVisible()
  await expect(page.getByRole('button', {name: 'Sync UISP', exact: true})).toBeVisible()
})

test('operator shell hides administrator-only actions without changing API permissions', async ({page}) => {
  // UI contract only. Real server-role denial remains covered by backend security suites.
  await page.route('**/api/auth/me', async route => {
    const response = await route.fetch()
    await route.fulfill({response, json: {...await response.json(), role: 'operator'}})
  })
  await signIn(page)
  await expect(page.locator('h1')).toHaveText('Dashboard')
  for (const name of ['Network ROI', 'Settings'])
    await expect(page.locator('nav').getByRole('button', {name, exact: true})).toHaveCount(0)
  await expect(page.getByRole('button', {name: 'Sync UISP', exact: true})).toHaveCount(0)
  await expect(page.locator('nav').getByRole('button', {name: 'Customer Communications', exact: true})).toBeVisible()
})
