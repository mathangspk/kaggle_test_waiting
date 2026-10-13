import importlib.util
import io
import json
from pathlib import Path
import random
import re
import sys
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

spec = importlib.util.spec_from_file_location("monitor_app", Path(__file__).parents[1] / "app.py")
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


@pytest.fixture
def analysis_client(tmp_path):
    from fastapi.testclient import TestClient
    with patch.dict(m.os.environ, {"MONITOR_DB": str(tmp_path / "analysis-db"), "ACCOUNTS_ENV": "",
                                  "MONITOR_INGEST_TOKEN": "test-ingest-token", "SAMPLING_INTERVAL_MINUTES": "30"}), patch.object(m.Collector, "start"):
        with TestClient(m.create_app()) as client:
            client.app.state.collector.accounts = {n: m.Account(n, "fake") for n in "ab"}
            yield client


def analysis_run(begin, account="a", state="success", latency=100, **extra):
    return {"account": account, "due": begin - 7200, "submit_begin": begin, "state": state,
            "t4x2": True, "start_latency_seconds": latency, "start_latency_upper": latency, **extra}


def test_analysis_actual_submit_vn_dates_thresholds_and_unknowns():
    begin = m.timestamp("2026-10-16T17:00:00Z")  # Saturday 00:00 VN, due Friday 22:00
    runs = [analysis_run(begin, latency=n) for n in (300, 301, 600, 601)]
    runs += [analysis_run(begin, state=s) for s in ("failed", "wrong_gpu", "cancelled", "invalid_output")]
    runs += [analysis_run(begin, state=s) for s in ("running", "ambiguous", "foreign_status")]
    runs += [analysis_run(begin, state="success", t4x2=False),
             {"account": "a", "due": begin, "state": "skipped", "skip_reason": "account_active"}]
    bucket = m.analysis_buckets(runs, {"a": None}, begin + 301, "weekend")[0]
    assert bucket["attempted"] == 12 and bucket["resolved"] == 8
    assert (bucket["fast"], bucket["slow"], bucket["over10"], bucket["failures"]) == (1, 2, 1, 4)
    assert bucket["unknown"] == 4 and bucket["pending"] == 0 and bucket["overdue_unconfirmed"] == 2
    assert bucket["success_rate"] == 1 / 8
    assert bucket["rate_lower"] == 1 / 12 and bucket["rate_upper"] == 5 / 12
    assert bucket["median_seconds"] == 450.5 and bucket["p90_seconds"] == pytest.approx(600.7)
    assert bucket["day_count"] == 1 and not bucket["eligible"] and bucket["rank"] is None
    assert bucket["skip_reasons"] == {"account_active": 1}
    assert sum(b["attempted"] for b in m.analysis_buckets(runs, {"a": None}, begin, "weekday")) == 0
    pending = m.analysis_counts([analysis_run(begin, state="running")], begin + 300)
    assert pending["pending"] == 1 and pending["resolved"] == 0 and pending["failures"] == 0


def test_analysis_equal_account_weights_missing_coverage_and_resolved_dates():
    begin = m.timestamp("2026-10-01T01:00:00Z")
    runs = [analysis_run(begin + (i % 7) * 86400) for i in range(100)]
    runs += [analysis_run(begin + (i % 7) * 86400, account="b", state="failed") for i in range(20)]
    bucket = m.analysis_buckets(runs, {"a": None, "b": None}, begin + 30 * 86400)[4]
    assert bucket["eligible"] and bucket["resolved"] == 120 and bucket["success_rate"] == .5
    assert bucket["rate_lower"] == bucket["rate_upper"] == .5
    assert [a["attempted"] for a in bucket["accounts"]] == [100, 20]
    missing = m.analysis_buckets(runs, {"a": None, "b": None, "c": None}, begin + 30 * 86400)[4]
    assert missing["success_rate"] is None and not missing["eligible"]
    assert missing["rate_lower"] == 1 / 3 and missing["rate_upper"] == 2 / 3
    assert missing["coverage"]["missing_accounts"] == ["c"]
    assert missing["reason"] == "missing_account_coverage"
    # Unknown observations on seven dates cannot supply resolved-date coverage.
    sparse = [analysis_run(begin) for _ in range(20)] + [analysis_run(begin + i * 86400, state="queued") for i in range(7)]
    counts = m.analysis_counts(sparse, begin + 30 * 86400)
    assert counts["day_count"] == 7 and counts["resolved_day_count"] == 1 and not counts["eligible"]
    assert m.wilson(0, 0) == (None, None)
    assert m.wilson(1, 8) == pytest.approx((.022417491450056642, .47088818221285356))


def test_analysis_api_contract_filters_and_summary_compatibility(analysis_client):
    client = analysis_client
    store = client.app.state.collector.store
    begin = m.timestamp("2026-10-16T17:00:00Z")
    run = insert_scheduled(store, due=begin - 7200)
    run.update(analysis_run(begin))
    store.save(run)
    body = client.get("/api/analysis?account=A&day_type=weekend").json()
    assert body["schema_version"] == 2 and body["source"] == "probe" and body["metric"] == "submit_to_start"
    assert body["policy"] == {"min_resolved": 20, "min_days": 7, "interval_minutes": 30}
    assert len(body["buckets"]) == 12 and body["buckets"][0]["fast"] == 1
    sampling = body["sampling"]
    assert {key: sampling[key] for key in ("observed_opportunities", "attempted", "skipped", "interval_minutes", "quota_inferable")} == {
        "observed_opportunities": 1, "attempted": 1, "skipped": 0, "interval_minutes": 30, "quota_inferable": False}
    assert sampling["phase"] == "preliminary" and sampling["known_successes"] == 1 and sampling["day_count"] == 1
    assert sampling["recommendation"] == "collect_not_evaluate" and sampling["heldout"]["status"] == "unavailable"
    assert "independent Bernoulli" in body["definitions"]["wilson"]
    assert body["statistics_window"]["limit"] == 5000
    assert client.get("/api/summary").json()["schema_version"] == 1
    for query in ("source=other", "day_type=holiday", "account=missing"):
        assert client.get("/api/analysis?" + query).status_code == 422
    for query in ("mode=other", "horizon_hours=0", "horizon_hours=49", "horizon_hours=1.5", "source=other"):
        assert client.get("/api/decision?" + query).status_code == 422
    decision = client.get("/api/decision").json()
    assert decision["status"] == "abstain" and decision["reason"] == "missing_baseline"
    assert decision["promise"] is False


def test_decision_upcoming_dates_baseline_waiting_and_uncertainty(tmp_path):
    collector = m.Collector(m.Store(tmp_path / "db"), [m.Account("a")])
    now = m.timestamp("2026-10-16T16:30:00Z")  # Friday 23:30 VN
    historical = []
    # Seven prior weekday/weekend dates for each candidate, >=20 resolved.
    for hour, day_type, latency in ((22, "weekday", 100), (0, "weekend", 50), (2, "weekend", 60)):
        date = m.datetime(2026, 8, 1, hour, tzinfo=m.LOCAL)
        dates = []
        while len(dates) < 7:
            if m.local_day_type(date) == day_type:
                dates.append(date.timestamp())
            date += m.timedelta(days=1)
        historical += [analysis_run(dates[i % 7], latency=latency) for i in range(21)]
    result = m.build_decision(collector, historical, "probe", "a", now, horizon_hours=3)
    assert [c["day_type"] for c in result["candidates"]] == ["weekday", "weekend", "weekend"]
    assert [c["hour_start"] for c in result["candidates"]] == [22, 0, 2]
    assert [c["total_p50_seconds"] for c in result["candidates"]] == [100, 1850, 9060]
    assert result["candidates"][1]["accounts"][0]["total_p90_seconds"] == 1850
    assert all(c["bucket"]["eligible"] for c in result["candidates"])
    assert result["status"] == "tentative" and result["recommended_at"] == m.iso(now)
    assert result["selection_basis"] == "descriptive_p50_p90_pareto"
    assert "no_latency_confidence" in result["limitations"]
    missing = [r for r in historical if m.datetime.fromtimestamp(r["submit_begin"], m.LOCAL).hour != 22]
    assert m.build_decision(collector, missing, "probe", "a", now)["reason"] == "missing_baseline"
    weekend = m.build_decision(collector, historical, "probe", "a", now, mode="schedule", horizon_hours=3, day_type="weekend")
    assert len(weekend["candidates"]) == 2
    # Missing per-account coverage must prevent recommendations even with ample pooled evidence.
    collector.accounts["b"] = m.Account("b")
    assert m.build_decision(collector, historical, "probe", None, now)["status"] == "abstain"


def production_payload():
    begin = time.time() - 1000
    return {"event_id": "wrapper-event-1", "account": "a", "requested_ref": "a/production-job", "ref": "a/production-job", "version": 12,
            "submit_begin": m.iso(begin), "submit_ack": m.iso(begin + 2), "accelerator": "NvidiaTeslaT4",
            "push_outcome": "accepted", "timing": {"start_utc": m.iso(begin + 50), "end_utc": m.iso(begin + 60),
            "gpu_names": ["Tesla T4", "Tesla T4"], "gpu_query_ok": True}}


def test_production_authenticated_idempotent_metadata_never_blocks_probe(analysis_client):
    client = analysis_client
    headers = {"x-monitor-token": "test-ingest-token"}
    payload = production_payload()
    assert client.post("/api/production/register", json=payload).status_code == 401
    first = client.post("/api/production/register", json=payload, headers=headers)
    assert first.status_code == 200 and first.json()["created"] is True
    assert first.json()["primary_timing_accepted"] is False
    assert client.post("/api/production/register", json=payload, headers=headers).json()["created"] is False
    assert client.post("/api/production/register", json={**payload, "version": 13}, headers=headers).status_code == 409
    store = client.app.state.collector.store
    with store.connect() as db:
        persisted = json.loads(db.execute("SELECT data FROM production_events").fetchone()[0])
    assert persisted["reported_timing"] == payload["timing"] and persisted["reported_timing_provenance"] == "self_reported"
    assert persisted["state"] == "external_pending" and not persisted["active"]
    assert store.runs() == [] and store.claim(insert_scheduled(store), time.time())
    analysis = client.get("/api/analysis?source=production&account=a").json()
    assert analysis["accounts"][0]["attempted"] == 1 and analysis["accounts"][0]["unknown"] == 1
    assert analysis["accounts"][0]["resolved"] == 0 and analysis["accounts"][0]["median_seconds"] is None
    # Replay survives restart without replacement of the original stored data.
    assert not m.Store(store.path).register_production(payload, time.time())
    client.app.state.ingest_token = ""
    assert client.post("/api/production/register", json=payload, headers=headers).status_code == 503


@pytest.mark.parametrize("changes", [
    {"account": "unregistered"}, {"ref": "b/foreign"}, {"version": None}, {"version": True},
    {"version": "12"}, {"version": 0}, {"accelerator": "P100"}, {"event_id": ""},
    {"push_outcome": "success"}, {"submit_begin": "2026-10-01T12:00:00"},
    {"submit_ack": "bad"}, {"timing": {"fetched_version": 12}}, {"secret": "must-not-store"},
])
def test_production_registration_validation(analysis_client, changes):
    response = analysis_client.post("/api/production/register", json={**production_payload(), **changes},
                                    headers={"x-monitor-token": "test-ingest-token"})
    assert response.status_code == 422
    with analysis_client.app.state.collector.store.connect() as db:
        assert db.execute("SELECT count(*) FROM production_events").fetchone()[0] == 0


def test_analysis_window_bounded_and_unknown_preserved(tmp_path):
    collector = m.Collector(m.Store(tmp_path / "db"), [m.Account("a")])
    begin = m.timestamp("2026-10-01T01:00:00Z")
    runs = [analysis_run(begin + i, state="unrecognized") for i in range(5001)]
    with patch.object(collector.store, "runs", return_value=runs) as load:
        body = m.build_analysis(collector, account="a", now=begin + 10000)
    load.assert_called_once_with("state!='scheduled'", limit=5001)
    assert body["statistics_window"]["truncated"] and body["statistics_window"]["count"] == 5000
    assert body["accounts"][0]["unknown"] == 5000 and body["accounts"][0]["resolved"] == 0


@pytest.mark.parametrize("outcome", ["ambiguous", "rejected"])
def test_production_unconfirmed_version_and_explicit_rejection(analysis_client, outcome):
    payload = {**production_payload(), "version": None, "push_outcome": outcome}
    assert analysis_client.post("/api/production/register", json=payload,
                                headers={"x-monitor-token": "test-ingest-token"}).status_code == 200
    account = analysis_client.get("/api/analysis?source=production&account=a").json()["accounts"][0]
    assert account["attempted"] == 1 and account["median_seconds"] is None
    assert account["failures"] == (1 if outcome == "rejected" else 0)
    assert account["unknown"] == (0 if outcome == "rejected" else 1)


def test_production_concurrent_replays_are_atomic(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = m.Store(tmp_path / "db")
    payload = production_payload()
    with ThreadPoolExecutor(max_workers=4) as pool:
        created = list(pool.map(lambda _: store.register_production(payload, time.time()), range(8)))
    assert sum(created) == 1
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM production_events").fetchone()[0] == 1


def test_decision_supported_comparison_remains_tentative(tmp_path):
    collector = m.Collector(m.Store(tmp_path / "db"), [m.Account("a")])
    now = m.timestamp("2026-10-13T01:00:00Z")  # Tuesday 08:00 VN
    runs = []
    date = m.datetime(2026, 9, 1, 8, tzinfo=m.LOCAL)
    dates = []
    while len(dates) < 7:
        if m.local_day_type(date) == "weekday":
            dates.append(date.timestamp())
        date += m.timedelta(days=1)
    for i in range(21):
        runs.extend([analysis_run(dates[i % 7], latency=100),
                     analysis_run(dates[i % 7] + 7200, latency=700)])
    result = m.build_decision(collector, runs, "probe", "a", now, horizon_hours=2)
    assert result["status"] == "tentative" and result["recommended_at"] == m.iso(now)
    assert result["certainty"] == "descriptive_only" and result["promise"] is False


@pytest.mark.parametrize("outcome", ["ambiguous", "rejected"])
def test_production_nullable_timeout_identity_contract(analysis_client, outcome):
    client = analysis_client
    payload = {**production_payload(), "push_outcome": outcome, "ref": None, "version": None, "submit_ack": None}
    headers = {"x-monitor-token": "test-ingest-token"}
    assert client.post("/api/production/register", json=payload, headers=headers).status_code == 200
    stored = client.app.state.collector.store.production_event(payload["event_id"])
    assert stored["ref"] is None and stored["submit_ack"] is None
    assert stored["requested_ref"] == "a/production-job" and stored["evidence_provenance"] == "registration_metadata"
    with patch.object(client.app.state.collector.transport, "invoke") as invoke:
        result = client.post("/api/production/verify", json={"event_id": payload["event_id"]}, headers=headers).json()
    invoke.assert_not_called()
    assert not result["verified"] and result["reason"] == "unconfirmed_submission_identity"


@pytest.mark.parametrize("changes", [{"requested_ref": None}, {"requested_ref": "b/foreign"},
                                      {"ref": None}, {"submit_ack": None}, {"ref": "a/different"}])
def test_production_accepted_identity_boundaries(analysis_client, changes):
    assert analysis_client.post("/api/production/register", json={**production_payload(), **changes},
                                headers={"x-monitor-token": "test-ingest-token"}).status_code == 422


def provider_receipt(payload):
    return {"ok": True, "data": {"status": "complete", "requested_version": payload["version"],
            "timing": {**payload["timing"], "run_id": payload["event_id"], "ref": payload["ref"],
                       "fetched_version": payload["version"], "source": "production"}}}


def test_production_verified_provider_receipt_primary_analysis_and_replay(analysis_client):
    client = analysis_client
    payload, headers = production_payload(), {"x-monitor-token": "test-ingest-token"}
    assert client.post("/api/production/register", json=payload, headers=headers).status_code == 200
    collector = client.app.state.collector
    receipt = provider_receipt(payload)
    # Provider timing, rather than registration timing, determines the result.
    receipt["data"]["timing"]["start_utc"] = m.iso(m.timestamp(payload["submit_begin"]) + 301)
    receipt["data"]["timing"]["end_utc"] = m.iso(m.timestamp(payload["submit_begin"]) + 302)
    with patch.object(collector.transport, "invoke", return_value=receipt) as invoke:
        result = client.post("/api/production/verify", json={"event_id": payload["event_id"]}, headers=headers)
        assert result.status_code == 200 and result.json()["verified"]
        invoke.assert_called_once_with("production_output", {"source": "production", "ref": payload["ref"],
                                       "version": 12, "event_id": payload["event_id"]}, collector.accounts["a"])
        assert client.post("/api/production/verify", json={"event_id": payload["event_id"]}, headers=headers).json()["reason"] == "already_verified"
        assert invoke.call_count == 1
    account = client.get("/api/analysis?source=production&account=a").json()["accounts"][0]
    assert account["resolved"] == 1 and account["slow"] == 1 and account["unknown"] == 0
    assert account["median_seconds"] == 301 and account["failures"] == 0
    stored = collector.store.production_event(payload["event_id"])
    assert not stored["active"] and stored["evidence_provenance"] == "provider_artifact"
    assert stored["reported_timing"] == payload["timing"] and collector.store.runs() == []
    assert client.post("/api/production/register", json=payload, headers=headers).json()["created"] is False
    assert collector.store.production_event(payload["event_id"]) == stored


@pytest.mark.parametrize("invalid", ["queued", "running", "error", "missing", "foreign_nonce", "foreign_ref",
                                     "version", "requested_version", "wrong_gpu", "clock", "source", "transport"])
def test_production_verification_invalid_evidence_remains_unknown(analysis_client, invalid):
    client = analysis_client
    payload, headers = production_payload(), {"x-monitor-token": "test-ingest-token"}
    client.post("/api/production/register", json=payload, headers=headers)
    receipt = provider_receipt(payload)
    data, timing = receipt["data"], receipt["data"]["timing"]
    if invalid in ("queued", "running", "error"):
        data["status"] = invalid
    elif invalid == "missing":
        data["timing"] = None
    elif invalid == "foreign_nonce":
        timing["run_id"] = "another-event"
    elif invalid == "foreign_ref":
        timing["ref"] = "b/production-job"
    elif invalid == "version":
        timing["fetched_version"] = 13
    elif invalid == "requested_version":
        data["requested_version"] = 13
    elif invalid == "wrong_gpu":
        timing["gpu_names"] = ["Tesla T4"]
    elif invalid == "clock":
        timing["end_utc"] = m.iso(time.time() + 10000)
    elif invalid == "source":
        timing["source"] = "probe"
    elif invalid == "transport":
        receipt = {"ok": False, "error": "secret-must-not-leak"}
    collector = client.app.state.collector
    with patch.object(collector.transport, "invoke", return_value=receipt):
        result = client.post("/api/production/verify", json={"event_id": payload["event_id"]}, headers=headers)
    assert result.status_code == 200 and not result.json()["verified"]
    assert "secret-must-not-leak" not in result.text
    stored = collector.store.production_event(payload["event_id"])
    assert stored["state"] == "external_pending" and stored["evidence_provenance"] == "registration_metadata"
    account = client.get("/api/analysis?source=production&account=a").json()["accounts"][0]
    assert account["unknown"] == 1 and account["resolved"] == account["failures"] == 0


def test_production_verify_auth_and_body_contract(analysis_client):
    client = analysis_client
    headers = {"x-monitor-token": "test-ingest-token"}
    assert client.post("/api/production/verify", json={"event_id": "missing"}).status_code == 401
    assert client.post("/api/production/verify", json={"event_id": "missing"}, headers=headers).status_code == 404
    assert client.post("/api/production/verify", json={"event_id": "missing", "timing": {}}, headers=headers).status_code == 422
    client.app.state.ingest_token = ""
    assert client.post("/api/production/verify", json={"event_id": "missing"}, headers=headers).status_code == 503


@pytest.mark.parametrize("status", ["COMPLETE", "QUEUED", "RUNNING", "ERROR"])
@pytest.mark.parametrize("foreign", [False, True])
def test_helper_production_exact_version_owned_slug_and_artifact(status, foreign, capsys):
    api, endpoint = MagicMock(), MagicMock()
    api.build_kaggle_client.return_value.__enter__.return_value.kernels.kernels_api_client = endpoint
    endpoint.get_kernel_session_status.return_value = SimpleNamespace(status=SimpleNamespace(name=status), failure_message="")
    endpoint.list_kernel_session_output.return_value = SimpleNamespace(files=[
        SimpleNamespace(file_name="queue-monitor-timing.json", url="https://mock.invalid/receipt")])
    payload = {"source": "production", "ref": "a/production-job", "version": 12, "event_id": "event-1"}
    artifact = {"run_id": "foreign" if foreign else "event-1", "ref": payload["ref"], "source": "production"}
    download = MagicMock()
    download.__enter__.return_value.iter_content.return_value = [json.dumps(artifact).encode()]
    with patch.dict(sys.modules, mocked_kaggle_modules(api)), patch.dict(m.os.environ, {"KAGGLE_USERNAME": "a"}), \
            patch.object(m.sys, "stdin", io.StringIO(json.dumps(payload))), patch("requests.get", return_value=download) as get, \
            patch("requests.sessions.Session.request", side_effect=AssertionError("Live HTTP forbidden")):
        m.helper("production_output")
    result = json.loads(capsys.readouterr().out)
    status_request = endpoint.get_kernel_session_status.call_args.args[0]
    assert status_request.user_name == "a" and status_request.kernel_slug == "production-job"
    assert status_request.to_dict()["versionLabel"] == "v12"
    if status != "COMPLETE":
        assert result["ok"] and result["data"]["timing"] is None
        endpoint.list_kernel_session_output.assert_not_called()
        get.assert_not_called()
    else:
        output_request = endpoint.list_kernel_session_output.call_args.args[0]
        assert output_request.to_dict()["versionLabel"] == "v12"
        assert output_request.user_name == "a" and output_request.kernel_slug == "production-job"
        assert result["ok"] is (not foreign)
        if not foreign:
            assert result["data"]["timing"]["fetched_version"] == 12
    api.kernels_push.assert_not_called()


def test_helper_production_foreign_owner_rejected_without_read(capsys):
    api = MagicMock()
    payload = {"source": "production", "ref": "b/production-job", "version": 12, "event_id": "event-1"}
    with patch.dict(sys.modules, mocked_kaggle_modules(api)), patch.dict(m.os.environ, {"KAGGLE_USERNAME": "a"}), \
            patch.object(m.sys, "stdin", io.StringIO(json.dumps(payload))):
        m.helper("production_output")
    assert not json.loads(capsys.readouterr().out)["ok"]
    api.build_kaggle_client.assert_not_called()


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "foreign_ref", "failure_status", "malformed"])
def test_helper_production_missing_or_invalid_artifact_never_success(invalid, capsys):
    api, endpoint = MagicMock(), MagicMock()
    api.build_kaggle_client.return_value.__enter__.return_value.kernels.kernels_api_client = endpoint
    endpoint.get_kernel_session_status.return_value = SimpleNamespace(
        status=SimpleNamespace(name="COMPLETE"), failure_message="provider failure" if invalid == "failure_status" else "")
    file = SimpleNamespace(file_name="queue-monitor-timing.json", url="https://mock.invalid/receipt")
    files = [] if invalid == "missing" else [file, file] if invalid == "duplicate" else [file]
    endpoint.list_kernel_session_output.return_value = SimpleNamespace(files=files)
    payload = {"source": "production", "ref": "a/job", "version": 7, "event_id": "event-7"}
    artifact = {"run_id": "event-7", "ref": "a/foreign" if invalid == "foreign_ref" else "a/job"}
    download = MagicMock()
    download.__enter__.return_value.iter_content.return_value = [b"not-json" if invalid == "malformed" else json.dumps(artifact).encode()]
    with patch.dict(sys.modules, mocked_kaggle_modules(api)), patch.dict(m.os.environ, {"KAGGLE_USERNAME": "a"}), \
            patch.object(m.sys, "stdin", io.StringIO(json.dumps(payload))), patch("requests.get", return_value=download) as get, \
            patch("requests.sessions.Session.request", side_effect=AssertionError("Live HTTP forbidden")):
        m.helper("production_output")
    result = json.loads(capsys.readouterr().out)
    if invalid == "failure_status":
        assert result["ok"] and result["data"]["timing"] is None
        endpoint.list_kernel_session_output.assert_not_called()
    else:
        assert not result["ok"]
    if invalid in ("missing", "duplicate", "failure_status"):
        get.assert_not_called()


def test_production_verify_transport_exception_retains_metadata(analysis_client):
    client = analysis_client
    headers, payload = {"x-monitor-token": "test-ingest-token"}, production_payload()
    client.post("/api/production/register", json=payload, headers=headers)
    collector = client.app.state.collector
    original = collector.store.production_event(payload["event_id"])
    with patch.object(collector.transport, "invoke", side_effect=TimeoutError("private-provider-url")):
        response = client.post("/api/production/verify", json={"event_id": payload["event_id"]}, headers=headers)
    assert response.status_code == 200 and not response.json()["verified"]
    assert "private-provider-url" not in response.text
    assert collector.store.production_event(payload["event_id"]) == original


@pytest.mark.parametrize("outcome", ["ambiguous", "rejected"])
def test_foreign_uncertain_observed_ref_is_metadata_only(analysis_client, outcome):
    payload = {**production_payload(), "push_outcome": outcome, "ref": "foreign/returned-job", "submit_ack": None}
    headers = {"x-monitor-token": "test-ingest-token"}
    client = analysis_client
    assert client.post("/api/production/register", json=payload, headers=headers).status_code == 200
    stored = client.app.state.collector.store.production_event(payload["event_id"])
    assert stored["ref"] == "foreign/returned-job" and stored["requested_ref"] == "a/production-job"
    assert stored["evidence_provenance"] == "registration_metadata"
    with patch.object(client.app.state.collector.transport, "invoke") as invoke:
        result = client.post("/api/production/verify", json={"event_id": payload["event_id"]}, headers=headers).json()
    invoke.assert_not_called()
    assert not result["verified"] and result["reason"] == "unconfirmed_submission_identity"


@pytest.mark.parametrize("alias", ["absent", "matching", "foreign", "only_event_id"])
def test_production_artifact_nonce_contract(analysis_client, alias):
    client, payload = analysis_client, production_payload()
    headers = {"x-monitor-token": "test-ingest-token"}
    client.post("/api/production/register", json=payload, headers=headers)
    receipt = provider_receipt(payload)
    timing = receipt["data"]["timing"]
    if alias != "absent":
        timing["event_id"] = "foreign" if alias == "foreign" else payload["event_id"]
    if alias == "only_event_id":
        timing.pop("run_id")
    with patch.object(client.app.state.collector.transport, "invoke", return_value=receipt):
        result = client.post("/api/production/verify", json={"event_id": payload["event_id"]}, headers=headers).json()
    assert result["verified"] is (alias in ("absent", "matching"))


@pytest.mark.parametrize("case,expected", [("tradeoff", "tradeoff"), ("equivalent", "equivalent"),
                                          ("future", "historical_comparison"), ("margin", "historical_comparison")])
def test_decision_descriptive_pareto_actual_builder(tmp_path, case, expected):
    collector = m.Collector(m.Store(tmp_path / "db"), [m.Account("a")])
    # Current local 09:50; next 2h boundary costs 600s, both weekday.
    now = m.timestamp("2026-10-13T02:50:00Z")
    if case == "tradeoff":
        current = [100] * 16 + [10000] * 5
        future = [110] * 21
    elif case == "equivalent":
        current, future = [710] * 21, [100] * 21
    elif case == "margin":
        current, future = [730] * 21, [100] * 21
    else:
        current, future = [2000] * 21, [410] * 21
    date = m.datetime(2026, 9, 1, 8, tzinfo=m.LOCAL)
    dates = []
    while len(dates) < 7:
        if m.local_day_type(date) == "weekday":
            dates.append(date.timestamp())
        date += m.timedelta(days=1)
    runs = [analysis_run(dates[i % 7], latency=value) for i, value in enumerate(current)]
    runs += [analysis_run(dates[i % 7] + 7200, latency=value) for i, value in enumerate(future)]
    result = m.build_decision(collector, runs, "probe", "a", now, horizon_hours=1)
    assert all(c["bucket"]["eligible"] for c in result["candidates"])
    assert result["reason"] == expected and result["certainty"] == "descriptive_only"
    assert result["selection_basis"] == "descriptive_p50_p90_pareto" and not result["promise"]
    if case in ("tradeoff", "equivalent"):
        assert result["status"] == "abstain" and result["recommended_at"] is None
    else:
        assert result["status"] == "tentative" and result["recommended_at"] == m.iso(now + 600)
        # Both buckets' fast-rate bounds are identical, proving rate intervals
        # neither supply nor gate the latency comparison.
        if case == "future":
            assert all(c["bucket"]["rate_upper"] == 0 for c in result["candidates"])


def test_decision_sole_candidate_is_not_comparative_claim(tmp_path):
    collector = m.Collector(m.Store(tmp_path / "db"), [m.Account("a")])
    now = m.timestamp("2026-10-13T01:00:00Z")
    dates = [m.timestamp(f"2026-09-{day:02d}T01:00:00Z") for day in (1, 2, 3, 4, 7, 8, 9)]
    runs = [analysis_run(dates[i % 7]) for i in range(21)]
    result = m.build_decision(collector, runs, "probe", "a", now, horizon_hours=1)
    assert result["status"] == "tentative" and result["reason"] == "sole_eligible_candidate"
    assert result["certainty"] == "descriptive_only" and "no_latency_confidence" in result["limitations"]


def test_decision_both_fast_candidates_do_not_use_rate_intervals(tmp_path):
    collector = m.Collector(m.Store(tmp_path / "db"), [m.Account("a")])
    now = m.timestamp("2026-10-13T02:59:50Z")
    dates = [m.timestamp(f"2026-09-{day:02d}T01:00:00Z") for day in (1, 2, 3, 4, 7, 8, 9)]
    runs = [analysis_run(dates[i % 7], latency=100) for i in range(21)]
    runs += [analysis_run(dates[i % 7] + 7200, latency=11) for i in range(21)]
    result = m.build_decision(collector, runs, "probe", "a", now, horizon_hours=1)
    assert [c["total_p50_seconds"] for c in result["candidates"]] == [100, 21]
    assert all(c["bucket"]["rate_lower"] == c["bucket"]["rate_upper"] == 1 for c in result["candidates"])
    assert result["status"] == "tentative" and result["recommended_at"] == m.iso(now + 10)
    assert result["certainty"] == "descriptive_only" and not result["promise"]


def test_real_wrapper_generated_artifact_through_helper_and_verification(analysis_client):
    tracking_spec = importlib.util.spec_from_file_location("backend_tracking_contract", Path(__file__).parents[1] / "track_production.py")
    tracking = importlib.util.module_from_spec(tracking_spec)
    tracking_spec.loader.exec_module(tracking)
    payload, client = production_payload(), analysis_client
    writer = MagicMock()
    # Execute the real generator with GPU and filesystem output substituted;
    # this exercises its actual run_id-only JSON rather than a hand-built nonce.
    with patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="Tesla T4\nTesla T4\n")), \
            patch("pathlib.Path", return_value=writer):
        exec(compile(tracking.timing_start(payload["event_id"], payload["ref"]) + tracking.TIMING_END,
                     "wrapper-instrumentation", "exec"), {})
    artifact = writer.write_text.call_args.args[0]
    assert json.loads(artifact)["run_id"] == payload["event_id"] and "event_id" not in json.loads(artifact)
    api, endpoint = MagicMock(), MagicMock()
    api.build_kaggle_client.return_value.__enter__.return_value.kernels.kernels_api_client = endpoint
    endpoint.get_kernel_session_status.return_value = SimpleNamespace(status=SimpleNamespace(name="COMPLETE"), failure_message="")
    endpoint.list_kernel_session_output.return_value = SimpleNamespace(files=[
        SimpleNamespace(file_name="queue-monitor-timing.json", url="https://mock.invalid/generated")])
    download = MagicMock()
    download.__enter__.return_value.iter_content.return_value = [artifact.encode()]
    def invoke(operation, request, account):
        output = io.StringIO()
        with patch.dict(sys.modules, mocked_kaggle_modules(api)), patch.dict(m.os.environ, {"KAGGLE_USERNAME": account.username}), \
                patch.object(m.sys, "stdin", io.StringIO(json.dumps(request))), patch.object(m.sys, "stdout", output), \
                patch("requests.get", return_value=download), \
                patch("requests.sessions.Session.request", side_effect=AssertionError("Live HTTP forbidden")):
            m.helper(operation)
        return json.loads(output.getvalue())
    headers = {"x-monitor-token": "test-ingest-token"}
    assert client.post("/api/production/register", json=payload, headers=headers).status_code == 200
    with patch.object(client.app.state.collector.transport, "invoke", side_effect=invoke):
        result = client.post("/api/production/verify", json={"event_id": payload["event_id"]}, headers=headers)
    assert result.status_code == 200 and result.json()["verified"]
    assert client.app.state.collector.store.production_event(payload["event_id"])["evidence_provenance"] == "provider_artifact"


@pytest.mark.parametrize("interval", [30, 60, 120])
def test_configured_balanced_sampling_and_persistent_suppression(tmp_path, interval):
    store = m.Store(tmp_path / "db", sampling_interval_minutes=interval)
    store.plan(100, list("abcd"))
    original = store.runs()
    eligible = [r for r in original if r["state"] == "scheduled"]
    assert len(eligible) == 8
    for window in (0, interval * 60):
        rows = [r for r in eligible if r["window"] == window]
        assert {r["account"] for r in rows} == set("abcd") and len(rows) == 4
        assert all(window + r["slot"] * 450 <= r["due"] < window + (r["slot"] + 1) * 450 for r in rows)
    suppressed = [r for r in original if r["state"] == "skipped"]
    assert len(suppressed) == (interval // 30 - 1) * 4
    assert all(r["skip_reason"] == "sampling_interval" and r.get("submit_begin") is None for r in suppressed)
    store.plan(101, list("abcd"))
    assert store.runs() == original
    restarted = m.Store(store.path, sampling_interval_minutes=interval)
    restarted.plan(102, list("abcd"))
    assert restarted.runs() == original
    assert store.get_meta("sampling_interval_minutes") == interval
    assert set(store.next_probes(1800)) == set("abcd")


def test_sampling_interval_switch_preserves_history_active_and_deduplicates(tmp_path):
    store = m.Store(tmp_path / "db", sampling_interval_minutes=30)
    store.plan(1900, ["a", "b"])
    scheduled = store.runs("state='scheduled' AND window=1800")
    active = next(r for r in scheduled if r["account"] == "a")
    assert store.claim(active, 1900)
    active.update(state="queued", version=1, submit_ack=1901)
    store.save(active)
    original = {r["id"]: r for r in store.runs()}
    slower = m.Store(store.path, sampling_interval_minutes=120)
    slower.plan(1902, ["b", "a"])
    assert slower.runs("active=1") == [active]
    persisted = {r["id"]: r for r in slower.runs()}
    assert set(original) <= set(persisted)
    for id_, old in original.items():
        assert persisted[id_]["due"] == old["due"] and persisted[id_]["window"] == old["window"]
        if old["state"] == "scheduled":
            assert persisted[id_]["state"] == "skipped" and persisted[id_]["skip_reason"] == "sampling_interval"
    faster = m.Store(store.path, sampling_interval_minutes=30)
    faster.plan(1903, ["a", "b"])
    assert faster.runs("active=1") == [active]
    after = faster.runs()
    keys = [(r["account"], r["window"]) for r in after]
    assert len(keys) == len(set(keys))
    assert all(r["state"] == "skipped" for r in after if r["id"] in persisted and persisted[r["id"]]["state"] == "skipped")


@pytest.mark.parametrize("invalid", [0, 15, 45, 180, True, "30.0", "", "invalid"])
def test_sampling_interval_validation(invalid, tmp_path):
    with pytest.raises(ValueError, match="SAMPLING_INTERVAL_MINUTES"):
        m.Store(tmp_path / "db", sampling_interval_minutes=invalid)


def test_sampling_interval_environment_and_general_strata(tmp_path):
    with patch.dict(m.os.environ, {"SAMPLING_INTERVAL_MINUTES": "60"}):
        store = m.Store(tmp_path / "db")
    assert store.sampling_interval_minutes == 60
    collector = m.Collector(store, [m.Account("a")])
    body = m.build_analysis(collector, now=100)
    assert body["sampling"]["interval_minutes"] == body["policy"]["interval_minutes"] == 60
    assert not body["sampling"]["quota_inferable"]
    plan = m.stratified_plan(7200, ["a", "b", "c"], random.Random(2), window_seconds=7200)
    assert all(7200 + r["slot"] * 2400 <= r["due"] < 7200 + (r["slot"] + 1) * 2400 for r in plan)
    with pytest.raises(ValueError, match="window_seconds"):
        m.stratified_plan(0, ["a"], window_seconds=0)


def test_sampling_future_suppressed_slots_are_not_observed_opportunities(tmp_path):
    store = m.Store(tmp_path / "db", sampling_interval_minutes=120)
    store.plan(100, ["a"])
    collector = m.Collector(store, [m.Account("a")])
    sampling = m.build_analysis(collector, now=100)["sampling"]
    assert sampling["observed_opportunities"] == sampling["skipped"] == 0
    # By 04:00 UTC, all three intentionally suppressed 30m opportunities have
    # passed, but none were submissions or evidence about GPU quota.
    sampling = m.build_analysis(collector, now=7200)["sampling"]
    assert sampling["observed_opportunities"] == sampling["skipped"] == 3
    assert sampling["attempted"] == sampling["known_successes"] == 0
    assert not sampling["quota_inferable"]


def heldout_history():
    # Exactly fourteen local dates, all with both 2h windows observed for each
    # account. Earlier seven train on hour08; heldout deliberately favors hour10.
    begin = m.timestamp("2026-09-01T01:00:00Z")
    runs = []
    for day in range(14):
        for name in "ab":
            for hour_offset in (0, 7200):
                train = day < 7
                fast = (train and hour_offset == 0) or (not train and hour_offset == 7200)
                for _ in range(3):
                    runs.append(analysis_run(begin + day * 86400 + hour_offset, account=name, latency=100 if fast else 700))
    return runs


def test_heldout_split_training_selection_no_leakage_and_noncausal_rates():
    runs = heldout_history()
    now = m.timestamp("2026-10-01T00:00:00Z")
    diagnostic = m.heldout_sampling_diagnostic(runs, {"a": None, "b": None}, now)
    assert diagnostic["status"] == "available" and diagnostic["selected_hours"] == [8]
    assert diagnostic["training_record_count"] == diagnostic["heldout_record_count"] == 84
    assert len(diagnostic["training_dates"]) == len(diagnostic["heldout_dates"]) == 7
    assert max(diagnostic["training_dates"]) < min(diagnostic["heldout_dates"])
    assert not set(diagnostic["training_dates"]) & set(diagnostic["heldout_dates"])
    assert diagnostic["baseline"]["success_rate"] == .5 and diagnostic["selected"]["success_rate"] == 0
    assert diagnostic["observed_rate_difference"] == -.5
    assert not diagnostic["causal"] and not diagnostic["counterfactual"] and diagnostic["savings_seconds"] is None
    assert "selected_windows_are_subset_of_baseline" in diagnostic["limitations"]
    # Changing heldout labels cannot change the trained selected windows.
    changed = [dict(r, start_latency_seconds=1, start_latency_upper=1) if m.datetime.fromtimestamp(r["submit_begin"], m.LOCAL).date().isoformat() in diagnostic["heldout_dates"] else r for r in runs]
    changed_diagnostic = m.heldout_sampling_diagnostic(changed, {"a": None, "b": None}, now)
    assert changed_diagnostic["selected_hours"] == [8] and changed_diagnostic["training_selected_fast_rate"] == 1
    assert changed_diagnostic["selected"]["success_rate"] == 1


def test_heldout_unknown_equal_weights_no_data_coverage():
    runs = heldout_history()
    now = m.timestamp("2026-10-01T00:00:00Z")
    dates = sorted({m.datetime.fromtimestamp(r["submit_begin"], m.LOCAL).date().isoformat() for r in runs})
    def is_test(r):
        return m.datetime.fromtimestamp(r["submit_begin"], m.LOCAL).date().isoformat() in dates[-7:]
    # Add disproportionate fast samples for account a without changing training.
    extra = [dict(r, start_latency_seconds=100, start_latency_upper=100) for r in runs if is_test(r) and r["account"] == "a"] * 9
    result = m.heldout_sampling_diagnostic(runs + extra, {"a": None, "b": None}, now)
    assert result["selected"]["success_rate"] == pytest.approx(.45)  # (.9 + 0)/2
    unknown = [dict(r, state="external_pending", source="production") if is_test(r) and r["account"] == "b" else r for r in runs]
    result = m.heldout_sampling_diagnostic(unknown, {"a": None, "b": None}, now)
    assert result["status"] == "unavailable" and result["reason"] == "insufficient_heldout_account_coverage"
    assert result["selected"]["unknown"] == 21 and result["selected"]["success_rate"] is None
    assert result["selected"]["rate_lower"] == 0 and result["selected"]["rate_upper"] == .5
    missing = [r for r in runs if not (is_test(r) and r["account"] == "b")]
    result = m.heldout_sampling_diagnostic(missing, {"a": None, "b": None}, now)
    assert result["selected"]["coverage"]["missing_accounts"] == ["b"]
    assert result["selected"]["accounts"][1]["attempted"] == 0
    assert result["selected"]["rate_upper"] == .5


def test_heldout_requires_dates_and_eligible_training():
    runs = heldout_history()
    now = m.timestamp("2026-10-01T00:00:00Z")
    sparse = [r for r in runs if r["submit_begin"] < m.timestamp("2026-09-14T00:00:00Z")]
    assert m.heldout_sampling_diagnostic(sparse, {"a": None, "b": None}, now)["reason"] == "insufficient_dates"
    assert m.heldout_sampling_diagnostic(runs, {"a": None, "b": None, "c": None}, now)["reason"] == "insufficient_training_bucket_evidence"


def test_heldout_analysis_source_account_and_daytype_filters(tmp_path):
    collector = m.Collector(m.Store(tmp_path / "db", sampling_interval_minutes=120), [m.Account("a"), m.Account("b")])
    probe = heldout_history()
    now = m.timestamp("2026-10-01T00:00:00Z")
    with patch.object(collector.store, "runs", return_value=probe):
        body = m.build_analysis(collector, account="a", now=now)
        assert body["sampling"]["heldout"]["baseline"]["coverage"]["configured"] == 1
        assert all(a["account"] == "a" for a in body["sampling"]["heldout"]["baseline"]["accounts"])
        weekend = m.build_analysis(collector, day_type="weekend", now=now)
        assert weekend["sampling"]["heldout"]["status"] == "unavailable"
        assert weekend["sampling"]["recommendation"] == "collect_not_evaluate"
        assert weekend["sampling"]["interval_minutes"] == 120
    # A different source cannot borrow the probe's training/heldout dates.
    production = m.build_analysis(collector, source="production", now=now)
    assert production["sampling"]["day_count"] == 0 and production["sampling"]["heldout"]["status"] == "unavailable"


def test_credentials_parser_and_isolation():
    accounts = m.parse_accounts({"ACCOUNT_COUNT": "1", "ACCOUNT_1_USERNAME": "tester", "ACCOUNT_1_API_TOKEN": "secret"})
    assert "secret" not in repr(accounts)
    with patch.dict(m.os.environ, {"KAGGLE_KEY": "other-secret", "ACCOUNTS_ENV": "private", "VERBOSE": "1"}):
        env = m.isolated_env(accounts[0], "isolated")
    assert env["KAGGLE_API_TOKEN"] == "secret"
    assert "KAGGLE_KEY" not in env and "ACCOUNTS_ENV" not in env and "VERBOSE" not in env
    production = m.parse_accounts({"KAGGLE_ACCOUNTS_COUNT": "1", "KAGGLE_ACCOUNT_1_USERNAME": "tester",
                                  "KAGGLE_ACCOUNT_1_API_TOKEN": "secret"})
    assert production[0].username == "tester" and production[0].token == "secret"
    actual_format = {"PRODUCTION_KAGGLE_ACCOUNT_COUNT": "4"}
    for i in range(1, 5):
        actual_format.update({f"PRODUCTION_KAGGLE_ACCOUNT_{i}_USERNAME": f"tester{i}",
                              f"PRODUCTION_KAGGLE_ACCOUNT_{i}_API_TOKEN": f"fake-token-{i}"})
    parsed = m.parse_accounts(actual_format)
    assert [a.username for a in parsed] == [f"tester{i}" for i in range(1, 5)]
    assert [a.token for a in parsed] == [f"fake-token-{i}" for i in range(1, 5)]
    case_changed = m.parse_accounts({"ACCOUNT_COUNT": "1", "ACCOUNT_1_USERNAME": "Owner", "ACCOUNT_1_API_TOKEN": "fake"})
    assert case_changed[0].username == "owner"
    with pytest.raises(ValueError, match="Duplicate"):
        m.parse_accounts({"ACCOUNT_COUNT": "2", "ACCOUNT_1_USERNAME": "x", "ACCOUNT_1_API_TOKEN": "s",
                          "ACCOUNT_2_USERNAME": "X", "ACCOUNT_2_API_TOKEN": "s"})


def test_strata_and_persistent_plan(tmp_path):
    plan = m.stratified_plan(1800, ["a", "b", "c", "d"], random.Random(4))
    assert len(plan) == 4
    assert {r["slot"] for r in plan} == {0, 1, 2, 3}
    for name in ("a", "b", "c", "d"):
        rows = [p for p in plan if p["account"] == name]
        assert len(rows) == 1
        assert all(1800 + r["slot"] * 450 <= r["due"] < 1800 + (r["slot"] + 1) * 450 for r in rows)
    store = m.Store(tmp_path / "db")
    store.plan(1900, ["a", "b"])
    original = store.runs()
    # Current window plus the upcoming window are planned so next_probe is always
    # available before the window opens.
    assert len(original) == 4
    assert {r["window"] for r in original} == {1800, 3600}
    store.plan(1901, ["a", "b"])
    assert store.runs() == original
    # Restart catches up missing windows, retaining original randomized instants.
    m.Store(tmp_path / "db").plan(5500, ["a", "b"])
    final = store.runs()
    assert len(final) == 8
    assert {r["window"] for r in final} == {1800, 3600, 5400, 7200}
    assert {r["id"] for r in original} <= {r["id"] for r in final}


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4, 7])
def test_general_n_strata_have_one_independent_due_per_account(count):
    names = [f"account{i}" for i in range(count)]
    plan = m.stratified_plan(0, names, random.Random(8))
    assert len(plan) == count
    assert sorted(r["account"] for r in plan) == sorted(names)
    if not count:
        return
    width = 1800 / count
    assert all(r["slot"] * width <= r["due"] < (r["slot"] + 1) * width for r in plan)
    if count > 1:
        assert len({r["due"] - r["slot"] * width for r in plan}) == count


def test_exact_push_identity_and_status():
    assert m.parse_push({"ref": "a/queue-monitor-probe", "versionNumber": 7}, "a/queue-monitor-probe") == 7
    for data in ({"ref": "a/queue-monitor-probe", "kernel_id": 7},
                 {"ref": "b/queue-monitor-probe", "version_number": 7},
                 {"ref": "a/queue-monitor-probe", "version_number": True},
                 {"ref": "a/queue-monitor-probe", "version_number": 7, "error": True}):
        with pytest.raises(ValueError):
            m.parse_push(data, "a/queue-monitor-probe")
    assert m.parse_status("KernelWorkerStatus.COMPLETE") == "complete"
    with pytest.raises(ValueError):
        m.parse_status("not complete")


class FakeTransport:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def invoke(self, operation, payload, account=None):
        self.calls.append((operation, payload.copy()))
        return self.result


def insert_scheduled(store, account="a", slot=0, due=None):
    due = time.time() if due is None else due
    run = {"id": f"{account}-{slot}", "account": account, "slot": slot, "window": 0,
           "position": 0, "due": due, "state": "scheduled"}
    with store.connect() as db:
        db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,0,?)",
                   (run["id"], account, 0, slot, due, 0, "scheduled", json.dumps(run)))
    return run


def test_no_overlap_and_ambiguous_restart(tmp_path):
    store = m.Store(tmp_path / "db")
    store.set_meta("next_window", int(time.time() // 1800) * 1800 + 1800)
    first = insert_scheduled(store)
    assert store.claim(first, time.time())
    assert not store.claim(insert_scheduled(store, slot=1), time.time())
    transport = FakeTransport({"ok": False, "error": "api_timeout"})
    collector = m.Collector(store, [m.Account("a", "fake")], True, transport)
    with patch.object(m.threading.Thread, "start"):
        collector.start()
    assert store.runs("active=1")[0]["state"] == "ambiguous"
    collector.ready = True
    collector.tick(time.time())
    assert not transport.calls
    assert store.runs("id='a-1'")[0]["skip_reason"] == "account_active"


def test_catchup_not_burst_and_disabled_skips(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    store.set_meta("next_window", int(now // 1800) * 1800 + 1800)
    insert_scheduled(store, due=now - 200)
    insert_scheduled(store, account="b", due=now - 1)
    insert_scheduled(store, account="c", due=now)
    transport = FakeTransport({"ok": False, "error": "api_timeout"})
    collector = m.Collector(store, [m.Account(n, "fake") for n in "abc"], True, transport)
    collector.ready = True
    collector.tick(now)
    assert len(transport.calls) == 1
    assert store.runs("id='a-0'")[0]["skip_reason"] == "missed_slot"
    collector.tick(now + 1)
    assert len(transport.calls) == 1
    collector.auto = False
    collector.tick(now + 2)
    assert store.runs("id='c-0'")[0]["skip_reason"] == "probes_disabled"


def active_run(store, now):
    run = insert_scheduled(store, due=now)
    store.claim(run, now - 100)
    run.update(state="submitted", version=5, submit_ack=now - 98)
    store.save(run)
    return run


def test_missed_queue_is_unknown_and_stale_version_cannot_complete(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    run = active_run(store, now)
    transport = FakeTransport({"ok": True, "data": {"status": "complete", "requested_version": 4}})
    collector = m.Collector(store, [m.Account("a", "fake")], transport=transport)
    collector.poll(run, now)
    assert run["state"] == "submitted" and run["errors"] == 1
    transport.result["data"]["requested_version"] = 5
    collector.poll(run, now)
    assert run["state"] == "collecting"
    assert run.get("queue_lower_seconds") is None
    assert run.get("queue_upper_seconds") is None
    assert transport.calls[-1][1]["version"] == 5


def test_timeout_keeps_account_active_and_backs_off(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    run = active_run(store, now)
    run["submit_begin"] = now - 4000
    transport = FakeTransport({"ok": False, "http_status": 429, "error": "HTTPError"})
    collector = m.Collector(store, [m.Account("a", "fake")], transport=transport)
    collector.poll(run, now)
    assert run["observation_timed_out"]
    assert store.runs("active=1")
    assert run["next_poll"] > now + 20
    with store.connect() as db:
        call = dict(db.execute("SELECT * FROM calls").fetchone())
    assert call["http_status"] == 429 and call["latency_ms"] >= 0


def test_probe_identity_gpu_evidence_and_clock_bounds():
    run = {"id": "uuid", "ref": "a/queue-monitor-probe", "version": 5,
           "submit_begin": 1000, "submit_ack": 1010}
    data = {"run_id": "uuid", "ref": run["ref"], "fetched_version": 5,
            "start_utc": m.iso(1305), "end_utc": m.iso(1306), "gpu_names": ["Tesla T4", "Tesla T4"], "gpu_query_ok": True}
    result = m.validate_timing(data, run, 1400)
    assert result["t4x2"] and result["start_latency_lower"] == 295 and result["start_latency_upper"] == 305
    with pytest.raises(ValueError, match="Stale"):
        m.validate_timing(dict(data, run_id="old"), run, 1400)
    with pytest.raises(ValueError, match="version"):
        m.validate_timing(dict(data, fetched_version=4), run, 1400)
    assert not m.validate_timing(dict(data, gpu_names=["Tesla T4"]), run, 1400)["t4x2"]
    assert not m.validate_timing(dict(data, gpu_names=["NVIDIA T400", "NVIDIA T400"]), run, 1400)["t4x2"]


def test_statistics_failure_denominator_timezone_and_insufficient_days():
    due = m.timestamp("2026-10-12T18:00:00+00:00")  # 01:00 next day in Vietnam
    base = {"due": due, "submit_begin": due, "state": "success", "t4x2": True,
            "start_latency_seconds": 100, "start_latency_upper": 100,
            "first_queued": due + 5, "queue_lower_seconds": 15, "queue_upper_seconds": 40}
    rows = [base, dict(base, start_latency_seconds=300, start_latency_upper=305),
            dict(base, state="failed"), dict(base, state="ambiguous"),
            {"due": due, "state": "skipped"}]
    hour = m.hourly_stats(rows)[1]
    assert hour["submit_to_start_p50_seconds"] == 200 and hour["submit_to_start_p90_seconds"] == 280
    assert hour["median_queue_seconds"] == 40 and hour["p90_seconds"] == 40
    assert hour["sample_count"] == 4 and hour["queue_sample_count"] == 4
    assert hour["within_5m_t4x2"] == 1 and hour["denominator"] == 4
    assert hour["success_5m_rate"] == .25
    assert hour["failures"] == 1 and hour["incomplete"] == 1 and hour["skipped"] == 1
    assert hour["unknown_queue"] == 0
    assert hour["day_count"] == 1 and hour["rank"] is None
    for day in (1, 2):
        rows.append(dict(base, due=due + day * 86400))
    assert m.hourly_stats(rows)[1]["rank"] == 1


def test_single_instance_lock(tmp_path):
    first = m.InstanceLock(tmp_path / "db")
    second = m.InstanceLock(tmp_path / "db")
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_observed_queue_bounds_and_cancel_requested_is_not_terminal(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    run = active_run(store, now)
    transport = FakeTransport({"ok": True, "data": {"status": "queued", "requested_version": 5}})
    collector = m.Collector(store, [m.Account("a", "fake")], transport=transport)
    with patch.object(m.time, "time", return_value=now):
        collector.poll(run, now)
    with patch.object(m.time, "time", return_value=now + 15):
        collector.poll(run, now + 15)
    transport.result["data"]["status"] = "running"
    with patch.object(m.time, "time", return_value=now + 30):
        collector.poll(run, now + 30)
    assert run["queue_lower_seconds"] == 15
    assert run["queue_upper_seconds"] == 130
    transport.result["data"]["status"] = "CANCEL_REQUESTED"
    collector.poll(run, now + 31)
    assert run["state"] == "submitted" and store.runs("active=1")
    transport.result["data"]["status"] = "CANCEL_ACKNOWLEDGED"
    collector.poll(run, now + 32)
    # Acknowledged cancellation is the one provider status mapped to the explicit
    # terminal cancelled state; it must release the account.
    assert run["state"] == "cancelled" and not store.runs("active=1")


def test_unacceptable_requires_observed_queue_lower_strictly_over_600(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    run = active_run(store, now)
    run.update(submit_begin=now - 2000, submit_ack=now - 1990)
    transport = FakeTransport({"ok": True, "data": {"status": "queued", "requested_version": 5}})
    collector = m.Collector(store, [m.Account("a", "fake")], transport=transport)
    for offset in (0, 600):
        with patch.object(m.time, "time", return_value=now + offset):
            collector.poll(run, now + offset)
        assert not run.get("unacceptable", False)
        assert run["queue_lower_seconds"] == offset
    with patch.object(m.time, "time", return_value=now + 601):
        collector.poll(run, now + 601)
    assert run["unacceptable"] is True
    assert store.runs("active=1")  # Threshold never fabricates cancellation.
    evidence = m.validate_timing({"run_id": run["id"], "ref": run["ref"], "fetched_version": 5,
                                 "start_utc": m.iso(now + 602), "end_utc": m.iso(now + 603),
                                 "gpu_names": ["Tesla T4", "Tesla T4"], "gpu_query_ok": True}, run, now + 604)
    assert evidence["submit_to_start_unacceptable"] is True
    assert "unacceptable" not in evidence  # Separate metric cannot overwrite queue classification.


def test_unknown_queue_is_never_replaced_by_submit_to_start_in_hour_metrics():
    due = m.timestamp("2026-10-12T18:00:00+00:00")
    run = {"id": "unknown", "account": "a", "due": due, "submit_begin": due,
           "state": "success", "t4x2": True, "start_latency_seconds": 90,
           "start_latency_lower": 85, "start_latency_upper": 90}
    normalized = m.frontend_run(run)
    assert normalized["queue_estimate_seconds"] is None
    assert normalized["queue_lower_seconds"] is None and normalized["queue_upper_seconds"] is None
    assert normalized["submit_to_start_seconds"] == 90
    hour = m.hourly_stats([run])[1]
    assert hour["median_queue_seconds"] is None and hour["p50_seconds"] is None and hour["p90_seconds"] is None
    assert hour["queue_sample_count"] == 0 and hour["sample_count"] == 1
    assert hour["unknown_queue"] == 1
    assert hour["submit_to_start_p90_seconds"] == 90 and hour["success_5m_rate"] == 1


def test_summary_frontend_contract_with_known_and_unknown_queue(tmp_path):
    from fastapi.testclient import TestClient
    entered, release = m.threading.Event(), m.threading.Event()

    def blocked_check(self, operation, payload, account=None):
        assert operation == "check"
        entered.set()
        release.wait(5)
        return {"ok": True}

    due = m.timestamp("2026-10-12T18:00:00+00:00")
    with patch.dict(m.os.environ, {"MONITOR_DB": str(tmp_path / "contract-db"), "ACCOUNTS_ENV": "",
                                  "AUTO_PROBES": "false", "MIN_RANK_DAYS": "3"}), \
            patch.object(m.Transport, "invoke", blocked_check):
        with TestClient(m.create_app()) as client:
            try:
                assert entered.wait(2)
                collector = client.app.state.collector
                collector.accounts = {n: m.Account(n, "fake") for n in "abc"}
                store = collector.store
                completed = insert_scheduled(store, due=due)
                completed.update(state="success", submit_begin=due, submit_ack=due + 5,
                                 start=due + 100, end=due + 101, t4x2=True,
                                 start_latency_seconds=100, start_latency_lower=95, start_latency_upper=100,
                                 first_queued=due + 10, queue_lower_seconds=20, queue_upper_seconds=40)
                store.save(completed)
                failed = insert_scheduled(store, slot=1, due=due + 1)
                failed.update(state="failed", submit_begin=due + 1, first_queued=due + 10,
                              queue_lower_seconds=30, queue_upper_seconds=80)
                store.save(failed)
                queued = insert_scheduled(store, account="b", due=due + 2)
                queued.update(state="queued", submit_begin=due + 2, first_queued=due + 10, queue_lower_seconds=10)
                store.save(queued)
                skipped = insert_scheduled(store, account="c", due=due + 3)
                skipped.update(state="skipped", skip_reason="probes_disabled")
                store.save(skipped)
                unknown = insert_scheduled(store, slot=2, due=due + 4)
                unknown.update(state="success", submit_begin=due + 4, t4x2=True,
                               start_latency_seconds=200, start_latency_upper=200)
                store.save(unknown)
                future_due = time.time() + 1800
                insert_scheduled(store, account="b", slot=1, due=future_due)
                response = client.get("/api/summary")
                assert response.status_code == 200
                body = response.json()
                assert {"meta", "accounts", "runs", "hours"} <= body.keys()
                meta = body["meta"]
                assert meta["health"] == "ok" and meta["timezone"] == "Asia/Ho_Chi_Minh"
                assert meta["sample_count"] == 4 and meta["day_count"] == 1
                assert meta["auto_probes"] is False and meta["cancellation_supported"] is False
                assert meta["stale_after_seconds"] == 2400
                assert meta["data_updated_at"] == m.iso(due + 101)
                assert "submit-to-start" in meta["success_5m_metric"] and "T4x2" in meta["success_5m_metric"]
                runs = {r["id"]: r for r in body["runs"]}
                assert runs[completed["id"]]["status"] == "completed"
                assert runs[failed["id"]]["status"] == "failed"
                assert runs[skipped["id"]]["submitted_at"] is None
                assert runs[completed["id"]]["submitted_at"] == m.iso(due)
                assert runs[completed["id"]]["started_at"] == m.iso(due + 100)
                assert runs[completed["id"]]["queue_estimate_seconds"] == 40
                assert runs[completed["id"]]["submit_to_start_seconds"] == 100
                assert runs[unknown["id"]]["queue_estimate_seconds"] is None
                hour = body["hours"][1]
                assert hour["sample_count"] == 4 and hour["day_count"] == 1
                assert hour["queue_sample_count"] == 2 and hour["median_queue_seconds"] == 60
                assert hour["unknown_queue"] == 2  # Still queued and missed queued.
                assert hour["p90_seconds"] == 76 and hour["success_5m_rate"] == .5
                assert hour["rank_eligible"] is False and hour["rank"] is None
                assert hour["failures"] == 1 and hour["incomplete"] == 1 and hour["skipped"] == 1
                accounts = {a["id"]: a for a in body["accounts"]}
                assert accounts["b"]["name"] == "b" and accounts["b"]["status"] == "queued"
                assert accounts["b"]["next_probe_utc"] == m.iso(future_due)
            finally:
                release.set()


def test_installed_sdk_readiness_without_auth_or_network():
    result = m.Transport().invoke("check", {})
    assert result["ok"], result
    assert result["version_requests"] is True


def mocked_kaggle_modules(api):
    # Never import kaggle's __init__: its automatic authentication can contact
    # Kaggle. Real generated kagglesdk request/response classes remain in use.
    modules = {name: ModuleType(name) for name in ("kaggle", "kaggle.api", "kaggle.api.kaggle_api_extended")}
    modules["kaggle.api.kaggle_api_extended"].KaggleApi = lambda: api
    return modules


@pytest.mark.parametrize("operation", ["status", "output"])
@pytest.mark.parametrize("version", [2, 12])
def test_helper_serializes_exact_version_label_for_status_and_output(operation, version, capsys):
    from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionStatusRequest, ApiListKernelSessionOutputRequest
    payload = {"id": "probe-identity", "ref": "owner/queue-monitor-probe", "version": version}
    api, endpoint = MagicMock(), MagicMock()
    api.build_kaggle_client.return_value.__enter__.return_value.kernels.kernels_api_client = endpoint
    endpoint.get_kernel_session_status.return_value = SimpleNamespace(status=SimpleNamespace(name="QUEUED"), failure_message="")
    endpoint.list_kernel_session_output.return_value = SimpleNamespace(files=[
        SimpleNamespace(file_name="timing.json", url="https://mock.invalid/timing"),
        SimpleNamespace(file_name="queue-monitor-probe.log", url="https://mock.invalid/log")])
    download = MagicMock()
    download.__enter__.return_value.iter_content.return_value = [json.dumps({"run_id": payload["id"]}).encode()]
    with patch.dict(sys.modules, mocked_kaggle_modules(api)), patch.object(m.sys, "stdin", io.StringIO(json.dumps(payload))), \
            patch("requests.get", return_value=download) as get, \
            patch("requests.sessions.Session.request", side_effect=AssertionError("Live HTTP forbidden")):
        m.helper(operation)
    result = json.loads(capsys.readouterr().out)
    assert result["ok"], result
    method = endpoint.get_kernel_session_status if operation == "status" else endpoint.list_kernel_session_output
    method.assert_called_once()
    request = method.call_args.args[0]
    cls = ApiGetKernelSessionStatusRequest if operation == "status" else ApiListKernelSessionOutputRequest
    assert isinstance(request, cls)
    assert request.user_name == "owner" and request.kernel_slug == "queue-monitor-probe"
    assert request.version_label == f"v{version}"
    assert request.to_dict()["versionLabel"] == f"v{version}"  # Actual SDK wire serialization.
    if operation == "status":
        assert result["data"]["requested_version"] == version
        get.assert_not_called()
        endpoint.list_kernel_session_output.assert_not_called()
    else:
        assert result["data"]["fetched_version"] == version
        get.assert_called_once_with("https://mock.invalid/timing", stream=True, timeout=(10, 20))
        endpoint.get_kernel_session_status.assert_not_called()


@pytest.mark.parametrize("version", [True, 0, "2"])
def test_helper_rejects_invalid_version_without_endpoint_call(version, capsys):
    api = MagicMock()
    payload = {"ref": "owner/queue-monitor-probe", "version": version}
    with patch.dict(sys.modules, mocked_kaggle_modules(api)), patch.object(m.sys, "stdin", io.StringIO(json.dumps(payload))), \
            patch("requests.sessions.Session.request", side_effect=AssertionError("Live HTTP forbidden")):
        m.helper("status")
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and result["error"] == "ValueError"
    api.build_kaggle_client.assert_not_called()


@pytest.mark.parametrize("response_ref", ["owner/queue-monitor-probe", "", "other/queue-monitor-probe"])
def test_helper_push_reads_real_sdk_response_identity_and_version(response_ref, tmp_path, capsys):
    from kagglesdk.kernels.types.kernels_api_service import ApiSaveKernelResponse
    # Official generated wire fields are ref and versionNumber, exposed in
    # Python as ref and version_number (with a versionNumber alias).
    response = ApiSaveKernelResponse.from_dict({"ref": response_ref, "versionNumber": 12})
    assert response.version_number == response.versionNumber == 12
    api = MagicMock()
    api.kernels_push.return_value = response
    payload = {"id": "probe-identity", "ref": "owner/queue-monitor-probe"}
    with patch.dict(sys.modules, mocked_kaggle_modules(api)), patch.object(m.sys, "stdin", io.StringIO(json.dumps(payload))), \
            patch.object(m.tempfile, "tempdir", str(tmp_path)), \
            patch("requests.sessions.Session.request", side_effect=AssertionError("Live HTTP forbidden")):
        m.helper("push")
    result = json.loads(capsys.readouterr().out)
    assert result == {"ok": True, "data": {"ref": response_ref, "version_number": 12, "error": False}}
    assert api.kernels_push.call_args.kwargs == {"acc": "NvidiaTeslaT4"}
    if response_ref == payload["ref"]:
        assert m.parse_push(result["data"], payload["ref"]) == 12
    else:
        with pytest.raises(ValueError, match="identity"):
            m.parse_push(result["data"], payload["ref"])  # No fallback to assumed owner/slug.


@pytest.mark.parametrize("source_kind,matched", [("exact", True), ("crlf", True),
                                                 ("other_nonce", False), ("appended", False),
                                                 ("substring", False), ("foreign_ref", False)])
def test_helper_reconcile_requires_full_source_and_owned_ref(source_kind, matched, capsys):
    from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelRequest, ApiGetKernelResponse
    payload = {"id": "unique-current-run", "ref": "owner/queue-monitor-probe"}
    source = m.probe_script(payload["id"], payload["ref"])
    ref = payload["ref"]
    if source_kind == "crlf":
        source = source.replace("\n", "\r\n")
    elif source_kind == "other_nonce":
        source = m.probe_script("other-run", payload["ref"])
    elif source_kind == "appended":
        source += "print('additional-code')\n"
    elif source_kind == "substring":
        source = f"# nonce: {payload['id']}\n"
    elif source_kind == "foreign_ref":
        ref = "foreign/queue-monitor-probe"
    response = ApiGetKernelResponse.from_dict({"metadata": {"ref": ref, "currentVersionNumber": 1},
                                              "blob": {"source": source}})
    api = MagicMock()
    endpoint = api.build_kaggle_client.return_value.__enter__.return_value.kernels.kernels_api_client
    endpoint.get_kernel.return_value = response
    with patch.dict(sys.modules, mocked_kaggle_modules(api)), patch.object(m.sys, "stdin", io.StringIO(json.dumps(payload))), \
            patch("requests.sessions.Session.request", side_effect=AssertionError("Live HTTP forbidden")):
        m.helper("reconcile")
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["ok"] is True and result["data"]["matched"] is matched
    assert result["data"]["version_number"] == 1
    assert "source" not in output and payload["id"] not in output
    request = endpoint.get_kernel.call_args.args[0]
    assert isinstance(request, ApiGetKernelRequest)
    assert request.user_name == "owner" and request.kernel_slug == "queue-monitor-probe"
    assert not request.version_label  # Latest metadata is checked solely for nonce recovery.
    api.kernels_push.assert_not_called()
    endpoint.get_kernel_session_status.assert_not_called()


def test_empty_push_ref_is_reconciled_without_resubmission(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    store.set_meta("next_window", int(now // 1800) * 1800 + 1800)
    run = insert_scheduled(store, due=now)
    transport = FakeTransport({"ok": True, "data": {"ref": "", "version_number": 1, "error": False}})
    collector = m.Collector(store, [m.Account("a", "fake")], auto=True, transport=transport)
    collector.ready = True
    with patch.object(m.time, "time", return_value=now):
        collector.tick(now)
    saved = store.runs("id='a-0'")[0]
    assert saved["state"] == "ambiguous" and saved["version"] is None
    assert saved["push_response"] == {"ref": None, "version_number": 1, "error": False}
    assert saved["push_end"] == now and saved["next_poll"] == now + 60
    assert saved["push_response_received"] is True
    collector.tick(now + 59)
    assert [operation for operation, _ in transport.calls] == ["push"]
    transport.result = {"ok": True, "data": {"matched": True, "ref": "a/queue-monitor-probe", "version_number": 1}}
    with patch.object(m.time, "time", return_value=now + 60):
        collector.tick(now + 60)
    saved = store.runs("id='a-0'")[0]
    assert [operation for operation, _ in transport.calls] == ["push", "reconcile"]
    assert saved["state"] == "submitted" and saved["version"] == 1
    assert saved["submit_ack"] == now and saved["submit_ack_uncertain"] is False
    assert saved["metadata_recovered"] is True and store.runs("active=1")


def test_restart_recovers_ambiguous_push_and_preserves_original_end(tmp_path):
    path = tmp_path / "db"
    store = m.Store(path)
    now = time.time()
    store.set_meta("next_window", int(now // 1800) * 1800 + 1800)
    run = insert_scheduled(store, due=now)
    store.claim(run, now - 100)
    # Old backend ambiguous row: last_api_end is the only persisted push end.
    run.update(state="ambiguous", last_api_end=now - 90, next_poll=now)
    store.save(run)
    transport = FakeTransport({"ok": True, "data": {"matched": True, "ref": run["ref"], "version_number": 1}})
    restarted = m.Collector(m.Store(path), [m.Account("a", "fake")], True, transport)
    with patch.object(m.threading.Thread, "start"), patch.object(m.time, "time", return_value=now):
        restarted.start()
        restarted.tick(now)
    recovered = store.runs("id='a-0'")[0]
    assert recovered["state"] == "submitted" and recovered["version"] == 1
    assert recovered["submit_begin"] == now - 100
    assert recovered["submit_ack"] == now - 90 and recovered["push_end"] == now - 90
    assert recovered["last_api_end"] == now
    assert recovered["submit_ack_uncertain"] is True  # Old response envelope was not retained.
    assert [op for op, _ in transport.calls] == ["reconcile"]


def test_unmatched_reconcile_keeps_blocked_and_original_push_end(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    store.set_meta("next_window", int(now // 1800) * 1800 + 1800)
    run = insert_scheduled(store, due=now)
    store.claim(run, now - 100)
    run.update(state="ambiguous", last_api_end=now - 80, next_poll=now)
    store.save(run)
    insert_scheduled(store, slot=1, due=now)
    transport = FakeTransport({"ok": True, "data": {"matched": False, "ref": run["ref"], "version_number": 9}})
    collector = m.Collector(store, [m.Account("a", "fake")], True, transport)
    collector.ready = True
    for offset in (0, 59, 60):
        with patch.object(m.time, "time", return_value=now + offset):
            collector.tick(now + offset)
    saved = store.runs("id='a-0'")[0]
    assert saved["state"] == "ambiguous" and saved["version"] is None
    assert saved["push_end"] == now - 80 and saved["next_poll"] == now + 120
    assert [operation for operation, _ in transport.calls] == ["reconcile", "reconcile"]
    assert store.runs("id='a-1'")[0]["skip_reason"] == "account_active"
    assert store.runs("active=1")


def test_restart_during_push_recovers_without_inventing_original_ack(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    store.set_meta("next_window", int(now // 1800) * 1800 + 1800)
    run = insert_scheduled(store, due=now)
    store.claim(run, now - 30)  # No response or push-end timestamp survived.
    transport = FakeTransport({"ok": True, "data": {"matched": True, "ref": run["ref"], "version_number": 1}})
    collector = m.Collector(m.Store(tmp_path / "db"), [m.Account("a", "fake")], True, transport)
    with patch.object(m.threading.Thread, "start"), patch.object(m.time, "time", return_value=now):
        collector.start()
    assert store.runs("active=1")[0]["state"] == "ambiguous"
    with patch.object(m.time, "time", return_value=now + 60):
        collector.tick(now + 60)
    recovered = store.runs("id='a-0'")[0]
    assert recovered["state"] == "submitted" and recovered["metadata_recovered"]
    assert recovered["push_end"] is None
    assert recovered["submit_ack"] == now + 60 and recovered["submit_ack_uncertain"] is True
    assert recovered["submit_ack_source"] == "reconciliation_observation_upper_bound"
    assert [operation for operation, _ in transport.calls] == ["reconcile"]


def test_explicit_provider_push_rejection_releases_account_sanitized(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    store.set_meta("next_window", int(now // 1800) * 1800 + 1800)
    insert_scheduled(store, due=now)
    transport = FakeTransport({"ok": True, "data": {"ref": "", "version_number": 0, "error": True}})
    collector = m.Collector(store, [m.Account("a", "fake")], True, transport)
    collector.ready = True
    collector.tick(now)
    saved = store.runs("id='a-0'")[0]
    assert saved["state"] == "failed" and saved["error"] == "provider_push_rejected"
    assert saved["push_response"] == {"ref": None, "version_number": None, "error": True}
    assert not store.runs("active=1")
    assert [operation for operation, _ in transport.calls] == ["push"]
    diagnostic = m.push_diagnostic({"ref": "secret-bearing-url?token=secret", "version_number": True,
                                   "error": "secret error text"})
    assert diagnostic == {"ref": None, "version_number": None, "error": False}


def legacy_database(path, runs):
    # Reproduce the old binary account index and historical slot constraint.
    with m.sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE runs (
            id TEXT PRIMARY KEY, account TEXT NOT NULL, window INTEGER NOT NULL,
            slot INTEGER NOT NULL, due REAL NOT NULL, position INTEGER NOT NULL,
            state TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL,
            UNIQUE(account,window,slot));
            CREATE UNIQUE INDEX one_active_account ON runs(account) WHERE active=1;''')
        for run in runs:
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
                       (run["id"], run["account"], run["window"], run["slot"], run["due"], run["position"],
                        run["state"], int(run["state"] in m.ACTIVE), json.dumps(run)))


def test_account_case_config_change_migrates_and_polls_original_no_second_push(tmp_path):
    now = time.time()
    active = {"id": "original", "account": "Owner", "ref": "Owner/queue-monitor-probe",
              "window": 0, "slot": 0, "due": now - 100, "position": 0,
              "state": "submitted", "submit_begin": now - 100, "submit_ack": now - 90,
              "version": 1, "next_poll": now, "errors": 0}
    scheduled = {"id": "next", "account": "OWNER", "window": 0, "slot": 1,
                 "due": now, "position": 1, "state": "scheduled"}
    path = tmp_path / "legacy-db"
    legacy_database(path, [active, scheduled])
    store = m.Store(path)
    store.set_meta("next_window", int(now // 1800) * 1800 + 1800)
    config = m.parse_accounts({"ACCOUNT_COUNT": "1", "ACCOUNT_1_USERNAME": "Owner", "ACCOUNT_1_API_TOKEN": "fake"})
    transport = FakeTransport({"ok": True, "data": {"status": "running", "requested_version": 1}})
    collector = m.Collector(store, config, True, transport)
    collector.ready = True
    collector.tick(now)
    assert [op for op, _ in transport.calls] == ["status"]
    assert transport.calls[0][1]["id"] == "original"
    assert transport.calls[0][1]["account"] == "owner"
    assert transport.calls[0][1]["ref"] == "owner/queue-monitor-probe"
    recovered = store.runs("id='original'")[0]
    assert recovered["state"] == "running" and recovered["probe_ref"] == active["ref"]
    assert store.runs("id='next'")[0]["skip_reason"] == "account_active"
    with store.connect() as db:
        assert {r[0] for r in db.execute("SELECT account FROM runs")} == {"owner"}
    timing = {"run_id": active["id"], "ref": active["ref"], "fetched_version": 1,
              "start_utc": m.iso(now - 10), "end_utc": m.iso(now - 9),
              "gpu_names": ["Tesla T4", "Tesla T4"], "gpu_query_ok": True}
    assert m.validate_timing(timing, recovered, now)["t4x2"]


def test_case_migration_conflicting_active_records_rejects_atomically(tmp_path):
    path = tmp_path / "conflict-db"
    rows = [{"id": f"job-{i}", "account": account, "ref": f"{account}/queue-monitor-probe",
             "window": 0, "slot": 0, "due": 1000, "position": 0, "state": "ambiguous"}
            for i, account in enumerate(("Owner", "owner"))]
    legacy_database(path, rows)
    with pytest.raises(RuntimeError, match="migration conflict: multiple active"):
        m.Store(path)
    with m.sqlite3.connect(path) as db:
        persisted = [json.loads(row[0]) for row in db.execute("SELECT data FROM runs ORDER BY id")]
        assert persisted == rows
        assert db.execute("SELECT count(*) FROM runs WHERE active=1").fetchone()[0] == 2
        assert "COLLATE NOCASE" not in db.execute("SELECT sql FROM sqlite_master WHERE name='one_active_account'").fetchone()[0]


def test_case_migration_retains_colliding_completed_history_and_is_idempotent(tmp_path):
    path = tmp_path / "history-db"
    rows = [{"id": f"job-{i}", "account": account, "ref": f"{account}/queue-monitor-probe",
             "window": 0, "slot": 0, "due": 1000, "position": 0, "state": state,
             "gpu_names": ["Tesla T4", "Tesla T4"], "submit_begin": 1000}
            for i, (account, state) in enumerate((("Owner", "success"), ("owner", "failed")))]
    legacy_database(path, rows)
    store = m.Store(path)
    normalized = store.runs()
    assert len(normalized) == 2 and {r["id"] for r in normalized} == {"job-0", "job-1"}
    assert all(r["account"] == "owner" and r["ref"] == "owner/queue-monitor-probe" for r in normalized)
    assert {r["state"] for r in normalized} == {"success", "failed"}
    assert all(r["slot"] == 0 and r["window"] == 0 for r in normalized)
    assert m.Store(path).runs() == normalized
    # Planner retries cannot add duplicate schedule keys after removal of the
    # legacy binary unique constraint (including retained historical collisions).
    store.set_meta("next_window", 0)
    store.plan(1001, ["owner"])
    after = store.runs()
    # Window 0 already had a colliding historical slot for this account, so the
    # planner cannot add a duplicate there; it only adds the upcoming window.
    window_zero = [r for r in after if r["window"] == 0]
    assert {r["id"] for r in window_zero} == {"job-0", "job-1"}
    assert [r["window"] for r in after if r["id"] not in {"job-0", "job-1"}] == [1800]
    store.plan(1002, ["owner"])
    assert store.runs() == after


def test_sql_nocase_guard_blocks_standalone_case_variant_claim(tmp_path):
    store = m.Store(tmp_path / "guard-db")
    first = insert_scheduled(store, account="Owner")
    second = insert_scheduled(store, account="owner", slot=1)
    assert store.claim(first, time.time())
    # A direct database caller can bypass Python normalization, but not the
    # active-account guard. Verify the SQL rule independently of the parser.
    with store.connect() as db:
        db.execute("UPDATE runs SET account='OWNER' WHERE id=?", (first["id"],))
    assert not store.claim(second, time.time())
    with pytest.raises(m.sqlite3.IntegrityError):
        with store.connect() as db:
            db.execute("UPDATE runs SET active=1 WHERE id=?", (second["id"],))
    assert len(store.runs("active=1")) == 1


def test_reconcile_migrated_ref_preserves_exact_original_script(capsys):
    from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelResponse
    payload = {"id": "legacy-nonce", "ref": "owner/queue-monitor-probe", "probe_ref": "Owner/queue-monitor-probe"}
    api = MagicMock()
    endpoint = api.build_kaggle_client.return_value.__enter__.return_value.kernels.kernels_api_client
    endpoint.get_kernel.return_value = ApiGetKernelResponse.from_dict({
        "metadata": {"ref": "Owner/queue-monitor-probe", "currentVersionNumber": 1},
        "blob": {"source": m.probe_script(payload["id"], payload["probe_ref"])}})
    with patch.dict(sys.modules, mocked_kaggle_modules(api)), patch.object(m.sys, "stdin", io.StringIO(json.dumps(payload))), \
            patch("requests.sessions.Session.request", side_effect=AssertionError("Live HTTP forbidden")):
        m.helper("reconcile")
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] and result["data"]["matched"]
    assert result["data"]["ref"] == payload["ref"]


def test_validated_timing_clears_current_over_600_observation(tmp_path):
    store = m.Store(tmp_path / "timing-db")
    now = time.time()
    run = active_run(store, now)
    run.update(state="collecting", over_600_observation=True)
    store.save(run)
    timing = {"run_id": run["id"], "ref": run["ref"], "fetched_version": run["version"],
              "start_utc": m.iso(now - 50), "end_utc": m.iso(now - 49),
              "gpu_names": ["Tesla T4", "Tesla T4"], "gpu_query_ok": True}
    collector = m.Collector(store, [m.Account("a", "fake")], transport=FakeTransport({"ok": True, "data": timing}))
    collector.poll(run, now)
    assert run["state"] == "success" and not run.get("over_600_observation")
    assert run["historical_over_600_observation"] is True
    assert run["submit_to_start_unacceptable"] is False


def test_api_responsive_while_background_transport_blocked(tmp_path):
    from fastapi.testclient import TestClient
    entered = m.threading.Event()
    release = m.threading.Event()

    def blocked_check(self, operation, payload, account=None):
        assert operation == "check"
        entered.set()
        release.wait(5)
        return {"ok": True, "version_requests": True}

    with patch.dict(m.os.environ, {"MONITOR_DB": str(tmp_path / "api-db"), "ACCOUNTS_ENV": "", "AUTO_PROBES": "false"}), \
            patch.object(m.Transport, "invoke", blocked_check):
        with TestClient(m.create_app()) as client:
            try:
                assert entered.wait(2)
                assert client.get("/healthz").status_code == 200
                response = client.get("/api/summary")
                assert response.status_code == 200
                assert response.json()["timezone"] == "Asia/Ho_Chi_Minh"
                assert len(response.json()["hourly"]) == 24
                assert response.json()["auto_probes"] is False
            finally:
                release.set()


class RaisingTransport:
    def __init__(self):
        self.calls = []

    def invoke(self, operation, payload, account=None):
        self.calls.append((operation, payload.copy()))
        raise RuntimeError("transport exploded")


class CountdownTransport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def invoke(self, operation, payload, account=None):
        self.calls.append((operation, payload.copy()))
        return self.payload


def test_state_vocabularies_are_explicit_and_disjoint():
    assert m.SCHEMA_VERSION >= 1
    assert m.APP_STATES == set(m.ACTIVE_STATES) | set(m.TERMINAL_STATES) | set(m.PENDING_STATES)
    assert m.ACTIVE_STATES.isdisjoint(m.TERMINAL_STATES)
    assert m.PENDING_STATES.isdisjoint(m.ACTIVE_STATES)
    assert m.PENDING_STATES.isdisjoint(m.TERMINAL_STATES)
    assert m.PROVIDER_STATES == {"queued", "running", "pending", "cancel_requested",
                                 "complete", "error", "cancelled"}
    with pytest.raises(ValueError):
        m.parse_status("teleported")


def test_readiness_retries_with_bounded_backoff_and_rechecks(tmp_path):
    store = m.Store(tmp_path / "db")
    transport = CountdownTransport({"ok": False, "error": "not_ready"})
    collector = m.Collector(store, [m.Account("a", "fake")], False, transport)
    now = 1000.0
    with patch.object(m.time, "time", return_value=now):
        collector.check_readiness(now)
    assert [op for op, _ in transport.calls] == ["check"]
    assert collector.readiness_next == now + m.READINESS_BACKOFF_INITIAL
    collector.check_readiness(now + 1)
    assert len(transport.calls) == 1  # Attempts are spaced by the backoff.
    backoffs = []
    for _ in range(12):
        now = collector.readiness_next
        with patch.object(m.time, "time", return_value=now):
            collector.check_readiness(now)
        backoffs.append(collector.readiness_backoff)
    assert max(backoffs) == m.READINESS_BACKOFF_MAX and backoffs[-1] == m.READINESS_BACKOFF_MAX
    transport.payload = {"ok": True, "version_requests": True}
    now = collector.readiness_next
    with patch.object(m.time, "time", return_value=now):
        collector.check_readiness(now)
    assert collector.sdk_ready and collector.readiness_next == now + m.READINESS_RECHECK_SECONDS


def test_iteration_guard_keeps_worker_alive_across_exceptions(tmp_path):
    store = m.Store(tmp_path / "db")
    collector = m.Collector(store, [m.Account("a", "fake")], False, RaisingTransport())
    # Readiness is checked inside the guarded loop: a raising transport is a
    # bounded failed attempt, not a dead worker.
    collector._iteration()
    assert collector.sdk_ready is False and collector.readiness["ok"] is False
    with patch.object(collector, "tick", side_effect=RuntimeError("tick failed")):
        collector._iteration()
    assert collector.last_error == "RuntimeError" and collector.progress == "error"
    assert collector.ready is False  # Idle/erroring worker must not report ready.
    # call() converts an unexpected transport exception into a sanitized result.
    assert collector.call({"id": "x", "account": "a"}, "status") == {"ok": False, "error": "RuntimeError"}


def test_worker_stuck_distinguishes_bounded_call_from_hung_loop(tmp_path):
    collector = m.Collector(m.Store(tmp_path / "db"), [m.Account("a", "fake")], False, m.Transport())
    collector.started_at = collector.tick_started = 1000.0
    collector.calls_in_flight, collector.api_started = 1, 1000.0
    assert not collector.worker_stuck(1000.0 + collector.call_timeout)
    assert collector.worker_stuck(1000.0 + collector.call_timeout + m.WATCHDOG_CALL_MARGIN_SECONDS + 1)
    collector.calls_in_flight = 0
    hungry = 1000.0 + collector.call_timeout * 2 + m.WATCHDOG_CALL_MARGIN_SECONDS
    assert not collector.worker_stuck(hungry)
    assert collector.worker_stuck(hungry + 1)


def test_watchdog_fatals_only_dead_or_hung_worker_without_touching_remote(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    run = insert_scheduled(store, due=now)
    store.claim(run, now)
    run.update(state="queued", version=1, submit_ack=now, first_queued=now, next_poll=now + 10_000)
    store.save(run)
    collector = m.Collector(store, [m.Account("a", "fake")], False, m.Transport())
    exited = []
    collector._exit_hook = exited.append

    class Alive:
        def is_alive(self):
            return True

    class Dead:
        def is_alive(self):
            return False

    collector.thread = Alive()
    collector.tick_started = collector.tick_finished = time.time()
    assert collector.watchdog_check(time.time()) is None
    collector.thread = Dead()
    reason = collector.watchdog_check(time.time())
    assert reason == "worker_thread_dead"
    collector.fatal(reason)
    assert exited == [1] and collector.fatal_reason == "worker_thread_dead"
    # The queued remote job is never speculatively cancelled or released.
    assert store.runs("active=1")[0]["state"] == "queued"

    collector.stop.clear()
    collector.thread = Alive()
    collector.calls_in_flight = 0
    collector.tick_started, collector.tick_finished, collector.started_at = 0.0, None, 0.0
    assert collector.watchdog_check(10 ** 9) == "worker_stuck"


def test_runtime_recovers_interrupted_pushing_without_re_push(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    store.set_meta("next_window", int(now // 1800) * 1800 + 1800)
    run = insert_scheduled(store, due=now)
    store.claim(run, now - 30)  # Persisted 'pushing' with no saved outcome.
    assert store.runs("state='pushing'")
    transport = CountdownTransport({"ok": True, "data": {"matched": True, "ref": run["ref"], "version_number": 1}})
    collector = m.Collector(store, [m.Account("a", "fake")], True, transport)
    collector.ready = True
    with patch.object(m.time, "time", return_value=now):
        collector.tick(now)
    saved = store.runs("id='a-0'")[0]
    assert saved["state"] == "ambiguous" and saved["error"] == "unexpected_push_interruption"
    assert [op for op, _ in transport.calls] == []  # Never re-pushed.
    assert store.runs("active=1")


def test_push_transport_exception_becomes_ambiguous_without_resubmission(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    store.set_meta("next_window", int(now // 1800) * 1800 + 1800)
    insert_scheduled(store, due=now)
    transport = RaisingTransport()
    collector = m.Collector(store, [m.Account("a", "fake")], True, transport)
    collector.ready = True
    with patch.object(m.time, "time", return_value=now):
        collector.tick(now)
    saved = store.runs("id='a-0'")[0]
    assert saved["state"] == "ambiguous" and saved["error"] == "RuntimeError"
    assert [op for op, _ in transport.calls] == ["push"]
    with patch.object(m.time, "time", return_value=now + 60):
        collector.tick(now + 60)
    # The next attempt reconciles the unknown push, it never submits again.
    assert [op for op, _ in transport.calls] == ["push", "reconcile"]
    assert store.runs("id='a-0'")[0]["state"] == "ambiguous"


def test_removed_account_active_run_stays_blocked_and_visible(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    # This test isolates recovery of removed accounts. Randomly planning a new
    # configured-account slot can otherwise launch an unrelated probe here.
    store.set_meta("next_window", int(now // 1800) * 1800 + 3600)
    run = insert_scheduled(store, account="ghost", due=now)
    store.claim(run, now - 100)
    run.update(state="submitted", version=1, submit_ack=now - 90, next_poll=now)
    store.save(run)
    transport = CountdownTransport({"ok": True, "data": {"status": "running", "requested_version": 1}})
    collector = m.Collector(store, [m.Account("a", "fake")], True, transport)
    collector.ready = True
    collector.tick(now)
    assert transport.calls == []  # A removed account is never called.
    assert store.runs("account='ghost' AND active=1")
    # Even a direct call yields an error without terminalizing the remote job.
    result = collector.call(store.runs("account='ghost'")[0], "status")
    assert result == {"ok": False, "error": "account_removed"}
    assert store.runs("id='ghost-0'")[0]["state"] == "submitted"
    summary = m.build_summary(collector, None, now)
    ghost = {a["id"]: a for a in summary["accounts"]}["ghost"]
    assert ghost["blocked"] == 1 and ghost["active_runs"] == 1


def test_planner_keeps_next_probe_and_launch_is_throttled(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    store.set_meta("next_window", int(now // 1800) * 1800 + 1800)
    for slot, account in enumerate("abc"):
        insert_scheduled(store, account=account, slot=slot, due=now)
    transport = CountdownTransport({"ok": False, "error": "api_timeout"})
    collector = m.Collector(store, [m.Account(n, "fake") for n in "abc"], True, transport)
    collector.ready = True
    collector.tick(now)
    assert [op for op, _ in transport.calls] == ["push"]  # No burst: one launch.
    with patch.object(m.time, "time", return_value=now + 1):
        collector.tick(now + 1)
    assert [op for op, _ in transport.calls] == ["push"]
    summary = m.build_summary(collector, None, now)
    assert all(a["next_probe_utc"] for a in summary["accounts"])
    assert {r["window"] for r in store.runs("state='scheduled'")} >= {
        int(now // 1800) * 1800 + 1800}


def test_stop_event_prevents_new_launch(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    store.set_meta("next_window", int(now // 1800) * 1800 + 1800)
    insert_scheduled(store, due=now)
    transport = CountdownTransport({"ok": True, "data": {"status": "running", "requested_version": 1}})
    collector = m.Collector(store, [m.Account("a", "fake")], True, transport)
    collector.ready = True
    collector.stop.set()
    collector.tick(now)
    assert transport.calls == []
    assert store.runs("id='a-0'")[0]["state"] == "scheduled"


def test_summary_cache_versioned_and_backwards_compatible(tmp_path):
    store = m.Store(tmp_path / "db")
    collector = m.Collector(store, [m.Account("a", "fake")], False, CountdownTransport({"ok": True}))
    cache = collector.summary_cache
    version = store.data_version
    cache.put(version, "cached", now=1000.0)
    assert cache.get(version, now=1000.0 + cache.ttl - 0.1) == "cached"
    assert cache.get(version, now=1000.0 + cache.ttl) is None
    assert cache.get(version + 1, now=1000.0) is None
    store.bump()
    assert store.data_version == version + 1
    summary = m.build_summary(collector, None, 1000.0)
    assert summary["schema_version"] == m.SCHEMA_VERSION
    assert {"meta", "accounts", "runs", "hours", "hourly", "statistics", "definitions"} <= summary.keys()
    meta = summary["meta"]
    assert meta["schema_version"] == m.SCHEMA_VERSION
    assert "blocked" in summary["accounts"][0] or summary["accounts"] == []


def test_health_separates_freshness_attempt_heartbeat_and_config(tmp_path):
    store = m.Store(tmp_path / "db")
    collector = m.Collector(store, [m.Account("a", "fake")], False, CountdownTransport({"ok": True}))

    class Alive:
        def is_alive(self):
            return True

    collector.thread = Alive()
    assert collector.ready is False and collector.sdk_ready is False  # idle is not ready
    collector._iteration()
    assert collector.ready is True and collector.sdk_ready is True
    summary = m.build_summary(collector, "accounts_env_not_set", 1234.0)
    meta = summary["meta"]
    assert meta["config_ok"] is False and meta["configuration_error"] == "accounts_env_not_set"
    assert meta["sdk_ready"] is True and meta["worker_alive"] is True
    assert {"data_updated_at", "evidence_updated_at", "heartbeat_utc",
            "last_api_attempt_utc", "api_in_flight"} <= meta.keys()


def test_healthz_reports_progress_and_separated_timestamps(tmp_path):
    from fastapi.testclient import TestClient
    accounts = tmp_path / "accounts.env"
    accounts.write_text("PRODUCTION_KAGGLE_ACCOUNT_COUNT=1\n"
                        "PRODUCTION_KAGGLE_ACCOUNT_1_USERNAME=tester\n"
                        "PRODUCTION_KAGGLE_ACCOUNT_1_API_TOKEN=fake\n", encoding="utf-8")

    def ready_check(self, operation, payload, account=None):
        return {"ok": True, "version_requests": True}

    with patch.dict(m.os.environ, {"MONITOR_DB": str(tmp_path / "health-db"),
                                   "ACCOUNTS_ENV": str(accounts), "AUTO_PROBES": "false"}), \
            patch.object(m.Transport, "invoke", ready_check):
        with TestClient(m.create_app()) as client:
            deadline = time.time() + 5
            body = {}
            while time.time() < deadline:
                body = client.get("/healthz").json()
                if body.get("progress") == "ok":
                    break
                time.sleep(0.05)
            assert body["progress"] == "ok"
            assert body["ok"] is True and body["worker_alive"] is True
            assert body["ready"] is True and body["sdk_ready"] is True
            assert body["tick_started_utc"] and body["tick_finished_utc"]
            # Freshness, API attempt and heartbeat stay independent signals.
            assert body["heartbeat_utc"]
            assert body["last_api_attempt_utc"] and body["last_api_result_utc"]
            assert body["api_in_flight"] is False
            assert body["readiness_attempts"] >= 1


def test_shutdown_is_bounded_and_reports_whether_worker_stopped(tmp_path):
    store = m.Store(tmp_path / "db")
    collector = m.Collector(store, [m.Account("a", "fake")], False, m.Transport())

    class Thread:
        def __init__(self, alive):
            self.alive, self.joins = alive, []

        def join(self, timeout=None):
            self.joins.append(timeout)

        def is_alive(self):
            return self.alive

    live = Thread(True)
    collector.thread = live
    assert collector.shutdown(grace=0.01) is False
    assert live.joins == [0.01]
    done = Thread(False)
    collector.thread = done
    assert collector.shutdown(grace=0.01) is True


def test_compose_stop_grace_exceeds_bounded_shutdown(tmp_path):
    compose = (Path(__file__).parents[1] / "compose.yaml").read_text(encoding="utf-8")
    call_timeout = float(re.search(r'CALL_TIMEOUT:\s*"([0-9.]+)"', compose).group(1))
    grace = float(re.search(r"stop_grace_period:\s*([0-9.]+)s", compose).group(1))
    collector = m.Collector(m.Store(tmp_path / "db"), [m.Account("a", "fake")], False,
                            m.Transport(timeout=call_timeout))
    # A tick can make two bounded remote calls; the app joins for that plus the
    # watchdog margin. Docker must outlast it so the lock releases gracefully.
    assert collector.shutdown_grace == 2 * call_timeout + m.WATCHDOG_CALL_MARGIN_SECONDS
    assert grace > collector.shutdown_grace


def test_logic_graph_occupancy_includes_active_beyond_recent_sample(tmp_path):
    store = m.Store(tmp_path / "db")
    base = 1_000_000.0
    active = insert_scheduled(store, account="ghost", due=base)
    store.claim(active, base)
    active.update(state="running", version=1, submit_ack=base, next_poll=base + 10_000)
    store.save(active)
    for slot in range(205):
        run = insert_scheduled(store, account="a", slot=slot, due=base + 1000 + slot)
        run.update(state="success", submit_begin=run["due"], t4x2=True)
        store.save(run)
    by_state, by_active = store.state_occupancy()
    graph = m.logic_graph(by_state, by_active)
    running = {n["id"]: n for n in graph["nodes"]}["running"]
    assert running["active_occupancy"] == 1
    assert graph["occupancy"]["active_total"] == 1
    assert graph["occupancy"]["total"] == 206
    assert any(n["id"] == "provider" for n in graph["nodes"])
    assert any(e["from"] == "ambiguous" and e["to"] == "submitted" for e in graph["edges"])


def test_roadmap_snapshot_reads_local_file_only(tmp_path, monkeypatch):
    roadmap = tmp_path / "roadmap.json"
    roadmap.write_text(json.dumps(roadmap_fixture()), encoding="utf-8")
    with patch("requests.sessions.Session.request", side_effect=AssertionError("Live HTTP forbidden")):
        monkeypatch.setenv("ROADMAP_JSON", str(roadmap))
        snapshot = m.roadmap_snapshot()
    assert snapshot["available"] is True and snapshot["source"] == "local_roadmap_json"
    assert snapshot["snapshot"]["issues"][0]["number"] == 11
    monkeypatch.delenv("ROADMAP_JSON", raising=False)
    with patch.object(m, "__file__", str(tmp_path / "empty" / "app.py")):
        missing = m.roadmap_snapshot()
    assert missing["available"] is False and missing["reason"] == "roadmap_json_not_packaged"


def test_logic_and_roadmap_endpoints_and_schema_version(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    roadmap = tmp_path / "roadmap.json"
    roadmap.write_text(json.dumps(roadmap_fixture()), encoding="utf-8")
    monkeypatch.setenv("ROADMAP_JSON", str(roadmap))

    def ready_check(self, operation, payload, account=None):
        return {"ok": True, "version_requests": True}

    with patch.dict(m.os.environ, {"MONITOR_DB": str(tmp_path / "endpoints-db"),
                                   "ACCOUNTS_ENV": "", "AUTO_PROBES": "false"}), \
            patch.object(m.Transport, "invoke", ready_check):
        with TestClient(m.create_app()) as client:
            logic = client.get("/api/logic").json()
            assert logic["schema_version"] == m.SCHEMA_VERSION
            ids = {n["id"] for n in logic["nodes"]}
            assert {"scheduled", "pushing", "ambiguous", "success", "provider"} <= ids
            assert any(e["from"] == "ambiguous" and e["to"] == "submitted" for e in logic["edges"])
            assert logic["occupancy"]["total"] >= 0
            road = client.get("/api/roadmap").json()
            assert road["available"] is True and road["snapshot"] == roadmap_fixture()
            summary = client.get("/api/summary").json()
            assert summary["schema_version"] == m.SCHEMA_VERSION
            assert {"meta", "accounts", "runs", "hours"} <= summary.keys()
            assert summary["meta"]["schema_version"] == m.SCHEMA_VERSION


def roadmap_fixture():
    return {"repo": "owner/repo", "synced_at": "2026-10-13T00:00:00Z",
            "issues": [{"number": 11, "title": "Supervise collector", "state": "open",
                        "url": "https://github.com/owner/repo/issues/11", "body": "Details",
                        "updatedAt": "2026-10-13T00:00:00Z"}],
            "dependencies": [{"from": 4, "to": 11}], "blocked": [4]}


@pytest.mark.parametrize("payload", [[], {"issues": []}, {**roadmap_fixture(), "issues": [None]},
                                     {**roadmap_fixture(), "dependencies": [{"from": 4}]},
                                     {**roadmap_fixture(), "blocked": [True]}])
def test_roadmap_malformed_schema_is_unavailable(tmp_path, monkeypatch, payload):
    path = tmp_path / "roadmap.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("ROADMAP_JSON", str(path))
    assert m.roadmap_snapshot()["reason"] == "roadmap_json_unreadable"


def test_failed_attempt_never_refreshes_evidence_and_cached_health_is_live(tmp_path):
    store = m.Store(tmp_path / "db")
    run = insert_scheduled(store, due=1000)
    store.claim(run, 1000)
    run.update(state="queued", version=1, submit_ack=1001, last_observed=1010)
    store.save(run)
    collector = m.Collector(store, [m.Account("a", "fake")], False, RaisingTransport())
    with patch.object(m.time, "time", return_value=1100):
        collector.poll(run, 1100)
    payload = m.build_summary(collector, None, 1100)
    assert payload["meta"]["evidence_updated_at"] == m.iso(1010)
    assert payload["meta"]["last_api_attempt_utc"] == m.iso(1100)
    collector.api_in_flight = True
    collector.api_last_started = 1200
    collector.last_error = "RuntimeError"
    refreshed = m.refresh_summary_health(payload, collector, None)
    assert refreshed["meta"]["api_in_flight"] is True
    assert refreshed["meta"]["last_api_attempt_utc"] == m.iso(1200)
    assert refreshed["meta"]["evidence_updated_at"] == m.iso(1010)
    assert refreshed["meta"]["health"] == "error" and not refreshed["ready"]
    assert payload["meta"]["api_in_flight"] is False  # Cache was not mutated.


def test_summary_indexed_bound_is_honest_and_old_active_account_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "SUMMARY_RUN_LIMIT", 3)
    store = m.Store(tmp_path / "db")
    old = insert_scheduled(store, account="ghost", due=1000)
    store.claim(old, 1000)
    old.update(state="queued", version=1, submit_ack=1001)
    store.save(old)
    for slot in range(5):
        run = insert_scheduled(store, slot=slot, due=2000 + slot)
        run.update(state="skipped")
        store.save(run)
    collector = m.Collector(store, [], False, RaisingTransport())
    with patch.object(store, "runs", wraps=store.runs) as query:
        summary = m.build_summary(collector, None, 3000)
    query.assert_any_call("state!='scheduled'", limit=3)
    assert summary["meta"]["statistics_window"]["kind"] == "recent_runs"
    assert summary["meta"]["statistics_window"]["count"] == 3
    assert summary["meta"]["statistics_window"]["oldest_due_utc"] == m.iso(2002)
    ghost = summary["accounts"][0]
    assert ghost["blocked"] == 1 and ghost["latest_run"]["id"] == old["id"]
    with store.connect() as db:
        plan = db.execute("EXPLAIN QUERY PLAN SELECT data FROM runs WHERE state!='scheduled' ORDER BY due DESC,position LIMIT 3").fetchall()
    assert any("runs_summary_due" in r["detail"] for r in plan)
    assert len(store.runs()) == 6


def test_unexpected_exception_after_remote_push_cannot_resubmit(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    run = insert_scheduled(store, due=now)
    store.claim(run, now)
    transport = CountdownTransport({"ok": True, "data": {"ref": run["ref"], "version_number": 1}})
    collector = m.Collector(store, [m.Account("a", "fake")], True, transport)
    original_save = store.save
    failures = []

    def fail_first_save(row, active=None):
        if not failures:
            failures.append(True)
            raise RuntimeError("disk transient")
        return original_save(row, active)

    with patch.object(store, "save", side_effect=fail_first_save):
        collector.push(run, now)
    assert store.runs("active=1")[0]["state"] == "ambiguous"
    collector.ready = True
    with patch.object(m.time, "time", return_value=now + 61):
        collector.tick(now + 61)
    assert [op for op, _ in transport.calls] == ["push", "reconcile"]


def test_ready_is_revoked_on_failed_recheck_and_tick_exception(tmp_path):
    collector = m.Collector(m.Store(tmp_path / "db"), [m.Account("a", "fake")], False,
                            CountdownTransport({"ok": True}))
    collector._iteration()
    assert collector.ready
    with patch.object(collector, "tick", side_effect=RuntimeError()):
        collector._iteration()
    assert not collector.ready
    collector.ready = True
    collector.readiness_next = 0
    collector.transport.payload = {"ok": False}
    collector._iteration()
    assert not collector.ready and not collector.sdk_ready


def test_slow_readiness_failure_backoff_starts_after_completion(tmp_path):
    collector = m.Collector(m.Store(tmp_path / "db"), [], False, RaisingTransport())
    with patch.object(m.time, "time", side_effect=[1000, 1090]):
        collector.check_readiness(1000)
    assert collector.readiness_next == 1090 + m.READINESS_BACKOFF_INITIAL


def test_stuck_worker_is_unhealthy_before_watchdog_exits(tmp_path):
    collector = m.Collector(m.Store(tmp_path / "db"), [m.Account("a", "fake")], False, RaisingTransport())
    collector.thread = SimpleNamespace(is_alive=lambda: True)
    collector.started_at = collector.tick_finished = 1000
    collector.ready = collector.sdk_ready = True
    with patch.object(m.time, "time", return_value=10000):
        health = m.live_health(collector, None)
    assert not health["ok"] and not health["ready"] and health["watchdog"] == "worker_stuck"


def test_completed_calls_are_progress_during_three_call_iteration(tmp_path):
    collector = m.Collector(m.Store(tmp_path / "db"), [], False, m.Transport(timeout=90))
    collector.started_at = collector.tick_finished = 1000
    collector.api_last_end = 1270
    assert not collector.worker_stuck(1271)
    assert collector.worker_stuck(1270 + collector.shutdown_grace + 1)


def test_worker_supervises_dead_watchdog(tmp_path):
    collector = m.Collector(m.Store(tmp_path / "db"), [], False, RaisingTransport())
    collector.watchdog_started = True
    collector.watchdog_thread = SimpleNamespace(is_alive=lambda: False)
    exits = []
    collector._exit_hook = exits.append
    collector.loop()
    assert exits == [1] and collector.fatal_reason == "watchdog_thread_dead"


def test_shutdown_waits_for_inflight_call_and_skips_launch(tmp_path):
    entered, release = m.threading.Event(), m.threading.Event()

    class BlockingTransport:
        timeout = 0.1

        def invoke(self, operation, payload, account=None):
            entered.set()
            assert release.wait(2)
            return {"ok": True}

    collector = m.Collector(m.Store(tmp_path / "db"), [m.Account("a", "fake")], True, BlockingTransport())
    with patch.object(collector, "push", side_effect=AssertionError("shutdown must not push")):
        collector.start()
        try:
            assert entered.wait(2)
            assert not collector.shutdown(grace=0.01)
            assert collector.thread.is_alive()
            release.set()
            assert collector.shutdown(grace=2)
        finally:
            release.set()
            collector.shutdown(grace=2)


def test_startup_failure_releases_instance_lock(tmp_path):
    from fastapi.testclient import TestClient
    with patch.dict(m.os.environ, {"MONITOR_DB": str(tmp_path / "db"), "ACCOUNTS_ENV": ""}), \
            patch.object(m, "Store", side_effect=RuntimeError("startup failure")), \
            patch.object(m.InstanceLock, "release", autospec=True) as release:
        with pytest.raises(RuntimeError, match="startup failure"):
            with TestClient(m.create_app()):
                pass
        release.assert_called_once()
        # Mocking release leaves the file open; close the test-owned handle.
        release.call_args.args[0].file.close()


def test_unstopped_worker_keeps_instance_lock(tmp_path):
    from fastapi.testclient import TestClient
    app = m.create_app()
    with patch.dict(m.os.environ, {"MONITOR_DB": str(tmp_path / "db"), "ACCOUNTS_ENV": ""}), \
            patch.object(m.Collector, "start"), patch.object(m.Collector, "shutdown", return_value=False), \
            patch.object(m.InstanceLock, "release", autospec=True) as release:
        with TestClient(app):
            pass
        release.assert_not_called()
        assert app.state.collector.instance_lock is app.state.instance_lock
        contender = m.InstanceLock(tmp_path / "db")
        with pytest.raises(RuntimeError, match="already running"):
            contender.acquire()
        app.state.instance_lock.file.close()


@pytest.mark.parametrize("rejected", [False, True])
@pytest.mark.parametrize("exception_type", [RuntimeError, ValueError])
def test_push_bookkeeping_failure_preserves_durable_outcome(tmp_path, rejected, exception_type):
    store = m.Store(tmp_path / "db")
    now = time.time()
    store.set_meta("next_window", int(now // 1800) * 1800 + 1800)
    run = insert_scheduled(store, due=now)
    transport = CountdownTransport({"ok": True, "data": {
        "ref": f"a/{m.SLUG}", "version_number": 73, "error": rejected,
    }})
    collector = m.Collector(store, [m.Account("a", "fake")], True, transport)
    collector.sdk_ready = collector.ready = True
    collector.readiness_next = now + 10000
    original_set_meta = store.set_meta
    writes = []

    def fail_post_save_bookkeeping(key, value):
        if key == "last_launch":
            writes.append(value)
            if len(writes) == 2:
                raise exception_type("bookkeeping unavailable")
        return original_set_meta(key, value)

    with patch.object(store, "set_meta", side_effect=fail_post_save_bookkeeping), \
            patch.object(store, "save", wraps=store.save) as save, \
            patch.object(m.time, "time", return_value=now):
        collector._iteration()
        # There is one outcome write, with no phantom downgrade after metadata
        # failure. ValueError must not be swallowed by the parsing guard either.
        assert save.call_count == 1
    assert len(writes) == 2 and collector.last_error == exception_type.__name__
    assert [op for op, _ in transport.calls] == ["push"]
    saved = store.runs("id=?", (run["id"],))[0]
    state = "failed" if rejected else "submitted"
    assert saved["state"] == state
    assert saved["ref"] == f"a/{m.SLUG}"
    assert saved["push_response"]["version_number"] == 73
    assert saved["push_response_received"] is True
    assert saved["submit_ack_uncertain"] is False
    if rejected:
        assert saved["error"] == "provider_push_rejected"
        assert saved["terminal_observed"] == now
        assert not store.runs("active=1")
    else:
        assert saved["version"] == 73 and saved["submit_ack"] == now
        assert store.runs("active=1")[0]["id"] == run["id"]
    graph = m.logic_graph(*store.state_occupancy())
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes[state]["occupancy"] == 1
    assert nodes["ambiguous"]["occupancy"] == 0
    assert graph["counts"]["active_total"] == (0 if rejected else 1)
    assert not any(edge["from"] == state and edge["to"] == "ambiguous" for edge in graph["edges"])

    # Recovery resumes exact-version polling for accepted work; rejected work
    # remains terminal. Neither result is reconciled or re-pushed.
    transport.payload = {"ok": True, "data": {"status": "running", "requested_version": 73}}
    with patch.object(m.time, "time", return_value=now + 16):
        collector._iteration()
    assert collector.last_error is None
    assert [op for op, _ in transport.calls] == (["push"] if rejected else ["push", "status"])
    saved = store.runs("id=?", (run["id"],))[0]
    assert saved["state"] == ("failed" if rejected else "running")


def test_launch_bookkeeping_failure_after_claim_recovers_without_push(tmp_path):
    store = m.Store(tmp_path / "db")
    now = time.time()
    store.set_meta("next_window", int(now // 1800) * 1800 + 1800)
    run = insert_scheduled(store, due=now)
    transport = RaisingTransport()
    collector = m.Collector(store, [m.Account("a", "fake")], True, transport)
    collector.sdk_ready = collector.ready = True
    collector.readiness_next = now + 10000
    with patch.object(store, "set_meta", side_effect=RuntimeError("metadata failed")), \
            patch.object(m.time, "time", return_value=now):
        collector._iteration()
    assert collector.last_error == "RuntimeError" and not collector.ready
    assert store.runs("id=?", (run["id"],))[0]["state"] == "pushing"
    assert transport.calls == []
    with patch.object(m.time, "time", return_value=now + 1):
        collector._iteration()
    saved = store.runs("id=?", (run["id"],))[0]
    assert saved["state"] == "ambiguous" and saved["error"] == "unexpected_push_interruption"
    assert store.runs("active=1")[0]["id"] == run["id"]
    assert collector.last_error is None and transport.calls == []
