"""Copy existing SQLite accounts into a dedicated local PostgreSQL, without resets.

Source SQLite is read-only. Existing destination accounts are never overwritten.
The original password hashes and account IDs are retained; sessions and documents
are deliberately outside this account-only import.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import sqlite3
import uuid

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    args = parser.parse_args()
    target = make_url(os.environ['DATABASE_URL'])
    if target.get_backend_name() != 'postgresql' or target.host != '127.0.0.1':
        raise RuntimeError('account_import_requires_local_postgresql')
    source = sqlite3.connect(Path(args.source).resolve().as_uri() + '?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    source.execute('PRAGMA query_only=ON')
    rows = source.execute('SELECT * FROM "user"').fetchall()
    source.close()
    imported = 0
    with psycopg.connect(target.set(drivername='postgresql').render_as_string(hide_password=False)) as conn:
        columns = {r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='user'")}
        for row in rows:
            values = {k: row[k] for k in row.keys() if k in columns}
            values['id'] = uuid.UUID(values['id'])
            existing = conn.execute('SELECT id, username, hashed_password FROM "user" WHERE id=%s OR username=%s', (values['id'], values['username'])).fetchall()
            if existing:
                if len(existing) != 1 or existing[0] != (values['id'], values['username'], values['hashed_password']):
                    raise RuntimeError('account_import_conflict_no_changes_committed')
                continue
            for key in ('is_active', 'is_superuser', 'is_verified'):
                values[key] = bool(values[key])
            for key in ('created_at', 'deletion_requested_at'):
                if values.get(key):
                    values[key] = datetime.datetime.fromisoformat(values[key])
            # Preserve the existing target's primary bootstrap slot; this does
            # not change the imported user's role, password, or identifier.
            if values.get('admin_slot') and conn.execute('SELECT 1 FROM "user" WHERE admin_slot=%s', (values['admin_slot'],)).fetchone():
                values['admin_slot'] = None
            names = list(values)
            conn.execute(sql.SQL('INSERT INTO "user" ({}) VALUES ({})').format(sql.SQL(',').join(map(sql.Identifier, names)), sql.SQL(',').join(sql.Placeholder() for _ in names)), [values[k] for k in names])
            saved = conn.execute('SELECT hashed_password, auth_version FROM "user" WHERE id=%s', (values['id'],)).fetchone()
            if saved != (row['hashed_password'], row['auth_version']):
                raise RuntimeError('account_import_readback_failed')
            imported += 1
    print(json.dumps({'sourceAccounts': len(rows), 'importedAccounts': imported, 'passwordHashesPreserved': True, 'documentsImported': False}))


if __name__ == '__main__':
    main()
