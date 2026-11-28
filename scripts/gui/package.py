"""Validate actual-browser captures and produce a self-contained visual review package."""
import argparse
import hashlib
import html
import json
import shutil
import struct
import zipfile
from pathlib import Path


def build(parts: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    images = output / 'screenshots'
    images.mkdir()
    captures = []
    keys = set()
    for file in sorted(parts.rglob('*.json')):
        item = json.loads(file.read_text())
        if not isinstance(item, dict) or not {'id', 'viewport', 'files', 'layout'} <= item.keys():
            continue
        key = (item['id'], item['viewport']['name'])
        if key in keys:
            raise ValueError('Duplicate screenshot identity')
        keys.add(key)
        for name in item['files']:
            if Path(name).name != name or not name.endswith('.png'):
                raise ValueError('Unsafe capture path')
            source = file.parent / name
            data = source.read_bytes()
            if data[:8] != b'\x89PNG\r\n\x1a\n':
                raise ValueError('Not an actual PNG')
            width, height = struct.unpack('>II', data[16:24])
            expected = item['viewport']
            if width != expected['width'] or height < expected['height']:
                raise ValueError('Incorrect screenshot resolution')
            if 'viewport' in name and height != expected['height']:
                raise ValueError('Incorrect viewport height')
            shutil.copyfile(source, images / name)
            item.setdefault('sha256', {})[name] = hashlib.sha256(data).hexdigest()
        captures.append(item)
    expected = {(i, v) for i in range(1, 32) for v in ['desktop', 'mobile']}
    if keys != expected:
        raise ValueError('Missing/unexpected actual GUI captures: ' + str(sorted(expected - keys)))
    runtimes = []
    for f in parts.rglob('*-runtime.json'):
        record = json.loads(f.read_text())
        if record['page_errors']:
            raise ValueError('Actual application has uncaught errors')
        runtimes.append(record)
    findings = [{'id': x['id'], 'viewport': x['viewport']['name'], 'issue': 'horizontal document overflow', 'width': x['layout']['scrollWidth']} for x in captures if x['layout']['scrollWidth'] > x['viewport']['width']]
    report = {'scope': 'Actual Chromium-rendered application and installed WordPress plugins; synthetic disposable WISP data only', 'operator_visual_approval': 'REQUIRED — not yet granted', 'captures': len(captures), 'png_files': len(captures) * 2, 'automated_layout_findings': findings, 'runtime': runtimes, 'items': sorted(captures, key=lambda x: (x['id'], x['viewport']['name']))}
    (output / 'manifest.json').write_text(json.dumps(report, indent=2))
    cards=[]
    for x in report['items']:
        stem=str(x['id']).zfill(2)+'-'+x['viewport']['name']
        title=html.escape(x['title'])
        cards.append(f'<article data-title="{title.lower()}" data-viewport="{x["viewport"]["name"]}"><h2>{x["id"]}. {title}</h2><p>{x["viewport"]["width"]}×{x["viewport"]["height"]} viewport · actual rendered GUI · synthetic only</p><a href="screenshots/{stem}-viewport.png"><img loading="lazy" src="screenshots/{stem}-viewport.png" alt="{title} {x["viewport"]["name"]}"></a><p><a href="screenshots/{stem}-viewport.png">Original viewport PNG</a> · <a href="screenshots/{stem}-full.png">Full page PNG</a></p></article>')
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NWO NOC — actual GUI review</title><style>body{font:16px system-ui;margin:0;background:#eef2f7;color:#182b45}header{padding:24px;background:#132740;color:white}main{padding:20px;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:20px}article{background:white;padding:18px;border-radius:10px;min-width:0}h2{font-size:19px}img{width:100%;height:auto;border:1px solid #cbd5e1}a{color:#1660a6}input,select{padding:10px;margin:8px}header a{color:#b9dcff}[hidden]{display:none}</style><header><h1>Northwest Ohio Broadband — actual GUI screenshot gallery</h1><p>Disposable synthetic WISP environment. No mockup, production connection, customer message or live website change. Native integrations disabled.</p><p><strong>OPERATOR VISUAL APPROVAL REQUIRED before actual installation.</strong> Screenshots are evidence for review, not approval.</p><p>1920×1080 desktop and390×844 mobile; original viewport and full-page PNGs. No UI elements hidden or replaced for captures. Offline map basemap may be unavailable; markers and map controls remain real.</p><a href="manifest.json">Capture manifest, checksums and layout findings</a><label> Search <input id="search" type="search"></label><label> Viewport <select id="viewport"><option value="">Both</option><option>desktop</option><option>mobile</option></select></label></header><main>'''+''.join(cards)+'''</main><script>function filter(){document.querySelectorAll('article').forEach(x=>x.hidden=!x.dataset.title.includes(document.querySelector('#search').value.toLowerCase())||!!document.querySelector('#viewport').value&&x.dataset.viewport!==document.querySelector('#viewport').value)}document.querySelector('#search').addEventListener('input',filter);document.querySelector('#viewport').addEventListener('change',filter)</script></html>'''
    (output / 'index.html').write_text(page)
    (output / 'VISUAL-REVIEW.md').write_text('# Actual GUI visual review\n\nOperator approval: REQUIRED, pending.\n\nReview typography/colors, sidebar grouping, chart labels, map controls, empty space, overlap, navigation, mobile forms and task flow. The manifest retains all detected document overflows and runtime/API errors; successful capture does not establish a polished or approved interface. Existing integration configuration uses technical JSON controls, some modules open in separate layouts, and offline maps cannot fetch external tiles. Review these actual limitations in original PNGs. No mock responses or substitute graphics were used.\n\nAutomated horizontal-overflow findings: '+str(len(findings))+'\n')
    archive=output / 'nwo-noc-gui-preview.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for f in sorted(output.rglob('*')):
            if f.is_file() and f!=archive:z.write(f,f.relative_to(output))
    print(json.dumps({'captures':len(captures),'png_files':len(captures)*2,'layout_findings':len(findings),'zip_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'operator_approval':'PENDING'}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('parts',type=Path);p.add_argument('output',type=Path);a=p.parse_args();build(a.parts,a.output)
