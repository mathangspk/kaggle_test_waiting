"""Account evidence semantics with real persistence; no provider calls or workers."""
from test_monitor import m, insert_scheduled, production_payload, analysis_client
import pytest


def setup(tmp_path):
    store = m.Store(tmp_path / "activity.sqlite")
    return store, m.Collector(store, [m.Account("a")], False)


def account(collector, now=1000):
    return m.build_summary(collector, None, now)["accounts"][0]


def observation(state="running", at=1000):
    return {"identity": "a/job:v3", "run_type": "production", "provider_state": state,
            "state": state, "observed_at": m.iso(at), "gpu_requested": "T4x2"}


def test_completed_probe_and_unresolved_production_do_not_imply_idle(tmp_path):
    store, collector = setup(tmp_path)
    run = insert_scheduled(store, due=900)
    run.update(state="success", submit_begin=900, last_observed=950)
    store.save(run)
    assert account(collector)["status"] == "partial"
    payload = production_payload()
    payload.update(submit_begin=m.iso(990), submit_ack=m.iso(991))
    store.register_production(payload, 995)
    result = account(collector)
    assert result["probe_active_runs"] == 0 and result["status"] == "unresolved"
    production = next(o for o in result["activity"]["observations"] if o["run_type"] == "production")
    assert production["run_type"] == "production" and production["gpu_requested"] == "NvidiaTeslaT4"
    assert production["gpu_verified_devices"] is None
    store.record_activity("a", "browser_discovery", [], "full", 1000)
    assert account(collector)["status"] == "unresolved"


@pytest.mark.parametrize("coverage,expected", [("full", "observed_idle"), ("partial", "partial"), ("none", "unknown")])
def test_empty_discovery_requires_full_fresh_coverage(tmp_path, coverage, expected):
    store, collector = setup(tmp_path)
    assert account(collector)["status"] == "unknown"
    store.record_activity("A", "browser_discovery", [], coverage, 1000)
    result = account(collector, 1300)
    assert result["status"] == expected
    assert result["activity"]["sources"][0]["observed_at"] == m.iso(1000)
    assert account(collector, 1301)["status"] == "stale"
    assert account(collector, 999)["status"] != "observed_idle"


@pytest.mark.parametrize("state", ["running", "pending", "unresolved"])
def test_positive_and_unresolved_evidence_with_partial_coverage(tmp_path, state):
    store, collector = setup(tmp_path)
    store.record_activity("a", "production_api", [observation(state)], "partial", 1000)
    assert account(collector)["status"] == state
    assert account(collector, 1301)["status"] == ("unresolved" if state == "unresolved" else "stale")


@pytest.mark.parametrize("failure", [{"auth_error": "logged_out"}, {"error": "visit_timeout"}])
def test_failure_retains_last_known_evidence_across_restart(tmp_path, failure):
    store, collector = setup(tmp_path)
    store.record_activity("a", "browser_discovery", [observation()], "full", 1000)
    store.record_activity("a", "browser_discovery", [], "full", 1010, **failure)
    collector.store = m.Store(store.path)
    result = account(collector, 1010)["activity"]
    assert result["status"] == "unknown"
    assert result["observations"][0]["provider_state"] == "running"
    source = result["sources"][0]
    assert source["coverage"] == "none" and source["observed_at"] == m.iso(1000)
    assert source["last_attempt_at"] == m.iso(1010)
    assert all(source[key] == value for key, value in failure.items())
    assert not store.record_activity("a", "browser_discovery", [], "full", 1005)
    store.record_activity("a", "browser_discovery", [], "full", 1020)
    assert account(collector, 1020)["status"] == "observed_idle"


def test_full_empty_discovery_cannot_hide_stale_positive_other_source(tmp_path):
    store, collector = setup(tmp_path)
    store.record_activity("a", "production_api", [observation(at=600)], "partial", 600)
    store.record_activity("a", "browser_discovery", [], "full", 1000)
    assert account(collector)["status"] == "stale"


@pytest.mark.parametrize("change", [{"state": "idle"}, {"run_type": "other"}, {"identity": ""},
                                     {"observed_at": None}, {"observed_at": m.iso(1001)}])
def test_invalid_adapter_evidence_cannot_replace_valid_snapshot(tmp_path, change):
    store, collector = setup(tmp_path)
    store.record_activity("a", "browser_discovery", [observation()], "full", 1000)
    with pytest.raises(ValueError):
        store.record_activity("a", "browser_discovery", [{**observation(), **change}], "full", 1000)
    assert account(collector)["status"] == "running"


def test_api_preserves_evidence_contract(analysis_client):
    store = analysis_client.app.state.collector.store
    now = m.time.time()
    store.record_activity("a", "browser_discovery", [observation(at=now)], "partial", now)
    body = analysis_client.get("/api/summary").json()
    assert body["activity_contract_version"] == 1
    activity = body["accounts"][0]["activity"]
    assert activity["observations"][0]["source"] == "browser_discovery"
    assert activity["observed_at"] == m.iso(now) and activity["coverage"] == "partial"


def test_older_unresolved_production_survives_terminal_history_limit(tmp_path, monkeypatch):
    store, collector = setup(tmp_path)
    monkeypatch.setattr(m, "SUMMARY_RUN_LIMIT", 1)
    old = {**production_payload(), "event_id": "old", "submit_begin": m.iso(800), "submit_ack": m.iso(801)}
    recent = {**old, "event_id": "recent", "submit_begin": m.iso(990), "submit_ack": m.iso(991)}
    store.register_production(old, 802)
    store.register_production(recent, 992)
    store.verify_production("recent", {}, 995)
    store.record_activity("a", "browser_discovery", [], "full", 1000)
    result = account(collector)["activity"]
    assert result["status"] == "unresolved"
    assert {o["identity"] for o in result["observations"]} == {"production:old", "production:recent"}


@pytest.mark.parametrize("snapshot_state", ["terminal", "running", "pending", "unresolved"])
def test_snapshot_precedence_retains_unresolved_registration(tmp_path, snapshot_state):
    store, collector = setup(tmp_path)
    store.attach_production({"event_id": "existing", "account": "a", "ref": "a/job", "version": 3}, 1000)
    store.record_activity("a", "browser_discovery", [], "full", 1000)
    store.record_activity("a", "production_api:existing", [{**observation(snapshot_state),
                          "identity": "production:existing"}], "partial", 1000)
    result = account(collector)["activity"]
    assert result["status"] == "unresolved"
    assert [(o["identity"], o["state"]) for o in result["observations"]] == [("production:existing", "unresolved")]
    snapshot = next(s for s in store.activity_evidence() if s["source"] == "production_api:existing")
    assert snapshot["observations"][0]["state"] == snapshot_state

    # A real adapter update resolves the row and restores snapshot precedence.
    store.record_production_poll("existing", {"status": "complete", "failure": False}, None, None, 1001)
    result = account(collector, 1001)["activity"]
    assert result["status"] == "observed_idle"
    assert [(o["identity"], o["state"]) for o in result["observations"]] == [("production:existing", "terminal")]


@pytest.mark.parametrize("provider_state,expected", [("running", "running"), ("queued", "pending")])
def test_resolved_poll_snapshot_has_one_positive_and_retains_failure_semantics(tmp_path, provider_state, expected):
    store, collector = setup(tmp_path)
    store.attach_production({"event_id": "existing", "account": "a", "ref": "a/job", "version": 3}, 1000)
    store.record_activity("a", "browser_discovery", [], "full", 1000)
    store.record_production_poll("existing", {"status": provider_state, "failure": False}, None, None, 1000)
    result = account(collector)["activity"]
    assert result["status"] == expected
    assert len(result["observations"]) == 1
    assert result["observations"][0]["source"] == "production_api:existing"
    store.record_production_poll("existing", None, "provider_status_unavailable", None, 1001)
    result = account(collector, 1001)["activity"]
    assert result["status"] == "unknown"
    assert len(result["observations"]) == 1
    assert result["observations"][0]["state"] == expected
