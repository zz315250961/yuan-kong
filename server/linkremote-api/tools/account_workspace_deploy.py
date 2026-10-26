"""Guarded API/web update. No credential copying, remote-service or DB restore.

CLI is pinned to /opt/linkremote-api. Default is read-only preflight; --execute
backs up code and SQLite privately, applies additive schema and restarts only
the named account API. Failure restores code, never a live database snapshot.
"""
import argparse
from contextlib import closing
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

TARGETS = ('app.py', 'account_sessions.py', 'web/manage.html', 'web/assets/app.js', 'web/assets/app.css')

class DeploymentRolledBack(RuntimeError):
    pass

class DeploymentRecoveryFailed(RuntimeError):
    pass

def identity(path):
    return {'size': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

def regular(path):
    for part in (path, *path.parents):
        if part.is_symlink(): raise ValueError('Symlink rejected')
    if not stat.S_ISREG(path.stat().st_mode): raise ValueError('Not a regular file')

def preflight(base, stage):
    base, stage = Path(base), Path(stage)
    if stage.parent != base or not stage.name.startswith('.stage-account-'):
        raise ValueError('Stage outside intended root')
    regular(stage/'expected.json')
    expected = json.loads((stage/'expected.json').read_text(encoding='utf8'))
    if set(expected) != set(TARGETS): raise ValueError('Unexpected target inventory')
    for name in TARGETS:
        source, target = stage/name, base/name
        regular(source)
        if identity(source) != expected[name]['new']: raise ValueError('Stage hash mismatch')
        if expected[name]['old'] is None:
            if os.path.lexists(target): raise ValueError('New target already exists')
        else:
            regular(target)
            if identity(target) != expected[name]['old']: raise ValueError('Live hash mismatch')
    for name in ('data/linkremote.db','smtp.json','release.json'): regular(base/name)
    return expected

def atomic_copy(source, target, mode=0o644):
    regular(source)
    fd, temporary = tempfile.mkstemp(prefix='.account-swap-', dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as output, source.open('rb') as input_file:
            shutil.copyfileobj(input_file,output); output.flush(); os.fsync(output.fileno())
        os.chmod(temporary,mode)
        os.replace(temporary,target)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

def restart_api():
    subprocess.run(['pm2','restart','linkremote-api'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=25)

def check_health(require_sessions=True):
    for attempt in range(8):
        try:
            result=json.load(urllib.request.urlopen('http://127.0.0.1:21114/api/health',timeout=2))
            if result.get('ok') is not True: raise ValueError('Invalid health response')
            if not require_sessions: return
            try:
                urllib.request.urlopen('http://127.0.0.1:21114/api/sessions',timeout=2)
            except urllib.error.HTTPError as error:
                if error.code == 401: return
            raise ValueError('New sessions route not active')
        except (OSError,ValueError):
            if attempt == 7: raise
            time.sleep(.5)

def health_api():
    check_health(True)

def health_existing_api():
    check_health(False)

def execute(base,stage,backup_root,*,restart=restart_api,health=health_api,rollback_health=health_existing_api):
    base,stage,backup_root=Path(base),Path(stage),Path(backup_root)
    expected=preflight(base,stage)
    unchanged={name:identity(base/name) for name in ('smtp.json','release.json')}
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup_root.mkdir(parents=True,exist_ok=True)
    backup=backup_root/('linkremote-account-'+stamp)
    backup.mkdir(mode=0o700)
    for name in TARGETS:
        if expected[name]['old'] is not None:
            destination=backup/name; destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(base/name,destination); os.chmod(destination,0o600)
            if identity(destination)!=expected[name]['old']: raise ValueError('Backup hash mismatch')
    with closing(sqlite3.connect(base/'data/linkremote.db',timeout=10)) as source:
        with closing(sqlite3.connect(backup/'database.sqlite3')) as destination:
            source.backup(destination)
            if destination.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Backup integrity failure')
    os.chmod(backup/'database.sqlite3',0o600)
    schema_spec=importlib.util.spec_from_file_location('account_cutover_schema',stage/'account_sessions.py')
    schema=importlib.util.module_from_spec(schema_spec); schema_spec.loader.exec_module(schema)
    connection=sqlite3.connect(base/'data/linkremote.db',timeout=10)
    try:
        connection.execute('BEGIN IMMEDIATE')
        before=connection.execute('SELECT token,user_id,created_at,expires_at FROM tokens ORDER BY token').fetchall()
        schema.init_session_schema(connection)
        after=connection.execute('SELECT token,user_id,created_at,expires_at FROM tokens ORDER BY token').fetchall()
        if before!=after: raise ValueError('Existing token mutation')
        connection.commit()
    except Exception:
        connection.rollback(); raise
    finally:
        connection.close()
    replaced=[]
    try:
        # Module/assets first, entry point last. Each replacement is atomic.
        for name in (*TARGETS[1:],TARGETS[0]):
            if identity(stage/name)!=expected[name]['new']: raise ValueError('Stage changed after preflight')
            atomic_copy(stage/name,base/name); replaced.append(name)
        restart(); health()
        if any(identity(base/name)!=value for name,value in unchanged.items()):
            raise ValueError('Configuration or manifest changed')
        return {'state':'updated','backup':str(backup),'tokens_preserved':True,
            'configuration_preserved':True,'files':{name:identity(base/name) for name in TARGETS}}
    except Exception as error:
        for name in reversed(replaced):
            if expected[name]['old'] is not None: atomic_copy(backup/name,base/name)
            # A newly added, now unused module is retained, not destructively removed.
        try:
            restart()
            # Old code has no sessions route; require actual health, not only PM2.
            rollback_health()
        except Exception as recovery_error:
            raise DeploymentRecoveryFailed('Recovery health not verified; private backup: '+str(backup)) from recovery_error
        raise DeploymentRolledBack('Code restored and old API health verified; additive DB retained; private backup: '+str(backup)) from error

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--stage',required=True)
    parser.add_argument('--execute',action='store_true'); args=parser.parse_args()
    base=Path('/opt/linkremote-api'); stage=Path(args.stage)
    if args.execute: result=execute(base,stage,Path('/var/backups'))
    else:
        expected=preflight(base,stage)
        result={'state':'preflight-passed','executed':False,'files':expected}
    print(json.dumps(result))

if __name__=='__main__': main()
