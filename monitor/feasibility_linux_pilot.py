"""Bounded read-only Kaggle pilot. Logs only allowlisted identity/event evidence."""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from playwright.async_api import async_playwright

ROOT = Path('/private')


def counters():
    result = {}
    for name in ['memory.current', 'memory.peak', 'memory.max', 'cpu.stat', 'cpu.max', 'pids.current', 'memory.events']:
        try:
            result[name] = Path('/sys/fs/cgroup', name).read_text().strip()
        except OSError:
            pass
    return result


async def visit():
    empty = os.environ.get('PILOT_EMPTY') == '1'
    invalid = os.environ.get('PILOT_INVALID') == '1'
    result = {'observed_at': datetime.now(timezone.utc).isoformat(), 'empty_state_control': empty,
              'invalid_state_control': invalid, 'coverage': 'none', 'cgroup_start': counters()}
    started = time.monotonic()
    browser = None
    async with async_playwright() as pw:
        try:
            async with asyncio.timeout(55):
                browser = await pw.chromium.launch(headless=True, timeout=20000)
                state_input = None if empty else str(ROOT / 'storage-state.json')
                if invalid:
                    raw = json.loads((ROOT / 'storage-state.json').read_text())
                    state_input = {'cookies': [dict(x, value='phase3-deliberately-invalid') for x in raw['cookies']], 'origins': []}
                ctx = await browser.new_context(storage_state=state_input)
                page = await ctx.new_page()
                responses = []
                page.on('response', lambda r: responses.append({'path': urlsplit(r.url).path, 'status': r.status}) if '/api/' in r.url and urlsplit(r.url).hostname == 'www.kaggle.com' else None)
                async with page.expect_response(lambda r: r.url.endswith('/users.UsersService/GetCurrentUser'), timeout=25000) as response:
                    await page.goto('https://www.kaggle.com/', wait_until='domcontentloaded', timeout=25000)
                r = await response.value
                def identity(x):
                    if isinstance(x, dict):
                        return any((k.lower() == 'username' and v == 'thangma') or identity(v) for k, v in x.items())
                    if isinstance(x, list):
                        return any(identity(v) for v in x)
                    return False
                verified = r.status == 200 and identity(await r.json())
                result['identity'] = {'http_status': r.status, 'username': 'thangma' if verified else None}
                if not verified:
                    result['status'] = 'authentication_required'
                    login = page.get_by_role('button', name=re.compile('^(Login|Sign In|Sign in)$')).or_(page.get_by_role('link', name=re.compile('^(Login|Sign In|Sign in)$')))
                    try:
                        await login.first.wait_for(state='visible', timeout=4000)
                        result['login_visible'] = True
                    except Exception:
                        result['login_visible'] = False
                    return result
                if empty or invalid:
                    result['status'] = 'unexpected_authenticated_empty_state'
                    return result
                await page.get_by_role('button', name='Thang Ma', exact=True).click(timeout=7000)
                profile = await page.get_by_role('link', name='Your Profile', exact=True).get_attribute('href')
                result['profile'] = profile if profile == '/thangma' else 'mismatch'
                if profile != '/thangma':
                    result['status'] = 'identity_mismatch'
                    return result
                await page.keyboard.press('Escape')
                await page.get_by_role('button', name=re.compile('View Active Events|Open Active Events')).click(timeout=7000)
                panel = page.get_by_role('tooltip')
                await panel.wait_for(timeout=7000)
                await page.wait_for_timeout(5000)
                before = await panel.inner_text()
                result['scrolling'] = await panel.evaluate("""el => [el,...el.querySelectorAll('*')].filter(x=>x.clientHeight>0&&x.scrollHeight>x.clientHeight&&['auto','scroll'].includes(getComputedStyle(x).overflowY)).map(x=>{const before=x.scrollTop;x.scrollTop=x.scrollHeight;return {client_height:x.clientHeight,scroll_height:x.scrollHeight,before,after:x.scrollTop}})""")
                await page.wait_for_timeout(1000)
                text = await panel.inner_text()
                result['events'] = {'versions': re.findall(r'Version #(\d+)', text),
                                    'successful_labels': text.count('Successful'),
                                    'active_footer': re.findall(r'\d+ Active Events', text),
                                    'queue_monitor_probe_present': 'Queue Monitor Probe' in text,
                                    'event_view_links': await panel.get_by_role('link', name=re.compile('^Status for .*Click to open')).count(),
                                    'panel_text_length': len(text),
                                    'unchanged_after_scroll': before == text}
                result['api_responses'] = responses
                result['coverage'] = 'partial'
                result['status'] = 'authenticated_events_read'
                raw = await ctx.storage_state()
                state = {'cookies': [x for x in raw['cookies'] if x['domain'] in ['kaggle.com', '.kaggle.com', 'www.kaggle.com']],
                         'origins': [x for x in raw['origins'] if x['origin'] == 'https://www.kaggle.com']}
                tmp = ROOT / 'storage-state.json.refreshed'
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as f:
                    json.dump(state, f)
                os.replace(tmp, ROOT / 'storage-state.json')
                result['refreshed_state_saved'] = True
        except Exception as exc:
            result['status'] = 'visit_failed'
            result['error_type'] = type(exc).__name__
        finally:
            if browser:
                await browser.close()
            result['elapsed_seconds'] = round(time.monotonic() - started, 3)
            result['cgroup_end'] = counters()
    return result


if __name__ == '__main__':
    print(json.dumps(asyncio.run(visit())))
