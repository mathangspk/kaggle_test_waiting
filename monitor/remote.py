"""SSH operator commands using dedicated key; sudo password via process env."""
import os
from pathlib import Path
import shlex
import sys
import paramiko
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
c = paramiko.SSHClient()
c.load_system_host_keys()
c.connect('100.92.238.77', username='thangma',
          key_filename=str(Path(__file__).parent / '.deploy/server_ed25519'),
          look_for_keys=False, allow_agent=False, timeout=15)
try:
    command = sys.argv[1]
    if len(sys.argv) > 2:
        local = Path(sys.argv[2])
        remote = '/home/thangma/kaggle-queue-monitor/operator.py'
        with c.open_sftp() as s:
            s.put(str(local), remote)
        command = command.replace('{script}', remote)
    stdin, out, err = c.exec_command('sudo -S -p "" sh -c ' + shlex.quote(command), timeout=900)
    stdin.write(os.environ['DEPLOY_PASSWORD'] + '\n')
    stdin.flush()
    # Drain before waiting to avoid large-output channel deadlock.
    text = out.read().decode() + err.read().decode()
    print(text.replace(os.environ['DEPLOY_PASSWORD'], '[redacted]'))
    code = out.channel.recv_exit_status()
    if code:
        sys.exit(code)
finally:
    c.close()
