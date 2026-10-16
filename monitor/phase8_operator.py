"""Production integration operator; invoked via existing remote.py private stdin.

Only sanitized evidence is emitted. Full inspect/config and SQLite backup remain
root-private on the approved server. Never invokes provider launch/cancel methods.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0,str(Path(__file__).parent))
import phase7_operator as p

ROOT=Path('/home/thangma/kaggle-production-phase8')
LIVE=Path('/home/thangma/kaggle-queue-monitor')
PROJECT='kaggle-queue-monitor'


def emit(value):
    print(json.dumps(value,sort_keys=True),flush=True)


def private_json(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f: json.dump(value,f,indent=2)


def inspect_services():
    ids=p.docker('ps','-aq','--filter','label=com.docker.compose.project='+PROJECT).split()
    return json.loads(p.docker('inspect',*ids)) if ids else []


def monitor():
    return next(c for c in inspect_services() if c['Config']['Labels'].get('com.docker.compose.service')=='monitor')


def env(c):
    return dict(v.split('=',1) for v in c['Config']['Env'] if '=' in v)


def database(c):
    return Path(next(m['Source'] for m in c['Mounts'] if m['Destination']=='/data'))/'monitor.sqlite3'


def dbfacts(path):
    with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        result={'integrity':db.execute('PRAGMA integrity_check').fetchone()[0],
            'counts':{t:db.execute('SELECT count(*) FROM "'+t+'"').fetchone()[0]
                      for t in tables if t in ('runs','calls','production_events','activity_evidence')},
            'run_states':dict(db.execute('SELECT state,count(*) FROM runs GROUP BY state')),
            'submit_attempts':db.execute("SELECT count(*) FROM runs WHERE json_extract(data,'$.submit_begin') IS NOT NULL").fetchone()[0],
            'active':db.execute('SELECT count(*) FROM runs WHERE active=1').fetchone()[0]}
        return result


def inventory():
    assert not (ROOT/'deployed.json').exists(),'use evidence for deployed state; preserve original baseline'
    assert not any(c['State']['Running'] for c in p.containers()),'isolated pilot must stay stopped'
    ROOT.mkdir(mode=0o700,exist_ok=True)
    os.chmod(ROOT,0o700)
    c=monitor()
    values=env(c)
    result={'time':time.time(),'host':p.host(),'monitor':{'id':c['Id'],'image':c['Image'],
        'started':c['State']['StartedAt'],'restart_count':c['RestartCount'],
        'policy':{k:values.get(k) for k in ('AUTO_PROBES','PROBE_ADMISSION_POLICY','CALL_TIMEOUT','SAMPLING_INTERVAL_MINUTES')},
        'restart_policy':c['HostConfig']['RestartPolicy'],
        'mounts':[{'source':m['Source'],'destination':m['Destination']} for m in c['Mounts']]},
        'database':dbfacts(database(c)),'health':p.http(3000,'/healthz')[1]}
    private_json(ROOT/'baseline.json',result)
    private_json(ROOT/'baseline-inspect.private.json',c)
    emit(result)


def backup(label):
    c=monitor()
    target=ROOT/('backup-'+label+'-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()))
    target.mkdir(mode=0o700)
    src=database(c)
    dst=target/'monitor.sqlite3'
    fd=os.open(dst,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    with sqlite3.connect('file:'+str(src)+'?mode=ro',uri=True) as source,sqlite3.connect(dst) as out:
        source.backup(out)
        assert out.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    for name in ('compose.yaml','.env','Dockerfile'):
        if (LIVE/name).is_file():
            shutil.copy2(LIVE/name,target/name)
            os.chmod(target/name,0o600)
    private_json(target/'container-inspect.private.json',c)
    image_tag='kaggle-phase8-rollback:pre-integration'
    p.docker('tag',c['Image'],image_tag)
    # Immutable image export covers later cache cleanup; no environment/credentials
    # are embedded by docker save. Private inspect/config are never printed.
    p.docker('save','-o',str(target/'monitor-image.tar'),image_tag)
    os.chmod(target/'monitor-image.tar',0o600)
    result={'directory':str(target),'database':dbfacts(dst),'image_id':c['Image'],
        'image_tag':image_tag,'sqlite_sha256':hashlib.sha256(dst.read_bytes()).hexdigest()}
    private_json(target/'manifest.json',result)
    private_json(ROOT/('backup-'+label+'.json'),result)
    emit(result)


def compose(*args):
    return p.run(['docker','compose','-p',PROJECT,'--project-directory',str(LIVE),
        '--env-file',str(LIVE/'.env'),'-f',str(ROOT/'compose.production.yaml'),*args])


def prepare():
    assert not (ROOT/'deployed.json').exists(),'already deployed; preserve original rollback manifest'
    c=monitor();values=env(c)
    manifest=json.loads((p.ROOT/'image-manifest.json').read_text())
    # Local image/source was just rebuilt and locally tested through the Phase 7
    # allowlisted build route; deployment pins IDs, never mutable tags.
    source=Path('/home/thangma/.local/share/kaggle-monitor-phase7/token')
    assert (source/'token').stat().st_mode&0o777==0o600
    account_mount=next(m for m in c['Mounts'] if m['Destination']=='/run/secrets/accounts.env')
    volume=next(m for m in c['Mounts'] if m['Destination']=='/data')
    assert volume['Type']=='volume'
    config={'name':PROJECT,'services':{
        'monitor':{'image':manifest['monitor_image'],'restart':'unless-stopped','init':True,
            'stop_grace_period':'220s','ports':['127.0.0.1:3000:3000'],
            'environment':{'AUTO_PROBES':'false','PROBE_ADMISSION_POLICY':'observe_only',
                'CALL_TIMEOUT':values.get('CALL_TIMEOUT','90'),'MIN_RANK_DAYS':values.get('MIN_RANK_DAYS','3'),
                'SAMPLING_INTERVAL_MINUTES':values.get('SAMPLING_INTERVAL_MINUTES','30'),
                'MONITOR_INGEST_TOKEN':'${MONITOR_INGEST_TOKEN:-}',
                'MONITOR_DB':'/data/monitor.sqlite3','ACCOUNTS_ENV':'/run/secrets/accounts.env',
                'DISCOVERY_INGEST_TOKEN_FILE':'/run/discovery-token/token'},
            'volumes':['monitor-data:/data',account_mount['Source']+':/run/secrets/accounts.env:ro',
                       str(source)+':/run/discovery-token:ro'],
            'healthcheck':{'test':['CMD','python','-c',"import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/healthz',timeout=5)"],
                'interval':'30s','timeout':'10s','start_period':'60s','retries':3}},
        'discovery':{'image':manifest['worker_image'],'restart':'no','init':True,'user':'1000:1000',
            'read_only':True,'cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],
            'mem_limit':'1g','memswap_limit':'1g','cpus':.75,'pids_limit':128,'shm_size':'128m',
            'stop_grace_period':'65s','environment':{'DISCOVERY_ENABLED':'true'},
            'volumes':['/home/thangma/.local/share/kaggle-monitor-auth/thangma:/private/thangma',
                str(source)+':/run/discovery-token:ro','discovery-data:/var/lib/discovery'],
            'tmpfs':['/tmp:size=192m,mode=1777'],
            'healthcheck':{'test':['CMD','python','-c',"import json,time; from pathlib import Path; s=json.loads(Path('/var/lib/discovery/state.json').read_text()); assert not s.get('suspended') and s.get('ingestion')=='ok' and time.time()-s.get('last_success',0)<600"],
                'interval':'60s','timeout':'5s','start_period':'90s','retries':2}}},
        'volumes':{'monitor-data':{'external':True,'name':volume['Name']},'discovery-data':{}}}
    # Verify existing production token interpolation matches runtime without printing.
    configpath=ROOT/'compose.production.yaml'
    private_json(configpath,config) # JSON is valid YAML; file contains only paths/refs.
    merged=json.loads(compose('config','--format','json'))
    expected_token=values.get('MONITOR_INGEST_TOKEN','')
    assert merged['services']['monitor']['environment']['MONITOR_INGEST_TOKEN']==expected_token,'existing token interpolation mismatch'
    w=merged['services']['discovery'];m=merged['services']['monitor']
    assert int(w['mem_limit'])==int(w['memswap_limit'])==1073741824 and float(w['cpus'])==.75
    assert m['environment']['AUTO_PROBES']=='false' and m['environment']['PROBE_ADMISSION_POLICY']=='observe_only'
    assert m['volumes'][0]['source']=='monitor-data'
    # Restore old runtime configuration exactly without dependency on ambient .env.
    old=dict(config);old=json.loads(json.dumps(old));old['services'].pop('discovery')
    old['volumes'].pop('discovery-data')
    oldm=old['services']['monitor'];oldm['image']=c['Image']
    oldm['environment']={k:values[k] for k in ('AUTO_PROBES','MIN_RANK_DAYS','CALL_TIMEOUT',
        'SAMPLING_INTERVAL_MINUTES','MONITOR_DB','ACCOUNTS_ENV','PROBE_ADMISSION_POLICY') if k in values}
    oldm['environment']['MONITOR_INGEST_TOKEN']='${MONITOR_INGEST_TOKEN:-}'
    oldm['volumes']=['monitor-data:/data',account_mount['Source']+':/run/secrets/accounts.env:ro']
    private_json(ROOT/'compose.rollback.private.yaml',old)
    private_json(ROOT/'deployment-manifest.json',{'images':manifest,'data_volume':volume['Name'],
        'old_policy':{k:values.get(k) for k in ('AUTO_PROBES','PROBE_ADMISSION_POLICY')},
        'new_policy':{'AUTO_PROBES':'false','PROBE_ADMISSION_POLICY':'observe_only'},
        'worker_restart':'no: durable suspension; no browser restart storm',
        'monitor_restart':'unless-stopped'})
    emit({'config_validated':True,'data_volume':volume['Name'],'images':manifest,
        'old_auto_probes':values.get('AUTO_PROBES'),'new_auto_probes':'false',
        'accounts_preserved':True,'discovery_account':'thangma'})


def verify_history():
    baseline=json.loads((ROOT/'backup-quiesced.json').read_text())
    old=Path(baseline['directory'])/'monitor.sqlite3'
    current=database(monitor())
    with sqlite3.connect(old) as before,sqlite3.connect('file:'+str(current)+'?mode=ro',uri=True) as after:
        result={}
        for table,key in (('runs','id'),('calls','id'),('production_events','event_id')):
            exists=before.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone()
            if not exists: continue
            ids={r[0] for r in before.execute('SELECT '+key+' FROM '+table)}
            now={r[0] for r in after.execute('SELECT '+key+' FROM '+table)}
            assert ids<=now,table+' history lost'
            result[table]={'old':len(ids),'now':len(now),'all_prior_ids_retained':True}
        terminal=0
        for run_id,state,data in before.execute('SELECT id,state,data FROM runs'):
            if state in ('scheduled','skipped','pushing','ambiguous','submitted','queued','running','collecting','timed_out'):continue
            current_row=after.execute('SELECT state,data FROM runs WHERE id=?',(run_id,)).fetchone()
            prior=json.loads(data);now=json.loads(current_row[1])
            assert current_row[0]==state and all(k in now and now[k]==v for k,v in prior.items()),'historical terminal data changed'
            terminal+=1
        columns=[r[1] for r in before.execute('PRAGMA table_info(calls)')]
        call_rows=list(before.execute('SELECT '+','.join(columns)+' FROM calls ORDER BY id'))
        current_calls=dict((r[0],r) for r in after.execute('SELECT '+','.join(columns)+' FROM calls ORDER BY id'))
        assert all(current_calls.get(row[0])==row for row in call_rows),'historical call data changed'
        result['content_retention']={'terminal_runs_all_prior_fields_unchanged':terminal,
            'calls_all_prior_columns_unchanged':len(call_rows)}
        assert after.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    emit({'history':result,'database':dbfacts(current)})
    private_json(ROOT/'history-verification.json',result)


def deploy():
    assert not (ROOT/'deployed.json').exists(),'already deployed; do not duplicate/recreate services'
    assert (ROOT/'backup-online.json').exists() and (ROOT/'compose.rollback.private.yaml').exists()
    assert not any(c['State']['Running'] for c in p.containers())
    c=monitor()
    # Graceful shutdown prevents an in-flight SDK operation/SQLite migration race.
    p.docker('stop','--time','220',c['Id'])
    assert not monitor()['State']['Running']
    backup('quiesced')
    try:
        compose('up','-d','--no-build','monitor')
        for _ in range(60):
            try:
                health=p.http(3000,'/healthz')[1]
                if health['ok'] and health['config_ok'] and health['sdk_ready']: break
            except Exception: pass
            time.sleep(2)
        else: raise RuntimeError('production_health_not_ready')
        assert health['auto_probes'] is False
        verify_history()
        compose('up','-d','--no-build','discovery')
        shutil.copy2(ROOT/'compose.production.yaml',LIVE/'compose.yaml')
        os.chmod(LIVE/'compose.yaml',0o600)
        private_json(ROOT/'deployed.json',{'started':time.time(),'images':json.loads((ROOT/'deployment-manifest.json').read_text())['images']})
        emit({'deployed':True,'health':health,'containers':[(c['Name'],c['Image']) for c in inspect_services()]})
    except Exception:
        rollback()
        raise


def update_images():
    """Non-destructive image swap for the reviewed discovery fix only.

    Every unrelated monitor artifact must be byte-identical to the running image,
    and the freshly built Phase 7 candidate images must embed the uploaded source,
    so the only shipped delta is the reviewed discovery contract/visit change.
    """
    assert (ROOT/'deployed.json').exists(),'no deployment to update'
    deployed=json.loads((ROOT/'deployed.json').read_text())
    old=deployed['images']
    new=json.loads((p.ROOT/'image-manifest.json').read_text())
    assert new['monitor_image']!=old['monitor_image'] or new['worker_image']!=old['worker_image']
    def digest(image,path):
        return p.docker('run','--rm','--entrypoint','sha256sum',image,path).split()[0]
    def source(name):
        return hashlib.sha256((p.ROOT/name).read_bytes()).hexdigest()
    for name in ('app.py','requirements.txt','roadmap.json'):
        assert digest(new['monitor_image'],'/app/'+name)==digest(old['monitor_image'],'/app/'+name),\
            'unexpected monitor change: '+name
    assert digest(new['monitor_image'],'/app/app.py')==source('app.py')
    assert digest(new['monitor_image'],'/app/discovery_contract.py')==source('discovery_contract.py')
    assert digest(new['monitor_image'],'/app/discovery_contract.py')!=digest(old['monitor_image'],'/app/discovery_contract.py')
    for name in ('discovery_visit.py','discovery_worker.py','discovery_contract.py'):
        assert digest(new['worker_image'],'/worker/'+name)==source(name),'worker image/source mismatch: '+name
    assert digest(new['worker_image'],'/worker/discovery_visit.py')!=digest(old['worker_image'],'/worker/discovery_visit.py')
    backup('pre-update')
    compose('stop','discovery')
    volume=Path('/var/lib/docker/volumes/'+PROJECT+'_discovery-data/_data/state.json')
    if volume.exists():
        state=json.loads(volume.read_text())
        private_json(ROOT/'worker-state-preupdate.json',state)
        state.pop('next_at',None);state.pop('failures',None)
        fd=os.open(volume,os.O_WRONLY|os.O_TRUNC)
        with os.fdopen(fd,'w') as f: json.dump(state,f)
        os.chmod(volume,0o600);os.chown(volume,1000,1000)
    config=json.loads((ROOT/'compose.production.yaml').read_text())
    config['services']['monitor']['image']=new['monitor_image']
    config['services']['discovery']['image']=new['worker_image']
    private_json(ROOT/'compose.production.yaml',config)
    manifest=json.loads((ROOT/'deployment-manifest.json').read_text());manifest['images']=new
    private_json(ROOT/'deployment-manifest.json',manifest)
    compose('up','-d','--no-build','monitor','discovery')
    for _ in range(60):
        try:
            health=p.http(3000,'/healthz')[1]
            if health['ok'] and health['config_ok'] and health['sdk_ready']: break
        except Exception: pass
        time.sleep(2)
    else: raise RuntimeError('production_health_not_ready')
    assert health['auto_probes'] is False
    verify_history()
    private_json(ROOT/'deployed.json',{'started':deployed.get('started'),'images':new,
        'updated':time.time()})
    emit({'updated':True,'old_images':old,'images':new,'health':health,
          'policy':{'AUTO_PROBES':'false','PROBE_ADMISSION_POLICY':'observe_only'}})


def thangma():
    return next(a for a in p.http(3000,'/api/summary')[1]['accounts'] if a['username']=='thangma')


def faults():
    # Production-image supervision/auth faults; never ingests to the live monitor.
    worker=next(c for c in inspect_services() if c['Config']['Labels'].get('com.docker.compose.service')=='discovery')
    was_running=worker['State']['Running']
    if was_running:compose('stop','discovery')
    try:
        output=compose('run','--rm','--no-deps','-v',str(p.ROOT/'phase11_faults.py')+
            ':/worker/phase11_faults.py:ro','discovery','python','phase11_faults.py')
        private_json(ROOT/'phase11-faults.json',json.loads(output));emit(json.loads(output))
    finally:
        if was_running:compose('start','discovery')


def restart_persistence():
    # Monitor restart must not lose persisted observations; worker restart must
    # reuse disk auth, resume scheduled visits and add no submission/SDK call.
    before=thangma()['activity'];before_db=dbfacts(database(monitor()))
    compose('restart','monitor')
    for _ in range(60):
        try:
            if p.http(3000,'/healthz')[1]['ok']:break
        except Exception:pass
        time.sleep(1)
    else:raise RuntimeError('monitor_not_ready_after_restart')
    after_monitor=thangma()['activity']
    assert after_monitor['observations']==before['observations'],'monitor restart lost observations'
    assert after_monitor['observed_at']==before['observed_at']
    started=time.time()
    compose('restart','discovery')
    from datetime import datetime
    for _ in range(84):
        sources=thangma()['activity']['sources']
        if (sources and sources[0]['coverage']=='partial' and not sources[0]['error']
                and not sources[0]['auth_error']
                and datetime.fromisoformat(sources[0]['observed_at']).timestamp()>started):
            after=thangma()['activity'];after_db=dbfacts(database(monitor()))
            assert after_db['submit_attempts']==before_db['submit_attempts'],'new submission after restart'
            assert after_db['counts']['calls']==before_db['counts']['calls'],'new sdk call after restart'
            value={'case':'production_monitor_and_worker_restart','monitor_observations_preserved':True,
                'before_observed_at':before['observed_at'],'after_observed_at':after['observed_at'],
                'observations_before':len(before['observations']),'observations_after':len(after['observations']),
                'auth_reused_no_error':True,'submit_attempts_unchanged':True,'calls_unchanged':True,
                'auto_probes':p.http(3000,'/healthz')[1]['auto_probes']}
            private_json(ROOT/'restart-persistence.json',value);emit(value);return
        time.sleep(5)
    raise RuntimeError('no_fresh_success_after_worker_restart')


def ttl_expiry():
    # Natural 300s evidence TTL on the production integration; no clock or DB edit.
    from datetime import datetime
    account=thangma();before=account['activity'];src=before['sources'][0]
    assert src['coverage']=='partial' and src['freshness']=='fresh'
    assert not src['error'] and not src['auth_error']
    compose('stop','discovery')
    wait=max(0,datetime.fromisoformat(src['observed_at']).timestamp()+312-time.time())
    time.sleep(wait+3)
    account=thangma();after=account['activity'];asrc=after['sources'][0]
    assert asrc['freshness']=='stale'
    assert asrc['coverage']=='partial' and not asrc['error'] and not asrc['auth_error']
    assert after['observed_at']==before['observed_at']
    assert account['status']=='stale'
    assert 'required_source_stale:browser_discovery' in account['admission']['reasons']
    value={'case':'production_natural_300s_evidence_ttl_expiry','wait_seconds':round(wait,1),
        'before':before,'after':after,'admission':account['admission']}
    private_json(ROOT/'ttl-expiry.json',value);emit(value)
    emit({'worker_restart':compose('start','discovery')})


def rollback():
    # Config/image rollback retains post-deployment records; additive schema does
    # not require destructive restore. Offline backup restore is separate recovery.
    for c in inspect_services():
        if c['Config']['Labels'].get('com.docker.compose.service')=='discovery' and c['State']['Running']:
            p.docker('stop','--time','65',c['Id'])
    p.run(['docker','compose','-p',PROJECT,'--project-directory',str(LIVE),'--env-file',str(LIVE/'.env'),'-f',
        str(ROOT/'compose.rollback.private.yaml'),'up','-d','--no-build','monitor'])
    # Canonical rollback has secret references only; existing private env supplies it.
    shutil.copy2(ROOT/'compose.rollback.private.yaml',LIVE/'compose.yaml')
    os.chmod(LIVE/'compose.yaml',0o600)
    emit({'rollback':'old image/runtime environment restored; current DB retained',
          'prior_auto_probes_restored':True})


if __name__=='__main__':
    action=sys.argv[1]
    if action=='inventory': inventory()
    elif action=='backup': backup('online')
    elif action=='prepare': prepare()
    elif action=='deploy': deploy()
    elif action=='update-images': update_images()
    elif action=='faults': faults()
    elif action=='restart-persistence': restart_persistence()
    elif action=='ttl-expiry': ttl_expiry()
    elif action=='history': verify_history()
    elif action=='inspect-panel':
        # Stop only scheduled production discovery; monitor stays up. One bounded
        # diagnostic uses the same limits/auth and restores the prior running worker.
        worker=next(c for c in inspect_services() if c['Config']['Labels'].get('com.docker.compose.service')=='discovery')
        was_running=worker['State']['Running']
        if was_running:compose('stop','discovery')
        try:
            output=compose('run','--rm','--no-deps','-v',str(Path(__file__).with_name('phase11_collect.py'))+
                ':/worker/phase11_collect.py:ro','discovery','python','phase11_collect.py')
            private_json(ROOT/'panel-shape.json',json.loads(output));emit(json.loads(output))
        finally:
            if was_running:compose('start','discovery')
    elif action=='collect-live':
        # Overlay the newest uploaded discovery modules on the pinned image so the
        # candidate fix is exercised against the live panel without a rebuild.
        worker=next(c for c in inspect_services() if c['Config']['Labels'].get('com.docker.compose.service')=='discovery')
        was_running=worker['State']['Running']
        if was_running:compose('stop','discovery')
        try:
            src=p.ROOT
            output=compose('run','--rm','--no-deps',
                '-v',str(src/'discovery_visit.py')+':/worker/discovery_visit.py:ro',
                '-v',str(src/'discovery_contract.py')+':/worker/discovery_contract.py:ro',
                '-v',str(src/'phase11_collect.py')+':/worker/phase11_collect.py:ro',
                'discovery','python','phase11_collect.py')
            private_json(ROOT/'collect-live.json',json.loads(output));emit(json.loads(output))
        finally:
            if was_running:compose('start','discovery')
    elif action=='install-operator':
        target=ROOT/'ops';target.mkdir(mode=0o700,exist_ok=True)
        for name in ('phase8_operator.py','phase7_operator.py','phase9_soak.py'):
            shutil.copy2(Path(__file__).with_name(name),target/name)
            os.chown(target/name,0,0);os.chmod(target/name,0o600)
        emit({'retained_operator':str(target/'phase8_operator.py'),'root_private':True})
    elif action=='canonical-config':
        # Installed after successful runtime validation as well as during deploy.
        shutil.copy2(ROOT/'compose.production.yaml',LIVE/'compose.yaml')
        os.chmod(LIVE/'compose.yaml',0o600)
        emit({'canonical_compose':str(LIVE/'compose.yaml'),'secret_free':True,
              'auto_probes':'false','policy':'observe_only'})
    elif action=='sanitize-rollback':
        path=ROOT/'compose.rollback.private.yaml'
        config=json.loads(path.read_text());values=config['services']['monitor']['environment']
        config['services']['monitor']['environment']={k:values[k] for k in ('AUTO_PROBES','MIN_RANK_DAYS',
            'CALL_TIMEOUT','SAMPLING_INTERVAL_MINUTES','MONITOR_DB','ACCOUNTS_ENV','PROBE_ADMISSION_POLICY') if k in values}
        config['services']['monitor']['environment']['MONITOR_INGEST_TOKEN']='${MONITOR_INGEST_TOKEN:-}'
        private_json(path,config)
        p.run(['docker','compose','-p',PROJECT,'--project-directory',str(LIVE),'--env-file',str(LIVE/'.env'),
            '-f',str(path),'config','--quiet'])
        emit({'rollback_compose_secret_references_only':True,'syntax_validated':True})
    elif action=='rollback-rehearsal':
        # Old image opens a *copy* of the migrated DB offline, not the live DB.
        scratch=ROOT/'rollback-rehearsal';scratch.mkdir(mode=0o700,exist_ok=True)
        target=scratch/'monitor.sqlite3'
        with sqlite3.connect('file:'+str(database(monitor()))+'?mode=ro',uri=True) as src,sqlite3.connect(target) as dst:
            src.backup(dst)
        os.chmod(target,0o600)
        old=json.loads((ROOT/'backup-quiesced.json').read_text())
        code="from app import Store; import sqlite3,json; s=Store('/data/monitor.sqlite3'); d=sqlite3.connect('/data/monitor.sqlite3'); assert d.execute('PRAGMA integrity_check').fetchone()[0]=='ok'; print(json.dumps({'old_image_store_opened_migrated_copy':True,'runs':d.execute('SELECT count(*) FROM runs').fetchone()[0],'calls':d.execute('SELECT count(*) FROM calls').fetchone()[0]}))"
        output=p.docker('run','--rm','--network','none','--read-only','-e','AUTO_PROBES=false',
            '-e','PYTHONDONTWRITEBYTECODE=1','-v',str(scratch)+':/data',old['image_id'],'python','-c',code)
        value=json.loads(output);private_json(ROOT/'rollback-rehearsal.json',value);emit(value)
    elif action=='rollback': rollback()
    elif action=='start-soak':
        # Copy observer into root-owned private operational directory, independent
        # from the mutable upload/build context. systemd restarts only observer.
        target=ROOT/'phase9_soak.py'
        changed=not target.exists() or target.read_bytes()!=Path(__file__).with_name('phase9_soak.py').read_bytes()
        shutil.copy2(Path(__file__).with_name('phase9_soak.py'),target)
        os.chown(target,0,0);os.chmod(target,0o600)
        active=subprocess.run(['systemctl','is-active','--quiet','kaggle-discovery-soak.service']).returncode==0
        if active and changed:p.run(['systemctl','restart','kaggle-discovery-soak.service'])
        if not active:
            p.run(['systemd-run','--unit=kaggle-discovery-soak','--property=Restart=on-failure',
                '--property=RestartSec=30','--property=RuntimeMaxSec=173000',
                '--property=MemoryMax=128M','--property=CPUQuota=5%',
                '--property=TasksMax=32','--property=UMask=0077',
                '/usr/bin/python3',str(target)])
        emit({'observer_unit':'kaggle-discovery-soak.service','duration_hours':48,
              'interval_seconds':60,'retention':'4 files x4MiB','alerts':'local journal transitions + private latest/progress',
              'recovery':'observer restart only; no browser/auth/job automation'})
    elif action=='evidence':
        script=ROOT/'phase9_soak.py'
        result=json.loads(p.run(['/usr/bin/python3',str(script),'once']))
        private_json(ROOT/'immediate-evidence.json',result)
        emit(result)
        progress=ROOT/'soak/progress.json'
        if progress.exists():emit({'soak_progress':json.loads(progress.read_text())})
    elif action=='soak-summary':
        directory=ROOT/'soak'
        samples=[]
        for file in directory.glob('samples.jsonl*'):
            samples += [json.loads(line) for line in file.read_text().splitlines() if line.strip()]
        samples.sort(key=lambda s:s['at'])
        groups={};cycles={}
        for s in samples:
            for c in s.get('containers',[]):
                group=groups.setdefault(c['service'],{'peak_bytes':0,'peak_pids':0,'oom_kill_max':0,
                    'cpu_usage_usec':0,'throttled_usec':0})
                cg=c.get('cgroup',{})
                if cg:
                    group['peak_bytes']=max(group['peak_bytes'],int(cg['memory.peak']))
                    group['peak_pids']=max(group['peak_pids'],int(cg['pids.peak']))
                    cpu=dict(line.split() for line in cg['cpu.stat'].splitlines())
                    events=dict(line.split() for line in cg['memory.events'].splitlines())
                    group['cpu_usage_usec']=max(group['cpu_usage_usec'],int(cpu['usage_usec']))
                    group['throttled_usec']=max(group['throttled_usec'],int(cpu['throttled_usec']))
                    group['oom_kill_max']=max(group['oom_kill_max'],int(events['oom_kill']))
                worker=c.get('worker',{})
                if worker.get('last_attempt'):cycles[worker['last_attempt']]=worker
        timings={}
        for path in ('/healthz','/api/summary','/'):
            values=sorted(s[path]['ms'] for s in samples if path in s)
            if values:timings[path]={'n':len(values),'p50_ms':values[len(values)//2],
                'p95_ms':values[min(len(values)-1,int(len(values)*.95))],'max_ms':values[-1]}
        value={'progress':json.loads((directory/'progress.json').read_text()),'retained_samples':len(samples),
            'min_mem_available_kib':min(s['mem_available_kib'] for s in samples if 'mem_available_kib' in s),
            'resource_high_water':groups,'cycles':list(cycles.values()),'http':timings,
            'evidence_bytes':sum(f.stat().st_size for f in directory.iterdir() if f.is_file())}
        private_json(ROOT/'soak-summary.json',value);emit(value)
    elif action=='verify-final':
        baseline=json.loads((ROOT/'backup-quiesced.json').read_text())['database']
        current=dbfacts(database(monitor()))
        assert current['submit_attempts']==baseline['submit_attempts']
        assert current['counts']['calls']==baseline['counts']['calls']
        assert current['active']==0
        verify_history()
        manifest=json.loads((ROOT/'deployment-manifest.json').read_text())['images']
        evidence=[]
        for c in inspect_services():
            service=c['Config']['Labels'].get('com.docker.compose.service')
            assert c['Image']==manifest['worker_image' if service=='discovery' else 'monitor_image']
            modules=['app.py','discovery_contract.py'] if service=='monitor' else ['discovery_visit.py','discovery_worker.py','discovery_contract.py']
            hashes=json.loads(p.docker('exec',c['Id'],'python','-c',
                'import hashlib,json;from pathlib import Path; print(json.dumps({n:hashlib.sha256(Path(n).read_bytes()).hexdigest() for n in '+repr(modules)+'}))'))
            for name,digest in hashes.items():
                assert digest==hashlib.sha256((p.ROOT/name).read_bytes()).hexdigest(),'uploaded source changed: '+name
            evidence.append({'service':service,'id':c['Id'],'image':c['Image'],
                'source_hashes_verified':hashes,
                'running':c['State']['Running'],'health':c['State'].get('Health',{}).get('Status'),
                'restart':c['HostConfig']['RestartPolicy'],'user':c['Config']['User'],
                'readonly':c['HostConfig']['ReadonlyRootfs'],'init':c['HostConfig']['Init'],
                'ports':c['HostConfig']['PortBindings'],'networks':list(c['NetworkSettings']['Networks']),
                'cap_drop':c['HostConfig']['CapDrop'],'security_opt':c['HostConfig']['SecurityOpt']})
        other_before=json.loads((p.ROOT/'baseline.json').read_text())['production']
        other_now=p.production()
        preserved=all(any(a==b for b in other_now) for a in other_before if a['name']!='/kaggle-queue-monitor-monitor-1')
        assert preserved
        value={'database':current,'no_new_submissions_or_sdk_calls':True,'containers':evidence,
            'other_services_unchanged':preserved,'pilot_running':any(c['State']['Running'] for c in p.containers()),
            'systemd':p.run(['systemctl','show','kaggle-discovery-soak.service','--property=ActiveState,SubState,MemoryCurrent,MemoryPeak,CPUUsageNSec,TasksCurrent,NRestarts,RuntimeMaxUSec,MemoryMax,CPUQuotaPerSecUSec'])}
        private_json(ROOT/'final-verification.json',value);emit(value)
    else: raise ValueError('unknown action')
