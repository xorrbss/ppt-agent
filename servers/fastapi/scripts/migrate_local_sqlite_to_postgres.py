"""Merge a read-only SQLite snapshot into an offline local PostgreSQL candidate.

No constraints, authentication, or triggers are disabled. Existing application
data is retained. Conflicting singleton/seed rows are archived in a private
migration schema, while explicit rules select the active row. Cutover is a
separate operation, performed only after this script verifies every source row.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg.copy import LibpqWriter
from psycopg.generators import copy_to


class FlushingCopyWriter(LibpqWriter):
    """Bound libpq's pending buffer for multi-hundred-MB embedded slide JSON.

    Without flushing, a whole table can accumulate in libpq on Windows and
    repeated buffer compaction becomes quadratic. This changes only transport;
    the COPY format, transaction and complete row readback stay unchanged.
    """

    def write(self, data):
        view = memoryview(data)
        for offset in range(0, len(view), 128 * 1024):
            self.connection.wait(copy_to(self._pgconn, view[offset:offset + 128 * 1024], flush=True))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def normalize(value, kind):
    if value is None:
        return None
    if kind == 'uuid':
        return str(uuid.UUID(str(value)))
    if kind == 'boolean':
        return bool(value)
    if kind.startswith('timestamp'):
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    if kind == 'json' and isinstance(value, str):
        # PostgreSQL JSON preserves the input text. Comparing that exact text
        # avoids parsing/reserializing hundreds of MB of embedded slide assets.
        return value
    if kind in ('json', 'jsonb'):
        return canonical(json.loads(value) if isinstance(value, str) else value)
    if kind in ('integer', 'bigint', 'smallint'):
        return int(value)
    return value


def digest(row, columns):
    h = hashlib.sha256()
    for col, kind in columns.items():
        value = normalize(row.get(col), kind)
        part = b'N' if value is None else b'V' + str(value).encode('utf-8')
        h.update(len(part).to_bytes(8, 'big'))
        h.update(part)
    return h.hexdigest()


class Migration:
    def __init__(self, source, connection, report_path):
        self.s = source
        self.p = connection
        self.report_path = report_path
        self.report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {'status': 'running', 'tables': {}, 'conflictsArchived': {}, 'eventCursorOffset': None}
        self.source_tables = [r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        self.types = {}
        self.keys = {}
        self.dependencies = {}
        for table in self.source_tables:
            self.types[table] = dict(connection.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (table,)).fetchall())
            info = source.execute(f'PRAGMA table_info("{table}")').fetchall()
            self.keys[table] = [r[1] for r in sorted(info, key=lambda x: x[5]) if r[5]]
            missing = set(r[1] for r in info) - self.types[table].keys()
            if missing:
                raise RuntimeError(f'source_columns_missing:{table}:{sorted(missing)}')
            self.dependencies[table] = set(r[0].strip('"') for r in connection.execute("SELECT confrelid::regclass::text FROM pg_constraint WHERE contype='f' AND conrelid=%s::regclass", ('public.' + table,)).fetchall()) - {table}
        self.offset = self.report['eventCursorOffset']
        if self.offset is None:
            self.offset = connection.execute('SELECT COALESCE(MAX(cursor),0) FROM workspace_operation_events').fetchone()[0]
        self.report['eventCursorOffset'] = self.offset
        connection.execute('CREATE SCHEMA IF NOT EXISTS local_sqlite_migration')
        connection.execute('REVOKE ALL ON SCHEMA local_sqlite_migration FROM PUBLIC')
        connection.execute('CREATE TABLE IF NOT EXISTS local_sqlite_migration.conflicts (table_name text NOT NULL, key_digest text NOT NULL, source_row jsonb NOT NULL, previous_target_row jsonb, resolution text NOT NULL, PRIMARY KEY(table_name,key_digest))')
        self.admissions = {}
        for row in source.execute('SELECT * FROM public_operation_admissions'):
            d = dict(row)
            if d['state'] not in ('succeeded', 'failed', 'cancelled', 'expired'):
                raise RuntimeError('nonterminal_legacy_admission_requires_review')
            policy = source.execute('SELECT * FROM public_policy_profiles WHERE version=?', (d['policy_version'],)).fetchone()
            seconds = policy['generation_deadline_seconds' if d['operation_kind'] == 'generation' else 'export_deadline_seconds']
            deadline = datetime.fromisoformat(d['queued_at']) + timedelta(seconds=seconds)
            self.admissions[str(uuid.UUID(d['operation_id']))] = {**d, 'deadline_at': deadline.isoformat()}
        connection.commit()

    def archive(self, table, source, target, resolution):
        key = hashlib.sha256(canonical([source.get(k) for k in self.keys[table]]).encode()).hexdigest()
        self.p.execute('INSERT INTO local_sqlite_migration.conflicts VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING', (table, key, Jsonb(json.loads(canonical(source))), Jsonb(json.loads(canonical(target))) if target else None, resolution))
        self.report['conflictsArchived'][table] = self.report['conflictsArchived'].get(table, 0) + 1

    def transform(self, table, row):
        d = dict(row)
        if table == 'workspace_operation_events':
            d['cursor'] += self.offset
        if table == 'workspace_outbox' and d.get('event_cursor') is not None:
            d['event_cursor'] += self.offset
            payload = json.loads(d['payload'])
            if payload.get('event_cursor') is not None:
                payload['event_cursor'] += self.offset
                d['payload'] = canonical(payload)
                d['payload_hash'] = hashlib.sha256(d['payload'].encode()).hexdigest()
        if table == 'workspace_operations':
            admission = self.admissions.get(str(uuid.UUID(d['id'])))
            if admission:
                d.update(contract_version='public-generation-export/v1', execution_profile_version=admission['policy_version'], phase='legacy_admission', deadline_at=admission['deadline_at'])
        if table == 'public_operation_admissions':
            admission = self.admissions[str(uuid.UUID(d['operation_id']))]
            op = self.s.execute('SELECT fencing_token FROM workspace_operations WHERE id=?', (d['operation_id'],)).fetchone()
            d.update(resource_units=max(1, int(d['slide_count'] or 0)), policy_digest=hashlib.sha256(d['policy_version'].encode()).hexdigest(), deadline_at=admission['deadline_at'], fence=op[0] or 0)
        for col, value in list(d.items()):
            if self.types[table][col] == 'boolean' and value is not None:
                d[col] = bool(value)
        return d

    def key(self, table, row):
        return tuple(normalize(row[k], self.types[table][k]) for k in self.keys[table])

    def select(self, table, columns):
        return sql.SQL(',').join(sql.SQL('{}::text AS {}').format(sql.Identifier(c), sql.Identifier(c)) if self.types[table][c] in ('json','jsonb') else sql.Identifier(c) for c in columns)

    def run_table(self, table):
        started = time.monotonic()
        total = self.s.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        before = self.p.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]
        previous = self.report['tables'].get(table)
        if previous and previous.get('source') == total and previous.get('after',before) == before:
            return
        if table == 'alembic_version':
            for row in self.s.execute('SELECT * FROM alembic_version'):
                self.archive(table, dict(row), None, 'target_schema_revision_retained')
            self.p.commit()
            self.report['tables'][table] = {'source': total, 'before': before, 'archived': total}
            return
        if not total:
            self.report['tables'][table] = {'source': 0, 'before': before, 'after': before, 'verified': 0}
            return
        info = self.s.execute(f'PRAGMA table_info("{table}")').fetchall()
        cols = [r[1] for r in info]
        if table == 'workspace_operations':
            cols += ['contract_version','execution_profile_version','phase','deadline_at']
        if table == 'public_operation_admissions':
            cols += ['resource_units','policy_digest','deadline_at','fence']
        check_cols = {c:self.types[table][c] for c in cols}
        with self.p.cursor(row_factory=dict_row) as cur:
            cur.execute(sql.SQL('SELECT {} FROM {}').format(self.select(table, cols), sql.Identifier(table)))
            existing = {self.key(table,r):r for r in cur}
        # Seed/settings collisions are small. Destination user documents never
        # enter these update paths; conflicting document IDs fail closed.
        expected = {}
        archived_only = 0
        self.p.execute(sql.SQL('CREATE TEMP TABLE import_rows (LIKE {} INCLUDING DEFAULTS) ON COMMIT DROP').format(sql.Identifier(table)))
        source_rows = self.s.execute(f'SELECT * FROM "{table}"')
        if table == 'workspace_operations':
            # Self-FK retry parents must precede their descendants.
            pending = [dict(r) for r in source_rows]
            ordered = []; available = {k[0] for k in existing}
            while pending:
                ready = [r for r in pending if not r['retry_of_operation_id'] or str(uuid.UUID(r['retry_of_operation_id'])) in available]
                if not ready: raise RuntimeError('workspace_retry_dependency_cycle')
                for r in ready:
                    available.add(str(uuid.UUID(r['id']))); ordered.append(r); pending.remove(r)
            source_rows = ordered
        updates = []
        insert_rows = 0
        with self.p.cursor() as cur:
            with cur.copy(sql.SQL('COPY import_rows ({}) FROM STDIN').format(sql.SQL(',').join(map(sql.Identifier, cols))), writer=FlushingCopyWriter(cur)) as copy:
                for source_row in source_rows:
                    raw = dict(source_row); row = self.transform(table, raw)
                    key = self.key(table,row)
                    target = existing.get(key)
                    if target:
                        if digest(row,check_cols) != digest(target,check_cols):
                            if table == 'user':
                                if any(normalize(row[k],self.types[table][k]) != normalize(target[k],self.types[table][k]) for k in ['id','username','hashed_password','auth_version','is_active','is_superuser']):
                                    raise RuntimeError('user_conflict_preserved_no_password_reset')
                                updates.append(('archive',raw,target,'existing_account_slot_and_session_retained'))
                                row = target
                            elif table == 'provider_settings':
                                updates.append(('archive',raw,target,'source_settings_merged_missing_keys_current_credentials_retained'))
                                config = json.loads(row['config']); config.update(json.loads(target['config']))
                                row['config'] = canonical(config)
                                row['revision'] = max(row['revision'],target['revision']) + 1
                                updates.append(('update',row,target,None))
                            elif table in ('template_v2','template_v2_revisions'):
                                updates.append(('archive',raw,target,'source_template_revision_restored_previous_seed_retained_in_archive'))
                                updates.append(('update',row,target,None))
                            elif table == 'public_policy_profiles':
                                updates.append(('archive',raw,target,'current_runtime_policy_retained'))
                                row = target
                            else:
                                raise RuntimeError(f'unexpected_primary_key_conflict:{table}')
                        expected[key] = digest(row,check_cols)
                        continue
                    if table == 'public_quota_counters':
                        matching = next((v for v in existing.values() if all(row[k]==v[k] for k in ['policy_version','scope_type','scope_key','operation_kind'])),None)
                        if matching:
                            updates.append(('archive',raw,matching,'equivalent_live_quota_scope_retained'))
                            archived_only += 1
                            continue
                    expected[key] = digest(row,check_cols)
                    copy.write_row([row.get(c) for c in cols]); insert_rows += 1
        for kind,row,target,resolution in updates:
            if kind == 'archive': self.archive(table,row,target,resolution)
            else:
                nonkeys = [c for c in cols if c not in self.keys[table]]
                query = sql.SQL('UPDATE {} SET {} WHERE {}').format(sql.Identifier(table),sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(c)) for c in nonkeys),sql.SQL(' AND ').join(sql.SQL('{}=%s').format(sql.Identifier(c)) for c in self.keys[table]))
                self.p.execute(query,[row[c] for c in nonkeys]+[row[c] for c in self.keys[table]])
        if insert_rows:
            self.p.execute(sql.SQL('INSERT INTO {} ({}) SELECT {} FROM import_rows').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,cols)),sql.SQL(',').join(map(sql.Identifier,cols))))
        actual = {}
        with self.p.cursor(name='verify_rows',row_factory=dict_row) as cur:
            cur.itersize = 8
            cur.execute(sql.SQL('SELECT {} FROM {}').format(self.select(table,cols),sql.Identifier(table)))
            for row in cur:
                key = self.key(table,row)
                if key in expected: actual[key] = digest(row,check_cols)
        if actual != expected:
            bad = next((k for k in expected if actual.get(k) != expected[k]),None)
            if bad is not None:
                for raw in self.s.execute(f'SELECT * FROM "{table}"'):
                    original = self.transform(table,raw)
                    if self.key(table,original) == bad:
                        with self.p.cursor(row_factory=dict_row) as check:
                            check.execute(sql.SQL('SELECT {} FROM {} WHERE {}').format(self.select(table,cols),sql.Identifier(table),sql.SQL(' AND ').join(sql.SQL('{}=%s').format(sql.Identifier(c)) for c in self.keys[table])),bad)
                            saved = check.fetchone()
                        fields = [c for c in cols if normalize(original.get(c),check_cols[c]) != normalize(saved.get(c),check_cols[c])]
                        raise RuntimeError(f'row_readback_digest_mismatch:{table}:fields={fields}')
            raise RuntimeError(f'row_readback_digest_mismatch:{table}:expected={len(expected)}:actual={len(actual)}')
        after = self.p.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]
        if after != before + insert_rows:
            raise RuntimeError(f'row_count_mismatch:{table}')
        self.p.commit()
        self.report['tables'][table] = {'source':total,'before':before,'inserted':insert_rows,'after':after,'verified':len(expected),'archivedOnly':archived_only,'seconds':round(time.monotonic()-started,2)}
        self.report_path.write_text(json.dumps(self.report,indent=2),encoding='utf-8')
        print(f'{table}: source={total} added={insert_rows} verified={len(expected)}',flush=True)

    def run(self):
        remaining = set(self.source_tables)
        while remaining:
            ready = sorted(t for t in remaining if not (self.dependencies[t] & remaining))
            if not ready: raise RuntimeError('table_dependency_cycle:'+','.join(sorted(remaining)))
            for table in ready:
                self.run_table(table); remaining.remove(table)
        # Event sequence must follow remapped cursors, so later writes cannot
        # collide. All constraints and triggers remained enabled throughout.
        seq = self.p.execute("SELECT pg_get_serial_sequence('workspace_operation_events','cursor')").fetchone()[0]
        if seq:
            self.p.execute('SELECT setval(%s,(SELECT MAX(cursor) FROM workspace_operation_events),true)',(seq,))
        self.p.commit()
        self.report['status'] = 'verified'
        self.report_path.write_text(json.dumps(self.report,indent=2),encoding='utf-8')
        print('All source tables accounted for; source-row readback verified.',flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    if not args.database.startswith('presenton_migration_'):
        raise RuntimeError('offline_candidate_database_required')
    source = sqlite3.connect(Path(args.source).resolve().as_uri()+'?mode=ro',uri=True)
    source.row_factory = sqlite3.Row
    source.execute('PRAGMA query_only=ON')
    # Metadata probes use tuples; source rows need both tuple indexing and keys.
    with psycopg.connect(host='127.0.0.1',port=21000,dbname=args.database,user='presenton_rc',connect_timeout=10) as conn:
        # SQLite DATETIME values are application UTC without an offset. Do not
        # interpret them in the Windows/PostgreSQL server's Asia/Seoul timezone.
        conn.execute("SET TIME ZONE 'UTC'")
        Migration(source,conn,Path(args.report)).run()
    source.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Do not print DSNs, SQL parameter values, password hashes, or row data.
        if isinstance(exc,psycopg.Error):
            print(f'Migration stopped: {type(exc).__name__}; {exc.diag.message_primary}; constraint={exc.diag.constraint_name}',file=sys.stderr)
        else:
            print(f'Migration stopped: {type(exc).__name__}: {exc}',file=sys.stderr)
        raise SystemExit(1)
