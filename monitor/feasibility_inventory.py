"""Read-only, allowlisted metadata audit for #20; never reads cookie databases.

Local State is parsed privately to emit directory IDs and known username hints
only. Google emails/names, keys, tokens and arbitrary URLs are never printed.
Hints are not authenticated Kaggle identity evidence.
"""

import argparse
import json
import os
from pathlib import Path
import shlex

ACCOUNTS = ("annalynhi", "thanglong1996", "thangma", "thnhmngc")
USER_REPORTED_MAPPING = {
    "Profile 6": "thanglong1996",
    "Profile 7": "annalynhi",
    "Profile 5": "thnhmngc",
}


def inventory():
    root = Path(os.environ["LOCALAPPDATA"]) / "Google/Chrome/User Data"
    try:
        state = json.loads((root / "Local State").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"error": "local_state_unavailable"}
    cache = state.get("profile", {}).get("info_cache", {})
    profiles = []
    for directory in ("Default", *(f"Profile {i}" for i in range(1, 8))):
        info = cache.get(directory, {})
        # Compare private label values; emit only exact known Kaggle strings.
        hints = [a for a in ACCOUNTS if a in
                 {str(info.get("name", "")).lower(),
                  str(info.get("shortcut_name", "")).lower()}]
        profiles.append({
            "directory": directory,
            "exists": (root / directory).is_dir(),
            "google_identity_metadata_present": bool(info.get("user_name")),
            "known_username_label_hints": hints,
            "user_reported_kaggle_username": USER_REPORTED_MAPPING.get(directory),
            "mapping_provenance": "user_reported" if directory in USER_REPORTED_MAPPING else None,
        })
    return {"profiles": profiles,
            "chrome_version": state.get("last_version", "unknown"),
            "app_bound_key_present": bool(state.get("os_crypt", {}).get("app_bound_encrypted_key")),
            "normal_root_devtools_active_port_file_present": (root / "DevToolsActivePort").exists(),
            "mapping_verified": False}


def server(readonly=False):
    import paramiko

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    try:
        client.connect("100.92.238.77", username="thangma",
                       key_filename=str(Path(__file__).parent / ".deploy/server_ed25519"),
                       look_for_keys=False, allow_agent=False, timeout=15)
        # Capabilities only: no container env, mounts, credentials or logs.
        command = ("uname -s; getconf _NPROCESSORS_ONLN; "
                   "command -v docker; "
                   "docker info --format '{{.OSType}}' >/dev/null 2>&1; "
                   "echo docker_access_exit=$?; "
                   "sudo -n docker info --format '{{.OSType}}' >/dev/null 2>&1; "
                   "echo sudo_noninteractive_docker_exit=$?; "
                   "python3 -c \"import importlib.util; print('playwright_installed=' + "
                   "str(importlib.util.find_spec('playwright') is not None))\"")
        if readonly:
            # No sudo/privilege retries, installations, browser launch or state transfer.
            code = '''import importlib.util,json,os,pathlib,shutil,subprocess
def text(path):
    try: return pathlib.Path(path).read_text().strip()
    except OSError: return None
mem = {}
for line in (text('/proc/meminfo') or '').splitlines():
    k, _, v = line.partition(':')
    if k in ('MemTotal', 'MemAvailable'): mem[k + '_kib'] = int(v.split()[0])
result = {'online_cpus': os.cpu_count(), 'host_memory': mem,
          'session_cgroup_memory_max': text('/sys/fs/cgroup/memory.max'),
          'session_cgroup_cpu_max': text('/sys/fs/cgroup/cpu.max'),
          'system_python_playwright': importlib.util.find_spec('playwright') is not None,
          'browser_commands_in_path': [n for n in ('chromium','chromium-browser','google-chrome') if shutil.which(n)]}
try:
    r = subprocess.run(['docker','info','--format','{{.OSType}}'],capture_output=True,text=True,timeout=15)
    error = r.stderr.lower()
    reason = ('accessible' if r.returncode == 0 else
              'docker_socket_permission_denied' if 'permission denied' in error else
              'docker_daemon_unreachable' if 'cannot connect' in error else 'docker_info_failed')
    result.update(docker_exit_code=r.returncode, docker_access=reason)
except FileNotFoundError: result['docker_access'] = 'cli_unavailable'
except subprocess.TimeoutExpired: result['docker_access'] = 'deadline_exceeded'
print(json.dumps(result))
'''
            command = 'python3 -c ' + shlex.quote(code)
        _, out, _ = client.exec_command(command, timeout=30)
        output = out.read().decode()
        return {"capabilities": json.loads(output) if readonly else output.splitlines(),
                "exit_code": out.channel.recv_exit_status()}
    except Exception as exc:
        return {"error": "server_access_failed", "error_type": type(exc).__name__}
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inventory", "server", "server-readonly"))
    args = parser.parse_args()
    print(json.dumps(inventory() if args.mode == "inventory" else
                     server(readonly=args.mode == "server-readonly"), indent=2))
