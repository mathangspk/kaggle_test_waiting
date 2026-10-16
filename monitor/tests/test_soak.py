"""Offline operational observer boundaries; no Docker/provider/browser operations."""
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).parents[1]))
import phase9_soak as soak


def test_suspended_private_checkpoint_alerts_without_emitting_outbox_or_environment(tmp_path,monkeypatch):
    monkeypatch.setattr(soak,'host',lambda:{'mem_available_kib':25*1024*1024,'loadavg':'0 0 0'})
    secret='private-outbox-token-must-never-appear'
    (tmp_path/'state.json').write_text(json.dumps({'outbox':{'token':secret},
        'suspended':'authentication_required','last_result':'authentication_required',
        'ingestion':'ingestion_failed','last_success':0,'last_attempt':1,'next_at':2}))
    containers=[{'Id':'test','Config':{'Env':['SECRET='+secret],
        'Labels':{'com.docker.compose.service':'discovery'}},
        'State':{'Running':False,'OOMKilled':False,'StartedAt':'2026-10-16T00:00:00Z'},
        'Image':'sha256:test','RestartCount':0,
        'HostConfig':{'RestartPolicy':{'Name':'no'},'Memory':1073741824,
            'MemorySwap':1073741824,'NanoCpus':750000000,'PidsLimit':128,'ShmSize':134217728},
        'Mounts':[{'Source':str(tmp_path),'Destination':'/var/lib/discovery'}]}]
    monkeypatch.setattr(soak,'command',lambda args:json.dumps(containers) if 'inspect' in args else 'test')
    def get(path):
        return {'http':200,'ms':1,'data':({'auto_probes':False,'ok':True} if path=='/healthz' else
            {'meta':{'probe_admission_policy':'observe_only'},'accounts':[]} if path=='/api/summary' else None)}
    monkeypatch.setattr(soak,'get',get)
    result=soak.sample()
    assert secret not in json.dumps(result) and 'outbox' not in json.dumps(result)
    assert {'discovery_suspended','discovery_ingestion_failed','discovery_not_running',
            'discovery_success_older600s','monitor_missing'}<=set(result['alerts'])


def test_observer_alerts_on_unexpected_launch_policy_and_http_failure(monkeypatch):
    monkeypatch.setattr(soak,'host',lambda:{'mem_available_kib':25*1024*1024,'loadavg':'0 0 0'})
    monkeypatch.setattr(soak,'command',lambda _: '')
    def get(path):
        if path=='/':raise TimeoutError('private raw error')
        return {'http':200,'ms':1,'data':({'auto_probes':True,'ok':True} if path=='/healthz' else
            {'meta':{'probe_admission_policy':'enforce'},'accounts':[]})}
    monkeypatch.setattr(soak,'get',get)
    result=soak.sample()
    assert {'auto_probes_not_false','policy_not_observe_only','http_unavailable:/'}<=set(result['alerts'])
    assert 'private raw error' not in json.dumps(result)
