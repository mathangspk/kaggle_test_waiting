# Probe admission — issue #23

## Explicit rollout policy

`PROBE_ADMISSION_POLICY=observe_only` is the default. `AUTO_PROBES` remains the
separate launch switch. Observe-only evaluates enforcing policy and persists
`admission.would_defer`, sorted `reasons`, `evaluated_at`, `activity_status`,
`required_sources` and `policy` on attempted/skipped opportunities. It retains
existing probe-only busy protection. It does **not** certify safe account idle.

`PROBE_ADMISSION_POLICY=enforce` must be explicitly selected only after #24
observation/resource/live acceptance. Invalid policy fails configuration; there
is no enforcing fallback to observe-only or idle on failure.

`PROBE_REQUIRED_ACTIVITY_SOURCES=browser_discovery` is the default, comma-separated
required exhaustive collector IDs. Every required source must have fresh full
coverage. Empty source lists are invalid. Partial known-version production
polling cannot substitute for exhaustive discovery. TTL uses persisted provider
observation time, inclusive at the boundary; future timestamps cannot prove idle.

Enforcing admission requires `observed_idle` plus all required source checks.
Running, pending and unresolved observations, stale/unknown nonterminal evidence,
any retained source error/auth failure, missing required source and incomplete
required coverage defer admission. Terminal known-run history alone never proves
idle. Errors on one account do not block others. A stale terminal-only optional
known-run source does not invalidate fresh exhaustive discovery.

Deferred slots are retained as `skipped`, `skip_reason=activity_deferred` with
`skip_reasons` and the full admission decision; they are not retried as catch-up
submissions and are not probe samples. Existing readiness, expiry, disabled and
probe busy skip reasons remain. `/api/summary` adds per-account `admission` and
meta `probe_admission_policy` / `required_activity_sources`; legacy `blocked`
remains probe-only. Admission diagnostics do not turn external events into runs.

## Local coordination and restart

Probe claim takes SQLite `BEGIN IMMEDIATE`, reloads the scheduled row, reads all
account probe/production records and snapshots, evaluates admission, then persists
either the skip or active `pushing` reservation in the same transaction. This
shares write serialization with receipt registration, attachment and observation
updates; concurrent probe claims through separate Store objects cannot reserve
two probes for one account. All account records are considered regardless of the
summary/history display window. The existing unique active-account index remains.

Reservations survive restart. Interrupted `pushing` becomes `ambiguous`; no
automatic re-push or age-based release is added. Registered/attached unresolved
production retains its identity and blocks enforcing admission even with legacy
`active=False`. Existing polling/reconciliation and terminal history are preserved.

This is **local database coordination, not an atomic provider-wide lock**.
Production registration is a post-submission receipt and attachment records an
existing job: neither endpoint controls the preceding external submission. A
receipt/attachment committed before a claim is included; one committed after a
claim cannot undo the probe reservation. Manual website and external producers
can race polling and local claims. The existing single collector instance lock
is still required; independent databases cannot coordinate with each other.

## Verification and activation blockers

`tests/test_admission.py` covers enforcing states, exact-version production,
interactive discovery, queued work, missing/incomplete/stale/future/error/auth
evidence, inclusive TTL and individual-observation expiry, observe-only diagnostics,
invalid configuration, account isolation, persisted ambiguity, simultaneous claims
and a claim waiting for an in-flight evidence transaction.

No deployment or live activation performed. #22 remains blocked on all-account
authentication and unattended Linux authentication transfer; full fresh discovery
is not available. #24 resource/resilience and live gating acceptance remain open.
Default rollout stays observe-only. No provider-wide exclusion is claimed.
