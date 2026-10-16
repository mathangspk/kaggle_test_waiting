# Phase 6 — independent local integration readiness review (#23 / #24)

2026-10-15. Master owns integration/deployment. Reviewed Phase 5, activity and
admission contracts, current tracked diff and new worker/tests/configuration.
No browser, SSH, deployment, service restart, GPU launch/cancel, commit or push.

## Decision

**Local admission/integration accepted for a thangma-only observe-only pilot.**
Live Phase 7 acceptance remains pending. No enforcement recommendation: the
discovery adapter deliberately emits only partial coverage, including empty
panels. It cannot meet the default fresh/full required-source idle policy.
Successful resource/live testing alone would not change that coverage boundary.

## Findings and fixes

1. **Mixed-panel evidence loss:** the selector fallback checked visible version
   text only when *no* supported links existed. A supported terminal row could
   hide a different unsupported active row. `discovery_visit.py` now checks every
   sample's visible version multiset against linked versions, failing the attempt
   if any are unmatched. Conflicting version labels for the same destination
   across samples also fail instead of overwriting identity. Current status may
   still change for the same identity. New offline regressions cover both cases.
   This is a conservative visible-row consistency check, not completeness proof;
   unsupported non-Version shapes, lazy loading and pagination remain unverified.
2. **HTTP wall-clock bound:** urllib's five-second socket timeout did not cap the
   whole exchange. `discovery_worker.py` now sends in a separate non-browser Python
   process with a five-second supervisor timeout, terminating/reaping that process
   on timeout. Token stays in the private mounted file, payload goes through stdin,
   and output/errors are suppressed. A real local stalled-HTTP regression proves
   the outer timeout; no browser or provider is involved. Ingestion failure leaves
   the durable outbox and backs off. Visit deadline remains separate (<=60s).
3. Added an API -> persisted partial empty snapshot -> summary -> enforcing claim
   regression: account remains partial, default policy observe_only, enforcing
   claim skips with `required_coverage_incomplete:browser_discovery`, without push.

## Independent review results

- Identity: exact current-user username set plus independent `/thangma` profile
  destination; strict HTTPS Kaggle owner/ref/script-version identity and positive
  version, duplicate rejection, unknown labels unresolved. Native execution/session
  identity, GPU hardware and exhaustive idle evidence are not asserted. The API
  trusts the dedicated worker assertion; it does not independently query Kaggle.
- Secret boundary: auth mounted only in worker; scoped Kaggle cookies/origin on
  atomic refresh, 0700 directory/0600 state, no auth in logs or image COPY. Token
  is separate from production ingestion, read-only directory mounts support
  rotation, endpoint authenticates before streaming <=64 KiB, thangma allowlist,
  strict <=100-row partial/none schema, 24h attempt age, no future timestamp,
  fixed 300s TTL. Dedicated credential does not authorize production endpoints.
  API token-file owner/mode is an operator provisioning requirement; unlike the
  worker, API does not itself enforce Linux private-file permissions.
- Delayed/empty UI: >=8s bounded sampling and scrolling; zero footer/stability
  never imply full/idle. Failure retains last-known observations and success time.
- Bounds: one sequential browser tree, auth-directory flock, 50s coroutine,
  <=60s outer visit/cleanup, Linux subreaper + detached-descendant sweep, fatal
  cleanup failure prevents another visit. 1 GiB/no extra swap, 0.75 CPU, 128 PIDs,
  128 MiB shm, 192 MiB tmpfs, non-root/read-only/cap-drop/init, no restart loop.
  These are configured limits, not new-worker runtime measurements. Phase 5 Linux
  stdlib process-tree results are prior evidence, not rerun in this review.
- Schedule/outbox: completion-relative 180–300s, capped failure backoff+jitter,
  missed slots coalesced, one persisted newest snapshot, suspension checkpoint
  before ingestion; auth/identity/private-state failures stop future browser visits.
  Unsent snapshots may be replaced by a newer visit by design. Suspended outbox
  older than API's 24h acceptance window needs operator recovery; no automatic
  authentication repair or indefinite deliverability is claimed.
- Admission: explicit observe_only default; independent AUTO_PROBES launch switch.
  Observe-only **can still submit probes when AUTO_PROBES=true**. Enforce rejects
  busy/pending/unresolved/stale/errors/missing/incomplete required evidence. Claims
  and evidence writes share SQLite BEGIN IMMEDIATE; reservation survives restart
  as ambiguity, no re-push. Account isolation holds. Manual/provider submissions
  and separate databases remain outside that local transaction/lock.

## No-production-restart pilot route

**Recommended Phase 7 route: isolated monitor + discovery Compose project**, using
the reviewed monitor image for ingestion/dashboard with AUTO_PROBES=false. This
requires building the new full monitor image for the isolated instance, but does
not require deploying it over or restarting the production collector.

Master/operator should prepare a dedicated `compose.pilot.yaml` base (not the
production `compose.yaml`) with exactly these monitor properties:

```yaml
services:
  monitor:
    build: .
    restart: "no"
    environment:
      AUTO_PROBES: "false"
      PROBE_ADMISSION_POLICY: observe_only
      MONITOR_DB: /data/monitor.sqlite3
      ACCOUNTS_ENV: /run/secrets/accounts.env
      MONITOR_INGEST_TOKEN: ""
    ports: ["127.0.0.1:3001:3000"]
    volumes:
      - pilot-monitor-data:/data
      - ${PILOT_ACCOUNTS_ENV:?private thangma-only account file required}:/run/secrets/accounts.env:ro
volumes:
  pilot-monitor-data:
```

Merge that base with `compose.discovery.yaml`, project name
`kaggle-discovery-pilot`, profile `discovery`, explicitly enable DISCOVERY_ENABLED.
Use the approved host and existing private thangma auth directory; privately
provision the distinct discovery token first (0700 directory/0600 file, 1000:1000).
Current app startup requires a valid private **thangma API credential** file to
register that account; browser storage-state is not an API credential substitute.
If that file is unavailable, this unchanged-app sidecar route is blocked. Do not
use fake credentials or mount all-account production secrets to bypass it.

The fixed `http://monitor:3000` destination then resolves to the isolated project's
monitor on its own default network. Use fresh project-scoped volumes; do not set
external volumes, production volume names, shared production network aliases,
container names, production database paths, or production port 3000. Do not copy
the production database (it could contain active work that auto=false still polls).
Review merged config before starting: only thangma credentials/auth, AUTO_PROBES
false, observe_only, distinct DB/network/port and expected resource limits.
Measure sidecar monitor overhead as well as whole worker/container/host resources.

This route validates discovery -> ingestion -> **pilot** summary/dashboard,
expiry/failure/outbox/restart/rollback and repeated cycles without changing the
production collector. It does not validate the production collector's new polling
or persisted admission integration. Roll back by stopping/removing only that
project's containers; retain private state for diagnosis, never use a production
project down/volume removal. Stop the pilot worker before a later integrated
worker is started against the same auth directory.

### When a full production monitor deployment is required

Worker-only overlay startup cannot add a route/token mount/new admission code to
the existing running monitor. To put discovery into the **current production
dashboard/database**, master must schedule a reviewed full monitor image rebuild
and recreation with the overlay's dedicated token mount/env, explicit
PROBE_ADMISSION_POLICY=observe_only, and **AUTO_PROBES=false for the no-new-GPU
acceptance window**. Retain existing remote jobs; do not cancel or re-push them.
Use the existing 220s graceful shutdown, verified backup/recovery and rollback
plan; do not attach a second app to the live DB or hot-patch the running container.
Inspect actual configuration/health and restored ambiguous reservations before
continuing. Re-enabling automatic submissions is a separate master's decision.

No live route, image build, merged pilot config or credential availability was
verified here. The above is an operator handoff, not deployment evidence.

## Checks and blockers

- Backend: `uv run --python 3.12 --with-requirements monitor/requirements.txt python -m pytest monitor/tests -q`
  — **323 passed, 2 skipped**, 38.14s after the final regression.
- Frontend: `npm test` — **66 passed / 9 files**; `npm run build` — **passed**
  (TypeScript + Vite) in `monitor/frontend` (jsdom, no browser).
- `git diff --check` passed (existing line-ending notices only). Linux-only
  process-tree tests skip on Windows. Existing Starlette/AnyIO deprecation warning
  remains. No local Docker runtime/config or new Linux-worker verification.
- Phase 7 still needs actual new-image limits/ownership/init, real row parsing and
  auth refresh, repeated ingestion/UI cycles, CPU/throttling/RAM/PID/OOM/headroom
  and responsiveness measurements, natural expiry/revocation, crash/timeout,
  persistence and rollback. No full idle coverage or other-account acceptance.
- Keep #23/#24 open. Local acceptance is not live activation or Phase 7 acceptance.

## Files owned by this review

`discovery_visit.py`, `discovery_worker.py`, `tests/test_discovery.py`, this report.
Other existing edits preserved; no app/frontend/base Compose edits in Phase 6.
