# Existing-account authentication feasibility — issue #20

Issue: https://github.com/mathangspk/kaggle_test_waiting/issues/20

## Current Phase 3 result — 2026-10-15 14:53–14:59 UTC

**thangma Linux Docker authentication and disk/browser/container-restart reuse now
pass on the user-approved existing 31.21 GiB / 8 CPU host.** Empty and deliberately
invalid state return `authentication_required`. Restart read Successful event
versions #144/#143; coverage remains partial. Actual container peaks were
553.9/419.6 MiB under enforced 1 GiB / 0.75 CPU limits, no OOM. Private state is
retained out of repo at
`/home/thangma/.local/share/kaggle-monitor-auth/thangma/storage-state.json`
(directories 0700/file 0600, UID/GID 1000:1000). Pilot containers, temporary
dependencies and remote script were removed; pinned browser image remains cached.
Full evidence, dependency digest, limitations and handoff:
[`PHASE-3-LINUX-AUTH.md`](PHASE-3-LINUX-AUTH.md).
Other accounts remain deferred by user choice. The earlier Docker/target blockers
below are historical and resolved for this host/thangma pilot. Natural expiry,
host reboot and all-account acceptance remain unverified.

## Historical Windows and infrastructure evidence

Latest thangma-only checks: **2026-10-15 13:42–13:45 UTC**. Initial evidence was
collected 2026-10-14, approximately 02:33–02:38 UTC; mapping follow-up approximately
22:18–22:22 UTC that day. **Windows thangma reuse passes; Linux Docker acceptance
remains blocked.** The user explicitly deferred the other three accounts until
they log in later: **pending by user choice, not failed**. No further login/setup
attempts for those accounts were made during the latest pass.
This report is feasibility evidence, not a live account-activity snapshot.

## User-reported mapping update

In the follow-up conversation, the user explicitly supplied:

- `Profile 6` → `thanglong1996`
- `Profile 7` → `annalynhi`
- `Profile 5` → `thnhmngc`

Provenance: **user-reported**, not independently verified by authenticated Kaggle
identity checks. These replace the earlier unknown mappings for those three
accounts; mapping alone does not establish accessible authentication or reuse.
The original directory mapping for thangma remains unknown.

## Per-account evidence matrix

| Account | Source profile / mapping | Verified Kaggle username | Event discovery / coverage | Windows reuse | Linux headless reuse | Restart persistence | Auth expiry behavior / blocker |
| --- | --- | --- | --- | --- | --- | --- | --- |
| annalynhi | **Profile 7 (user-reported)** | Not verified | Pending by user choice | Pending by user choice | Pending | Pending | User will log in later; not classified as failed |
| thanglong1996 | **Profile 6 (user-reported)** | Not verified | Pending by user choice | Pending by user choice | Pending | Pending | User will log in later; not classified as failed |
| thangma | Existing isolated Playwright context; original Chrome directory mapping still unknown | **thangma**: account-menu Your Profile `/thangma`; `GetCurrentUser` HTTP 200 in source and both fresh headless tests | Three Successful probe versions #142/#141/#140; 0 Active Events; scrolling sampled; **partial** | **Passed again** in two fresh Windows Chrome headless processes with Kaggle-only in-memory storage state | Docker socket permission denied; native Linux auth untested; no state transferred | Fresh-process rehydration passed twice again; disk/container/server persistence untested | Prior empty-context signed-out control passed; actual expiry, revocation, MFA and refresh untested |
| thnhmngc | **Profile 5 (user-reported)** | Not verified | Pending by user choice | Pending by user choice | Pending | Pending | User will log in later; not classified as failed |

All four accounts were assessed for available access. Actual authenticated identity
and discovery testing was possible only for thangma. No public account page or
Google identity was used to certify a Kaggle login.

## Latest user-approved thangma-only pass

The user narrowed the active scope to thangma and will log in the other accounts
later. Earlier missing-account setup attempts below are historical, not present
failures or a request to log in now. Only the existing thangma context and two
sequential fresh headless test browsers were operated in this pass.

### Identity, events and fresh-process reuse

| Sample (UTC, 2026-10-15) | Authenticated identity | Event observation | Visit elapsed | Page JS heap used | Page task duration |
| --- | --- | --- | --- | --- | --- |
| Existing MCP browser, 13:44:01.774 | HTTP 200 username `thangma`; Your Profile `/thangma` | Successful #142/#141/#140; 0 Active Events | 9.210 s | 151,411,380 bytes (144.4 MiB) | 2.929 s |
| Fresh headless process 1, 13:44:08.831 | Same independently verified identity | Same three rows and footer | 6.450 s | 51,189,592 bytes (48.8 MiB) | 1.988 s |
| Fresh headless process 2, 13:44:16.186 | Same independently verified identity | Same three rows and footer | 6.571 s | 52,591,160 bytes (50.2 MiB) | 1.979 s |

These are Chromium CDP **page renderer** JS heap/task metrics, not total browser
working set, peak RAM or CPU utilization. Visit elapsed excludes browser launch;
the source visit includes resolving three read-only event view links. No conclusion
about the provisional 1 GiB/0.75 CPU worker budget follows from these measurements.
Both fresh processes used private Kaggle-only in-memory storage state from the
verified source context. No auth state file, token value or cookie value was
emitted. The first test browser was closed before starting the second. Browser
launch timeout was 20 s, with a 60 s visit deadline; all samples completed normally.

Each panel had a 272 px scroll viewport and 288 px content. Scrolling moved
`scrollTop` from 0 to 16 (the bottom), then another text sample was unchanged.
This verifies the small sampled panel's scroll traversal, **not** long-list lazy
loading, pagination, interactive-session completeness or exhaustive account
discovery. Coverage remains **partial** despite the 0 Active Events footer.
No `observed_idle` or admission decision was recorded.

Read-only event view destinations resolved in the source browser:

| UI version / state | Notebook ref | scriptVersionId |
| --- | --- | --- |
| #142 / Successful | thangma/queue-monitor-probe | 358692116 |
| #141 / Successful | thangma/queue-monitor-probe | 358687720 |
| #140 / Successful | thangma/queue-monitor-probe | 358679043 |

These are script-version references, **not native session IDs**. The UI labels
GPU T4 x2; physical device verification was not performed. Only newly opened view
tabs were closed. No launch/cancel, editor controls or event-action menu was used.
Actual auth expiry/refresh, disk persistence and container restart are untested.

### Separate persistent setup window

At 13:42 UTC the supplied browser exposed one context with one Kaggle home page;
no retained setup-browser handle was available. A read-only process audit found
the normal non-debug Chrome plus two custom-data Chrome roots with debugging
pipes, but no TCP debugging ports. A process's pipe flag alone is not a supported
attachment handle. The separate persistent setup window's ownership, page and
authentication therefore remain unverified. No attempt was made to open its
profile, replace its connection, terminate it or close any user's window.

### Read-only Linux feasibility and exact Docker blocker

One existing-key, known-host-verified SSH check ran without sudo, password entry,
installations, deployment or browser launch. A single ordinary `docker info`
attempt exited **1** with sanitized classification
**`docker_socket_permission_denied`**. Earlier noninteractive sudo failure was
not retried. This blocks Docker-target authentication/resource/restart testing
under the currently available authorization.

Host measurements: **8 online CPUs**, MemTotal **32,723,684 KiB** (31.21 GiB),
MemAvailable **26,448,764 KiB** (25.22 GiB). Session-level cgroup v2 memory/CPU
limit files were unavailable; no target container limits were inspected. These
host values do not certify the intended **4 GiB / 2 CPU** target.

`google-chrome` was found in the remote PATH; system Python has no Playwright.
Thus native Linux browser availability is a positive capability finding, but its
launch/auth reuse was not tested and should not be described as impossible.
No state was transferred to Linux. The precise next Docker prerequisite is an
authorized operator path with permission to the Docker daemon/socket, supplied
through the existing admin/deployment workflow; no repeated privilege attempts
or permission changes were made.

## Mapped-account follow-up: actual attempts

At **22:18 UTC**, a fresh read-only process audit confirmed the running normal
Chrome browser still had neither remote-debugging port nor pipe. The normal
user-data root had no `DevToolsActivePort` file. The provided automation browser
exposed exactly one accessible context, with one Kaggle page. Other isolated
processes were not treated as attachable sessions merely because they existed;
no supported connection handle for them was available through the supplied tools.

The updated inventory records mapping provenance separately from metadata and
keeps `mapping_verified=false`. Profiles 5, 6 and 7 exist. No original profile
was opened, copied, overwritten or attached. No encrypted credential/cookie store
was read. A safe supported attachment to the running normal Chrome browser could
not be established; no connection endpoint was available to attempt CDP attachment.

At **22:21–22:22 UTC**, separate fresh **headed** isolated Chrome browsers were
tested sequentially for each mapped setup target, with no imported state:

| Setup target | Kaggle identity response | Normal sign-in flow reached | Actual result |
| --- | --- | --- | --- |
| Profile 6 → thanglong1996 | `GetCurrentUser` HTTP 200; no known username | Kaggle Login → Sign in with Google → `accounts.google.com/v3/signin/identifier`; fixed text “Email or phone” / “Sign in with Google” | User authentication required; no identity/event verification |
| Profile 7 → annalynhi | `GetCurrentUser` HTTP 200; no known username | Same normal Google identifier screen | User authentication required; no identity/event verification |
| Profile 5 → thnhmngc | `GetCurrentUser` HTTP 200; no known username | Same normal Google identifier screen | User authentication required; no identity/event verification |

These were isolated setup-route checks, **not** tests of the originals' cookie
contents or proof that their existing logins have expired. No credentials were
entered or Google identity selected. No visible browser-rejection or human-check
message was observed at this initial step; later sign-in/MFA success is untested.
An `input[type=email]` selector did not match a visible input; the identifier-screen
conclusion comes from the Google path and fixed visible text, not that selector.
Two initial setup checks timed out against the old Sign In link selector; current
Kaggle instead exposed a Login button, and the final verifier supports both variants.
Each agent-created browser was closed before the next was launched. No setup
window remains open; original Chrome and the provided automation browser remain open.

The existing isolated context was independently rechecked at
**22:22:27.958 UTC**: `GetCurrentUser` HTTP 200 returned `thangma`. The earlier
headless/discovery evidence remains timestamped above; no new discovery or restart
success is claimed for this follow-up. No Linux work/deployment was performed.

**Historical user-assisted next step (now deferred by user choice):** coordinate one isolated setup browser at a
time, starting with thanglong1996 (Profile 6 identifies the account). In that
isolated browser complete Kaggle → Login → Sign in with Google using normal Google
authentication/MFA/recovery. Repeat for annalynhi and thnhmngc. Keep normal Chrome
windows open. Do not send credentials or cookie/token values in chat. After each
successful sign-in, the operator must verify Kaggle's current username before
reusing that context's supported Kaggle-only auth state or collecting events.
The original profile mapping needs no further user confirmation at this stage.

## Original-profile audit and supported access

- `Default` and `Profile 1`–`Profile 7` exist in the normal Windows Chrome user-data
  directory. Allowlisted `Local State` inspection found Google identity metadata
  in Default and Profiles 1, 2, 4, 5, 6, 7, but not Profile 3. That is metadata,
  not verification of current Google or Kaggle authentication.
- Exact known-username comparisons against profile label metadata produced no
  mapping hints. Names/emails were not emitted. No cookie database, password store,
  key value, session file or original profile was exported/copied.
- Installed Chrome: **155.0.8059.40**. The normal Chrome browser has no enabled
  remote-debugging endpoint. Separate custom-data automation processes do exist;
  their presence does not make the original browser attachable. Only the provided
  isolated Playwright context was used.
- Chrome's `Local State` has an app-bound encrypted-key field (presence only).
  This does not prove any particular Kaggle cookie's encryption scheme. Copying
  profiles is not evidence of portable authentication.
- Chrome 136+ ignores debugging switches against its normal data directory and
  requires a nonstandard user-data directory. Reopening the normal directory for
  automation, opening a profile concurrently, extracting protected credentials or
  disabling authentication protections is not a supported solution here.
- Supported working path: the already authenticated isolated browser's
  `storageState()` API, filtered to Kaggle cookies/origin, used directly in memory
  by fresh isolated browser contexts. Google authentication state was not transferred.
  For the other accounts, that API cannot retrieve state from inaccessible original
  browser sessions. User-assisted normal authentication in separate isolated
  contexts is the next available path.

References consulted:

- https://developer.chrome.com/blog/remote-debugging-port
- https://playwright.dev/python/docs/auth

## Initial Windows evidence (2026-10-14)

At **02:37:17.982 UTC**, two sequential headless Chrome launches each showed:

- `users.UsersService/GetCurrentUser`: HTTP 200, allowlisted username `thangma`.
- Authenticated account-menu Your Profile: `/thangma`.
- Events panel, implemented as a tooltip, fully readable for the two visible rows:
  `Queue Monitor Probe`, `Version #71 with GPU T4 x2`, `Successful`; and
  `Queue Monitor Probe`, `Version #70 with GPU T4 x2`, `Successful`.
- `0 Active Events` footer. No scrollable overflow in the sampled panel, so real
  long-list scrolling, pagination and exhaustive coverage are **not verified**.
- Rows use JavaScript navigation rather than stable DOM `href` attributes.

At **02:37:50.027 UTC**, read-only opening of those event links in the original
isolated automation context resolved:

| Displayed version | Notebook ref | scriptVersionId | Interpretation |
| --- | --- | --- | --- |
| #71 | thangma/queue-monitor-probe | 358183127 | Script-version identity, not a native session ID |
| #70 | thangma/queue-monitor-probe | 358181438 | Script-version identity, not a native session ID |

Only those newly opened view tabs were closed. No notebook editor, launch,
cancellation, event dismissal or other production control was invoked. Accelerator
labels are provider UI labels; hardware was not independently verified.

The two headless tests reused the same initial in-memory state in separate fresh
browser processes, closing the first before launching the second. This verifies
fresh-process rehydration; it does **not** verify rotated-state persistence,
filesystem persistence, a Windows reboot, Linux portability or container restart.
The bundled Playwright browser executable was unavailable on the first launch
attempts; using the supported installed `channel: 'chrome'` resolved that tooling
blocker. The original browser and the provided automation browser stayed open.

At **02:38:19.120 UTC**, a fresh empty headless context showed Sign In, no Thang Ma
account menu and no View Active Events entry. `GetCurrentUser` returned HTTP 200
without any known username. Thus HTTP 200 alone is insufficient. This is a
signed-out control, **not** measured natural expiry or deliberate session revocation.

## Initial Linux access evidence (2026-10-14)

- No local Docker CLI is installed/available in PATH.
- Existing deployment SSH key and known-host verification allowed a read-only
  capability check against the configured server, without a password prompt.
- Remote system reported Linux and **8 online processors**. This is host evidence;
  the intended **4 GiB / 2 CPU** worker target/limits were not measured or verified.
- Remote `/usr/bin/docker` exists. Ordinary `docker info` exited **1**;
  `sudo -n docker info` also exited **1**. The available login could not establish
  Docker access. No deployment password is available in this agent's environment.
- Remote system Python reported `playwright_installed=False`. Browser binaries
  elsewhere on the host were not inventoried. No software/container was installed.
- No authentication state was sent to Linux. Docker-target identity, discovery,
  resource limits, private state-file permissions and restart persistence remain
  untested. A user-assisted authenticated isolated session alone will not resolve
  the independent Docker-access blocker.

## Contract and worker handoff

No shared-contract changes. Per `ACTIVITY-CONTRACT.md`, the sample is
`source=browser_discovery`, `coverage=partial`; it must never certify
`observed_idle`. Failed/inaccessible accounts need `coverage=none` with sanitized
auth/access errors, retaining earlier observations according to the contract.
No sample from this investigation was written to the application's activity store.
Native session IDs and complete account coverage remain unresolved.

Issue #22 can implement against the contract and test its own isolated contexts
after coordination. Original-profile access is still blocked. Browser/profile
operations for this investigation have finished; any follow-up operators must
coordinate sole ownership and keep original user sessions open. At most one
additional agent-created browser ran at a time; the user's existing processes
were not altered.

## Recommended next step

1. Continue only thangma for the current stage. No login action is requested for
   the other three accounts until the user chooses to resume them.
2. Restore authorized Docker operator access through the existing deployment/admin
   workflow. Verify the actual 4 GiB/2 CPU target and private out-of-repo authentication
   storage before exporting any state.
3. Test thangma identity and discovery on the actual Linux target, then repeat
   after browser/container restart. Establish long-panel traversal,
   stable event/run identities, expired-auth handling and recovery before claiming
   full coverage or enabling admission. Later verify the other accounts independently
   after their user-assisted sign-ins. thangma's Windows pass is not an all-account
   or Linux certification.

## Owned files and reproducibility

- `monitor/feasibility_inventory.py`: read-only allowlisted local metadata/server
  capability audit. Run `python monitor/feasibility_inventory.py inventory` or
  `python monitor/feasibility_inventory.py server`. The latter uses the existing
  private deployment key without displaying it and performs no sudo mutation.
  The latest pass uses `server-readonly`: host resource/PATH checks and exactly
  one ordinary Docker info probe with sanitized error classification, no sudo.
- `monitor/feasibility_windows.js`: invoke with the Playwright `run_code` tool's
  `filename` parameter from the existing isolated authenticated thangma page.
  Current thangma source identity/event checks, two sequential Chrome headless
  tests, in-memory Kaggle-only state, bounded visits, panel scrolling and page-level
  resource metrics. It never attaches to normal profiles or exports state.
- `monitor/feasibility_mapped_auth.js`: follow-up verifier for the three
  user-reported mappings. Rechecks the existing isolated Kaggle identity, then
  sequentially opens empty headed browsers through the normal Google sign-in
  entry point. Emits only allowlisted identities, origins/paths and fixed UI
  signals; enters no credentials and closes only its own test browsers.
- This report. Other agents' application/frontend/test changes were preserved.

Checks completed: both Python audit modes executed; Windows verifier executed with
two successful real provider identity/discovery checks; read-only event-link
resolution; signed-out control. No commit, push, production run or cancellation.
Issue #20 remains open because all-account/Linux acceptance is not yet satisfied.
