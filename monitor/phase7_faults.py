"""Serialized isolated-image fault acceptance; never alters retained browser auth.

Run only after stopping the scheduled discovery worker. Uses its real supervisor,
one Chromium at a time, private temporary synthetic auth, and pilot ingestion only.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import discovery_worker as worker
from discovery_visit import save_private


results = []
with tempfile.TemporaryDirectory(prefix='phase7-fault-') as directory:
    root = Path(directory)
    os.chmod(root, 0o700)
    for name, crash in [('hung_browser', False), ('crashed_browser_leader', True)]:
        marker = root / (name + '.started')
        code = ("from playwright.sync_api import sync_playwright; import os,time; "
                "p=sync_playwright().start(); b=p.chromium.launch(headless=True); "
                "open("+repr(str(marker))+",'w').write('started'); " +
                ("os._exit(17)" if crash else "time.sleep(120)"))
        payload, metrics = worker.run_visit(root/'unused', [sys.executable,'-c',code], deadline=12)
        assert marker.exists(), 'browser did not start; cannot claim browser fault coverage'
        assert payload['error'] == ('worker_crash' if crash else 'visit_timeout')
        assert not worker.descendants()
        results.append({'case':name,'result':payload['error'],'browser_started':True,
                        'descendants_after':0,'metrics':metrics})
    for kind in ('empty_auth', 'invalid_auth'):
        auth = root / 'storage-state.json'
        value = {'cookies':[], 'origins':[]}
        if kind == 'invalid_auth':
            value['cookies'] = [{'name':'.AspNet.Cookies','value':'phase7-deliberately-invalid',
                'domain':'.kaggle.com','path':'/','expires':-1,'httpOnly':True,
                'secure':True,'sameSite':'Lax'}]
        save_private(auth, value)
        payload, metrics = worker.run_visit(auth)
        assert payload['auth_error'] == 'authentication_required'
        assert not worker.descendants()
        results.append({'case':kind,'result':payload['auth_error'],'coverage':payload['coverage'],
                        'descendants_after':0,'metrics':metrics})
    # Durable real rejected HTTP -> disk reload -> suspended retry -> accepted HTTP.
    wrong_token = root / 'token'
    save_private(wrong_token, 'unused')
    wrong_token.write_text('phase7-intentionally-wrong-token')
    statefile = root / 'fault-state.json'
    calls = []
    def visit_once():
        calls.append(1)
        return payload, metrics
    url = 'http://monitor:3000/api/activity/browser-discovery'
    state = worker.cycle({}, visit_once, lambda p: worker.run_ingest(url,wrong_token,p),
                         checkpoint=lambda s:save_private(statefile,s))
    save_private(statefile,state)
    assert state['ingestion'] == 'ingestion_failed' and state['outbox'] == payload
    assert state['suspended'] == 'authentication_required'
    reloaded = json.loads(statefile.read_text())
    worker.cycle(reloaded, visit_once,
                 lambda p:worker.run_ingest(url,Path('/run/discovery-token/token'),p))
    assert len(calls) == 1 and 'outbox' not in reloaded and reloaded['ingestion'] == 'ok'
    assert reloaded['suspended'] == 'authentication_required'
    results.append({'case':'rejected_ingestion_durable_outbox_reload_suspended_retry',
                    'visits':len(calls),'failed_ingestion':state['ingestion'],
                    'recovered_ingestion':reloaded['ingestion'],'outbox_remaining':False,
                    'suspended':reloaded['suspended']})

assert not worker.descendants()
print(json.dumps({'faults':results,'final_cgroup':worker.counters()}),flush=True)
