# Phase 9 — bounded unattended production soak started

See `PHASE-8-PRODUCTION.md` for exact images, deployment, current known discovery
failure, backups, resource baseline and rollback. This is a **running observation
window**, not a24h/48h pass.

## Actual start and bounds

- Started **2026-10-15 22:15:02.427454 UTC**.
- Deadline **2026-10-17 22:15:02.427455 UTC** (48h).
- Transient host systemd unit: `kaggle-discovery-soak.service`, active/running.
- Sampling every60s **after sample completion**; missed time is not backfilled.
- Hard observer budget128MiB,5%CPU,32tasks; service RuntimeMaxSec173000.
- Internal persisted deadline prevents observer restart from extending the window.
- Restart=on-failure/30s for the **observer only**, no provider/browser/auth controls.
- JSONL rotation4MiB ×4files (~16MiB maximum samples), plus bounded latest/progress.
- Root0700directory/0600files. No auth/ingest-token values or raw container env,
  raw provider pages, screenshots or exception messages in emitted metrics.

Evidence: `/home/thangma/kaggle-production-phase8/soak/`
`samples.jsonl[.1-.3]`, `latest.json`, `progress.json`.
Root-owned observer source is `/home/thangma/kaggle-production-phase8/phase9_soak.py`;
does not depend on the laptop. Updating only observer source may restart its unit;
progress/deadline survive. Current monitor/discovery are **left running**.

## What is measured / alerted

Every sample records selected production container/image/start/restart/health/limits,
cgroup memory/CPU/throttle/PID/OOM, host headroom/load, private checkpoint's **safe
subset** (no outbox), HTTP status/timing, source freshness/coverage/admission summaries.

Alerts: stopped/missing service, OOM, missing cgroup/checkpoint, worker limits mismatch,
suspension, rejected ingestion, missing recent partial success, low host headroom,
HTTP failure, unexpected AUTO_PROBES or non-observe-only policy. Alert-set transitions
are written to the unit journal and current alerts persist in latest/progress.
**No external notification service was provisioned.** Alerts are not unattended
repair; auth recovery and browser restart need operator review. Monitor's pre-existing
unless-stopped policy remains its only automatic app recovery. Worker restart=no
intentionally prevents crash loops; Docker unhealthy alone does not restart it.

Current expected discovery alerts: `discovery_no_partial_success` and
`discovery_success_older600s`. At initial6-sample/~289s snapshot,5samples contained
alerts. At later10-sample checkpoint **534.117s elapsed,9alert samples**,52,331B
retained evidence; no observer restarts. Live worker successfully ingests failure snapshots, but has **no positive
partial success in production** due to unsupported interactive panel identity;
its Docker health is unhealthy. Collector/HTTP remain healthy. This window is
currently a **degraded discovery soak**, not a successful healthy-discovery soak.
Do not exclude these failure samples or reset counters to manufacture success.

Commands through approved remote.py private-env/private-stdin route:

```text
python3 /home/thangma/kaggle-production-phase8/ops/phase8_operator.py evidence
python3 /home/thangma/kaggle-production-phase8/ops/phase8_operator.py soak-summary
systemctl status kaggle-discovery-soak.service --no-pager
journalctl -u kaggle-discovery-soak.service --no-pager
```

Stopping the observer alone: `systemctl stop kaggle-discovery-soak.service`.
It does **not** stop production services. At natural deadline the observer exits,
leaving production running and evidence retained. Before claiming24h/48h, inspect
persisted elapsed time/sample gaps/restarts/alerts, exact image identity, cgroup
peaks, source success/error history and DB submission counters. Sampling gaps and
initial alert samples count against acceptance; elapsed wall time alone is not pass.

## Remaining elapsed and lifecycle gates

No24h or48h duration has elapsed in this invocation. No natural credential expiry,
provider revocation, captcha/MFA, destructive OOM, true provider-page hang, host
reboot or all-account discovery acceptance. Transient systemd unit **does not survive
host reboot**; reboot recovery/sustained observation across reboot needs separate
qualification. Do not reboot or start another worker for this soak test.

Once interactive identity support is evidence-backed and live positive ingestion
is repaired, retain this failed-window evidence and start an explicitly identified
new qualification window. Do not relabel prior failures as healthy. Partial-only
coverage never authorizes full-coverage idle or enforcement, even after48hours.
