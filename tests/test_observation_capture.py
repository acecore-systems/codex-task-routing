from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import test_usage_snapshot as fixture
from test_usage_snapshot import ROOT, CHILD, OTHER, enrollment, usage, routing, _validate_observation_usage
from test_observation import missing_completion
from routing_core.observation_capture import record_event, export_capture, record_baseline, verify_capture, certify_capture, fire_arm

SCRIPT = Path(__file__).resolve().parents[1] / 'plugins/codex-task-routing/scripts/capture_hook.py'
TURN = '019c0000-0000-7000-8000-000000000011'
USAGE = {'input_tokens': 100, 'cached_input_tokens': 40, 'output_tokens': 30,
         'reasoning_output_tokens': 20, 'total_tokens': 130}


class HookCaptureTests(unittest.TestCase):
    def setUp(self):
        fixture.UsageSnapshotTests.setUp(self)
        self.home = self.base / 'home'
        self.policy = routing.load_policy(codex_home=self.home)
        self.opened = routing.observation_campaign_open(codex_home=self.home, policy=self.policy,
                                                        campaign_id='trial', purpose='normal')
        routing.observation_start(codex_home=self.home, policy=self.policy,
                                  task_id='task', campaign_id='trial')
        self.enrolled = {'schema_version': 1, 'root_id': ROOT, 'threads': [enrollment()['threads'][0]],
                         'allow_descendants': True, 'start_evidence_ref': 'before first prompt'}
        routing.observation_enroll(codex_home=self.home, sampling_key=self.opened['sampling_key'],
                                   campaign_id='trial', task_id='task', enrollment=self.enrolled)
        shutil.copyfile(self.path, self.home / 'state_5.sqlite')
        self.path = self.home / 'state_5.sqlite'
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE threads SET tokens_used = 0 WHERE id = ?', (CHILD,))
            db.commit()
        record_baseline(codex_home=self.home, sampling_key=self.opened['sampling_key'],
                        campaign_id='trial', task_id='task',
                        snapshot_reader=lambda enrolled, thread: usage.read_snapshot(self.path, enrolled, thread))

    def tearDown(self):
        fixture.UsageSnapshotTests.tearDown(self)

    def event(self, kind, **extra):
        return {'hook_event_name': kind, 'session_id': ROOT, 'turn_id': TURN, **extra}

    def send(self, event, child_reader=None):
        return record_event(codex_home=self.home, policy=self.policy, event=event, child_reader=child_reader)

    def capture(self):
        state = routing._read_observation_state(self.home, self.opened['sampling_key'])
        campaign = state['samples'][self.opened['sampling_key']]['campaigns']['trial']
        return campaign['enrollments']['task'], campaign['captures']['task']

    def verify(self, counts):
        with closing(sqlite3.connect(self.path)) as db:
            for thread, total in counts.items():
                db.execute('UPDATE threads SET tokens_used = ? WHERE id = ?', (total, thread))
            db.commit()
        def reader(offset):
            def take(enrolled, thread):
                snapshot = usage.read_snapshot(self.path, enrolled, thread)
                timestamp = datetime.fromisoformat(snapshot['observed_at']) + timedelta(seconds=offset)
                snapshot['observed_at'] = timestamp.isoformat()
                return snapshot
            return take
        first = verify_capture(codex_home=self.home, sampling_key=self.opened['sampling_key'],
                               campaign_id='trial', task_id='task', snapshot_reader=reader(0), continuity_ref='no reset')
        self.assertEqual(first['state'], 'pending')
        second = verify_capture(codex_home=self.home, sampling_key=self.opened['sampling_key'],
                                campaign_id='trial', task_id='task', snapshot_reader=reader(2), continuity_ref='no reset')
        self.assertEqual(second['state'], 'verified')

    def test_next_prompt_arm_is_one_shot_and_requires_normal_confirmation(self):
        home = self.base / 'armed-home'
        policy = routing.load_policy(codex_home=home)
        opened = routing.observation_campaign_open(codex_home=home, policy=policy,
                                                   campaign_id='next', purpose='normal work')
        arm_input = {'schema_version': 1, 'root_id': ROOT, 'threads': [enrollment()['threads'][0]],
                     'allow_descendants': False, 'start_evidence_ref': None}
        routing.observation_arm(codex_home=home, sampling_key=opened['sampling_key'], campaign_id='next',
                                task_id='next-task', enrollment=arm_input)
        before = routing._read_observation_state(home, opened['sampling_key'])['samples'][opened['sampling_key']]['campaigns']['next']
        self.assertEqual(before['tasks'], {})
        self.assertIsNotNone(before['arm'])
        wrong = self.event('UserPromptSubmit', session_id=OTHER, prompt='private')
        self.assertEqual(fire_arm(codex_home=home, policy=policy, event=wrong, snapshot_reader=lambda *_: None), 'ignored')
        prompt = self.event('UserPromptSubmit', prompt='private prompt must not persist')
        reader = lambda enrolled, thread: usage.read_snapshot(self.path, enrolled, thread)
        self.assertEqual(fire_arm(codex_home=home, policy=policy, event=prompt, snapshot_reader=reader), 'started')
        self.assertEqual(fire_arm(codex_home=home, policy=policy, event=prompt, snapshot_reader=reader), 'ignored')
        campaign = routing._read_observation_state(home, opened['sampling_key'])['samples'][opened['sampling_key']]['campaigns']['next']
        self.assertEqual(campaign['classifications']['next-task']['state'], 'provisional')
        self.assertEqual(campaign['captures']['next-task']['baselines'][ROOT]['total_tokens'], 200)
        self.assertNotIn('private prompt', json.dumps(campaign))
        completion = missing_completion()
        with self.assertRaises(routing.RoutingError):
            routing.observation_complete(codex_home=home, sampling_key=opened['sampling_key'],
                                         campaign_id='next', task_id='next-task', completion=completion)
        routing.observation_confirm_normal(codex_home=home, sampling_key=opened['sampling_key'],
                                           campaign_id='next', task_id='next-task', evidence_ref='normal work')
        routing.observation_complete(codex_home=home, sampling_key=opened['sampling_key'],
                                     campaign_id='next', task_id='next-task', completion=completion)

    def test_unreadable_next_prompt_disarms_instead_of_using_a_later_turn(self):
        home = self.base / 'invalid-prompt-home'
        policy = routing.load_policy(codex_home=home)
        opened = routing.observation_campaign_open(codex_home=home, policy=policy,
                                                   campaign_id='next', purpose='normal work')
        arm_input = {'schema_version': 1, 'root_id': ROOT, 'threads': [enrollment()['threads'][0]]}
        routing.observation_arm(codex_home=home, sampling_key=opened['sampling_key'], campaign_id='next',
                                task_id='future-task', enrollment=arm_input)
        env = dict(os.environ, CODEX_HOME=str(home))
        proc = subprocess.run([sys.executable, str(SCRIPT), 'prompt'], input='x' * (128 * 1024 + 2),
                              capture_output=True, text=True, encoding='utf-8', env=env)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, '', ''))
        status = routing.observation_status(codex_home=home, policy=policy)
        self.assertFalse(status['campaign']['armed'])
        self.assertEqual(status['campaign']['arm_failure'], 'unreadable_prompt_event')
        self.assertEqual(status['campaign']['attempts'], 0)
        result = fire_arm(codex_home=home, policy=policy, event=self.event('UserPromptSubmit'),
                          snapshot_reader=lambda *_: None)
        self.assertEqual(result, 'ignored')

    def test_short_next_prompt_can_be_excluded_as_interrupted(self):
        home = self.base / 'excluded-home'
        policy = routing.load_policy(codex_home=home)
        opened = routing.observation_campaign_open(codex_home=home, policy=policy,
                                                   campaign_id='next', purpose='normal work')
        arm_input = {'schema_version': 1, 'root_id': ROOT, 'threads': [enrollment()['threads'][0]]}
        routing.observation_arm(codex_home=home, sampling_key=opened['sampling_key'], campaign_id='next',
                                task_id='short-task', enrollment=arm_input)
        fire_arm(codex_home=home, policy=policy, event=self.event('UserPromptSubmit'),
                 snapshot_reader=lambda enrolled, thread: usage.read_snapshot(self.path, enrolled, thread))
        completion = missing_completion()
        completion['state'] = 'interrupted'
        completion['measurement']['reason'] = 'short question excluded'
        routing.observation_complete(codex_home=home, sampling_key=opened['sampling_key'],
                                     campaign_id='next', task_id='short-task', completion=completion)
        campaign = routing._read_observation_state(home, opened['sampling_key'])['samples'][opened['sampling_key']]['campaigns']['next']
        self.assertEqual(campaign['classifications']['short-task']['state'], 'excluded')
        self.assertEqual(len(campaign['tasks']), 1)

    def test_root_and_child_pairs_and_coverage_evidence(self):
        self.send(self.event('PreModelRequest'))
        self.send(self.event('PostModelRequest', status='completed', token_usage=USAGE))
        reads = []
        def child_reader(candidate, agent):
            reads.append(agent)
            return usage.read_snapshot(self.path, candidate, agent)
        self.send(self.event('SubagentStart', agent_id=CHILD), child_reader=child_reader)
        self.assertEqual(reads, [CHILD])
        self.send(self.event('PreModelRequest', agent_id=CHILD))
        self.send(self.event('PostModelRequest', agent_id=CHILD, status='completed', token_usage=USAGE))
        self.send(self.event('SubagentStop', agent_id=CHILD))
        self.send(self.event('Stop'))
        enrolled, capture = self.capture()
        partial = export_capture(enrolled, capture, evidence_ref='hook record')
        self.assertEqual(partial['state'], 'partial')
        self.verify({ROOT: 330, CHILD: 130})
        enrolled, capture = self.capture()
        result = export_capture(enrolled, capture, evidence_ref='hook record', coverage_ref='coverage checked', work_completed_ref='work complete')
        self.assertEqual(result['state'], 'measured')
        self.assertEqual(len(result['runs']), 2)
        self.assertEqual(result['runs'][0]['usage']['input_tokens'], 100)
        self.assertEqual(result['runs'][1]['usage']['cache_input_tokens'], 40)
        self.assertFalse(result['runtime_model_verified'])
        for run in result['runs']:
            _validate_observation_usage(run['usage'])

    def test_duplicate_missing_and_unknown_children_cannot_claim_full(self):
        self.send(self.event('PreModelRequest'))
        self.send(self.event('PreModelRequest'))
        self.send(self.event('PostModelRequest', status='completed', token_usage=USAGE))
        self.send(self.event('SubagentStart', agent_id=OTHER), child_reader=lambda *_: (_ for _ in ()).throw(routing.RoutingError('not enrolled')))
        self.send(self.event('Stop'))
        enrolled, capture = self.capture()
        result = export_capture(enrolled, capture, evidence_ref='hooks', coverage_ref='coverage')
        self.assertEqual(result['state'], 'partial')
        self.assertIn('ambiguous_pair', result['reasons'])
        self.assertIn('child_registration_failed', result['reasons'])
        self.assertNotIn(OTHER, json.dumps(capture))

    def test_delayed_persistence_or_mismatch_cannot_claim_coverage(self):
        self.send(self.event('PreModelRequest'))
        self.send(self.event('PostModelRequest', status='completed', token_usage=USAGE))
        self.send(self.event('Stop'))
        def reader(offset):
            def take(enrolled, thread):
                snapshot = usage.read_snapshot(self.path, enrolled, thread)
                snapshot['observed_at'] = (datetime.fromisoformat(snapshot['observed_at']) + timedelta(seconds=offset)).isoformat()
                return snapshot
            return take
        def check(offset):
            return verify_capture(codex_home=self.home, sampling_key=self.opened['sampling_key'],
                                  campaign_id='trial', task_id='task', snapshot_reader=reader(offset), continuity_ref='no reset')
        self.assertEqual(check(0)['reason'], 'counter_mismatch')
        self.assertEqual(check(2)['reason'], 'counter_mismatch')
        enrolled, capture = self.capture()
        self.assertEqual(export_capture(enrolled, capture, evidence_ref='hooks', coverage_ref='verified', work_completed_ref='work complete')['state'], 'partial')
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE threads SET tokens_used = 330 WHERE id = ?', (ROOT,))
            db.commit()
        self.assertEqual(check(4)['state'], 'pending')
        self.assertEqual(check(6)['state'], 'verified')

    def test_failed_reverification_and_new_event_invalidate_previous_success(self):
        self.send(self.event('PreModelRequest'))
        self.send(self.event('PostModelRequest', status='completed', token_usage=USAGE))
        self.send(self.event('Stop'))
        self.verify({ROOT: 330})
        result = verify_capture(codex_home=self.home, sampling_key=self.opened['sampling_key'],
                                campaign_id='trial', task_id='task',
                                snapshot_reader=lambda *_: (_ for _ in ()).throw(routing.RoutingError('unavailable')),
                                continuity_ref='no reset')
        self.assertEqual(result['reason'], 'snapshot_unavailable')
        enrolled, capture = self.capture()
        self.assertEqual(capture['verification']['state'], 'missing')
        self.assertEqual(export_capture(enrolled, capture, evidence_ref='hooks', coverage_ref='checked')['state'], 'partial')
        self.verify({ROOT: 330})
        result = verify_capture(codex_home=self.home, sampling_key=self.opened['sampling_key'],
                                campaign_id='trial', task_id='task', snapshot_reader=lambda *_: None,
                                continuity_ref='')
        self.assertEqual(result['reason'], 'continuity_evidence_invalid')
        self.verify({ROOT: 330})
        before = self.capture()[1]
        self.send(self.event('PreModelRequest'))
        enrolled, capture = self.capture()
        self.assertEqual(capture, before)

    def test_enrollment_change_invalidates_verified_capture(self):
        self.send(self.event('PreModelRequest'))
        self.send(self.event('PostModelRequest', status='completed', token_usage=USAGE))
        self.send(self.event('Stop'))
        self.verify({ROOT: 330})
        extra = dict(self.enrolled, threads=enrollment()['threads'])
        routing.observation_enroll(codex_home=self.home, sampling_key=self.opened['sampling_key'],
                                   campaign_id='trial', task_id='task', enrollment=extra)
        enrolled, capture = self.capture()
        self.assertEqual(capture['verification']['state'], 'missing')
        self.assertEqual(export_capture(enrolled, capture, evidence_ref='hooks', coverage_ref='checked',
                                        work_completed_ref='work complete')['state'], 'partial')

    def test_completion_before_final_stop_captures_tail_and_stop(self):
        self.send(self.event('PreModelRequest'))
        self.send(self.event('PostModelRequest', status='completed', token_usage=USAGE))
        routing.observation_complete(codex_home=self.home, sampling_key=self.opened['sampling_key'],
                                     campaign_id='trial', task_id='task', completion=missing_completion())
        self.send(self.event('PreModelRequest'))
        self.send(self.event('PostModelRequest', status='completed', token_usage=USAGE))
        self.assertEqual(routing.observation_status(codex_home=self.home, policy=self.policy)['campaign']['capture_pending'], 1)
        self.send(self.event('Stop'))
        enrolled, capture = self.capture()
        self.verify({ROOT: 460})
        enrolled, capture = self.capture()
        result = export_capture(enrolled, capture, evidence_ref='hooks', coverage_ref='verified', work_completed_ref='work complete')
        self.assertEqual(result['state'], 'measured')
        self.assertEqual(result['runs'][0]['usage']['input_tokens'], 200)

    def test_pre_enrolled_child_without_start_is_not_full(self):
        root_child = dict(self.enrolled, threads=enrollment()['threads'])
        routing.observation_enroll(codex_home=self.home, sampling_key=self.opened['sampling_key'],
                                   campaign_id='trial', task_id='task', enrollment=root_child)
        self.send(self.event('PreModelRequest'))
        self.send(self.event('PostModelRequest', status='completed', token_usage=USAGE))
        self.send(self.event('PreModelRequest', agent_id=CHILD))
        self.send(self.event('PostModelRequest', agent_id=CHILD, status='completed', token_usage=USAGE))
        self.send(self.event('SubagentStop', agent_id=CHILD))
        self.send(self.event('Stop'))
        enrolled, capture = self.capture()
        result = export_capture(enrolled, capture, evidence_ref='hooks', coverage_ref='verified', work_completed_ref='work complete')
        self.assertEqual(result['state'], 'partial')
        self.assertIn('child_lifecycle_unobserved', result['reasons'])

    def test_missing_post_and_invalid_counts_are_partial(self):
        self.send(self.event('PreModelRequest'))
        self.send(self.event('Stop'))
        enrolled, capture = self.capture()
        result = export_capture(enrolled, capture, evidence_ref='hooks', coverage_ref='coverage')
        self.assertEqual(result['state'], 'partial')
        self.assertIn('request_pair_incomplete', result['reasons'])
        self.send(self.event('PostModelRequest', status='completed', token_usage={**USAGE, 'total_tokens': 131}))
        enrolled, capture = self.capture()
        self.assertTrue(capture['threads'][ROOT]['turns'][TURN]['pending'])
        self.assertEqual(export_capture(enrolled, capture, evidence_ref='hooks', coverage_ref='coverage')['state'], 'partial')

    def test_hook_entrypoint_registers_verified_child_and_exports(self):
        env = dict(os.environ, CODEX_HOME=str(self.home))
        events = [self.event('PreModelRequest'),
                  self.event('PostModelRequest', status='completed', token_usage=USAGE),
                  self.event('SubagentStart', agent_id=CHILD),
                  self.event('PreModelRequest', agent_id=CHILD),
                  self.event('PostModelRequest', agent_id=CHILD, status='completed', token_usage=USAGE),
                  self.event('SubagentStop', agent_id=CHILD), self.event('Stop')]
        for payload in events:
            if payload['hook_event_name'] == 'Stop':
                with closing(sqlite3.connect(self.path)) as db:
                    db.execute('UPDATE threads SET tokens_used = 330 WHERE id = ?', (ROOT,))
                    db.execute('UPDATE threads SET tokens_used = 130 WHERE id = ?', (CHILD,))
                    db.commit()
            proc = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload),
                                  capture_output=True, text=True, env=env)
            self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, '', ''))
        enrolled, capture = self.capture()
        self.assertEqual(capture['verification']['state'], 'matched')
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE threads SET tokens_used = 999 WHERE id = ?', (ROOT,))
            db.commit()
        certify_capture(codex_home=self.home, sampling_key=self.opened['sampling_key'], campaign_id='trial', task_id='task', continuity_ref='no reset')
        enrolled, capture = self.capture()
        result = export_capture(enrolled, capture, evidence_ref='hooks', coverage_ref='verified', work_completed_ref='work complete')
        self.assertEqual(result['state'], 'measured')
        self.assertEqual(len(result['runs']), 2)
        self.assertNotIn('private-', json.dumps(capture))

    def test_entrypoint_filters_private_fields_and_ignores_other_sessions(self):
        state_path = self.home / routing.OBSERVATIONS_RELATIVE / (self.opened['sampling_key'] + '.json')
        env = dict(os.environ, CODEX_HOME=str(self.home))
        payload = self.event('PreModelRequest', transcript_path='private-path', prompt='private-prompt', last_assistant_message='private-answer')
        proc = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload), capture_output=True, text=True, env=env)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, '', ''))
        self.assertNotIn('private-', state_path.read_text(encoding='utf-8'))
        before = state_path.read_bytes()
        payload['session_id'] = OTHER
        proc = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload), capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(state_path.read_bytes(), before)

if __name__ == '__main__': unittest.main()
