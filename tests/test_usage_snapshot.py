from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from test_observation import routing
from routing_core.observation import _validate_observation_usage

SCRIPT = Path(__file__).resolve().parents[1] / 'plugins/codex-task-routing/scripts/usage_snapshot.py'
spec = importlib.util.spec_from_file_location('usage_snapshot_test', SCRIPT)
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)
interval_spec = importlib.util.spec_from_file_location('usage_interval_test', SCRIPT.parent / 'usage_interval.py')
interval_module = importlib.util.module_from_spec(interval_spec)
interval_spec.loader.exec_module(interval_module)
ROOT = '019c0000-0000-7000-8000-000000000001'
CHILD = '019c0000-0000-7000-8000-000000000002'
OTHER = '019c0000-0000-7000-8000-000000000003'


def enrollment():
    return {'schema_version': 1, 'root_id': ROOT, 'threads': [
        {'thread_id': ROOT, 'parent_id': None, 'depth': 0},
        {'thread_id': CHILD, 'parent_id': ROOT, 'depth': 1},
    ]}


class UsageSnapshotTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parent / '.tmp-runtime-tests'
        base.mkdir(exist_ok=True)
        self.base = base / ('usage-' + uuid.uuid4().hex)
        self.base.mkdir()
        self.path = self.base / 'state_5.sqlite'
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, source TEXT, tokens_used INTEGER, model TEXT, reasoning_effort TEXT, cli_version TEXT, title TEXT, rollout_path TEXT)')
            db.execute('PRAGMA schema_version = 123')
            db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)', (ROOT, 'vscode', 200, 'gpt-6-astra', 'xhigh', '0.165.0-alpha.1.2', 'private-title', 'private-path'))
            child_source = json.dumps({'subagent': {'thread_spawn': {'parent_thread_id': ROOT, 'depth': 1, 'agent_nickname': 'private-name'}}})
            db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)', (CHILD, child_source, 50, 'gpt-6.1-terra', 'xhigh', '0.165.0-alpha.1.2', 'private-title', 'private-path'))
            db.commit()

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_reads_only_allowlisted_metadata_and_never_writes(self):
        before = self.path.read_bytes()
        original = sqlite3.connect
        with patch.object(usage.sqlite3, 'connect') as connect:
            def guarded(*args, **kwargs):
                db = original(*args, **kwargs)
                def allow(action, first, second, database, source):
                    if action == sqlite3.SQLITE_READ and first == 'threads' and second not in {'id', 'source', 'tokens_used', 'model', 'reasoning_effort', 'cli_version'}:
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                db.set_authorizer(allow)
                return db
            connect.side_effect = guarded
            result = usage.read_snapshot(self.path, enrollment(), CHILD)
        self.assertEqual(result['total_tokens'], 50)
        self.assertEqual(result['parent_id'], ROOT)
        self.assertFalse(result['runtime_model_verified'])
        self.assertNotIn('private', json.dumps(result))
        self.assertEqual(before, self.path.read_bytes())

    def test_unenrolled_target_never_opens_database(self):
        with patch.object(usage.sqlite3, 'connect') as connect:
            with self.assertRaises(usage.RoutingError):
                usage.read_snapshot(self.path, enrollment(), OTHER)
            connect.assert_not_called()

    def test_wrong_parent_unknown_schema_and_reset_are_rejected(self):
        wrong = enrollment()
        wrong['threads'][1]['depth'] = 2
        with self.assertRaises(usage.RoutingError): usage.read_snapshot(self.path, wrong, CHILD)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE threads SET tokens_used = -1 WHERE id = ?', (ROOT,))
            db.commit()
        with self.assertRaises(usage.RoutingError): usage.read_snapshot(self.path, enrollment(), ROOT)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE threads SET tokens_used = 200, cli_version = 'unknown' WHERE id = ?", (ROOT,))
            db.commit()
        with self.assertRaises(usage.RoutingError): usage.read_snapshot(self.path, enrollment(), ROOT)
        bad = self.base / 'other' / 'state_5.sqlite'
        bad.parent.mkdir()
        with closing(sqlite3.connect(bad)) as db:
            db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, source TEXT)')
            db.commit()
        with self.assertRaises(usage.RoutingError): usage.read_snapshot(bad, enrollment(), ROOT)

    def test_interval_requires_identity_order_continuity_and_boundary_evidence(self):
        before = usage.read_snapshot(self.path, enrollment(), ROOT)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE threads SET tokens_used = 230 WHERE id = ?', (ROOT,))
            db.commit()
        after = usage.read_snapshot(self.path, enrollment(), ROOT)
        result = interval_module.interval(before, after, evidence_ref='two snapshots')
        self.assertEqual(result['usage']['total_tokens'], 30)
        self.assertEqual(result['usage']['turn_scope'], 'partial_turn')
        full = interval_module.interval(before, after, evidence_ref='two snapshots',
                                        start_boundary_ref='before work', end_boundary_ref='after work',
                                        continuity_ref='no reset evidence')
        self.assertEqual(full['usage']['turn_scope'], 'full_turn')
        self.assertFalse(full['runtime_model_verified'])
        _validate_observation_usage(full['usage'])
        bad = dict(after, total_tokens=100)
        with self.assertRaises(routing.RoutingError):
            interval_module.interval(before, bad, evidence_ref='two snapshots')
        bad = dict(after, thread_id=CHILD)
        with self.assertRaises(routing.RoutingError):
            interval_module.interval(before, bad, evidence_ref='two snapshots')
        bad = dict(after, db_schema_version=999)
        with self.assertRaises(routing.RoutingError):
            interval_module.interval(before, bad, evidence_ref='two snapshots')
        bad = dict(after, configured_effort='max')
        with self.assertRaises(routing.RoutingError):
            interval_module.interval(before, bad, evidence_ref='two snapshots')
        bad = dict(after, observed_at='not a time')
        with self.assertRaises(routing.RoutingError):
            interval_module.interval(before, bad, evidence_ref='two snapshots')

    def test_cli_requires_existing_campaign_task_and_enrollment(self):
        home = self.base / 'home'
        policy = routing.load_policy(codex_home=home)
        opened = routing.observation_campaign_open(codex_home=home, policy=policy,
                                                   campaign_id='trial', purpose='normal work')
        routing.observation_start(codex_home=home, policy=policy, task_id='task', campaign_id='trial')
        routing.observation_enroll(codex_home=home, sampling_key=opened['sampling_key'],
                                   campaign_id='trial', task_id='task', enrollment=enrollment())
        args = [sys.executable, str(SCRIPT), '--codex-home', str(home), '--db', str(self.path),
                '--sampling-key', opened['sampling_key'], '--campaign-id', 'trial', '--task-id', 'task', '--thread-id']
        proc = subprocess.run(args + [CHILD], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['total_tokens'], 50)
        proc = subprocess.run(args + [OTHER], capture_output=True, text=True, encoding='utf-8')
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, '')
        with self.assertRaises(routing.RoutingError):
            routing.observation_enroll(codex_home=home, sampling_key=opened['sampling_key'],
                                       campaign_id='trial', task_id='task',
                                       enrollment={'schema_version': 1, 'root_id': ROOT, 'threads': [enrollment()['threads'][0]]})

if __name__ == '__main__': unittest.main()
