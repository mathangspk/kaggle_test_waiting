# Phase 2 — deployment target and authorized Docker evidence

Evidence collected **2026-10-15 14:08:04–14:09:04 UTC** for
[#22](https://github.com/mathangspk/kaggle_test_waiting/issues/22) and
[#24](https://github.com/mathangspk/kaggle_test_waiting/issues/24).

**Status: authorized sudo Docker access now verified through the existing remote
operator workflow. Phase 2 exit remains blocked on target selection. No deployment
performed. Earlier access-denied observations below are historical.**

## Authorized administration follow-up

The user supplied sudo authorization for this session. The existing
`monitor/remote.py` workflow successfully performed read-only `docker info`,
`docker ps`, `virsh list --all` and `virsh dominfo` checks. No password was saved
to this document or project configuration.

- Docker server 29.1.3, host `thangma-Z97M-D3H`: 8 CPUs / 31.21 GiB.
- Existing `kaggle-queue-monitor-monitor-1` runs on this host and is healthy.
- Separate running VM `Z97M-D3H-second-brain`: 2 vCPUs / 4 GiB allocated.
- This verifies host Docker access, not guest access or intended deployment target.
- No containers/services were created, restarted or modified.

## Exact observed target and the capacity discrepancy

| Item | Observed evidence |
| --- | --- |
| Existing deployment SSH endpoint | `100.92.238.77`, login `thangma`, UID/GID `1000:1000` |
| Host identity | `thangma-Z97M-D3H`; Ubuntu 22.04.5 LTS; Linux `6.8.0-138-generic`, x86_64 |
| Hardware/virtualization signals | DMI vendor Gigabyte, product/board `Z97M-D3H`; `systemd-detect-virt`, `--vm`, `--container` each returned `none` (exit 1) |
| Existing deployment directory | `/home/thangma/kaggle-queue-monitor`, owner `thangma:thangma`, mode `0775` |
| Existing monitor listener | Host `127.0.0.1:3000`; `/healthz` HTTP 200 in 3.7 ms, `ok=true`, `worker_alive=true`, `sdk_ready=true`, `auto_probes=true` |
| Host capacity | 8 online logical CPUs, affinity and effective cpuset `0-7`; MemTotal **32,723,684 KiB = 31.21 GiB** |
| Separate running VM | libvirt domain **`Z97M-D3H-second-brain`**, DHCP lease address **`192.168.122.76/24`** (lease evidence, not verified guest SSH identity) |
| VM allocation | Live domain XML `memory=currentMemory=4194304 KiB` (**4 GiB**), `vcpu=2`; live domstats `balloon.current=balloon.maximum=4194304`, `vcpu.current=vcpu.maximum=2` |

The configured deployment tooling reaches the larger host, **not the VM's lease
address**. A separate VM with exactly the planned capacity exists, but there is
no inspected requirement or deployment configuration tying that VM to this monitor.
Its name alone does not establish intended ownership or authorize moving work.
The 4 GiB / 2 CPU plan therefore remains an **unconfirmed target/budget**, not a
measured limit on the current monitor and not an allocation inferred from host RAM.
The operator must choose/confirm host versus VM. No guest SSH connection was made;
guest Docker access, guest workload baseline and guest available RAM are unverified.

## Cgroup and container-limit evidence

The earlier feasibility probe read only cgroup-root files. This pass resolved the
SSH session's actual `/proc/self/cgroup` path and inspected its ancestors:

- Unified cgroup v2 mounted at `/sys/fs/cgroup`, mount root `/`.
- SSH session `/user.slice/user-1000.slice/session-232.scope` and its user-slice
  ancestors have `memory.max=max`, `memory.high=max`, `memory.swap.max=max`.
- `user.slice` has `cpu.max=max 100000`, effective cpuset `0-7`.
  CPU control files absent at lower session levels are **not** a measured 2-CPU cap.
- `system.slice` has `memory.max=max`, `cpu.max=max 100000`, cpuset `0-7`.
- Three readable `system.slice/docker-*.scope` groups also have
  `memory.max=max`, `memory.high=max`, `cpu.max=max 100000`, cpuset `0-7`, and
  `pids.max=37992`. No 4 GiB / 2 CPU limit was found there.
- Remote Compose resource-key scan found no `mem_limit`, `cpus`, CPU quota,
  cpuset, or deploy/resources/limits/reservations keys. Its monitor service and
  loopback port match the local deployment structure. Full configuration and
  environment values were not printed.

Snapshot at 14:08:38 UTC (cgroup IDs abbreviated here):

| Docker cgroup prefix | memory.current | memory.peak | pids.current | Mapping |
| --- | ---: | ---: | ---: | --- |
| `e944d24a1c6a` | 8,130,560 B (7.75 MiB) | 12,259,328 B (11.69 MiB) | 9 | Service identity not queried through daemon |
| `8db13fac1c38` | 50,094,080 B (47.77 MiB) | 50,618,368 B (48.27 MiB) | 1 | Service identity not queried through daemon |
| `25683f577d30` | 50,827,264 B (48.47 MiB) | 120,045,568 B (114.48 MiB) | 4 | `/proc/163761/comm=uvicorn`; that PID's cgroup matches this scope; consistent with monitor, Compose-label identity still unverified |

All three scopes reported `memory.events` OOM/OOM-kill counters zero and
`cpu.stat` throttled periods/time zero. These are existing cgroup-lifetime
counters, **not** a browser-worker load test or restart acceptance. Docker
inspection of container names, Compose labels and HostConfig limits remains
blocked by daemon authorization.

The QEMU process PID 1645 belongs to the separate VM's `machine.slice` scope.
That scope has `memory.max=max`, `cpu.max=max 100000`, cpuset `0-7`; its measured
`memory.current` was 6,321,020,928 B. A host QEMU cgroup charge includes host-side
costs and is **not guest available memory**. The 4 GiB / 2 vCPU figures above come
from explicit libvirt allocation data, not QEMU RSS or this cgroup. Guest balloon
telemetry had `last-update=1791702346`, older than this audit; it is not a fresh
guest workload/headroom baseline.

## Measured host baseline (existing workloads left running)

| Metric | Measurement |
| --- | --- |
| MemAvailable, start/end | 26,455,548 / 26,472,144 KiB (**25.23 / 25.25 GiB**) |
| Swap total/free | 2,097,148 / 2,097,148 KiB; no swap used at snapshot |
| Load average, 1/5/15 min | `0.18 / 0.27 / 0.26` |
| CPU over 5.006 seconds | **2.52% busy**, 0.10% iowait, 0.00% steal, aggregate across host CPUs |
| Uptime at start | 370,950.17 seconds |
| Root filesystem, also backing deployment directory | 234,622,398,464 B total; 79,251,804,160 B used; 143,377,874,944 B available |
| Existing service states | Docker, containerd and tailscaled active |
| Largest observed process RSS | QEMU 3,407,720 KiB; Chrome processes present (largest 452,376 KiB); GNOME Shell 306,604 KiB; dockerd 143,988 KiB; uvicorn 61,852 KiB |

Process inspection used PID/PPID/user/**comm**/RSS/%CPU only, without command-line
arguments or environment. `ps %CPU` is lifetime-oriented and was not used for the
5-second CPU figure; that figure uses `/proc/stat` deltas, excludes guest-field
double counting, and treats idle/iowait separately. RSS is not additive private
memory and these samples are not worker peak measurements. The host is shared
with a running VM and desktop/browser workloads. Present headroom neither reserves
capacity nor validates the trial 1 GiB / 0.75 CPU worker budget. No worker exists
in this measurement, and no browser/profile operation was performed.

## Deployment identity and authorized Docker route

- Existing `monitor/.deploy/server_ed25519` successfully authenticated SSH as
  `thangma` with system known-host verification; no trust bypass or key creation.
- Login groups include `sudo`, `libvirt`, `kvm` and others, **not `docker`**.
  Group membership is not proof of passwordless sudo authorization.
- `/var/run/docker.sock` is an actual socket, owner `root:docker`, mode **0660**.
  The session has no docker group. `/run/user/1000/docker.sock` is absent.
- Docker context list contains only active `default`; its endpoint is
  **`unix:///var/run/docker.sock`**. No `DOCKER_HOST`, `DOCKER_CONTEXT`,
  `DOCKER_TLS_VERIFY` or `DOCKER_CERT_PATH` override is set remotely.
- Exactly one ordinary `docker info --format '{{json .}}` attempt in this pass
  returned exit **1**, classified **socket_permission_denied**; raw stderr was
  withheld. No alternate socket, privileged session, or libvirt capability was
  used to circumvent this boundary.
- `deploy.py` documents password SSH followed by password-fed `sudo -S` and
  deployment mutations. **Do not run it for an audit.** `remote.py` documents
  dedicated-key SSH plus `sudo -S -p "" sh -c ...`, using `DEPLOY_PASSWORD` from
  the local process environment. Its optional second argument uploads a script;
  it must be omitted for read-only audits.
- Local `DEPLOY_PASSWORD_present=False` was checked as a boolean only. Thus the
  documented password-fed admin workflow is **not executable in this session**.
  Historical feasibility already recorded `sudo -n docker info` exit 1. It was
  not retried here; no new claim about sudo policy was inferred from that history.
- Read-only libvirt access (`virsh --readonly --connect qemu:///system`) succeeded
  for list, selected allocation XML fields, state, DHCP lease and domstats. This
  establishes infrastructure evidence, **not Docker administrator access**.

### Precise minimal operator actions

1. Confirm whether this pilot is intended for `100.92.238.77` (host) or the
   separately named 4 GiB / 2 vCPU VM. If host, explicitly confirm its agreed
   monitor budget; if VM, provide its authorized SSH/deployment identity/route
   and obtain a fresh **guest** baseline. Do not adopt the lease IP as a verified
   deployment identity without that confirmation.
2. For the confirmed target, have the existing authorized operator make the
   documented admin workflow available through secure local environment
   provisioning of `DEPLOY_PASSWORD` to the operator process, or run the read-only
   admin audit themselves and return sanitized evidence. **No credential in chat
   or an issue.** On the current host, once provisioned, the initial check is
   `python monitor/remote.py "docker info --format '{{.OSType}}'"` (no upload
   argument). Success must be measured; no successful sudo path is claimed yet.
3. Once authorized, obtain only formatted `docker ps`, targeted `docker inspect`
   fields (name, Compose project/service labels, PID, Memory/MemorySwap/NanoCpus/
   CpuQuota/CpuPeriod/CpusetCpus/PidsLimit) and `docker stats --no-stream`.
   Avoid unrestricted inspect, environment dumps, logs or `docker compose config`.
   This closes container identity/limit evidence without deployment. Stop on an
   authorization failure and report it; do not cycle through privilege attempts.

No sudo/group/socket policy changes, package installation or service restart is
needed or authorized for this phase.

## Private authentication-storage proposal — not created

Proposed host path, **conditional on confirming this host**:
`/home/thangma/.local/share/kaggle-monitor-auth/thangma/storage-state.json`.
This is outside the deployment/build tree. The existing parent
`/home/thangma/.local/share` is `thangma:thangma`, mode **0700**; the proposed
`kaggle-monitor-auth` directory does **not** exist. No state/directory was created,
transferred or read. If the VM is selected, verify its own home/permissions before
choosing the equivalent guest path.

For Phase 3/worker ownership: private root and per-account directory **0700**,
state file **0600**, owned by the explicitly selected runtime UID/GID (not yet
chosen/verified). Transfer only necessary Kaggle state through the authorized
private route and verify identity on Linux. Mount only the needed account subtree
in the future worker, outside image/build context; use read-only access if state
does not rotate, or narrowly owned writable storage for atomic refresh. Do not
expose it to the dashboard or repository. Persistence/recovery/retention remain
later-phase acceptance. Existing deployment `secrets/` is mode 0700 and `.env`
0600, but neither was opened and neither is the proposed browser-auth store.

## Checks, ownership and next-phase handoff

Reviewed `PHASE-PLAN.md`, `ACCOUNT-ACTIVITY-HANDOFF.md`,
`ACCOUNT-AUTH-FEASIBILITY.md`, live issue bodies/comments #22/#24, `deploy.py`,
`remote.py`, feasibility audit code, local Compose and deployment documentation.
Live issues were open with no comments at the initial read.

Three bounded known-host-verified SSH sessions ran inline Python probes, without
uploading a script: host/cgroup/baseline/socket audit; container cgroup and
read-only VM allocation audit; VM state/lease/runtime allocation and health audit.
All exited **0**. The contained ordinary Docker probe exited **1** as recorded.
Read operations included `/proc`, `/sys/fs/cgroup`, selected DMI and file metadata,
`ps` without arguments/environment, `df`, `systemctl is-active`, Docker context
metadata, read-only virsh, and HTTP GET `/healthz`. Only allowlisted Compose
structure/resource keys and libvirt allocation fields were emitted; secret files
and raw domain XML were not printed. No app test suite is applicable to this
documentation-only phase.

**Owned file:** `monitor/PHASE-2-INFRASTRUCTURE.md` only. Existing application,
frontend, tests, Compose and other phase documents were preserved. No production
GPU launch/cancel, browser/profile operation, deployment, commit or push.

Phase 3 remains blocked on target selection and executable authorized Docker
access; no Linux auth transfer or restart verification occurred. Phase 7 still
requires actual worker peak/load/resilience measurements on the confirmed target.
Neither issue #22 nor #24 is ready to close from this infrastructure evidence.

Evidence comments posted (issues remain open):
- [#22 checkpoint](https://github.com/mathangspk/kaggle_test_waiting/issues/22#issuecomment-6194127520)
- [#24 checkpoint](https://github.com/mathangspk/kaggle_test_waiting/issues/24#issuecomment-6194127711)
