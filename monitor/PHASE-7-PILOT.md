# Phase 7 — isolated thangma observe-only pilot (#24 / #22)

> **Current status:** the bounded thangma restart/ingestion/healthy-TTL pilot was
> subsequently requalified successfully. See `PHASE-7-REQUALIFICATION.md` for the
> exact rebuilt images, measured results and final stopped state. The report below
> preserves the initial failed acceptance evidence; its UI blocker and missing
> qualified monitor image are superseded by that continuation.

## Outcome

**Executed; partial integration/resource evidence obtained; live acceptance BLOCKED.**
2026-10-15 UTC on approved existing `thangma-Z97M-D3H` / `100.92.238.77`.
Both pilot containers are intentionally **stopped**, PID 0, no running pilot or
control containers, loopback 3001 closed. #24 and #22 remain open.

Three scheduled visits successfully authenticated, resolved real version identities,
refreshed private auth and ingested partial observations. Initial href-less provider
links required a narrow adapter fix. One subsequent panel consistency failure and
repeated post-restart profile-button timeouts prevent unattended/restart acceptance.
No new production deployment, service restart, GPU submission/cancellation,
enforcement activation, commit or push was performed.

## Configuration and credential boundary

Read Phase 6 readiness, Phase 5 and Phase 3, current production/discovery Compose,
Dockerfiles, application environment handling, requirements and frontend build config.
Used dedicated base `compose.pilot.yaml` + `compose.discovery.yaml`, **never** the
production base. Project/network: `kaggle-discovery-pilot` /
`kaggle-discovery-pilot_default`; fresh project-scoped DB and worker volumes.
Pilot monitor published only `127.0.0.1:3001:3000`, AUTO_PROBES=false,
PROBE_ADMISSION_POLICY=observe_only, empty production ingest token.

The new monitor image's existing `parse_accounts(dotenv_values(...,
interpolate=False))` validated production credentials privately in a network-none
provisioning container. Selected exactly thangma; revalidated the one-account
serialization and resulting file. Production secrets were not mounted into the
pilot services. Created a separate random discovery-only token. Credential values
were never emitted, copied locally, embedded in images or issue comments. Existing
key/known-host-verified `remote.py` supplied sudo through private stdin; temporary
process environment was cleared in finally. No password is in pilot source/config.

Actual worker HostConfig and cgroup verification: 1,073,741,824 B memory and total
memory+swap, memory.swap.max=0, cpu.max=75000 100000 / NanoCpus=750000000, 128 PIDs,
128 MiB shm, 192 MiB tmpfs, init, UID:GID 1000:1000, read-only root, ALL caps dropped,
no-new-privileges, restart=no, no worker ports/Docker socket. Monitor was separately
bounded to 512 MiB/no extra swap, 0.75 CPU and 128 PIDs with init; it uses the existing
root/writable-root application image. No production DB or network was shared.

## Measured resource matrix

144 host-loopback samples, mostly 10s cadence in bounded observation windows;
not continuous measurement over the later harness interruption. Peaks are cgroup
lifetime high-water marks, not renderer-only memory; shm/tmpfs are charged within
the worker limit. Sample/exec measurement overhead is included.

| Component | Actual peak RAM | PIDs high-water | CPU evidence | OOM |
| --- | ---: | ---: | --- | --- |
| Original worker, three rejected parsing cycles | 497.94 MiB | 97 | Per-visit duration 19.228 / 13.644 / 13.373s | all memory.events OOM counters 0 |
| Fixed worker, scheduled cycles | **521.97 MiB** | **95** | Successful visit deltas 12.537 / 14.505 / 14.701 CPU-s | counters 0; OOMKilled=false |
| Pilot monitor | **101.52 MiB** | **12** | Startup/restart throttling observed; final post-restart sampled cumulative CPU 245.170s, throttled 1.429s, including hours without browsers | counters 0; OOMKilled=false |
| Serialized fault container | 206.59 MiB | 77 | Final cumulative 7.670 CPU-s | counters 0 |

Fixed-worker successful visits took **24.057 / 27.033 / 27.311s**. Cumulative
throttled-time deltas were 38.987 / 42.532 / 45.460s respectively; cgroup throttled
time can exceed wall duration across runnable threads, and is not CPU usage.
After visits worker metrics reported pids.current=2 and ~15.6–16.2 MiB memory,
with no accumulating browser tree. Measurement exec can temporarily add processes.

Host: 31.21 GiB / 8 logical CPUs. Baseline MemAvailable **25.19 GiB**, lowest sampled
**24.61 GiB**, final **25.19 GiB**. Swap remained unused. Sum of independently
observed fixed worker + monitor peaks is ~623.5 MiB, a conservative envelope,
not a simultaneous sampled peak. These results concern this host, not the separate VM.

| Endpoint | n | p50 | p95 | max | Failures |
| --- | ---: | ---: | ---: | ---: | ---: |
| Production :3000 /healthz | 144 | 1.68ms | 1.93ms | 3.77ms | 0 |
| Production :3000 /api/summary | 144 | 60.14ms | 87.72ms | 109.44ms | 0 |
| Production :3000 dashboard HTML | 144 | 2.54ms | 2.91ms | 3.70ms | 0 |
| Pilot :3001 /healthz | 144 | 1.86ms | 2.08ms | 2.88ms | 0 |
| Pilot :3001 /api/summary | 144 | 9.82ms | 11.16ms | 14.63ms | 0 |
| Pilot :3001 dashboard HTML | 144 | 2.26ms | 3.55ms | 5.33ms | 0 |

Real browser dashboard QA used a temporary localhost-only GET proxy over verified
SSH while the server discovery worker was stopped. Overview/Operations rendered,
one thangma account, partial/stale retained evidence, unknown status and automatic
probes **Tắt** (off); console had zero errors/warnings. Browser summary fetch 158ms
includes the proxy/SSH path. This was not a browser-rendering load test concurrent
with discovery; concurrent responsiveness evidence above is HTTP measurement.
The dashboard tab and local proxy PID 18160 were closed/stopped afterward.

## Evidence / resilience matrix

| Check | Actual result / boundary |
| --- | --- |
| Initial three scheduled cycles | All ingested coverage=none / invalid_event_identity; safe unknown, no invented idle |
| Href-less row diagnosis | Status role-links have empty href and no ancestor anchor; terminal status navigation opened a read-only version popup with strict Kaggle owner/ref/scriptVersionId |
| Defect fix | `resolve_panel_link` rechecks sampled row before click, reads popup URL, closes popup, caches resolved destinations; strict event validation unchanged |
| First successful fixed cycle, 15:57:42 UTC | thangma current-user + independent /thangma profile; versions 146/145, IDs 358726563/358721753; partial ingestion |
| Next cycle | panel_unavailable, 26.080s / 13.715 CPU-s; retained last-success observations; backoff |
| Recovery, 16:05:37 UTC | partial ingestion, auth refresh, 27.033s |
| Third success, 16:09:28 UTC | partial ingestion; versions 147/146/145, additional ID 358734367; 27.311s |
| Monitor restart | Exact observations persisted across pilot monitor restart; AUTO_PROBES remained false; production untouched |
| Worker restart / fresh-container reuse | Durable schedule/last success persisted, but scheduled visit failed in 9.132s; fresh one-shot also failed in 8.933s. Diagnostic verified thangma username, then Playwright timeout at exact `Thang Ma` profile button. **Usable post-restart discovery NOT accepted** |
| Evidence TTL | Prior fresh observations naturally aged past 300s; stale rows retained, account unknown, would_defer includes required_source_stale. No clock/DB timestamp manipulation. Explicit expiry action found them already stale; not a clean isolated healthy-source TTL transition test |
| Hung browser supervisor | Real Chromium launched, test leader slept; outer 12s test bound killed/reaped tree in 7.086s; visit_timeout, zero descendants |
| Crashed browser leader | Real Chromium launched, leader exited 17; supervisor cleaned orphan tree in 0.985s; worker_crash, zero descendants |
| Detached child / early leader exit | Linux stdlib tests in built bounded worker image: 2 passed in 1.399s; actual fork/setsid descendants killed/reaped |
| Empty / invalid auth controls | Separate private temporary synthetic states, original auth never modified; live visits 4.107 / 4.400s, authentication_required / coverage none; zero descendants |
| Failed ingestion and suspension | Real invalid-token HTTP rejection -> durable private outbox -> disk reload -> suspended retry with real token accepted; only one visit, no second browser, outbox removed, suspension retained |
| OOM / real auth expiry / provider revocation / captcha | **NOT TESTED**; zero observed OOM is not OOM recovery acceptance; invalid auth controls are not natural expiry/revocation |
| Provider hung page / long lists / host reboot / other accounts | **NOT TESTED**; supervisor sleep is not a hung Kaggle-page reproduction |

Terminal version rows are script-version identities, not native sessions or
GPU/hardware evidence. Existing production can independently progress while left
unchanged; observing version 147 does not mean this pilot submitted it.

## Final live state, retained artifacts and rollback

Harness usage interruption left the discovery worker stopped, monitor running with
AUTO_PROBES=false for approximately five further hours. On resumption, rechecked
workspace and actual containers before acting; no duplicates created. Final offline
pilot DB check: integrity_check=ok, **zero submit_begin attempts, zero calls,
zero production_events**, one activity-evidence row, 12 skipped probe slots and one
future scheduled slot. Scheduled slots are disabled opportunities, not remote jobs.
DB retains the explicitly labelled negative-auth control as latest source attempt
and prior three successful version observations as stale diagnostic history.

Both pilot containers now exited/PID 0/OOMKilled=false; 3001 closed. All control
containers were --rm. Production collector and both other existing containers have
the **same IDs, image IDs, StartedAt and RestartCount as baseline**. Production
health final HTTP 200 in 4.16ms. Production auto-probe setting was not changed.

Retained deliberately:

- Existing private auth: `/home/thangma/.local/share/kaggle-monitor-auth/thangma/`
  (0700), `storage-state.json` (0600), 1000:1000. Final refresh mtime corresponds
  to successful 16:09:28 UTC cycle. No authentication values reported.
- Minimal API file: `/home/thangma/.local/share/kaggle-monitor-phase7/accounts.env`,
  root:root 0600, parent 0700; token subdirectory 0700 and file `token/token` 0600,
  both 1000:1000. Retained only for explicitly authorized recovery/retest.
- `kaggle-discovery-pilot_discovery-data` (private durable schedule/state) and
  `kaggle-discovery-pilot_pilot-monitor-data` (private diagnostic SQLite).
  Final DB directory chmod 0700 and SQLite files 0600. No production DB copy.
- Worker image `kaggle-phase7-discovery:run-20261015`:
  `sha256:d91374da2429c06e1666adcab739f9f4ca59d16cc3caa74dad3b2ad854b9b9e7`.
- Exercised monitor image ID:
  `sha256:17a5550a9307d7c0f13424e28162ca26f5e1791c9a2c4d32b017f0e0c1b26823`.
  **That image is no longer available in Docker's image cache at final inspection.**
  Container is retained stopped. Separately rebuilt `kaggle-phase7-monitor:local`
  `6c49229d7b71` remains cached but was not the running monitor; do not equate them.
  A reviewed monitor rebuild/requalification is needed for later recreation.
- Digest-pinned monitor bases in `Dockerfile.pilot`: node
  `c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392`, python
  `a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`.
  Worker uses the Phase 3 Playwright digest and exact pinned Python packages.
- Sanitized server evidence in `/home/thangma/kaggle-discovery-phase7/`:
  `baseline.json`, `merged-config.json`, `measurements.jsonl`, `report.json`,
  `pre-fix-worker-state.json`, `live-once.json`, `expiry.json`, `faults.json`,
  `final-state.json`, `audit.json`, build log and allowlisted source.
  Source tar and exact malformed-path artifacts from the initial Windows upload
  attempt were removed. These files contain no credential values.

Rollback already effected by stopping **only** this project's services. To remove
its stopped containers/network later, use only this explicit project and these
two Compose files, with the private path variables set; `down` **without -v**
preserves diagnostic volumes. Never use production compose.yaml/project or remove
shared/private auth. Starting either worker again needs explicit recovery review;
do not clear suspension or launch concurrent workers sharing this auth directory.

## Files, checks and remaining gates

Added: `compose.pilot.yaml`, `Dockerfile.pilot`, `phase7_operator.py`,
`phase7_diagnostic.py`, `phase7_faults.py`, `phase7_dashboard_proxy.py`, this report.
Necessary adapter fix: `discovery_visit.py`; regressions: `tests/test_discovery.py`
(distinct href-less rows, conflicting destination rejection, reordered-row no-click,
popup close on resolution failure). Existing app/frontend/other user work preserved.

- Final backend: **327 passed, 2 Linux-only skipped**, 30.00s; existing dependency
  deprecation warning. Built-image Linux tree tests independently passed above.
- Frontend: **66 passed / 9 files**; pinned-image frontend TypeScript/Vite build
  succeeded during actual monitor image build. No frontend change for this phase.
- `git diff --check`: passed, existing CRLF notices only.

Remaining gates: repair and repeatedly qualify independent profile UI detection
after browser/container restart without weakening identity checks; diagnose panel
instability; requalify rebuilt monitor image; clean healthy-source TTL transition;
real auth expiry/revocation/recovery and unattended resilience; OOM/captcha/hung-page
and host reboot; long-list/native-session/full discovery coverage; deferred accounts;
integrated production collector/polling qualification. **Partial discovery cannot
satisfy full-coverage idle admission. Enforcement remains deferred.**
