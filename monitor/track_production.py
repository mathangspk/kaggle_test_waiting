"""Explicit, single-push production submission tracking (no execution polling)."""

import argparse
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sys
import tempfile
from urllib.parse import urlsplit
import uuid


REF = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9_-]+\Z")
FIELDS = {"event_id", "account", "requested_ref", "ref", "version", "submit_begin", "submit_ack",
          "accelerator", "push_outcome"}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_folder(folder, account):
    folder = Path(folder).resolve(strict=True)
    metadata = json.loads((folder / "kernel-metadata.json").read_text(encoding="utf-8"))
    ref = metadata.get("id")
    if not isinstance(ref, str) or not REF.fullmatch(ref) or ref.split("/")[0] != account:
        raise ValueError("Metadata id must be an exact account/slug matching --account")
    code = metadata.get("code_file")
    if not isinstance(code, str) or not code or Path(code).is_absolute() or PureWindowsPath(code).drive:
        raise ValueError("code_file must be a relative source path")
    if ".." in code.replace("\\", "/").split("/"):
        raise ValueError("code_file cannot traverse outside the folder")
    source = (folder / code).resolve(strict=True)
    if not source.is_relative_to(folder) or not source.is_file():
        raise ValueError("code_file must exist inside the folder")
    kind = metadata.get("kernel_type")
    if kind == "notebook":
        notebook = json.loads(source.read_text(encoding="utf-8"))
        if source.suffix != ".ipynb" or not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list) or notebook.get("nbformat") != 4:
            raise ValueError("Notebook source must be a valid v4 .ipynb")
        for cell in notebook["cells"]:
            if not isinstance(cell, dict) or cell.get("cell_type") not in {"code", "markdown", "raw"}:
                raise ValueError("Invalid notebook cell")
            text = cell.get("source")
            if not isinstance(text, str) and not (isinstance(text, list) and all(isinstance(line, str) for line in text)):
                raise ValueError("Invalid notebook cell source")
    elif kind == "script":
        if source.suffix.lower() not in {".py", ".r", ".rmd", ".jl"}:
            raise ValueError("Unsupported script source")
    else:
        raise ValueError("kernel_type must be notebook or script")
    return folder, ref


def load_api():
    # Kaggle's package import authenticates using its official configuration.
    # Never import it at module scope or rewrite environment/configuration.
    api = importlib.import_module("kaggle").api
    # Some SDK versions swallow import-time authentication failures.
    api.authenticate()
    return api


def timing_start(event_id, ref):
    return f'''# Explicit queue-monitor instrumentation: changes submitted execution.
import datetime as _qm_datetime
import json as _qm_json
import pathlib as _qm_pathlib
import subprocess as _qm_subprocess
_qm_timing = {{"run_id": {event_id!r}, "ref": {ref!r},
    "start_utc": _qm_datetime.datetime.now(_qm_datetime.timezone.utc).isoformat(),
    "gpu_names": [], "gpu_query_ok": False}}
try:
    _qm_gpu = _qm_subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
        capture_output=True, text=True, timeout=15)
    _qm_timing["gpu_query_ok"] = _qm_gpu.returncode == 0
    if _qm_timing["gpu_query_ok"]:
        _qm_timing["gpu_names"] = [v.strip() for v in _qm_gpu.stdout.splitlines() if v.strip()]
except Exception:
    pass
'''


TIMING_END = '''_qm_timing["end_utc"] = _qm_datetime.datetime.now(_qm_datetime.timezone.utc).isoformat()
_qm_pathlib.Path("queue-monitor-timing.json").write_text(_qm_json.dumps(_qm_timing), encoding="utf-8")
'''


def instrument_copy(folder, destination, event_id, ref):
    """Bounded submission copy: Kaggle pushes only metadata and code_file."""
    metadata_path = folder / "kernel-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    relative = Path(metadata["code_file"])
    source = folder / relative
    for path in (metadata_path, source, *source.parents):
        if path == folder.parent:
            break
        if path.is_symlink():
            raise ValueError("Instrumentation does not follow symlinks")
    if metadata_path.stat().st_size > 1024 * 1024 or source.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Instrumentation metadata/source exceeds copy limit")
    if metadata.get("language") != "python" or (metadata["kernel_type"] == "script" and source.suffix != ".py"):
        raise ValueError("Instrumentation supports Python only")
    body = source.read_text(encoding="utf-8")
    start = timing_start(event_id, ref)
    if metadata["kernel_type"] == "notebook":
        notebook = json.loads(body)
        def cell(code):
            return {"cell_type": "code", "execution_count": None, "metadata": {},
                    "outputs": [], "source": code, "id": uuid.uuid4().hex[:12]}
        notebook["cells"] = [cell(start)] + notebook["cells"] + [cell(TIMING_END)]
        body = json.dumps(notebook)
    else:
        # The provider uploads only one source file. Embed the unchanged script
        # and run it as a separate file so its future imports remain legal.
        body = start + f'''import runpy as _qm_runpy
import tempfile as _qm_tempfile
try:
    with _qm_tempfile.TemporaryDirectory(prefix="queue-monitor-source-") as _qm_dir:
        _qm_source = _qm_pathlib.Path(_qm_dir) / {source.name!r}
        _qm_source.write_text({body!r}, encoding="utf-8")
        _qm_runpy.run_path(str(_qm_source), run_name="__main__")
finally:
''' + "".join("    " + line + "\n" for line in TIMING_END.splitlines())
    destination = Path(destination)
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    (destination / "kernel-metadata.json").write_bytes(metadata_path.read_bytes())
    return destination


def response_field(response, name):
    return response.get(name) if isinstance(response, dict) else getattr(response, name, None)


def classify_response(response, expected_ref):
    ref = response_field(response, "ref")
    version = response_field(response, "versionNumber")
    ref = ref if isinstance(ref, str) and REF.fullmatch(ref) else None
    version = version if type(version) is int and version > 0 else None
    if response_field(response, "error"):
        outcome = "rejected"
    elif ref == expected_ref and version is not None:
        outcome = "accepted"
    else:
        outcome = "ambiguous"
    return ref, version, outcome


def save_receipt(path, event, *, create=False):
    """Reserve before push, then atomically replace and fsync sanitized JSON."""
    payload = (json.dumps(event, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if create:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        fd, temporary = tempfile.mkstemp(prefix=".production-receipt-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    if os.name != "nt":
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def read_receipt(path):
    event = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(event, dict) or set(event) != FIELDS:
        raise ValueError("Invalid receipt schema")
    uuid.UUID(event["event_id"])
    if not isinstance(event["account"], str) or not re.fullmatch(r"[A-Za-z0-9_-]+", event["account"]):
        raise ValueError("Invalid receipt account")
    if not isinstance(event["requested_ref"], str) or not REF.fullmatch(event["requested_ref"]) or event["requested_ref"].split("/")[0] != event["account"]:
        raise ValueError("Invalid requested identity")
    if event["ref"] is not None and (not isinstance(event["ref"], str) or not REF.fullmatch(event["ref"])):
        raise ValueError("Invalid receipt ref")
    if event["version"] is not None and (type(event["version"]) is not int or event["version"] <= 0):
        raise ValueError("Invalid receipt version")
    if event["push_outcome"] not in {"accepted", "ambiguous", "rejected"}:
        raise ValueError("Invalid receipt outcome")
    if event["accelerator"] != "NvidiaTeslaT4":
        raise ValueError("Invalid receipt accelerator")
    times = []
    for name in ("submit_begin", "submit_ack"):
        value = event[name]
        if value is None and name == "submit_ack" and event["push_outcome"] == "ambiguous":
            continue
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError("Receipt timestamps must be UTC")
        times.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
    if len(times) == 2 and times[1] < times[0]:
        raise ValueError("Receipt acknowledgement precedes submission")
    if event["push_outcome"] == "accepted" and (event["ref"] != event["requested_ref"] or event["version"] is None):
        raise ValueError("Accepted receipt requires exact identity")
    return event


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("--monitor-url must be a plain origin without credentials")
    if not parsed.hostname or parsed.scheme not in {"http", "https"}:
        raise ValueError("Invalid monitor URL")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Remote monitor URLs require HTTPS")
    return url.rstrip("/") + "/api/production/register"


def upload(event, endpoint, token):
    import httpx

    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        response = client.post(endpoint, json=event, headers={"X-Monitor-Token": token})
        response.raise_for_status()


def verify(event_id, endpoint, token):
    # Only backend-fetched exact-version provider evidence may verify timing.
    upload({"event_id": event_id}, endpoint, token)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--folder")
    result.add_argument("--account")
    result.add_argument("--receipt", type=Path)
    result.add_argument("--upload-receipt", type=Path)
    result.add_argument("--verify-receipt", type=Path)
    result.add_argument("--instrument", action="store_true")
    result.add_argument("--monitor-url", default="http://127.0.0.1:3000")
    result.add_argument("--accelerator", choices=["NvidiaTeslaT4"], default="NvidiaTeslaT4")
    result.add_argument("--token-env", default="MONITOR_INGEST_TOKEN")
    result.add_argument("--dry-run", action="store_true")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        endpoint = validate_url(args.monitor_url)
        replay = args.upload_receipt or args.verify_receipt
        if replay:
            if args.folder or args.account or args.receipt or args.instrument or (args.upload_receipt and args.verify_receipt):
                raise ValueError("Replay/verification cannot include push arguments")
            event = read_receipt(replay)
        else:
            if not args.folder or not args.account or not args.receipt:
                raise ValueError("Push requires --folder, --account and --receipt")
            # This repository's monitoring identity is excluded before any credentials are read.
            if args.account.lower() == "monitoring4credentials":
                print("Skipped monitoring4credentials; no credentials read or push performed.")
                return 0
            folder, expected_ref = validate_folder(args.folder, args.account)
            receipt = args.receipt.resolve()
            if receipt.is_relative_to(folder):
                raise ValueError("Receipt must be outside the input folder")
            if not receipt.parent.is_dir() or receipt.exists() or receipt.is_symlink():
                raise ValueError("Receipt needs an existing protected parent and a new path")
        if args.dry_run:
            print("Dry run validated; no credentials read, push, receipt write or upload performed.")
            return 0
        token = os.environ.get(args.token_env)
        if not token or "\n" in token or "\r" in token:
            raise ValueError("Monitor token is missing or invalid")
        if args.verify_receipt:
            try:
                verify(event["event_id"], endpoint.removesuffix("register") + "verify", token)
            except Exception:
                print("Backend verification request failed; receipt preserved. Raw errors suppressed.", file=sys.stderr)
                return 3
            print("Backend verification request completed; consult backend verification state for evidence.")
            return 0
        if not replay:
            # Suppress SDK output, including authentication and raw provider errors.
            with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
                api = load_api()
            event = dict(event_id=str(uuid.uuid4()), account=args.account, requested_ref=expected_ref, ref=None, version=None,
                         submit_begin=None, submit_ack=None,
                         accelerator=args.accelerator, push_outcome="ambiguous")
            with ExitStack() as stack:
                push_folder = folder
                if args.instrument:
                    temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix="production-tracking-"))
                    push_folder = instrument_copy(folder, temporary, event["event_id"], expected_ref)
                event["submit_begin"] = utc_now()
                save_receipt(receipt, event, create=True)
                with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
                    try:
                        response = api.kernels_push(str(push_folder), acc=args.accelerator)
                        event["submit_ack"] = utc_now()
                        event["ref"], event["version"], event["push_outcome"] = classify_response(response, expected_ref)
                    except Exception as exc:
                        # No response means no provider acknowledgement, even on timeout.
                        status = getattr(exc, "status", None)
                        if type(status) is int and 400 <= status < 500 and status not in {408, 429}:
                            event["push_outcome"] = "rejected"
                            event["submit_ack"] = utc_now()
            save_receipt(receipt, event)
        try:
            upload(event, endpoint, token)
        except Exception:
            print("Monitor upload failed; receipt preserved. Replay with --upload-receipt; do not repush.", file=sys.stderr)
            return 3
        print("Production registration uploaded: " + event["push_outcome"])
        return 0 if event["push_outcome"] == "accepted" else 2
    except (Exception, SystemExit):
        print("Tracking failed; inspect inputs and any existing receipt before proceeding. Raw errors suppressed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
