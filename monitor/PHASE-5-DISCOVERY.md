# Phase 5 — bounded thangma discovery (#22), 2026-10-15

## Acceptance boundary

Local implementation/integration checks pass. Linux **stdlib-only** process-tree
fault checks and merged Compose configuration validation pass on approved host
`thangma-Z97M-D3H` (`100.92.238.77`, 31.21 GiB / 8 CPU).
**The new image was not built/run; live worker/Phase 7 acceptance is pending.**
No existing monitor changes deployed, services restarted, GPU work launched or
cancelled, admission activated, commit or push. #22 stays open.
Phase 3 authenticated thangma/restart evidence is a prerequisite, not a live test
of this implementation. annalynhi, thanglong1996 and thnhmngc remain pending.

## Exact owned files

- `discovery_contract.py`: fixed-account strict snapshot/row identity validation.
- `discovery_visit.py`: one read-only Playwright visit, independent current-user
  response + `/thangma` profile check, delayed-panel sampling/bounded scrolling,
  atomic private auth refresh restricted to Kaggle domains/origin.
- `discovery_worker.py`: sequential supervisor, auth-directory lock, process-group
  kill + Linux child-subreaper descendant sweep, durable single-slot outbox,
  suspension/schedule/metrics and bounded ingestion call.
- `Dockerfile.discovery`, `compose.discovery.yaml`: pinned isolated opt-in worker.
- `tests/test_discovery.py`: delayed/empty/changed panel, strict identities,
  scheduler/backoff/suspension/outbox/checkpoint, HTTP auth and durable evidence
  retention/recovery, Linux process-tree cases (skipped on Windows).
- `tests/discovery_linux_check.py`: standalone stdlib Linux process-tree checks.
- `app.py`: additive ingestion route + Request import; other work preserved.
- `Dockerfile`: additionally copy `discovery_contract.py` into monitor image.
- `ACTIVITY-CONTRACT.md`: additive ingestion documentation.
- This report. Original `compose.yaml` unchanged.

## Configuration prepared, not applied

Master must authorize Phase 7 before building/applying the overlay or restarting
the monitor. Applying it needs a reviewed monitor image rebuild/recreation too.

| Setting | Value |
| --- | --- |
| Explicit overlay/profile | `compose.discovery.yaml`, profile `discovery` |
| Worker enable flag | `DISCOVERY_ENABLED=false` by default; explicit `true` required |
| Monitor pilot policy in overlay | `PROBE_ADMISSION_POLICY=observe_only` |
| Memory / total memory+swap | `1g` / `1g` (no additional swap) |
| CPU / PID / shm bounds | `0.75` / `128` / `128m` |
| `/tmp` tmpfs | `192m`; tmpfs/shm charged within memory limit |
| Isolation | UID/GID 1000:1000, read-only root, drop ALL caps, no-new-privileges, init |
| Restart / shutdown | `restart: "no"` prevents pilot crash loops; 65s stop grace |
| Network | no published worker ports or Docker socket; internal monitor ingestion |
| Normal cadence | random 180–300s **after completion**, missed slots coalesced |
| Failure backoff | 180/360/720/1440/1800s + 0–30s jitter; no historical visit queue |
| Visit bounds | coroutine 50s; outer visit+cleanup <=60s, final 5s reserved for reap |
| Ingestion bound / TTL | 5s HTTP call / fixed evidence TTL 300s |

The overlay retains base monitor probe settings; do not run a base deployment or
launch work as a Phase 5 check. No enforcement activation is configured.

Image is pinned to the measured Phase 3 digest:
`mcr.microsoft.com/playwright/python@sha256:640d578aae63cfb632461d1b0aecb01414e4e020864ac3dd45a868dc0eff3078`.
Packages: `playwright==1.55.0`, `pyee==13.0.0`, `greenlet==3.2.4`. Build install has
60s outer timeout, binary-only/no cache/no retries/15s I/O timeout.

One account/browser tree at a time; no dashboard-triggered browser. Auth directory
lock also prevents duplicate containers sharing that directory. Linux subreaper
adopts detached/double-forked descendants for cleanup, not just the original
process group. If cleanup cannot finish, supervisor exits rather than overlap;
container termination owns any kernel-stuck process.

Private auth remains outside the repository:
`/home/thangma/.local/share/kaggle-monitor-auth/thangma/storage-state.json`,
parent 0700/file 0600, owner 1000:1000. Only that account directory is mounted.
Refresh uses exclusive 0600 temp + fsync + atomic rename. No auth copied to source,
images, logs or issue comments.

Future operator must privately provision a separate random discovery-only token
at `/home/thangma/.local/share/kaggle-monitor-discovery-token/token`, parent 0700,
file 0600, owner 1000:1000. **Not created in Phase 5.** `DISCOVERY_TOKEN_DIR` overrides
the host directory. Both containers mount it read-only at `/run/discovery-token`;
monitor reads `DISCOVERY_INGEST_TOKEN_FILE`. Directory mount supports atomic
rotation. This credential is separate from `MONITOR_INGEST_TOKEN`.

`discovery-data` persists private `state.json`: suspension, next due, newest
outbox, last success/attempt and metrics. Suspension/outbox checkpoint happens
before ingestion. New visits coalesce/replace an undelivered snapshot. Suspended
accounts retry only their failure outbox, never a browser. Auth/mismatched identity
or private-file errors suspend until operator recovery; ordinary errors back off.
To resume after verified private auth repair: stop worker, remove `suspended`,
`outbox`, `next_at` from its private state JSON, retain 0600 ownership, then
explicitly authorize starting it. Restart alone never clears suspension.

Health logs/state expose sanitized result, attempts/success, ingestion status,
failure count, duration, sampled peak PIDs, cgroup memory current/peak, CPU
usage/throttling, pids current/peak (where supported), memory/OOM events. Health
requires recent partial success (<600s), delivered ingestion and no suspension;
backoff can intentionally be unhealthy. No provider text, credentials, URLs,
screenshots, traces or exception messages are logged; no metrics port is exposed.

## Honest discovery and ingestion

Panel sampling waits at least 8s, up to 12 one-second bounded scrolling steps.
Tests reproduce the early false-zero footer with rows arriving at five seconds.
Visible Version rows without matched event links fail the attempt and retain
last-known evidence. Stability, scroll bottom and `0 Active Events` never prove
completeness. Successful samples, including empty panels, are **partial**, never
full/idle. Scrolling/pagination completeness is not asserted.

Supported rows require one positive `Version #N` plus a Kaggle
`/code/thangma/<slug>?scriptVersionId=<positive ID>` destination. Identity is
`script-version:<ID>` with owner/ref/version; duplicate/conflicting rows fail the
attempt, rather than silently discard an unknown active row. These are
browser-asserted script-version identities, not independently verified native
execution/session IDs. Run type stays unknown; no GPU hardware or timing is
invented. Unknown labels become unresolved. Unsupported interactive/destination
shapes require evidence-backed adapter extensions.

Ingestion accepts only the dedicated credential, fixed source and registered
thangma, <=64 KiB / 100 strict rows, no future timestamps or attempts older than
24h. The trusted worker asserts evidence; API does not re-query Kaggle or validate
provider HTML. TTL derives from attempt time, not ingestion time. Out-of-order
writes return `accepted=false`. Failures retain prior observations/success time
and set coverage none; writes invalidate summary cache through Store version bump.

## Checks

1. `uv run --python 3.12 --with-requirements monitor/requirements.txt python -m pytest monitor/tests -q`
   — **319 passed, 2 Linux-only skipped**, one dependency deprecation warning.
   Initial reduced-dependency run exposed a missing Kaggle module in an existing
   helper test; full pinned project requirements resolved it.
2. `tests/discovery_linux_check.py` on approved host using dedicated SSH key/system
   known-host verification — **2 passed in 1.527s**. Real fork + `setsid()` child
   killed/reaped after timeout and after early leader exit. No surviving test
   descendants; both visits below configured test deadline. No sudo, browser,
   private auth reads or application/service calls.
3. Host `docker compose -f <temporary base> -f <temporary overlay> --profile discovery config --quiet`
   — **exit 0**, syntax/merge validation only, not runtime HostConfig. Only three
   worker modules, stdlib check and two Compose files uploaded to ephemeral
   `/tmp/phase5-stdlib-check-*`; all files/directory removed afterward.
4. `git diff --check` passed (line-ending notices only). No local Docker executable;
   WSL cannot start because its required virtualization feature is unavailable.

Phase 3 measured 553.9/419.6 MiB cgroup peaks and 8.029/8.306 CPU-s for initial/
restart auth visits at these limits. **Not measurements of the new worker.**

## Phase 7 coverage gaps / master handoff

- Review/authorize deployment; verify actual new-image HostConfig/cgroup limits,
  ownership/init, auth/refresh/restart, real event-link parsing and ingestion.
- Repeated discovery -> API -> dashboard cycles, natural expiry/revocation,
  captcha/login/network faults, crash/OOM, rollback and persistence acceptance.
- Measure whole-container CPU/throttling, peak RAM/PIDs, OOM, host headroom and
  API/dashboard responsiveness. Bare-host stdlib fault tests are not worker budget
  measurements; no image build or live worker browser check was performed here.
- Long lists/pagination/lazy loading, native session/interactive identities and
  exhaustive discovery remain unverified. Even a successful live sample remains
  partial and cannot satisfy current full-coverage idle admission requirements.
- Other accounts, host reboot, unattended auth lifecycle and sustained resilience
  remain pending. Do not close live #22 acceptance or enable enforcement.
