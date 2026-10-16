# Exact-version production polling — issue #21

The collector polls registered accepted versions and caller-attached existing
versions using owning-account credentials in an isolated, timeout-bounded helper.
`production_status` issues exactly one official `get_kernel_session_status`
request with `version_label=v<N>`. It never queries latest, downloads artifacts,
pushes or cancels production. The collector validates the helper's requested
ref/version echo and provider status before persisting execution evidence. The
echo validates helper protocol, not independently returned provider identity or
scriptVersionId mapping.

## Attach an existing run

Authenticated endpoint: `POST /api/production/attach`, `X-Monitor-Token` uses the
existing ingest token. Example JSON (historical identity, not a current status):

```json
{
  "event_id": "historical-production-v3",
  "account": "thanglong1996",
  "ref": "thanglong1996/production-supervisor-a64e182ba8",
  "version": 3,
  "script_version_id": 358145984
}
```

The account must be configured and own the ref. The numeric script-version ID is
optional caller metadata, **not verified mapping or a native session ID**. The
endpoint does not contact Kaggle or launch anything; initial state is unresolved.
Same identity replays are idempotent; conflicting confirmed identities return 409.
An ambiguous receipt may be explicitly attached by its original event ID and
requested owned ref. The original submission outcome, times, reported timing and
registration hash remain intact. It is never automatically reconciled to latest.

New attachments have null submission/acknowledgement/due times, accelerator and
verified hardware. They are omitted from submission-time analysis. Explicit timing
verification returns `submission_time_unavailable` rather than fabricating latency.
Normal registered receipts retain the existing explicit artifact-verification path.

## Bounds and recovery

- One polling candidate per tick total; probe/production lanes alternate under
  contention. Existing probe submission/shutdown budgets remain in effect.
- Production selection rotates least-recently-served eligible accounts, then due
  versions, with persistent account fairness and per-event deadlines. Older active
  versions are retained independently of newer versions.
- Successful nonterminal polls are due after 60s. Failures retry after
  60/120/240/480/900s, capped at 900s, with one attempt per selected tick. A helper
  timeout terminates the bounded subprocess attempt. Sustained backlog/slow calls
  can extend cadence; 60s is a target, not a maximum evidence age guarantee.
- Terminal complete/error/cancelled removes the queue entry. Complete execution
  without artifacts remains terminal with unverified timing. Artifact verification
  wins over an in-flight stale poll and clears retained nonterminal evidence.
- SQLite stores execution record, per-event activity snapshot and queue/fairness
  updates atomically. Restart restores deadlines and fairness; interrupted reads
  can safely repeat, without a push. Legacy nonterminal registrations are enrolled.
- Auth/access/protocol/timeouts use sanitized codes, retain previous observations
  and success timestamps, and set coverage `none`. No duplicate tracked positive
  bypasses the failed-source semantics. Removed accounts remain unresolved.
- Known-run coverage is always partial; this adapter cannot certify account idle.
  Scheduler admission remains the separate #23 integration.

## Evidence and blockers

Latest Phase 4 review: [PHASE-4-ACCEPTANCE.md](PHASE-4-ACCEPTANCE.md),
2026-10-15: **293 backend tests passed**, including 33 production-polling cases.
Local acceptance passes; thangma-only live API evidence remains pending because
no scoped accessible API credential route was established. Historical thanglong1996
version/mapping acceptance remains independently pending.

Tests in `tests/test_production_polling.py` exercise actual SQLite persistence and
mocked official SDK requests: exact vN/owner, wrong identity/protocol, terminal
without artifacts, authenticated attachment, ambiguity, backoff, multi-account and
older-version fairness, restart, poll-lane budget, and artifact/poll races.

Initial implementation evidence: no live status or mapping was claimed for the supplied historical notebook/version
and scriptVersionId. Neither `ACCOUNTS_ENV` nor `SOURCE_ACCOUNTS_ENV` is configured
in this execution environment, and no workspace dotenv was found. #20 verifies
only thangma Windows browser reuse; thanglong1996 browser authentication and Linux
Docker access remain blocked. These are not evidence of current run state. No
historical attachment was written to a production database and no provider status
was fabricated. Owner-authenticated read-only provider validation of v3 and the
scriptVersionId mapping remains outstanding.
