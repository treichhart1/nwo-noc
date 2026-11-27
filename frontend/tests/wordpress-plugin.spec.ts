import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import path from 'node:path';

test.skip(!process.env.WORDPRESS_ACCEPTANCE, 'Dedicated disposable WordPress acceptance job only');
const evidence = process.env.NOC128_EVIDENCE || '/not-configured';
const origin = process.env.NOC128_WORDPRESS_ORIGIN || 'http://127.0.0.1:18378';
const control = (action: string, extra: string[] = []) => execFileSync('docker', ['exec', '-e', `NOC128_ACTION=${action}`, ...extra, 'noc128-wordpress', 'php', '/tests/control.php'], {encoding:'utf8'});
const mode = async (value: string) => { await new Promise(resolve => setTimeout(resolve,2100)); writeFileSync(path.join(evidence, 'fixtures/mode'), value); control('clear'); };

test('installable ZIP, approved public display, failure isolation and safe removal', async ({ page, request }) => {
  await page.goto(origin + '/wp-login.php');
  await page.locator('#user_login').fill('noc128-admin');
  await page.locator('#user_pass').fill(process.env.NOC128_PASSWORD!);
  await page.locator('#wp-submit').click();
  await expect(page).toHaveURL(/wp-admin/);
  await page.goto(origin + '/wp-admin/plugin-install.php?tab=upload');
  await page.locator('#pluginzip').setInputFiles(path.join(evidence, 'plugin/nwo-network-status.zip'));
  await page.locator('#install-plugin-submit').click();
  for (const file of ['nwo-network-status.php','validation.php','transport.php','uninstall.php']) execFileSync('docker',['exec','noc128-wordpress','php','-l','/var/www/html/wp-content/plugins/nwo-network-status/'+file],{stdio:'pipe'});
  await page.getByRole('link', {name:'Activate Plugin'}).click();
  const pure=JSON.parse(execFileSync('docker',['exec','-e','NOC128_PLUGIN_ROOT=/var/www/html/wp-content/plugins/nwo-network-status','noc128-wordpress','php','/tests/pure-contracts.php'],{encoding:'utf8'}));
  expect(pure.pure_contract_assertions).toBeGreaterThanOrEqual(40);
  const activation = JSON.parse(control('diagnose'));
  expect(activation.active, JSON.stringify(activation)+(await page.locator('body').innerText()).slice(-1800)).toBeTruthy();
  await page.goto(origin + '/wp-admin/options-general.php?page=nwo-network-status');
  await expect(page.getByRole('heading', {name:'NWO Network Status'}), (await page.locator('body').innerText()).slice(0,1800)).toBeVisible();
  const denied = await request.post(origin + '/wp-admin/admin-post.php', {form:{action:'nwo_status_save'},maxRedirects:0});
  expect([302,400,403]).toContain(denied.status());
  const csrf = await page.request.post(origin + '/wp-admin/admin-post.php', {form:{action:'nwo_status_save'},maxRedirects:0});
  expect(csrf.status()).toBe(403);
  await page.getByLabel('Public gateway HTTPS URL').fill('https://status.noc128.test/network-status-api/v2');
  await page.getByLabel('Enable public retrieval').check();
  await page.getByLabel('I approve this public gateway URL and its validated public IP addresses').check();
  await page.getByRole('button',{name:'Save reviewed settings'}).click();
  await expect(page.getByRole('heading',{name:'NWO Network Status'})).toBeVisible();
  await mode('healthy');
  const security = JSON.parse(control('contracts'));
  expect(security.security_contract_assertions).toBe(19);
  await page.goto(origin + '/?pagename=synthetic-network-status');
  // wp_insert_post derives this slug from the title.
  await expect(page.locator('.nwo-status-summary')).toContainText('All systems operational');
  expect((await request.get(origin + '/?pagename=synthetic-network-status')).status()).toBe(200);
  await page.setViewportSize({width:375,height:812});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await mode('outage');await page.reload();
  await expect(page.getByText('Synthetic approved outage',{exact:true})).toBeVisible();
  await expect(page.locator('.nwo-status-summary')).toContainText('Major service interruption');
  for (const [state, label] of [['partial','Partial service interruption'],['degraded','Degraded performance'],['maintenance','Maintenance in progress'],['scheduled','Scheduled maintenance'],['recovery','Monitoring recovery'],['resolved','Resolved']]) {
    await mode(state);await page.reload();
    await expect(page.getByText(label,{exact:true}).first()).toBeVisible();
  }
  await mode('healthy');await page.reload();
  await expect(page.locator('.nwo-status-summary')).toContainText('All systems operational');
  await expect(page.getByText('Synthetic approved outage',{exact:true})).toHaveCount(0);
  for (const failure of ['down','redirect','private']) {
    await mode(failure);await page.reload();
    await expect(page.locator('.nwo-status-summary')).toContainText('Status unknown');
    await expect(page.getByText('Last successful retrieval:',{exact:false})).toBeVisible();
    await expect(page.getByText('Synthetic approved outage',{exact:true})).toHaveCount(0);
  }
  await mode('stale');await page.reload();
  await expect(page.getByText('Stale information — current service status is unknown.')).toBeVisible();
  await mode('healthy');control('block');
  await page.goto(origin + '/?pagename=status-block');
  await expect(page.locator('.nwo-network-status[data-view=banner]')).toBeVisible();
  await expect(page.locator('.nwo-status-summary')).toContainText('All systems operational');
  const info=JSON.parse(control('version'));
  control('theme',['-e','NOC128_THEME=noc128classic']);
  await page.goto(origin+'/?pagename=synthetic-network-status');
  await expect(page.locator('.nwo-status-summary')).toContainText('All systems operational');
  control('theme',['-e','NOC128_THEME='+info.theme]);
  const publicEndpoint = await page.locator('[data-nwo-status-endpoint]').getAttribute('data-nwo-status-endpoint');
  await page.goto(origin + '/wp-admin/plugins.php');
  await page.locator('[data-slug=nwo-network-status] a').filter({hasText:'Deactivate'}).click();
  page.on('dialog', dialog => dialog.accept());
  await page.locator('[data-slug=nwo-network-status] a').filter({hasText:'Delete'}).click();
  await expect.poll(() => JSON.parse(control('removed')).installed).toBe(false);
  const removal=JSON.parse(control('removed'));
  expect(removal.remaining_options).toBe(0);
  expect(removal.pages_preserved).toBe(true);
  const feed=await request.get(publicEndpoint!);
  expect(feed.status()).toBe(404);
  writeFileSync(path.join(evidence,`wordpress-${info.wordpress}.json`),JSON.stringify({runtime:info,security,pure,removal,assertions:['ZIP admin installation and activation','shortcode','dynamic Gutenberg block','mobile viewport','operational','major interruption','withdrawal replacement','gateway failure','redirect rejected','private key rejection','stale unknown','authenticated invalid nonce','administrator settings save','partial disruption','degraded performance','maintenance','scheduled maintenance','recovery','resolved history','URL and IP guards','corrupt cache recovery','safe uninstall','settings and caches deleted','pages preserved','public route removed']},null,2));
});
