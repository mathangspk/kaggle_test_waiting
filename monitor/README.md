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
