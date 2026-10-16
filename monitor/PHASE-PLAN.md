# Account monitoring — phase execution plan

Master issue: https://github.com/mathangspk/kaggle_test_waiting/issues/18
One new chat per phase. Priority expresses urgency; dependencies determine order.
This master chat coordinates handoffs and acceptance. No phase is complete merely
because code exists locally. Inspect current workspace and live issues first.

## Phase 1 — P0: Review and accept the activity evidence contract (#19)

- Review existing summary/frontend implementation and unresolved-state regression.
- Confirm coverage, freshness, source, identity, errors and last-known evidence.
- Ensure partial/missing discovery never reports idle; GPU requests are not verified hardware.
- Run focused backend/frontend checks; report contract and changed files.
- Exit: reviewed contract and local acceptance evidence. Provider coverage is separate.

## Phase 2 — P0: Confirm deployment target and authorized Docker access (#22/#24)

- Resolve discrepancy: planned 4 GiB/2 CPU target versus SSH host reporting
  31.21 GiB/8 CPU. Establish whether the former is a VM/container budget.
- Confirm exact host, Docker-capable operating route, baseline workload and private
  authentication storage location. Do not request secrets in chat.
- Exit: documented target, access and measured baseline. No deployment yet.

## Phase 3 — P0: Validate thangma authentication in Linux (#20)

- Use only thangma for the pilot; other accounts deferred by user choice.
- Windows fresh headless reuse passed; Linux reuse and restart are unverified.
- Transfer only necessary authentication through a private authorized route.
- Verify Kaggle username, headless access, browser/container restart persistence,
  and explicit authentication-required behavior on missing/invalid state.
- Preserve original Chrome profiles and never print cookies/tokens.
- Exit: actual Linux evidence or a precise blocker requiring user assistance.
- Depends on Phase 2. Failure blocks unattended worker acceptance.

## Phase 4 — P1: Accept production API polling (#21)

- Review implemented exact-version polling, fair bounded retries and recovery.
- Verify attachments without push or invented submit times; distinguish execution
  completion from artifact/timing verification.
- With authorized account credentials, perform a read-only exact-version check.
  thanglong1996 historical v3 mapping remains pending if credentials unavailable;
  do not replace that evidence with a thangma check.
- Exit: local acceptance plus clearly scoped live evidence/pending identities.
- Depends on Phase 1; can proceed independently of Phases 2–3.

## Phase 5 — P1: Implement bounded browser discovery (#22)

- Separate service, one browser/visit at a time, isolated authentication storage.
- Validate event identities, scrolling/loading and discovery coverage; partial
  samples must stay partial even when panel says zero active events.
- Add authenticated ingestion, deadlines, process-tree cleanup, backoff, bounded
  queue and health/resource metrics. No browser launches on dashboard requests.
- Trial budget: 1 GiB RAM, 0.75 CPU, 60s visit deadline, discovery every 3–5 minutes.
  Tune from measurements, not page JS heap alone.
- Exit: tested worker with honest coverage and bounded resource/process behavior.
- Depends on Phases 1–3.

## Phase 6 — P0: Accept admission policy and concurrency handling (#23)

- Review implemented observe_only/enforce behavior and persisted diagnostics.
- Enforce mode defers on busy, queued, unresolved, stale, failed or incomplete
  required evidence. Preserve existing remote work and account isolation.
- Test restart and local submission coordination; document manual website race.
- Exit: admission acceptance; deployment remains observe-only until Phase 7 passes.
- Depends on Phase 1 and integration contracts from Phases 4–5.

## Phase 7 — P1: Observe-only pilot and resilience acceptance (#24)

- Deploy thangma-only discovery without changing live admission to enforce.
- Measure whole-worker/container CPU, peak RAM, processes, host headroom,
  repeated cycles and API/dashboard responsiveness.
- Verify timeout/crash/auth-expiry/ingestion/restart handling and rollback.
- Exit: measured resource report, observation coverage and remaining limitations.
- Depends on Phases 2–6. No unapproved GPU launch/cancellation for testing.

## Phase 8 — P0: Activate fail-closed pilot admission (#23/#24)

- Review Phase 7 evidence, select explicit TTL/coverage policy and activate enforce
  only for accepted accounts. Unsupported accounts remain explicitly deferred
  under the agreed policy; document global rollout impact before activation.
- Verify skip reasons, persistent behavior and rollback without launching test GPUs.
- Exit: recorded configuration and behavior acceptance, not just a config edit.

## Phase 9 — P1: Onboard remaining accounts (#20/#22/#24)

- User-assisted login later, sequentially: Profile 6 thanglong1996, Profile 7
  annalynhi, Profile 5 thnhmngc (user-reported original mappings).
- Independently repeat Linux identity/restart/discovery checks for each account.
- Recheck sequential cycle capacity with four accounts, then admit only accounts
  meeting freshness/coverage/auth acceptance. Do not infer success from thangma.
- Exit: per-account evidence matrix and all-account operational acceptance.

## Phase 10 — P2: Operations and follow-up improvements (#18/#24)

- Finalize auth-recovery runbook, alerts, private-state retention/backup policy,
  resource/cadence tuning and coverage limitations.
- Refresh packaged roadmap through existing sync process at release time.
- Evaluate further discovery coverage and model-loading progress instrumentation
  as separately scoped follow-ups; neither is implied by API running state.
- Exit: reviewed operational handoff and evidence-backed issue closure.

## Starter prompt for every new chat

> Execute Phase N from monitor/PHASE-PLAN.md, workspace C:\local\kaggle_test.
> Read monitor/ACCOUNT-ACTIVITY-HANDOFF.md, current code/status and linked live
> issues first. Verify prerequisites; preserve other work. Own only this phase.
> Implement and test where authorized; report blockers precisely. No production
> launch/cancel, commit or push without explicit authorization. Return changed
> files, checks, safe evidence, acceptance status and next-phase handoff; comment
> linked issue(s). Do not close issues based on local tests alone when live
> acceptance remains outstanding.

Only one phase chat may operate browser/profile state at a time. Serialize
overlapping app.py/compose edits or use coordinated worktrees. The master does
not assume it can automatically create or control the user's UI chat tabs.
