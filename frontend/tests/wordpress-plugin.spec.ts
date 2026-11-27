import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { writeFileSync, readFileSync } from 'node:fs';
import path from 'node:path';

test.skip(!process.env.WORDPRESS_ACCEPTANCE, 'Dedicated disposable WordPress acceptance job only');
const evidence = process.env.NOC128_EVIDENCE || '/not-configured';
const origin = 'http://127.0.0.1:18378';
const control = (action: string, extra: string[] = []) => execFileSync('docker', ['exec', '-e', `NOC128_ACTION=${action}`, ...extra, 'noc128-wordpress', 'php', '/tests/control.php'], {encoding:'utf8'});
const mode = (value: string) => { writeFileSync(path.join(evidence, 'fixtures/mode'), value); control('clear'); };

test('installable ZIP, approved public display, failure isolation and safe removal', async ({ page, request }) => {
  await page.goto(origin + '/wp-login.php');
  await page.locator('#user_login').fill('noc128-admin');
  await page.locator('#user_pass').fill(process.env.NOC128_PASSWORD!);
  await page.locator('#wp-submit').click();
  await expect(page).toHaveURL(/wp-admin/);
  await page.goto(origin + '/wp-admin/plugin-install.php?tab=upload');
  await page.locator('#pluginzip').setInputFiles(path.join(evidence, 'plugin/nwo-network-status.zip'));
  await page.locator('#install-plugin-submit').click();
  await page.getByRole('link', {name:'Activate Plugin'}).click();
  await page.goto(origin + '/wp-admin/options-general.php?page=nwo-network-status');
  await expect(page.getByRole('heading', {name:'NWO Network Status'})).toBeVisible();
  const denied = await request.post(origin + '/wp-admin/admin-post.php', {form:{action:'nwo_status_save'},maxRedirects:0});
  expect([302,400,403]).toContain(denied.status());
  control('configure');
  mode('healthy');
  const security = JSON.parse(control('contracts'));
  expect(security.security_contract_assertions).toBe(19);
  await page.goto(origin + '/?pagename=synthetic-network-status');
  // wp_insert_post derives this slug from the title.
  await expect(page.locator('.nwo-status-summary')).toContainText('All systems operational');
  expect((await request.get(origin + '/?pagename=synthetic-network-status')).status()).toBe(200);
  await page.setViewportSize({width:375,height:812});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  mode('outage');await page.reload();
  await expect(page.getByText('Synthetic approved outage',{exact:true})).toBeVisible();
  await expect(page.locator('.nwo-status-summary')).toContainText('Major service interruption');
  mode('healthy');await page.reload();
  await expect(page.locator('.nwo-status-summary')).toContainText('All systems operational');
  await expect(page.getByText('Synthetic approved outage',{exact:true})).toHaveCount(0);
  for (const failure of ['down','redirect','private']) {
    mode(failure);await page.reload();
    await expect(page.locator('.nwo-status-summary')).toContainText('Status unknown');
    await expect(page.getByText('Synthetic approved outage',{exact:true})).toHaveCount(0);
  }
  mode('stale');await page.reload();
  await expect(page.getByText('Stale information — current service status is unknown.')).toBeVisible();
  mode('healthy');control('block');
  await page.goto(origin + '/?pagename=status-block');
  await expect(page.locator('.nwo-network-status[data-view=banner]')).toBeVisible();
  await expect(page.locator('.nwo-status-summary')).toContainText('All systems operational');
  const info=JSON.parse(control('version'));
  control('theme',['-e','NOC128_THEME=noc128classic']);
  await page.goto(origin+'/?pagename=synthetic-network-status');
  await expect(page.locator('.nwo-status-summary')).toContainText('All systems operational');
  control('theme',['-e','NOC128_THEME='+info.theme]);
  writeFileSync(path.join(evidence,`wordpress-${info.wordpress}.json`),JSON.stringify({runtime:info,security,assertions:['ZIP admin installation and activation','shortcode','dynamic Gutenberg block','mobile viewport','operational','major interruption','withdrawal replacement','gateway failure','redirect rejected','private key rejection','stale unknown','invalid nonce','URL and IP guards','corrupt cache recovery']},null,2));
  await page.goto(origin + '/wp-admin/plugins.php');
  await page.locator('[data-slug=nwo-network-status] a').filter({hasText:'Deactivate'}).click();
  page.on('dialog', dialog => dialog.accept());
  await page.locator('[data-slug=nwo-network-status] a').filter({hasText:'Delete'}).click();
  await expect(page.locator('[data-slug=nwo-network-status]')).toHaveCount(0);
  const feed=await request.get(origin+'/wp-json/nwo-status/v1/feed');
  expect(feed.status()).toBe(404);
});
