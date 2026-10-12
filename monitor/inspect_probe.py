"""Read-only native SDK inspection for owned dedicated probes; no token output."""
import contextlib
import io
import json
import os
from dotenv import dotenv_values
from app import parse_accounts
records = []
accounts = parse_accounts(dotenv_values(os.environ['ACCOUNTS_ENV'], interpolate=False))
for account in accounts:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        os.environ['KAGGLE_API_TOKEN'] = account.token
        os.environ['KAGGLE_USERNAME'] = account.username
        try:
            from kagglesdk import KaggleClient
            from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelRequest
            request = ApiGetKernelRequest()
            request.user_name = account.username
            request.kernel_slug = 'queue-monitor-probe'
            with KaggleClient() as client:
                response = client.kernels.kernels_api_client.get_kernel(request)
            records.append({'account':account.username, 'ref':response.metadata.ref,
                'version':response.metadata.current_version_number,
                'blob_type':type(response.blob).__name__, 'blob_fields':response.blob.__doc__,
                'metadata_id':response.metadata.id})
        except Exception as exc:
            records.append({'account':account.username, 'error':type(exc).__name__,
                'status':getattr(getattr(exc,'response',None),'status_code',None)})
print(json.dumps(records))
