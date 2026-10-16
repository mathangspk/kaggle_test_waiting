"""Read-only Phase 3 prerequisite check; no auth export or remote mutations."""
import json
import os
from pathlib import Path
import shlex

import paramiko


def main():
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.connect(
        "100.92.238.77", username="thangma",
        key_filename=str(Path(__file__).parent / ".deploy/server_ed25519"),
        look_for_keys=False, allow_agent=False, timeout=15,
    )
    try:
        password = os.environ.get("DEPLOY_PASSWORD")
        # Same key/known-host and sudo route as remote.py, with a noninteractive
        # cached-authorization check when this process has no session password.
        command = "docker info --format '{{.OSType}} {{.ServerVersion}}'"
        prefix = 'sudo -S -p "" sh -c ' if password else "sudo -n sh -c "
        stdin, stdout, stderr = client.exec_command(prefix + shlex.quote(command), timeout=30)
        if password:
            stdin.write(password + "\n")
            stdin.flush()
        stdin.channel.shutdown_write()
        text = stdout.read().decode(errors="replace")
        error = stderr.read().decode(errors="replace")
        code = stdout.channel.recv_exit_status()
        result = {
            "target": "approved existing monitor host; 31.21 GiB / 8 CPUs",
            "password_available_in_process": bool(password),
            "route": "remote.py-compatible sudo -S" if password else "sudo -n cached authorization",
            "exit_code": code,
            "docker_linux_verified": code == 0 and text.startswith("linux "),
        }
        if code:
            result["blocker"] = (
                "sudo_password_required" if "password is required" in error.lower()
                else "sudo_authentication_failed" if "incorrect password" in error.lower()
                else "authorized_docker_check_failed"
            )
        print(json.dumps(result))
    finally:
        client.close()


if __name__ == "__main__":
    main()
