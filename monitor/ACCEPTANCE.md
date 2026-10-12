# Deployment evidence — 2026-10-12

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
