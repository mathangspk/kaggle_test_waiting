import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


spec = importlib.util.spec_from_file_location("production_tracking", Path(__file__).parents[1] / "track_production.py")
tracking = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tracking)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    folder = tmp_path / "kernel"
    folder.mkdir()
    (folder / "kernel-metadata.json").write_text(json.dumps({
        "id": "owner/my-kernel", "code_file": "main.py", "kernel_type": "script",
        "language": "python", "title": "My Kernel",
    }))
    (folder / "main.py").write_text("print('production')\n")
    receipt = tmp_path / "receipt.json"
    api = Mock()
    api.kernels_push.return_value = SimpleNamespace(ref="owner/my-kernel", versionNumber=7, error=None)
    loader = Mock(return_value=api)
    uploader = Mock()
    monkeypatch.setattr(tracking, "load_api", loader)
    monkeypatch.setattr(tracking, "upload", uploader)
    monkeypatch.setenv("MONITOR_INGEST_TOKEN", "test-monitor-secret")
    args = ["--folder", str(folder), "--account", "owner", "--receipt", str(receipt)]
    return folder, receipt, api, loader, uploader, args


def test_accepted_exact_version_and_read_only_source(setup, monkeypatch):
    folder, receipt, api, loader, uploader, args = setup
    original = {p.name: p.read_bytes() for p in folder.iterdir()}
    times = iter(["2026-10-13T10:00:00Z", "2026-10-13T10:00:03Z"])
    monkeypatch.setattr(tracking, "utc_now", lambda: next(times))
    assert tracking.main(args) == 0
    event = tracking.read_receipt(receipt)
    assert event["push_outcome"] == "accepted"
    assert (event["ref"], event["version"]) == ("owner/my-kernel", 7)
    assert event["requested_ref"] == "owner/my-kernel"
    assert event["submit_begin"] == "2026-10-13T10:00:00Z"
    assert event["submit_ack"] == "2026-10-13T10:00:03Z"
    api.kernels_push.assert_called_once_with(str(folder.resolve()), acc="NvidiaTeslaT4")
    uploader.assert_called_once_with(event, "http://127.0.0.1:3000/api/production/register", "test-monitor-secret")
    assert original == {p.name: p.read_bytes() for p in folder.iterdir()}
    assert "secret" not in receipt.read_text()
    assert set(event) == tracking.FIELDS


def test_timeout_ambiguous_single_push(setup, capsys):
    _, receipt, api, _, uploader, args = setup
    api.kernels_push.side_effect = TimeoutError("provider-secret")
    assert tracking.main(args) == 2
    assert tracking.read_receipt(receipt)["push_outcome"] == "ambiguous"
    assert tracking.read_receipt(receipt)["submit_ack"] is None
    assert tracking.read_receipt(receipt)["ref"] is None
    assert tracking.read_receipt(receipt)["requested_ref"] == "owner/my-kernel"
    api.kernels_push.assert_called_once()
    uploader.assert_called_once()
    assert "provider-secret" not in receipt.read_text() + str(capsys.readouterr())


@pytest.mark.parametrize("response", [
    SimpleNamespace(ref="", versionNumber=7, error=None),
    SimpleNamespace(ref="owner/my-kernel", versionNumber=None, error=None),
    SimpleNamespace(ref="other/my-kernel", versionNumber=7, error=None),
    SimpleNamespace(ref="owner/my-kernel", versionNumber=True, error=None),
    None,
])
def test_incomplete_or_mismatched_identity_never_guessed(setup, response):
    _, receipt, api, _, _, args = setup
    api.kernels_push.return_value = response
    assert tracking.main(args) == 2
    event = tracking.read_receipt(receipt)
    assert event["push_outcome"] == "ambiguous"
    if getattr(response, "ref", None) == "":
        assert event["ref"] is None and event["version"] == 7


def test_rejected_response_sanitized(setup, capsys):
    _, receipt, api, _, _, args = setup
    api.kernels_push.return_value = {"error": "secret API body", "ref": None, "versionNumber": None}
    assert tracking.main(args) == 2
    assert tracking.read_receipt(receipt)["push_outcome"] == "rejected"
    assert "secret API body" not in receipt.read_text() + str(capsys.readouterr())


def test_upload_failure_preserves_receipt_and_replay_only(setup, capsys):
    _, receipt, api, loader, uploader, args = setup
    uploader.side_effect = RuntimeError("upload-secret")
    assert tracking.main(args) == 3
    before = receipt.read_bytes()
    assert tracking.main(args) == 1  # existing receipt blocks another push
    uploader.side_effect = None
    assert tracking.main(["--upload-receipt", str(receipt)]) == 0
    assert receipt.read_bytes() == before
    api.kernels_push.assert_called_once()
    loader.assert_called_once()
    assert uploader.call_count == 2
    assert uploader.call_args.args[0] == json.loads(before)
    assert "upload-secret" not in str(capsys.readouterr())


def test_default_skip_never_reads_environment(setup, monkeypatch):
    _, receipt, api, loader, uploader, args = setup
    args[args.index("owner")] = "monitoring4credentials"
    class NoRead:
        def get(self, *args):
            raise AssertionError("environment read")
    with monkeypatch.context() as isolated:
        isolated.setattr(tracking, "os", SimpleNamespace(environ=NoRead()))
        result = tracking.main(args)
    assert result == 0
    loader.assert_not_called()
    uploader.assert_not_called()
    assert not receipt.exists()


def test_dry_run_no_import_push_upload_or_credentials(setup, monkeypatch):
    _, receipt, _, loader, uploader, args = setup
    monkeypatch.delenv("MONITOR_INGEST_TOKEN")
    assert tracking.main(args + ["--dry-run"]) == 0
    loader.assert_not_called()
    uploader.assert_not_called()
    assert not receipt.exists()


@pytest.mark.parametrize("update", [
    {"id": "other/my-kernel"}, {"id": "owner/my-kernel/7"},
    {"code_file": "../outside.py"}, {"code_file": "C:\\outside.py"},
    {"code_file": "missing.py"},
])
def test_invalid_metadata_blocks_push(setup, update):
    folder, _, _, loader, uploader, args = setup
    path = folder / "kernel-metadata.json"
    data = json.loads(path.read_text())
    data.update(update)
    path.write_text(json.dumps(data))
    assert tracking.main(args) == 1
    loader.assert_not_called()
    uploader.assert_not_called()


def test_notebook_validated_and_unchanged(setup):
    folder, _, api, _, _, args = setup
    path = folder / "kernel-metadata.json"
    data = json.loads(path.read_text())
    data.update(code_file="main.ipynb", kernel_type="notebook")
    path.write_text(json.dumps(data))
    source = folder / "main.ipynb"
    source.write_text(json.dumps({"nbformat": 4, "nbformat_minor": 5, "cells": [], "metadata": {}}))
    before = source.read_bytes()
    assert tracking.main(args) == 0
    assert source.read_bytes() == before
    api.kernels_push.assert_called_once()


def test_upload_transport_token_header(monkeypatch):
    response = Mock()
    client = Mock()
    client.post.return_value = response
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=client)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(Client=factory))
    tracking.upload({"event_id": "example"}, "https://monitor.example/api/production/register", "external-secret")
    factory.assert_called_once_with(timeout=30, follow_redirects=False, trust_env=False)
    client.post.assert_called_once_with("https://monitor.example/api/production/register", json={"event_id": "example"}, headers={"X-Monitor-Token": "external-secret"})


def test_kaggle_import_is_runtime_only(monkeypatch):
    api = Mock()
    imported = Mock(return_value=SimpleNamespace(api=api))
    monkeypatch.setattr(tracking.importlib, "import_module", imported)
    assert tracking.load_api() is api
    imported.assert_called_once_with("kaggle")
    api.authenticate.assert_called_once_with()


def test_authentication_exit_is_sanitized_before_push(setup, capsys):
    _, receipt, api, loader, uploader, args = setup
    loader.side_effect = SystemExit("credential-secret")
    assert tracking.main(args) == 1
    api.kernels_push.assert_not_called()
    uploader.assert_not_called()
    assert not receipt.exists()
    assert "credential-secret" not in str(capsys.readouterr())


def snapshot(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.mark.parametrize("instrument", [False, True])
def test_receipt_inside_folder_rejected_without_any_mutation(setup, instrument):
    folder, _, api, loader, uploader, args = setup
    before = snapshot(folder)
    args[-1] = str(folder / "receipt.json")
    if instrument:
        args.append("--instrument")
    assert tracking.main(args) == 1
    assert snapshot(folder) == before
    loader.assert_not_called()
    api.kernels_push.assert_not_called()
    uploader.assert_not_called()


def test_instrumented_push_copy_nonce_and_cleanup(setup):
    folder, receipt, api, _, _, args = setup
    # Unknown ancillary files are never traversed or copied.
    (folder / "private-receipt.json").write_text("not uploaded")
    (folder / "main.py").write_text("from __future__ import annotations\nprint('production')\n")
    before = snapshot(folder)
    copies = []
    def push(copy_folder, acc):
        copy = Path(copy_folder)
        copies.append(copy)
        event = tracking.read_receipt(receipt)
        assert copy != folder
        assert set(snapshot(copy)) == {"kernel-metadata.json", "main.py"}
        assert (copy / "kernel-metadata.json").read_bytes() == (folder / "kernel-metadata.json").read_bytes()
        source = (copy / "main.py").read_text()
        assert repr(event["event_id"]) in source
        assert "try:" in source and "finally:" in source and "run_path" in source
        assert "nvidia-smi" in source and "queue-monitor-timing.json" in source
        compile(source, "instrumented.py", "exec")
        assert snapshot(folder) == before
        return SimpleNamespace(ref="owner/my-kernel", versionNumber=7, error=None)
    api.kernels_push.side_effect = push
    assert tracking.main(args + ["--instrument"]) == 0
    assert copies and not copies[0].exists()
    assert snapshot(folder) == before
    api.kernels_push.assert_called_once()


@pytest.mark.parametrize("fails", [False, True])
def test_script_instrumentation_artifact_finally_without_gpu(setup, tmp_path, monkeypatch, fails):
    import subprocess

    folder, _, _, _, _, _ = setup
    source = "from __future__ import annotations\n"
    source += "raise RuntimeError('synthetic failure')\n" if fails else "value: int = 42\n"
    (folder / "main.py").write_text(source)
    before = snapshot(folder)
    copy = tmp_path / "copy"
    copy.mkdir()
    run_id = "bbc0b463-9fd6-416c-8a5e-25cb3c804a82"
    tracking.instrument_copy(folder, copy, run_id, "owner/my-kernel")
    work = tmp_path / "execution"
    work.mkdir()
    monkeypatch.chdir(work)
    gpu = Mock(return_value=SimpleNamespace(returncode=0, stdout="Tesla T4\nTesla T4\n"))
    monkeypatch.setattr(subprocess, "run", gpu)
    code = compile((copy / "main.py").read_text(), "instrumented.py", "exec")
    if fails:
        with pytest.raises(RuntimeError, match="synthetic failure"):
            exec(code, {})
    else:
        exec(code, {})
    artifact = json.loads((work / "queue-monitor-timing.json").read_text())
    assert artifact["run_id"] == run_id
    assert artifact["ref"] == "owner/my-kernel"
    assert artifact["gpu_names"] == ["Tesla T4", "Tesla T4"]
    assert artifact["gpu_query_ok"] is True
    assert artifact["start_utc"] <= artifact["end_utc"]
    gpu.assert_called_once()
    assert snapshot(folder) == before


def test_notebook_instrumentation_cells_original_unchanged(setup, tmp_path):
    folder, _, _, _, _, _ = setup
    metadata_path = folder / "kernel-metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata.update(kernel_type="notebook", code_file="main.ipynb")
    metadata_path.write_text(json.dumps(metadata))
    original_cell = {"cell_type": "code", "source": "print('production')", "outputs": [], "metadata": {}, "execution_count": None}
    notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [original_cell]}
    (folder / "main.ipynb").write_text(json.dumps(notebook))
    before = snapshot(folder)
    copy = tmp_path / "notebook-copy"
    copy.mkdir()
    tracking.instrument_copy(folder, copy, "nonce-123", "owner/my-kernel")
    instrumented = json.loads((copy / "main.ipynb").read_text())
    assert len(instrumented["cells"]) == 3
    assert instrumented["cells"][1] == original_cell
    assert "nonce-123" in instrumented["cells"][0]["source"]
    assert "queue-monitor-timing.json" in instrumented["cells"][-1]["source"]
    assert snapshot(folder) == before


def test_verify_receipt_only_requests_backend_event(setup, monkeypatch):
    _, receipt, api, loader, uploader, args = setup
    assert tracking.main(args) == 0
    before = receipt.read_bytes()
    verifier = Mock()
    monkeypatch.setattr(tracking, "verify", verifier)
    assert tracking.main(["--verify-receipt", str(receipt)]) == 0
    verifier.assert_called_once_with(json.loads(before)["event_id"], "http://127.0.0.1:3000/api/production/verify", "test-monitor-secret")
    loader.assert_called_once()
    api.kernels_push.assert_called_once()
    uploader.assert_called_once()
    assert receipt.read_bytes() == before


def test_verify_transport_posts_event_id_only(monkeypatch):
    post = Mock()
    monkeypatch.setattr(tracking, "upload", post)
    tracking.verify("event-nonce", "https://monitor.example/api/production/verify", "external-secret")
    post.assert_called_once_with({"event_id": "event-nonce"}, "https://monitor.example/api/production/verify", "external-secret")


def test_verify_failure_sanitized_receipt_preserved(setup, monkeypatch, capsys):
    _, receipt, api, _, _, args = setup
    assert tracking.main(args) == 0
    before = receipt.read_bytes()
    monkeypatch.setattr(tracking, "verify", Mock(side_effect=RuntimeError("secret provider response")))
    assert tracking.main(["--verify-receipt", str(receipt)]) == 3
    assert receipt.read_bytes() == before
    assert "secret provider response" not in str(capsys.readouterr())
    api.kernels_push.assert_called_once()


def test_ambiguous_receipt_replay_null_ack_no_repush(setup):
    _, receipt, api, loader, uploader, args = setup
    api.kernels_push.side_effect = TimeoutError("synthetic")
    assert tracking.main(args) == 2
    before = receipt.read_bytes()
    event = tracking.read_receipt(receipt)
    assert event["requested_ref"] == "owner/my-kernel"
    assert event["ref"] is None and event["submit_ack"] is None
    assert tracking.main(["--upload-receipt", str(receipt)]) == 2
    assert uploader.call_args.args[0] == event
    assert receipt.read_bytes() == before
    api.kernels_push.assert_called_once()
    loader.assert_called_once()


def test_instrumentation_rejects_oversized_source_before_receipt_or_push(setup):
    folder, receipt, api, _, uploader, args = setup
    (folder / "main.py").write_bytes(b"#" * (16 * 1024 * 1024 + 1))
    before = snapshot(folder)
    assert tracking.main(args + ["--instrument"]) == 1
    assert not receipt.exists()
    api.kernels_push.assert_not_called()
    uploader.assert_not_called()
    assert snapshot(folder) == before
