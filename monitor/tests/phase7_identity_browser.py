"""Built-image, network-none UI regression for delayed navigation and independent href."""
import asyncio
import json
import time
from playwright.async_api import async_playwright
from discovery_visit import identity_hint, verify_profile


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('''<script>setTimeout(()=>{
              const b=document.createElement('button');b.setAttribute('aria-label','Renamed account');
              b.onclick=()=>{const a=document.createElement('a');a.href='/thangma';
                a.innerText='Your Profile';document.body.appendChild(a)};document.body.appendChild(b)
            },6500)</script>''')
            started = time.monotonic()
            await verify_profile(page, identity_hint({'userName':'thangma','displayName':'Renamed account'}))
            elapsed = time.monotonic()-started
            assert 6 <= elapsed < 15
            await page.locator('a').evaluate("a=>a.setAttribute('href','/other')")
            # Existing wrong profile link must never be replaced by an expected-href selector.
            await page.get_by_role('button').evaluate('b=>b.onclick=null')
            try:
                await verify_profile(page,'Renamed account')
            except ValueError as exc:
                assert str(exc)=='identity_mismatch'
            else:
                raise AssertionError('wrong profile accepted')
            print(json.dumps({'delayed_profile_seconds':round(elapsed,3),
                'renamed_hint_supported':True,'wrong_profile_rejected':True,'network':'none'}))
        finally:
            await browser.close()


asyncio.run(main())
