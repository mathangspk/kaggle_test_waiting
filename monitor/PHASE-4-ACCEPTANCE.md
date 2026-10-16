# Phase 4 — exact-version API polling acceptance

Reviewed **2026-10-15** against [issue #21](https://github.com/mathangspk/kaggle_test_waiting/issues/21)
and `PHASE-PLAN.md`. Phase 1 independent local acceptance was supplied as passed.

**Result: local acceptance passes; live provider acceptance remains pending.**
This is local review evidence, not deployment evidence or issue closure.

## Reviewed behavior

| Area | Accepted local evidence |
| --- | --- |
| Exact version and owner | `production_status` uses the owning account's isolated helper and one official `get_kernel_session_status` request with `version_label=v<N>`. Wrong owner is rejected before the provider read. No latest lookup or artifact fetch in this polling path. |
| Identity boundary | Collector validates the helper's requested ref/version, status and boolean failure flag. Those identity fields echo the request; they are not independently returned provider identity or script-version mapping proof. |
| Existing attachments | Authenticated, owned exact identities attach without push. New attachments have null submit/ack/due times and no inferred execution start, queue timing or verified devices. Same-identity replay is idempotent, including after terminal completion/restart; confirmed conflicts are rejected. |
| Ambiguous submissions | No automatic latest reconciliation or repeat push. Explicit attachment retains original receipt timestamps, outcome, reported timing and registration hash. |
| Execution versus artifacts | Complete/error/cancelled execution ends polling. Complete without artifacts does not verify timing or hardware. Caller-reported timing stays self-reported; only validated provider artifacts qualify timing metrics. An attachment without submission time stays outside submission-time analysis. Artifact evidence wins over an in-flight status poll. |
| Fairness and bounds | One poll candidate per tick; alternating probe/production lanes under contention. Production chooses least-recently-served eligible account, then due versions. Older active versions remain eligible. Timeout uses the bounded helper subprocess. |
| Recovery | Record, activity snapshot, retry deadline and account fairness update atomically. SQLite restores deadlines/fairness across restart. Failures retry at 60/120/240/480/900 seconds, capped at 900; success resets the failure counter/cadence. Interrupted reads can repeat safely. |
| Honest account evidence | Auth/404/protocol/timeout failures preserve last-known observations and success timestamp with coverage none. Successful known-version evidence is partial, never exhaustive discovery or proof of idle. |

## Changes owned by this acceptance pass

- `monitor/tests/test_production_polling.py`: add regressions for a failing account
  alongside healthy multi-version backlog, restart at every retry, capped deadlines,
  recovery/reset, and terminal attachment replay after restart without requeue or
  fabricated timing. Existing tests and implementation were preserved.
- `monitor/PRODUCTION-POLLING.md`: clarify request-echo identity limits and link this
  current acceptance record.
- `monitor/PHASE-4-ACCEPTANCE.md`: this report.

No application defect was reproduced in this review; no `app.py` fix was needed.
The first broad test attempt exposed a local test-environment defect: the existing
temporary venv lacked its Python launcher. Restoring that launcher with
`python -m venv --without-pip` at the same external temporary venv preserved its
installed dependencies and allowed the real isolated SDK-readiness test to pass.

## Checks

```powershell
& "C:\Users\TECHNI~1\AppData\Local\Temp\opencode\monitor-backend-venv\Scripts\python.exe" -m pytest monitor/tests -q -p no:cacheprovider
git diff --check
```

- **293 passed** (including 33 production-polling cases); dependency deprecation
  warnings only. Covers attachment API authentication/validation, exact SDK request,
  terminal/artifact separation, failures, ambiguity, persistence, fairness and races,
  plus the existing backend/activity/admission/wrapper regressions.
- Initial focused run before the two added regressions: **31 passed**.
- Initial broad run before repairing the launcher: **290 passed, 1 failed** in
  isolated SDK readiness (`helper_protocol`); resolved by the environment repair.
- `git diff --check` passed; Git emitted existing LF/CRLF normalization notices.

## Live evidence and precise gaps

The permitted live test scope is **thangma only**. The previously documented
`thangma/queue-monitor-probe` v142 is an available historical candidate identity,
not a newly verified API result. The Phase 3/feasibility browser observations are
not production API acceptance evidence.

Safe presence-only checks in this process found no configured `ACCOUNTS_ENV`,
`SOURCE_ACCOUNTS_ENV`, `KAGGLE_USERNAME`, `KAGGLE_API_TOKEN`, `KAGGLE_KEY`, or
`KAGGLE_CONFIG_DIR`. No workspace dotenv was found. Default local
`~/.kaggle/kaggle.json` and `~/.kaggle/access_token` files are absent. No scoped,
already-accessible thangma API credential route was established; therefore **no
live provider call or production attachment was performed**. This does not claim
credentials are absent from the deployed server. Remote multi-account credentials
were not read; no other account authentication or browser state was accessed.

Pending evidence:

1. With an authorized thangma-scoped API credential route, perform a read-only
   exact-version request and record sanitized ref/version/status/time. This would
   validate only that thangma check, without launch/cancel or timing inference.
2. `thanglong1996/production-supervisor-a64e182ba8` v3 remains unverified and outside
   the current thangma-only live scope. `scriptVersionId=358145984` remains unverified
   metadata, not a native session ID. A successful thangma check cannot verify
   either this historical version or its mapping; even an exact v3 status read
   would not by itself prove the supplied scriptVersionId mapping.

Issue #21 remains open. Phase 4's local contract is available to later integrations;
full live acceptance remains pending. No GPU launch/cancel, browser operation,
server deployment/privilege change, commit or push occurred in this pass.
