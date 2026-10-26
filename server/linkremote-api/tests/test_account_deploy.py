"""Deployment safety on disposable files/SQLite. Never reaches PM2/network."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import account_workspace_deploy as deploy

def identity(path):
    return {'size': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / 'live'; self.stage = self.base / '.stage-account-test'
        self.backups = Path(self.temp.name) / 'backups'
        self.base.mkdir(); self.stage.mkdir()
        for name in deploy.TARGETS:
            new = self.stage / name; new.parent.mkdir(parents=True, exist_ok=True)
            if name == 'account_sessions.py': shutil.copyfile(Path(__file__).parents[1] / name, new)
            else: new.write_text('new payload ' + name, encoding='utf8')
            if name != 'account_sessions.py':
                old = self.base / name; old.parent.mkdir(parents=True, exist_ok=True)
                old.write_text('old payload ' + name, encoding='utf8')
        (self.base / 'data').mkdir()
        self.db = self.base / 'data/linkremote.db'
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('CREATE TABLE tokens(token TEXT PRIMARY KEY,user_id INTEGER,created_at TEXT,expires_at TEXT)')
            conn.execute('INSERT INTO tokens VALUES (?,?,?,?)', ('synthetic-retained-token',1,'created','expires'))
            conn.commit()
        (self.base/'smtp.json').write_text('{"private":"synthetic-not-a-credential"}', encoding='utf8')
        (self.base/'release.json').write_text('{"old_release":"preserve"}', encoding='utf8')
        expected = {name: {'old': identity(self.base/name) if (self.base/name).exists() else None,
            'new': identity(self.stage/name)} for name in deploy.TARGETS}
        (self.stage/'expected.json').write_text(json.dumps(expected), encoding='utf8')
    def test_dryrun_never_creates_backup_or_mutates_files(self):
        deploy.preflight(self.base, self.stage)
        self.assertFalse(self.backups.exists())
        self.assertTrue((self.base/'app.py').read_text().startswith('old payload'))
    def test_stale_live_file_fails_closed_before_backup(self):
        (self.base/'app.py').write_text('changed externally', encoding='utf8')
        with self.assertRaises(ValueError): deploy.preflight(self.base, self.stage)
        self.assertFalse(self.backups.exists())
    def test_execute_preserves_tokens_config_release_and_private_backup(self):
        restarts=[]
        result=deploy.execute(self.base,self.stage,self.backups,
            restart=lambda: restarts.append('api-only'), health=lambda: None)
        self.assertEqual(restarts,['api-only'])
        self.assertEqual(result['state'],'updated')
        self.assertTrue(result['tokens_preserved'])
        self.assertTrue(result['configuration_preserved'])
        self.assertTrue((self.base/'app.py').read_text().startswith('new payload'))
        self.assertIn('old_release', (self.base/'release.json').read_text())
        backup=Path(result['backup'])
        self.assertTrue((backup/'app.py').read_text().startswith('old payload'))
        with closing(sqlite3.connect(backup/'database.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT token FROM tokens').fetchone()[0], 'synthetic-retained-token')
    def test_failed_restart_restores_code_but_never_overwrites_live_database(self):
        calls=[]
        def fail_once():
            calls.append(1)
            if len(calls)==1: raise RuntimeError('synthetic start failure')
        with self.assertRaises(deploy.DeploymentRolledBack):
            deploy.execute(self.base,self.stage,self.backups,restart=fail_once,health=lambda: None,rollback_health=lambda: None)
        self.assertEqual(len(calls),2)
        self.assertTrue((self.base/'app.py').read_text().startswith('old payload'))
        with closing(sqlite3.connect(self.db)) as conn:
            columns=[row[1] for row in conn.execute('PRAGMA table_info(tokens)')]
            self.assertIn('session_id', columns)
            conn.execute("INSERT INTO tokens(token,user_id,created_at,expires_at) VALUES ('old-code-login-1',1,'c','e')")
            conn.execute("INSERT INTO tokens(token,user_id,created_at,expires_at) VALUES ('old-code-login-2',1,'c','e')")
            conn.commit()
    def test_unlisted_target_is_rejected(self):
        expected=json.loads((self.stage/'expected.json').read_text()); expected['smtp.json']=expected['app.py']
        (self.stage/'expected.json').write_text(json.dumps(expected))
        with self.assertRaises(ValueError): deploy.preflight(self.base,self.stage)
    def test_code_rollback_requires_verified_old_api_health(self):
        calls=[]
        def new_health_failure(): raise RuntimeError('new health fails')
        with self.assertRaises(deploy.DeploymentRolledBack):
            deploy.execute(self.base,self.stage,self.backups,restart=lambda: None,
                health=new_health_failure,rollback_health=lambda: calls.append('old-health-verified'))
        self.assertEqual(calls,['old-health-verified'])
    def test_successful_pm2_restart_but_failed_old_health_is_not_rollback_success(self):
        def unhealthy(): raise RuntimeError('service unhealthy')
        with self.assertRaises(deploy.DeploymentRecoveryFailed):
            deploy.execute(self.base,self.stage,self.backups,restart=lambda: None,
                health=unhealthy,rollback_health=unhealthy)

if __name__=='__main__': unittest.main()
