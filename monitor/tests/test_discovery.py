"""Offline discovery boundary, delayed-panel, durability and process-tree checks."""
import asyncio
import json
from pathlib import Path
import random
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import discovery_contract as contract
import discovery_visit as visit
import discovery_worker as worker
from test_monitor import m, analysis_client, insert_scheduled


HREF = "https://www.kaggle.com/code/thangma/queue-monitor-probe?scriptVersionId=358145984"


def test_interactive_shared_ancestor_fails_before_popup_navigation():
    panel = DelayedPanel()
    async def rows(_):
        return [{'href':'','text':'Running Version #158 Successful Version #157 Successful'}]
    async def visible():return 'Version #158 Successful Version #157 Successful'
    async def resolve(*_):pytest.fail('ambiguous interactive row must not open editor')
    panel.evaluate_all=rows
    panel.inner_text=visible
    with pytest.raises(ValueError,match='panel_unavailable'):
        asyncio.run(visit.collect_panel(panel,panel.pause,resolve))


def test_display_hint_cannot_authorize_wrong_or_mixed_current_user():
    for raw in ({'userName':'other','displayName':'Thang Ma'},
                {'userName':'thangma','displayName':'Thang Ma','nested':{'username':'other'}},
                {'displayName':'Thang Ma'}):
        with pytest.raises(ValueError):
            visit.identity_hint(raw)
    assert visit.identity_hint({'userName':'thangma','displayName':'Changed display name'}) == 'Changed display name'


def test_correct_username_and_display_hint_cannot_override_wrong_profile_href():
    class Profile:
        async def wait_for(self, **_): pass
        async def get_attribute(self, _): return '/other'
        async def click(self, **_): pass
    class Page:
        def get_by_role(self, *_args, **_kwargs): return Profile()
    hint = visit.identity_hint({'userName':'thangma','displayName':'Thang Ma'})
    with pytest.raises(ValueError, match='identity_mismatch'):
        asyncio.run(visit.verify_profile(Page(),hint))


def snapshot(at=1000, rows=True):
    return {"account": "thangma", "observed_at": at, "coverage": "partial",
            "observations": [contract.event(HREF, "Version #144 Running", at)] if rows else [],
            "error": None, "auth_error": None}


@pytest.mark.parametrize("href,text", [
    (HREF.replace("thangma", "other"), "Version #144 Running"),
    (HREF.replace("www.kaggle.com", "evil.example"), "Version #144 Running"),
    (HREF.replace("358145984", "0"), "Version #144 Running"),
    (HREF + "&scriptVersionId=123", "Version #144 Running"),
    (HREF.split("?")[0], "Version #144 Running"),
    (HREF, "Running"), (HREF, "Version #1 Version #2 Running"),
    (HREF, "Version #144 Successful Running"),
])
def test_event_rejects_unvalidated_owner_or_version(href, text):
    with pytest.raises(ValueError):
        contract.event(href, text, 1000)


def test_unknown_label_is_unresolved_not_idle_or_session():
    row = contract.event(HREF, "Version #144 New provider state", 1000)
    assert row["state"] == "unresolved" and row["run_type"] == "unknown"
    assert row["identity"] == "script-version:358145984"
    assert "session_id" not in row and "gpu_verified_devices" not in row


@pytest.mark.parametrize('status',['Running','Cancelled','Successful','Unknown','Retrying'])
def test_ui_positive_cannot_claim_stable_identity_or_terminal_resolution(status):
    row={'structured':True,'href':'https://www.kaggle.com/code/thangma/example/edit',
         'text':'Interactive Session '+status,'title':'example','interactive':True,'status':status}
    first=contract.observation(row,1000,0)
    second=contract.observation(row,1000,1)
    assert first['identity']!=second['identity']
    assert first['state']=='unresolved' and first['ref'] is None and first['version'] is None
    assert first['run_type']=='interactive' and 'session_id' not in first
    payload={**snapshot(1000,False),'observations':[first,second]}
    contract.validate(payload,{'thangma'},1000)
    for changes in ({'state':'terminal'},{'state':'running'},{'ref':'thangma/example'},
                    {'version':1},{'run_type':'production'}):
        with pytest.raises(ValueError):
            contract.validate({**payload,'observations':[{**first,**changes}]},{'thangma'},1000)


def test_ui_unknown_through_ingestion_persistence_summary_and_enforcing_defer(analysis_client):
    client=analysis_client
    client.app.state.collector.accounts={'thangma':m.Account('thangma')}
    at=time.time()-1
    row=contract.ui_observation({'interactive':True,'status':'Cancelled','text':'Interactive Session'},at,0)
    payload={**snapshot(at,False),'observations':[row]}
    from unittest.mock import patch
    # Real route validation/auth, followed by SQLite write and summary; no provider.
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        token=Path(directory)/'token';token.write_text('local-test-token')
        with patch.dict(m.os.environ,{'DISCOVERY_INGEST_TOKEN_FILE':str(token)}):
            response=client.post('/api/activity/browser-discovery',json=payload,
                                 headers={'X-Discovery-Token':'local-test-token'})
    assert response.status_code==200 and response.json()['accepted']
    account=client.get('/api/summary').json()['accounts'][0]
    assert account['activity']['status']=='unresolved'
    assert account['activity']['coverage']=='partial' and account['admission']['would_defer']
    assert 'required_coverage_incomplete:browser_discovery' in account['admission']['reasons']


@pytest.mark.parametrize("change", [
    {"account": "annalynhi"}, {"coverage": "full"}, {"coverage": "none"},
    {"observed_at": float("nan")}, {"observed_at": 1001},
    {"error": "cookie contents"}, {"auth_error": "authentication_required"},
    {"extra": "html"}, {"observations": [{}]},
])
def test_strict_snapshot_contract(change):
    with pytest.raises(ValueError):
        contract.validate({**snapshot(), **change}, {"thangma"}, 1000)


def test_duplicate_and_invented_identity_rejected():
    payload = snapshot()
    payload["observations"] *= 2
    with pytest.raises(ValueError):
        contract.validate(payload, {"thangma"}, 1000)
    for field, value in [("ref", "other/job"), ("state", "terminal"),
                         ("session_id", "358145984"), ("observed_at", contract.iso(999))]:
        payload = snapshot()
        payload["observations"][0][field] = value
        with pytest.raises(ValueError):
            contract.validate(payload, {"thangma"}, 1000)


class DelayedPanel:
    def __init__(self, empty=False):
        self.ticks = 0
        self.empty = empty
        self.scrolled = 0

    def get_by_role(self, *args, **kwargs):
        return self

    async def evaluate_all(self, _):
        # First two seconds falsely show zero active; rows arrive at five seconds.
        return [] if self.empty or self.ticks < 5 else [{"href": HREF, "text": "Version #144 Running"}]

    async def evaluate(self, _):
        self.scrolled += 1
        return self.ticks < 6

    async def inner_text(self):
        return "0 Active Events" if self.empty or self.ticks < 5 else "Version #144 Running"

    async def pause(self, _):
        self.ticks += 1


def test_short_zero_footer_wait_does_not_discard_late_rows():
    panel = DelayedPanel()
    rows = asyncio.run(visit.collect_panel(panel, panel.pause))
    assert len(rows) == 1 and panel.ticks >= 8 and panel.scrolled >= 8
    payload = snapshot(rows=False)
    payload["observations"] = [contract.event(r["href"], r["text"], 1000) for r in rows]
    assert contract.validate(payload, {"thangma"}, 1000)["coverage"] == "partial"


def test_stable_empty_panel_remains_partial():
    panel = DelayedPanel(empty=True)
    assert asyncio.run(visit.collect_panel(panel, panel.pause)) == []
    assert contract.validate(snapshot(rows=False), {"thangma"}, 1000)["coverage"] == "partial"


def test_changed_row_selector_is_failure_not_empty_snapshot():
    panel = DelayedPanel(empty=True)
    async def visible_rows():
        return "Version #144 Running"
    panel.inner_text = visible_rows
    with pytest.raises(ValueError, match="panel_unavailable"):
        asyncio.run(visit.collect_panel(panel, panel.pause))


def test_supported_link_does_not_hide_unmatched_visible_version():
    panel = DelayedPanel()
    panel.ticks = 5
    async def mixed_rows():
        return "Version #144 Running\nVersion #145 Running"
    panel.inner_text = mixed_rows
    with pytest.raises(ValueError, match="panel_unavailable"):
        asyncio.run(visit.collect_panel(panel, panel.pause))


def test_conflicting_version_for_same_destination_fails_sample():
    panel = DelayedPanel()
    panel.ticks = 5
    async def changed_rows(_):
        return [{"href": HREF, "text": "Version #144 Running" if panel.ticks == 6 else "Version #145 Running"}]
    async def current_text():
        return "Version #144 Running" if panel.ticks == 6 else "Version #145 Running"
    panel.evaluate_all = changed_rows
    panel.inner_text = current_text
    with pytest.raises(ValueError, match="invalid_event_identity"):
        asyncio.run(visit.collect_panel(panel, panel.pause))


def test_href_less_resolution_rechecks_each_row_not_nonunique_text_cache():
    panel = DelayedPanel()
    async def role_links(_):
        return [{"href": "", "text": f"Version #{version} Successful"} for version in (146, 145)]
    async def visible():
        return "Version #146 Successful\nVersion #145 Successful"
    calls = []
    async def resolve(index, text):
        calls.append((index, text))
        return HREF.replace("358145984", str(358726563 - index))
    panel.evaluate_all, panel.inner_text = role_links, visible
    rows = asyncio.run(visit.collect_panel(panel, panel.pause, resolve))
    assert len(rows) == 2 and len(calls) == 16
    assert {contract.event(r['href'], r['text'], 1000)['version'] for r in rows} == {145, 146}


def test_role_link_resolution_conflicting_destinations_still_fail_closed():
    panel = DelayedPanel()
    async def role_links(_):
        return [{"href": "", "text": f"Version #{version} Successful"} for version in (146, 145)]
    async def visible():
        return "Version #146 Successful\nVersion #145 Successful"
    async def resolve(*_):
        return HREF
    panel.evaluate_all, panel.inner_text = role_links, visible
    with pytest.raises(ValueError, match='invalid_event_identity'):
        asyncio.run(visit.collect_panel(panel, panel.pause, resolve))


def test_reordered_popup_row_is_never_clicked():
    class Link:
        def nth(self, _): return self
        async def evaluate(self, _): return 'Version #145 Successful'
        async def click(self, **_): pytest.fail('reordered row must not be clicked')
    class Panel:
        def get_by_role(self, *_args, **_kwargs): return Link()
    with pytest.raises(ValueError, match='panel_unavailable'):
        asyncio.run(visit.resolve_panel_link(Panel(),0,'Version #146 Successful'))


def test_popup_is_closed_when_destination_does_not_resolve():
    class Popup:
        closed = False
        async def wait_for_url(self, *_args, **_kwargs): raise TimeoutError()
        async def close(self): self.closed = True
    popup = Popup()
    class Opened:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        @property
        def value(self):
            async def ready(): return popup
            return ready()
    class Link:
        def nth(self, _): return self
        async def evaluate(self, _): return 'Version #146 Successful'
        async def click(self, **_): pass
    class Panel:
        page = None
        def get_by_role(self,*_args,**_kwargs): return Link()
        def expect_popup(self,**_): return Opened()
    panel = Panel()
    panel.page = panel
    with pytest.raises(TimeoutError):
        asyncio.run(visit.resolve_panel_link(panel,0,'Version #146 Successful'))
    assert popup.closed


def test_ingestion_wall_deadline_terminates_stalled_http_process(tmp_path):
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    class StalledResponse(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.end_headers()
            # Headers are immediate, body stalls beyond the outer deadline.
            release.wait(3)
        def log_message(self, *_):
            pass
    release = threading.Event()
    server = ThreadingHTTPServer(("127.0.0.1", 0), StalledResponse)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token = tmp_path / "token"
    token.write_text("test-only-discovery-token")
    token.chmod(0o600)
    try:
        # Skip only the Linux permission check on Windows in this subprocess;
        # the real ingest function and HTTP I/O still run.
        command = ("import discovery_worker as w; from pathlib import Path; "
                   "w.private_file=lambda _:None; "
                   "w.ingest(" + repr(f"http://127.0.0.1:{server.server_port}/") +
                   ",Path(" + repr(str(token)) + "),{})")
        original = worker.subprocess.run
        def run_http(args, **kwargs):
            return original([sys.executable, "-c", command], cwd=str(Path(worker.__file__).parent), **kwargs)
        from unittest.mock import patch
        started = time.monotonic()
        with patch.object(worker.subprocess, "run", side_effect=run_http):
            with pytest.raises(subprocess.TimeoutExpired):
                worker.run_ingest("unused", token, snapshot(), deadline=1)
        assert time.monotonic() - started < 2.5
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_schedule_coalesces_and_durable_auth_failure_suspends_browser(tmp_path):
    visits, sent = [], []
    def failed_visit():
        visits.append(1)
        return contract.failure("authentication_required", 1000), {"duration_seconds": 2}
    def failed_ingest(payload):
        sent.append(payload)
        raise OSError("secret error must not escape")
    state = worker.cycle({}, failed_visit, failed_ingest, lambda: 1000, random.Random(1))
    visit.save_private(tmp_path / "state.json", state)
    state = json.loads((tmp_path / "state.json").read_text())
    worker.cycle(state, failed_visit, failed_ingest, lambda: 99999, random.Random(2))
    assert len(visits) == 1 and len(sent) == 2
    assert state["suspended"] == "authentication_required" and isinstance(state["outbox"], dict)
    assert state["next_at"] > 99999 and state["failures"] == 2
    worker.cycle(state, failed_visit, lambda _: None, lambda: 100000)
    assert "outbox" not in state and len(visits) == 1


def test_coalescing_latest_attempt_backoff_and_recovery():
    rng = random.Random(5)
    assert all(180 <= worker.next_delay(0, rng) <= 300 for _ in range(100))
    assert 1800 <= worker.next_delay(999, rng) <= 1830
    def fail(_):
        raise OSError()
    state = worker.cycle({}, lambda: (snapshot(), {}), fail, lambda: 1000, rng)
    state = worker.cycle(state, lambda: (snapshot(2000), {}), fail, lambda: 2000, rng)
    assert state["outbox"]["observed_at"] == 2000  # One slot, not missed-tick queue.
    state = worker.cycle(state, lambda: (snapshot(3000), {}), lambda _: None, lambda: 3000, rng)
    assert state["failures"] == 0 and state["last_success"] == 3000 and "outbox" not in state


def test_suspension_checkpoint_precedes_ingestion(tmp_path):
    path = tmp_path / "state.json"
    def send(_):
        persisted = json.loads(path.read_text())
        assert persisted["suspended"] == "identity_mismatch" and "outbox" in persisted
    worker.cycle({}, lambda: (contract.failure("identity_mismatch", 1000), {}), send,
                 checkpoint=lambda state: visit.save_private(path, state))


def test_ingestion_auth_limits_allowlist_and_persistent_failure(analysis_client, tmp_path, monkeypatch):
    client = analysis_client
    client.app.state.collector.accounts = {"thangma": m.Account("thangma")}
    path = tmp_path / "token"
    path.write_text("dedicated-discovery-token")
    monkeypatch.setenv("DISCOVERY_INGEST_TOKEN_FILE", str(path))
    endpoint = "/api/activity/browser-discovery"
    headers = {"X-Discovery-Token": "dedicated-discovery-token"}
    at = time.time() - 1
    payload = snapshot(at)
    assert client.post(endpoint, json=payload).status_code == 401
    assert client.post(endpoint, json=payload, headers={"X-Monitor-Token": "test-ingest-token"}).status_code == 401
    assert client.post("/api/production/attach", json={}, headers=headers).status_code == 401
    assert client.post(endpoint, content=b"x" * 65537, headers=headers).status_code == 413
    assert client.post(endpoint, json={**payload, "coverage": "full"}, headers=headers).status_code == 422
    assert client.post(endpoint, json={**payload, "account": "annalynhi"}, headers=headers).status_code == 422
    assert client.post(endpoint, json=payload, headers=headers).json()["accepted"] is True
    assert client.post(endpoint, json=contract.failure("authentication_required", at + .1), headers=headers).status_code == 200
    store = m.Store(client.app.state.collector.store.path)
    evidence = store.activity_evidence()[0]
    assert evidence["coverage"] == "none" and evidence["observations"] == payload["observations"]
    assert evidence["observed_at"] == m.iso(at)
    assert evidence["auth_error"] == "authentication_required"
    assert client.get("/api/summary").json()["accounts"][0]["status"] == "unknown"
    assert client.post(endpoint, json=payload, headers=headers).json()["accepted"] is False
    recovered = snapshot(time.time() - .01, rows=False)
    assert client.post(endpoint, json=recovered, headers=headers).json()["accepted"] is True
    assert client.get("/api/summary").json()["accounts"][0]["status"] == "partial"
    monkeypatch.delenv("DISCOVERY_INGEST_TOKEN_FILE")
    assert client.post(endpoint, json=recovered, headers=headers).status_code == 503


def test_ingested_empty_partial_cannot_admit_enforcing_probe(analysis_client, tmp_path, monkeypatch):
    client = analysis_client
    collector = client.app.state.collector
    collector.accounts = {"thangma": m.Account("thangma")}
    token = tmp_path / "token"
    token.write_text("test-only-discovery-token")
    monkeypatch.setenv("DISCOVERY_INGEST_TOKEN_FILE", str(token))
    at = time.time() - 1
    response = client.post("/api/activity/browser-discovery", json=snapshot(at, rows=False),
                           headers={"X-Discovery-Token": "test-only-discovery-token"})
    assert response.status_code == 200 and response.json()["accepted"]
    account = client.get("/api/summary").json()["accounts"][0]
    assert account["status"] == "partial" and account["admission"]["policy"] == "observe_only"
    run = insert_scheduled(collector.store, account="thangma", due=at)
    assert not collector.store.claim(run, at + 1, "enforce")
    assert "required_coverage_incomplete:browser_discovery" in run["admission"]["reasons"]
    assert run["state"] == "skipped" and "submit_begin" not in run


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux prctl, /proc and process groups")
@pytest.mark.parametrize("early_exit", [False, True])
def test_real_process_tree_timeout_and_detached_orphan_cleanup(tmp_path, early_exit):
    pidfile = tmp_path / "child.pid"
    script = ("import os,time; pid=os.fork(); "
              "\nif pid==0:\n os.setsid(); open(" + repr(str(pidfile)) +
              ",'w').write(str(os.getpid())); time.sleep(60)"
              "\nelse:\n " + ("time.sleep(.2)" if early_exit else "time.sleep(60)"))
    started = time.monotonic()
    payload, metrics = worker.run_visit(tmp_path / "unused", [sys.executable, "-c", script], deadline=6)
    assert time.monotonic() - started < 6
    assert payload["error"] == ("worker_crash" if early_exit else "visit_timeout")
    assert not Path("/proc", pidfile.read_text()).exists()
    assert not worker.descendants()
    assert "cgroup_after" in metrics
