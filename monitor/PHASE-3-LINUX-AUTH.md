# Phase 3 — thangma-only Linux authentication pilot

## Decision and acceptance

**2026-10-15: thangma-only Linux Docker authentication and container-restart
persistence passed on the user-approved existing host (31.21 GiB / 8 CPUs).**
The separate 4 GiB / 2 vCPU VM was not selected. Other accounts remain deferred
by user choice. This accepts bounded authentication feasibility, not exhaustive
discovery, unattended expiry recovery, production deployment or admission policy.

The initial process lacked the master's authorization and one `sudo -n` check
returned `sudo_password_required`. The master then explicitly supplied session
authorization. Password-fed sudo through the existing key/known-host-verified
Paramiko route succeeded. That initial blocker is resolved.

## Private transfer and dependencies

- Source identity reverified at **14:53:22 UTC**: `GetCurrentUser` HTTP 200,
  username **thangma**, plus account-menu Your Profile **`/thangma`**.
- Only `kaggle.com`/`.kaggle.com`/`www.kaggle.com` cookies and the
  `https://www.kaggle.com` storage origin were transferred. No Google state,
  original profile, credential database or full profile copy was used.
- This MCP runtime does not expose Node `require`, `process`, or dynamic import;
  the proposed child-process bridge could not run there. A temporary Windows
  **127.0.0.1-only**, serial HTTP bridge passed state directly from MCP request
  memory to verified SSH/SFTP. No access logs, state response or local auth file.
  The bridge process (PID 37548) was stopped after transfer/initial commands.
- SFTP wrote an exclusive temporary file, chmod 0600 before state writing, then
  atomically renamed it. Host directories verified **0700**, file **0600**, all
  owned by runtime **1000:1000**. Linux refresh also used an exclusive 0600
  temporary file and atomic replacement. No auth state values were emitted.
- Inventory: three existing application images, no Playwright image; host Chrome
  **154.0.8037.97**. Chose the supported bundled-browser image rather than bind
  mounting host Chrome and its desktop dependencies into the worker.
- Image: `mcr.microsoft.com/playwright/python:v1.55.0-noble`, measured **965,578,236
  bytes**, runtime pinned to digest
  `sha256:640d578aae63cfb632461d1b0aecb01414e4e020864ac3dd45a868dc0eff3078`.
  Pull was bounded at 180s. The MCP request timed out awaiting it, but subsequent
  image inspection independently verified successful completion; no duplicate pull.
- Pinned Python packages: **playwright==1.55.0, pyee==13.0.0,
  greenlet==3.2.4**, binary-only, no cache, zero retries, 15s network timeout and
  a 45s installation cap. No host package installation or production image edits.
  A separate empty-control container's install exited 70 before any browser ran;
  reused the successful pilot's pinned dependencies read-only for both controls.

## Enforced isolation and bounds

Each container had Docker Memory=**1,073,741,824**, MemorySwap=**1,073,741,824**
(no extra swap), NanoCpus=**750,000,000**, PidsLimit=**128**, /dev/shm=128 MiB,
UID/GID 1000:1000, all capabilities dropped, and no-new-privileges. No published
ports or Docker socket mount. Authentication mount was scoped to thangma; controls
mounted it read-only. Each run created one headless Chromium and closed it before
container exit. There was never more than one pilot browser concurrently.

Kernel cgroups independently reported `memory.max=1073741824` and
`cpu.max=75000 100000`. Visit coroutine deadline 55s; outer process-tree deadline
60s, including browser launch. Browser launch timeout 20s. Package setup has its
separate bounded 45s cap and is excluded from visit elapsed measurements.

## Actual Linux results

| Run / UTC start | Identity / event evidence | Visit elapsed | Cgroup peak memory | Visit CPU usage delta |
| --- | --- | ---: | ---: | ---: |
| Initial container, 14:54:30 | HTTP 200 thangma + /thangma; footer 0 Active Events, no rows after short wait; refreshed state saved | 12.702s | 580,841,472 B (553.9 MiB) | 8.029 CPU-s |
| Same container restarted, 14:55:35 | Reloaded Linux-refreshed disk state; HTTP 200 thangma + /thangma; Successful #144 and #143, two view links, 0 Active Events; refreshed state saved | 15.594s | 439,951,360 B (419.6 MiB) | 8.306 CPU-s |
| Fresh empty-state control, 14:57:29 | HTTP 200 with no thangma identity; explicit `authentication_required`, coverage none | 4.896s | 211,607,552 B (201.8 MiB) | 3.398 CPU-s |
| Fresh deliberately invalid-state control, 14:58:41 | In-memory cookie values replaced with a fixed invalid marker; HTTP 200 with no thangma identity, Login visible; explicit `authentication_required`, coverage none | 6.210s | 307,990,528 B (293.7 MiB) | 4.410 CPU-s |

All browser runs exited **0**, Docker **OOMKilled=false**, PID=0 after exit;
cgroup `memory.events` max/oom/oom_kill counters all zero. CPU throttling was
observed, consistent with the 0.75 CPU cap. CPU deltas are whole-container cgroup
usage sampled around the visit, not renderer JS metrics or host CPU percentages.
First-run memory peak also includes earlier package setup in the same cgroup.
A setup-time docker-stats snapshot showed 75.48% CPU, 55.25 MiB, 9 PIDs;
it is not a browser peak or peak process count.

The source Windows panel independently showed #144/#143 at **14:55:12 UTC**.
Increasing panel settle time from 2s to 5s on Linux restart yielded those same
rows. This demonstrates why an early empty panel must not certify idle.
No scrollable overflow existed in the successful Linux sample; long-list
scrolling/pagination/lazy loading remain unverified. All authenticated samples
retain **coverage=partial**. Linux script-version destinations/native session IDs
were not resolved; no GPU hardware verification or observed-idle claim.

The restart stopped the initial browser/container completely, then started the
same container and a fresh browser using the state saved by Linux, without a new
Windows transfer. This verifies disk + browser/container restart, not host reboot,
new-container authenticated reuse, natural expiration, revocation, refresh-token
lifetime or Google MFA/recovery. Invalid control did not alter retained state.
Deadline/crash enforcement is configured, not fault-injection tested here.

## Artifacts, cleanup and host health

**Retained private authentication artifact (contents never reported):**
`/home/thangma/.local/share/kaggle-monitor-auth/thangma/storage-state.json`
(0600, 1000:1000), under verified 0700 directories. Retained for the explicitly
authorized next thangma pilot; no backups/local copies were made. Remove through
the authorized operator if the pilot is abandoned; no automatic retention policy
has yet been accepted.

Removed the three pilot/control containers (`kaggle-auth-phase3-thangma`,
`kaggle-auth-phase3-empty`, `kaggle-auth-phase3-invalid`), temporary host dependency
tree `/home/thangma/.local/share/kaggle-monitor-auth/phase3-deps`, and uploaded
`thangma/feasibility_linux_pilot.py`. The failed install container had already been
removed before recreation. Pinned image remains cached (~921 MiB), no running
pilot service. Temporary process environment was cleared in `finally`; no password
was saved to helper/source/config files. The local bridge listener was stopped.

Post-cleanup monitor `/healthz`: **HTTP 200 in 3.73ms**, ok/worker_alive/sdk_ready
true, auto_probes true (existing setting, not changed). MemAvailable
**26,347,152 KiB (~25.13 GiB)**. Existing monitor and dashboard remained healthy;
VM manager remained running. No existing services restarted/reconfigured. These
are post-pilot snapshots, not sustained-load/dashboard latency acceptance.

## Ownership and next-phase handoff

Owned files: this report, `feasibility_linux_access.py`,
`feasibility_linux_bridge.py`, `feasibility_linux_pilot.py`, and the short current
status pointer in `ACCOUNT-AUTH-FEASIBILITY.md`. Reviewed phase docs, handoff,
remote workflow, live #20/#22 comments and git status. Meaningful checks were the
actual Linux visits, restart, negative controls, cgroup/permission inspection and
post-cleanup health check. No application/frontend/Compose edits or policy change.

**Phase 3 thangma feasibility accepted; #20 remains open for deferred accounts and
auth lifecycle work; #22 remains open for bounded worker/discovery implementation.**
Phase 5 must handle asynchronous panel population, retain partial coverage,
deadlines/backoff, per-account persistence and sanitized auth-required errors.
Phase 7 still requires repeated-cycle, fault, ingestion and responsiveness QA.
No production GPU launch/cancel, enforce activation, commit or push.
