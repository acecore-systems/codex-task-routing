from __future__ import annotations
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import unittest
from contextlib import closing
from pathlib import Path
import test_usage_snapshot as fixture
from test_usage_snapshot import ROOT, CHILD
from test_observation_capture import TURN, USAGE
from test_compare_observations import inputs

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/codex-task-routing/scripts'
CHILD_TURN = '019c0000-0000-7000-8000-000000000012'


class ObservationEndToEndTest(unittest.TestCase):
    def setUp(self):
        fixture.UsageSnapshotTests.setUp(self)
        self.home = self.base / 'e2e-home'
        self.home.mkdir()
        self.db = self.home / 'state_5.sqlite'
        shutil.copyfile(self.path, self.db)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('UPDATE threads SET tokens_used = 0 WHERE id = ?', (CHILD,))
            db.commit()
        self.env = dict(os.environ, CODEX_HOME=str(self.home))

    def tearDown(self):
        fixture.UsageSnapshotTests.tearDown(self)

    def cli(self, *args):
        proc = subprocess.run([sys.executable, str(SCRIPTS / 'routing.py'), '--codex-home', str(self.home),
                               'observation', *args], capture_output=True, text=True, encoding='utf-8', env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def hook(self, kind, **kwargs):
        payload = {'hook_event_name': kind, 'session_id': ROOT, 'turn_id': TURN,
                   'prompt': 'never store this content', **kwargs}
        command = [sys.executable, str(SCRIPTS / 'capture_hook.py')]
        if kind == 'UserPromptSubmit':
            command.append('prompt')
        proc = subprocess.run(command, input=json.dumps(payload),
                              capture_output=True, text=True, encoding='utf-8', env=self.env)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, '', ''))

    def test_arm_through_stop_completion_and_comparison(self):
        opened = self.cli('campaign-open', '--campaign-id', 'trial', '--purpose', 'ordinary work')
        key = opened['sampling_key']
        enrolled = {'schema_version': 1, 'root_id': ROOT, 'threads': [
            {'thread_id': ROOT, 'parent_id': None, 'depth': 0}], 'allow_descendants': True,
            'start_evidence_ref': None}
        input_path = self.base / 'enrollment.json'
        input_path.write_text(json.dumps(enrolled), encoding='utf-8')
        self.cli('arm-next', '--campaign-id', 'trial', '--sampling-key', key, '--task-id', 'future-task', '--input', str(input_path))
        status = self.cli('status', '--json', '--sampling-key', key)
        self.assertEqual(status['campaign']['attempts'], 0)
        self.hook('UserPromptSubmit')
        self.cli('confirm-normal', '--campaign-id', 'trial', '--sampling-key', key, '--task-id', 'future-task', '--evidence-ref', 'normal work verified')
        self.hook('PreModelRequest')
        self.hook('PostModelRequest', status='completed', token_usage=USAGE)
        self.hook('SubagentStart', agent_id=CHILD)
        self.hook('PreModelRequest', agent_id=CHILD, turn_id=CHILD_TURN)
        self.hook('PostModelRequest', agent_id=CHILD, turn_id=CHILD_TURN, status='completed', token_usage=USAGE)
        self.hook('SubagentStop', agent_id=CHILD, turn_id=CHILD_TURN)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('UPDATE threads SET tokens_used = 330 WHERE id = ?', (ROOT,))
            db.execute('UPDATE threads SET tokens_used = 130 WHERE id = ?', (CHILD,))
            db.commit()
        self.hook('Stop')
        args = ['--campaign-id', 'trial', '--sampling-key', key, '--task-id', 'future-task']
        partial = self.cli('capture-export', *args, '--evidence-ref', 'capture evidence')
        self.assertEqual(partial['state'], 'partial')
        self.cli('capture-certify', *args, '--continuity-ref', 'continuous runtime verified')
        interim = self.cli('capture-export', *args, '--evidence-ref', 'capture evidence', '--coverage-ref', 'coverage verified')
        self.assertEqual(interim['state'], 'partial')
        self.assertIn('work_completion_unverified', interim['reasons'])
        result = self.cli('capture-export', *args, '--evidence-ref', 'capture evidence', '--coverage-ref', 'coverage verified', '--work-completed-ref', 'work completed')
        self.assertEqual(result['state'], 'measured')
        runs = []
        for item in result['runs']:
            runs.append({'run_key': item['thread_id'] + ':' + item['turn_id'], 'relationship': item['relationship'],
                         'state': 'completed', 'run_id': item['thread_id'] + ':' + item['turn_id'], 'run_id_reason': None,
                         'requested': {'model': 'gpt-6-astra' if item['relationship'] == 'parent' else 'gpt-6.1-terra',
                                       'effort': 'xhigh', 'reason': None},
                         'observed': {'model': None, 'effort': None, 'evidence_ref': None, 'reason': 'runtime unverified'},
                         'usage': item['usage']})
        completion = {'state': 'completed', 'scope': 'full synthetic work', 'measurement': {'state': 'measured', 'reason': None},
                      'verification': {'reference': 'test evidence', 'reason': None}, 'rework': 'none', 'runs': runs}
        completion_path = self.base / 'completion.json'
        bad = json.loads(json.dumps(completion))
        bad['runs'][0]['usage']['input_tokens'] += 1
        completion_path.write_text(json.dumps(bad), encoding='utf-8')
        proc = subprocess.run([sys.executable, str(SCRIPTS / 'routing.py'), '--codex-home', str(self.home), 'observation', 'complete', *args, '--input', str(completion_path)], capture_output=True, text=True, encoding='utf-8')
        self.assertNotEqual(proc.returncode, 0)
        completion_path.write_text(json.dumps(completion), encoding='utf-8')
        self.cli('complete', *args, '--input', str(completion_path))
        status = self.cli('status', '--json', '--sampling-key', key)
        self.assertEqual((status['campaign']['attempts'], status['campaign']['measured']), (1, 1))
        comparison = inputs()
        comparison['candidate']['completion'] = completion
        comparison['candidate']['conditions']['parent_effort']['value'] = 'xhigh'
        comparison['baseline']['conditions']['parent_effort']['value'] = 'xhigh'
        for run in comparison['baseline']['completion']['runs']:
            run['usage']['basis'] = 'hook_request_counters'
            run['observed'].update(model=None, effort=None, evidence_ref=None, reason='runtime unverified')
        compare_path = self.base / 'comparison.json'
        compare_path.write_text(json.dumps(comparison), encoding='utf-8')
        proc = subprocess.run([sys.executable, str(SCRIPTS / 'compare_observations.py'), '--input', str(compare_path)],
                              capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        compared = json.loads(proc.stdout)
        self.assertEqual(compared['basis'], 'hook_request_counters')
        self.assertEqual(compared['total_tokens']['candidate'], 260)
        self.assertFalse(compared['runtime_model_verified'])
        state_file = self.home / 'codex-task-routing' / 'observations' / (key + '.json')
        self.assertNotIn('never store this content', state_file.read_text(encoding='utf-8'))

if __name__ == '__main__': unittest.main()
