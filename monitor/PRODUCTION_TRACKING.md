# Production submission tracking (#16)

`track_production.py` is an explicit CLI wrapper for one real Kaggle
`kernels_push(folder, acc="NvidiaTeslaT4")`. Running it without `--dry-run`
can launch the supplied production notebook/script. It records submission
timing and registers metadata. Opt-in instrumentation can produce a timing
artifact, but **registration alone does not satisfy full verified execution
measurement: backend exact-version provider verification must complete.**

## Run

Install `monitor/requirements.txt`. Supply credentials through Kaggle's official
configuration/environment (`KAGGLE_API_TOKEN`, or the official legacy
`KAGGLE_USERNAME` / `KAGGLE_KEY` mechanism). The wrapper imports Kaggle only at
runtime, uses its authenticated API, and does not set environment variables,
read repository account files, or rewrite global credential configuration.
Choose credentials for the owner named by `--account`; metadata `id` must
exactly match that account as `account/slug`. Kaggle enforces credential
authorization; the wrapper does not independently verify the authenticated user.
The repository identity `monitoring4credentials` is always skipped before
reading credentials/environment. Dry-run also avoids credentials and imports.

Set `MONITOR_INGEST_TOKEN` externally to the backend's ingest token. Use
`--token-env NAME` to select a different environment variable. This is separate
from Kaggle credentials and sent only as `X-Monitor-Token`.

```powershell
python monitor/track_production.py --folder "C:\production\kernel" --account owner --receipt "C:\private\receipts\push-001.json" --dry-run
python monitor/track_production.py --folder "C:\production\kernel" --account owner --receipt "C:\private\receipts\push-001.json" --monitor-url http://127.0.0.1:3000 --accelerator NvidiaTeslaT4
```

`--folder`, `--account`, and a new `--receipt` path are required for pushes.
The receipt parent must already exist. Use a private directory outside the
repository, with an owner-only Windows ACL; on POSIX new files have mode 0600.
If receipts must live inside a checkout, add that directory to your local
`.git/info/exclude` before use. JSON is not inherently ignored or protected.
The wrapper does not edit ignore rules. Each intentional new push needs a new
receipt path; an existing path blocks a second push. Receipts inside `--folder`
are prohibited, including paths resolving into it through symlinks. This check
happens before authentication, receipt writes or pushes.

The folder must contain `kernel-metadata.json`, an exact unversioned `id`, and
an existing relative `code_file` contained within that folder (including after
symlink resolution). Scripts and v4 `.ipynb` notebooks are supported. Production
source and metadata are read-only to this wrapper. By default no instrumentation
or start timestamps are inserted. The official SDK may normalize notebook content in
its request in the usual way. The accelerator parameter explicitly selects T4;
it is a request, not verification of assigned hardware.

## Explicit instrumentation

Add `--instrument` to the push command to submit an instrumented temporary copy.
**This opt-in changes submitted execution**, while leaving every original file
unchanged. It supports Python scripts and Python v4 notebooks only. The bounded
copy contains only metadata and its known `code_file`, the two files the official
push API reads; it does not recursively copy datasets, ancillary receipts or
unknown files. Metadata is limited to 1 MiB and source to 16 MiB; metadata/source
symlinks and source-path symlinks are rejected. Provider dataset/kernel references
remain unchanged. Temporary copies are removed after the push attempt.

Python scripts are embedded unchanged into a generated wrapper in the copy.
The wrapper writes the original source into a temporary runtime file and calls
`runpy.run_path(..., run_name="__main__")`, preserving legal future imports.
This changes the script's `__file__` location; code relying on neighboring local
files must account for the provider's single-source upload behavior.
Before execution it captures UTC start and actual `nvidia-smi` GPU names
(never assumes two T4s). A `finally` block records UTC end and writes
`queue-monitor-timing.json` even if the script raises. Process termination or
artifact-write failure may still prevent output. A failed job's artifact is
not sufficient for successful timing verification.

Notebook copies receive explicit first and last code cells; original cells are
preserved. The first captures start/GPU data; the last writes the end artifact.
If execution fails before the last cell, end evidence is unavailable. Timing
includes the instrumentation overhead. Artifacts contain `run_id` equal to the
receipt's actual UUID event nonce, requested `ref`, `start_utc`, `end_utc`,
`gpu_names`, and `gpu_query_ok`; they are not uploaded directly by this CLI.

## Receipt and upload contract

A UUID event is reserved durably before the single push. After the call returns
or raises, a sanitized receipt is atomically replaced and fsynced before upload.
Only these fields are saved and posted to `POST /api/production/register`:

```json
{
  "event_id": "6f55b42a-74d4-4a81-838e-a627221f70c5",
  "account": "owner",
  "requested_ref": "owner/my-kernel",
  "ref": "owner/my-kernel",
  "version": 7,
  "submit_begin": "2026-10-13T10:00:00Z",
  "submit_ack": "2026-10-13T10:00:03Z",
  "accelerator": "NvidiaTeslaT4",
  "push_outcome": "accepted"
}
```

Times are UTC observations on the submitting machine, not provider execution
times. `submit_begin` is recorded just before durable reservation and the push;
`submit_ack` is when a provider response or definitive rejection is observed,
not proof of provider acceptance. Timeout/uncertain exceptions and interruption
retain null `submit_ack`. Accurate client clocks matter. `requested_ref` always
holds the exact desired metadata identity, independently of nullable observed
`ref`. An uncertain receipt can have both null observed ref and null ack.

Acceptance requires an explicit response `ref` exactly equal to the folder id
and a positive integer `versionNumber`. The exact returned version is retained.
A missing/empty ref remains null and **ambiguous even with a valid version**;
the folder id or a response URL is never substituted. Mismatched identity,
missing version, timeout and uncertain failures are ambiguous. Explicit provider
error responses or definitive HTTP 4xx exceptions (except 408/429) are rejected.
Raw API messages, SDK output, credentials and monitor response bodies are not
written to logs or receipts.

There is one wrapper push attempt and no wrapper retry. The installed provider
SDK/transport controls its own internal behavior. An ambiguous outcome must
not trigger another push: externally reconcile with read-only provider metadata
and source comparison if needed, without rewriting production content. Backend
registration and any future observations are separate concerns.

Failed uploads leave the receipt intact. Replay uploads the same event UUID
without importing Kaggle or pushing:

```powershell
python monitor/track_production.py --upload-receipt "C:\private\receipts\push-001.json" --monitor-url https://monitor.example
```

Remote monitor origins require HTTPS. Redirects are not followed. Registration
must implement the contract above and handle repeated event IDs idempotently;
this utility does not add or verify backend endpoint behavior. Receipts are
unsigned client metadata, not authenticated provider execution evidence. There
is no artifact import option.

Exit codes: `0` accepted/uploaded, verification request completed, or dry-run/skipped;
`2` ambiguous/rejected registration uploaded; `3` upload/verification request
failed (receipt preserved); `1` validation,
authentication or persistence failure. After any failure inspect the existing
receipt before proceeding; a process kill or disk failure can leave uncertain
submission status.

## Integration gap and verification

After provider execution, explicitly request backend verification:

```powershell
python monitor/track_production.py --verify-receipt "C:\private\receipts\push-001.json" --monitor-url https://monitor.example
```

This reads the local receipt without changing it and sends only `{ "event_id":
"<receipt UUID>" }` to `POST /api/production/verify` with `X-Monitor-Token`.
It does not import Kaggle, push, upload client timing, or read provider artifacts
locally. The backend agent implements official read-only exact-version status,
source/nonce identity and artifact retrieval. Backend verification must require
provider status `complete` and validated artifact evidence before reporting
successful verified metrics; a script's `finally` artifact on failure is not
success evidence. HTTP success means the request completed, not that verification
succeeded: consult backend verification state.

This utility has no automatic collector or scheduled polling. Neither acceptance
nor acknowledgement measures queue wait, actual start, execution end, or verified
hardware. Full job timing remains partial until verification is explicitly run
and the provider artifact is present and validated for the exact version. Runs
without instrumentation may have no usable artifact. Missing notebook final-cell
output or uncertain identity must remain unverified. Backend integration is
external to these files; these mocked tests do not establish end-to-end provider
verification. There is no user-supplied artifact import option.

The focused tests mock Kaggle import/API and monitor transport, using synthetic
accounts and credentials. Instrumentation tests execute synthetic scripts locally
with the GPU subprocess mocked; they do not launch kernels or access real accounts,
network or GPU hardware.

```powershell
python -m pytest monitor/tests/test_production_tracking.py -q
```
