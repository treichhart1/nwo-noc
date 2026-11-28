import {expect, Page} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
export const viewports=[{name:'desktop',width:1920,height:1080},{name:'mobile',width:390,height:844}];
// Screenshots are of the unmodified, rendered product. No response fulfillment,
// CSS injection, hidden controls, fake charts or replacement DOM is permitted.
export async function capture(page:Page,id:number,title:string,viewport:typeof viewports[number],focus?:string){
 const root=process.env.NOC_GUI_OUTPUT;if(!root)throw new Error('Explicit screenshot output required');
 fs.mkdirSync(root,{recursive:true});await page.evaluate(()=>document.fonts.ready);
 await expect(page.locator('body')).not.toBeEmpty();
 if(focus){const target=page.locator(focus).first();await expect(target).toBeVisible();await target.scrollIntoViewIfNeeded();}
 else await page.evaluate(()=>scrollTo(0,0));
 await page.waitForTimeout(350); // allow the actual map/charts to finish layout
 const stem=String(id).padStart(2,'0')+'-'+viewport.name;
 const layout=await page.evaluate(()=>({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,headings:Array.from(document.querySelectorAll('h1,h2,h3')).map(e=>e.textContent),overflowElements:Array.from(document.querySelectorAll('main,section,article,table,pre')).filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&(r.right>innerWidth+2||r.left< -2)}).slice(0,25).map(e=>({tag:e.tagName,label:e.getAttribute('aria-label'),width:Math.round(e.getBoundingClientRect().width)}))}));
 const viewportPng=await page.screenshot({path:path.join(root,stem+'-viewport.png'),animations:'disabled',fullPage:false});
 await page.screenshot({path:path.join(root,stem+'-full.png'),animations:'disabled',fullPage:true});
 // Supplemental lossless PNG thumbnail of the actual viewport, for remote visual
 // inspection through authenticated CI logs when artifact downloads are unavailable.
 // Canvas is detached: the product DOM/layout/styles and original PNGs are untouched.
 const preview=await page.evaluate(async b64=>{const bytes=Uint8Array.from(atob(b64),c=>c.charCodeAt(0));const image=await createImageBitmap(new Blob([bytes],{type:'image/png'}));const width=Math.min(image.width,image.width<600?260:640),height=Math.round(image.height*width/image.width);const canvas=new OffscreenCanvas(width,height);canvas.getContext('2d')!.drawImage(image,0,0,width,height);const blob=await canvas.convertToBlob({type:'image/png'});const values=new Uint8Array(await blob.arrayBuffer());let binary='';for(let i=0;i<values.length;i++)binary+=String.fromCharCode(values[i]);return btoa(binary);},viewportPng.toString('base64'));
 console.log(JSON.stringify({gui_preview:id,title,viewport:viewport.name,layout,preview_png_base64:preview}));
 fs.writeFileSync(path.join(root,stem+'.json'),JSON.stringify({id,title,viewport,focus:focus??null,path:new URL(page.url()).pathname,query:new URL(page.url()).search,captured_at:new Date().toISOString(),synthetic:true,layout,files:[stem+'-viewport.png',stem+'-full.png']},null,2));
}
export async function capturePublic(page:Page,id:number,title:string){
 if(!process.env.NOC_GUI_OUTPUT)return;
 const original=page.viewportSize();
 for(const viewport of viewports){await page.setViewportSize({width:viewport.width,height:viewport.height});await capture(page,id,title,viewport);}
 if(original)await page.setViewportSize(original);await page.evaluate(()=>scrollTo(0,0));
}
