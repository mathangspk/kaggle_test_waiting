"""One serialized private-auth diagnostic; output only validated row shape facts."""
import asyncio
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit

import discovery_visit as visit
from discovery_contract import STATES
from playwright.async_api import Locator, Response

original_json = Response.json
async def diagnostic_json(self):
    raw = await original_json(self)
    if self.url.endswith('/users.UsersService/GetCurrentUser'):
        def fields(value):
            if isinstance(value,dict):
                return [k for k in value if k.lower() in ('username','displayname')] + [k for v in value.values() for k in fields(v)]
            if isinstance(value,list): return [k for v in value for k in fields(v)]
            return []
        print(json.dumps({'identity_field_names':fields(raw)}),flush=True)
    return raw

Response.json = diagnostic_json

original_click = Locator.click
async def diagnostic_click(self, *args, **kwargs):
    if 'Thang Ma' in str(self):
        await self.page.get_by_role('button').first.wait_for(timeout=10000)
        print(json.dumps({'account_controls': await self.page.get_by_role('button').evaluate_all(
            """els => els.map(e=>({label:(e.getAttribute('aria-label')||'').slice(0,80),
                text:(e.innerText||'').slice(0,80),title:(e.title||'').slice(0,80),
                hasPopup:e.getAttribute('aria-haspopup'),
                images:Array.from(e.querySelectorAll('img')).map(x=>({alt:x.alt,
                    kaggleAvatar:/kaggle/.test(x.src)}))})),""".rstrip(','))}),flush=True)
        print(json.dumps({'profile_links':await self.page.locator('a[href="/thangma"]').count()}),flush=True)
    return await original_click(self,*args,**kwargs)

Locator.click = diagnostic_click

original = visit.event


def inspect(href, text, at):
    url = urlsplit(href)
    # No arbitrary query values or provider bodies in operator output.
    query = parse_qs(url.query)
    print(json.dumps({'kaggle_https': url.scheme == 'https' and url.netloc == 'www.kaggle.com',
                      'href_hash': hashlib.sha256(href.encode()).hexdigest()[:12],
                      'path_shape': re.sub(r'[^/]+', '*', url.path),
                      'safe_code_path': url.path if re.fullmatch(r'/code/thangma/[a-z0-9-]+(?:/(?:edit|[0-9]+))?/?',url.path) else None,
                      'owner_thangma': '/thangma/' in url.path,
                      'code_path_supported': bool(re.fullmatch(r'/code/thangma/[a-z0-9][a-z0-9-]{0,127}/?', url.path)),
                      'query_keys': sorted(k for k in query if re.fullmatch('[A-Za-z]+', k)),
                      'script_version_ids': [v for v in query.get('scriptVersionId', []) if v.isdigit()],
                      'versions': re.findall(r'\bVersion #(\d+)\b', text),
                      'labels': [s for s in STATES if re.search(r'\b'+s+r'\b', text)]}), flush=True)
    return original(href, text, at)


visit.event = inspect
def trace(frame, kind, arg):
    if (kind == 'exception' and frame.f_code.co_filename.endswith('discovery_visit.py')
            and isinstance(arg[1], Exception) and not isinstance(arg[1], StopIteration)):
        print(json.dumps({'exception_type':type(arg[1]).__name__,
                          'function': frame.f_code.co_name, 'line': frame.f_lineno}), flush=True)
    return trace

sys.settrace(trace)
result = asyncio.run(visit.visit(Path('/private/thangma/storage-state.json')))
print(json.dumps({'result': result['error'] or result['auth_error'] or result['coverage']}))
