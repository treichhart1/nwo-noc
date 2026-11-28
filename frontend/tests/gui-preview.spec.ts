import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import {capture,viewports} from './gui-capture';
test.skip(process.env.NOC_GUI_PREVIEW!=='true','Explicit disposable synthetic GUI acceptance only');
test('actual seeded WISP application desktop and mobile visual evidence',async({browser})=>{
 test.setTimeout(420000);
 const state=process.env.NOC_ACCEPTANCE_STATE!;
 const env=Object.fromEntries(fs.readFileSync(path.join(state,'acceptance.env'),'utf8').trim().split('\n').map(l=>{const i=l.indexOf('=');return[l.slice(0,i),l.slice(i+1)]}));
 const fixture=(name:string)=>JSON.parse(fs.readFileSync(path.join(state,name+'-fixture.json'),'utf8'));
 const microwave=JSON.parse(fs.readFileSync(path.join(state,'microwave-devices.json'),'utf8')).device_ids;
 const gui=fixture('gui');
 for(const viewport of viewports){
  const context=await browser.newContext({viewport:{width:viewport.width,height:viewport.height},deviceScaleFactor:1});
  const page=await context.newPage();const errors:string[]=[];const external:string[]=[];const apiFailures:{path:string,status:number}[]=[];
  page.on('pageerror',e=>errors.push(e.name+': '+e.message.slice(0,200)));
  page.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=400)apiFailures.push({path:new URL(r.url()).pathname,status:r.status()});});
  await page.route('**/*',async r=>{if(new URL(r.request().url()).origin!=='http://127.0.0.1:18090'){external.push(new URL(r.request().url()).origin);await r.abort();}else await r.continue();});
  async function login(username:string){await page.goto('/');await expect(page.locator('.loginCard')).toBeVisible();await page.locator('#noc-username').fill(username);await page.locator('#noc-password').fill(env.ADMIN_PASSWORD);await page.getByRole('button',{name:'Sign in',exact:true}).click();await expect(page.locator('h1')).toContainText(/Dashboard|Tower field management/);}
  await page.goto('/');await expect(page.locator('.loginCard')).toBeVisible();await capture(page,1,'Administrator login',viewport);
  await login('acceptance');
  async function nav(label:string){await page.goto('/');await expect(page.locator('nav')).toBeAttached();if(viewport.name==='mobile')await page.locator('button.menu').click();await page.locator('nav').getByRole('button',{name:label,exact:true}).click();await expect(page.locator('h1')).toContainText(label);await page.waitForTimeout(600);}
  async function direct(query:string,heading:string){await page.goto('/?'+query+'=home');await expect(page.getByRole('heading',{name:new RegExp(heading,'i')}).first()).toBeVisible();await page.waitForTimeout(700);}
  async function shot(id:number,title:string,focus?:string){await capture(page,id,title,viewport,focus);}
  await shot(2,'Main NOC dashboard');
  await nav('Topology');await shot(3,'Network topology');
  await nav('Network Map');await shot(4,'Tower location map (offline basemap unavailable; real markers retained)');
  await nav('Tower Health');await expect(page.getByRole('region',{name:'Tower health and capacity'})).toBeVisible();await shot(5,'Tower health dashboard');
  await nav('Devices');await shot(6,'Device management');
  await nav('RF Diagnostics');await page.getByLabel('Device',{exact:true}).selectOption(String(Object.values(microwave)[0]));await expect(page.getByRole('region',{name:'Microwave backhaul telemetry'})).toContainText('Source:');await shot(7,'Wireless RF diagnostics');
  await shot(8,'Microwave backhaul monitoring','[aria-label="Microwave backhaul telemetry"]');
  await nav('Down Detector');await shot(9,'Down Detector');
  await nav('AI IPAM');await shot(10,'AI IPAM');
  await nav('Equipment Inventory');await shot(11,'Equipment inventory');
  await page.evaluate(()=>localStorage.removeItem('nwo_token'));await login('field-acceptance');await shot(12,'Technician dashboard (technician role)');
  await page.evaluate(()=>localStorage.removeItem('nwo_token'));await login('acceptance');
  await direct('maintenance','Maintenance');await shot(13,'Maintenance management');
  await direct('unified','Unified wireless');await shot(14,'Fiber infrastructure');
  await direct('planning','RF');await shot(15,'LiDAR and wireless coverage');
  await direct('network-roi','Network profitability');await page.getByRole('combobox',{name:'Financial scope',exact:true}).selectOption(String(fixture('finance').tower_scope_id));await page.waitForTimeout(700);await shot(16,'Tower profitability');
  await nav('Customer Impact');await shot(17,'Customer management and impact');
  await direct('applications','Customer');await shot(18,'Customer signup dashboard');
  await nav('Customer Impact');await shot(19,'Customer cancellation dashboard','[aria-label="Customer cancellation and retention"]');
  await page.getByText('Policy, competitor directory and historical data',{exact:true}).click();await shot(20,'Competitor intelligence — reported offers, no invented current prices','[aria-label="Customer cancellation and retention"] details[open]');
  await nav('Notifications');await expect(page.getByRole('region',{name:'Unified customer communications',exact:true})).toContainText('Email host transport:');await shot(21,'Unified communications','[aria-label="Unified customer communications"]');
  await shot(22,'Twilio and Telnyx configuration — disabled','[aria-label="Native SMS provider administration"]');
  await shot(23,'Slack configuration — disabled','[aria-label="Native Slack administration"]');
  await page.getByText('Email provider settings',{exact:true}).click();await page.getByLabel('Email provider',{exact:true}).selectOption('sendgrid');await shot(24,'SendGrid configuration — disabled','[aria-label="Unified customer communications"] details[open]');
  await page.getByLabel('Email provider',{exact:true}).selectOption('smtp');await shot(31,'SMTP configuration — disabled','[aria-label="Unified customer communications"] details[open]');
  await direct('unified','Unified wireless');await expect(page.getByRole('region',{name:'Private cellular infrastructure map'})).toContainText('Synthetic');await shot(25,'Cellular tower map — synthetic estimated observations','[aria-label="Private cellular infrastructure map"]');
  await nav('RF Diagnostics');await page.getByLabel('Device',{exact:true}).selectOption(String(gui.cellular_device_id));await expect(page.getByRole('region',{name:'Advanced MikroTik and cellular evidence'})).toContainText('recorded identity changes');await shot(26,'MikroTik hardware monitoring — synthetic evidence, native LTE blocked','[aria-label="Advanced MikroTik and cellular evidence"]');
  await nav('Settings');await shot(30,'System settings and integration readiness');
  fs.writeFileSync(path.join(process.env.NOC_GUI_OUTPUT!,viewport.name+'-runtime.json'),JSON.stringify({page_errors:errors,api_failures:apiFailures,blocked_external_origins:[...new Set(external)],synthetic:true},null,2));
  expect(errors,'Uncaught application errors in actual rendered GUI').toEqual([]);await context.close();
 }
});
