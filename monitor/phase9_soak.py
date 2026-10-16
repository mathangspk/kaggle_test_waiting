"""Bounded 48h production evidence observer; no browser or provider operations.

Privileged host process reads only the selected project's Docker metadata/cgroups,
worker checkpoint and local monitor HTTP. No automatic auth repair or job control.
"""
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT=Path('/home/thangma/kaggle-production-phase8/soak')
PROJECT='kaggle-queue-monitor'


def command(args):
    return subprocess.check_output(args,text=True,timeout=10,stderr=subprocess.DEVNULL).strip()


def get(path):
    started=time.monotonic()
    with urllib.request.urlopen('http://127.0.0.1:3000'+path,timeout=5) as r:
        data=r.read(2*1024*1024)
        return {'http':r.status,'ms':round((time.monotonic()-started)*1000,3),
                'data':json.loads(data) if path!='/' else None}


def host():
    mem=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    return {'mem_available_kib':int(mem['MemAvailable'].split()[0]),
            'loadavg':Path('/proc/loadavg').read_text().strip()}


def sample():
    result={'at':time.time(),'containers':[],'alerts':[]}
    result.update(host())
    ids=command(['docker','ps','-aq','--filter','label=com.docker.compose.project='+PROJECT]).split()
    raw=json.loads(command(['docker','inspect',*ids])) if ids else []
    services=set()
    for c in raw:
        service=c['Config']['Labels'].get('com.docker.compose.service')
        if service not in ('monitor','discovery'):continue
        services.add(service)
        item={'service':service,'id':c['Id'],'image':c['Image'],'started':c['State']['StartedAt'],
            'running':c['State']['Running'],'oom_killed':c['State']['OOMKilled'],
            'restarts':c['RestartCount'],'health':c['State'].get('Health',{}).get('Status'),
            'restart_policy':c['HostConfig']['RestartPolicy']['Name'],
            'limits':{k:c['HostConfig'][k] for k in ('Memory','MemorySwap','NanoCpus','PidsLimit','ShmSize')}}
        if not item['running']:result['alerts'].append(service+'_not_running')
        if item['oom_killed']:result['alerts'].append(service+'_oom_killed')
        if item['running']:
            cg=Path('/sys/fs/cgroup/system.slice/docker-'+c['Id']+'.scope')
            item['cgroup']={key:(cg/key).read_text().strip() for key in
                ('memory.current','memory.peak','memory.events','cpu.stat','pids.current','pids.peak') if (cg/key).exists()}
            if not item['cgroup']:result['alerts'].append(service+'_cgroup_unavailable')
        if service=='discovery':
            if item['limits']['Memory']!=1073741824 or item['limits']['MemorySwap']!=1073741824 or item['limits']['NanoCpus']!=750000000:
                result['alerts'].append('discovery_limits_mismatch')
            mount=next(m for m in c['Mounts'] if m['Destination']=='/var/lib/discovery')
            path=Path(mount['Source'])/'state.json'
            if path.exists():
                state=json.loads(path.read_text())
                item['worker']={k:state.get(k) for k in ('last_result','last_success','last_attempt','ingestion','suspended','failures','next_at','metrics')}
                if state.get('suspended'):result['alerts'].append('discovery_suspended')
                if state.get('ingestion')=='ingestion_failed':result['alerts'].append('discovery_ingestion_failed')
                if state.get('last_result')!='partial':result['alerts'].append('discovery_no_partial_success')
                if time.time()-state.get('last_success',0)>600:result['alerts'].append('discovery_success_older600s')
            elif time.time()-__import__('datetime').datetime.fromisoformat(c['State']['StartedAt'].replace('Z','+00:00')).timestamp()>90:
                result['alerts'].append('discovery_checkpoint_missing')
        result['containers'].append(item)
    for missing in {'monitor','discovery'}-services:result['alerts'].append(missing+'_missing')
    for path in ('/healthz','/api/summary','/'):
        try:
            value=get(path);body=value.pop('data')
            result[path]=value
            if path=='/healthz':
                result['health']={k:body.get(k) for k in ('ok','ready','auto_probes','worker_alive','sdk_ready','config_ok')}
                if body.get('auto_probes') is not False:result['alerts'].append('auto_probes_not_false')
                if not body.get('ok'):result['alerts'].append('monitor_unhealthy')
            elif path=='/api/summary':
                result['policy']=body['meta'].get('probe_admission_policy')
                if result['policy']!='observe_only':result['alerts'].append('policy_not_observe_only')
                result['accounts']=[{'username':a['username'],'status':a['status'],
                    'coverage':a.get('activity',{}).get('coverage'),'sources':a.get('activity',{}).get('sources',[]),
                    'observation_count':len(a.get('activity',{}).get('observations',[])),
                    'admission':a.get('admission')} for a in body['accounts']]
        except Exception:result['alerts'].append('http_unavailable:'+path)
    if result['mem_available_kib']<2*1024*1024:result['alerts'].append('host_headroom_below2GiB')
    return result


def save(path,data):
    tmp=path.with_suffix('.tmp')
    with tmp.open('w') as f:json.dump(data,f,indent=2)
    os.chmod(tmp,0o600);os.replace(tmp,path)


def main():
    os.umask(0o077)
    ROOT.mkdir(mode=0o700,exist_ok=True)
    statefile=ROOT/'progress.json'
    state=json.loads(statefile.read_text()) if statefile.exists() else {
        'started':time.time(),'deadline':time.time()+48*3600,'samples':0,'alert_samples':0}
    logger=logging.getLogger('soak');logger.setLevel(logging.INFO)
    handler=RotatingFileHandler(ROOT/'samples.jsonl',maxBytes=4*1024*1024,backupCount=3)
    handler.setFormatter(logging.Formatter('%(message)s'));logger.addHandler(handler)
    prior=None
    while time.time()<state['deadline']:
        try:result=sample()
        except Exception:result={'at':time.time(),'alerts':['observer_sample_failed']}
        logger.info(json.dumps(result,separators=(',',':')))
        handler.flush()
        state['samples']+=1;state['alert_samples']+=bool(result['alerts'])
        state['last_sample']=result['at'];state['elapsed_seconds']=result['at']-state['started']
        state['last_alerts']=result['alerts'];state['complete']=False
        save(ROOT/'latest.json',result);save(statefile,state)
        alerts=sorted(result['alerts'])
        if alerts!=prior:
            print(json.dumps({'event':'soak_alert_transition','at':result['at'],'alerts':alerts}),flush=True)
            prior=alerts
        time.sleep(min(60,max(0,state['deadline']-time.time())))
    state['complete']=True;state['ended']=time.time();save(statefile,state)
    print('{"event":"bounded_soak_observer_completed"}',flush=True)


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='once':print(json.dumps(sample(),sort_keys=True))
    else:main()
