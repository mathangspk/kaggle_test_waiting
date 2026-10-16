# Phase 8 — production observe-only integration (#18 / #22 / #24)

2026-10-16 local / 2026-10-15 UTC. Sole live operator; separate coverage audit
remained local/read-only for server/browser operations.

## Outcome and acceptance boundary

**Production collector deployment, database migration/history retention, private
ingestion, actual frontend and rollback preparation verified.** Continuous thangma
discovery is installed and running. **Successful positive production browser
discovery is blocked by the currently visible unsupported interactive-editor row.**
The new API has accepted real authenticated failure snapshots; no synthetic success,
pilot evidence import, full coverage, idle certification or successful production
auth-lifecycle soak is claimed. Phase 9 observer is started, not elapsed/accepted.
Keep #18/#22/#24 open. `PHASE-10-COVERAGE-AUDIT.md` independently documents remaining
coverage/identity limitations and two reproduced xfailed cases; no enforce activation.

## Preflight and explicit automatic-probe decision

Reinspected workspace, Phase 6/7 evidence, actual existing host and private auth
metadata; isolated pilot initially stopped. Existing production collector:

- Image `9a5e06b8ae71e7c2bd65a4c326b46ddca6b6c8056faac2fad44b8dbba01864fe`;
  AUTO_PROBES=**true**, policy not explicitly configured, CALL_TIMEOUT90,
  SAMPLING_INTERVAL_MINUTES30, restart=unless-stopped.
- Original volume `kaggle-queue-monitor_monitor-data`, 640 runs / 3,709 calls,
  620 submit attempts, no locally active runs. Existing credentials register four
  accounts. Preserved those registrations and all history; **only browser
  discovery is thangma-only**. No other-account onboarding/browser performed.
- Baseline MemAvailable25.20GiB, swap unused, app health ready/SDK/config OK.

For the expressly no-new-GPU rollout and soak, deliberately set **AUTO_PROBES=false**
and **PROBE_ADMISSION_POLICY=observe_only**. Observe-only alone does not stop
submissions. This is a recorded operational pause of existing sampling, not a
claim of unchanged automatic-probe policy. Existing work remains pollable and was
not cancelled/re-pushed. While preflight/builds ran, the unchanged old automatic
collector completed one further previously scheduled probe: quiesced baseline
**621 submit attempts / 3,713 calls**. No test issued a launch. After deployment,
those counters stayed exactly621/3713; automatic opportunities become skipped.
Resuming launches is a separate explicit policy decision, not a soak completion step.

## Backups, graceful shutdown and history verification

Server root-private backups (parent0700/files0600; contents never emitted):

1. `/home/thangma/kaggle-production-phase8/backup-online-20261015T220326Z/`
2. `/home/thangma/kaggle-production-phase8/backup-quiesced-20261015T221444Z/`

Each includes SQLite backup API snapshot, integrity check, original Compose/.env/
Dockerfile where present, private original inspect, exact old-image export
`monitor-image.tar`, and manifest. Old image tagged
`kaggle-phase8-rollback:pre-integration` as well. Original configuration/env/inspect
backups can contain existing credentials and remain root-private; never publish them.

Used220s graceful `docker stop`, verified original PID no longer running, then took
the quiesced backup before any migration. Only one monitor accessed the live DB.
New image reused the **original** production volume, not a copied pilot database.

Verified after deployment:

| Gate | Result |
| --- | --- |
| SQLite integrity | ok |
| Prior run IDs | all640 retained |
| Prior call IDs | all3713 retained |
| Terminal historical content | all prior fields unchanged in621 terminal runs |
| Historical call content | every prior column unchanged in3713 calls |
| Production registrations | zero before/after; no invented attachment |
| Post-rollout submissions/calls | zero new; counters remain621/3713 |
| Locally active jobs | zero |

Offline rollback rehearsal: exact old image opened a **copy of the migrated DB**
with its old Store, network-none/AUTO_PROBES=false; integrity passed and640runs /
3713calls remained. It never attached to the live DB or started a collector/browser.
A live rollback was not exercised because the updated collector is healthy.

## Built and deployed exact images

Current sources were allowlist-uploaded, rebuilt with digest-pinned Phase7 bases,
exact Python dependencies/npm lockfile, backend-tested and frontend-built. New-image
network-none Chromium delayed-profile/wrong-href regression passed in7.028s. An
isolated live requalification failed conservatively on the newly present
interactive row; no positive parsing acceptance was inferred from Phase7 history.

| Service | Exact production image ID |
| --- | --- |
| monitor | `sha256:c45e0f866dab9ef3316bf6b89a8002a9503978d3fe841f848693ea265d33ce3a` |
| discovery | `sha256:a49929094f2ee3a95e19ae2dd5213b33df66b49f728a5ed6297c8d82722fe8c9` |

Runtime IDs asserted against manifest; app/contract/worker module hashes inside
containers matched uploaded tested source. No hot patch. Original unrelated
dashboard/VM-manager container IDs/images/start/restart metadata unchanged.

Canonical server deployment configuration:
`/home/thangma/kaggle-queue-monitor/compose.yaml`, copied from
`/home/thangma/kaggle-production-phase8/compose.production.yaml`.
Both contain image IDs, configuration and private mount paths/**secret references
only**, not credential values. Rollback manifest also uses private env interpolation.
Generated target-specific manifests are outside Git/workspace. The repository
contains generic tooling/docs, not server .env, inspect, credentials or DBs.

Production project/network: `kaggle-queue-monitor` /
`kaggle-queue-monitor_default`. Monitor bound only127.0.0.1:3000; existing route to
dashboard unchanged. Dedicated discovery token was mounted read-only separately
from production ingestion, existing production ingest token privately matched before
deployment. Production collector keeps its existing account credential mount;
worker mounts only thangma auth, token and its new project-scoped discovery volume.
Pilot services remain stopped, 3001 closed, no duplicate auth-directory user.

### Actual isolation/restart policy

- Worker: **1,073,741,824B RAM/total swap budget; swap.max0; cpu.max75000/100000;
  128PIDs;128MiB shm;192MiB tmpfs**;1000:1000, init/read-only root, cap-dropALL,
  no-new-privileges; no ports/Docker socket. Actual Docker/cgroups verified.
- Worker restart=**no**, intentionally preserves bounded-failure/no restart-storm
  semantics. It remains running between visits with persistent backoff. Auth failures
  suspend inside the durable checkpoint, never automatically reauthenticate. Crash/
  reboot recovery requires operator action; observer alerts but does not restart it.
- Monitor restart=**unless-stopped**,220s grace, init. Original production monitor
  CPU/RAM/PID limits were absent and remain absent; **1GiB/.75CPU is the browser
  worker budget**, not an invented bound on the collector or whole host.
- Docker unhealthy does not imply stopped/restarted. Current discovery health is
  **unhealthy due to no successful partial visit**, while ingestion/backoff run and
  collector health is **healthy**. This distinction is intentional and alerted.

## Real production ingestion and unsupported row

Live diagnostics independently verified thangma current-user and profile menu.
The panel also contained an existing running/cancelled interactive notebook whose
role-link opened an `/edit` destination with no scriptVersionId; sampled shared
ancestor text included other notebook-version labels. No launch/cancel control was
used; diagnosing an existing destination is not acceptance of interactive identity.
No further unsupported-editor navigation is attempted by the new guard: ambiguous
multi-version row text fails **before popup navigation**. Regression covers that.
The strict identity schema has not been weakened or broadened to invented sessions.

Observed production cycles:

| Attempt / result | Duration | Visit CPU delta | Ingestion |
| --- | ---: | ---: | --- |
|22:14:50UTC, panel_unavailable |15.022s |9.032641CPU-s |accepted; coverage none/auth_error null |
|22:18:45UTC, invalid_event_identity |25.562s |13.274650CPU-s |accepted; coverage none/auth_error null |

Second row shape was still not acceptable under strict script-version mapping;
backoff increased rather than repeatedly revisiting. Original source error was
replaced with newer failure-attempt metadata. No last-success timestamp or positive
rows were fabricated/copied from pilot. Frontend correctly shows thangma unknown,
browser_discovery none and error; admission would_defer for incomplete/unknown/error.

Actual production Overview and Operations rendered via temporary GET-only localhost
SSH proxy while the continuous production worker remained installed/running. Four
legacy accounts/history present, automatic probes **Tắt**, policy observe_only,
HTTP200/readiness true; no console errors/warnings. Browser summary305ms includes
proxy/SSH and full production payload. Browser/proxy PID24956 stopped afterward;
the production worker and monitor remain running.

## Immediate resources — preliminary, not sustained acceptance

Bounded soak checkpoint (10 samples/~534s,22:23:56UTC):

| Component | Peak RAM | Peak PIDs | CPU / throttling | OOM |
| --- | ---: | ---: | --- | --- |
| Production worker |473.215MiB |100 |23.811 sampled cumulative CPU-s /70.573 throttled-s |0 kills |
| New production monitor |103.008MiB |12 |12.350 sampled cumulative CPU-s /0 throttled |0 kills |
| Observer |~13.86MiB current |1 task |~0.592CPU-s at verification |128MiB hard limit |

Worker after cleanup ~15–16MiB/2PIDs. Host sampled minimum24.74GiB available;
later direct check25.13GiB. Swap unused. First10observer samples: summary
190.840ms p50/230.237ms max, health5.290ms p50/197.870ms max, HTML2.503ms p50/
96.636ms max. The observer itself is CPU-capped5%, so these timings include its
throttling and are not an unthrottled latency benchmark. No HTTP-unavailable alerts.
Current cumulative evidence is in the bounded soak files, not frozen to these10samples.

## Operations, rollback and evidence paths

Root-private retained operator:
`/home/thangma/kaggle-production-phase8/ops/phase8_operator.py` and companion modules.
Use existing verified-key `monitor/remote.py` with temporary process environment/
private sudo stdin; clear environment finally. Never put password/token values in
source, CLI arguments, docs or issue output.

Read-only checks through that route:

```text
python3 /home/thangma/kaggle-production-phase8/ops/phase8_operator.py evidence
python3 /home/thangma/kaggle-production-phase8/ops/phase8_operator.py soak-summary
python3 /home/thangma/kaggle-production-phase8/ops/phase8_operator.py verify-final
```

Configuration/image rollback (only when explicitly selected or failed deployment):

```text
python3 /home/thangma/kaggle-production-phase8/ops/phase8_operator.py rollback
```

It stops only production discovery, recreates the old monitor image with original
runtime settings/private env, retains the **current migrated DB** and updates
canonical Compose. **Original AUTO_PROBES=true is restored by this rollback**;
understand that this resumes original automatic sampling, not an observe-only test.
Additive-schema compatibility was rehearsed offline. No live rollback performed.

If database corruption requires backup restore, first stop only this project's
discovery then monitor with65/220s grace, preserve the damaged DB/WAL/SHM privately,
use SQLite backup API from verified quiesced backup into an offline temporary DB,
check integrity/ownership/modes, then atomically replace target after archiving its
WAL/SHM. Never copy a DB over a live process or delete history blindly. Restore loses
post-backup changes, so it is a separate operator recovery decision. Current DB is
healthy; this destructive path was **not executed**.

Retain backups/images/private auth/token/DB/checkpoints and operational evidence:
`baseline.json`, `deployment-manifest.json`, `history-verification.json`,
`rollback-rehearsal.json`, `final-verification.json`, `immediate-evidence.json`,
`soak-summary.json`, and `soak/` under the Phase8 directory. Private auth stays at
the Phase3/7 path,0700directory/0600file1000:1000; no credential export to repository.
Do not restart the isolated worker against the same directory.

## Files and checks

Added `phase8_operator.py`, `phase9_soak.py`, `tests/test_soak.py`, this report and
`PHASE-9-SOAK.md`. Extended existing allowlist uploader and GET proxy for production.
Narrow `discovery_visit.py` guard + meaningful ambiguous-row regression; no app
source changes by this operator. Other agent/user work preserved.

- Final backend **357 passed,2 Linux-only skipped,2 expected failures**,30.44s;
  xfails are separate coverage-audit defects, not waived new regressions.
- Frontend **66 passed/9files**, exact image frontend build passed.
- Built-image network-none delayed identity browser regression passed7.028s.
- Safe rollback Store rehearsal passed on migrated DB copy, no network/GPU.
- git diff --check passed with existing line-ending notices.

Immediate deployment acceptance is **not** positive discovery/coverage acceptance.
Remaining gates: unsupported interactive and audit identity/status defects, real
positive production activity evidence, long elapsed soak, natural auth expiry/
recovery, other-account auth, exact existing-version provider polling provenance
(no registered production jobs available here), host reboot/OOM/captcha/hung-page
recovery, exhaustive coverage. No commit/push, GPU test launch/cancel or enforcement.
