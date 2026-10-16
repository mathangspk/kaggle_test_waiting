# Account activity evidence v1 — issue #19

Adapter entry point: `Store.record_activity(account, source, observations, coverage,
observed_at, *, error=None, auth_error=None, stale_after_seconds=300)`.
This is persistence/summary infrastructure only; adapters and admission follow in
#21–#23. No browser work occurs in dashboard requests.

- Account is the lowercase Kaggle username; source is a stable collector ID
  (e.g. `browser_discovery`, `production_api`). Each write replaces that source's
  account snapshot atomically. Use `coverage=full` ONLY for exhaustive account
  discovery, `partial` for known-run polling/event panels, `none` for failures.
- Observations contain `identity` (stable event/run ID), `run_type`
  (`probe`, `production`, `interactive`, `unknown`), `provider_state` (raw),
  `state` (`running`, `pending`, `unresolved`, `terminal`), `observed_at`
  (UTC ISO string), optional `ref`, `version`, `session_id`, `gpu_requested`,
  `gpu_verified_devices`. Never treat scriptVersionId as a session ID or
  requested accelerator as verified hardware. Account/source/coverage/freshness
  and `stale_after_seconds` are attached by the summary. Legacy tracked records
  may have null provider state/timestamp; no timestamp is invented.
  Do not include credentials or provider HTML.
- `observed_at` argument is epoch seconds, the collection attempt time.
  Errors are sanitized codes. Failed writes retain the previous observations and
  success timestamp, but record attempt time/error and coverage `none`.
- `/api/summary` keeps schema_version 1 (additive fields), with
  `activity_contract_version=1`. Account `activity` includes `status`, `coverage`,
  `observed_at`, `sources`, and `observations`. Each source preserves success and
  attempt timestamps, errors, coverage, TTL and freshness. Freshness is computed
  at summary time, never from HTTP fetch time. TTL boundary is inclusive.
- Account `status`/`state` mean account activity. `probe_status`,
  `probe_active_runs`, legacy `active_runs`, `blocked` and `active_run_id` remain
  probe-only; `blocked` is NOT admission based on this contract yet.
- Fresh positive evidence yields running/pending; unresolved identities always
  prevent idle. Stale positives become stale, retained errors become unknown.
  No positive evidence yields observed_idle only with fresh full discovery and
  no failed source, stale nonterminal observation, or unresolved tracked record.
  Otherwise report partial, stale or unknown. Completed probes cannot prove idle.
- Existing probe/production records are exposed as partial tracked evidence;
  production registration is unresolved even though its legacy active=False.
  Artifact verification and execution status are separate concerns.
- The API includes up to the recent terminal-history window and all unresolved
  production registrations (even outside that window). Overview renders at most
  10 observations per account, prioritizing nonterminal evidence, and labels
  omitted counts. Account status is computed before this display limit.
- Polling adapters must update the corresponding production/probe record as well
  as their snapshots when an identity resolves; a retained unresolved registration
  intentionally blocks idle even if a different source reports an empty panel.
  Snapshot errors retain observations until the next successful same-source write.
- Frontend keeps evidence on HTTP errors but displays account status unknown;
  freshness also expires locally from observation time during failed fetches.

Consumers must preserve evidence fields and must not infer admission from legacy
probe counts. Manual submissions can race even fresh discovery.

## Production adapter additions — issue #21

- Known-run polling uses stable per-event sources `production_api:<event_id>`.
  Each source covers exactly one attached/registered version with `partial`
  coverage. This avoids replacing evidence for other versions or accounts.
  Production record, snapshot, retry deadline and fairness state are committed
  atomically. A snapshot with observations supersedes duplicate tracked evidence;
  failed first attempts still retain the unresolved tracked registration.
- Production registration/attachment timestamps are local metadata, never provider
  observation times. Unobserved tracked production has null `observed_at`.
- Attachments supply caller-selected owner/ref/version. `script_version_id` is
  optional, unverified caller metadata; `script_version_mapping_verified=false`.
  It never becomes `session_id`, and an exact vN status read does not independently
  verify the supplied scriptVersionId mapping.
- Production `state=success` can mean terminal complete execution without verified
  timing. `execution_provenance=provider_exact_version_status` is separate from
  `evidence_provenance=provider_artifact`; only the latter qualifies timing metrics.
  No execution status synthesizes start/end/queue times or verified GPU hardware.
- The timestamp validator allows at most one microsecond of ISO serialization
  rounding; this does not extend the freshness TTL or allow future observations.

## Admission consumer additions — issue #23

- Summary account `admission` is separate from legacy probe-only `blocked`.
  It contains `policy`, `would_defer`, `reasons`, `evaluated_at`,
  `activity_status`, and `required_sources`. Summary meta exposes
  `probe_admission_policy` and `required_activity_sources`.
- Default policy is explicit `observe_only`. `enforce` requires fresh full
  coverage from every configured required exhaustive source (default
  `browser_discovery`), observed idle, and no unresolved/nonterminal or failed
  account evidence. Admission reads complete local records transactionally,
  independent of summary display limits. Invalid configuration does not fall open.
- Per-slot decisions are persisted on local probe opportunities, without making
  external evidence into probe samples. See `PROBE-ADMISSION.md` for rollout,
  concurrency limits and acceptance blockers. No atomic Kaggle-wide lock exists.

## Browser discovery ingestion — issue #22 / Phase 5

- `POST /api/activity/browser-discovery` uses a separate `X-Discovery-Token`, read
  from external `DISCOVERY_INGEST_TOKEN_FILE`. Disabled when unavailable; production
  ingestion credentials do not authorize this endpoint or vice versa.
- Fixed source `browser_discovery`, registered `thangma` pilot only, <=64 KiB,
  <=100 observations, strict allowlisted fields and errors. Only partial success
  or none+sanitized failure is accepted; full coverage is deliberately rejected.
- Rows use `script-version:<positive ID>` with matching thangma ref, positive
  version, unknown run type and normalized provider state. This is browser-asserted
  evidence, not independently verified native session identity or GPU hardware.
- Attempt timestamps must not be future or older than 24h; TTL fixed at 300s from
  observation, not receipt. Failed attempts retain prior observations/success
  timestamp. Out-of-order persistence returns `accepted=false`.
- No browser executes in the API process. Delayed rows, panel text, footer zero,
  stability and scroll bottom cannot certify full discovery or idle. See
  `PHASE-5-DISCOVERY.md` for opt-in configuration, checks and live acceptance gaps.
