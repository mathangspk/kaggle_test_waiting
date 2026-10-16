"""Exact production polling: persisted recovery, failure semantics and fair bounds."""
import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from test_monitor import m, production_payload, mocked_kaggle_modules, analysis_client, insert_scheduled


def attachment(event="existing", account="a", version=3):
    return {"event_id": event, "account": account, "ref": account + "/production-job", "version": version}


def response(run, status="RUNNING", **changes):
    return {"ok": True, "data": {"status": status, "failure": False,
                                "requested_version": run["version"], "requested_ref": run["ref"], **changes}}


def setup(tmp_path):
    store = m.Store(tmp_path / "production.sqlite")
    transport = MagicMock(timeout=45)
    collector = m.Collector(store, [m.Account(n, "fake-" + n) for n in "ab"], False, transport)
    return store, collector, transport


def test_attach_authenticated_idempotent_no_submission_or_timing(analysis_client):
    client = analysis_client
    headers = {"x-monitor-token": "test-ingest-token"}
    payload = {**attachment(), "script_version_id": 358145984}
    assert client.post("/api/production/attach", json=payload).status_code == 401
    assert client.post("/api/production/attach", json=payload, headers=headers).json()["created"]
    assert not client.post("/api/production/attach", json=payload, headers=headers).json()["created"]
    assert client.post("/api/production/attach", json={**payload, "version": 4}, headers=headers).status_code == 409
    run = client.app.state.collector.store.production_event("existing")
    assert run["submit_begin"] is None and run["submit_ack"] is None and run["due"] is None
    assert run["accelerator"] is None and not run["script_version_mapping_verified"]
    assert "session_id" not in run and "start" not in run
    assert client.get("/api/analysis?source=production").json()["accounts"][0]["attempted"] == 0
    with patch.object(client.app.state.collector.transport, "invoke") as invoke:
        result = client.post("/api/production/verify", json={"event_id": "existing"}, headers=headers).json()
        assert result["reason"] == "submission_time_unavailable"
        invoke.assert_not_called()


@pytest.mark.parametrize("changes", [{"account": "b"}, {"ref": "b/job"}, {"version": True}, {"version": 0},
                                     {"session_id": 358145984}, {"submit_begin": m.iso(1000)}, {"script_version_id": "358145984"}])
def test_attachment_validation(analysis_client, changes):
    assert analysis_client.post("/api/production/attach", json={**attachment(), **changes},
                                headers={"x-monitor-token": "test-ingest-token"}).status_code == 422


def test_helper_status_is_one_exact_owned_read_without_artifacts(capsys):
    api, endpoint = MagicMock(), MagicMock()
    api.build_kaggle_client.return_value.__enter__.return_value.kernels.kernels_api_client = endpoint
    endpoint.get_kernel_session_status.return_value = SimpleNamespace(status=SimpleNamespace(name="COMPLETE"), failure_message="")
    payload = {**attachment(), "source": "production"}
    with patch.dict(sys.modules, mocked_kaggle_modules(api)), patch.dict(m.os.environ, {"KAGGLE_USERNAME": "a"}), \
            patch.object(m.sys, "stdin", io.StringIO(json.dumps(payload))):
        m.helper("production_status")
    result = json.loads(capsys.readouterr().out)
    assert result == response(payload, "COMPLETE")
    request = endpoint.get_kernel_session_status.call_args.args[0]
    assert request.to_dict()["versionLabel"] == "v3"
    assert request.user_name == "a" and request.kernel_slug == "production-job"
    endpoint.get_kernel_session_status.assert_called_once()
    endpoint.list_kernel_session_output.assert_not_called()
    endpoint.get_kernel.assert_not_called()
    api.kernels_push.assert_not_called()


def test_helper_status_wrong_owner_no_provider_read(capsys):
    api = MagicMock()
    with patch.dict(sys.modules, mocked_kaggle_modules(api)), patch.dict(m.os.environ, {"KAGGLE_USERNAME": "b"}), \
            patch.object(m.sys, "stdin", io.StringIO(json.dumps({**attachment(), "source": "production"}))):
        m.helper("production_status")
    assert not json.loads(capsys.readouterr().out)["ok"]
    api.build_kaggle_client.assert_not_called()


@pytest.mark.parametrize("changes", [{"requested_version": 4}, {"requested_version": True}, {"requested_ref": "b/production-job"},
                                     {"status": "SURPRISE secret"}, {"failure": None}])
def test_status_identity_protocol_rejection_retains_unresolved(tmp_path, changes):
    store, collector, transport = setup(tmp_path)
    store.attach_production(attachment(), 1000)
    run = store.production_event("existing")
    transport.invoke.return_value = response(run, **changes)
    with patch.object(m.time, "time", return_value=1001):
        collector.poll_production(run)
    assert store.production_event("existing")["state"] == "external_pending"
    activity = m.build_summary(collector, None, 1001)["accounts"][0]["activity"]
    assert activity["status"] == "unresolved"
    assert activity["sources"][0]["error"] == "status_identity_or_protocol"


@pytest.mark.parametrize("status,state", [("COMPLETE", "success"), ("ERROR", "failed"), ("CANCELLED", "cancelled"),
                                          ("RUNNING", "running"), ("QUEUED", "queued"), ("CANCEL_REQUESTED", "submitted")])
def test_execution_independent_of_artifact_and_timing(tmp_path, status, state):
    store, collector, transport = setup(tmp_path)
    payload = production_payload()
    store.register_production(payload, 1000)
    run = store.production_event(payload["event_id"])
    transport.invoke.return_value = response(run, status)
    with patch.object(m.time, "time", return_value=1001):
        collector.poll_production(run)
    saved = store.production_event(payload["event_id"])
    assert saved["state"] == state and saved["last_observed"] == 1001
    assert saved["evidence_provenance"] == "registration_metadata"
    assert "start" not in saved and "gpu_names" not in saved and "queue_upper_seconds" not in saved
    assert saved["reported_timing"] == payload["timing"]
    assert m.analysis_counts([saved], 1001)["fast"] == 0
    observation = store.activity_evidence()[0]["observations"][0]
    assert observation["provider_state"] == status and observation["gpu_verified_devices"] is None
    assert observation["state"] == ("terminal" if status in {"COMPLETE", "ERROR", "CANCELLED"} else "running" if status == "RUNNING" else "pending")
    assert (store.next_production_poll(2000) is None) == (status in {"COMPLETE", "ERROR", "CANCELLED"})
    transport.invoke.assert_called_once_with("production_status", run, collector.accounts["a"])


@pytest.mark.parametrize("failure,code,auth", [({"ok": False, "http_status": 401, "error": "secret"}, "provider_auth_denied", True),
                                           ({"ok": False, "error": "api_timeout"}, "api_timeout", False),
                                           ({"ok": False, "http_status": 404}, "provider_status_unavailable", False)])
def test_error_retains_positive_without_certifying_current_running(tmp_path, failure, code, auth):
    store, collector, transport = setup(tmp_path)
    store.attach_production(attachment(), 1000)
    run = store.production_event("existing")
    transport.invoke.return_value = response(run)
    with patch.object(m.time, "time", return_value=1001):
        collector.poll_production(run)
    transport.invoke.return_value = failure
    with patch.object(m.time, "time", return_value=1061):
        collector.poll_production(store.production_event("existing"))
    collector.store = m.Store(store.path)
    activity = m.build_summary(collector, None, 1061)["accounts"][0]["activity"]
    assert activity["status"] == "unknown"
    assert len(activity["observations"]) == 1 and activity["observations"][0]["provider_state"] == "RUNNING"
    source = activity["sources"][0]
    assert source["observed_at"] == m.iso(1001) and source["coverage"] == "none"
    assert source["auth_error" if auth else "error"] == code
    assert collector.store.next_production_poll(1120) is None
    assert collector.store.next_production_poll(1121)["event_id"] == "existing"
    transport.invoke.return_value = response(run, "COMPLETE")
    with patch.object(m.time, "time", return_value=1121):
        collector.poll_production(collector.store.production_event("existing"))
    assert m.build_summary(collector, None, 1121)["accounts"][0]["activity"]["status"] == "partial"


def test_ambiguous_submission_never_queries_latest_or_pushes_and_attach_preserves_receipt(tmp_path):
    store, collector, transport = setup(tmp_path)
    payload = {**production_payload(), "push_outcome": "ambiguous", "ref": None, "version": None, "submit_ack": None}
    store.register_production(payload, 1000)
    with patch.object(m.time, "time", return_value=1001):
        collector.poll_production(store.next_production_poll(1001))
    transport.invoke.assert_not_called()
    store = m.Store(store.path)
    assert store.next_production_poll(1060) is None
    store.attach_production(attachment(payload["event_id"]), 1061)
    assert not store.register_production(payload, 1062)
    resolved = store.production_event(payload["event_id"])
    assert resolved["push_outcome"] == "ambiguous" and resolved["submit_ack"] is None
    assert resolved["submit_begin"] == m.timestamp(payload["submit_begin"])
    assert m.production_identity_confirmed(resolved)


def test_restart_account_and_version_fairness_backoff_and_older_active(tmp_path):
    store, collector, transport = setup(tmp_path)
    for event, account, version in [("old", "a", 1), ("new", "a", 9), ("other", "b", 4)]:
        store.attach_production(attachment(event, account, version), 1000)
    called = []
    for now in (1001, 1002, 1003):
        collector.store = store = m.Store(store.path)
        run = store.next_production_poll(now)
        called.append((run["event_id"], run["version"]))
        transport.invoke.return_value = response(run)
        with patch.object(m.time, "time", return_value=now):
            collector.poll_production(run)
    assert called == [("new", 9), ("other", 4), ("old", 1)]
    assert store.next_production_poll(1060) is None
    for now in (1061, 1121, 1241, 1481, 1961, 2861):
        with patch.object(m.time, "time", return_value=now):
            transport.invoke.return_value = {"ok": False, "error": "secret"}
            collector.poll_production(store.production_event("new"))
    assert store.production_event("new")["next_poll"] == 3761
    assert len(store.activity_evidence()) == 3
    assert store.production_event("old")["state"] == "running"


def test_tick_alternates_poll_lanes_with_at_most_one_poll(tmp_path):
    store, collector, transport = setup(tmp_path)
    store.attach_production(attachment(), 1000)
    run = insert_scheduled(store)
    run.update(state="running", active=True, submit_begin=900, version=1, next_poll=1000)
    store.save(run)
    with patch.object(collector, "poll_production") as production, patch.object(collector, "poll") as probe, \
            patch.object(m.time, "time", return_value=1001):
        collector.tick(1001)
        production.assert_called_once()
        probe.assert_not_called()
        collector.tick(1001)
        probe.assert_called_once()
        assert production.call_count == 1
    transport.invoke.assert_not_called()


def test_failed_account_backoff_survives_restart_without_starving_healthy_backlog(tmp_path):
    store, collector, transport = setup(tmp_path)
    store.attach_production(attachment("failing", "a"), 1000)
    for version in range(1, 5):
        store.attach_production(attachment("healthy-" + str(version), "b", version), 1000)
    transport.invoke.return_value = {"ok": False, "http_status": 401}
    with patch.object(m.time, "time", return_value=1001):
        collector.poll_production(store.next_production_poll(1001))
    assert store.production_event("failing")["next_poll"] == 1061
    for now in range(1002, 1006):
        collector.store = store = m.Store(store.path)
        run = store.next_production_poll(now)
        assert run["account"] == "b"
        transport.invoke.return_value = response(run, "COMPLETE")
        with patch.object(m.time, "time", return_value=now):
            collector.poll_production(run)
    assert store.next_production_poll(1060) is None
    for now, deadline in [(1061, 1181), (1181, 1421), (1421, 1901), (1901, 2801), (2801, 3701)]:
        collector.store = store = m.Store(store.path)
        run = store.next_production_poll(now)
        assert run["event_id"] == "failing"
        transport.invoke.return_value = {"ok": False, "error": "api_timeout"}
        with patch.object(m.time, "time", return_value=now):
            collector.poll_production(run)
        assert store.next_production_poll(deadline - 1) is None
        assert store.production_event("failing")["next_poll"] == deadline
    collector.store = store = m.Store(store.path)
    run = store.next_production_poll(3701)
    transport.invoke.return_value = response(run)
    with patch.object(m.time, "time", return_value=3701):
        collector.poll_production(run)
    saved = store.production_event("failing")
    assert saved["poll_errors"] == 0 and saved["next_poll"] == 3761
    source = next(s for s in store.activity_evidence() if s["account"] == "a")
    assert source["error"] is None and source["auth_error"] is None and source["coverage"] == "partial"
    transport.invoke.return_value = {"ok": False, "error": "api_timeout"}
    with patch.object(m.time, "time", return_value=3761):
        collector.poll_production(store.next_production_poll(3761))
    assert store.production_event("failing")["next_poll"] == 3821


def test_terminal_attachment_replay_after_restart_does_not_requeue_or_invent_timing(tmp_path):
    store, collector, transport = setup(tmp_path)
    payload = attachment()
    store.attach_production(payload, 1000)
    run = store.production_event("existing")
    transport.invoke.return_value = response(run, "COMPLETE")
    with patch.object(m.time, "time", return_value=1001):
        collector.poll_production(run)
    saved = store.production_event("existing")
    collector.store = store = m.Store(store.path)
    assert not store.attach_production(payload, 2000)
    assert store.production_event("existing") == saved
    assert store.next_production_poll(2000) is None
    assert saved["state"] == "success" and saved["evidence_provenance"] == "attachment_metadata"
    assert saved["submit_begin"] is None and "start" not in saved and "gpu_names" not in saved
    assert m.analysis_counts([saved], 2000)["attempted"] == 0


def test_artifact_verification_wins_over_inflight_poll(tmp_path):
    store, collector, transport = setup(tmp_path)
    payload = production_payload()
    store.register_production(payload, 1000)
    run = store.production_event(payload["event_id"])
    store.record_production_poll(run["event_id"], response(run)["data"], None, None, 1001)
    store.verify_production(run["event_id"], {"t4x2": True, "gpu_names": ["Tesla T4", "Tesla T4"]}, 1002)
    store.record_production_poll(run["event_id"], response(run)["data"], None, None, 1003)
    assert store.production_event(run["event_id"])["state"] == "success"
    assert store.activity_evidence()[0]["observations"][0]["state"] == "terminal"
    assert store.next_production_poll(2000) is None


def test_legacy_schema_migration_preserves_hash_and_enrolls_old_active(tmp_path):
    path = tmp_path / "legacy.sqlite"
    payload = production_payload()
    original = {"id": "production:" + payload["event_id"], "event_id": payload["event_id"],
                "account": "a", "source": "production", "state": "external_pending",
                "ref": payload["ref"], "requested_ref": payload["requested_ref"], "version": 12,
                "push_outcome": "accepted", "submit_begin": 900, "next_poll": 1100}
    with m.sqlite3.connect(path) as db:
        db.execute("CREATE TABLE production_events(event_id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,submit_begin REAL NOT NULL,data TEXT NOT NULL)")
        db.execute("INSERT INTO production_events VALUES (?,?,?,?)", (payload["event_id"], "unchanged-hash", 900, json.dumps(original)))
    store = m.Store(path)
    assert store.production_event(payload["event_id"]) == original
    assert store.next_production_poll(1099) is None
    assert store.next_production_poll(1100)["version"] == 12
    store.attach_production(attachment(), 1000)
    with store.connect() as db:
        assert db.execute("SELECT payload_hash FROM production_events WHERE event_id=?", (payload["event_id"],)).fetchone()[0] == "unchanged-hash"
        assert db.execute("SELECT submit_begin FROM production_events WHERE event_id='existing'").fetchone()[0] is None


def test_complete_with_failure_is_terminal_failed_and_removed_account_is_not_idle(tmp_path):
    store, collector, transport = setup(tmp_path)
    store.attach_production(attachment(), 1000)
    run = store.production_event("existing")
    transport.invoke.return_value = response(run, "COMPLETE", failure=True)
    with patch.object(m.time, "time", return_value=1001):
        collector.poll_production(run)
    assert store.production_event("existing")["state"] == "failed"
    store.attach_production(attachment("removed", "b"), 1000)
    del collector.accounts["b"]
    transport.reset_mock()
    with patch.object(m.time, "time", return_value=1002):
        collector.poll_production(store.production_event("removed"))
    transport.invoke.assert_not_called()
    accounts = {a["id"]: a for a in m.build_summary(collector, None, 1002)["accounts"]}
    assert accounts["b"]["status"] == "unresolved"
    assert accounts["b"]["activity"]["sources"][0]["auth_error"] == "account_removed"


def test_iso_microsecond_rounding_is_accepted_but_real_future_is_rejected(tmp_path):
    store, _, _ = setup(tmp_path)
    at = 1791946037.7492836
    observation = {"identity": "event", "run_type": "production", "provider_state": "RUNNING",
                   "state": "running", "observed_at": m.iso(at)}
    assert store.record_activity("a", "test", [observation], "partial", at)
    with pytest.raises(ValueError, match="future_activity_observation"):
        store.record_activity("a", "test", [{**observation, "observed_at": m.iso(at + .001)}], "partial", at)
