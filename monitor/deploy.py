"""Operator deployment; credentials never printed or committed."""
import io
import os
from pathlib import Path
import shlex
import time
import sys
import paramiko
from dotenv import dotenv_values
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

BASE = Path(__file__).parent
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
REMOTE = '/home/thangma/kaggle-queue-monitor'
PASSWORD = os.environ['DEPLOY_PASSWORD']
client = paramiko.SSHClient()
client.load_system_host_keys()
client.connect('100.92.238.77', username='thangma', password=PASSWORD,
               look_for_keys=False, allow_agent=False, timeout=15)

def run(command, sudo=False, timeout=600):
    if sudo:
        command = 'sudo -S -p "" sh -c ' + shlex.quote(command)
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if sudo:
        stdin.write(PASSWORD + '\n')
        stdin.flush()
    code = stdout.channel.recv_exit_status()
    text = stdout.read().decode() + stderr.read().decode()
    print(text.replace(PASSWORD, '[redacted]'))
    if code:
        raise RuntimeError('Remote command failed: ' + str(code))

try:
    run('mkdir -p ' + REMOTE + '/static ' + REMOTE + '/secrets; chmod 700 ' + REMOTE + '/secrets')
    sftp = client.open_sftp()
    for name in ['app.py', 'requirements.txt', 'Dockerfile', 'compose.yaml', '.dockerignore', 'static/index.html']:
        sftp.put(str(BASE / name), REMOTE + '/' + name)
    values = dotenv_values(os.environ['SOURCE_ACCOUNTS_ENV'], interpolate=False)
    count = int(values['PRODUCTION_KAGGLE_ACCOUNT_COUNT'])
    lines = ['PRODUCTION_KAGGLE_ACCOUNT_COUNT=' + str(count)]
    for i in range(1, count + 1):
        for suffix in ['USERNAME', 'API_TOKEN']:
            name = f'PRODUCTION_KAGGLE_ACCOUNT_{i}_{suffix}'
            value = values[name]
            if not value or '\n' in value or "'" in value:
                raise ValueError('Invalid credential format')
            lines.append(name + "='" + value + "'")
    with sftp.open(REMOTE + '/secrets/accounts.env', 'w') as f:
        f.write('\n'.join(lines) + '\n')
    sftp.chmod(REMOTE + '/secrets/accounts.env', 0o600)
    keydir = BASE / '.deploy'
    keydir.mkdir(exist_ok=True)
    private_path = keydir / 'server_ed25519'
    if not private_path.exists():
        key = Ed25519PrivateKey.generate()
        private_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()))
    else:
        key = serialization.load_ssh_private_key(private_path.read_bytes(), password=None)
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH).decode() + ' kaggle-monitor'
    run('mkdir -p ~/.ssh; chmod 700 ~/.ssh; touch ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys; '
        + 'grep -qxF ' + shlex.quote(public) + ' ~/.ssh/authorized_keys || printf "%s\\n" '
        + shlex.quote(public) + ' >> ~/.ssh/authorized_keys')
    probe = paramiko.SSHClient()
    probe.load_system_host_keys()
    probe.connect('100.92.238.77', username='thangma', key_filename=str(private_path),
                  look_for_keys=False, allow_agent=False, timeout=15)
    probe.close()
    print('SSH key authentication verified')
    run('docker compose version', sudo=True)
    run('cd ' + REMOTE + ' && docker compose up -d --build', sudo=True, timeout=900)
    run('tailscale serve --bg --https=8443 http://127.0.0.1:3000', sudo=True)
    time.sleep(15)
    run('curl -fsS http://127.0.0.1:3000/healthz; tailscale serve status')
finally:
    client.close()
