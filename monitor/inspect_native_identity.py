"""Read-only inspect raw owned probe responses; emit identity field names only."""
import os
import contextlib
import io
import json
import requests
from dotenv import dotenv_values
from app import parse_accounts
account = parse_accounts(dotenv_values(os.environ['ACCOUNTS_ENV'], interpolate=False))[-1]
os.environ['KAGGLE_API_TOKEN'] = account.token
os.environ['KAGGLE_USERNAME'] = account.username
original = requests.Session.send
records = []
def send(self, request, **kwargs):
    response = original(self, request, **kwargs)
    if 'KernelsApiService' in request.url:
        try:
            data = response.json()
            found = []
            def scan(value, path='root'):
                if not isinstance(value, dict):
                    return
                for k, v in value.items():
                    if isinstance(v, dict):
                        scan(v, path+'.'+k)
                    elif any(x in k.lower() for x in ('session', 'version', 'status', 'id')) and isinstance(v, (str,int)):
                        found.append({'path':path+'.'+k, 'value':v if isinstance(v,int) or k.lower()=='status' else '[string]'})
            scan(data)
            records.append({'method':request.url.split('/')[-1], 'status':response.status_code,
                'keys':list(data) if isinstance(data,dict) else [], 'identity_fields':found})
        except Exception:
            pass
    return response
requests.Session.send = send
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    from kagglesdk import KaggleClient
    from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelRequest, ApiGetKernelSessionStatusRequest
    with KaggleClient() as client:
        for cls, method in [(ApiGetKernelRequest,'get_kernel'),(ApiGetKernelSessionStatusRequest,'get_kernel_session_status')]:
            r = cls()
            r.user_name = account.username
            r.kernel_slug = 'queue-monitor-probe'
            r.version_label = 'v1'
            getattr(client.kernels.kernels_api_client,method)(r)
print(json.dumps(records))
