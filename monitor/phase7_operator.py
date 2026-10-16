"""Scoped Phase 7 operator. Server actions run via existing remote.py private stdin.

--upload uses the existing verified SSH key and uploads an allowlisted source tree.
No credential values, container environments, provider bodies or raw DB are emitted.
"""
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import time
import urllib.request

ROOT = Path('/home/thangma/kaggle-discovery-phase7')
PRIVATE = Path('/home/thangma/.local/share/kaggle-monitor-phase7')
PROJECT = 'kaggle-discovery-pilot'


def run(args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def docker(*args):
    return run(['docker', *args])


def compose(*args):
    env = dict(os.environ, PILOT_ACCOUNTS_ENV=str(PRIVATE / 'accounts.env'),
               DISCOVERY_TOKEN_DIR=str(PRIVATE / 'token'), DISCOVERY_ENABLED='true')
    return run(['docker', 'compose', '-p', PROJECT, '-f', str(ROOT / 'compose.pilot.yaml'),
                '-f', str(ROOT / 'compose.discovery.yaml'), '--profile', 'discovery', *args], env=env)


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def containers():
    ids = docker('ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT).split()
    return json.loads(docker('inspect', *ids)) if ids else []


def production():
    ids = docker('ps', '-q').split()
    result = []
    for c in json.loads(docker('inspect', *ids)) if ids else []:
        if c['Config']['Labels'].get('com.docker.compose.project') != PROJECT:
            result.append({'id': c['Id'], 'name': c['Name'], 'started': c['State']['StartedAt'],
                           'restarts': c['RestartCount'], 'image': c['Image']})
    return result


def host():
    mem = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    return {k: mem[k].strip() for k in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree')} | {
        'loadavg': Path('/proc/loadavg').read_text().strip()}


def http(port, path):
    start = time.monotonic()
    with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=5) as response:
        body = response.read()
        return {'status': response.status, 'ms': round((time.monotonic()-start)*1000, 2),
                'bytes': len(body)}, json.loads(body) if path != '/' else None


def inventory():
    result = {'host': host(), 'production': production(), 'health': http(3000, '/healthz')[0]}
    auth = Path('/home/thangma/.local/share/kaggle-monitor-auth/thangma/storage-state.json')
    result['auth'] = [{'path': str(p), 'uid': p.stat().st_uid,
                       'mode': oct(p.stat().st_mode & 0o777), 'bytes': p.stat().st_size}
                      for p in (auth.parent, auth)]
    result['port3001_free'] = subprocess.run(['ss', '-H', '-ltn', 'sport = :3001'],
                                             capture_output=True, text=True).stdout == ''
    emit(result)
    ROOT.mkdir(mode=0o700, exist_ok=True)
    (ROOT / 'baseline.json').write_text(json.dumps(result))


def build():
    # The pilot's upstream bases were resolved once and are now digest-pinned.
    pins = [line for line in (ROOT / 'Dockerfile.pilot').read_text().splitlines() if line.startswith('FROM ')]
    compose('config', '--quiet')
    output = compose('build')
    # Build output contains only allowlisted source/dependency operations.
    (ROOT / 'build.log').write_text(output)
    value = {'base_pins': pins, 'monitor_image': docker('image', 'inspect',
         'kaggle-phase7-monitor:local', '--format', '{{.Id}}'), 'worker_image': docker(
         'image', 'inspect', PROJECT + '-discovery', '--format', '{{.Id}}')}
    for service in ('monitor','worker'):
        docker('tag',value[service+'_image'],'kaggle-phase7-'+service+':requalification-20261016')
    (ROOT/'image-manifest.json').write_text(json.dumps(value,indent=2))
    emit(value)


DERIVE = r'''
import json, os, secrets
from pathlib import Path
from dotenv import dotenv_values
from app import parse_accounts
accounts = parse_accounts(dotenv_values('/production/accounts.env', interpolate=False))
selected = [a for a in accounts if a.username == 'thangma']
assert len(selected) == 1
a = selected[0]
values = {'ACCOUNT_COUNT': '1', 'ACCOUNT_1_USERNAME': a.username}
values['ACCOUNT_1_API_TOKEN' if a.token else 'ACCOUNT_1_KEY'] = a.token or a.key
assert parse_accounts(values)[0].username == 'thangma'
assert all('\n' not in v and '\r' not in v and "'" not in v for v in values.values())
p = Path('/private/accounts.env')
fd = os.open(p, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write(''.join(k+"='"+v+"'\n" for k,v in values.items()))
assert len(parse_accounts(dotenv_values(p, interpolate=False))) == 1
token = Path('/private/token/token')
fd = os.open(token, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
with os.fdopen(fd,'w') as f: f.write(secrets.token_urlsafe(48))
os.chown(token,1000,1000)
print('{"minimal_accounts":1,"identity":"thangma","validated_parser":true}')
'''


def prepare():
    assert json.loads((ROOT / 'baseline.json').read_text())['port3001_free']
    assert not any(c['State']['Running'] for c in containers()), 'stop existing pilot before review'
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    (PRIVATE / 'token').mkdir(mode=0o700, exist_ok=True)
    os.chown(PRIVATE / 'token', 1000, 1000)
    if not (PRIVATE / 'accounts.env').exists():
        emit(json.loads(docker('run', '--rm', '--network', 'none', '--read-only',
        '-e', 'PYTHONDONTWRITEBYTECODE=1', '-v',
        '/home/thangma/kaggle-queue-monitor/secrets:/production:ro',
            '-v', str(PRIVATE)+':/private', 'kaggle-phase7-monitor:local', 'python', '-c', DERIVE)))
    config = json.loads(compose('config', '--format', 'json'))
    credential_check = docker('run','--rm','--network','none','--read-only','-e','PYTHONDONTWRITEBYTECODE=1',
        '-v',str(PRIVATE/'accounts.env')+':/private/accounts.env:ro','kaggle-phase7-monitor:local',
        'python','-c',"from app import parse_accounts; from dotenv import dotenv_values; a=parse_accounts(dotenv_values('/private/accounts.env',interpolate=False)); assert len(a)==1 and a[0].username=='thangma'; print('one_validated_thangma_account')")
    assert credential_check=='one_validated_thangma_account'
    m, w = config['services']['monitor'], config['services']['discovery']
    assert m['environment']['AUTO_PROBES'] == 'false'
    assert m['environment']['PROBE_ADMISSION_POLICY'] == 'observe_only'
    assert len(config['services']) == 2
    assert m['ports'][0]['host_ip'] == '127.0.0.1' and str(m['ports'][0]['published']) == '3001'
    assert int(w['mem_limit']) == int(w['memswap_limit']) == 1073741824
    assert float(w['cpus']) == .75 and w['pids_limit'] == 128
    (ROOT / 'merged-config.json').write_text(json.dumps(config, indent=2))
    emit({'merged_config_verified': True, 'services': list(config['services'])})


def snapshot():
    result = {'time': time.time(), 'host': host(), 'containers': []}
    for c in containers():
        h = c['HostConfig']
        item = {'name': c['Name'], 'id': c['Id'], 'image': c['Image'],
                'status': c['State']['Status'], 'oom_killed': c['State']['OOMKilled'],
                'restart_count': c['RestartCount'], 'started_at':c['State']['StartedAt'], 'user': c['Config']['User'],
                'limits': {k: h[k] for k in ('Memory', 'MemorySwap', 'NanoCpus', 'PidsLimit',
                           'ShmSize', 'ReadonlyRootfs', 'Init', 'CapDrop', 'SecurityOpt')},
                'networks': list(c['NetworkSettings']['Networks']),
                'mounts': [{'source': v['Source'], 'destination': v['Destination'], 'rw': v['RW']}
                           for v in c['Mounts']]}
        if c['State']['Running']:
            item['cgroup'] = json.loads(docker('exec', c['Id'], 'python', '-c',
                "import json; from pathlib import Path; print(json.dumps({p:Path('/sys/fs/cgroup',p).read_text().strip() for p in ['memory.current','memory.peak','memory.max','memory.swap.max','memory.events','cpu.max','cpu.stat','pids.current','pids.peak']}))"))
            if c['Config']['Labels']['com.docker.compose.service'] == 'discovery':
                item['worker_state'] = json.loads(docker('exec', c['Id'], 'python', '-c',
                    "import json; from pathlib import Path; p=Path('/var/lib/discovery/state.json'); s=json.loads(p.read_text()) if p.exists() else {}; s.pop('outbox',None); print(json.dumps(s))"))
        result['containers'].append(item)
    for port in (3000, 3001):
        result[str(port)] = {}
        for path in ('/healthz', '/api/summary', '/'):
            try:
                timing, body = http(port, path)
                result[str(port)][path] = timing
                if port == 3001 and path == '/api/summary':
                    result['pilot_summary'] = {k: body.get(k) for k in ('meta', 'accounts')}
            except Exception:
                result[str(port)][path] = {'error': 'unavailable'}
    with (ROOT / 'measurements.jsonl').open('a') as f:
        f.write(json.dumps(result) + '\n')
    emit(result)


def watch(seconds):
    # Whole-container samples, plus HTTP responsiveness while browsers may be active.
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            snapshot()
        time.sleep(10)
    emit({'watch_seconds': seconds, 'evidence': str(ROOT / 'measurements.jsonl')})


def finish():
    compose('stop')
    after = production()
    baseline = json.loads((ROOT / 'baseline.json').read_text())['production']
    import sqlite3
    dbpath = Path('/var/lib/docker/volumes/'+PROJECT+'_pilot-monitor-data/_data/monitor.sqlite3')
    with sqlite3.connect('file:'+str(dbpath)+'?mode=ro',uri=True) as db:
        jobs = [json.loads(r[0]) for r in db.execute('SELECT data FROM runs')]
        evidence = [json.loads(r[0]) for r in db.execute('SELECT data FROM activity_evidence')]
        dbcheck = {'integrity':db.execute('PRAGMA integrity_check').fetchone()[0],
            'run_states':[r[0] for r in db.execute('SELECT state FROM runs')],
            'submit_attempts':sum(j.get('submit_begin') is not None for j in jobs),
            'calls':list(db.execute('SELECT operation,count(*) FROM calls GROUP BY operation')),
            'production_events':db.execute('SELECT count(*) FROM production_events').fetchone()[0],
            'activity_rows':len(evidence)}
        assert dbcheck['integrity']=='ok' and dbcheck['submit_attempts']==0 and not dbcheck['calls']
        assert dbcheck['production_events']==0
    os.chmod(dbpath.parent,0o700)
    for p in dbpath.parent.glob('monitor.sqlite3*'): os.chmod(p,0o600)
    images = []
    for c in containers():
        service = c['Config']['Labels']['com.docker.compose.service']
        tag = 'kaggle-phase7-'+service+':requalification-20261016'
        check = subprocess.run(['docker','image','inspect',c['Image']],capture_output=True,text=True)
        if check.returncode:
            images.append({'id':c['Image'],'cache_available':False})
        else:
            docker('tag',c['Image'],tag)
            image = json.loads(check.stdout)[0]
            images.append({'tag':tag,'id':c['Image'],'size_bytes':image['Size'],'cache_available':True})
    # Remove only exact non-secret artifacts from the initial Windows path attempt.
    wrong = Path('/home/thangma') / r'\home\thangma\kaggle-discovery-phase7'
    wrong_archive = Path('/home/thangma') / r'\home\thangma\kaggle-discovery-phase7\source.tar.gz'
    if wrong_archive.is_file(): wrong_archive.unlink()
    if wrong.is_dir(): wrong.rmdir()
    archive = ROOT/'source.tar.gz'
    if archive.exists(): archive.unlink()
    value = {'production_unchanged': after == baseline, 'production': after, 'database':dbcheck,
          'images':images,
          'pilot': [{'name': c['Name'], 'status': c['State']['Status'],
                     'pid': c['State']['Pid'], 'oom_killed': c['State']['OOMKilled']}
                    for c in containers()], 'host': host(), 'health': http(3000,'/healthz')[0]}
    (ROOT/'final-state.json').write_text(json.dumps(value,indent=2))
    emit(value)


def report():
    samples = [json.loads(line) for line in (ROOT/'measurements.jsonl').read_text().splitlines()]
    cycles, groups = {}, {}
    for sample in samples:
        for c in sample['containers']:
            if 'cgroup' not in c: continue
            epoch=c['id']+'@'+c.get('started_at','unknown')
            g = groups.setdefault(epoch, {'name':c['name'],'image':c['image'],'peak_bytes':0,
                                           'pids_peak':0,'cpu_usec':0,'throttled_usec':0})
            cg = c['cgroup']
            cpu = dict(line.split() for line in cg['cpu.stat'].splitlines())
            g['peak_bytes'] = max(g['peak_bytes'],int(cg['memory.peak']))
            g['pids_peak'] = max(g['pids_peak'],int(cg['pids.peak']))
            g['cpu_usec'] = max(g['cpu_usec'],int(cpu['usage_usec']))
            g['throttled_usec'] = max(g['throttled_usec'],int(cpu['throttled_usec']))
            s = c.get('worker_state',{})
            if s.get('last_attempt',0)>=samples[0]['time']: cycles[s['last_attempt']] = s
    timing = {}
    for port in ('3000','3001'):
        timing[port] = {}
        for path in ('/healthz','/api/summary','/'):
            values = sorted(s[port][path]['ms'] for s in samples if 'ms' in s[port][path])
            timing[port][path] = {'n':len(values),'min_ms':values[0],
                'p50_ms':values[len(values)//2],'p95_ms':values[min(len(values)-1,int(len(values)*.95))],
                'max_ms':values[-1], 'failures':sum('error' in s[port][path] for s in samples)}
    for s in cycles.values():
        metrics=s['metrics']
        before=dict(line.split() for line in metrics['cgroup_before']['cpu.stat'].splitlines())
        after=dict(line.split() for line in metrics['cgroup_after']['cpu.stat'].splitlines())
        s['visit_cpu_seconds']=round((int(after['usage_usec'])-int(before['usage_usec']))/1e6,6)
        s['visit_throttled_seconds']=round((int(after['throttled_usec'])-int(before['throttled_usec']))/1e6,6)
    data = {'samples':len(samples), 'host_min_available_kib':min(int(s['host']['MemAvailable'].split()[0]) for s in samples),
            'containers':groups, 'timings':timing,'cycles':list(cycles.values())}
    (ROOT/'report.json').write_text(json.dumps(data,indent=2))
    emit(data)


def audit():
    import socket
    active = docker('ps','-q','--filter','label=com.docker.compose.project='+PROJECT).split()
    assert not active
    with socket.socket() as sock:
        port_closed = sock.connect_ex(('127.0.0.1',3001)) != 0
    auth = Path('/home/thangma/.local/share/kaggle-monitor-auth/thangma/storage-state.json')
    result = {'no_running_pilot_containers':not active,'loopback3001_closed':port_closed,
              'production_unchanged':production()==json.loads((ROOT/'baseline.json').read_text())['production'],
              'private_metadata':[{'path':str(p),'uid':p.stat().st_uid,'gid':p.stat().st_gid,
                  'mode':oct(p.stat().st_mode&0o777),'mtime':p.stat().st_mtime}
                  for p in (auth.parent,auth,PRIVATE,PRIVATE/'accounts.env',PRIVATE/'token',PRIVATE/'token/token')],
              'available_pilot_images':docker('image','ls','--filter','reference=kaggle-phase7-*',
                                      '--format','{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}').splitlines()}
    (ROOT/'audit.json').write_text(json.dumps(result,indent=2))
    emit(result)


def upload():
    import paramiko
    base = Path(__file__).parent
    remote_root = '/home/thangma/kaggle-discovery-phase7'
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.connect('100.92.238.77', username='thangma',
                   key_filename=str(base / '.deploy/server_ed25519'),
                   look_for_keys=False, allow_agent=False, timeout=15)
    try:
        files = ['app.py', 'discovery_contract.py', 'discovery_visit.py', 'discovery_worker.py',
                 'Dockerfile', 'Dockerfile.discovery', 'compose.pilot.yaml', 'compose.discovery.yaml',
                 'requirements.txt', 'roadmap.json', '.dockerignore', 'phase7_operator.py',
                 'phase7_diagnostic.py', 'Dockerfile.pilot',
                 'phase7_faults.py',
                 'tests/discovery_linux_check.py']
        files += ['tests/phase7_identity_browser.py']
        files += ['phase8_operator.py']
        files += ['phase9_soak.py']
        files += ['phase11_inspect.py']
        files += ['phase11_collect.py']
        files += ['phase11_faults.py']
        files += ['tests/phase11_dom_browser.py']
        files += [str(p.relative_to(base)) for p in (base/'frontend').rglob('*')
                  if p.is_file() and not set(p.relative_to(base/'frontend').parts) &
                  {'node_modules', 'dist', 'tests'}]
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w:gz') as tar:
            for name in files:
                tar.add(base/name, arcname=name.replace('\\', '/'))
        with client.open_sftp() as s:
            try: s.mkdir(remote_root, mode=0o700)
            except OSError: pass
            with s.open(remote_root+'/source.tar.gz', 'wb') as f: f.write(data.getvalue())
        _, out, err = client.exec_command('tar -xzf '+shlex.quote(remote_root+'/source.tar.gz')+' -C '+shlex.quote(remote_root))
        error = err.read().decode(errors='replace')
        assert out.channel.recv_exit_status() == 0, 'source extraction failed: ' + error
        emit({'uploaded_allowlisted_files': len(files)})
    finally:
        client.close()


if __name__ == '__main__':
    action = sys.argv[1]
    if action == '--upload': upload()
    elif action == 'inventory': inventory()
    elif action == 'begin-requalification':
        assert not any(c['State']['Running'] for c in containers())
        import shutil
        prior = ROOT/'initial-pilot'
        prior.mkdir(mode=0o700,exist_ok=False)
        for p in ROOT.iterdir():
            if p.is_file() and (p.suffix in ('.json','.jsonl','.log') or p.name=='Dockerfile.pilot'):
                shutil.copy2(p,prior/p.name)
        (ROOT/'measurements.jsonl').unlink(missing_ok=True)
        inventory()
        statefile = Path('/var/lib/docker/volumes/'+PROJECT+'_discovery-data/_data/state.json')
        shutil.copy2(statefile,prior/'pre-requalification-worker-state.json')
        emit({'prior_evidence_archived':str(prior),'worker_schedule_preserved':True})
    elif action == 'build': build()
    elif action == 'prepare': prepare()
    elif action == 'start':
        prepare()
        emit({'compose': compose('up', '-d', '--no-build')})
        manifest=json.loads((ROOT/'image-manifest.json').read_text())
        for c in containers():
            service=c['Config']['Labels']['com.docker.compose.service']
            assert c['Image']==manifest['worker_image' if service=='discovery' else 'monitor_image']
        emit({'exact_built_images_running':True})
    elif action == 'snapshot': snapshot()
    elif action == 'watch': watch(int(sys.argv[2]))
    elif action == 'restart': emit({'compose': compose('restart', 'discovery')})
    elif action == 'stop-discovery': emit({'compose':compose('stop','discovery')})
    elif action == 'wait-fresh':
        from datetime import datetime
        started = time.time()
        until = time.monotonic()+390
        while time.monotonic()<until:
            import contextlib
            with contextlib.redirect_stdout(io.StringIO()): snapshot()
            account = http(3001,'/api/summary')[1]['accounts'][0]
            sources = account['activity']['sources']
            if sources and sources[0]['coverage']=='partial' and not sources[0]['error'] and not sources[0]['auth_error']:
                at = datetime.fromisoformat(sources[0]['observed_at']).timestamp()
                if at>started:
                    value={'fresh_success_after_command':True,'activity':account['activity'],
                           'auto_probes':http(3001,'/healthz')[1]['auto_probes']}
                    with (ROOT/'fresh-successes.jsonl').open('a') as f: f.write(json.dumps(value)+'\n')
                    emit(value)
                    break
            time.sleep(5)
        else: raise RuntimeError('no_fresh_success_within390s')
    elif action == 'identity-browser-regression':
        assert not any(c['State']['Running'] for c in containers()
                       if c['Config']['Labels']['com.docker.compose.service']=='discovery')
        output=docker('run','--rm','--network','none','--memory','1g','--memory-swap','1g',
            '--cpus','0.75','--pids-limit','128','--shm-size','128m','--tmpfs','/tmp:size=192m,mode=1777',
            '--read-only','--init','--user','1000:1000','--cap-drop','ALL',
            '--security-opt','no-new-privileges:true','-v',
            str(ROOT/'tests/phase7_identity_browser.py')+':/worker/phase7_identity_browser.py:ro',
            json.loads((ROOT/'image-manifest.json').read_text())['worker_image'],'python','phase7_identity_browser.py')
        emit({'browser_regression':output})
    elif action == 'live-once':
        compose('stop','discovery')
        code = ("import json; from pathlib import Path; import discovery_worker as w; "
                "p,m=w.run_visit(Path('/private/thangma/storage-state.json')); "
                "w.run_ingest('http://monitor:3000/api/activity/browser-discovery',Path('/run/discovery-token/token'),p); "
                "print(json.dumps({'payload':p,'metrics':m,'ingestion':'ok','descendants_after':len(w.descendants())}))")
        output = compose('run','--rm','--no-deps','discovery','python','-c',code)
        (ROOT/'live-once.json').write_text(output)
        emit(json.loads(output))
    elif action == 'retry-fixed-worker':
        compose('stop', 'discovery')
        volume = Path('/var/lib/docker/volumes/'+PROJECT+'_discovery-data/_data/state.json')
        state = json.loads(volume.read_text())
        assert state.get('last_result') == 'invalid_event_identity' and not state.get('suspended')
        (ROOT/'pre-fix-worker-state.json').write_text(json.dumps(state))
        state.pop('next_at',None)
        volume.write_text(json.dumps(state))
        os.chmod(volume,0o600)
        os.chown(volume,1000,1000)
        emit({'compose':compose('up','-d','--no-build','discovery'), 'operator_retry_after_fix':True})
    elif action == 'diagnose':
        compose('stop', 'discovery')
        emit({'diagnostic': compose('run', '--rm', '--no-deps', '-v',
             str(ROOT/'phase7_diagnostic.py')+':/worker/phase7_diagnostic.py:ro',
             'discovery', 'python', 'phase7_diagnostic.py')})
    elif action == 'persistence-restart':
        compose('stop','discovery')
        before = http(3001,'/api/summary')[1]['accounts'][0]['activity']
        compose('restart','monitor')
        for _ in range(30):
            try:
                after = http(3001,'/api/summary')[1]['accounts'][0]['activity']
                break
            except Exception: time.sleep(1)
        assert before['observations'] == after['observations']
        emit({'monitor_restart_observations_preserved':True, 'activity':after})
        emit({'worker_restart':compose('start','discovery')})
        snapshot()
    elif action == 'expiry':
        compose('stop','discovery')
        before = http(3001,'/api/summary')[1]['accounts'][0]['activity']
        assert before['observations'] and before['coverage'] == 'partial'
        assert before['sources'][0]['coverage']=='partial' and before['sources'][0]['freshness']=='fresh'
        assert not before['sources'][0]['error'] and not before['sources'][0]['auth_error']
        observed = before['sources'][0]['observed_at']
        from datetime import datetime
        wait = max(0, datetime.fromisoformat(observed).timestamp()+312-time.time())
        watch(int(wait)+1)
        expired_account = http(3001,'/api/summary')[1]['accounts'][0]
        after = expired_account['activity']
        assert after['sources'][0]['freshness'] == 'stale'
        assert after['sources'][0]['coverage']=='partial' and not after['sources'][0]['error'] and not after['sources'][0]['auth_error']
        assert before['observed_at']==after['observed_at']
        assert expired_account['status']=='stale'
        assert 'required_source_stale:browser_discovery' in expired_account['admission']['reasons']
        assert len(before['observations']) == len(after['observations'])
        value = {'case':'natural_300s_evidence_ttl_expiry','wait_seconds':wait,
                  'before':before,'after':after,'admission':expired_account['admission']}
        (ROOT/'expiry.json').write_text(json.dumps(value))
        emit(value)
    elif action == 'recover-expiry-evidence':
        account=http(3001,'/api/summary')[1]['accounts'][0]
        after=account['activity']
        assert account['status']=='stale' and after['sources'][0]['freshness']=='stale'
        assert after['sources'][0]['coverage']=='partial' and not after['sources'][0]['error'] and not after['sources'][0]['auth_error']
        assert 'required_source_stale:browser_discovery' in account['admission']['reasons']
        samples=[json.loads(line) for line in (ROOT/'measurements.jsonl').read_text().splitlines()]
        before=next(s for s in reversed(samples) if s.get('pilot_summary',{}).get('accounts') and
            s['pilot_summary']['accounts'][0]['activity']['observed_at']==after['observed_at'] and
            s['pilot_summary']['accounts'][0]['activity']['sources'][0]['freshness']=='fresh')
        activity=before['pilot_summary']['accounts'][0]['activity']
        assert activity['sources'][0]['coverage']=='partial' and not activity['sources'][0]['error'] and not activity['sources'][0]['auth_error']
        assert [o['identity'] for o in activity['observations']]==[o['identity'] for o in after['observations']]
        value={'case':'healthy_source_natural300s_ttl','before_sample_at':before['time'],
            'validated_at':time.time(),'before':activity,'after':after,'admission':account['admission'],
            'tooling_assertion_corrected':'healthy expiry status is stale, not unknown'}
        (ROOT/'expiry.json').write_text(json.dumps(value,indent=2))
        emit(value)
    elif action == 'faults':
        assert all(not c['State']['Running'] for c in containers()
                   if c['Config']['Labels']['com.docker.compose.service']=='discovery')
        output = compose('run','--rm','--no-deps','-v',str(ROOT/'phase7_faults.py')+
                         ':/worker/phase7_faults.py:ro','discovery','python','phase7_faults.py')
        (ROOT/'faults.json').write_text(output)
        emit(json.loads(output))
        emit({'linux_process_checks':compose('run','--rm','--no-deps','-v',
             str(ROOT/'tests/discovery_linux_check.py')+':/worker/discovery_linux_check.py:ro',
             'discovery','python','discovery_linux_check.py')})
    elif action == 'finish': finish()
    elif action == 'report': report()
    elif action == 'audit': audit()
    else: raise ValueError('unknown operator action')
