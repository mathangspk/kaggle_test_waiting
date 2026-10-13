# Kaggle Queue Monitor

Private Tailscale dashboard for four accounts, randomized stratified sampling once
per account per 30-minute window. At most one active probe per account. Dedicated
private `queue-monitor-probe` kernels request `NvidiaTeslaT4`; output validates two
actual T4 devices. Production notebooks are untouched.

## Run

Set `ACCOUNTS_ENV` to an external dotenv credentials file. Supports
`PRODUCTION_KAGGLE_ACCOUNT_COUNT` and indexed `PRODUCTION_KAGGLE_ACCOUNT_N_USERNAME`
and `PRODUCTION_KAGGLE_ACCOUNT_N_API_TOKEN`. Never commit this file.

Local: `pip install -r requirements.txt`, then `uvicorn app:app --workers 1`.
Automatic probes default off locally: set `AUTO_PROBES=true` to enable.
Server: place credentials in `secrets/accounts.env`, then `sudo docker compose up -d --build`.
Compose enables automatic probes. Data persists in the `monitor-data` volume.

## Measurements

Queued bounds are observational, not authoritative provider transition timestamps.
Missing queued observations stay unknown. Submit-to-start latency is reported
separately and includes provisioning. Confirmed T4x2 starts within 300 seconds
are the numerator of the 5-minute success rate; all attempted submissions are
the denominator, including failures and incomplete runs. Percentiles based only
on successful runs are explicitly labelled; skipped slots and day coverage are
shown separately. Rankings require at least three observed dates, but this is
only a preliminary signal, not proof of independent samples or GPU inventory.

Thresholds: <=5 minutes acceptable, >5..10 minutes slow, >10 minutes unacceptable.
No automatic cancellation: public SDK stop support remains unverified. A client
timeout is not remote cancellation. Ambiguous pushes block further probes for
that account until reconciled, preventing duplicate allocation.

API: `/healthz`, `/api/summary`. UI refreshes every 15 seconds.
Health exposes SDK readiness, configuration errors and probe enablement.
Tests: `python -m pytest tests -q`.

## Operations

Private URL: `https://vm-manager-thangma-z97m-d3h.tailf1bdc4.ts.net:8443`.
Requires authorized tailnet membership. Existing HTTPS port 443 is preserved.
Logs: `sudo docker compose logs --tail 100`; restart: `sudo docker compose restart`.
Pause new probes: change compose `AUTO_PROBES` to `false` and recreate service.
Already submitted runs continue to be tracked; Docker stop does not cancel them.
Back up the named SQLite volume before migration; do not use `down -v`.
Local dedicated SSH key is `monitor/.deploy/server_ed25519`, excluded from Git.

## Roadmap dashboard

The React/TypeScript frontend uses local shadcn-style Radix primitives and React
Flow for a read-only state map. Docker builds it with Node 22, then copies static
assets into the Python image; Node is not a production runtime service.

Pages: Overview, Hours, Runs, Roadmap, Logic, Operations. Hash routes support
direct links (for example `/#/logic`). Refresh is bounded at 15 seconds, aborts
overlapping work and retains the last successful snapshot after errors.

`python sync_roadmap.py` refreshes the packaged GitHub issue snapshot using `gh`.
Rebuild/deploy after syncing. The browser shows snapshot time and marks it stale
after 24 hours; runtime never needs a GitHub token. `/api/roadmap` and `/api/logic`
expose that snapshot and the canonical runtime graph. Logic counts cover all
persisted states, not only the latest 200 table rows.

Summary statistics use the latest 5,000 non-scheduled rows with an explicit
window; historical rows remain retained. A 10-second cache reduces repeated
computation. Worker watchdog, guarded readiness retry and 220-second Docker stop
grace cover bounded-call recovery and safe restart. Missing-account credentials
never prove remote termination or release an unresolved active job.

Frontend checks: `npm ci`, `npm test`, `npm run build` in `frontend/`.
Backend checks remain `python -m pytest tests -q`.

## Allocation analysis release

`/api/analysis` uses actual submission time, 2-hour Vietnam buckets and explicit
probe/production sources. Pending outcomes are unknown, not failed. Equal-account
rates preserve missing-account uncertainty; eligibility requires 20 resolved
attempts across 7 resolved dates per configured account and bucket. Wilson
diagnostics assume independent observations and are not a guarantee for correlated
Kaggle probes. Median/P90 remain pooled successful-run descriptive quantiles.

`/api/decision` compares waiting + successful historical P50/P90. Recommendations
are descriptive only: both totals must dominate alternatives by 30 seconds.
Conflicting or near-equal totals abstain. Fast-rate bounds are not latency
confidence. Missing baseline or candidate evidence also abstains.

Production tracking instructions: [PRODUCTION_TRACKING.md](PRODUCTION_TRACKING.md).
Registration and verification require `MONITOR_INGEST_TOKEN`, stored in server
`.env` with owner-only permissions. Deployment preserves an existing secret or
creates one without printing it. Use the same secret on a trusted submitting
machine. Registration alone never supplies verified start timing; opt-in copied
instrumentation plus explicit exact-version artifact verification are necessary.
No real production notebook was launched to test this release.

Balanced probe sampling accepts `SAMPLING_INTERVAL_MINUTES=30|60|120`, default 30.
The scheduler retains 30-minute strata and suppresses extra windows at longer
intervals, recording `sampling_interval` skips. It never deletes history or
releases active remote jobs when the interval changes. Four accounts have at
most 192/96/48 scheduled opportunities per day respectively, not guaranteed runs.
Sampling diagnostics withhold utility evaluation until separate training and
held-out date coverage exists. Comparisons are observational, not causal savings.
