"""Temporary loopback-only GET proxy over verified SSH for isolated dashboard QA."""
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import argparse

import paramiko

parser=argparse.ArgumentParser()
parser.add_argument('--production',action='store_true')
args=parser.parse_args()
REMOTE_PORT=3000 if args.production else 3001

client = paramiko.SSHClient()
client.load_system_host_keys()
client.connect('100.92.238.77',username='thangma',
               key_filename=str(Path(__file__).parent/'.deploy/server_ed25519'),
               look_for_keys=False,allow_agent=False,timeout=15)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def do_GET(self):
        if not self.path.startswith('/') or self.path.startswith('//'):
            self.send_error(400)
            return
        connection = http.client.HTTPConnection('127.0.0.1',REMOTE_PORT,timeout=10)
        connection.sock = client.get_transport().open_channel(
            'direct-tcpip',('127.0.0.1',REMOTE_PORT),('127.0.0.1',0))
        try:
            connection.request('GET',self.path,headers={'Host':'127.0.0.1:'+str(REMOTE_PORT)})
            response = connection.getresponse()
            body = response.read()
            self.send_response(response.status)
            self.send_header('Content-Type',response.getheader('Content-Type','application/octet-stream'))
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            connection.close()


try:
    HTTPServer(('127.0.0.1',3002),Handler).serve_forever()
finally:
    client.close()
