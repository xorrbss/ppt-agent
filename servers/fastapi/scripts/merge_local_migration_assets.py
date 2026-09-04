"""Preserve original asset paths and merge new local runtime assets without overwrite."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / 'app_data'
RUNTIME = APP / 'public-v2-local'
AUDIT = RUNTIME / 'migrations/20260901-full'
PRODUCT_DIRS = {'assets','authored_vnext','authored-checkpoints','authored-hybrid','authored-quality-receipts','authored-vnext','exports','font-embedding-allowlist','fonts','images','pptx-to-html','pptx-to-json','public-artifacts','source_images','source_locked','template-package-cas','templates','uploads','workspace_assets','workspace-artifacts'}


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def copy_missing(source,dest):
    if dest.exists():
        if not dest.is_file() or sha(source) != sha(dest):
            raise RuntimeError('asset_collision_without_overwrite:'+str(dest.relative_to(APP)))
        return False
    dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,dest)
    if sha(source) != sha(dest):
        raise RuntimeError('asset_copy_checksum_failed')
    return True


def main():
    report={'storageMode':'existing_app_data_paths_preserved','currentRuntimeFilesMerged':0,'currentRuntimeFilesAlreadyPresent':0,'publicArtifactObjectsCopied':0,'checks':{},'restored':[],'missingBeforeMigration':[]}
    # Only merge product files, never runtime secrets, databases, logs, or caches.
    for directory in sorted(PRODUCT_DIRS):
        start=RUNTIME/'app-data'/directory
        if not start.is_dir():continue
        for path in start.rglob('*'):
            if path.is_file() and not path.is_symlink():
                if directory == 'source_locked' and '.sqlite3' in path.name:
                    if path.suffix == '.sqlite3':
                        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
                            if any(db.execute('SELECT COUNT(*) FROM "'+t+'"').fetchone()[0] for(t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")):
                                raise RuntimeError('nonempty_current_source_locked_sidecar_requires_merge')
                    # The empty test registry must not overwrite the original
                    # source-package registry; its files stay in the backup.
                    continue
                result=copy_missing(path,APP/path.relative_to(RUNTIME/'app-data'))
                report['currentRuntimeFilesMerged' if result else 'currentRuntimeFilesAlreadyPresent']+=1
    s=sqlite3.connect((AUDIT/'source.sqlite3').as_uri()+'?mode=ro',uri=True);s.row_factory=sqlite3.Row
    files={}
    def check(table,row,path,expected=None,required=True):
        if not path.is_file():
            if required:report['missingBeforeMigration'].append({'table':table,'id':str(row['id']),'path':str(path),'sha256':expected,'sizeBytes':row['size_bytes'] if 'size_bytes' in row.keys() else None})
            return
        digest=files.setdefault(str(path),None)
        if digest is None:
            digest=sha(path);files[str(path)]=digest
        if expected and digest!=expected:raise RuntimeError('registered_asset_digest_mismatch:'+table+':'+str(row['id']))
        report['checks'][table]=report['checks'].get(table,0)+1
    for row in s.execute('SELECT * FROM imageasset'):
        value=unquote(row['path'])
        path=APP/value.split('/app_data/',1)[1] if '/app_data/' in value else Path(value)
        if not path.is_absolute():path=APP/path
        check('imageasset',row,path)
    for row in s.execute("SELECT * FROM workspace_assets WHERE state='ready'"):
        check('workspace_assets',row,APP/'workspace_assets'/row['storage_key'],row['sha256'])
    for row in s.execute("SELECT * FROM workspace_artifacts WHERE status='ready'"):
        check('workspace_artifacts',row,APP/row['storage_key'],row['sha256'])
    for row in s.execute('SELECT * FROM public_artifacts'):
        source=APP/'public-artifacts'/row['storage_key']
        check('public_artifacts',row,source,row['sha256'])
        if source.is_file():
            report['publicArtifactObjectsCopied']+=int(copy_missing(source,RUNTIME/'local-artifact-authority'/row['storage_key']))
    # Recover a missing export only from byte-identical existing files. Never
    # regenerate and call a different artifact the same historical download.
    missing=list(report['missingBeforeMigration'])
    for item in missing:
        if not item['sha256'] or not item['sizeBytes']:continue
        candidates=[]
        for root in (APP/'exports',APP/'workspace-artifacts',ROOT/'artifacts',ROOT/'output',ROOT/'outputs'):
            if not root.is_dir():continue
            for p in root.rglob('*'):
                try:
                    if p.is_file() and p.stat().st_size==item['sizeBytes']:candidates.append(p)
                except OSError:continue
        for p in candidates:
            if sha(p)==item['sha256']:
                copy_missing(p,Path(item['path']))
                report['restored'].append({'table':item['table'],'id':item['id'],'sha256':item['sha256']})
                report['missingBeforeMigration'].remove(item)
                report['checks'][item['table']]=report['checks'].get(item['table'],0)+1
                files[item['path']]=item['sha256']
                break
    (AUDIT/'asset-manifest.json').write_text(json.dumps(files,ensure_ascii=False,indent=2),encoding='utf-8')
    (AUDIT/'asset-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('missingBeforeMigration','restored')},ensure_ascii=False),flush=True)
    print(json.dumps({'restoredCount':len(report['restored']),'missingOriginalCount':len(report['missingBeforeMigration'])}),flush=True)


if __name__=='__main__':main()
