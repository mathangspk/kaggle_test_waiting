"""Online consistent SQLite backup inside the deployed persistent volume."""
from datetime import datetime, timezone
from pathlib import Path
import os
import sqlite3

source = Path(os.environ.get('MONITOR_DB', '/data/monitor.sqlite3'))
target = source.with_name('backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.sqlite3')
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
    assert dst.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
print('Consistent backup verified:', target.name)
