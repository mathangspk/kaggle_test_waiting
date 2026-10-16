"""Real SQLite admission/restart/concurrency checks; every remote call is mocked."""
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import patch

import pytest
from test_monitor import m, insert_scheduled, production_payload


def idle(store, account="a", at=1000):
    store.record_activity(account, "browser_discovery", [], "full", at)


@pytest.mark.parametrize("run_type,state", [(t, s) for t in ("production", "interactive", "unknown")
                                           for s in ("running", "pending", "unresolved")])
def test_enforce_blocks_positive_evidence(tmp_path, run_type, state):
    store = m.Store(tmp_path / "db")
    idle(store)
    store.record_activity("a", "external", [{"identity": "job", "run_type": run_type,
                          "state": state, "provider_state": "queued" if state == "pending" else state,
                          "observed_at": m.iso(1000)}], "partial", 1000)
    run = insert_scheduled(store, due=1000)
    assert not store.claim(run, 1000, "enforce")
    assert run["skip_reason"] == "activity_deferred"
    assert "activity_" + state in run["admission"]["reasons"]
    assert "submit_begin" not in run
    assert store.runs()[0]["admission"] == run["admission"]


@pytest.mark.parametrize("kind,reason", [("missing", "required_source_missing:browser_discovery"),
    ("partial", "required_coverage_incomplete:browser_discovery"),
    ("none", "required_coverage_incomplete:browser_discovery"),
    ("stale", "required_source_stale:browser_discovery"),
    ("future", "required_source_stale:browser_discovery"),
    ("auth", "source_auth_error:browser_discovery"), ("error", "source_error:browser_discovery")])
def test_required_evidence_fail_closed(tmp_path, kind, reason):
    store = m.Store(tmp_path / "db")
    if kind != "missing":
        store.record_activity("a", "browser_discovery", [], kind if kind in {"partial", "none"} else "full",
                              699 if kind == "stale" else 1001 if kind == "future" else 1000,
                              auth_error="logged_out" if kind == "auth" else None,
                              error="timeout" if kind == "error" else None)
    # Another full collector cannot substitute for missing/failed required discovery.
    store.record_activity("a", "other", [], "full", 1000)
    run = insert_scheduled(store, due=1000)
    assert not store.claim(run, 1000, "enforce")
    assert reason in run["admission"]["reasons"]


def test_ttl_boundary_and_old_observation(tmp_path):
    store = m.Store(tmp_path / "db")
    idle(store)
    assert store.claim(insert_scheduled(store, due=1300), 1300, "enforce")
    store = m.Store(tmp_path / "other")
    idle(store)
    store.record_activity("a", "known", [{"identity": "job", "run_type": "production", "state": "running",
                          "provider_state": "running", "observed_at": m.iso(699)}], "partial", 1000)
    run = insert_scheduled(store, due=1000)
    assert not store.claim(run, 1000, "enforce")
    assert "nonterminal_stale" in run["admission"]["reasons"]


def test_default_observe_and_invalid_policy(tmp_path, monkeypatch):
    monkeypatch.delenv("PROBE_ADMISSION_POLICY", raising=False)
    store = m.Store(tmp_path / "db")
    collector = m.Collector(store, [m.Account("a")])
    assert collector.admission_policy == "observe_only"
    run = insert_scheduled(store, due=1000)
    assert store.claim(run, 1000)
    assert run["admission"]["would_defer"] and run["admission"]["policy"] == "observe_only"
    for policy in ("", "enforcing", "fail_open"):
        with pytest.raises(ValueError):
            m.Collector(store, [], admission_policy=policy)
    with pytest.raises(ValueError):
        m.Collector(store, [], required_activity_sources=[])


@pytest.mark.parametrize("attached", [False, True])
def test_registered_attached_ambiguity_restart_and_account_isolation(tmp_path, attached):
    store = m.Store(tmp_path / "db")
    idle(store)
    idle(store, "b")
    if attached:
        store.attach_production({"event_id": "existing", "account": "a", "ref": "a/notebook", "version": 3}, 1000)
    else:
        payload = production_payload()
        payload.update(push_outcome="ambiguous", ref=None, version=None)
        store.register_production(payload, 1000)
    store = m.Store(store.path)
    run = insert_scheduled(store, due=1000)
    assert not store.claim(run, 1000, "enforce")
    assert "activity_unresolved" in run["admission"]["reasons"]
    assert store.claim(insert_scheduled(store, account="b", due=1000), 1000, "enforce")


def test_concurrent_claims_and_persisted_reservation(tmp_path):
    path = tmp_path / "db"
    store = m.Store(path)
    idle(store)
    runs = [insert_scheduled(store, slot=i, due=1000) for i in range(2)]
    stores = [m.Store(path), m.Store(path)]
    barrier = threading.Barrier(2)

    def claim(i):
        barrier.wait()
        return stores[i].claim(runs[i], 1000, "enforce")

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(claim, range(2)))
    assert sorted(results) == [False, True]
    restarted = m.Store(path)
    assert len(restarted.runs("active=1")) == 1
    collector = m.Collector(restarted, [m.Account("a")], admission_policy="enforce")
    collector.recover_interrupted_pushes(1001)
    assert restarted.runs("active=1")[0]["state"] == "ambiguous"
    assert not restarted.claim(insert_scheduled(restarted, slot=2, due=1001), 1001, "enforce")


def test_scheduler_enforce_never_pushes_and_observe_records_diagnostic(tmp_path):
    for policy in ("enforce", "observe_only"):
        store = m.Store(tmp_path / policy)
        store.set_meta("next_window", 1800)
        run = insert_scheduled(store, due=1000)
        collector = m.Collector(store, [m.Account("a")], auto=True, admission_policy=policy)
        collector.ready = True
        with patch.object(m.time, "time", return_value=1000), patch.object(collector, "push") as push:
            collector.tick(1000)
        assert push.call_count == (policy == "observe_only")
        saved = store.runs("id=?", (run["id"],))[0]
        assert saved["admission"]["would_defer"]
        assert saved["state"] == ("skipped" if policy == "enforce" else "pushing")


def test_attachment_and_claim_share_write_serialization(tmp_path):
    store = m.Store(tmp_path / "db")
    idle(store)
    run = insert_scheduled(store, due=1000)
    other = m.Store(store.path)
    # A committed attachment is visible even to a Store instantiated beforehand.
    other.attach_production({"event_id": "existing", "account": "a", "ref": "a/notebook", "version": 1}, 1000)
    assert not store.claim(run, 1000, "enforce")
    assert len(store.runs()) == 1
    assert other.production_event("existing")["state"] == "external_pending"


@pytest.mark.parametrize("status", ["running", "queued"])
def test_exact_version_production_blocks_until_terminal(tmp_path, status):
    store = m.Store(tmp_path / "db")
    idle(store)
    store.attach_production({"event_id": "existing", "account": "a", "ref": "a/notebook", "version": 1}, 1000)
    store.record_production_poll("existing", {"status": status, "failure": False}, None, None, 1000)
    run = insert_scheduled(store, due=1000)
    assert not store.claim(run, 1000, "enforce")
    assert "activity_" + ("pending" if status == "queued" else "running") in run["admission"]["reasons"]
    store.record_production_poll("existing", {"status": "complete", "failure": False}, None, None, 1001)
    assert store.claim(insert_scheduled(store, slot=1, due=1001), 1001, "enforce")
    assert store.production_event("existing")["evidence_provenance"] == "attachment_metadata"


def test_claim_waits_for_inflight_evidence_transaction(tmp_path):
    import json
    store = m.Store(tmp_path / "db")
    idle(store)
    run = insert_scheduled(store, due=1000)
    other = m.Store(store.path)
    started = threading.Event()

    def claim():
        started.set()
        return other.claim(run, 1000, "enforce")

    with ThreadPoolExecutor(1) as pool:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Model the uncommitted receipt/attachment write; admission must not
            # read the preceding idle snapshot and reserve before this commits.
            db.execute("INSERT INTO production_events VALUES (?,?,NULL,?)", ("inflight", "hash", json.dumps({
                "id": "production:inflight", "event_id": "inflight", "account": "a", "source": "production",
                "state": "external_pending", "ref": "a/job", "version": 1})))
            future = pool.submit(claim)
            assert started.wait(2)
            assert not future.done()
        assert not future.result(timeout=5)
    assert "activity_unresolved" in run["admission"]["reasons"]


def test_failed_source_and_expiry_survive_restart(tmp_path):
    store = m.Store(tmp_path / "db")
    idle(store)
    store.record_activity("a", "known", [], "partial", 1000, auth_error="denied")
    restarted = m.Store(store.path)
    run = insert_scheduled(restarted, due=1000)
    assert not restarted.claim(run, 1000, "enforce")
    assert "source_auth_error:known" in run["admission"]["reasons"]
    restarted.record_activity("a", "known", [], "partial", 1001)
    run = insert_scheduled(restarted, slot=1, due=1301)
    assert not restarted.claim(run, 1301, "enforce")
    assert "required_source_stale:browser_discovery" in run["admission"]["reasons"]


def test_snapshot_cannot_release_unresolved_tracked_identity(tmp_path):
    store = m.Store(tmp_path / "db")
    idle(store)
    store.attach_production({"event_id": "existing", "account": "a", "ref": "a/notebook", "version": 1}, 1000)
    store.record_activity("a", "production_api:existing", [{"identity": "production:existing",
                          "run_type": "production", "state": "terminal", "provider_state": "complete",
                          "observed_at": m.iso(1000)}], "partial", 1000)
    run = insert_scheduled(store, due=1000)
    assert not store.claim(run, 1000, "enforce")
    assert "activity_unresolved" in run["admission"]["reasons"]
    collector = m.Collector(store, [m.Account("a")], admission_policy="enforce")
    summary = m.build_summary(collector, None, 1000)["accounts"][0]
    assert summary["status"] == summary["activity"]["status"] == "unresolved"
    assert summary["admission"]["would_defer"]
    assert [o["state"] for o in summary["activity"]["observations"]
            if o["identity"] == "production:existing"] == ["unresolved"]
