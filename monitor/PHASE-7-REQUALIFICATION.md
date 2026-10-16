# Phase 7 continuation — thangma restart requalification (#24 / #22)

2026-10-16 local / **2026-10-15 UTC**, approved existing host `100.92.238.77`.

## Decision

**ACCEPTED for this bounded thangma-only isolated observe-only pilot:** new-container
auth reuse, two monitor/worker restart cycles with actual newer ingestion, resource
bounds, healthy-source natural evidence TTL transition, safe fault cleanup and
rollback. The initial profile-button blocker and missing qualified monitor image
are resolved. This does not accept unattended auth lifecycle, full discovery,
other accounts, production integration or enforcement. Keep #24/#22 open for their
broader outstanding gates.

## Diagnosis, fix and identity boundary

Before acting, verified both retained pilot containers stopped, 3001 closed,
production metadata unchanged and private-file ownership/modes intact. Reviewed
retained schedule/evidence privately. Prior non-secret JSON/log evidence and the
worker checkpoint were archived under server `initial-pilot/`; no auth copy made.

Live rendered-DOM diagnostics showed **zero navigation buttons immediately after
GetCurrentUser**, then the hydrated navigation included the account button and
the independent Your Profile link after opening its menu. GetCurrentUser exposes
`userName` and `displayName`. The earlier 5s navigation timeout was too short for
asynchronous hydration under the 0.75-CPU cap; the new code waits at most 12s for
that accessible button, within unchanged 50s coroutine / 60s outer visit bounds.

`identity_hint` requires every exact username field to equal `thangma`; missing or
mixed/wrong usernames fail. A unique nonempty display name is only a button-locating
hint and may change. `verify_profile` independently checks the **actual href of
the uniquely named Your Profile link is exactly `/thangma`**. It does not locate an
expected-href link and ignore a different account. Wrong/missing profile proof
still fails. Playwright timeouts are now classified as visit_timeout.
Strict script-version identity validation and partial-only coverage are unchanged.

Meaningful regression evidence:

- Real Chromium, new built image, **network none**, same 1 GiB/0.75 CPU/PID/shm/tmpfs
  limits: renamed account button deliberately appeared after 6.5s; verification
  completed in **7.088s**, exceeding the old 5s allowance. Wrong profile href was
  then rejected. No provider or GPU work in this regression.
- Backend tests reject a wrong/mixed/missing username despite a matching display
  name and reject `/other` despite a valid username/button hint.

## Exact images and isolation requalified

Both services were rebuilt, recreated and runtime image IDs asserted equal to the
build manifest; the cached-but-unqualified old monitor was not reused:

| Service | Qualified image ID / retained tag |
| --- | --- |
| Monitor | `sha256:605a6fcf15bc88f86e789188e76fe2e894ea0cc0b11f135abb9bbc84db591c95` · `kaggle-phase7-monitor:requalification-20261016` |
| Worker | `sha256:bcca782bb1a5b5ad2417962c7f1faf49bef88ff95f804fd18ee47f3458e0b7f8` · `kaggle-phase7-discovery:requalification-20261016` (also worker alias) |

Digest-pinned base images, exact Python requirements and npm lockfile remain as
recorded in the original pilot report. Existing project/network/volumes reused;
no duplicate browser/container. Reviewed merged base+overlay, thangma-only file
revalidated using the exact new monitor's parser in a network-none container.
AUTO_PROBES=false, observe_only, separate token, private loopback3001 DB/dashboard.

Actual worker Docker/cgroup limits remained: **1,073,741,824 B RAM and total swap
budget, memory.swap.max=0, cpu.max=75000 100000, 128 PIDs, 128 MiB shm, 192 MiB
tmpfs**, UID/GID1000, init, read-only root, ALL caps dropped/no-new-privileges,
restart=no. Monitor: **512 MiB/no extra swap, 0.75 CPU, 128 PIDs/init**.
No production service/config/DB/network was changed.

## Actual fresh-cycle and resource matrix

| UTC successful observation | Lifecycle | Wall time | Visit CPU delta | Cgroup peak RAM | Peak PIDs | Ingestion |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 21:29:59.037546 | New worker+monitor containers, retained disk auth | 25.897s | 13.493207 CPU-s | 499.176 MiB | 97 | partial, accepted |
| 21:34:32.796497 | After first full monitor+worker restart | 27.119s | 14.181726 CPU-s | 490.770 MiB | 99 | newer partial, accepted |
| 21:39:31.555899 | After second full monitor+worker restart | 26.760s | 13.974071 CPU-s | **532.730 MiB** | 95 | newer partial, accepted |
| 21:46:36.386363 | Separate fresh container after fault controls | 27.671s | 14.504412 CPU-s | 489.645 MiB | 91 | partial, accepted; zero descendants |

Three scheduled visits / **three successes, zero browser/parse/ingestion failures**
in this requalification. Both restart cycles independently preserved previous
observations before accepting newer ones. Disk auth refresh occurred after success;
no Windows retransfer or schedule checkpoint reset. Saved normal completion-relative
180–300s cadence remained intact. Latest retained checkpoint next_at and last_success
belong to scheduled visits; the separate final restore updates API evidence only.

Observed versions 156/157/158 at `thangma/queue-monitor-probe`, strict script-version
IDs `358806953` / `358810051` / `358812903`; terminal Successful labels. These are
provider script versions, **not native sessions or GPU verification**. Production
can independently progress while unchanged; observing a newer version is not a
pilot submission.

Visit throttled-time deltas: **40.589213 / 44.696698 / 41.447529s** (scheduled),
42.535085s (separate final visit). Throttle time may exceed wall time across threads;
it is not CPU usage. Peaks are whole-cgroup high-water marks, including shm/tmpfs.
Worker returned to ~15–19 MiB / 2 PIDs after visits; a concurrent health/metrics exec
can briefly add one PID. No tree accumulation observed.

Monitor peak **106.230 MiB / 12 PIDs**. Sampled CPU sums across its three cgroup
lifetimes: **23.887425 CPU-s** (not exact shutdown totals). Report now groups cgroup
counters by StartedAt to avoid treating restart resets as one lifetime. Fault image
peak **212.160 MiB / 76 PIDs**, final cumulative 8.094770 CPU-s. All measured OOM
counters zero; both stopped containers OOMKilled=false. **No OOM fault was induced.**

Host MemAvailable: **25.218 GiB baseline**, **24.580 GiB minimum sampled**,
**25.165 GiB final**, swap unused. Independently observed worker+monitor peak sum
~638.96 MiB is an envelope, not a simultaneous peak. No separate-VM claim.

### Responsiveness — 141 samples per endpoint

| Endpoint | p50 | p95 | max | Failures |
| --- | ---: | ---: | ---: | ---: |
| Production /healthz | 1.68ms | 1.91ms | 3.31ms | 0 |
| Production /api/summary | 53.99ms | 82.58ms | 107.69ms | 0 |
| Production dashboard HTML | 2.46ms | 2.73ms | 3.41ms | 0 |
| Pilot /healthz | 1.84ms | 2.08ms | 20.01ms | 0 |
| Pilot /api/summary | 10.51ms | 12.98ms | 16.11ms | 0 |
| Pilot dashboard HTML | 2.26ms | 4.53ms | 6.77ms | 0 |

Sampling ~5s during restart waits, 10s during TTL wait; observers included in cgroup
cost. Not a sustained concurrency benchmark. Actual new-monitor browser dashboard
rendered fresh partial account evidence after the final restore, AUTO_PROBES=false,
observe_only, no JS console errors/warnings. Proxy/SSH fetch 46ms, DOMContentLoaded
330ms. Browser QA occurred with server discovery stopped; concurrent browser-load
responsiveness is HTTP evidence above. Local proxy PID19748 and browser closed.

## Healthy TTL and safe fault matrix

| Case | Actual evidence |
| --- | --- |
| Healthy-source TTL | Stopped discovery after fresh 21:39:31 success; waited naturally with fixed300s TTL. Last sampled fresh at age292s; stale afterward. Source stayed partial with error/auth_error null, same observations/attempt timestamp retained. Account became **stale**, admission would_defer includes required_source_stale. No shortened TTL, clock manipulation or DB evidence edits |
| TTL tooling correction | Initial operator assertion expected unknown; application contract correctly returns stale for healthy expiry. Corrected tooling and validated saved before/after samples. This was not an application failure or a second synthetic expiry test |
| Hung browser supervision | Real Chromium started, leader slept; killed/reaped at 7.085s inside12s test deadline; zero descendants |
| Crashed leader | Real Chromium started, leader exited17; tree reaped in1.053s; zero descendants |
| Detached/setsid orphan cleanup | Built-image Linux tests: 2 passed in1.399s |
| Empty/invalid private auth | Fresh temporary synthetic states, live visits4.586/4.303s -> authentication_required, coverage none, zero descendants. Retained real auth untouched |
| Ingestion failure/outbox | Real wrong-token rejection, durable checkpoint+reload, suspended retry accepted with correct token, one visit only, no second browser, outbox cleared |
| Post-fault recovery | Separate real authenticated container successfully refreshed/ingested at21:46:36; final API source no longer contains the synthetic auth-control error |

## Final state / retention / rollback

**Both isolated services stopped/PID0; no running pilot/control container; 3001
closed.** All temporary controls --rm. Production collector and both other baseline
containers retained exact IDs/image IDs/StartedAt/RestartCount; final production
health HTTP200 in4.28ms. No production restart/GPU launch/cancel/enforce/commit/push.

Offline pilot DB integrity_check=ok, **zero submit attempts, zero calls, zero
production_events**, one activity row; 13 skipped opportunities + one future
scheduled opportunity, all jobs disabled. Final successful observations retained
for diagnosis and naturally age while services are stopped.

Retained private state/permissions unchanged from original report:
`/home/thangma/.local/share/kaggle-monitor-auth/thangma/storage-state.json`
(0600, parent0700,1000:1000), minimal accounts.env (root0600/private parent0700),
separate token (1000:1000,0600/0700), both project-scoped volumes. DB directory0700,
SQLite files0600. Exact qualified monitor/worker images **both verified cached and
tagged** at shutdown. Prior initial-worker image also retained. No credential values
in repo, logs, issues or images; temporary sudo process environment cleared finally.

Server evidence: `/home/thangma/kaggle-discovery-phase7/` with current baseline,
merged-config, image-manifest, measurements.jsonl, report.json, fresh-successes.jsonl,
expiry.json, faults.json, live-once.json, final-state.json and audit.json; original
report artifacts in `initial-pilot/`. Source tar removed on finish. Rollback already
completed by stopping only this project; later scoped `down` **without -v** retains
diagnostic volumes. Never use production Compose/project or remove shared auth.

## Files, checks and honest remaining gaps

Changed this continuation: `discovery_visit.py`, `tests/test_discovery.py`,
`phase7_operator.py`, `phase7_diagnostic.py`; added
`tests/phase7_identity_browser.py`, this report; historical report gets current-status
pointer. Other existing application/frontend/user edits preserved.

- Backend **329 passed, 2 Linux-only skipped**,28.71s; existing dependency warning.
- Real built-image delayed-DOM/wrong-profile regression passed; Linux cleanup2passed.
- Frontend **66 passed / 9 files**; exact monitor image npm/TypeScript/Vite build
  succeeded. No frontend change required.
- git diff --check passed (existing line-ending notices).

Still **unknown/unaccepted**: natural auth expiry, provider revocation, MFA/captcha
recovery, destructive OOM recovery, truly hung provider-page reproduction, host
reboot, long unattended resilience, long lists/pagination/native-session coverage,
other accounts, and integrated production collector/polling deployment. The earlier
panel inconsistency did not recur across this short run; broad provider/UI stability
is not proven. Discovery remains partial and cannot authorize full-coverage idle
admission. **No enforcement recommendation.**
