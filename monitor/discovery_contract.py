"""Strict, credential-free browser evidence boundary shared by worker and API."""
from datetime import datetime, timezone
import hashlib
import math
import re
from urllib.parse import urlsplit, parse_qs

ACCOUNT = "thangma"  # Pilot allowlist; onboarding other identities is a separate phase.
ERRORS = {"visit_timeout", "visit_failed", "panel_unavailable", "invalid_event_identity",
          "worker_crash", "private_state_unavailable"}
AUTH_ERRORS = {"authentication_required", "identity_mismatch"}
STATES = {"Running": "running", "Queued": "pending", "Pending": "pending",
          "Successful": "terminal", "Complete": "terminal", "Failed": "terminal",
          "Canceled": "terminal", "Cancelled": "terminal", "Unknown": "unresolved"}


def iso(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def failure(code, at):
    return {"account": ACCOUNT, "observed_at": at, "coverage": "none", "observations": [],
            "error": code if code in ERRORS else None,
            "auth_error": code if code in AUTH_ERRORS else None}


def status_label(text):
    """Legacy fixture grammar: status must be the entire suffix after Version #N.

    Never scan a title/accessible-name sentence for terminal words. Real DOM rows
    use an independently extracted status leaf instead of this compatibility path.
    """
    suffix = re.split(r"\bVersion #\d+\b", text)[-1].strip()
    labels = suffix.split()
    if len(labels) > 1 and all(label in STATES for label in labels):
        raise ValueError("invalid_event_identity")
    return suffix if suffix in STATES else "Unknown"


def event(href, text, at, *, status=None):
    """Use exact owner/ref/version + provider scriptVersionId, never a session ID."""
    url = urlsplit(href)
    match = re.fullmatch(r"/code/(thangma)/([a-z0-9][a-z0-9-]{0,127})/?", url.path)
    query = parse_qs(url.query)
    ids = query.get("scriptVersionId", [])
    versions = set(re.findall(r"\bVersion #(\d+)\b", text))
    if (url.scheme != "https" or url.netloc != "www.kaggle.com" or not match
            or len(ids) != 1 or not re.fullmatch(r"[1-9][0-9]{0,17}", ids[0])
            or len(versions) != 1):
        raise ValueError("invalid_event_identity")
    version = int(next(iter(versions)))
    if not 1 <= version <= 1000000000:
        raise ValueError("invalid_event_identity")
    label = status_label(text) if status is None else status if status in STATES else "Unknown"
    return {"identity": "script-version:" + ids[0], "ref": "/".join(match.groups()),
            "version": version, "run_type": "unknown", "provider_state": label,
             "state": STATES[label], "observed_at": iso(at)}


def ui_observation(row, at, slot):
    """Snapshot-scoped positive UI evidence, NOT a provider/native session ID.

    No stable destination is known. Even an observed terminal label cannot resolve
    this identity, so state is always unresolved; no owner/ref/version is invented.
    """
    seed = repr((ACCOUNT, at, slot, row.get("text", ""), row.get("title", "")))
    return {"identity": "ui-observation:" + hashlib.sha256(seed.encode()).hexdigest(),
            "ref": None, "version": None,
            "run_type": "interactive" if row.get("interactive") is True else "unknown",
            "provider_state": row.get("status") if row.get("status") in STATES else "Unknown",
            "state": "unresolved", "observed_at": iso(at)}


def observation(row, at, slot):
    if row.get("structured"):
        try:
            # Only an already-present exact read-only destination is considered.
            # No editor or status role-link is followed to discover an identity.
            return event(row.get("href", ""), row["text"], at, status=row.get("status", "Unknown"))
        except ValueError:
            return ui_observation(row, at, slot)
    return event(row["href"], row["text"], at)


def validate(payload, accounts, now):
    def invalid():
        raise ValueError("invalid_discovery_snapshot")
    if not isinstance(payload, dict) or set(payload) != {
            "account", "observed_at", "coverage", "observations", "error", "auth_error"}:
        invalid()
    at = payload["observed_at"]
    if (payload["account"] != ACCOUNT or ACCOUNT not in accounts or type(at) not in (int, float)
            or not math.isfinite(at) or at < now - 86400 or at > now
            or payload["coverage"] not in ("none", "partial")):
        invalid()
    error, auth = payload["error"], payload["auth_error"]
    if (error is not None and error not in ERRORS) or (auth is not None and auth not in AUTH_ERRORS):
        invalid()
    rows = payload["observations"]
    if not isinstance(rows, list) or len(rows) > 100:
        invalid()
    if payload["coverage"] == "none":
        if rows or bool(error) == bool(auth):
            invalid()
    elif error or auth:
        invalid()
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"identity", "ref", "version", "run_type",
                                                   "provider_state", "state", "observed_at"}:
            invalid()
        ui = isinstance(row["identity"], str) and re.fullmatch(r"ui-observation:[0-9a-f]{64}", row["identity"])
        if ui:
            if (row["ref"] is not None or row["version"] is not None
                    or row["run_type"] not in ("interactive", "unknown") or row["state"] != "unresolved"
                    or row["provider_state"] not in STATES or row["observed_at"] != iso(at)
                    or row["identity"] in seen):
                invalid()
        elif (not isinstance(row["identity"], str)
                or not re.fullmatch(r"script-version:[1-9][0-9]{0,17}", row["identity"])
                or not isinstance(row["ref"], str)
                or not re.fullmatch(r"thangma/[a-z0-9][a-z0-9-]{0,127}", row["ref"])
                or type(row["version"]) is not int or not 1 <= row["version"] <= 1000000000
                or row["run_type"] != "unknown" or row["provider_state"] not in STATES
                or row["state"] != STATES[row["provider_state"]] or row["observed_at"] != iso(at)
                or row["identity"] in seen):
            invalid()
        seen.add(row["identity"])
    return payload
