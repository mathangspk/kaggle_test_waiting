"""Independent Chromium DOM fixture for the observed LI layout; no network/clicks."""
import asyncio
import json
from playwright.async_api import async_playwright
from discovery_visit import ROW_SAMPLE_JS, collect_panel
from discovery_contract import observation, validate


async def main():
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        try:
            page=await browser.new_page()
            await page.set_content('''<div role="tooltip"><ul role="list">
              <li><a role="link" aria-label="Status for Retrying. Click to open Successful in new window">
                <div><span>Successful</span><span>Version #1</span><span>Retrying</span></div></a></li>
              <li><a role="link" aria-label="Status for Running. Click to open Same title in new window">
                <div><span>Same title</span><span>Interactive Session</span><span>Running</span></div></a></li>
              <li><a role="link" aria-label="Status for Running. Click to open Same title in new window">
                <div><span>Same title</span><span>Interactive Session</span><span>Running</span></div></a></li>
              <li><a role="link" href="https://www.kaggle.com/code/thangma/read-only?scriptVersionId=123"
                aria-label="Status for Successful. Click to open Terminal example in new window">
                <div><span>Terminal example</span><span>Version #3</span><span>Successful</span></div></a></li>
              <li><a role="link" href="https://www.kaggle.com/code/thangma/interactive/edit"
                aria-label="Status for Cancelled. Click to open Cancelled example in new window">
                <div><span>Cancelled example</span><span>Interactive Session</span><span>Cancelled</span></div></a></li>
            </ul></div>''')
            # Fail any click, popup or real navigation. Sampling/scrolling is enough.
            await page.evaluate("document.addEventListener('click',()=>{throw Error('unexpected click')},true)")
            requests=[];page.on('request',lambda r:requests.append(r.url))
            panel=page.get_by_role('tooltip')
            raw=await panel.get_by_role('link').evaluate_all(ROW_SAMPLE_JS)
            assert raw[0]['status']=='Unknown'
            assert raw[1]['interactive'] and raw[1]['status']=='Running'
            rows=await collect_panel(panel,page.wait_for_timeout)
            obs=[observation(r,1000,i) for i,r in enumerate(rows)]
            payload={'account':'thangma','observed_at':1000,'coverage':'partial',
                'observations':obs,'error':None,'auth_error':None}
            validate(payload,{'thangma'},1000)
            assert len(obs)==5 and len({o['identity'] for o in obs})==5
            assert obs[0]['state']=='unresolved' and obs[0]['provider_state']=='Unknown'
            assert all(obs[i]['state']=='unresolved' for i in (1,2,4))
            assert obs[3]['identity']=='script-version:123' and obs[3]['state']=='terminal'
            assert obs[4]['provider_state']=='Cancelled' and obs[4]['ref'] is None
            assert not requests and len(page.context.pages)==1
            print(json.dumps({'rows':5,'identical_href_less_rows_retained':2,
                'title_word_not_status':True,'interactive_unresolved_positive':True,
                'direct_version_supported':True,'editor_requests':0,'clicks_required':0}))
        finally:await browser.close()


asyncio.run(main())
