# Phase 11 — production discovery fix and resilience verification (#18 / #22 / #24)

2026-10-16 local / 2026-10-15–16 UTC. Sole live operator; workspace
`C:\local\kaggle_test`. Read `PHASE-8-PRODUCTION.md`, `PHASE-9-SOAK.md` and
`PHASE-10-COVERAGE-AUDIT.md` for the pre-fix deployment, soak start and audit.

## Outcome

**The production browser-discovery blocker is fixed and verified.** The running
worker now produces accepted `partial` observations on every scheduled visit
instead of failing on the interactive row. Production remains `observe_only` with
`AUTO_PROBES=false`. #19/#23 were already closed; **#22 is closed** on this
evidence. #21 and #24 remain open (see Remaining gates). No GPU launch/cancel,
commit or push is implied by this phase.

## Diagnosis

The deployed images predated the reviewed fix. Comparing the running images with
the uploaded source showed only two files differed:

- Deployed worker `discovery_visit.py` had no structured row sampler and raised
  `panel_unavailable`/`invalid_event_identity` on the interactive row.
- Deployed `discovery_contract.py` (in **both** the monitor and worker images)
  rejected `ui-observation` rows (`identity` had to match `script-version:…`).

`app.py` (`1158c449…`), `requirements.txt`, `roadmap.json` and the built frontend
asset hashes were already byte-identical to local, so the monitor rebuild was a
no-op except for the shared contract. The Phase 8 deployment had shipped new
`app.py` with an old `discovery_contract.py`, which is why the worker could never
reach a positive partial success.

## Fix

- `discovery_contract.py`: added `ui_observation()` and a structured branch in
  `observation()`; `validate()` now accepts `ui-observation:<sha256>` rows with
  `run_type in (interactive, unknown)`, `state == unresolved`, no invented
  ref/version. Script-version rows are unchanged.
- `discovery_visit.py`: `ROW_SAMPLE_JS` + `collect_panel` structured sampling;
  interactive/0-version rows become explicit `ui_observation` (unresolved) rather
  than a panel failure.
- `monitor/tests/test_discovery.py`: fixed a stale test that unpacked the
  `analysis_client` fixture as a tuple (`test_ui_unknown_through_ingestion_persistence_summary_and_enforcing_defer`).

Live read-only reproduction (`phase11_collect.py`) against the current panel:
`collect_panel_ok: true`, one observation `ui-observation` / `interactive` /
`Cancelled` / `unresolved`.

## Build and deployment

Built on the server from the allowlist-uploaded source (digest-pinned Phase 7
bases, exact requirements/npm lockfile). Guarded non-destructive swap via the new
`update-images` operator action, which asserts the monitor's only delta is
`discovery_contract.py` and verifies worker source hashes before recreating.

| Service | Old image | New image |
| --- | --- | --- |
| monitor | `sha256:c45e0f866dab9ef3316bf6b89a8002a9503978d3fe841f848693ea265d33ce3a` | `sha256:bffeb07cba2b30c258f2abda0d757ce012269deba551c9c2302f1e4792b27020` |
| discovery | `sha256:a49929094f2ee3a95e19ae2dd5213b33df66b49f728a5ed6297c8d82722fe8c9` | `sha256:4e6d03d5ff40224da98fb257e7ab7f54a08ef0934ae97a161c5de44723b0bdf6` |

Pre-swap DB backup: `/home/thangma/kaggle-production-phase8/backup-pre-update-20261015T224943Z/`.
Deployed module hashes: `app.py 1158c449…`, `discovery_contract.py d3bb2f2c…`,
`discovery_visit.py d2cf36da…`, `discovery_worker.py dc4a1f5b…`.

## Verification (production, observe-only)

- **Worker healthy:** `last_result: partial`, `ingestion: ok`, `failures: 0`,
  180–300s cadence; 15+ consecutive successes post-swap.
- **Monitor semantics:** thangma `partial` (never idle) with
  `required_coverage_incomplete:browser_discovery`; other accounts
  `required_source_missing`; all `would_defer`; `auto_probes=false`.
- **Resources (bounded soak, 99 samples):** discovery peak 512,278,528 B
  (~488 MiB) / 100 PIDs, monitor 118,870,016 B (~113 MiB) / 13 PIDs, OOM kills 0,
  host min available 25.9 GiB; worker returns to ~16 MiB / 2 PIDs.
- **Restart persistence:** monitor restart preserved all 158 observations and
  `observed_at`; worker restart reused disk auth and produced a fresh partial
  success with no new submission or SDK call.
- **Natural 300s TTL expiry:** fresh → stale; `required_source_stale:browser_discovery`;
  same observations/`observed_at`; no data loss.
- **Faults (production image, no monitor writes):** hung browser → `visit_timeout`
  7.085s; crashed leader → `worker_crash` 1.039s; empty/invalid synthetic auth →
  `authentication_required` (coverage none); zero descendants in all cases.
- **verify-final:** images pinned, source hashes match, history retained
  (3713 calls, 621 submit attempts unchanged, no new submissions).

## New tooling

- `phase11_collect.py` — read-only live-panel reproduction of the sampling path.
- `phase11_faults.py` — production-image supervision/auth faults, no ingestion.
- `phase8_operator.py` actions: `update-images`, `faults`, `restart-persistence`,
  `ttl-expiry`.

## Issue actions

- **#22 closed** on the production thangma-scope acceptance evidence above.
- **#21 commented, kept open** — local acceptance passes (33 tests) and the live
  collector wiring is present (`readiness.version_requests=true`, SDK 2.2.4,
  `POST /api/production/attach` validates body); no registered production run or
  owning-account credentials exist to exercise exact-version polling.
- **#24 commented, kept open** — resilience/resource/rollout evidence recorded;
  the 48h soak window has not elapsed and all-four-account evidence is blocked by
  the #20 deferral.

## Remaining gates

- Other accounts `thanglong1996`/`annalynhi`/`thnhmngc` — deferred to the final
  stage by the user (login unavailable now); #20 stays open.
- #21 live exact-version provenance and the thanglong1996 v3 / scriptVersionId
  `358145984` mapping.
- Elapsed 48h soak and host-reboot/OOM recovery.
- Coverage-qualified **enforcement remains NO-GO** (`PHASE-10-COVERAGE-AUDIT.md`).
