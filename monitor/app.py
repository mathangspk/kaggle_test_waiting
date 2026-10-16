"""Private-network Kaggle queue collector. Run: uvicorn app:app --workers 1.

Configuration (process environment, never the credentials file):
ACCOUNTS_ENV=/external/accounts.env; AUTO_PROBES=false (default);
MONITOR_DB=monitor.sqlite3; MIN_RANK_DAYS=3; CALL_TIMEOUT=45.
SAMPLING_INTERVAL_MINUTES=30 (allowed: 30, 60, 120).
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
import hashlib
import hmac
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
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from starlette.requests import Request

UTC = timezone.utc
LOCAL = ZoneInfo("Asia/Ho_Chi_Minh")
SLUG = "queue-monitor-probe"

# Bumped whenever the normalized payload contract for /api/summary, /api/logic
# or /api/roadmap changes in a way clients may need to detect.
SCHEMA_VERSION = 1

# Explicit closed vocabularies. App states are what this collector persists.
# Provider states are the only remote statuses accepted from the SDK. Named
# constants prevent a fabricated or misspelled state from entering history.
APP_STATES = frozenset({
    "scheduled", "pushing", "ambiguous", "submitted", "queued", "running",
    "timed_out", "collecting", "success", "failed", "wrong_gpu",
    "invalid_output", "cancelled", "skipped",
})
ACTIVE = ("pushing", "ambiguous", "submitted", "queued", "running", "timed_out", "collecting")
ACTIVE_STATES = frozenset(ACTIVE)
TERMINAL_STATES = frozenset({"success", "failed", "wrong_gpu", "invalid_output", "cancelled"})
PENDING_STATES = frozenset({"scheduled", "skipped"})

PROVIDER_STATES = frozenset({"queued", "running", "pending", "cancel_requested",
                             "complete", "error", "cancelled"})
PROVIDER_TERMINAL = frozenset({"complete", "error", "cancelled"})
# Historical alias: TERMINAL previously held provider terminal statuses.
TERMINAL = PROVIDER_TERMINAL

# Readiness is retried on a bounded exponential backoff and re-verified after a
# long success interval. The watchdog only restarts the process when the worker
# is provably dead or hung past every bounded remote call.
READINESS_BACKOFF_INITIAL = 5.0
READINESS_BACKOFF_MAX = 300.0
READINESS_RECHECK_SECONDS = 1800.0
WATCHDOG_INTERVAL_SECONDS = 5.0
WATCHDOG_CALL_MARGIN_SECONDS = 30.0
DEFAULT_CALL_TIMEOUT = 45.0
SUMMARY_RUN_LIMIT = 5000

# Canonical runtime graph: nodes are the app states plus the collector/service
# boundary; edges are the only allowed progressions. Served read-only by
# /api/logic with live occupancy so the dashboard never fabricates transitions.
APP_STATE_DESCRIPTIONS = {
    "scheduled": "Slot planned for a 30-minute window; no remote job exists yet.",
    "pushing": "Submitting the private probe kernel; the remote save may be in flight.",
    "ambiguous": "Push outcome unknown. Blocked pending exact-version reconciliation; never resubmitted.",
    "submitted": "Push acknowledged with a confirmed version; observing provider status.",
    "queued": "Provider reports this exact version queued.",
    "running": "Provider reports this exact version running.",
    "timed_out": "Legacy active timeout state. Current polling records observation_timed_out without cancelling or releasing the run.",
    "collecting": "Provider confirmed terminal complete; fetching exact-version timing evidence.",
    "success": "Validated timing artifact showing two T4 devices. Terminal.",
    "wrong_gpu": "Validated timing artifact without two T4 devices. Terminal.",
    "invalid_output": "Exact version confirmed terminal but timing evidence stayed invalid after bounded retries. Terminal.",
    "cancelled": "Provider acknowledged cancellation. Terminal.",
    "failed": "Provider failure or explicit push rejection. Terminal.",
    "skipped": "Slot not launched (missed/disabled/blocked); no remote job exists.",
}
LOGIC_EDGES = (
    ("scheduled", "pushing", "Launch throttled to one push per 15s and one active run per account."),
    ("scheduled", "skipped", "Missed slot, probes disabled, not ready, or account already active."),
    ("pushing", "submitted", "Push response identity and version parsed exactly."),
    ("pushing", "ambiguous", "Timeout, unconfirmed identity, or an interrupted call. No resubmission."),
    ("pushing", "failed", "Explicit provider rejection of the save."),
    ("ambiguous", "submitted", "Reconciliation matched full source nonce and exact version."),
    ("ambiguous", "ambiguous", "Reconciliation still unconfirmed; account stays blocked."),
    ("submitted", "queued", "Provider reports queued for this version."),
    ("submitted", "running", "Provider reports running for this version."),
    ("submitted", "collecting", "Provider reports complete for this version."),
    ("submitted", "cancelled", "Provider acknowledged cancellation."),
    ("submitted", "failed", "Provider reports failure for this version."),
    ("queued", "running", "Queue observed, then running."),
    ("queued", "collecting", "Queue observed, then complete without a running observation."),
    ("queued", "failed", "Provider failure while queued."),
    ("running", "collecting", "Running observed, then complete."),
    ("running", "failed", "Provider failure while running."),
    ("collecting", "success", "Exact-version artifact validated as T4x2."),
    ("collecting", "wrong_gpu", "Exact-version artifact validated but without two T4 devices."),
    ("collecting", "invalid_output", "Bounded retries exhausted; exact version is terminal."),
    ("collecting", "collecting", "Retry exact-version output fetch with bounded backoff."),
) + tuple(
    (source, target, {
        "submitted": "Provider reports pending or cancellation requested; keep the account active.",
        "queued": "Provider reports queued for the exact version; keep observing.",
        "running": "Provider reports running for the exact version; keep observing.",
        "collecting": "Provider confirms complete; collect exact-version evidence.",
        "cancelled": "Provider acknowledges cancellation; the run is terminal.",
        "failed": "Provider confirms failure; the run is terminal.",
    }[target])
    for source in ("submitted", "queued", "running", "timed_out")
    for target in ("submitted", "queued", "running", "collecting", "cancelled", "failed")
    if (source, target) not in {
        ("submitted", "queued"), ("submitted", "running"), ("submitted", "collecting"),
        ("submitted", "cancelled"), ("submitted", "failed"), ("queued", "running"),
        ("queued", "collecting"), ("queued", "failed"), ("running", "collecting"),
        ("running", "failed"),
    }
)


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


def configured_sampling_interval(value=None):
    value = os.getenv("SAMPLING_INTERVAL_MINUTES", "30") if value is None else value
    if isinstance(value, bool) or str(value) not in {"30", "60", "120"}:
        raise ValueError("SAMPLING_INTERVAL_MINUTES must be 30, 60 or 120")
    return int(value)


def stratified_plan(window: int, usernames: list[str], rng=None, window_seconds=1800) -> list[dict]:
    if isinstance(window_seconds, bool) or not isinstance(window_seconds, int) or window_seconds <= 0:
        raise ValueError("window_seconds must be a positive integer")
    rng = rng or random.SystemRandom()
    order = list(usernames)
    if not order:
        return []
    rng.shuffle(order)
    width = window_seconds / len(order)
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
    if text not in PROVIDER_STATES:
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


ANALYSIS_POLICY = {"min_resolved": 20, "min_days": 7, "interval_minutes": 30}
ANALYSIS_DEFINITIONS = {
    "endpoint": "Confirmed successful exact-version timing artifact with T4x2 and submit_begin-to-start <=300 seconds.",
    "buckets": "Two-hour Asia/Ho_Chi_Minh buckets and dates use actual submit_begin; skipped local opportunities use due separately.",
    "resolved": "fast + slow (300<latency<=600) + over10 (latency>600) + confirmed failures; unresolved evidence stays unknown.",
    "unknown": "Includes pending (age<=300s), overdue_unconfirmed, ambiguous and unrecognized outcomes. Elapsed time or a running observation alone does not confirm a late start or failure.",
    "rates": "Per-account success_rate=fast/resolved; identification interval=[fast/attempted,(fast+unknown)/attempted]. Aggregate rates use equal configured-account weights; no-data accounts contribute [0,1] and make the point estimate unavailable.",
    "wilson": "95% Wilson bounds on resolved independent Bernoulli trials only; descriptive diagnostic, not authoritative confidence for correlated account/day observations. Aggregate bounds average per-account diagnostics, not a pooled confidence interval.",
    "percentiles": "p50/p90 use known confirmed successful T4x2 submit-to-start seconds only; failures and unresolved runs excluded. Aggregate percentiles describe pooled successful observations, not equal-account rates.",
    "eligibility": "Each selected configured account needs >=20 resolved outcomes across >=7 resolved local dates in that bucket. No exact sparse ranking.",
    "sampling": "Observed opportunities, attempts and local skips in a bounded recent window. Configured probe interval is 30, 60 or 120 minutes. Planning retains 30-minute strata; only the first UTC 30-minute window per configured interval is sampled, with other slots skipped as sampling_interval. GPU quota cannot be inferred.",
    "heldout": "Preliminary, read-only, noncausal fast-rate diagnostic. Split actual local submission dates chronologically: earlier dates train, latest seven dates test; require at least seven training and seven heldout dates. Eligible training two-hour buckets select a time-window policy by equal-account resolved fast rate. Heldout selected-window rates are compared with the overall heldout baseline using equal configured-account weights and explicit unknown/no-data coverage. This is an observational covariate comparison, not a counterfactual decision evaluation, latency confidence estimate or causal savings estimate.",
    "production": "Registration is metadata only. Caller timing is self-reported and excluded. Explicit authenticated verification can accept a complete exact-version provider-fetched queue-monitor-timing.json artifact with run_id matching registered event_id, observed ref, timestamps and T4x2. Optional artifact event_id must agree with run_id.",
    "decision": "Descriptive p50/p90 Pareto comparison only, with a 30-second material margin in both metrics. No latency confidence estimate or statistical guarantee; fast-rate identification intervals are not latency evidence. Upcoming candidates use their actual local weekday/weekend; waiting+p50/p90 is conditional on confirmed successes, not a completion guarantee.",
}


def analysis_policy(collector):
    return {**ANALYSIS_POLICY, "interval_minutes": collector.store.sampling_interval_minutes}


def local_day_type(dt):
    return "weekend" if dt.weekday() >= 5 else "weekday"


def wilson(fast, resolved):
    if not resolved:
        return None, None
    z = 1.959963984540054
    p = fast / resolved
    denominator = 1 + z * z / resolved
    center = (p + z * z / (2 * resolved)) / denominator
    radius = z * math.sqrt(p * (1 - p) / resolved + z * z / (4 * resolved ** 2)) / denominator
    return max(0, center - radius), min(1, center + radius)


def analysis_counts(runs, now):
    counts = dict.fromkeys(("attempted", "resolved", "fast", "slow", "over10", "failures",
                            "unknown", "pending", "overdue_unconfirmed", "skipped"), 0)
    latencies, dates, resolved_dates, names, skip_reasons = [], set(), set(), set(), {}
    for run in runs:
        begin = run.get("submit_begin")
        if begin is None:
            if run["state"] == "skipped" and run["due"] <= now:
                counts["skipped"] += 1
                reason = run.get("skip_reason", "unspecified")
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            continue
        counts["attempted"] += 1
        date = datetime.fromtimestamp(begin, LOCAL).date().isoformat()
        dates.add(date)
        names.add(run["account"])
        latency = run.get("start_latency_upper", run.get("start_latency_seconds"))
        # Legacy probe success is persisted only after validate_timing. Production
        # requires an explicit future provider verifier, never caller timing.
        verified = run.get("source", "probe") == "probe" or run.get("evidence_provenance") == "provider_artifact"
        known_success = (verified and run["state"] == "success" and run.get("t4x2") is True
                         and isinstance(latency, (int, float)) and not isinstance(latency, bool)
                         and math.isfinite(latency) and latency >= 0)
        if known_success:
            counts["fast" if latency <= 300 else "slow" if latency <= 600 else "over10"] += 1
            latencies.append(latency)
            counts["resolved"] += 1
            resolved_dates.add(date)
        elif run["state"] in {"failed", "wrong_gpu", "invalid_output", "cancelled"}:
            counts["failures"] += 1
            counts["resolved"] += 1
            resolved_dates.add(date)
        else:
            counts["unknown"] += 1
            if run["state"] in ACTIVE_STATES or run["state"] == "external_pending":
                counts["pending" if now - begin <= 300 else "overdue_unconfirmed"] += 1
    total, resolved = counts["attempted"], counts["resolved"]
    low, high = wilson(counts["fast"], resolved)
    eligible = resolved >= 20 and len(resolved_dates) >= 7
    return {**counts, "success_rate": counts["fast"] / resolved if resolved else None,
            "rate_lower": counts["fast"] / total if total else None,
            "rate_upper": (counts["fast"] + counts["unknown"]) / total if total else None,
            "wilson_low": low, "wilson_high": high,
            "median_seconds": percentile(latencies, .5), "p90_seconds": percentile(latencies, .9),
            "day_count": len(dates), "resolved_day_count": len(resolved_dates), "account_count": len(names),
            "eligible": eligible, "reason": None if eligible else "insufficient_resolved" if resolved < 20 else "insufficient_days",
            "skip_reasons": skip_reasons}


def analysis_buckets(runs, configured, now, day_type="all", account=None):
    names = [account] if account else sorted(configured)
    entries = {hour: [] for hour in range(0, 24, 2)}
    for run in runs:
        if account and run["account"] != account:
            continue
        at = run.get("submit_begin")
        if at is None:
            if run["state"] != "skipped" or run["due"] > now:
                continue
            at = run["due"]
        local = datetime.fromtimestamp(at, LOCAL)
        if day_type != "all" and local_day_type(local) != day_type:
            continue
        entries[local.hour // 2 * 2].append(run)
    result = []
    for hour, rows in entries.items():
        pooled = analysis_counts(rows, now)
        per_account = [{"account": name, **analysis_counts([r for r in rows if r["account"] == name], now)}
                       for name in sorted(set(names) | {r["account"] for r in rows})]
        weighted = [r for r in per_account if r["account"] in names]
        def average(key, no_data=None):
            values = [r[key] if r[key] is not None else no_data for r in weighted]
            return sum(values) / len(values) if values and all(v is not None for v in values) else None
        if not account:
            pooled.update(success_rate=average("success_rate"), rate_lower=average("rate_lower", 0),
                          rate_upper=average("rate_upper", 1), wilson_low=average("wilson_low"),
                          wilson_high=average("wilson_high"))
        eligible = bool(weighted) and all(r["eligible"] for r in weighted)
        missing = [r["account"] for r in weighted if not r["attempted"]]
        result.append({**pooled, "hour_start": hour, "label": f"{hour:02d}:00–{hour+2:02d}:00",
                       **({"account": account} if account else {}), "accounts": per_account,
                       "eligible": eligible, "reason": None if eligible else "missing_account_coverage" if missing or not weighted else "insufficient_account_evidence",
                       "coverage": {"configured": len(names), "observed": sum(bool(r["attempted"]) for r in weighted),
                                    "missing_accounts": missing}, "weighting": "equal_account", "rank": None})
    return result


def analysis_window(collector, source):
    store = collector.store
    if source == "production":
        with store.connect() as db:
            rows = db.execute("SELECT data FROM production_events ORDER BY submit_begin DESC,event_id LIMIT ?",
                              (SUMMARY_RUN_LIMIT + 1,)).fetchall()
        runs = [json.loads(r[0]) for r in rows]
    else:
        runs = store.runs("state!='scheduled'", limit=SUMMARY_RUN_LIMIT + 1)
    truncated = len(runs) > SUMMARY_RUN_LIMIT
    runs = runs[:SUMMARY_RUN_LIMIT]
    return runs, {"kind": "recent_source_records", "limit": SUMMARY_RUN_LIMIT, "count": len(runs),
                  "truncated": truncated, "ordering": "submit_begin_desc" if source == "production" else "due_desc",
                  "filters_applied_after_window": True, "all_history_retained": True}


def build_decision(collector, runs, source, account, now, mode="earliest", horizon_hours=6, day_type="all"):
    local = datetime.fromtimestamp(now, LOCAL)
    boundary = local.replace(hour=local.hour // 2 * 2, minute=0, second=0, microsecond=0) + timedelta(hours=2)
    times = [local]
    while boundary.timestamp() <= now + horizon_hours * 3600:
        times.append(boundary)
        boundary += timedelta(hours=2)
    candidates = []
    by_day_type = {}
    for at in times:
        actual_type = local_day_type(at)
        if day_type != "all" and actual_type != day_type:
            continue
        if actual_type not in by_day_type:
            by_day_type[actual_type] = analysis_buckets(runs, collector.accounts, now, actual_type, account)
        bucket = by_day_type[actual_type][at.hour // 2]
        waiting = at.timestamp() - now
        candidates.append({"at": iso(at.timestamp()), "local_at": at.isoformat(), "day_type": actual_type,
                           "hour_start": bucket["hour_start"], "waiting_seconds": waiting,
                           "total_p50_seconds": waiting + bucket["median_seconds"] if bucket["median_seconds"] is not None else None,
                           "total_p90_seconds": waiting + bucket["p90_seconds"] if bucket["p90_seconds"] is not None else None,
                           "accounts": [{"account": row["account"], "eligible": row["eligible"], "reason": row["reason"],
                                         "total_p50_seconds": waiting + row["median_seconds"] if row["median_seconds"] is not None else None,
                                         "total_p90_seconds": waiting + row["p90_seconds"] if row["p90_seconds"] is not None else None}
                                        for row in bucket["accounts"]],
                           "bucket": bucket})
    decision = {"schema_version": 2, "generated_at": iso(now), "mode": mode, "source": source,
                "account": account, "day_type": day_type, "horizon_hours": horizon_hours,
                "status": "abstain", "reason": "insufficient_evidence", "recommended_at": None,
                "certainty": "descriptive_only", "promise": False, "candidates": candidates,
                "selection_basis": "descriptive_p50_p90_pareto", "material_margin_seconds": 30,
                "limitations": ["no_latency_confidence", "no_statistical_guarantee", "confirmed_successes_only",
                                "pooled_latency_percentiles_may_reflect_account_imbalance"]}
    baseline = next((c for c in candidates if c["waiting_seconds"] == 0), None)
    if mode == "earliest" and (baseline is None or not baseline["bucket"]["eligible"] or baseline["total_p50_seconds"] is None):
        decision["reason"] = "missing_baseline"
        return decision
    if not candidates or any(not c["bucket"]["eligible"] or c["total_p50_seconds"] is None or c["total_p90_seconds"] is None for c in candidates):
        return decision
    metrics = ("total_p50_seconds", "total_p90_seconds")
    margin = decision["material_margin_seconds"]
    if len(candidates) == 1:
        decision.update(status="tentative", reason="sole_eligible_candidate", recommended_at=candidates[0]["at"])
        return decision
    dominant = next((candidate for candidate in candidates
                     if all(all(other[key] - candidate[key] >= margin for key in metrics)
                            for other in candidates if other is not candidate)), None)
    if dominant is not None:
        decision.update(status="tentative", reason="historical_comparison", recommended_at=dominant["at"])
        return decision
    # Compare only latency totals. A median/tail conflict cannot be settled by
    # unrelated endpoint-rate bounds or by lexicographic median ranking.
    frontier = [candidate for candidate in candidates if not any(
        all(other[key] <= candidate[key] for key in metrics) and any(other[key] < candidate[key] for key in metrics)
        for other in candidates if other is not candidate)]
    near_equivalent = any(all(abs(left[key] - right[key]) < margin for key in metrics)
                          for left in frontier for right in candidates if left is not right)
    frontier_conflict = any(max(c[key] for c in frontier) - min(c[key] for c in frontier) >= margin
                            for key in metrics)
    decision["reason"] = "equivalent" if near_equivalent and not frontier_conflict else "tradeoff"
    return decision


def heldout_sampling_diagnostic(runs, configured, now, account=None):
    names = [account] if account else sorted(configured)
    submitted = [r for r in runs if r.get("submit_begin") is not None and (account is None or r["account"] == account)]
    def date_of(run):
        return datetime.fromtimestamp(run["submit_begin"], LOCAL).date().isoformat()
    dates = sorted({date_of(r) for r in submitted})
    result = {"status": "unavailable", "reason": "insufficient_dates", "phase": "preliminary",
              "metric": "fast_rate", "causal": False, "counterfactual": False, "savings_seconds": None,
              "selection_basis": "training_equal_account_resolved_fast_rate",
              "limitations": ["observational_covariate_comparison", "no_causal_savings", "no_latency_confidence",
                              "selected_windows_are_subset_of_baseline", "bounded_recent_window"],
              "min_training_days": 7, "min_heldout_days": 7,
              "training_dates": [], "heldout_dates": [], "selected_hours": [],
              "training_record_count": 0, "heldout_record_count": 0,
              "selected": None, "baseline": None, "observed_rate_difference": None}
    if len(dates) < 14:
        return result
    train_dates, test_dates = dates[:-7], dates[-7:]
    train = [r for r in submitted if date_of(r) in train_dates]
    test = [r for r in submitted if date_of(r) in test_dates]
    result.update(training_dates=train_dates, heldout_dates=test_dates,
                  training_record_count=len(train), heldout_record_count=len(test))
    eligible = [b for b in analysis_buckets(train, configured, now, account=account)
                if b["eligible"] and b["success_rate"] is not None]
    if not eligible:
        result["reason"] = "insufficient_training_bucket_evidence"
        return result
    top_rate = max(b["success_rate"] for b in eligible)
    selected_hours = [b["hour_start"] for b in eligible if abs(b["success_rate"] - top_rate) < 1e-12]
    selected = [r for r in test if datetime.fromtimestamp(r["submit_begin"], LOCAL).hour // 2 * 2 in selected_hours]
    def weighted_counts(rows):
        pooled = analysis_counts(rows, now)
        accounts = [{"account": name, **analysis_counts([r for r in rows if r["account"] == name], now)} for name in names]
        def average(key, absent=None):
            values = [a[key] if a[key] is not None else absent for a in accounts]
            return sum(values) / len(values) if values and all(v is not None for v in values) else None
        return {**pooled, "accounts": accounts, "weighting": "equal_account",
                "success_rate": average("success_rate"), "rate_lower": average("rate_lower", 0),
                "rate_upper": average("rate_upper", 1), "wilson_low": average("wilson_low"), "wilson_high": average("wilson_high"),
                "coverage": {"configured": len(names), "observed": sum(bool(a["attempted"]) for a in accounts),
                             "missing_accounts": [a["account"] for a in accounts if not a["attempted"]],
                             "resolved_accounts": sum(bool(a["resolved"]) for a in accounts)}}
    baseline, selected_counts = weighted_counts(test), weighted_counts(selected)
    result.update(selected_hours=selected_hours, training_selected_fast_rate=top_rate,
                  baseline=baseline, selected=selected_counts)
    if selected_counts["success_rate"] is None or baseline["success_rate"] is None:
        result["reason"] = "insufficient_heldout_account_coverage"
        return result
    if any(a["day_count"] < 7 for a in selected_counts["accounts"]):
        result["reason"] = "insufficient_selected_heldout_dates"
        return result
    result.update(status="available", reason="noncausal_observational_comparison",
                  observed_rate_difference=selected_counts["success_rate"] - baseline["success_rate"])
    return result


def build_analysis(collector, source="probe", account=None, day_type="all", now=None):
    now = time.time() if now is None else now
    runs, window = analysis_window(collector, source)
    buckets = analysis_buckets(runs, collector.accounts, now, day_type, account)
    selected = []
    for run in runs:
        if account and run["account"] != account:
            continue
        if run.get("submit_begin") is None and (run["state"] != "skipped" or run["due"] > now):
            continue
        at = run.get("submit_begin", run["due"])
        if at is None:
            at = run["due"]
        if day_type == "all" or local_day_type(datetime.fromtimestamp(at, LOCAL)) == day_type:
            selected.append(run)
    submits = [r["submit_begin"] for r in selected if r.get("submit_begin") is not None]
    window.update(selected_count=len(selected), oldest_submit_utc=iso(min(submits)) if submits else None,
                  newest_submit_utc=iso(max(submits)) if submits else None)
    names = [account] if account else sorted(set(collector.accounts) | {r["account"] for r in selected})
    return {"schema_version": 2, "generated_at": iso(now), "metric": "submit_to_start",
            "timezone": "Asia/Ho_Chi_Minh", "definitions": ANALYSIS_DEFINITIONS,
            "policy": analysis_policy(collector), "source": source, "account": account, "day_type": day_type,
            "bucket_hours": 2, "statistics_window": window, "buckets": buckets,
            "accounts": [{"account": name, "configured": name in collector.accounts,
                          **analysis_counts([r for r in selected if r["account"] == name], now)} for name in names],
            "sampling": {"phase": "preliminary", "observed_opportunities": sum(b["attempted"] + b["skipped"] for b in buckets),
                         "attempted": sum(b["attempted"] for b in buckets), "skipped": sum(b["skipped"] for b in buckets),
                         "known_successes": sum(b["fast"] + b["slow"] + b["over10"] for b in buckets),
                         "day_count": len({datetime.fromtimestamp(at, LOCAL).date() for at in submits}),
                         "interval_minutes": collector.store.sampling_interval_minutes, "quota_inferable": False,
                         "recommendation": "collect_not_evaluate" if len({datetime.fromtimestamp(at, LOCAL).date() for at in submits}) < 14 else "review_heldout_diagnostic",
                         "heldout": heldout_sampling_diagnostic(selected, collector.accounts, now, account)},
            "decision": build_decision(collector, runs, source, account, now, day_type=day_type)}


def validate_production_registration(payload, accounts, now):
    required = {"event_id", "account", "requested_ref", "ref", "version", "submit_begin", "submit_ack", "accelerator", "push_outcome"}
    if not isinstance(payload, dict) or not required <= payload.keys() or payload.keys() - required - {"timing"}:
        raise ValueError("invalid_registration_fields")
    if not isinstance(payload["event_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", payload["event_id"]):
        raise ValueError("invalid_event_id")
    account, requested_ref, ref = payload["account"], payload["requested_ref"], payload["ref"]
    if not isinstance(account, str) or account.lower() not in accounts:
        raise ValueError("unregistered_account")
    def valid_ref(value):
        return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}/[A-Za-z0-9_-]{1,80}", value)
    if (not valid_ref(requested_ref) or requested_ref.split("/")[0].lower() != account.lower()
            or (ref is not None and not valid_ref(ref))):
        raise ValueError("invalid_owned_ref")
    if payload["accelerator"] != "NvidiaTeslaT4" or payload["push_outcome"] not in ("accepted", "ambiguous", "rejected"):
        raise ValueError("invalid_submission_metadata")
    if payload["push_outcome"] == "accepted" and ref != requested_ref:
        raise ValueError("unconfirmed_returned_ref")
    version = payload["version"]
    if (version is not None and (isinstance(version, bool) or not isinstance(version, int) or version < 1)) or (payload["push_outcome"] == "accepted" and version is None):
        raise ValueError("invalid_exact_version")
    try:
        begin = timestamp(payload["submit_begin"])
        ack = timestamp(payload["submit_ack"]) if payload["submit_ack"] is not None else None
        if not math.isfinite(begin) or begin > now + 5 or (ack is not None and not begin <= ack <= now + 5) or (ack is None and payload["push_outcome"] == "accepted"):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise ValueError("invalid_submission_timestamps") from None
    if "timing" in payload:
        timing = payload["timing"]
        if not isinstance(timing, dict) or set(timing) != {"start_utc", "end_utc", "gpu_names", "gpu_query_ok"}:
            raise ValueError("invalid_reported_timing")
        try:
            start, end = timestamp(timing["start_utc"]), timestamp(timing["end_utc"])
            names = timing["gpu_names"]
            if not begin - 5 <= start <= end <= now + 5 or not isinstance(timing["gpu_query_ok"], bool) or not isinstance(names, list) or len(names) > 16 or not all(isinstance(n, str) and len(n) < 200 for n in names):
                raise ValueError()
        except (ValueError, TypeError, AttributeError, OverflowError):
            raise ValueError("invalid_reported_timing") from None
    return payload


def matching_production_artifact(data, event_id, ref):
    return (isinstance(data, dict) and data.get("run_id") == event_id
            and ("event_id" not in data or data["event_id"] == event_id)
            and data.get("ref") == ref and data.get("source", "production") == "production")


def validate_production_attachment(payload, accounts):
    if not isinstance(payload, dict) or not {"event_id", "account", "ref", "version"} <= payload.keys() or payload.keys() - {"event_id", "account", "ref", "version", "script_version_id"}:
        raise ValueError("invalid_attachment_fields")
    if not isinstance(payload["event_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", payload["event_id"]):
        raise ValueError("invalid_event_id")
    account, ref = payload["account"], payload["ref"]
    if not isinstance(account, str) or account.lower() not in accounts:
        raise ValueError("unregistered_account")
    if not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}/[A-Za-z0-9_-]{1,80}", ref) or ref.split("/")[0].lower() != account.lower():
        raise ValueError("invalid_owned_ref")
    if type(payload["version"]) is not int or payload["version"] < 1:
        raise ValueError("invalid_exact_version")
    if "script_version_id" in payload and (type(payload["script_version_id"]) is not int or payload["script_version_id"] < 1):
        raise ValueError("invalid_script_version_id")


def production_identity_confirmed(run):
    return (run.get("identity_attached") or run.get("push_outcome") == "accepted") and (
        isinstance(run.get("ref"), str) and run["ref"] == run.get("requested_ref")
        and run["ref"].split("/")[0].lower() == run["account"]
        and type(run.get("version")) is int and run["version"] > 0)


class Store:
    def __init__(self, path, sampling_interval_minutes=None):
        self.path = str(path)
        self.sampling_interval_minutes = configured_sampling_interval(sampling_interval_minutes)
        # Monotonic counter bumped on every mutation; lets the HTTP layer cache
        # an expensive summary and invalidate it precisely instead of reloading
        # full history every 15 seconds.
        self._version_lock = threading.Lock()
        self.data_version = 0
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
                CREATE TABLE IF NOT EXISTS production_events (
                    event_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
                    submit_begin REAL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS activity_evidence (
                    account TEXT NOT NULL, source TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(account,source));
                CREATE TABLE IF NOT EXISTS production_poll_queue (
                    event_id TEXT PRIMARY KEY, account TEXT NOT NULL,
                    next_poll REAL NOT NULL, last_attempt REAL NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS production_poll_due ON production_poll_queue(next_poll);
                CREATE INDEX IF NOT EXISTS production_poll_account ON production_poll_queue(account);
                CREATE TABLE IF NOT EXISTS production_poll_accounts (
                    account TEXT PRIMARY KEY, last_attempt REAL NOT NULL);
            ''')
            # Old registrations required submission times; attachments do not.
            if any(r["name"] == "submit_begin" and r["notnull"] for r in db.execute("PRAGMA table_info(production_events)")):
                db.executescript('''ALTER TABLE production_events RENAME TO production_events_old;
                    CREATE TABLE production_events (event_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
                        submit_begin REAL, data TEXT NOT NULL);
                    INSERT INTO production_events SELECT * FROM production_events_old;
                    DROP TABLE production_events_old;''')
            for row in db.execute("SELECT event_id,data FROM production_events").fetchall():
                run = json.loads(row["data"])
                if run["state"] not in TERMINAL_STATES:
                    db.execute("INSERT OR IGNORE INTO production_poll_queue(event_id,account,next_poll) VALUES (?,?,?)",
                               (row["event_id"], run["account"], run.get("next_poll", 0)))
            db.commit()
            self._migrate_accounts(db)
        self.bump()

    def bump(self):
        with self._version_lock:
            self.data_version += 1
            return self.data_version

    def record_activity(self, account, source, observations, coverage, observed_at, *,
                        error=None, auth_error=None, stale_after_seconds=300):
        if coverage not in {"full", "partial", "none"} or not source or not account:
            raise ValueError("invalid_activity_snapshot")
        if not math.isfinite(observed_at) or not math.isfinite(stale_after_seconds) or stale_after_seconds <= 0:
            raise ValueError("invalid_activity_timestamp_or_ttl")
        for observation in observations:
            if (not observation.get("identity") or observation.get("run_type") not in {"probe", "production", "interactive", "unknown"}
                    or observation.get("state") not in {"running", "pending", "unresolved", "terminal"}
                    or "provider_state" not in observation or not isinstance(observation.get("observed_at"), str)):
                raise ValueError("invalid_activity_observation")
            # ISO serialization rounds to microseconds; allow only that rounding.
            if timestamp(observation["observed_at"]) > observed_at + 0.000001:
                raise ValueError("future_activity_observation")
        account = account.lower()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM activity_evidence WHERE account=? AND source=?", (account, source)).fetchone()
            previous = json.loads(row[0]) if row else {}
            if previous.get("last_attempt_at") and timestamp(previous["last_attempt_at"]) > observed_at:
                return False
            failed = bool(error or auth_error)
            snapshot = {"account": account, "source": source, "coverage": "none" if failed else coverage,
                        "observed_at": previous.get("observed_at") if failed else iso(observed_at),
                        "last_attempt_at": iso(observed_at), "error": error, "auth_error": auth_error,
                        "stale_after_seconds": stale_after_seconds,
                        "observations": previous.get("observations", []) if failed else observations}
            db.execute("INSERT OR REPLACE INTO activity_evidence VALUES (?,?,?)", (account, source, json.dumps(snapshot, allow_nan=False)))
        self.bump()
        return True

    def activity_evidence(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM activity_evidence ORDER BY account,source")]

    def register_production(self, payload, now):
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        begin = timestamp(payload["submit_begin"])
        run = {"id": "production:" + payload["event_id"], "account": payload["account"].lower(),
               "source": "production", "due": begin, "submit_begin": begin,
               "submit_ack": timestamp(payload["submit_ack"]) if payload["submit_ack"] is not None else None,
               "requested_ref": payload["requested_ref"], "ref": payload["ref"], "event_id": payload["event_id"],
               "version": payload["version"], "accelerator": payload["accelerator"],
               "push_outcome": payload["push_outcome"], "state": "external_pending",
               "active": False, "registered_at": now, "evidence_provenance": "registration_metadata",
               "reported_timing": payload.get("timing"), "reported_timing_provenance": "self_reported" if "timing" in payload else None}
        if payload["push_outcome"] == "rejected":
            run["state"] = "failed"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT payload_hash FROM production_events WHERE event_id=?", (payload["event_id"],)).fetchone()
            if existing:
                if not hmac.compare_digest(existing[0], digest):
                    raise ValueError("event_id_payload_conflict")
                return False
            db.execute("INSERT INTO production_events VALUES (?,?,?,?)", (payload["event_id"], digest, begin, json.dumps(run)))
            if run["state"] not in TERMINAL_STATES:
                db.execute("INSERT INTO production_poll_queue(event_id,account,next_poll) VALUES (?,?,?)", (payload["event_id"], run["account"], now))
        self.bump()
        return True

    def production_event(self, event_id):
        with self.connect() as db:
            row = db.execute("SELECT data FROM production_events WHERE event_id=?", (event_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def attach_production(self, payload, now):
        account = payload["account"].lower()
        owner, slug = payload["ref"].split("/")
        ref = owner.lower() + "/" + slug
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM production_events WHERE event_id=?", (payload["event_id"],)).fetchone()
            if row:
                run = json.loads(row[0])
                requested_owner, requested_slug = run["requested_ref"].split("/")
                if run["account"] != account or requested_owner.lower() + "/" + requested_slug != ref or run.get("push_outcome") == "rejected":
                    raise ValueError("attachment_identity_conflict")
                if production_identity_confirmed(run):
                    if run["ref"] != ref or run["version"] != payload["version"] or run.get("script_version_id") != payload.get("script_version_id"):
                        raise ValueError("attachment_identity_conflict")
                    return False
            else:
                run = {"id": "production:" + payload["event_id"], "event_id": payload["event_id"],
                       "account": account, "source": "production", "requested_ref": ref,
                       "due": None, "submit_begin": None, "submit_ack": None, "accelerator": None,
                       "push_outcome": None, "state": "external_pending", "active": False,
                       "registered_at": now, "evidence_provenance": "attachment_metadata"}
            run.update(ref=ref, version=payload["version"], identity_attached=True,
                       script_version_id=payload.get("script_version_id"), script_version_mapping_verified=False,
                       next_poll=now)
            if row:
                db.execute("UPDATE production_events SET data=? WHERE event_id=?", (json.dumps(run), payload["event_id"]))
            else:
                digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                db.execute("INSERT INTO production_events VALUES (?,?,NULL,?)", (payload["event_id"], digest, json.dumps(run)))
            db.execute("INSERT OR REPLACE INTO production_poll_queue VALUES (?,?,?,0)", (payload["event_id"], account, now))
        self.bump()
        return True

    def next_production_poll(self, now):
        with self.connect() as db:
            # Least recently served eligible account, then earliest due version.
            row = db.execute('''SELECT e.data FROM production_poll_queue q JOIN production_events e USING(event_id)
                WHERE q.next_poll<=? ORDER BY
                COALESCE((SELECT p.last_attempt FROM production_poll_accounts p WHERE p.account=q.account),0),
                q.next_poll,q.last_attempt,q.event_id LIMIT 1''', (now,)).fetchone()
        return json.loads(row[0]) if row else None

    def record_production_poll(self, event_id, data, error, auth_error, now):
        source = "production_api:" + event_id
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = json.loads(db.execute("SELECT data FROM production_events WHERE event_id=?", (event_id,)).fetchone()[0])
            if run.get("evidence_provenance") == "provider_artifact":
                return
            row = db.execute("SELECT data FROM activity_evidence WHERE account=? AND source=?", (run["account"], source)).fetchone()
            previous = json.loads(row[0]) if row else {}
            failed = bool(error or auth_error)
            if failed:
                run["poll_errors"] = run.get("poll_errors", 0) + 1
                delay = min(900, 60 * 2 ** min(run["poll_errors"] - 1, 4))
                observations = previous.get("observations", [])
            else:
                raw, status = data["status"], parse_status(data["status"])
                state = {"complete": "success", "error": "failed", "cancelled": "cancelled", "queued": "queued", "running": "running"}.get(status, "submitted")
                if data.get("failure") and status == "complete":
                    state = "failed"
                run.update(state=state, provider_status=raw, last_observed=now, poll_errors=0,
                           execution_provenance="provider_exact_version_status")
                if status in PROVIDER_TERMINAL:
                    run["terminal_observed"] = now
                delay = 60
                observations = [{"identity": run["id"], "run_type": "production", "ref": run["ref"],
                                 "version": run["version"], "provider_state": raw, "observed_at": iso(now),
                                 "state": "terminal" if status in PROVIDER_TERMINAL else "running" if status == "running" else "pending",
                                 "gpu_requested": run.get("accelerator"), "gpu_verified_devices": run.get("gpu_names")}]
            run.update(poll_error=error, poll_auth_error=auth_error, poll_attempt_at=now, next_poll=now + delay)
            db.execute("INSERT OR REPLACE INTO production_poll_accounts VALUES (?,?)", (run["account"], now))
            snapshot = {"account": run["account"], "source": source, "coverage": "none" if failed else "partial",
                        "observed_at": previous.get("observed_at") if failed else iso(now),
                        "last_attempt_at": iso(now), "error": error, "auth_error": auth_error,
                        "stale_after_seconds": 300, "observations": observations}
            db.execute("UPDATE production_events SET data=? WHERE event_id=?", (json.dumps(run), event_id))
            db.execute("INSERT OR REPLACE INTO activity_evidence VALUES (?,?,?)", (run["account"], source, json.dumps(snapshot)))
            if run["state"] in TERMINAL_STATES:
                db.execute("DELETE FROM production_poll_queue WHERE event_id=?", (event_id,))
            else:
                db.execute("UPDATE production_poll_queue SET next_poll=?,last_attempt=? WHERE event_id=?", (now + delay, now, event_id))
        self.bump()

    def verify_production(self, event_id, evidence, now):
        # Merge evidence without modifying the immutable registration hash or
        # identity. Concurrent successful callbacks cannot erase caller metadata.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM production_events WHERE event_id=?", (event_id,)).fetchone()
            if not row:
                raise ValueError("unknown_event_id")
            run = json.loads(row[0])
            run.update(evidence, state="success", active=False, evidence_provenance="provider_artifact",
                       evidence_observed_at=now, provider_status="complete", artifact_filename="queue-monitor-timing.json")
            db.execute("UPDATE production_events SET data=? WHERE event_id=?", (json.dumps(run), event_id))
            db.execute("DELETE FROM production_poll_queue WHERE event_id=?", (event_id,))
            source = "production_api:" + event_id
            snapshot = {"account": run["account"], "source": source, "coverage": "partial",
                        "observed_at": iso(now), "last_attempt_at": iso(now), "error": None, "auth_error": None,
                        "stale_after_seconds": 300, "observations": [{"identity": run["id"], "run_type": "production",
                        "ref": run["ref"], "version": run["version"], "state": "terminal",
                        "provider_state": "complete", "observed_at": iso(now), "gpu_verified_devices": run.get("gpu_names")}]}
            db.execute("INSERT OR REPLACE INTO activity_evidence VALUES (?,?,?)", (run["account"], source, json.dumps(snapshot)))
        self.bump()
        return run

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
        db.execute("CREATE INDEX IF NOT EXISTS runs_summary_due ON runs(due DESC,position) WHERE state!='scheduled'")

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
        interval_seconds = self.sampling_interval_minutes * 60
        changed = False
        # Interval changes operate only on unsubmitted scheduled slots. Keep all
        # attempts/active jobs and never revive a skipped historical opportunity.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT value FROM meta WHERE key='sampling_interval_minutes'").fetchone()
            previous_interval = json.loads(previous[0]) if previous else 30
            if previous_interval != self.sampling_interval_minutes:
                db.execute("INSERT OR REPLACE INTO meta VALUES ('next_window',?)", (json.dumps(current),))
                changed = True
            db.execute("INSERT OR REPLACE INTO meta VALUES ('sampling_interval_minutes',?)", (json.dumps(self.sampling_interval_minutes),))
            for row in db.execute("SELECT id,data FROM runs WHERE state='scheduled'").fetchall():
                run = json.loads(row["data"])
                if run["window"] % interval_seconds:
                    run.update(state="skipped", skip_reason="sampling_interval", sampling_interval_minutes=self.sampling_interval_minutes)
                    db.execute("UPDATE runs SET state='skipped',active=0,data=? WHERE id=?", (json.dumps(run), row["id"]))
                    changed = True
        cursor = self.get_meta("next_window", current)
        # Plan through the *next* window so a next_probe time is always visible
        # before its window opens. Missed windows are still persisted (bounded
        # to 96) but launch throttling prevents a catch-up burst.
        # Include the next eligible interval even when intervening 30m windows
        # are deliberately suppressed, so next_probe remains visible.
        next_sample_window = int(now // interval_seconds) * interval_seconds + interval_seconds
        horizon = min(next_sample_window + 1800, cursor + 96 * 1800)
        created = changed
        for window in range(cursor, horizon, 1800):
            with self.connect() as db:
                for item in stratified_plan(window, usernames):
                    suppressed = window % interval_seconds != 0
                    item.update(id=uuid.uuid4().hex, state="skipped" if suppressed else "scheduled",
                                sampling_interval_minutes=self.sampling_interval_minutes)
                    if suppressed:
                        item["skip_reason"] = "sampling_interval"
                    created = db.execute('''INSERT INTO runs
                        SELECT ?,?,?,?,?,?,?,0,?
                        WHERE NOT EXISTS (SELECT 1 FROM runs WHERE account=? COLLATE NOCASE AND window=?)''',
                               (item["id"], item["account"], window, item["slot"], item["due"], item["position"], item["state"], json.dumps(item),
                                item["account"], window)).rowcount == 1 or created
                db.execute("INSERT OR REPLACE INTO meta VALUES ('next_window',?)", (json.dumps(window + 1800),))
        if created:
            self.bump()

    def runs(self, where="1", args=(), limit=None):
        with self.connect() as db:
            rows = db.execute(f"SELECT data FROM runs WHERE {where} ORDER BY due DESC,position" +
                              (" LIMIT ?" if limit else ""), (*args, limit) if limit else args).fetchall()
            return [json.loads(r[0]) for r in rows]

    def state_occupancy(self):
        """Counts every persisted row by state, independent of the recent-200
        sample, so live occupancy always includes active runs outside the cut."""
        with self.connect() as db:
            by_state = {r["state"]: r["c"] for r in db.execute(
                "SELECT state, count(*) AS c FROM runs GROUP BY state")}
            active = {r["state"]: r["c"] for r in db.execute(
                "SELECT state, count(*) AS c FROM runs WHERE active=1 GROUP BY state")}
        return by_state, active

    def next_probes(self, now):
        with self.connect() as db:
            return {r["account"]: r["due"] for r in db.execute(
                "SELECT account,min(due) AS due FROM runs WHERE state='scheduled' AND due>=? GROUP BY account", (now,))}

    def save(self, run, active=None):
        canonical_run(run)
        if active is None:
            active = run["state"] in ACTIVE
        with self.connect() as db:
            db.execute("UPDATE runs SET account=?,state=?,active=?,data=? WHERE id=?",
                       (run["account"], run["state"], int(active), json.dumps(run), run["id"]))
        self.bump()

    def admission(self, account, now, required_sources=("browser_discovery",), db=None):
        if db is None:
            with self.connect() as connection:
                return self.admission(account, now, required_sources, connection)
        account = account.lower()
        tracked = [json.loads(r[0]) for r in db.execute("SELECT data FROM runs WHERE account=? COLLATE NOCASE", (account,))]
        tracked += [json.loads(r[0]) for r in db.execute(
            "SELECT data FROM production_events WHERE lower(json_extract(data,'$.account'))=?", (account,))]
        snapshots = [json.loads(r[0]) for r in db.execute("SELECT data FROM activity_evidence WHERE account=?", (account,))]
        activity = account_activity(account, tracked, snapshots, now)
        reasons = []
        # Independently retain local unresolved reservations/receipts even if a
        # collector snapshot reports terminal without resolving its tracked row.
        for record in tracked:
            if record["state"] not in TERMINAL_STATES | {"scheduled", "skipped"}:
                state = ("running" if record["state"] == "running" else "pending"
                         if record["state"] in {"queued", "submitted"} else "unresolved")
                reasons.append("activity_" + state)
        sources = {s["source"]: s for s in activity["sources"]}
        for name in required_sources:
            source = sources.get(name)
            if source is None:
                reasons.append("required_source_missing:" + name)
            else:
                if source["freshness"] != "fresh":
                    reasons.append("required_source_" + source["freshness"] + ":" + name)
                if source["coverage"] != "full":
                    reasons.append("required_coverage_incomplete:" + name)
        for source in activity["sources"]:
            if source.get("auth_error"):
                reasons.append("source_auth_error:" + source["source"])
            if source.get("error"):
                reasons.append("source_error:" + source["source"])
        for observation in activity["observations"]:
            if observation["state"] != "terminal":
                reasons.append("activity_" + observation["state"])
                if observation["freshness"] != "fresh":
                    reasons.append("nonterminal_" + observation["freshness"])
        if activity["status"] != "observed_idle":
            reasons.append("account_" + activity["status"])
        return {"evaluated_at": iso(now), "would_defer": bool(reasons),
                "reasons": sorted(set(reasons)), "activity_status": activity["status"],
                "required_sources": list(required_sources)}

    def claim(self, run, now, policy="observe_only", required_sources=("browser_discovery",)):
        if policy not in {"observe_only", "enforce"} or not required_sources:
            raise ValueError("invalid_admission_policy")
        canonical_run(run)
        try:
            with self.connect() as db:
                # Same write serialization as receipt registration, attachment and
                # collector snapshots. Persist reservation BEFORE any remote push.
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT data FROM runs WHERE id=? AND state='scheduled'", (run["id"],)).fetchone()
                if not row:
                    return False
                run.clear()
                run.update(json.loads(row[0]))
                canonical_run(run)
                decision = self.admission(run["account"], now, required_sources, db)
                decision["policy"] = policy
                run["admission"] = decision
                busy = db.execute("SELECT 1 FROM runs WHERE account=? COLLATE NOCASE AND active=1", (run["account"],)).fetchone()
                if busy or (policy == "enforce" and decision["would_defer"]):
                    run.update(state="skipped", skip_reason="account_active" if busy else "activity_deferred",
                               skip_reasons=decision["reasons"])
                    db.execute("UPDATE runs SET state='skipped',active=0,data=? WHERE id=?", (json.dumps(run), run["id"]))
                    claimed = False
                else:
                    run.pop("probe_ref", None)
                    run.update(state="pushing", ref=f"{run['account']}/{SLUG}", submit_begin=now,
                               next_poll=now, errors=0, version=None)
                    claimed = db.execute("UPDATE runs SET account=?,state='pushing',active=1,data=? WHERE id=? AND state='scheduled'",
                                         (run["account"], json.dumps(run), run["id"])).rowcount == 1
        except sqlite3.IntegrityError:
            return False
        self.bump()
        return claimed

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
                    production = operation in {"production_output", "production_status"}
                    if production and (payload.get("source") != "production"
                                       or not isinstance(payload.get("event_id"), str)
                                       or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", payload["event_id"])
                                       or not isinstance(payload.get("ref"), str)
                                       or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}/[A-Za-z0-9_-]{1,80}", payload["ref"])):
                        raise ValueError("Invalid production identity")
                    owner, slug = payload["ref"].split("/")
                    version = payload["version"]
                    if (isinstance(version, bool) or not isinstance(version, int) or version < 1
                            or (not production and slug != SLUG)
                            or (production and owner.lower() != os.getenv("KAGGLE_USERNAME", "").lower())):
                        raise ValueError("Invalid identity")
                    cls = ApiGetKernelSessionStatusRequest if operation in {"status", "production_status"} else ApiListKernelSessionOutputRequest
                    request = cls()
                    request.user_name, request.kernel_slug = owner, slug
                    # Official _kernel_version_label and test_kernel_versions.py
                    # require v<N>. 2.2.4 kernels_pull's slug/<N> is a different
                    # route; its status/output wrappers ignore versions entirely.
                    # https://github.com/Kaggle/kaggle-cli/blob/51b64b0fba360f97298178893010fe02c8da0519/tests/unit/test_kernel_versions.py
                    request.version_label = f"v{version}"
                    with api.build_kaggle_client() as client:
                        endpoint = client.kernels.kernels_api_client
                        if operation == "production_output":
                            status_request = ApiGetKernelSessionStatusRequest()
                            status_request.user_name, status_request.kernel_slug = owner, slug
                            status_request.version_label = f"v{version}"
                            status_response = endpoint.get_kernel_session_status(status_request)
                            status = parse_status(getattr(status_response.status, "name", str(status_response.status)))
                            if status != "complete" or bool(status_response.failure_message):
                                result = {"ok": True, "data": {"status": status, "requested_version": version, "timing": None}}
                                # No artifact fetch, no fabricated terminal failure.
                        if operation in {"status", "production_status"}:
                            response = endpoint.get_kernel_session_status(request)
                            status = getattr(response.status, "name", str(response.status))
                            result = {"ok": True, "data": {"status": status, "failure": bool(response.failure_message),
                                                             "requested_version": version}}
                            if production:
                                result["data"]["requested_ref"] = payload["ref"]
                        elif operation in ("output", "production_output") and (not production or (status == "complete" and not status_response.failure_message)):
                            # Do not use kernels_output: it also retrieves logs. Fetch
                            # ONLY timing.json from the exact version's file listing.
                            request.page_size = 100
                            response = endpoint.list_kernel_session_output(request)
                            filename = "queue-monitor-timing.json" if production else "timing.json"
                            files = [f for f in response.files or [] if f and f.file_name == filename]
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
                            if production:
                                if not matching_production_artifact(data, payload["event_id"], payload["ref"]):
                                    raise ValueError("Foreign production artifact")
                                result = {"ok": True, "data": {"status": "complete", "requested_version": version, "timing": data}}
                            else:
                                result = {"ok": True, "data": data}
                        elif not production:
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


class SummaryCache:
    """Short-lived, version-invalidated cache for the expensive summary build.

    All history stays in SQLite; this only avoids reloading it on every 15-second
    dashboard refresh. Any store mutation bumps the version and invalidates it.
    """

    def __init__(self, ttl=10.0):
        self.ttl = max(0.0, ttl)
        self._lock = threading.Lock()
        self._value = None
        self._version = -1
        self._at = 0.0

    def get(self, version, now=None):
        now = time.time() if now is None else now
        with self._lock:
            if (self._value is not None and self._version == version
                    and now - self._at < self.ttl):
                return self._value
        return None

    def put(self, version, value, now=None):
        now = time.time() if now is None else now
        with self._lock:
            self._value = value
            self._version = version
            self._at = now


class Collector:
    def __init__(self, store, accounts, auto=False, transport=None, admission_policy=None, required_activity_sources=None):
        self.store = store
        self.admission_policy = admission_policy if admission_policy is not None else os.getenv("PROBE_ADMISSION_POLICY", "observe_only")
        self.required_activity_sources = tuple(required_activity_sources if required_activity_sources is not None else
                                              os.getenv("PROBE_REQUIRED_ACTIVITY_SOURCES", "browser_discovery").split(","))
        if (self.admission_policy not in {"observe_only", "enforce"} or not self.required_activity_sources
                or any(not s or s.strip() != s for s in self.required_activity_sources)):
            raise ValueError("invalid_admission_policy")
        self.accounts = {a.username.lower(): Account(a.username.lower(), a.token, a.key) for a in accounts}
        if len(self.accounts) != len(accounts):
            raise ValueError("Duplicate account usernames")
        self.auto, self.transport = auto, transport or Transport()
        self.stop = threading.Event()
        self.thread = None
        self.watchdog_thread = None
        self.watchdog_started = False
        self.ready = False
        self.sdk_ready = False
        self.readiness = {"ok": False, "error": "not_checked"}
        self.readiness_attempts = 0
        self.readiness_next = 0.0
        self.readiness_backoff = READINESS_BACKOFF_INITIAL
        self.heartbeat = None
        self.last_error = None
        self.fatal_reason = None
        self.progress = "starting"
        self.started_at = None
        self.tick_started = None
        self.tick_finished = None
        self.calls_in_flight = 0
        self.api_in_flight = False
        self.api_started = None
        self.api_last_started = None
        self.api_last_end = None
        self.api_last_http_status = None
        self.call_timeout = float(getattr(self.transport, "timeout", 0) or DEFAULT_CALL_TIMEOUT)
        # One poll (probe or production) plus one optional probe push per tick.
        self.shutdown_grace = max(10.0, self.call_timeout * 2 + WATCHDOG_CALL_MARGIN_SECONDS)
        self.summary_cache = SummaryCache(ttl=float(os.getenv("SUMMARY_CACHE_SECONDS", "10") or 10))
        self._exit_hook = None  # Tests may intercept the process-exit watchdog.

    def _invoke(self, operation, payload, account=None):
        started = time.time()
        self.calls_in_flight += 1
        self.api_in_flight = True
        self.api_started = started
        self.api_last_started = started
        try:
            result = self.transport.invoke(operation, payload, account)
            if not isinstance(result, dict):
                result = {"ok": False, "error": "helper_protocol"}
        except Exception as exc:
            # A transport that raises unexpectedly is a bounded failed attempt,
            # not a reason to lose the worker or the persisted run.
            result = {"ok": False, "error": type(exc).__name__}
        finally:
            self.calls_in_flight -= 1
            self.api_in_flight = self.calls_in_flight > 0
            self.api_last_started = started
            self.api_last_end = time.time()
            self.api_last_http_status = result.get("http_status")
        return result

    def call(self, run, operation):
        account = self.accounts.get(run["account"])
        if account is None:
            # Removed account: report an attempt error but never fabricate a
            # terminal state or release the remote job. The run stays blocked.
            started = time.time()
            result = {"ok": False, "error": "account_removed"}
            self.api_last_started, self.api_last_end = started, time.time()
        else:
            result = self._invoke(operation, run, account)
        try:
            self.store.call(run["id"], operation, self.api_last_started, result)
        except Exception:
            pass  # Call telemetry must never break recovery.
        run["last_api_begin"] = self.api_last_started
        run["last_api_end"] = self.api_last_end
        run["last_api_latency_ms"] = (self.api_last_end - self.api_last_started) * 1000
        run["last_api_http_status"] = result.get("http_status")
        return result

    def start(self):
        self.started_at = time.time()
        # Persisted pushing means the remote save may already have happened.
        for run in self.store.runs("state='pushing'"):
            run.update(state="ambiguous", error="restart_during_push", next_poll=time.time() + 60)
            self.store.save(run)
        self.thread = threading.Thread(target=self.loop, name="queue-collector", daemon=True)
        self.thread.start()
        self.watchdog_thread = threading.Thread(target=self.watchdog, name="queue-watchdog", daemon=True)
        self.watchdog_thread.start()
        self.watchdog_started = True

    def loop(self):
        while not self.stop.is_set():
            if self.watchdog_started and not self.watchdog_thread.is_alive():
                self.fatal("watchdog_thread_dead")
                return
            self._iteration()
            self.stop.wait(1)

    def _iteration(self):
        now = time.time()
        self.tick_started = now
        try:
            # The initial SDK check runs inside the guarded loop so a cold or
            # slow SDK can never kill the worker thread.
            self.check_readiness(now)
            self.ready = self.sdk_ready and bool(self.accounts) and not self.stop.is_set()
            self.tick(time.time())
            self.tick_finished = time.time()
            self.heartbeat = self.tick_finished
            self.progress = "ok"
            self.last_error = None
            # Readiness needs a completed tick: a never-progressing worker is
            # idle and must not report ready.
            self.ready = self.sdk_ready and bool(self.accounts) and not self.stop.is_set()
        except Exception as exc:
            self.ready = False
            self.last_error = type(exc).__name__
            self.progress = "error"

    def check_readiness(self, now):
        if now < self.readiness_next:
            return
        self.readiness_attempts += 1
        result = self._invoke("check", {})
        self.readiness = result
        completed = max(now, self.api_last_end)
        if result.get("ok"):
            self.sdk_ready = True
            self.readiness_backoff = READINESS_BACKOFF_INITIAL
            self.readiness_next = completed + READINESS_RECHECK_SECONDS
        else:
            self.sdk_ready = False
            delay = self.readiness_backoff
            self.ready = False
            self.readiness_next = completed + delay
            self.readiness_backoff = min(READINESS_BACKOFF_MAX, delay * 2)

    def worker_stuck(self, now):
        """True only when progress is impossible within any bounded call."""
        if self.calls_in_flight > 0:
            return (now - (self.api_started if self.api_started is not None else now)) > self.call_timeout + WATCHDOG_CALL_MARGIN_SECONDS
        # A completed bounded call is progress too: readiness + poll + push can
        # share one iteration, without a finished tick between those calls.
        refs = [value for value in (self.tick_finished, self.started_at, self.api_last_end) if value is not None]
        ref = max(refs) if refs else None
        if ref is None:
            ref = self.tick_started
        if ref is None:
            return False
        return (now - ref) > self.call_timeout * 2 + WATCHDOG_CALL_MARGIN_SECONDS

    def watchdog_check(self, now):
        if self.stop.is_set() or self.thread is None:
            return None
        if not self.thread.is_alive():
            return "worker_thread_dead"
        if self.worker_stuck(now):
            return "worker_stuck"
        return None

    def watchdog(self):
        while not self.stop.wait(WATCHDOG_INTERVAL_SECONDS):
            reason = self.watchdog_check(time.time())
            if reason:
                self.fatal(reason)
                return

    def fatal(self, reason):
        """Terminate only on an irrecoverable worker failure so Docker restarts.
        No remote/queued job is ever cancelled by this path."""
        self.fatal_reason = reason
        self.ready = False
        self.progress = "fatal"
        self.stop.set()
        if self._exit_hook is not None:
            self._exit_hook(1)
            return
        os._exit(1)

    def shutdown(self, grace=None):
        """Bounded shutdown. Returns True only when the worker actually stopped."""
        self.stop.set()
        self.ready = False
        grace = self.shutdown_grace if grace is None else grace
        if self.thread is not None:
            self.thread.join(timeout=grace)
        if self.watchdog_thread is not None:
            self.watchdog_thread.join(timeout=5)
        return not (self.thread is not None and self.thread.is_alive())

    def recover_interrupted_pushes(self, now):
        # A persisted 'pushing' row at tick start means a previous attempt was
        # interrupted before its outcome was saved. Reconcile, never re-push.
        for run in self.store.runs("state='pushing'"):
            run.update(state="ambiguous", error="unexpected_push_interruption",
                       next_poll=now + 60)
            self.store.save(run)

    def tick(self, now):
        if self.stop.is_set():
            return
        self.recover_interrupted_pushes(now)
        self.store.plan(now, list(self.accounts))
        # One remote call per tick: fair earliest-due polling across accounts.
        polling = sorted((r for r in self.store.runs("active=1")
                          if r["state"] != "pushing"
                          and r["account"] in self.accounts and r.get("next_poll", 0) <= now),
                         key=lambda r: r.get("next_poll", 0))
        production = self.store.next_production_poll(now)
        # Alternate lanes under contention; production cannot starve probes or
        # add a third bounded remote call to the existing shutdown budget.
        if production and (not polling or self.store.get_meta("last_poll_lane", "probe") == "probe"):
            if not self.stop.is_set():
                self.store.set_meta("last_poll_lane", "production")
                self.poll_production(production)
        elif polling and not self.stop.is_set():
            self.store.set_meta("last_poll_lane", "probe")
            self.poll(polling[0], now)
        last_launch = self.store.get_meta("last_launch", 0)
        due = sorted(self.store.runs("state='scheduled' AND due<=?", (now,)),
                     key=lambda r: (r["due"], r["position"]))
        launched = False
        busy = {r["account"] for r in self.store.runs("active=1")}
        for run in due:
            if self.stop.is_set():
                break
            # Polling can take up to CALL_TIMEOUT; don't launch an expired slot
            # based on the timestamp taken before that blocking subprocess.
            decision_time = max(now, time.time())
            run["admission"] = {**self.store.admission(run["account"], decision_time, self.required_activity_sources),
                                "policy": self.admission_policy}
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
                if self.store.claim(run, decision_time, self.admission_policy, self.required_activity_sources):
                    self.store.set_meta("last_launch", decision_time)
                    launched = True
                    busy.add(run["account"])
                    self.push(run, decision_time)

    def poll_production(self, run):
        error, auth_error, data = None, None, None
        if not production_identity_confirmed(run):
            error = "unconfirmed_submission_identity"
        elif run["account"] not in self.accounts:
            auth_error = "account_removed"
        else:
            result = self.call(run, "production_status")
            if not result.get("ok"):
                if result.get("http_status") in {401, 403}:
                    auth_error = "provider_auth_denied"
                else:
                    error = "api_timeout" if result.get("error") == "api_timeout" else "provider_status_unavailable"
            else:
                data = result.get("data")
                try:
                    if (not isinstance(data, dict) or type(data.get("requested_version")) is not int
                            or data["requested_version"] != run["version"] or data.get("requested_ref") != run["ref"]
                            or type(data.get("failure")) is not bool):
                        raise ValueError()
                    parse_status(data["status"])
                except (ValueError, KeyError, TypeError):
                    error = "status_identity_or_protocol"
        self.store.record_production_poll(run["event_id"], data, error, auth_error, time.time())

    def push(self, run, decision_time):
        try:
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
                    # Explicit save rejection, not an HTTP timeout or missing
                    # identity, proves this attempt was rejected.
                    run.update(state="failed", error="provider_push_rejected",
                               terminal_observed=run["push_end"])
                else:
                    version = parse_push(result["data"], run["ref"])
                    run.update(state="submitted", version=version, submit_ack=run["push_end"],
                               next_poll=time.time() + 15)
            except (ValueError, KeyError, TypeError):
                run.update(state="ambiguous", error=result.get("error", "push_identity_unconfirmed"),
                           next_poll=time.time() + 60)
            self.store.save(run)
        except Exception:
            # Unexpected failure after the claim: the remote save may already
            # have happened, so reconcile rather than re-push.
            run.update(state="ambiguous", error="push_call_exception",
                       next_poll=time.time() + 60)
            self.store.save(run)
        # The outcome is durable now. Bookkeeping failure must surface through
        # the guarded iteration, never downgrade confirmed identity or reactivate
        # an explicitly rejected run as ambiguous.
        self.store.set_meta("last_launch", run["push_end"] if run["state"] == "failed"
                            else max(decision_time, time.time()))

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
                run.update(evidence, state="success" if evidence["t4x2"] else "wrong_gpu",
                           evidence_observed_at=time.time())
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
            elif status == "cancelled":
                # An acknowledged cancellation is terminal. This is the only
                # transition that maps to the explicit cancelled state.
                run.update(state="cancelled", terminal_observed=observed)
                run.pop("error", None)
            else:  # status == "error"
                run.update(state="failed", terminal_observed=observed, error="provider_error")
            if status in {"running", *PROVIDER_TERMINAL} and run.get("first_queued") is not None:
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


def logic_graph(by_state: dict, active_by_state: dict) -> dict:
    """Canonical, read-only runtime graph with live occupancy.

    Occupancy is counted over every persisted row, so an active run older than
    the recent-200 summary sample is still represented here.
    """
    nodes = []
    for state in sorted(APP_STATES):
        nodes.append({
            "id": state, "kind": "app_state", "label": state,
            "active": state in ACTIVE_STATES, "terminal": state in TERMINAL_STATES,
            "description": APP_STATE_DESCRIPTIONS[state],
            "occupancy": int(by_state.get(state, 0)),
            "active_occupancy": int(active_by_state.get(state, 0)),
        })
    nodes.append({
        "id": "provider", "kind": "remote", "label": "Kaggle provider",
        "active": False, "terminal": False, "occupancy": None, "active_occupancy": None,
        "description": "Remote Kaggle kernels/session status. Polled read-only; cancellation unsupported.",
    })
    edges = [{"from": source, "to": target, "description": description}
             for source, target, description in LOGIC_EDGES]
    occupancy = {
        "total": int(sum(by_state.values())),
        "active_total": int(sum(active_by_state.values())),
        "by_state": {k: int(v) for k, v in sorted(by_state.items())},
        "active_by_state": {k: int(v) for k, v in sorted(active_by_state.items())},
    }
    return {
        "nodes": nodes,
        "edges": edges,
        "occupancy": occupancy, "counts": occupancy,
    }


def roadmap_snapshot() -> dict:
    """Read the locally packaged roadmap.json. Never calls GitHub."""
    override = os.getenv("ROADMAP_JSON")
    path = Path(override) if override else Path(__file__).with_name("roadmap.json")
    if not path.is_file():
        return {"available": False, "reason": "roadmap_json_not_packaged",
                "source": "local_roadmap_json", "snapshot": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("repo"), str) or not isinstance(payload.get("synced_at"), str):
            raise ValueError("Invalid roadmap snapshot")
        if not all(isinstance(payload.get(key), list) for key in ("issues", "dependencies", "blocked")):
            raise ValueError("Invalid roadmap collections")
        for issue in payload["issues"]:
            if not isinstance(issue, dict) or type(issue.get("number")) is not int or not all(
                    isinstance(issue.get(key), str) for key in ("title", "state", "url", "body", "updatedAt")):
                raise ValueError("Invalid roadmap issue")
        if not all(isinstance(edge, dict) and type(edge.get("from")) is int and type(edge.get("to")) is int
                   for edge in payload["dependencies"]) or not all(type(number) is int for number in payload["blocked"]):
            raise ValueError("Invalid roadmap dependencies")
    except (OSError, ValueError):
        return {"available": False, "reason": "roadmap_json_unreadable",
                "source": "local_roadmap_json", "snapshot": None}
    return {"available": True, "reason": None, "source": "local_roadmap_json",
            "snapshot": payload}


def account_activity(name, tracked, snapshots, now):
    sources, observations = [], []
    for snapshot in snapshots:
        if snapshot["account"] != name:
            continue
        source = {k: v for k, v in snapshot.items() if k != "observations"}
        at = timestamp(source["observed_at"]) if source.get("observed_at") else None
        source["freshness"] = ("unknown" if at is None else "fresh"
                               if 0 <= now - at <= source["stale_after_seconds"] else "stale")
        sources.append(source)
        for observation in snapshot["observations"]:
            age = now - timestamp(observation["observed_at"])
            observations.append({**observation, "account": name, "source": source["source"],
                                 "coverage": source["coverage"],
                                 "stale_after_seconds": source["stale_after_seconds"],
                                 "freshness": "fresh" if 0 <= age <= source["stale_after_seconds"] else "stale"})
    for run in tracked:
        if run["account"] != name:
            continue
        state = run["state"]
        if state in {"scheduled", "skipped"}:
            continue
        normalized = ("terminal" if state in TERMINAL_STATES else "running" if state == "running"
                      else "pending" if state in {"queued", "submitted"} else "unresolved")
        if run.get("source") == "production":
            source = "production_api:" + run["event_id"]
            matching = [o for o in observations if o["source"] == source and o["identity"] == run["id"]]
            if normalized == "unresolved":
                # A snapshot cannot resolve the underlying registration. Keep
                # its source/error metadata, but expose one unresolved identity
                # until the adapter actually updates the tracked record.
                observations = [o for o in observations if not (o["source"] == source and o["identity"] == run["id"])]
            elif matching:
                # Resolved records use the adapter's authoritative snapshot,
                # including failure retention, without duplicate positives.
                continue
        at = run.get("last_observed", run.get("evidence_observed_at", run.get("registered_at", run.get("submit_ack"))))
        if run.get("source") == "production":
            at = run.get("last_observed", run.get("evidence_observed_at"))
        observations.append({"account": name, "identity": run["id"], "source": "tracked_" + run.get("source", "probe"),
                             "run_type": run.get("source", "probe"), "ref": run.get("ref"), "version": run.get("version"),
                             "state": normalized, "provider_state": run.get("last_status", run.get("provider_status")),
                             "observed_at": iso(at) if at is not None else None, "coverage": "partial",
                             "stale_after_seconds": 300,
                             "freshness": "fresh" if at is not None and 0 <= now - at <= 300 else "stale" if at is not None else "unknown",
                             "gpu_requested": run.get("accelerator"), "gpu_verified_devices": run.get("gpu_names")})
    # A failed collector invalidates its retained positive evidence for current status.
    failed_sources = {s["source"] for s in sources if s.get("error") or s.get("auth_error")}
    current = [o for o in observations if o["freshness"] == "fresh" and o["source"] not in failed_sources]
    nonterminal = [o for o in observations if o["state"] != "terminal"]
    full = any(s["coverage"] == "full" and s["freshness"] == "fresh" for s in sources)
    coverage = "full" if full else "partial" if observations or any(s["coverage"] == "partial" for s in sources) else "none"
    if any(o["state"] == "running" for o in current):
        status = "running"
    elif any(o["state"] == "pending" for o in current):
        status = "pending"
    elif any(o["state"] == "unresolved" for o in nonterminal):
        status = "unresolved"
    elif failed_sources:
        status = "unknown"
    elif nonterminal or (sources and all(s["freshness"] == "stale" for s in sources)):
        status = "stale"
    elif full:
        status = "observed_idle"
    else:
        status = "partial" if coverage == "partial" else "unknown"
    times = [s["observed_at"] for s in sources if s.get("observed_at")]
    times += [o["observed_at"] for o in observations if o.get("observed_at")]
    return {"status": status, "coverage": coverage, "observed_at": max(times, key=timestamp) if times else None,
            "sources": sources, "observations": observations}


def build_summary(collector: Collector, config_error, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    store = collector.store
    runs = store.runs("state!='scheduled'", limit=SUMMARY_RUN_LIMIT)
    pending = store.next_probes(now)
    active = {r["account"]: r for r in store.runs("active=1")}
    snapshots = store.activity_evidence()
    with store.connect() as db:
        # Bound terminal history, but never hide older unresolved production registrations.
        production = [json.loads(r[0]) for r in db.execute('''SELECT data FROM production_events
            WHERE json_extract(data,'$.state') NOT IN ('success','failed','wrong_gpu','invalid_output','cancelled')
            OR event_id IN (SELECT event_id FROM production_events ORDER BY submit_begin DESC,event_id LIMIT ?)
            ORDER BY submit_begin DESC,event_id''', (SUMMARY_RUN_LIMIT,))]
    tracked = list({r["id"]: r for r in runs + list(active.values()) + production}.values())
    accounts = []
    for name in sorted(set(collector.accounts) | set(active) | {r["account"] for r in production} | {s["account"] for s in snapshots}):
        account_runs = [r for r in runs if r["account"] == name]
        latest = active.get(name) or (account_runs[0] if account_runs else None)
        sampled = [r for r in account_runs if r.get("submit_begin") is not None]
        latest_sample = sampled[0] if sampled else None
        state = active[name]["state"] if name in active else "idle"
        activity = account_activity(name, tracked, snapshots, now)
        accounts.append({"id": name, "name": name, "username": name,
                          "admission": {**store.admission(name, now, collector.required_activity_sources),
                                        "policy": collector.admission_policy},
                         "status": activity["status"], "state": activity["status"], "activity": activity,
                         "probe_status": frontend_status(state), "probe_active_runs": int(name in active),
                         "sample_count": len(sampled),
                         "day_count": len({datetime.fromtimestamp(r["due"], LOCAL).date() for r in sampled}),
                         "active_runs": int(name in active),
                         "blocked": int(name in active),
                         "queue_estimate_seconds": queue_estimate(latest_sample) if latest_sample else None,
                         "queue_lower_seconds": latest_sample.get("queue_lower_seconds") if latest_sample else None,
                         "queue_upper_seconds": latest_sample.get("queue_upper_seconds") if latest_sample else None,
                         "updated_at": iso(latest_sample.get("last_observed", latest_sample["submit_begin"])) if latest_sample else None,
                         "active_run_id": active[name]["id"] if name in active else None,
                         "latest_run": frontend_run(latest) if latest else None,
                         "next_probe_utc": iso(pending[name]) if name in pending else None})
    with store.connect() as db:
        calls = [dict(r) for r in db.execute("SELECT * FROM calls ORDER BY id DESC LIMIT 200")]
    min_days = max(1, int(os.getenv("MIN_RANK_DAYS", "3")))
    hours = hourly_stats(runs, min_days)
    sample_count = sum(r.get("submit_begin") is not None for r in runs)
    day_count = len({datetime.fromtimestamp(r["due"], LOCAL).date() for r in runs if r.get("submit_begin") is not None})
    generated_at = iso(now)
    # Evidence freshness is distinct from the timestamp of this HTTP response
    # and from the latest API attempt or the worker heartbeat.
    updates = [r[key] for r in runs + list(active.values()) for key in ("submit_ack", "last_observed", "terminal_observed", "evidence_observed_at", "end")
               if r.get(key) is not None]
    heartbeat = iso(collector.heartbeat) if collector.heartbeat is not None else None
    last_api_attempt = iso(collector.api_last_started) if collector.api_last_started is not None else (iso(calls[0]["at"]) if calls else None)
    alive = bool(collector.thread and collector.thread.is_alive())
    healthy = alive and not collector.last_error and collector.fatal_reason is None
    meta = {"schema_version": SCHEMA_VERSION,
            "health": "ok" if healthy else "error", "timezone": "Asia/Ho_Chi_Minh",
            "stale_after_seconds": 2400,
            "data_updated_at": iso(max(updates)) if updates else None,
            "evidence_updated_at": iso(max(updates)) if updates else None,
            "heartbeat": heartbeat, "heartbeat_utc": heartbeat,
            "last_api_attempt_utc": last_api_attempt,
            "api_in_flight": collector.api_in_flight,
            "config_ok": config_error is None, "configuration_error": config_error,
            "sdk_ready": collector.sdk_ready, "worker_alive": alive,
            "sample_count": sample_count, "day_count": day_count,
            "statistics_window": {"kind": "recent_runs", "limit": SUMMARY_RUN_LIMIT,
                                  "count": len(runs), "oldest_due_utc": iso(runs[-1]["due"]) if runs else None,
                                  "newest_due_utc": iso(runs[0]["due"]) if runs else None,
                                  "all_history_retained": True,
                                  "cache_ttl_seconds": collector.summary_cache.ttl},
             "auto_probes": collector.auto, "ready": collector.ready,
             "probe_admission_policy": collector.admission_policy,
             "required_activity_sources": list(collector.required_activity_sources),
            "cancellation_supported": False,
            "success_5m_metric": "confirmed T4x2 submit-to-start upper bound <=300 seconds / all attempted submissions"}
    return {"schema_version": SCHEMA_VERSION, "activity_contract_version": 1,
            "meta": meta, "hours": hours, "timezone": "Asia/Ho_Chi_Minh", "generated_at": generated_at,
            "auto_probes": collector.auto, "ready": collector.ready,
            "accounts": accounts, "runs": [frontend_run(r) for r in runs[:200]], "hourly": hours,
            "api_calls": calls, "minimum_rank_days": min_days,
            "statistics": {"day_count": day_count, "sample_count": sample_count,
                           "total_runs": len(runs), "skipped": sum(r["state"] == "skipped" for r in runs)},
            "definitions": {"latency": "probe UTC start minus push request begin; bounds include push API latency",
                            "queue": "queue_estimate_seconds is the conservative observed queue upper bound, not a measured duration; missed queued or still queued has no estimate",
                            "rate": "submit-to-start: definite <=300s successful T4x2 / all attempted submissions, including ambiguity and failures",
                            "percentiles": "median_queue_seconds and p90_seconds use known observed queue upper-bound estimates across submitted outcomes; submit_to_start_p50/p90_seconds use successful T4x2 only; recent-run window in meta.statistics_window, local-hour buckets",
                            "unacceptable": "observed queue lower bound >600 seconds; submit_to_start_unacceptable is a separate latency flag",
                            "timeout": "observation alert only; account remains blocked until confirmed terminal"}}


def live_health(collector, config_error):
    now = time.time()
    alive = bool(collector.thread and collector.thread.is_alive())
    stuck = collector.watchdog_check(now)
    watchdog_alive = not collector.watchdog_started or collector.watchdog_thread.is_alive()
    healthy = alive and watchdog_alive and stuck is None and not collector.last_error and collector.fatal_reason is None and not collector.stop.is_set()
    ready = bool(healthy and collector.ready and collector.sdk_ready and config_error is None)
    return {
        "schema_version": SCHEMA_VERSION, "ok": bool(healthy), "ready": ready,
        "worker_alive": alive, "watchdog_alive": watchdog_alive,
        "auto_probes": collector.auto,
        "heartbeat_utc": iso(collector.heartbeat) if collector.heartbeat is not None else None,
        "tick_started_utc": iso(collector.tick_started) if collector.tick_started is not None else None,
        "tick_finished_utc": iso(collector.tick_finished) if collector.tick_finished is not None else None,
        "api_in_flight": collector.api_in_flight,
        "last_api_attempt_utc": iso(collector.api_last_started) if collector.api_last_started is not None else None,
        "last_api_result_utc": iso(collector.api_last_end) if collector.api_last_end is not None else None,
        "sdk_ready": collector.sdk_ready, "config_ok": config_error is None,
        "configuration_error": config_error, "worker_error": collector.last_error,
        "progress": collector.progress, "watchdog": stuck,
        "fatal_reason": collector.fatal_reason, "readiness_attempts": collector.readiness_attempts,
        "readiness": collector.readiness,
    }


def refresh_summary_health(payload, collector, config_error):
    health = live_health(collector, config_error)
    return {**payload, "ready": health["ready"], "meta": {
        **payload["meta"], **health, "health": "ok" if health["ok"] else "error",
        "heartbeat": health["heartbeat_utc"],
    }}


def create_app():
    from fastapi import FastAPI, HTTPException, Header, Body, Query
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from dotenv import dotenv_values

    @contextlib.asynccontextmanager
    async def lifespan(app):
        path = Path(os.getenv("MONITOR_DB", str(Path(__file__).with_name("monitor.sqlite3")))).resolve()
        lock = InstanceLock(path)
        lock.acquire()
        app.state.instance_lock = lock
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
            collector.instance_lock = lock
            app.state.collector = collector
            app.state.config_error = config_error
            app.state.ingest_token = os.getenv("MONITOR_INGEST_TOKEN", "")
            collector.start()
            yield
        finally:
            if collector:
                import asyncio
                # Bounded join. If the worker will not stop within the grace, do
                # NOT release the process lock: let the process exit release the
                # OS lock rather than allow a second instance to start.
                stopped = await asyncio.to_thread(collector.shutdown)
                if stopped:
                    lock.release()
            else:
                lock.release()

    app = FastAPI(title="Kaggle Queue Monitor", lifespan=lifespan)

    def collector_health(collector) -> dict:
        result = live_health(collector, app.state.config_error)
        return result, result["ok"]

    @app.get("/healthz")
    def health():
        collector = app.state.collector
        body, healthy = collector_health(collector)
        return JSONResponse(body, status_code=200 if healthy else 503)

    @app.get("/api/summary")
    def summary():
        collector = app.state.collector
        store = collector.store
        version = store.data_version
        cached = collector.summary_cache.get(version)
        if cached is not None:
            return refresh_summary_health(cached, collector, app.state.config_error)
        payload = build_summary(collector, app.state.config_error)
        collector.summary_cache.put(version, payload)
        return refresh_summary_health(payload, collector, app.state.config_error)

    def analysis_filters(source, account, day_type):
        if source not in ("probe", "production") or day_type not in ("all", "weekday", "weekend"):
            raise HTTPException(422, "invalid_analysis_filter")
        if account is not None:
            account = account.lower()
            if account not in app.state.collector.accounts:
                raise HTTPException(422, "unregistered_account")
        return account

    @app.get("/api/analysis")
    def analysis(source: str = "probe", account: str | None = None, day_type: str = "all"):
        account = analysis_filters(source, account, day_type)
        return build_analysis(app.state.collector, source, account, day_type)

    @app.get("/api/decision")
    def decision(mode: str = "earliest", horizon_hours: int = Query(6, ge=1, le=48),
                 source: str = "probe", account: str | None = None, day_type: str = "all"):
        account = analysis_filters(source, account, day_type)
        if mode not in ("earliest", "schedule"):
            raise HTTPException(422, "invalid_decision_mode")
        collector = app.state.collector
        runs, window = analysis_window(collector, source)
        return {**build_decision(collector, runs, source, account, time.time(), mode, horizon_hours, day_type),
                "statistics_window": window, "metric": "submit_to_start", "policy": analysis_policy(collector),
                "definitions": ANALYSIS_DEFINITIONS}

    def authenticate_ingest(x_monitor_token):
        token = app.state.ingest_token
        if not token:
            raise HTTPException(503, "production_ingest_disabled")
        if not hmac.compare_digest(x_monitor_token.encode(), token.encode()):
            raise HTTPException(401, "invalid_monitor_token")

    @app.post("/api/activity/browser-discovery")
    async def browser_discovery(request: Request, x_discovery_token: str = Header("")):
        # Separate least-privilege credential: production ingest tokens do not
        # authorize browser evidence, and browser tokens cannot register work.
        from discovery_contract import validate
        try:
            token_path = Path(os.environ["DISCOVERY_INGEST_TOKEN_FILE"])
            if token_path.stat().st_size > 512:
                raise ValueError("invalid_token")
            token = token_path.read_text().strip()
            if not token:
                raise ValueError("invalid_token")
        except (KeyError, OSError, ValueError):
            raise HTTPException(503, "discovery_ingest_disabled") from None
        if not hmac.compare_digest(x_discovery_token.encode(), token.encode()):
            raise HTTPException(401, "invalid_discovery_token")
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 65536:
                raise HTTPException(413, "discovery_payload_too_large")
            data.extend(chunk)
        collector = app.state.collector
        try:
            payload = validate(json.loads(data), collector.accounts, time.time())
        except (ValueError, TypeError, KeyError, OverflowError):
            raise HTTPException(422, "invalid_discovery_snapshot") from None
        import asyncio
        accepted = await asyncio.to_thread(
            collector.store.record_activity, payload["account"], "browser_discovery",
            payload["observations"], payload["coverage"], payload["observed_at"],
            error=payload["error"], auth_error=payload["auth_error"], stale_after_seconds=300)
        return {"accepted": accepted, "source": "browser_discovery", "coverage": payload["coverage"]}

    @app.post("/api/production/register")
    def production_register(payload: dict = Body(...), x_monitor_token: str = Header("")):
        authenticate_ingest(x_monitor_token)
        try:
            validate_production_registration(payload, app.state.collector.accounts, time.time())
            created = app.state.collector.store.register_production(payload, time.time())
        except ValueError as exc:
            reason = str(exc)
            raise HTTPException(409 if reason == "event_id_payload_conflict" else 422, reason) from None
        return {"schema_version": 2, "event_id": payload["event_id"], "created": created,
                "source": "production", "evidence_provenance": "registration_metadata",
                "primary_timing_accepted": False, "polled": False, "blocks_probe": False}

    @app.post("/api/production/attach")
    def production_attach(payload: dict = Body(...), x_monitor_token: str = Header("")):
        authenticate_ingest(x_monitor_token)
        try:
            validate_production_attachment(payload, app.state.collector.accounts)
            created = app.state.collector.store.attach_production(payload, time.time())
        except ValueError as exc:
            reason = str(exc)
            raise HTTPException(409 if reason == "attachment_identity_conflict" else 422, reason) from None
        return {"schema_version": 2, "event_id": payload["event_id"], "created": created,
                "source": "production", "primary_timing_accepted": False,
                "identity_provenance": "caller_attachment", "script_version_mapping_verified": False,
                "blocks_probe": False}

    @app.post("/api/production/verify")
    def production_verify(payload: dict = Body(...), x_monitor_token: str = Header("")):
        authenticate_ingest(x_monitor_token)
        if set(payload) not in ({"event_id"}, {"id"}):
            raise HTTPException(422, "invalid_verification_fields")
        event_id = payload.get("event_id", payload.get("id"))
        if not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", event_id):
            raise HTTPException(422, "invalid_event_id")
        collector = app.state.collector
        run = collector.store.production_event(event_id)
        if run is None:
            raise HTTPException(404, "unknown_event_id")
        response = {"schema_version": 2, "event_id": event_id, "source": "production", "verified": False,
                    "state": run["state"], "evidence_provenance": run["evidence_provenance"], "blocks_probe": False}
        if run["evidence_provenance"] == "provider_artifact":
            return {**response, "verified": True, "reason": "already_verified"}
        account = collector.accounts.get(run["account"])
        if account is None:
            return {**response, "reason": "unregistered_account"}
        # Never promote requested identity, a timeout, or an uncertain version
        # into a confirmed provider identity. Legacy registrations may lack it.
        if not production_identity_confirmed(run):
            return {**response, "reason": "unconfirmed_submission_identity"}
        if run.get("submit_begin") is None:
            return {**response, "reason": "submission_time_unavailable"}
        try:
            fetched = collector.transport.invoke("production_output", {
                "source": "production", "ref": run["ref"], "version": run["version"], "event_id": event_id}, account)
            if not isinstance(fetched, dict) or fetched.get("ok") is not True:
                return {**response, "reason": "provider_evidence_unavailable"}
            data = fetched["data"]
            if (data.get("status") != "complete" or type(data.get("requested_version")) is not int
                    or data["requested_version"] != run["version"]):
                return {**response, "reason": "exact_version_not_complete"}
            timing = data.get("timing")
            if not isinstance(timing, dict):
                return {**response, "reason": "provider_evidence_unavailable"}
            if (not matching_production_artifact(timing, event_id, run["ref"])
                    or type(timing.get("fetched_version")) is not int):
                return {**response, "reason": "invalid_provider_artifact"}
            evidence = validate_timing(timing, {**run, "id": event_id}, time.time())
            if not evidence["t4x2"]:
                return {**response, "reason": "invalid_provider_artifact"}
        except Exception:
            # Network, SDK, artifact and clock errors leave registration unknown;
            # never expose remote exception text, URLs or credentials.
            return {**response, "reason": "provider_evidence_unavailable"}
        verified = collector.store.verify_production(event_id, evidence, time.time())
        return {**response, "verified": True, "state": verified["state"],
                "evidence_provenance": "provider_artifact", "reason": "exact_version_artifact_validated"}

    @app.get("/api/logic")
    def logic():
        collector = app.state.collector
        by_state, active_by_state = collector.store.state_occupancy()
        graph = logic_graph(by_state, active_by_state)
        return {"schema_version": SCHEMA_VERSION, "generated_at": iso(time.time()),
                "states": sorted(APP_STATES), "active_states": sorted(ACTIVE_STATES),
                "terminal_states": sorted(TERMINAL_STATES),
                "provider_states": sorted(PROVIDER_STATES),
                "cancellation_supported": False,
                "notes": "Read-only canonical graph. Occupancy counts every persisted row; "
                         "active runs older than the recent-200 sample are still included.",
                **graph}

    @app.get("/api/roadmap")
    def roadmap():
        snapshot = roadmap_snapshot()
        return {"schema_version": SCHEMA_VERSION, "generated_at": iso(time.time()), **snapshot}

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
