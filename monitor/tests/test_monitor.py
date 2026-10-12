import importlib.util
import io
import json
from pathlib import Path
import random
import sys
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

spec = importlib.util.spec_from_file_location("monitor_app", Path(__file__).parents[1] / "app.py")
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


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
    assert len(original) == 2
    store.plan(1901, ["a", "b"])
    assert store.runs() == original
    # Restart catches up missing windows, retaining original randomized instants.
    m.Store(tmp_path / "db").plan(5500, ["a", "b"])
    assert len(store.runs()) == 6


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
    assert store.runs("active=1")
    transport.result["data"]["status"] = "CANCEL_ACKNOWLEDGED"
    collector.poll(run, now + 32)
    assert run["state"] == "failed" and not store.runs("active=1")


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
                insert_scheduled(store, account="b", slot=1, due=due + 1800)
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
                assert accounts["b"]["next_probe_utc"] == m.iso(due + 1800)
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
    assert len(store.runs()) == 2


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
