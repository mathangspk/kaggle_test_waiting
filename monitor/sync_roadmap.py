"""Generate a timestamped, credential-free GitHub roadmap snapshot for deployment."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

repo = 'mathangspk/kaggle_test_waiting'
result = subprocess.run(['gh', 'issue', 'list', '--repo', repo, '--state', 'all',
    '--limit', '100', '--json', 'number,title,state,url,body,updatedAt'],
    capture_output=True, text=True, encoding='utf-8', check=True)
snapshot = {
    'repo': repo,
    'synced_at': datetime.now(timezone.utc).isoformat(),
    'issues': json.loads(result.stdout),
    'dependencies': [{'from':a, 'to':b} for a,b in [
        (1,2),(1,3),(2,5),(3,5),(3,6),(6,7),(5,9),(7,8),(8,9),
        (11,13),(12,13)]],
    'blocked': [4],
}
Path(__file__).with_name('roadmap.json').write_text(
    json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
print('Roadmap snapshot synchronized:', len(snapshot['issues']), 'issues')
