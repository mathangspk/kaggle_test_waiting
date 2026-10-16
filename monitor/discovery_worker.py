"""Sequential opt-in discovery supervisor; no provider submission/cancellation APIs."""
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

from discovery_contract import ACCOUNT, failure, validate
from discovery_visit import private_file, save_private

STOP = False


def stop(*_):
    global STOP
    STOP = True


def counters():
    result = {}
    for key in ("memory.current", "memory.peak", "pids.current", "pids.peak", "memory.events", "cpu.stat"):
        try:
            result[key] = Path("/sys/fs/cgroup", key).read_text().strip()
        except OSError:
            pass
    return result


def subreaper():
    # Adopt daemonized/double-forked browser descendants, even if they setsid().
    import ctypes
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise RuntimeError("subreaper_unavailable")


def descendants():
    parents = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                # comm may contain spaces/parentheses; fields after final ')'.
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                parents[int(entry.name)] = int(fields[1])
            except (OSError, ValueError, IndexError):
                pass
    found = {os.getpid()}
    while True:
        children = {pid for pid, parent in parents.items() if parent in found}
        if children <= found:
            return found - {os.getpid()}
        found |= children


def clean_adopted(until):
    # Dedicated single-threaded supervisor has no unrelated child processes.
    while time.monotonic() < until:
        children = descendants()
        if not children:
            return
        for pid in children:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if not pid:
                    break
            except ChildProcessError:
                break
        time.sleep(0.02)
    raise RuntimeError("process_cleanup_failed")


def run_visit(state_path, command=None, deadline=60):
    """Linux process group contains driver + Chromium tree, including orphan children.

    Reserve five seconds for SIGKILL/reap. Always kill the group, even on normal
    leader exit (a crashed driver can leave a renderer behind). Never overlap.
    """
    started = time.monotonic()
    subreaper()
    before = counters()
    peak_pids = 0
    with tempfile.TemporaryDirectory(prefix="discovery-") as directory:
        output = Path(directory, "result.json")
        cmd = command or [sys.executable, str(Path(__file__).with_name("discovery_visit.py")),
                          str(state_path), str(output)]
        proc = subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        timed_out = False
        try:
            while proc.poll() is None:
                if STOP or time.monotonic() - started >= deadline - 5:
                    timed_out = True
                    break
                try:
                    peak_pids = max(peak_pids, int(Path("/sys/fs/cgroup/pids.current").read_text()))
                except (OSError, ValueError):
                    pass
                time.sleep(0.1)
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # If the OS cannot reap within the budget, terminate the supervisor.
            # Docker's init/cgroup cleanup then owns the tree; no next visit starts.
            try:
                proc.wait(timeout=max(0.01, deadline - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                raise RuntimeError("process_cleanup_failed") from None
            clean_adopted(started + deadline)
        result = failure("visit_timeout" if timed_out else "worker_crash", time.time())
        if not timed_out and proc.returncode == 0:
            try:
                if output.stat().st_size > 131072:
                    raise ValueError("result_too_large")
                result = validate(json.loads(output.read_text()), {ACCOUNT}, time.time())
            except (OSError, ValueError, TypeError, KeyError):
                result = failure("worker_crash", time.time())
    return result, {"duration_seconds": round(time.monotonic() - started, 3),
                    "sampled_peak_pids": peak_pids, "cgroup_before": before, "cgroup_after": counters()}


def ingest(url, token_path, payload):
    # Fixed internal destination configured by operator; no redirects leaking token.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    private_file(token_path)
    token = token_path.read_text().strip()
    if not token or len(token) > 512:
        raise ValueError("invalid_ingest_configuration")
    request = urllib.request.Request(url, json.dumps(payload, allow_nan=False).encode(),
                                     {"Content-Type": "application/json", "X-Discovery-Token": token})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=5) as response:
        body = json.loads(response.read(4096))
        if response.status != 200 or body.get("accepted") is not True:
            raise ValueError("ingest_rejected")


def next_delay(failures, rng=random):
    return rng.uniform(180, 300) if not failures else min(1800, 180 * 2 ** min(failures - 1, 4)) + rng.uniform(0, 30)


def run_ingest(url, token_path, payload, deadline=5):
    # urllib's timeout bounds individual socket operations, not the whole call.
    # A separate non-browser process also bounds slow/drip-fed HTTP responses.
    subprocess.run([sys.executable, str(Path(__file__).resolve()), "--ingest", url, str(token_path)],
                   input=json.dumps(payload, allow_nan=False).encode(),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=deadline, check=True)


def cycle(state, visit, send, now=time.time, rng=random, checkpoint=None):
    """One slot and one coalescing durable outbox. Missed intervals never enqueue."""
    if not state.get("suspended"):
        payload, metrics = visit()
        state.update(outbox=payload, metrics=metrics, last_attempt=now())
        if payload.get("auth_error") or payload.get("error") == "private_state_unavailable":
            state["suspended"] = payload.get("auth_error") or payload["error"]
        state["last_result"] = payload.get("auth_error") or payload.get("error") or "partial"
        if payload["coverage"] == "partial":
            state["last_success"] = payload["observed_at"]
        if checkpoint:
            checkpoint(state)  # Suspension/outbox survives a crash during ingestion.
    delivered = True
    if state.get("outbox"):
        try:
            send(state["outbox"])
            state.pop("outbox")
        except Exception:
            delivered = False
    failed = not delivered or state.get("last_result") != "partial"
    state["failures"] = min(16, state.get("failures", 0) + 1) if failed else 0
    state["ingestion"] = "ok" if delivered else "ingestion_failed"
    state["next_at"] = now() + next_delay(state["failures"], rng)
    return state


def main():
    if os.getenv("DISCOVERY_ENABLED", "false") != "true":
        return
    if sys.platform != "linux":
        raise RuntimeError("linux_worker_required")
    import fcntl
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    state_dir = Path("/var/lib/discovery")
    auth = Path("/private/thangma/storage-state.json")
    # Lock the same mounted auth directory across duplicate containers too.
    with open(auth.parent / ".discovery.lock", "a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_file = state_dir / "state.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        while not STOP:
            if time.time() < state.get("next_at", 0):
                time.sleep(max(0, min(1, state["next_at"] - time.time())))
                continue
            cycle(state, lambda: run_visit(auth), lambda p: run_ingest(
                "http://monitor:3000/api/activity/browser-discovery", Path("/run/discovery-token/token"), p),
                checkpoint=lambda value: save_private(state_file, value))
            save_private(state_file, state)
            print(json.dumps({k: state.get(k) for k in ("last_result", "last_success", "last_attempt",
                              "suspended", "ingestion", "failures", "next_at", "metrics")}), flush=True)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 4 and sys.argv[1] == "--ingest":
            ingest(sys.argv[2], Path(sys.argv[3]), json.load(sys.stdin))
        else:
            main()
    except Exception:
        print('{"error":"worker_stopped"}', flush=True)
        sys.exit(1)
