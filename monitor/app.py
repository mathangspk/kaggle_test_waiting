"""Private-network Kaggle queue collector. Run: uvicorn app:app --workers 1.

Configuration (process environment, never the credentials file):
ACCOUNTS_ENV=/external/accounts.env; AUTO_PROBES=false (default);
MONITOR_DB=monitor.sqlite3; MIN_RANK_DAYS=3; CALL_TIMEOUT=45.
External production dotenv schema: PRODUCTION_KAGGLE_ACCOUNT_COUNT=N,
PRODUCTION_KAGGLE_ACCOUNT_1_USERNAME=..., PRODUCTION_KAGGLE_ACCOUNT_1_API_TOKEN=...
(1-based). Also accepts ACCOUNT_COUNT=N, ACCOUNT_1_USERNAME=...,
ACCOUNT_1_API_TOKEN=... (1-based). KAGGLE_ACCOUNT_COUNT / KAGGLE_ACCOUNTS_COUNT
with KAGGLE_ACCOUNT_1_USERNAME/KAGGLE_ACCOUNT_1_API_TOKEN or
KAGGLE_USERNAME_1/KAGGLE_API_TOKEN_1 are also accepted. Legacy ACCOUNT_1_KEY
is supported instead of API_TOKEN. Credentials are loaded only at lifespan.
No authentication: bind only on your private Tailscale network.

Push ambiguity blocks new submissions while read-only reconciliation checks the
latest dedicated notebook's complete source every 60s. Observation timeout is NOT remote cancellation and
continues low-frequency exact-version polling. No stop endpoint is provided.
"""
from __future__ import annotations

import contextlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
LOCAL = ZoneInfo("Asia/Ho_Chi_Minh")
SLUG = "queue-monitor-probe"
ACTIVE = ("pushing", "ambiguous", "submitted", "queued", "running", "timed_out", "collecting")
TERMINAL = {"complete", "error", "cancelled"}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def timestamp(value: str) -> float:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("UTC timestamp required")
    if dt.utcoffset().total_seconds() != 0:
        raise ValueError("UTC timestamp required")
    return dt.timestamp()


@dataclass(repr=False)
class Account:
    username: str
    token: str = field(default="", repr=False)
    key: str = field(default="", repr=False)


def parse_accounts(values: dict) -> list[Account]:
    """Pure parser: errors contain neither secret values nor dotenv content."""
    def first(*keys):
        return next((values[k] for k in keys if values.get(k)), "")
    try:
        count = int(first("PRODUCTION_KAGGLE_ACCOUNT_COUNT", "ACCOUNT_COUNT", "ACCOUNTS_COUNT", "KAGGLE_ACCOUNT_COUNT", "KAGGLE_ACCOUNTS_COUNT"))
    except (ValueError, TypeError):
        raise ValueError("Invalid account count") from None
    if not 1 <= count <= 100:
        raise ValueError("Account count must be 1..100")
    result = []
    for i in range(1, count + 1):
        username = first(f"PRODUCTION_KAGGLE_ACCOUNT_{i}_USERNAME", f"ACCOUNT_{i}_USERNAME", f"KAGGLE_ACCOUNT_{i}_USERNAME", f"KAGGLE_USERNAME_{i}", f"ACCOUNT_{i}_KAGGLE_USERNAME")
        token = first(f"PRODUCTION_KAGGLE_ACCOUNT_{i}_API_TOKEN", f"ACCOUNT_{i}_API_TOKEN", f"KAGGLE_ACCOUNT_{i}_API_TOKEN", f"KAGGLE_API_TOKEN_{i}", f"ACCOUNT_{i}_KAGGLE_API_TOKEN")
        key = first(f"ACCOUNT_{i}_KEY", f"KAGGLE_ACCOUNT_{i}_KEY", f"KAGGLE_KEY_{i}")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", username or "") or not (token or key):
            raise ValueError(f"Invalid credentials configuration for account {i}")
        result.append(Account(username.lower(), token, key))
    if len({a.username.lower() for a in result}) != count:
        raise ValueError("Duplicate account usernames")
    return result


def stratified_plan(window: int, usernames: list[str], rng=None) -> list[dict]:
    rng = rng or random.SystemRandom()
    order = list(usernames)
    if not order:
        return []
    rng.shuffle(order)
    width = 1800 / len(order)
    plan = []
    for slot, name in enumerate(order):
        # One account per stratum, one probe per account per UTC window.
        due = window + slot * width + rng.random() * width
        plan.append({"window": window, "slot": slot, "account": name,
                     "due": due, "position": slot})
    return plan


def parse_push(data: dict, expected: str) -> int:
    """Never infer a version from a slug, kernel ID, or a latest-status call."""
    version = data.get("version_number", data.get("versionNumber"))
    if data.get("error") or data.get("ref") != expected:
        raise ValueError("Push identity unconfirmed")
    if isinstance(version, bool) or not str(version).isdigit() or int(version) < 1:
        raise ValueError("Push version unconfirmed")
    return int(version)


def push_diagnostic(data: dict) -> dict:
    """Persist only validated identifiers and an error flag, never error text."""
    ref = data.get("ref")
    ref = ref if isinstance(ref, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}/[A-Za-z0-9_-]{1,80}", ref) else None
    version = data.get("version_number", data.get("versionNumber"))
    version = version if isinstance(version, int) and not isinstance(version, bool) and version > 0 else None
    return {"ref": ref, "version_number": version, "error": data.get("error") is True}


def canonical_run(run: dict, account=None) -> dict:
    """Canonical account identity, preserving the already-pushed script identity."""
    account = (account if account is not None else run["account"]).lower()
    run["account"] = account
    ref = run.get("ref")
    if isinstance(ref, str) and "/" in ref:
        owner, slug = ref.split("/", 1)
        if owner.lower() != account:
            raise RuntimeError("Account migration conflict: run ref owner differs from account")
        canonical = f"{account}/{slug}"
        if ref != canonical:
            # The remote script embedded the old ref literally; do not rewrite
            # that nonce-bearing source expectation or its timing.json identity.
            run.setdefault("probe_ref", ref)
            run["ref"] = canonical
    return run


def matching_probe_source(source, run_id: str, ref: str) -> bool:
    # Preserve all content/whitespace except equivalent text-file line endings.
    # A nonce substring alone cannot establish that this is our exact probe.
    if not isinstance(source, str):
        return False
    normalize = lambda text: text.replace("\r\n", "\n").replace("\r", "\n")
    return normalize(source) == normalize(probe_script(run_id, ref))


def parse_status(value) -> str:
    text = str(value).strip().lower().split(".")[-1]
    text = {"completed": "complete", "failed": "error", "canceled": "cancelled",
            "cancel_acknowledged": "cancelled", "new_script": "pending"}.get(text, text)
    if text not in {"queued", "running", "pending", "cancel_requested", *TERMINAL}:
        raise ValueError("Unknown provider status")
    return text


def probe_script(run_id: str, ref: str) -> str:
    # Write start before the GPU query; rewrite with GPU evidence and end time.
    return f'''import json, subprocess
from datetime import datetime, timezone
from pathlib import Path
p = Path('/kaggle/working/timing.json')
d = {{'run_id': {run_id!r}, 'ref': {ref!r}, 'start_utc': datetime.now(timezone.utc).isoformat(), 'gpu_names': []}}
p.write_text(json.dumps(d))
try:
    r = subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'], capture_output=True, text=True, timeout=20)
    d['gpu_names'] = [x.strip() for x in r.stdout.splitlines() if x.strip()] if r.returncode == 0 else []
    d['gpu_query_ok'] = r.returncode == 0
except Exception:
    d['gpu_query_ok'] = False
d['end_utc'] = datetime.now(timezone.utc).isoformat()
p.write_text(json.dumps(d))
'''


def validate_timing(data: dict, run: dict, now: float) -> dict:
    if data.get("run_id") != run["id"] or data.get("ref") != run.get("probe_ref", run["ref"]):
        raise ValueError("Stale or foreign probe output")
    if data.get("fetched_version") != run["version"]:
        raise ValueError("Output version unconfirmed")
    start, end = timestamp(data["start_utc"]), timestamp(data["end_utc"])
    if not run["submit_begin"] - 5 <= start <= end <= now + 5:
        raise ValueError("Probe clock or timestamp invalid")
    names = data.get("gpu_names")
    if not isinstance(names, list) or not all(isinstance(x, str) and len(x) < 200 for x in names):
        raise ValueError("Invalid GPU evidence")
    t4x2 = (data.get("gpu_query_ok") is True and len(names) == 2
            and all(re.search(r"\bT4\b", x, re.IGNORECASE) for x in names))
    latency = max(0, start - run["submit_begin"])
    return {"start": start, "end": end, "gpu_names": names, "t4x2": t4x2,
            "start_latency_seconds": latency,
            "start_latency_lower": max(0, start - run["submit_ack"]),
            "start_latency_upper": latency, "submit_to_start_unacceptable": latency > 600}


def frontend_status(state: str) -> str:
    return {"success": "completed", "invalid_output": "failed", "wrong_gpu": "failed"}.get(state, state)


def queue_estimate(run: dict):
    """Conservative upper-bound estimate, only when queued was observed.

    This is not a measured point duration. Still-queued and missed-queued runs
    have no finite upper estimate; expose their known lower bound separately.
    """
    if run.get("first_queued") is None or run.get("queue_lower_seconds") is None:
        return None
    return run.get("queue_upper_seconds")


def frontend_run(run: dict) -> dict:
    result = dict(run)
    result.update(status=frontend_status(run["state"]), account_id=run["account"],
                  account_name=run["account"], name=run["account"],
                  queue_estimate_seconds=queue_estimate(run),
                  queue_lower_seconds=run.get("queue_lower_seconds"),
                  queue_upper_seconds=run.get("queue_upper_seconds"),
                  submit_to_start_seconds=run.get("start_latency_seconds"),
                  submit_to_start_lower_seconds=run.get("start_latency_lower"),
                  submit_to_start_upper_seconds=run.get("start_latency_upper"))
    for key, source in (("scheduled_at", "due"), ("submitted_at", "submit_begin"),
                        ("submission_ack_at", "submit_ack"), ("started_at", "start"),
                        ("completed_at", "end"), ("terminal_observed_at", "terminal_observed"),
                        ("reconciled_at_utc", "reconciled_at"), ("push_end_at", "push_end")):
        value = run.get(source)
        result[key] = iso(value) if value is not None else None
    return result


def percentile(values: list[float], fraction: float):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * fraction
    low, high = math.floor(index), math.ceil(index)
    return values[low] + (values[high] - values[low]) * (index - low)


def hourly_stats(runs: list[dict], min_days: int = 3) -> list[dict]:
    buckets = {h: [] for h in range(24)}
    for run in runs:
        local = datetime.fromtimestamp(run["due"], LOCAL)
        buckets[local.hour].append((run, local.date().isoformat()))
    result = []
    for hour, entries in buckets.items():
        submitted = [r for r, _ in entries if r.get("submit_begin") is not None]
        successful = [r for r in submitted if r["state"] == "success" and r.get("t4x2")]
        # Only definite <=5m bounds contribute to the numerator; clock uncertainty
        # crossing 5m is reported separately rather than optimistically counted.
        fast = [r for r in successful if r.get("start_latency_upper", float("inf")) <= 300]
        latencies = [r["start_latency_seconds"] for r in successful]
        queue_estimates = [value for r in submitted if (value := queue_estimate(r)) is not None]
        days = len({day for r, day in entries if r.get("submit_begin") is not None})
        total = len(submitted)
        result.append({"hour": hour, "day_count": days, "sample_count": total, "scheduled": len(entries),
                       "submitted": total, "successful_t4x2": len(successful),
                       "within_5m_t4x2": len(fast), "denominator": total,
                       "success_5m_rate": len(fast) / total if total else None,
                       "queue_sample_count": len(queue_estimates),
                       "median_queue_seconds": percentile(queue_estimates, .5),
                       "p90_seconds": percentile(queue_estimates, .9),
                       "p50_seconds": percentile(queue_estimates, .5),
                       "metric": "observed_queue_upper_bound_seconds",
                       "submit_to_start_p50_seconds": percentile(latencies, .5),
                       "submit_to_start_p90_seconds": percentile(latencies, .9),
                       "failures": sum(r["state"] in {"failed", "invalid_output", "wrong_gpu"} for r, _ in entries),
                       "incomplete": sum(r["state"] in ACTIVE for r, _ in entries),
                       "skipped": sum(r["state"] == "skipped" for r, _ in entries),
                        "unknown_queue": sum(r.get("queue_upper_seconds") is None for r in submitted),
                       "unacceptable": sum(bool(r.get("unacceptable")) for r in submitted),
                       "rank_eligible": days >= min_days and total > 0, "rank": None})
    eligible = sorted((r for r in result if r["rank_eligible"]),
                      key=lambda r: (-r["success_5m_rate"], r["p90_seconds"] if r["p90_seconds"] is not None else float("inf")))
    for rank, row in enumerate(eligible, 1):
        row["rank"] = rank
    return result


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript('''
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, account TEXT NOT NULL, window INTEGER NOT NULL,
                    slot INTEGER NOT NULL, due REAL NOT NULL, position INTEGER NOT NULL,
                    state TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS runs_due ON runs(state,due);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS calls (
                    id INTEGER PRIMARY KEY,run_id TEXT,operation TEXT,at REAL,
                    latency_ms REAL,ok INTEGER,http_status INTEGER,error TEXT);
            ''')
            self._migrate_accounts(db)

    @staticmethod
    def _migrate_accounts(db):
        # Serialize schema migration and validate conflicts BEFORE any changes.
        # A rejection rolls back all record/index changes, retaining every job.
        db.execute("BEGIN IMMEDIATE")
        conflict = db.execute("SELECT lower(account) FROM runs WHERE active=1 GROUP BY lower(account) HAVING count(*)>1").fetchone()
        if conflict:
            raise RuntimeError("Account migration conflict: multiple active runs for the same normalized username")
        rows = db.execute("SELECT * FROM runs").fetchall()
        updates = []
        for row in rows:
            run = canonical_run(json.loads(row["data"]), row["account"])
            updates.append((run["account"], json.dumps(run), row["id"]))
        # The legacy UNIQUE(account,window,slot) would prevent retaining two
        # completed historical case variants in the same slot after lowercase
        # normalization. Rebuild without that constraint, preserving all rows,
        # IDs, slot numbers and evidence. Planner deduplication is atomic below.
        legacy_unique = any(r["origin"] == "u" for r in db.execute("PRAGMA index_list(runs)"))
        if legacy_unique:
            db.execute("ALTER TABLE runs RENAME TO runs_legacy_account_case")
            db.execute('''CREATE TABLE runs (
                id TEXT PRIMARY KEY, account TEXT NOT NULL, window INTEGER NOT NULL,
                slot INTEGER NOT NULL, due REAL NOT NULL, position INTEGER NOT NULL,
                state TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL)''')
            db.execute("INSERT INTO runs SELECT * FROM runs_legacy_account_case")
            db.execute("DROP TABLE runs_legacy_account_case")
        db.executemany("UPDATE runs SET account=?,data=? WHERE id=?", updates)
        db.execute("DROP INDEX IF EXISTS one_active_account")
        db.execute("CREATE UNIQUE INDEX one_active_account ON runs(account COLLATE NOCASE) WHERE active=1")
        db.execute("CREATE INDEX IF NOT EXISTS runs_due ON runs(state,due)")
        db.execute("CREATE INDEX IF NOT EXISTS runs_schedule_key ON runs(account COLLATE NOCASE,window,slot)")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return _connection(db)

    def get_meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    def plan(self, now, usernames):
        current = int(now // 1800) * 1800
        cursor = self.get_meta("next_window", current)
        # Bounded work per tick, but persist every missed window on restart.
        for window in range(cursor, min(current + 1800, cursor + 96 * 1800), 1800):
            with self.connect() as db:
                for item in stratified_plan(window, usernames):
                    item.update(id=uuid.uuid4().hex, state="scheduled")
                    db.execute('''INSERT INTO runs
                        SELECT ?,?,?,?,?,?,?,0,?
                        WHERE NOT EXISTS (SELECT 1 FROM runs WHERE account=? COLLATE NOCASE AND window=? AND slot=?)''',
                               (item["id"], item["account"], window, item["slot"], item["due"], item["position"], "scheduled", json.dumps(item),
                                item["account"], window, item["slot"]))
                db.execute("INSERT OR REPLACE INTO meta VALUES ('next_window',?)", (json.dumps(window + 1800),))

    def runs(self, where="1", args=(), limit=None):
        with self.connect() as db:
            rows = db.execute(f"SELECT data FROM runs WHERE {where} ORDER BY due DESC,position" +
                              (" LIMIT ?" if limit else ""), (*args, limit) if limit else args).fetchall()
            return [json.loads(r[0]) for r in rows]

    def save(self, run, active=None):
        canonical_run(run)
        if active is None:
            active = run["state"] in ACTIVE
        with self.connect() as db:
            db.execute("UPDATE runs SET account=?,state=?,active=?,data=? WHERE id=?",
                       (run["account"], run["state"], int(active), json.dumps(run), run["id"]))

    def claim(self, run, now):
        canonical_run(run)
        run.pop("probe_ref", None)  # This scheduled row has not been pushed yet.
        run.update(state="pushing", ref=f"{run['account']}/{SLUG}", submit_begin=now,
                   next_poll=now, errors=0, version=None)
        try:
            with self.connect() as db:
                return db.execute("UPDATE runs SET account=?,state='pushing',active=1,data=? WHERE id=? AND state='scheduled'",
                                  (run["account"], json.dumps(run), run["id"])).rowcount == 1
        except sqlite3.IntegrityError:
            return False

    def call(self, run_id, operation, started, result):
        with self.connect() as db:
            db.execute("INSERT INTO calls(run_id,operation,at,latency_ms,ok,http_status,error) VALUES (?,?,?,?,?,?,?)",
                       (run_id, operation, started, (time.time() - started) * 1000,
                        int(result.get("ok", False)), result.get("http_status"), result.get("error")))


@contextlib.contextmanager
def _connection(db):
    try:
        with db:
            yield db
    finally:
        db.close()


def isolated_env(account: Account | None, home: str) -> dict:
    # Allowlist: never inherit another account's KAGGLE_* or ambient dotenv path.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL",
               "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env.update(HOME=home, USERPROFILE=home, KAGGLE_CONFIG_DIR=home,
               XDG_CONFIG_HOME=home, TEMP=home, TMP=home, TMPDIR=home,
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    if account:
        env["KAGGLE_USERNAME"] = account.username
        if account.token:
            env["KAGGLE_API_TOKEN"] = account.token
        else:
            env["KAGGLE_KEY"] = account.key
    return env


class Transport:
    def __init__(self, timeout=45):
        self.timeout = timeout

    def invoke(self, operation, payload, account=None):
        with tempfile.TemporaryDirectory(prefix="queue-monitor-") as home:
            try:
                proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--helper", operation],
                                      input=json.dumps(payload), capture_output=True, text=True,
                                      encoding="utf-8", env=isolated_env(account, home), cwd=home,
                                      timeout=self.timeout)
                # Helper emits only a sanitized JSON envelope; never expose stderr.
                result = json.loads(proc.stdout)
                if proc.returncode != 0 or not isinstance(result, dict) or "ok" not in result:
                    return {"ok": False, "error": "helper_protocol"}
                return result
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": "api_timeout"}
            except (OSError, ValueError):
                return {"ok": False, "error": "helper_protocol"}


def helper(operation: str):
    """Official Kaggle CLI SDK, in a fresh account-scoped subprocess."""
    result = {"ok": False, "error": "helper_failure"}
    # Import/auth warnings and exception messages can contain secrets/signed URLs.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            payload = json.load(sys.stdin)
            from kagglesdk.kernels.types.kernels_api_service import (
                ApiGetKernelRequest, ApiGetKernelSessionStatusRequest, ApiListKernelSessionOutputRequest)
            for cls in (ApiGetKernelSessionStatusRequest, ApiListKernelSessionOutputRequest):
                if not hasattr(cls, "version_label"):
                    raise RuntimeError("Version-qualified requests unavailable")
            if operation == "check":
                # Inspect installed source without importing kaggle (auto-auth).
                import ast
                dist = importlib.metadata.distribution("kaggle")
                source = Path(dist.locate_file("kaggle/api/kaggle_api_extended.py")).read_text(encoding="utf-8")
                tree = ast.parse(source)
                methods = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
                if "acc" not in [a.arg for a in methods["kernels_push"].args.args]:
                    raise RuntimeError("Accelerator option unavailable")
                from kagglesdk.kernels.services.kernels_api_service import KernelsApiClient
                for name in ("save_kernel", "get_kernel", "get_kernel_session_status", "list_kernel_session_output"):
                    if not callable(getattr(KernelsApiClient, name, None)):
                        raise RuntimeError("Required SDK method unavailable")
                result = {"ok": True, "sdk_version": dist.version, "version_requests": True}
            else:
                from kaggle.api.kaggle_api_extended import KaggleApi
                api = KaggleApi()
                api.authenticate()
                if operation == "push":
                    with tempfile.TemporaryDirectory() as folder:
                        base = Path(folder)
                        (base / "probe.py").write_text(probe_script(payload["id"], payload["ref"]), encoding="utf-8")
                        metadata = {"id": payload["ref"], "title": "Queue Monitor Probe", "code_file": "probe.py",
                                    "language": "python", "kernel_type": "script", "is_private": True,
                                    "enable_gpu": True, "enable_internet": False, "machine_shape": "NvidiaTeslaT4",
                                    "dataset_sources": [], "competition_sources": [], "kernel_sources": []}
                        (base / "kernel-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
                        response = api.kernels_push(folder, acc="NvidiaTeslaT4")
                        data = {"ref": response.ref, "version_number": response.version_number,
                                "error": bool(response.error)}
                        result = {"ok": True, "data": data}
                elif operation == "reconcile":
                    owner, slug = payload["ref"].split("/")
                    if slug != SLUG:
                        raise ValueError("Invalid identity")
                    request = ApiGetKernelRequest()
                    request.user_name, request.kernel_slug = owner, slug
                    # Latest read is allowed only for recovery; exact source
                    # identity is mandatory before accepting its version number.
                    with api.build_kaggle_client() as client:
                        response = client.kernels.kernels_api_client.get_kernel(request)
                    metadata, blob = response.metadata, response.blob
                    diagnostic = push_diagnostic({"ref": metadata.ref,
                                                  "version_number": metadata.current_version_number})
                    if diagnostic["ref"]:
                        metadata_owner, metadata_slug = diagnostic["ref"].split("/")
                        diagnostic["ref"] = f"{metadata_owner.lower()}/{metadata_slug}"
                    matched = (diagnostic["ref"] == payload["ref"]
                               and diagnostic["version_number"] is not None
                               and matching_probe_source(blob.source, payload["id"], payload.get("probe_ref", payload["ref"])))
                    result = {"ok": True, "data": {**diagnostic, "matched": matched}}
                else:
                    owner, slug = payload["ref"].split("/")
                    version = payload["version"]
                    if isinstance(version, bool) or not isinstance(version, int) or version < 1 or slug != SLUG:
                        raise ValueError("Invalid identity")
                    cls = ApiGetKernelSessionStatusRequest if operation == "status" else ApiListKernelSessionOutputRequest
                    request = cls()
                    request.user_name, request.kernel_slug = owner, slug
                    # Official _kernel_version_label and test_kernel_versions.py
                    # require v<N>. 2.2.4 kernels_pull's slug/<N> is a different
                    # route; its status/output wrappers ignore versions entirely.
                    # https://github.com/Kaggle/kaggle-cli/blob/51b64b0fba360f97298178893010fe02c8da0519/tests/unit/test_kernel_versions.py
                    request.version_label = f"v{version}"
                    with api.build_kaggle_client() as client:
                        endpoint = client.kernels.kernels_api_client
                        if operation == "status":
                            response = endpoint.get_kernel_session_status(request)
                            status = getattr(response.status, "name", str(response.status))
                            result = {"ok": True, "data": {"status": status, "failure": bool(response.failure_message),
                                                            "requested_version": version}}
                        elif operation == "output":
                            # Do not use kernels_output: it also retrieves logs. Fetch
                            # ONLY timing.json from the exact version's file listing.
                            request.page_size = 100
                            response = endpoint.list_kernel_session_output(request)
                            files = [f for f in response.files or [] if f and f.file_name == "timing.json"]
                            if len(files) != 1:
                                raise ValueError("Timing artifact unavailable")
                            import requests
                            with requests.get(files[0].url, stream=True, timeout=(10, 20)) as download:
                                download.raise_for_status()
                                body = bytearray()
                                for chunk in download.iter_content(4096):
                                    body.extend(chunk)
                                    if len(body) > 16384:
                                        raise ValueError("Artifact too large")
                            data = json.loads(body)
                            data["fetched_version"] = version
                            result = {"ok": True, "data": data}
                        else:
                            raise ValueError("Unknown operation")
        except Exception as exc:
            response = getattr(exc, "response", None)
            code = getattr(response, "status_code", None)
            result = {"ok": False, "error": type(exc).__name__,
                      "http_status": code if isinstance(code, int) else None}
    print(json.dumps(result))


class InstanceLock:
    """OS-held lock survives long calls, releases on process exit; no stale leases."""
    def __init__(self, path):
        self.path, self.file = str(path) + ".lock", None

    def acquire(self):
        self.file = open(self.path, "a+b")
        self.file.seek(0)
        if os.name == "nt":
            import msvcrt
            if os.path.getsize(self.path) == 0:
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            try:
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.file.close()
                raise RuntimeError("Collector already running for this database") from None
        else:
            import fcntl
            try:
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.file.close()
                raise RuntimeError("Collector already running for this database") from None

    def release(self):
        if self.file:
            self.file.close()


class Collector:
    def __init__(self, store, accounts, auto=False, transport=None):
        self.store = store
        self.accounts = {a.username.lower(): Account(a.username.lower(), a.token, a.key) for a in accounts}
        if len(self.accounts) != len(accounts):
            raise ValueError("Duplicate account usernames")
        self.auto, self.transport = auto, transport or Transport()
        self.stop = threading.Event()
        self.thread = None
        self.ready = False
        self.readiness = {"ok": False, "error": "not_checked"}
        self.heartbeat = None
        self.last_error = None

    def call(self, run, operation):
        started = time.time()
        result = self.transport.invoke(operation, run, self.accounts[run["account"]])
        self.store.call(run["id"], operation, started, result)
        run["last_api_begin"] = started
        run["last_api_end"] = time.time()
        run["last_api_latency_ms"] = (time.time() - started) * 1000
        run["last_api_http_status"] = result.get("http_status")
        return result

    def start(self):
        # Persisted pushing means the remote save may already have happened.
        for run in self.store.runs("state='pushing'"):
            run.update(state="ambiguous", error="restart_during_push", next_poll=time.time() + 60)
            self.store.save(run)
        self.thread = threading.Thread(target=self.loop, name="queue-collector", daemon=True)
        self.thread.start()

    def loop(self):
        self.readiness = self.transport.invoke("check", {})
        self.ready = self.readiness.get("ok", False) and bool(self.accounts)
        while not self.stop.is_set():
            try:
                self.heartbeat = time.time()
                self.tick(self.heartbeat)
                self.last_error = None
            except Exception as exc:
                self.last_error = type(exc).__name__
            self.stop.wait(1)

    def tick(self, now):
        self.store.plan(now, list(self.accounts))
        # One remote call per tick: fair earliest-due polling across accounts.
        polling = sorted((r for r in self.store.runs("active=1")
                          if r["state"] != "pushing"
                          and r["account"] in self.accounts and r.get("next_poll", 0) <= now),
                         key=lambda r: r.get("next_poll", 0))
        if polling:
            self.poll(polling[0], now)
        last_launch = self.store.get_meta("last_launch", 0)
        due = sorted(self.store.runs("state='scheduled' AND due<=?", (now,)),
                     key=lambda r: (r["due"], r["position"]))
        launched = False
        busy = {r["account"] for r in self.store.runs("active=1")}
        for run in due:
            # Polling can take up to CALL_TIMEOUT; don't launch an expired slot
            # based on the timestamp taken before that blocking subprocess.
            decision_time = max(now, time.time())
            reason = None
            if decision_time - run["due"] > 90:
                reason = "missed_slot"
            elif run["account"] not in self.accounts:
                reason = "account_removed"
            elif not self.auto:
                reason = "probes_disabled"
            elif not self.ready:
                reason = "not_ready"
            elif run["account"] in busy:
                reason = "account_active"
            if reason:
                run.update(state="skipped", skip_reason=reason)
                self.store.save(run)
            elif not launched and decision_time - last_launch >= 15 and not self.stop.is_set():
                if self.store.claim(run, decision_time):
                    self.store.set_meta("last_launch", decision_time)
                    result = self.call(run, "push")
                    run["push_end"] = run["last_api_end"]
                    run["push_response_received"] = result.get("ok") is True
                    run["submit_ack_uncertain"] = not run["push_response_received"]
                    data = result.get("data")
                    if isinstance(data, dict):
                        run["push_response"] = push_diagnostic(data)
                    try:
                        if not result["ok"]:
                            raise ValueError("Ambiguous push")
                        if isinstance(data, dict) and data.get("error") is True:
                            # Explicit save rejection, not an HTTP timeout or
                            # missing identity, proves this attempt was rejected.
                            run.update(state="failed", error="provider_push_rejected", terminal_observed=run["push_end"])
                            self.store.save(run)
                            self.store.set_meta("last_launch", run["push_end"])
                            launched = True
                            continue
                        version = parse_push(result["data"], run["ref"])
                        run.update(state="submitted", version=version, submit_ack=run["push_end"], next_poll=time.time() + 15)
                    except (ValueError, KeyError, TypeError):
                        run.update(state="ambiguous", error=result.get("error", "push_identity_unconfirmed"), next_poll=time.time() + 60)
                    self.store.save(run)
                    self.store.set_meta("last_launch", max(decision_time, time.time()))
                    launched = True
                    busy.add(run["account"])

    def reconcile(self, run):
        # Freeze the original push completion observation BEFORE this read call
        # replaces last_api_end. Old persisted ambiguous records have that field.
        if "push_end" not in run:
            run["push_end"] = run.get("submit_ack", run.get("last_api_end"))
        run.setdefault("submit_ack_uncertain", not run.get("push_response_received", False))
        result = self.call(run, "reconcile")
        try:
            data = result.get("data")
            if not result.get("ok") or not isinstance(data, dict) or data.get("matched") is not True:
                raise ValueError("Unconfirmed probe identity")
            version = parse_push(data, run["ref"])
            ack = run["push_end"]
            if ack is None:
                # Crash before push end was persisted: this read proves a save
                # occurred by now, not when the original push was acknowledged.
                ack = run["last_api_end"]
                run["submit_ack_uncertain"] = True
                run["submit_ack_source"] = "reconciliation_observation_upper_bound"
            else:
                run["submit_ack_source"] = "original_push_end"
            run.update(state="submitted", version=version, submit_ack=ack,
                       metadata_recovered=True, reconciled_at=run["last_api_end"],
                       errors=0, next_poll=run["last_api_end"] + 15)
            run.pop("error", None)
        except (ValueError, KeyError, TypeError):
            run.update(state="ambiguous", error=result.get("error") or "reconcile_identity_unconfirmed",
                       next_poll=run["last_api_end"] + 60)
        self.store.save(run)

    def poll(self, run, now):
        if run["state"] == "ambiguous":
            self.reconcile(run)
            return
        elapsed = now - run["submit_begin"]
        if not run.get("start") and elapsed > 600:
            # This is an observation alert, not a claim that queue time is known.
            run["over_600_observation"] = True
        if elapsed > 3600:
            run["observation_timed_out"] = True
        operation = "output" if run["state"] == "collecting" else "status"
        result = self.call(run, operation)
        try:
            if not result["ok"]:
                raise ValueError(result.get("error", "api_failure"))
            if operation == "output":
                evidence = validate_timing(result["data"], run, time.time())
                run.update(evidence, state="success" if evidence["t4x2"] else "wrong_gpu")
                if run.pop("over_600_observation", False):
                    run["historical_over_600_observation"] = True
                run.pop("error", None)
                self.store.save(run)
                return
            if result["data"].get("requested_version") != run["version"]:
                raise ValueError("status_version_unconfirmed")
            status = parse_status(result["data"]["status"])
            observed = time.time()
            run["last_status"], run["last_observed"] = status, observed
            run["errors"] = 0
            run.pop("error", None)
            if status == "queued":
                run.setdefault("first_queued", observed)
                run["last_queued"] = observed
                run["last_queued_request_begin"] = run["last_api_begin"]
                run["queue_lower_seconds"] = max(0, run["last_api_begin"] - run["first_queued"])
                if run["queue_lower_seconds"] > 600:
                    run["unacceptable"] = True
                run["state"] = "queued"
            elif status == "running":
                run.setdefault("first_running", observed)
                run["state"] = "running"
            elif status in {"pending", "cancel_requested"}:
                # Cancellation requested is not proof that the remote run ended.
                run["state"] = "submitted"
            elif status == "complete":
                run.update(state="collecting", terminal_observed=observed)
            else:
                run.update(state="failed", terminal_observed=observed, error="provider_" + status)
            if status in {"running", *TERMINAL} and run.get("first_queued") is not None:
                transition = run.get("first_running", observed)
                run["queue_upper_seconds"] = max(0, transition - run["submit_begin"])
                run["queue_lower_seconds"] = max(0, run.get("last_queued_request_begin", run["first_queued"]) - run["first_queued"])
            # A skipped queued state leaves both queue bounds null/unknown.
            run["next_poll"] = observed + (120 if run.get("observation_timed_out") else 15)
        except (ValueError, KeyError, TypeError):
            run["errors"] = run.get("errors", 0) + 1
            run["error"] = result.get("error") or ("invalid_output" if operation == "output" else "status_protocol")
            run["next_poll"] = time.time() + min(300, 15 * 2 ** min(run["errors"], 5))
            if operation == "output" and run["errors"] >= 5:
                # Exact status confirmed terminal; invalid evidence cannot be success.
                run["state"] = "invalid_output"
        self.store.save(run)


def create_app():
    from fastapi import FastAPI
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from dotenv import dotenv_values

    @contextlib.asynccontextmanager
    async def lifespan(app):
        path = Path(os.getenv("MONITOR_DB", str(Path(__file__).with_name("monitor.sqlite3")))).resolve()
        lock = InstanceLock(path)
        lock.acquire()
        collector = None
        try:
            config_error = None
            accounts = []
            external = os.getenv("ACCOUNTS_ENV")
            if external:
                try:
                    if not Path(external).is_file():
                        raise ValueError("Account file unavailable")
                    accounts = parse_accounts(dotenv_values(external, interpolate=False))
                except Exception:
                    config_error = "invalid_accounts_configuration"
            else:
                config_error = "accounts_env_not_set"
            collector = Collector(Store(path), accounts, os.getenv("AUTO_PROBES", "false").lower() == "true",
                                  Transport(max(5, min(120, float(os.getenv("CALL_TIMEOUT", "45"))))))
            app.state.collector = collector
            app.state.config_error = config_error
            collector.start()
            yield
        finally:
            if collector:
                collector.stop.set()
                # Keep the process lock until all in-flight subprocess work ends.
                import asyncio
                await asyncio.to_thread(collector.thread.join)
            lock.release()

    app = FastAPI(title="Kaggle Queue Monitor", lifespan=lifespan)

    @app.get("/healthz")
    def health():
        collector = app.state.collector
        alive = collector.thread.is_alive()
        healthy = alive and not collector.last_error
        return JSONResponse({"ok": healthy, "worker_alive": alive, "ready": collector.ready,
                             "auto_probes": collector.auto, "heartbeat_utc": iso(collector.heartbeat) if collector.heartbeat else None,
                             "configuration_error": app.state.config_error, "worker_error": collector.last_error,
                             "readiness": collector.readiness}, status_code=200 if healthy else 503)

    @app.get("/api/summary")
    def summary():
        collector = app.state.collector
        store = collector.store
        runs = store.runs("state!='scheduled'")
        pending = store.runs("state='scheduled'")
        active = {r["account"]: r for r in store.runs("active=1")}
        accounts = []
        for name in sorted(set(collector.accounts) | set(active)):
            account_runs = [r for r in runs if r["account"] == name]
            latest = account_runs[0] if account_runs else None
            sampled = [r for r in account_runs if r.get("submit_begin") is not None]
            latest_sample = sampled[0] if sampled else None
            future = [r["due"] for r in pending if r["account"] == name]
            state = active[name]["state"] if name in active else "idle"
            accounts.append({"id": name, "name": name, "username": name,
                             "status": frontend_status(state), "state": state,
                             "sample_count": len(sampled),
                             "day_count": len({datetime.fromtimestamp(r["due"], LOCAL).date() for r in sampled}),
                             "active_runs": int(name in active),
                             "queue_estimate_seconds": queue_estimate(latest_sample) if latest_sample else None,
                             "queue_lower_seconds": latest_sample.get("queue_lower_seconds") if latest_sample else None,
                             "queue_upper_seconds": latest_sample.get("queue_upper_seconds") if latest_sample else None,
                             "updated_at": iso(latest_sample.get("last_observed", latest_sample["submit_begin"])) if latest_sample else None,
                             "active_run_id": active[name]["id"] if name in active else None,
                             "latest_run": frontend_run(latest) if latest else None,
                             "next_probe_utc": iso(min(future)) if future else None})
        with store.connect() as db:
            calls = [dict(r) for r in db.execute("SELECT * FROM calls ORDER BY id DESC LIMIT 200")]
        min_days = max(1, int(os.getenv("MIN_RANK_DAYS", "3")))
        hours = hourly_stats(runs, min_days)
        sample_count = sum(r.get("submit_begin") is not None for r in runs)
        day_count = len({datetime.fromtimestamp(r["due"], LOCAL).date() for r in runs if r.get("submit_begin") is not None})
        generated_at = iso(time.time())
        # Collection freshness is distinct from the timestamp of this HTTP response.
        updates = [r[key] for r in runs for key in ("submit_begin", "submit_ack", "last_observed", "last_api_end", "end")
                   if r.get(key) is not None]
        heartbeat = iso(collector.heartbeat) if collector.heartbeat is not None else None
        healthy = collector.thread.is_alive() and not collector.last_error
        meta = {"health": "ok" if healthy else "error", "timezone": "Asia/Ho_Chi_Minh",
                "stale_after_seconds": 2400,
                "data_updated_at": iso(max(updates)) if updates else None,
                "heartbeat": heartbeat, "heartbeat_utc": heartbeat,
                "sample_count": sample_count, "day_count": day_count,
                "auto_probes": collector.auto, "ready": collector.ready,
                "cancellation_supported": False,
                "success_5m_metric": "confirmed T4x2 submit-to-start upper bound <=300 seconds / all attempted submissions"}
        return {"meta": meta, "hours": hours, "timezone": "Asia/Ho_Chi_Minh", "generated_at": generated_at,
                 "auto_probes": collector.auto, "ready": collector.ready,
                "accounts": accounts, "runs": [frontend_run(r) for r in runs[:200]], "hourly": hours,
                "api_calls": calls, "minimum_rank_days": min_days,
                "statistics": {"day_count": day_count, "sample_count": sample_count,
                               "total_runs": len(runs), "skipped": sum(r["state"] == "skipped" for r in runs)},
                "definitions": {"latency": "probe UTC start minus push request begin; bounds include push API latency",
                                "queue": "queue_estimate_seconds is the conservative observed queue upper bound, not a measured duration; missed queued or still queued has no estimate",
                                "rate": "submit-to-start: definite <=300s successful T4x2 / all attempted submissions, including ambiguity and failures",
                                "percentiles": "median_queue_seconds and p90_seconds use known observed queue upper-bound estimates across all submitted outcomes; submit_to_start_p50/p90_seconds use successful T4x2 only; all-history local-hour buckets",
                                "unacceptable": "observed queue lower bound >600 seconds; submit_to_start_unacceptable is a separate latency flag",
                                "timeout": "observation alert only; account remains blocked until confirmed terminal"}}

    static = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=str(static), check_dir=False), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        page = static / "index.html"
        if page.is_file():
            return FileResponse(page)
        return JSONResponse({"detail": "Dashboard static/index.html is not installed"}, status_code=404)

    return app


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--helper":
    helper(sys.argv[2])
else:
    app = create_app()
