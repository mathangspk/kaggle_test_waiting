"""Production-image fault acceptance that never writes to the live monitor.

Run only after the scheduled discovery worker is stopped by the caller. Uses the
real supervisor, one Chromium at a time and private temporary synthetic auth. It
deliberately performs NO ingestion, so production activity evidence is untouched.
"""
import json
import os
from pathlib import Path
import sys
import tempfile

import discovery_worker as worker
from discovery_visit import save_private


results = []
with tempfile.TemporaryDirectory(prefix='phase11-fault-') as directory:
    root = Path(directory)
    os.chmod(root, 0o700)
    for name, crash in [('hung_browser', False), ('crashed_browser_leader', True)]:
        marker = root / (name + '.started')
        code = ("from playwright.sync_api import sync_playwright; import os,time; "
                "p=sync_playwright().start(); b=p.chromium.launch(headless=True); "
                "open(" + repr(str(marker)) + ",'w').write('started'); " +
                ("os._exit(17)" if crash else "time.sleep(120)"))
        payload, metrics = worker.run_visit(root / 'unused', [sys.executable, '-c', code], deadline=12)
        assert marker.exists(), 'browser did not start; cannot claim browser fault coverage'
        assert payload['error'] == ('worker_crash' if crash else 'visit_timeout')
        assert not worker.descendants()
        results.append({'case': name, 'result': payload['error'], 'browser_started': True,
                        'descendants_after': 0, 'duration_seconds': metrics['duration_seconds']})
    for kind in ('empty_auth', 'invalid_auth'):
        auth = root / 'storage-state.json'
        value = {'cookies': [], 'origins': []}
        if kind == 'invalid_auth':
            value['cookies'] = [{'name': '.AspNet.Cookies', 'value': 'phase11-deliberately-invalid',
                'domain': '.kaggle.com', 'path': '/', 'expires': -1, 'httpOnly': True,
                'secure': True, 'sameSite': 'Lax'}]
        save_private(auth, value)
        payload, metrics = worker.run_visit(auth)
        assert payload['auth_error'] == 'authentication_required' and payload['coverage'] == 'none'
        assert not worker.descendants()
        results.append({'case': kind, 'result': payload['auth_error'], 'coverage': payload['coverage'],
                        'descendants_after': 0, 'duration_seconds': metrics['duration_seconds']})

assert not worker.descendants()
print(json.dumps({'faults': results, 'final_cgroup': worker.counters()}), flush=True)
