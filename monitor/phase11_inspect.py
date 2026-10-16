"""Read-only authenticated event DOM/schema inspection; never follows status links."""
import asyncio
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from playwright.async_api import async_playwright
from discovery_visit import identity_hint, verify_profile


def schema(value,depth=0):
    if depth>6:return type(value).__name__
    if isinstance(value,dict):return {k:schema(v,depth+1) for k,v in value.items()}
    if isinstance(value,list):return [schema(v,depth+1) for v in value[:3]]
    return type(value).__name__


async def main():
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        try:
            ctx=await browser.new_context(storage_state='/private/thangma/storage-state.json')
            # Hard deny all notebook/editor navigations, including unintended popups.
            await ctx.route('**/code/**',lambda route:route.abort())
            page=await ctx.new_page()
            responses=[]
            page.on('response',lambda r:responses.append(r) if '/api/i/' in r.url else None)
            async with page.expect_response(lambda r:r.url.endswith('/users.UsersService/GetCurrentUser'),timeout=20000) as response:
                await page.goto('https://www.kaggle.com/',wait_until='domcontentloaded',timeout=20000)
            r=await response.value
            assert r.status==200
            await verify_profile(page,identity_hint(await r.json()))
            await page.get_by_role('button',name=re.compile('View Active Events|Open Active Events')).click(timeout=5000)
            panel=page.get_by_role('tooltip');await panel.wait_for(timeout=5000)
            await page.wait_for_timeout(9000)
            rows=await panel.get_by_role('link',name=re.compile('^Status for .*Click to open')).evaluate_all(r"""links=>links.map(a=>{
                const safe=t=>(t||'').split('\n').map(s=>s.trim()).filter(Boolean).map(s=>
                    /^(Running|Queued|Pending|Successful|Complete|Failed|Cancelled|Canceled|Unknown|Interactive|Version #\d+|Session|Notebook|Cancel|Stop)$/.test(s)?s:
                    s.match(/^Version #\d+/)?.[0]||'<text>');
                const tree=(x,d)=>({tag:x.tagName,role:x.getAttribute('role'),
                    text:safe(Array.from(x.childNodes).filter(n=>n.nodeType===3).map(n=>n.textContent).join(' ')),
                    attrs:Array.from(x.attributes).map(a=>a.name),
                    children:d?Array.from(x.children).map(c=>tree(c,d-1)):[]});
                let p=a;const ancestors=[];for(let i=0;i<5&&p;i++,p=p.parentElement)
                    ancestors.push({tag:p.tagName,versions:(p.innerText||'').match(/Version #\d+/g),
                        links:p.querySelectorAll('[role=link]').length,tree:tree(p,2)});
                const label=a.getAttribute('aria-label')||'';
                const title=label.replace(/^Status for /,'').replace(/\. Click to open.*$/,'');
                const redact=t=>(t||'').split(title).join('<title>');
                const li=a.closest('li');
                return {tag:a.tagName,href:a.getAttribute('href'),label,
                    aria_status_segment:label.split('. Click to open')[0],
                    link_label_redacted:redact(label),li_label_redacted:redact(li?.getAttribute('aria-label')),
                    row_text_redacted:redact(li?.innerText),ancestors};
            })""")
            for row in rows:
                row.pop('ancestors',None)
                row['label_hash']=hashlib.sha256((row.pop('label') or '').encode()).hexdigest()[:16]
                href=row.get('href')
                if href:row['href']=urlsplit(href).path if re.fullmatch(r'/code/thangma/[a-z0-9-]+(?:/edit)?',urlsplit(href).path) else '<other>'
            apis=[]
            for r in responses:
                path=urlsplit(r.url).path
                if 'active_events.' in path:
                    try:apis.append({'path':path,'status':r.status,'schema':schema(await r.json())})
                    except Exception:pass
            print(json.dumps({'identity_verified':True,'rows':rows,'event_api_schemas':apis,'editor_navigation':False}))
        finally:await browser.close()


asyncio.run(main())
