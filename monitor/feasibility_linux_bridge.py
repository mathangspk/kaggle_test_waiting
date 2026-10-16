"""Private stdin -> verified SSH bridge. Never emits input or raw errors."""
import json
import os
from pathlib import Path
import shlex
import sys
import paramiko

ROOT = '/home/thangma/.local/share/kaggle-monitor-auth/thangma'


def main(payload=None):
    if payload is None:
        payload = json.load(sys.stdin)
    c = paramiko.SSHClient()
    c.load_system_host_keys()
    c.connect('100.92.238.77', username='thangma',
              key_filename=str(Path(__file__).parent / '.deploy/server_ed25519'),
              look_for_keys=False, allow_agent=False, timeout=15)
    def run(command, sudo=False, timeout=120):
        if sudo:
            command = 'sudo -S -p "" sh -c ' + shlex.quote(command)
        i, o, e = c.exec_command(command, timeout=timeout)
        if sudo:
            i.write(payload['password'] + '\n')
            i.flush()
        i.channel.shutdown_write()
        text = o.read().decode(errors='replace')
        e.read()  # Raw diagnostics can include paths/state; never return them.
        code = o.channel.recv_exit_status()
        return code, text
    try:
        action = payload['action']
        if action == 'inventory':
            code, text = run("docker image ls --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}'", True)
            _, chrome = run('google-chrome --version')
            print(json.dumps({'docker_exit': code, 'images': text.splitlines(), 'host_chrome': chrome.strip()}))
        elif action == 'transfer':
            state = payload['state']
            if any(x['domain'] not in ['kaggle.com', '.kaggle.com', 'www.kaggle.com'] for x in state['cookies']):
                raise ValueError('non_kaggle_state')
            if any(x['origin'] != 'https://www.kaggle.com' for x in state['origins']):
                raise ValueError('non_kaggle_state')
            code, _ = run('test -d /home/thangma/.local/share && umask 077 && mkdir -p ' + ROOT + ' && chmod 700 /home/thangma/.local/share/kaggle-monitor-auth ' + ROOT)
            if code:
                raise RuntimeError('private_directory_failed')
            with c.open_sftp() as s:
                path = ROOT + '/storage-state.json'
                # Exclusive temporary file, restrictive creation before writing.
                with s.open(path + '.new', 'wx') as f:
                    s.chmod(path + '.new', 0o600)
                    f.write(json.dumps(state))
                s.posix_rename(path + '.new', path)
                st = s.stat(path)
                if st.st_mode & 0o777 != 0o600 or st.st_uid != 1000:
                    raise RuntimeError('private_permissions_failed')
            print(json.dumps({'transferred': True, 'path': path, 'mode': '0600', 'uid': st.st_uid}))
        elif action == 'command':
            code, text = run(payload['command'], True, payload.get('timeout', 180))
            # Commands accepted here must deliberately emit only safe evidence.
            print(json.dumps({'exit_code': code, 'result': text.strip()}))
        elif action == 'upload':
            with c.open_sftp() as s:
                for name in ['feasibility_linux_pilot.py']:
                    target = ROOT + '/' + name
                    with s.open(target, 'w') as f:
                        s.chmod(target, 0o600)
                        f.write((Path(__file__).parent / name).read_text(encoding='utf-8'))
            print(json.dumps({'uploaded': True}))
        elif action == 'cleanup':
            names = ['kaggle-auth-phase3-thangma', 'kaggle-auth-phase3-empty', 'kaggle-auth-phase3-invalid']
            for name in names:
                code, text = run("docker inspect " + name + " --format '{{.State.Status}}'", True)
                if code or text.strip() != 'exited':
                    raise RuntimeError('cleanup_requires_exited_containers')
            code, _ = run('docker rm ' + ' '.join(names), True)
            if code:
                raise RuntimeError('container_cleanup_failed')
            cleanup = "import pathlib,shutil; p=pathlib.Path('/home/thangma/.local/share/kaggle-monitor-auth/phase3-deps'); assert p.is_dir() and not p.is_symlink(); shutil.rmtree(p)"
            code, _ = run('python3 -c ' + shlex.quote(cleanup), True)
            if code:
                raise RuntimeError('dependency_cleanup_failed')
            with c.open_sftp() as s:
                s.remove(ROOT + '/feasibility_linux_pilot.py')
            audit = """import json,time,urllib.request,pathlib
t=time.monotonic()
with urllib.request.urlopen('http://127.0.0.1:3000/healthz',timeout=5) as r:
 d=json.load(r); status=r.status
mem=dict(x.split(':',1) for x in pathlib.Path('/proc/meminfo').read_text().splitlines())
print(json.dumps({'health_http':status,'health_elapsed_ms':round((time.monotonic()-t)*1000,2),'ok':d.get('ok'),'worker_alive':d.get('worker_alive'),'sdk_ready':d.get('sdk_ready'),'auto_probes':d.get('auto_probes'),'mem_available':mem['MemAvailable'].strip()}))
"""
            code, text = run('python3 -c ' + shlex.quote(audit))
            print(json.dumps({'containers_removed': names, 'dependencies_and_remote_script_removed': True,
                              'retained_private_state': ROOT + '/storage-state.json',
                              'image_retained': 'mcr.microsoft.com/playwright/python:v1.55.0-noble',
                              'host_check': json.loads(text) if code == 0 else {'error': 'health_check_failed'}}))
    finally:
        payload.clear()
        c.close()


if __name__ == '__main__':
    try:
        if '--serve' in sys.argv:
            # Loopback-only, no access logs, no payload files; requests serial.
            from http.server import HTTPServer, BaseHTTPRequestHandler
            import contextlib
            import io
            import hmac
            capability = os.environ.pop('PHASE3_BRIDGE_TOKEN', None)
            if not capability:
                raise RuntimeError('private_bridge_capability_required')
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def do_POST(self):
                    if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + capability):
                        self.send_error(403)
                        return
                    if self.path != '/phase3-private-bridge':
                        self.send_error(404)
                        return
                    result = io.StringIO()
                    try:
                        size = int(self.headers.get('Content-Length', '0'))
                        if not 0 < size <= 2_000_000:
                            raise ValueError()
                        payload = json.loads(self.rfile.read(size))
                        with contextlib.redirect_stdout(result):
                            main(payload)
                    except Exception as exc:
                        result = io.StringIO(json.dumps({'error_type': type(exc).__name__, 'error': 'private_bridge_failed'}))
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(result.getvalue().encode())
            HTTPServer(('127.0.0.1', 18763), Handler).serve_forever()
        else:
            main()
    except Exception as exc:
        print(json.dumps({'error_type': type(exc).__name__, 'error': 'private_bridge_failed'}))
        sys.exit(1)
