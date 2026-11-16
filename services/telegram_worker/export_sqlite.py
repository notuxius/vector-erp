"""Export a stopped/snapshotted bot DB for import into an empty D1 schema.

Usage: python export_sqlite.py <snapshot.sqlite3> <private-output.sql>
Never commit the SQL file: it includes customers' sessions and requests.
"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

TABLES = ('meta', 'updates', 'sessions', 'requests', 'outbox', 'subscriptions', 'customer_profiles')

def export(source, destination):
    db = sqlite3.connect(f'file:{Path(source).resolve().as_posix()}?mode=ro', uri=True)
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    statements, manifest = [], {}
    for table in TABLES:
        if table in ('subscriptions', 'customer_profiles') and not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name=? AND type='table'", (table,)).fetchone():
            continue  # Legacy snapshots remain importable.
        columns = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
        rows = db.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
        manifest[table] = {'rows': len(rows), 'sha256': hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()}
        for row in rows:
            values = []
            for value in row:
                # SQLite quote() preserves strings, NULLs, numbers, and apostrophes.
                values.append(db.execute('SELECT quote(?)', (value,)).fetchone()[0])
            statements.append(f'INSERT INTO "{table}" ({",".join(columns)}) VALUES({",".join(values)});')
    # Preserve AUTOINCREMENT high water marks even if a historical row was removed.
    for table, sequence in db.execute("SELECT name,seq FROM sqlite_sequence WHERE name IN ('requests','outbox')"):
        statements.append(f"UPDATE sqlite_sequence SET seq=MAX(seq,{int(sequence)}) WHERE name='{table}';")
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('\n'.join(statements) + '\n', encoding='utf-8')
    output.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    db.close()
    print('Exported snapshot:', ', '.join(f'{table}={value["rows"]}' for table, value in manifest.items()))

if __name__ == '__main__':
    export(*sys.argv[1:])
