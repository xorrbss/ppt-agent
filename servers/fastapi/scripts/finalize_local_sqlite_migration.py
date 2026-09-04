"""Verify, quiesce stale work, and atomically select a verified migration DB."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb


NONTERMINAL = {
    'presentation_generation_jobs': ('state', ('queued','running'), {'state':'failed','last_error':{'code':'migrated_interrupted_job_not_replayed','retryable':False},'resume_allowed':False,'failed_at':None}),
    'authored_vnext_export_jobs': ('state', ('queued','running','cancel_requested'), {'state':'failed','failure_code':'migrated_interrupted_job_not_replayed','failure_detail':'Original request retained; automatic replay disabled during database migration','completed_at':None}),
    'async_presentation_generation_tasks': ('status', ('pending','processing','running'), {'status':'error','error':{'code':'migrated_interrupted_job_not_replayed','retryable':False},'message':'Original request retained; automatic replay disabled during database migration'}),
}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--candidate',required=True);parser.add_argument('--report',required=True);parser.add_argument('--audit',required=True);args=parser.parse_args()
    if not args.candidate.startswith('presenton_migration_'):raise RuntimeError('candidate_database_required')
    migration=json.loads(Path(args.report).read_text(encoding='utf-8'))
    if migration.get('status')!='verified':raise RuntimeError('migration_readback_not_verified')
    source=sqlite3.connect(Path(args.source).resolve().as_uri()+'?mode=ro',uri=True);source.execute('PRAGMA query_only=ON')
    expected_presentations=source.execute('select count(*) from presentations').fetchone()[0]
    expected_slides=source.execute('select count(*) from slides').fetchone()[0]
    source_admin=source.execute('select id from "user" where username=?',('admin',)).fetchone()[0]
    audit={'schemaVersion':1,'candidate':args.candidate,'checks':{},'quiesced':{},'cutover':False,'previousDatabase':'postgres_before_full_migration_20260901'}
    if Path(args.audit).exists():
        previous=json.loads(Path(args.audit).read_text(encoding='utf-8'))
        if previous.get('candidate')==args.candidate and not previous.get('cutover'):
            audit['quiesced']=previous.get('quiesced',{})
    with psycopg.connect(host='127.0.0.1',port=21000,dbname=args.candidate,user='presenton_rc') as c:
        c.execute("SET TIME ZONE 'UTC'")
        for table,(column,states,changes) in NONTERMINAL.items():
            with c.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute(sql.SQL('SELECT * FROM {} WHERE {}=ANY(%s)').format(sql.Identifier(table),sql.Identifier(column)),(list(states),))
                rows=cur.fetchall()
            for row in rows:
                c.execute("INSERT INTO local_sqlite_migration.conflicts VALUES (%s,%s,%s,NULL,%s) ON CONFLICT DO NOTHING",(table,'interrupted:'+str(row['id']),Jsonb(json.loads(json.dumps(row,default=str))),'original_nonterminal_state_archived_before_quiesce'))
            if rows:
                assignments=[];values=[]
                for key,value in changes.items():
                    if key in ('failed_at','completed_at'):value=datetime.now(timezone.utc)
                    if isinstance(value,dict):value=Jsonb(value)
                    assignments.append(sql.SQL('{}=%s').format(sql.Identifier(key)));values.append(value)
                values.append(list(states))
                c.execute(sql.SQL('UPDATE {} SET {} WHERE {}=ANY(%s)').format(sql.Identifier(table),sql.SQL(',').join(assignments),sql.Identifier(column)),values)
            audit['quiesced'][table]=audit['quiesced'].get(table,0)+len(rows)
        # The imported admin must see every legacy presentation.
        actual_admin=c.execute('select count(*) from presentations where owner_id=%s',(source_admin,)).fetchone()[0]
        actual_slides=c.execute('select count(*) from slides s join presentations p on p.id=s.presentation where p.owner_id=%s',(source_admin,)).fetchone()[0]
        audit['checks']['adminPresentations']={'expected':expected_presentations,'actual':actual_admin}
        audit['checks']['adminSlides']={'expected':expected_slides,'actual':actual_slides}
        if actual_admin!=expected_presentations or actual_slides!=expected_slides:raise RuntimeError('admin_presentation_or_slide_count_mismatch')
        owner_mismatches=c.execute('select count(*) from slides s join presentations p on p.id=s.presentation where s.owner_id IS DISTINCT FROM p.owner_id').fetchone()[0]
        audit['checks']['slideOwnerMismatches']=owner_mismatches
        if owner_mismatches:raise RuntimeError('slide_owner_mismatch')
        # Verify all imported source tables using the per-table audited counts.
        for table,result in migration['tables'].items():
            current=c.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]
            expected=result.get('after',current)
            if current!=expected:raise RuntimeError('post_import_table_count_drift:'+table)
        # Require every enabled FK to be satisfied without disabling triggers.
        violations=[]
        for table,parent,name,cols,refs in c.execute("select r.relname,fr.relname,k.conname,array(select a.attname from unnest(k.conkey) with ordinality x(n,o) join pg_attribute a on a.attrelid=k.conrelid and a.attnum=x.n order by x.o),array(select a.attname from unnest(k.confkey) with ordinality x(n,o) join pg_attribute a on a.attrelid=k.confrelid and a.attnum=x.n order by x.o) from pg_constraint k join pg_class r on r.oid=k.conrelid join pg_class fr on fr.oid=k.confrelid where k.contype='f' and k.connamespace='public'::regnamespace"):
            join=sql.SQL(' AND ').join(sql.SQL('c.{}=p.{}').format(sql.Identifier(a),sql.Identifier(b)) for a,b in zip(cols,refs,strict=True));nonnull=sql.SQL(' AND ').join(sql.SQL('c.{} IS NOT NULL').format(sql.Identifier(a)) for a in cols)
            count=c.execute(sql.SQL('SELECT COUNT(*) FROM {} c WHERE {} AND NOT EXISTS(SELECT 1 FROM {} p WHERE {})').format(sql.Identifier(table),nonnull,sql.Identifier(parent),join)).fetchone()[0]
            if count:violations.append({'constraint':name,'count':count})
        audit['checks']['foreignKeyViolations']=violations
        if violations:raise RuntimeError('foreign_key_violations')
        c.commit()
    source.close()
    Path(args.audit).write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    # No application process is running. Keep the original DB under a durable
    # rollback name and switch the verified candidate in one transaction.
    with psycopg.connect(host='127.0.0.1',port=21000,dbname='template1',user='presenton_rc') as c:
        active=c.execute("select datname,count(*) from pg_stat_activity where datname in ('postgres',%s) and backend_type='client backend' and pid<>pg_backend_pid() group by datname",(args.candidate,)).fetchall()
        if active:raise RuntimeError('database_cutover_connections_active')
        c.execute(sql.SQL('ALTER DATABASE postgres RENAME TO {}').format(sql.Identifier(audit['previousDatabase'])))
        c.execute(sql.SQL('ALTER DATABASE {} RENAME TO postgres').format(sql.Identifier(args.candidate)))
        c.commit()
    audit['cutover']=True;audit['completedAt']=datetime.now(timezone.utc).isoformat()
    Path(args.audit).write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'cutover':True,'adminPresentations':expected_presentations,'adminSlides':expected_slides,'foreignKeyViolations':0,'quiesced':audit['quiesced']}),flush=True)


if __name__=='__main__':
    try:main()
    except psycopg.Error as exc:
        print(f'Finalization stopped: {type(exc).__name__}; {exc.diag.message_primary}; constraint={exc.diag.constraint_name}')
        raise SystemExit(1) from exc
