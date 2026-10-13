# Kaggle monitor frontend — #12 / #15

React + TypeScript + Vite; local shadcn/ui components use Radix primitives.
Six Vietnamese pages: Tổng quan, Khung giờ, Lượt chạy, Lộ trình, Sơ đồ logic,
Vận hành. Hash routing supports direct links without backend route changes.
Desktop sidebar, accessible mobile Sheet, light/dark themes and Vietnamese
timestamps (Asia/Ho_Chi_Minh by default) are included.

## Build and checks

Node.js 22 or newer is required. The patched dependency baseline is Vite 6.4.4,
Vitest 4.1.11, React Router DOM 7.18.4 and Tailwind CSS/PostCSS 4.3.3.
Tailwind 4 explicitly loads `tailwind.config.js`, retaining the existing shadcn
colors, radii, class-based theme and animation plugin. See `DEPENDENCY_AUDIT.md`
for the advisory review and upgrade rationale.

```powershell
npm ci
npm run typecheck
npm test
npm run build
```

Output: `dist/index.html` and `dist/assets/`. Vite asset base is `/static/`.
All fonts, icons, CSS and JavaScript use local/system resources; no CDN.
This build does not copy files into the backend's static directory.

Optional local build preview: `npm run preview -- --host 127.0.0.1 --port 4173`.
Open `/static/`. APIs use the same origin; preview alone has no backend API.

## Read-only API integration

- `GET /api/analysis?source=probe|production&day_type=all|weekday|weekend&account=<optional>`:
  schema v2, metric `submit_to_start`, `generated_at`, `definitions`, `policy`,
  `buckets`, `accounts`, `sampling`. Account is omitted for all accounts.
  Windows use local even `hour_start` values 0, 2, …, 22; rates are fractions
  in [0, 1], durations seconds, counts nonnegative integers.
- `GET /api/decision?mode=earliest|schedule&horizon_hours=6&source=probe|production&account=<optional>`:
  `status: abstain|tentative`, `reason`, nullable `recommended_at`, `certainty`,
  `promise: false`, `candidates` with `at`, `local_at`, `day_type`,
  `waiting_seconds`, `total_p50_seconds`, `total_p90_seconds`, nested `bucket`.
  Horizon choices: 1, 3, 6 (default), 12, 24 hours. The backend also accepts
  `day_type`; the panel intentionally omits it (default all) and uses each
  future candidate's actual weekday/weekend evidence, independently of analysis.
  There is no submission action.
- `GET /api/summary`: `meta`, `hours` (or `hourly`), `accounts`, `runs`,
  optional `statistics`, `definitions`, `api_calls`, `minimum_rank_days`.
- `GET /healthz`: JSON `ok` boolean. HTTP 503 health bodies are displayed as
  unhealthy worker information, rather than discarded as transport failures.
- `GET /api/roadmap`: top-level `{repo, synced_at, issues, dependencies, blocked}`.
  Older `{available, reason, snapshot}` envelopes are also supported. Issue
  details read `body`, `url`, `updatedAt`; blocked IDs compare as strings/numbers.
- `GET /api/logic`: `{nodes:[{id,label,description}],
  edges:[{source,target,label}], counts:{[state]:number|null}}`.
  Older `from`/`to` edges, node occupancy and structured `occupancy`/`counts`
  are also supported. Backend counts take precedence over node counts.

Logic graph supports pan/zoom and mouse/keyboard node detail selection. The
full text node list and transition list provide an accessible fallback. Cycles
and self-loops are preserved without an unbounded layout traversal. Occupancy
is never calculated from the limited recent runs list. Active/terminal flags
come from backend node metadata or backend state lists when available.

Each endpoint polls every 15 seconds with a 12-second request timeout,
AbortController cleanup and overlapping-request protection. Obsolete responses
from an earlier mount cannot overwrite current data. Refresh errors retain the
last successful in-memory snapshot; errors and stale flags remain visible.
Snapshots do not persist across a full reload. Pages show both the successful
fetch time and backend timestamp; missing backend timestamps remain unknown.
Without a backend freshness threshold, page snapshots use a 45-second threshold.
Roadmap uses a separate 24-hour threshold measured from the packaged snapshot's
`synced_at`/`syncedAt`, not its latest API fetch or the collector heartbeat.
Its labels distinguish static deployment snapshots from runtime collector data;
an absent sync time remains unknown and a fetch error retains the snapshot.

Unknown metrics are displayed as unknown, never replaced with sample data.
Queue lower/upper bounds are separate from submit-to-start measurements.
Queue is a collapsed, secondary diagnostic from the unfiltered summary.
No legacy ranking or pooled raw-count probability is used in Overview/Hours.
Run account/state filters support `account_id`/`account` and
`status`/`state`. Overview shows account `next_probe_utc`. Operations displays
health/summary errors independently and shows recorded API errors.

### #15 adapter assumptions

`src/lib/analysis.ts` adapts the actual `app.py` analysis/decision builders.
Missing/null/invalid numeric values remain unknown, including account samples.
Backend `success_rate`, `rate_lower`, `rate_upper` are preserved, including null;
no pooled-count fallback is allowed. Rates/bounds average configured accounts
with equal weights. An account without evidence makes the point unavailable
and contributes [0, 1] to identification bounds. Pending/overdue are subsets of
unknown, not additional mutually exclusive outcomes. No Wilson interval is manufactured in the browser; supplied
Wilson bounds are labelled preliminary with an explicit independence assumption.

Eligibility needs backend `eligible === true`, at least 20 resolved and 7
**resolved** local dates for each selected account in each window (or stricter
backend policy), complete coverage and valid nested account evidence.
Partitions must satisfy attempted = resolved + unknown and resolved = fast +
slow + over10 + failures; pending + overdue <= unknown. Resolved dates cannot
exceed observation dates or resolved counts. Nested counters must sum to the
bucket counters. Known successful timings require median <= P90; failure-only
evidence may have null timings but cannot support a timed decision.
Only equivalent source/account/day groups should be
compared; windows are displayed chronologically, without ranks.
Account summaries accept `id`, `account`, or `account_id` plus optional
`name`/`account_name`, `attempted`, `resolved`, `day_count`.

Decision adapters resolve `recommended_at` to one candidate, use nested bucket
evidence, and derive the display baseline from the zero-`waiting_seconds`
candidate. Status `tentative` is displayed as a preliminary historical comparison,
never certainty or a promise. All candidates need valid eligibility and known
timings; totals must agree with wait + start. Earliest mode requires an eligible
current baseline; schedule mode can compare future candidates without one.
Unknown status or unmatched recommendation timestamps abstain conservatively.
Decision metadata `selection_basis: descriptive_p50_p90_pareto`,
`limitations: [no_latency_confidence]`, and `certainty: descriptive_only` is
preserved and explained. The backend selects only a candidate whose total wait
+ P50 and wait + P90 are each at least 30 seconds better than every alternative.
`latency_tradeoff`, `equivalent_total_latency`, `insufficient_evidence` and
`missing_baseline` have Vietnamese labels; unknown reason codes remain visible
as unknown backend reasons, never silently mapped to insufficient evidence.
Current-builder aliases `tradeoff`/`equivalent` are also labelled while preserving
their original codes. `sole_eligible_candidate` explains there is no alternative
to compare. `material_margin_seconds` and every limitation are preserved;
known limitations have Vietnamese explanations and new ones retain their raw code.
These distributions exclude failures and unresolved outcomes, and are not
statistical predictions. Outcome-rate intervals/Wilson do not quantify timing
confidence or gate frontend selection; the frontend does not choose a candidate.
Changing any query filter aborts the old request and clears its displayed
snapshot; a failed refresh within the same query retains the last success.
Production never falls back to probe evidence.

Sampling is explained in plain language, using `sampling.interval_minutes`:
one planned opportunity per account per sampling window. With four configured
accounts, the plan permits at most four total opportunities per selected window
and `4 * 1440 / interval_minutes` daily opportunities for full-day coverage.
These are opportunities, not guaranteed launches. The sampling window is distinct
from the two-hour analysis bucket. Missing/invalid interval has no inferred quota.
Production describes external submissions separately and never applies probe
scheduler opportunity limits.
The preliminary held-out summary explains at least seven training and seven
separate test dates, backend availability/reason, selected windows, equal-account
selected/baseline rates and bounds, and observed percentage-point difference.
It makes no causal savings or counterfactual latency claim and is not raw JSON.

## Verification

Automated tests cover both API shapes, occupancy/unknown semantics, unhealthy
health responses, malformed/error responses, VN timestamps, graph cycles,
15-second polling, offline recovery, timeout/unmount abort, overlap protection
and StrictMode response races.

#15 tests additionally cover resolved denominators, optimistic/pessimistic bounds,
pending-only evidence, unknown account samples, minimum coverage, preliminary
Wilson preservation, conservative decision abstention, exact encoded endpoints,
production emptiness and filter-change snapshot isolation.

`backend-contract.test.ts` invokes `tests/backend-fixtures.py` (Python 3 required)
to execute only the actual pure builders extracted by AST from `../app.py`.
No app imports, server, collector startup, database, or backend writes occur.
Generated cases verify 100/100 versus 0/20 accounts stays 50% (not 83.3%),
missing-account null stays null (not 100%), resolved-date coverage, real tentative
timestamp recommendations, future waits, descriptive Pareto dominance despite
overlapping rate intervals, latency tradeoff and equivalent-latency abstention.

Browser smoke checks against the production preview used contract fixtures
(test-only, not shipped): all six pages, rank eligibility, filters, run bounds,
Radix dialogs, graph node mouse/keyboard interaction and zoom, mobile navigation,
theme switching, mobile overflow and offline snapshot retention. Actual backend
live HTTP responses were not exercised. The #15 adapter regressions now directly
exercise actual pure backend builders; other compatibility uses supplied contracts.
