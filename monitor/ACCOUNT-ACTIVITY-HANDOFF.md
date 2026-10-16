# Account activity monitoring — master-chat handoff

Tracking: https://github.com/mathangspk/kaggle_test_waiting/issues/18

This chat coordinates scope, contracts, integration and acceptance. Separate chats
resolve one issue each; completion requires evidence, not just implementation.

## Current status (2026-10-16)

- **Deployed and healthy (observe-only, `AUTO_PROBES=false`):** monitor
  `sha256:bffeb07c…`, discovery `sha256:4e6d03d5…`. The production discovery
  blocker (interactive row) is fixed; the worker produces accepted `partial`
  observations every scheduled visit. See `PHASE-11-PRODUCTION-FIX.md`.
- **Closed:** #19 (activity semantics/contract), #22 (bounded discovery worker),
  #23 (probe admission gating). **Open:** #20 (other three accounts deferred to
  the final stage by the user), #21 (live exact-version provenance blocked on
  credentials/registered run), #24 (48h soak not elapsed; all-account matrix).
- **NO-GO for enforcement** (`PHASE-10-COVERAGE-AUDIT.md`); partial coverage never
  certifies idle. Only `thangma` is discovery-enabled; the other three show
  `required_source_missing`.
- Phase docs 2–11 capture evidence; `PHASE-1` remains defined only in `PHASE-PLAN.md`.

## Confirmed requirements

- Server: 4 GiB RAM, 2 CPUs; measure existing workloads before selecting limits.
- Accounts: annalynhi, thanglong1996, thangma, thnhmngc.
- Google authentication exists in separate Chrome profiles on this Windows laptop.
  Some account credentials are not remembered. Preserve original profiles.
- Laptop sometimes sleeps: server is the unattended-runtime target, laptop is for
  authentication setup/recovery. Authentication transfer is not yet verified.
- Sequential browser discovery every 3–5 minutes/account, at most one Chromium
  instance; bounded deadlines/backoff. Known-run API polling approximately 30–60s.
- Defer probes when required activity evidence is stale, incomplete, unavailable
  or unresolved. No active tracked probe does not establish account idle.
- Trial browser-worker budget: 1 GiB RAM / 0.75 CPU / 60s visit deadline. These
  are provisional, not measured guarantees. Use a separate worker container.

## Task board and dependencies

| Issue | Priority | Scope | Prerequisites |
| --- | --- | --- | --- |
| #19 | P0 | Status semantics, frontend and shared observation contract | None |
| #20 | P0 | Existing-profile reuse and all-account authentication feasibility | None |
| #21 | P1 | Exact-version production polling, attach existing runs | #19 contract |
| #22 | P1 | Resource-bounded headless discovery worker | #19 contract, #20 feasibility |
| #23 | P0 | Account-evidence probe admission | Observation integrations |
| #24 | P1 | Resource/resilience QA and staged deployment | #19–#23 evidence |

Issue URLs: https://github.com/mathangspk/kaggle_test_waiting/issues/NUMBER

Start #19 and #20 independently. After contract agreement, #21 and #22 can
proceed on distinct files. #23 follows integration; #24 validates observe-only
before scheduler gating is enabled.

## Separate-chat starter prompt

Replace NUMBER with the assigned issue:

> Resolve issue #NUMBER in mathangspk/kaggle_test_waiting, workspace
> C:\local\kaggle_test. Read monitor/ACCOUNT-ACTIVITY-HANDOFF.md and the live issue
> body/comments first. This is one task within a master-coordinated workstream.
> Inspect git status and preserve other chats' changes. Work only within the
> assigned scope; coordinate shared-file edits and browser/profile operations
> before proceeding. Implement and verify acceptance where prerequisites allow;
> report blockers honestly. Do not launch/cancel production jobs, commit or push
> without explicit authorization. Return changed files, checks/results, provider
> evidence without secrets, unresolved blockers, and any shared-contract changes.

## Ownership rules

- #19 owns initial summary/frontend contract; publish field decisions before
  adapters implement them. Coordinate `app.py` edits with #21 and #23.
- #20 is the sole browser/profile operator during feasibility. #22 browser tests
  must wait for an explicit handoff. Do not open a profile concurrently or close
  user browser sessions without coordination.
- #22 owns worker/service code; coordinate compose/Dockerfile edits with #24.
- Use separate worktrees where practical. Shared working-tree changes require
  explicit file ownership; never reset or overwrite another chat's work.
- No live GPU launch/cancellation, commit or push is implied by these issues.

## Evidence already available

- Current account summary/scheduler busy checks use only the probe `runs` table.
- `production_events` registrations start with `active=False`; execution polling
  must be separated from explicit artifact verification.
- User screenshot historically showed production-supervisor-a64e182ba8 version 3
  running on thanglong1996 while probe version 62 was complete.
- Supplied URL scriptVersionId=358145984 is not a verified native session ID.
  Verify identity/current status; do not infer current running state from screenshot.
- Windows Chrome directory contains Default and Profile 1–7; account mapping is
  unknown. Normal Chrome lacks remote debugging. Available automation browser is
  a separate profile; no existing-account reuse or Linux migration is verified.
- Existing monitor container has no browser dependencies or explicit CPU/RAM
  limits. Browser execution must stay off dashboard request paths.

## Acceptance boundaries

Authentication success on one account cannot certify the others. Google login
alone cannot certify Kaggle authentication. Empty/partial event panels, logout,
stale observations and failed queries cannot certify idle. Profile copying is
not a guaranteed Windows-to-Linux authentication migration. Polling and local
submission locks cannot eliminate races with manual website submissions.

Master integration records each issue's evidence and blockers. Update packaged
roadmap through the existing sync workflow at release time, not as evidence that
implementation is already deployed.
