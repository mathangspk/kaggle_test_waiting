# Phase 10 — coverage and admission gate audit (#18 / #22 / #23)

Audit date: 2026-10-16 local. Workspace: `C:\local\kaggle_test`.

## Decision

**NO-GO for coverage-qualified enforcement or account-wide idle certification.**
The current browser path can never produce accepted `full` evidence: successful
visits hardcode `partial`, and the shared validator/API reject `full`. Known-version
production polling is also always partial. More successful empty visits, scroll
stability, zero-active footers, resource headroom or restarts cannot change this.

Continue explicitly labelled **partial observe-only** operation. `observe_only`
is not a launch-disable switch: with `AUTO_PROBES=true`, current code can submit
despite `would_defer=true`. Recommend `AUTO_PROBES=false` when the operational goal
is observation without new GPU work; an operator retaining existing automatic
probes must explicitly accept that partial discovery does not protect them from
external activity. This audit did not change either setting.

An alternative deliberate **deny-all probe stop** using global `enforce` is
technically fail-closed, but is not successful coverage-based admission acceptance.
It requires an explicit operator decision about losing all automatic probe samples.
Keep #18/#22/#23 gates open.

## Scope and evidence provenance

Read the live bodies/comments of #18, #22 and #23; current `discovery_visit.py`,
`discovery_worker.py`, `discovery_contract.py`; activity persistence, production
polling, scheduler, admission, summary and ingestion code in `app.py`;
`ACTIVITY-CONTRACT.md`, `PROBE-ADMISSION.md`, `PRODUCTION-POLLING.md`,
`ACCOUNT-ACTIVITY-HANDOFF.md`, `PHASE-PLAN.md`, both Phase 7 reports, and existing
activity/admission/discovery tests. Reviewed the discovery overlay for policy scope.

Only this report and `tests/test_coverage_phase10.py` were authored by this audit.
No browser, SSH, Docker/server, provider or GPU operation was performed. Application,
worker and Compose changes belong to the coordinating operator. GitHub issue reads
and the requested #23 comment are the only remote collaboration operations.

Phase 7 evidence below is **reported prior live evidence**, not rerun here.
The separate operator's ongoing production observe-only deployment is not accepted
or described as completed by this audit. Historical stopped-container statements
are historical; they do not establish current production state.

## Honest acceptance matrix

| Gate | Available evidence | Acceptance / limit |
| --- | --- | --- |
| thangma Linux identity/auth reuse | Phase 7 requalification: exact username plus independent profile href; new containers and two service restart cycles | Accepted for bounded isolated pilot only; natural expiry/revocation/MFA/captcha and long unattended lifecycle remain open |
| Worker resource/restart/cleanup | Prior worker peak 532.730 MiB, monitor 106.230 MiB; bounded CPU/PIDs, no accumulation in short pilot, real fault cleanup | Bounded pilot accepted on 31.21 GiB/8CPU host; not other VM, all-account capacity, OOM recovery or production deployment acceptance |
| Healthy TTL expiry | Prior natural 300s transition from fresh partial to stale, retained data | Accepted for tested isolated path; production integration still needs its own evidence |
| Terminal script-version rows | Live Phase 7 Successful rows, strict owner/ref/version/scriptVersionId; persisted across restart | Narrow positive parsing accepted; not proof that running, queued or interactive event types were seen live |
| Running/pending state normalization | Existing mocked cases and offline fixture tests | Local logic accepted; live running/queued/cancel-request state and destination behavior unqualified |
| Delayed events | Existing 5s fixture; new arrival boundary cases through 13 ticks | Rows arriving by tick8 captured in fixture; after stable tick8 can be missed |
| Long list/virtualization/pagination | New bounded scrolling/100-row fixtures | Local bounds verified; no live exhaustive traversal, pagination implementation or completion proof |
| Stable identity | Script-version syntax, direct-link reorder fixtures, strict duplicate rejection | Exact version evidence only; href-less cache collision is an open reproduced defect; native sessions unsupported |
| Production polling | Local exact-version tests, source contract, no account enumeration | Partial known identities only; operator must supply production integration and exact-version provider evidence |
| Other three accounts | No current worker/API support for their discovery | Not accepted; thangma authentication does not transfer acceptance |
| Full discovery / idle | Both producer and endpoint prohibit full | Impossible through current discovery path; no full acceptance |
| Local admission serialization | Existing real SQLite concurrent claim/receipt/restart tests pass | Accepted within one DB; no lock on provider/manual submissions or separate DBs |
| All-account enforcing rollout | Offline real SQLite fixture across four names | Every account defers with current coverage; no coverage-qualified activation recommendation |
| Production observe-only deployment | Separate operator owns execution | Pending operator evidence, not certified by local tests |

## Exact supported events, states and identities

`collect_panel` targets only role-links whose accessible name matches
`^Status for .*Click to open` inside the tooltip reached by View/Open Active Events.
It checks visible English `Version #N` occurrences against linked occurrences.
It does not enumerate arbitrary account sessions or open every notebook.

`event()` accepts HTTPS `www.kaggle.com/code/thangma/<lowercase-hyphen-slug>`
with one positive `scriptVersionId` and exactly one distinct positive `Version #N`
in sampled text. Identity is `script-version:<ID>`, with separate owner/ref and
notebook-local version number. All browser observations have `run_type=unknown`.
Notebook names or known probe refs do not classify them as probe/production.
There is no native session ID, verified accelerator, editor/interactive state,
GPU allocation proof or model-loading progress evidence.

| Case-sensitive browser label | Normalized state |
| --- | --- |
| Running | running |
| Queued, Pending | pending |
| Successful, Complete, Failed, Canceled, Cancelled | terminal |
| Unknown, or no recognized label | unresolved |

Multiple recognized labels reject the event. This is text regex classification,
not a structured status field. Local title/status ambiguity is described below.
Production SDK states are a different vocabulary: `queued`, `running`, `pending`,
`cancel_requested`, `complete`, `error`, `cancelled`. Cancellation requested stays
nonterminal/pending; only acknowledged cancellation is terminal. Caller attachment
IDs are `production:<event_id>`; a supplied scriptVersionId mapping remains unverified.
Browser and production identities are not automatically joined by canonical version.
Contradictory stale positives can therefore continue blocking after another source
reports completion, which is conservative but needs an explicit reconciliation plan.

## False-negative and availability risks

1. **Bounded delayed loading:** 12 iterations maximum; stop after iteration8 if
   no scroll movement. There is no network/load-complete proof. The new fixture
   captures arrivals at 1/5/8 seconds, and demonstrates omission at 9/13 when no
   scroll movement occurs. Previously seeing a zero footer is not a completion signal.
2. **Long/virtualized/paginated lists:** each tick samples current links and scrolls
   scrollable descendants by 80%. It does not click Next/Load More, enumerate page
   tokens or assert an authoritative total. Continuous scrolling still stops at12;
   a thirteenth fixture page is omitted. At101 distinct raw destinations it fails
   rather than truncating to100, but a hidden 101st row is not detected. At100 rows
   it can succeed partially. Deadline/time spent resolving popups further limits work.
3. **Unsupported interactive/unversioned rows:** visible `Version #N` mismatches
   fail closed, but text without that pattern and without the supported link can
   be silently omitted. A running interactive/native session fixture returns an
   empty partial list. Other-owner/collaborative refs, other slugs/localization,
   selectors, session URLs and new event types are not established supported scope.
4. **Reproduced identity defect (strict xfail):** href-less destinations are cached
   by the whole sampled text. Two distinct visible links with identical text share
   one lookup/cache entry and collapse into one raw-href-keyed row. Matching version
   numbers across notebooks are legitimate; title equality is not identity proof.
   Existing row recheck protects changed text before click, not identical-text
   swaps or identity equality. Fix needs coordinated worker ownership, per-row
   destination resolution/identity checks, and a real sanitized layout fixture.
5. **Reproduced status ambiguity (strict xfail):** a notebook/title phrase containing
   `Successful` with an unrecognized actual status `Retrying` is classified terminal
   because regex scans all sampled text. Two recognized labels usually reject, but
   one title label plus unknown status does not. This synthetic fixture is not a
   claim that this exact provider row was observed. Use a dedicated structured
   status/accessible-status segment or fail unresolved when not provable.
6. **Raw URL accumulation:** rows are keyed by complete href, not canonical ID.
   Query aliases for one ID accumulate then validator rejects duplicate identities.
   This is safe loss of availability, not successful deduplication. Conversely,
   per-destination conflicting version numbers are rejected. Cross-visit binding
   of a script ID to ref/version is not persisted/independently verified.
7. **Non-atomic samples and replacement:** observations accumulated across ticks
   are all stamped at end-of-collection time, even if last seen earlier. A later
   successful empty partial write replaces the preceding browser running snapshot;
   there is no per-browser-ID terminal reconciliation or tombstone. Fixture verifies
   the positive disappears, while status remains partial and enforcement defers.
   Failed writes instead retain last-success observations and timestamp. Lifting
   the full prohibition without fixing omission semantics would expose unsafe idle.
8. **Cadence versus TTL:** healthy completion-relative delay180–300s plus next
   visit (~27s in Phase 7), ingestion and scheduling can exceed fixed300s TTL.
   A healthy maximum-delay fixture gives327s between observations. Transient stale
   windows are expected even with no worker error. Four-account scheduling is not
   implemented; naively repeating per-account waits would further worsen freshness.
   Do not lengthen TTL to claim exhaustive coverage; agree a risk/cadence budget.
9. **Manual/external race:** current SQLite reservation serializes local evidence
   and claims, not provider starts. Receipts/attachments describe already-submitted
   jobs. A new website/external submission after an observation/claim can race it.
   Read-only validation cannot prove zero future overlap without a coordinated
   submission policy/provider-side exclusion mechanism.

Partial coverage plus enforced admission currently contains these omissions by
denying idle admission. Observe-only diagnostics can miss positive events; they
must not be presented as exhaustive account status.

## Global policy effect and unresolved decisions

Current policy and required-source configuration are **global**, not per-account.
`Collector` uses one `admission_policy` and one required-source tuple for every
account. `Store.claim` applies it transactionally. There is no existing
"enforce accepted thangma, observe the other three" selector in the shared collector.
Phase 8's per-account activation wording requires a coordinated implementation or
explicit revised rollout scope; separate independent DBs do not supply coordination.

| Account | Current supported discovery | Under global enforce |
| --- | --- | --- |
| thangma | fresh partial on successful visit; never full | `required_coverage_incomplete:browser_discovery`; expiry/errors add reasons |
| annalynhi | unsupported in worker and ingestion allowlist | `required_source_missing:browser_discovery` plus account status reason |
| thanglong1996 | unsupported in worker and ingestion allowlist | same missing-source defer, even if a known production version polls terminal |
| thnhmngc | unsupported in worker and ingestion allowlist | same missing-source defer |

With auto enabled and an otherwise eligible due slot, enforcing claim persists
`skipped/activity_deferred`, no pushing reservation and no catch-up submission.
Readiness/disabled/missed/busy precedence can yield those other skip reasons first;
they do not establish adequate coverage. Polling/reconciliation of existing work
continues; enforcement neither cancels external jobs nor blocks all external producers.

Conflicts needing explicit owner decisions:

- #18/#23 require incomplete evidence to defer, while preserving existing automatic
  sampling in observe-only allows launches despite that diagnostic. Name and accept
  the operational risk, or disable automatic probes; do not call observe-only gating.
- Full-coverage evidence is required but no current accepted producer can supply it.
  Changing required sources to production polling or trusting a synthetic `full`
  Store write does not resolve this. Store accepts trusted adapter assertions; it
  does not contain a provider completeness proof verifier.
- Per-account onboarding/activation is planned; policy and source allowlist are
  currently global/thangma-only respectively. Need agreed unsupported-account impact.
- Short-pilot resource/restart acceptance and operational release are separable from
  exhaustive coverage and natural auth lifecycle. Passing one does not close another.
- Live #18/#22 bodies retain 4GiB/2CPU wording; later comments/Phase 7 record explicit
  selected 31.21GiB/8CPU host. Use the later scoped decision, not VM capacity claims.
- Deny every retained source error/unresolved identity is conservative but can block
  indefinitely. Specify authenticated resolution/recovery and stale-ID reconciliation,
  never age-based automatic idle release. TTL/cadence tradeoffs also need agreement.

## Feasible read-only operator validation plan

These are **operator-owned next actions**, not actions executed by this audit.
Coordinate sole ownership of browser/auth state with the ongoing deployment; use
only naturally existing events. If a needed running/queued/interactive/long-list
case does not exist, mark its cell untested rather than creating GPU work.

1. **Deployment evidence without changing it:** return sanitized exact image IDs
   and source hashes, effective policy/auto/required-source values, registered
   account names, TTL, worker allowlist, actual resource limits and non-secret
   health/checkpoint timings. Read existing summary and existing operator artifacts;
   do not dump env credentials, cookies, full HTML, DB contents or logs. Compare
   observation and attempt time, persisted coverage/error and admission reasons.
2. **Passive paired inventory:** when sole browser ownership is available, compare
   an independently inspected existing account activity/session inventory to the
   worker sample as close in time as possible. Record every visible event class,
   raw status segment, ref/version/script ID or absence of stable identity,
   container/image, UTC interval and source. Include naturally existing running,
   pending, terminal, interactive/editor and unknown rows, across more than one
   notebook, including same version number/title. No Run/Save/Start/Stop controls.
   A second UI view is corroboration, not authority unless its scope is established.
3. **Delayed/long-list challenge:** save sanitized minimal row/locator fixtures and
   metadata at initial render, after hydration, scroll steps and each existing
   pagination page. Record scroll container, loaded/total counts if provided,
   continuation tokens/end markers, repeats, reordered rows and omitted rows.
   Compare after8s, after12s and a separately authorized longer read-only observation
   within the operator's agreed bounded visit budget. If the production worker's
   deadline cannot enumerate the list, the outcome is partial/failure, not full.
4. **Resolve exact IDs read-only:** for sampled existing rows, compare popup
   destination owner/ref/scriptVersionId with independently opened version metadata
   and version-qualified official status. Treat the helper's requested-version echo
   as protocol evidence only. Native sessions require their own provider identity
   and terminal lifecycle; do not reuse scriptVersionId. Existing unattached runs
   can be checked directly read-only without writing a production attachment.
5. **Production observation history:** operator captures at least a full day of
   scheduled-cycle timestamps/resource/error summaries spanning natural workload
   changes, plus natural auth renewal/expiry when it occurs. One day is a proposed
   operational minimum, not completeness proof or expiry acceptance. Compute actual
   per-account observation gaps, max age, failures, omissions and false-negative
   mismatches; corroborate provider state rather than just counting HTTP200s.
6. **Policy shadow replay offline:** feed sanitized operator observations into an
   isolated local SQLite test store with no transport. Reproduce running/pending,
   unknown, missing, incomplete, expired, failed auth and restart cases for all four
   accounts; inspect decisions without claims against production or GPU launches.
   Existing fixture tests already establish local policy, not real coverage.

What would be sufficient for **full evidence** requires more than zero mismatches:
an explicit agreed universe of account activity types and ownership scope; an
authoritative provider enumeration mechanism covering that universe (or documented
disjoint authoritative inventories); stable identity and state semantics for every
class; complete continuation traversal/end proof with count reconciliation; handling
of concurrent list mutations and truncation/deadline failure; and a freshness budget
supported by actual cadence. Any unsupported class, incomplete traversal or unknown
completion must prohibit full. A finite collection of empty or terminal panels
cannot establish that provider-wide guarantee. If no read-only authoritative source
exists, full remains unavailable and the policy must continue to acknowledge that.

## Final enforcement go/no-go checklist

Coverage-qualified GO requires **all** of:

- Scope/completeness mechanism above reviewed and implemented under coordinated
  ownership; the versioned worker/API contract legitimately supports full with
  verifiable completion metadata, not just changing a string to bypass validation.
- Both reproduced identity/status defects fixed and tests promoted from strict
  xfail to pass; delayed/hidden/unversioned/paginated events fail safe, with live
  positive-event and exhaustive-boundary evidence for every admitted account.
- Account-specific identity/auth/recovery and relevant operational gates accepted;
  exact production image and ingestion/polling integration qualified by operator.
- Explicit global versus per-account policy, unsupported-account behavior,
  source/TTL/cadence, source-error recovery and acceptable external-submission race
  documented and approved. Absolute provider-wide atomic exclusion is not promised.
- No-push shadow decisions demonstrate defer for every adverse case, accept only
  legitimately fresh full idle evidence, preserve reservations through restart,
  retain per-account isolation and provide a scoped rollback. Activation itself
  must be operator-recorded live evidence; no GPU test launch is required here.

**Current outcome: NO-GO.** Continue partial observe-only with the explicit auto-probe
decision above. If reliable account-wide idle can never be established with available
read-only sources, either keep probes disabled/fail-closed or obtain an explicit
different risk-based admission policy through a separate review. Neither is evidence
that the current full-coverage gate passed.

## Local verification and handoff

New dedicated tests exercise real contract/persistence/admission with synthetic DOM
protocol fixtures: delayed arrivals, reordered equal-version refs, bounded virtual
pages, 100/101 limit, duplicate ID aliases, unversioned omissions, positive replacement,
four-account policy outcomes and healthy cadence/TTL gaps. Two strict expected
failures preserve desired behavior for the reproduced cache/status defects; these
are **open defects**, not passed acceptance cases.

Commands/results:

```text
uv run --no-project --with-requirements monitor/requirements.txt python -m pytest monitor/tests/test_coverage_phase10.py -q
25 passed, 2 xfailed (4.40s)

uv run --no-project --with-requirements monitor/requirements.txt python -m pytest monitor/tests -q -k "not test_ingestion_wall_deadline_terminates_stalled_http_process"
353 passed, 2 skipped, 1 deselected, 2 xfailed (31.27s)
```

The existing stalled-HTTP test was deliberately deselected to honor the no-server
operations constraint, including localhost test listeners. Two existing Linux-only
process-tree tests skip on Windows. One existing Starlette/AnyIO deprecation warning.
In-process FastAPI TestClient tests bind no server socket. `git diff --check` passed
with existing CRLF notices. No live gates closed; no application/worker/Compose edits,
GPU actions, commits or pushes. Operator owns deployment evidence and any fixes.
