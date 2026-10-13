# Deployment evidence — 2026-10-12

## Allocation semantics release — 2026-10-13

- Issues #14–#17 implement actual-submission 2-hour buckets, distinct pending/
  unknown/failure outcomes, account-weighted rates, source filters, minimum
  resolved-date coverage and uncertainty diagnostics.
- Primary visuals now use submit-to-start, with queue bounds secondary.
  Descriptive decisions compare waiting plus P50/P90; conflicts or insufficient
  evidence abstain. Probability bounds never stand in for latency confidence.
- Production wrapper supports immutable input by default, optional temporary-copy
  instrumentation, durable one-push receipts, upload-only retry and explicit
  authenticated exact-version provider artifact verification. Real generated
  artifact integration is covered with mocked provider transport; no production
  job launched and no live production timing falsely claimed.
- Final checks: 209 Python tests (178 backend, 31 wrapper) passed in deployed
  Python 3.12; frontend 59 tests/typecheck/build passed. Independent QA confirmed
  fixes for weighted-rate, decision-contract, artifact nonce and receipt identity
  mismatches. Sampling interval supports 30/60/120 minutes, default unchanged.
- Online SQLite backup checked before upgrade. Live analysis and decision APIs
  healthy, historical samples retained, production source empty and unranked.
- Source publication is authorized for this release; credentials and runtime
  data remain excluded from Git.

## Roadmap release — 2026-10-13

- Issues #11, #12 and #13 completed and the packaged roadmap synchronized.
- React/TypeScript dashboard serves six Vietnamese pages via the existing 8443
  Tailscale URL. React Flow is read-only and exposes state descriptions, actual
  occupancy and accessible alternative node controls. Mobile 390px viewport and
  all page routes verified in the live browser.
- 88 backend tests passed in the deployed Python 3.12 container; final edge-label
  clarification also passed all 88 tests locally. 20 frontend tests, TypeScript
  and production build passed. Docker's final npm install reports zero audited
  vulnerabilities after reviewed dependency upgrades.
- Guarded readiness retry, worker watchdog, next-window schedules, bounded
  summary queries/cache and durable push outcome recovery implemented. Independent
  QA fault injection found a terminal-rejection reactivation bug; regression fixed.
- A consistent online SQLite backup passed integrity checking before final
  upgrades. Persistent production history retained; final health confirms worker,
  watchdog and SDK ready, four accounts loaded and 179 submitted samples at check.
- Cancellation remains unsupported pending #4 native session identity evidence.
- Release source is local and deployed, with publication authorized; credentials
  and runtime data remain excluded from Git.

## Verified

- GitHub roadmap #10 and implementation issues #1–#9 created.
- SSH password authentication succeeded; dedicated Ed25519 public key installed
  without replacing authorized keys. Key-only authentication succeeded.
- Docker Compose service deployed at `/home/thangma/kaggle-queue-monitor`.
- `/healthz`: worker alive, SDK 2.2.4 ready, four accounts loaded, automatic probes enabled.
- Tailscale HTTPS dashboard opened through a browser, with live health/summary.
  Existing HTTPS port 443 continues to proxy port 8081; dashboard uses 8443.
- 49 tests passed in deployed Python 3.12 container. Independent QA checked
  scheduler, restart persistence, concurrency guards, source/version identity,
  credential isolation, API responsiveness and UI schema. Found defects fixed.
- Actual probe artifact checks have confirmed two Tesla T4 devices and UTC
  start/end timestamps. Early probes did not always expose queued during polling;
  those queue durations correctly remain unknown rather than zero.
- Service recreated during upgrades without losing run history. Ambiguous pushes
  reconciled via full source nonce and exact version, without resubmission.

## Cancellation blocker — issue #4

Official `kagglesdk==0.1.37` exposes
`KernelsApiClient.cancel_kernel_session(ApiCancelKernelSessionRequest)` and accepts
`kernel_session_id`. The response includes `error_message`. However, the installed
public push, metadata, status and output contracts do not resolve the exact batch
version into that native ID. Browser viewer run IDs have not been verified as
native session IDs. No speculative cancellation is performed. Current-token
authorization and cancellation acknowledgement remain untested.

Installed Kaggle 2.2.4 status wrapper omits supplied versions. Collector instead
uses explicit SDK `version_label="v<N>"`, validated against official upstream
version regression tests and live exact-version artifact retrieval.

## Statistical qualification

Rankings are withheld until at least three observed dates; even then they are
preliminary. 2–4 weeks of collection are needed to evaluate weekday/hour coverage.
The service measures allocation experience, not total available GPUs on Kaggle.

## Source publication

Source repository: https://github.com/mathangspk/kaggle_test_waiting
Code also exists on the deployed server. Credentials, databases and private SSH
keys are excluded from Git. No credentials were posted in issues.
