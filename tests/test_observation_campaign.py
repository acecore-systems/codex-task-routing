from __future__ import annotations

import copy
import io
import json
import shutil
import unittest
import uuid
from dataclasses import replace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from test_observation import routing, missing_completion, completed_child_run
from legacy_observation_fixture import seed_legacy


class CampaignTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parent / '.tmp-runtime-tests'
        root.mkdir(exist_ok=True)
        self.base = root / ('campaign-' + uuid.uuid4().hex)
        self.base.mkdir()
        self.home = self.base / 'home'
        self.policy = routing.load_policy(codex_home=self.home)
        self.key = routing.observation_sampling_key(self.policy)
        self.path = self.home / routing.OBSERVATIONS_RELATIVE / (self.key + '.json')

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def open(self, campaign='one'):
        return routing.observation_campaign_open(codex_home=self.home, policy=self.policy,
                                                  campaign_id=campaign, purpose='ordinary work comparison')

    def start(self, name, campaign='one', kind='normal'):
        return routing.observation_start(codex_home=self.home, policy=self.policy, task_id=name,
                                         kind=kind, campaign_id=campaign)

    def complete(self, name, completion, campaign='one'):
        return routing.observation_complete(codex_home=self.home, sampling_key=self.key,
                                            task_id=name, completion=completion, campaign_id=campaign)

    def status(self):
        return routing.observation_status(codex_home=self.home, policy=self.policy)

    def test_no_campaign_never_starts_from_hook_or_cli(self):
        self.assertIsNone(self.status()['campaign'])
        self.assertEqual(routing.observation_start(codex_home=self.home, policy=self.policy, task_id='new')['state'], 'no_campaign')
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = routing.main(['--codex-home', str(self.home), 'observation', 'start',
                                   '--task-id', 'new', '--json'])
        self.assertEqual(result, 2)
        self.assertFalse(self.path.exists())
        hook = routing.hook_payload(event='SessionStart', source='startup', codex_home=self.home, cwd=self.base)
        self.assertIn('No active observation campaign', hook['hookSpecificOutput']['additionalContext'])
        self.assertNotIn('observation start --campaign-id', hook['hookSpecificOutput']['additionalContext'])

    def test_new_policy_hash_without_open_is_not_active(self):
        self.open()
        # The policy renderer is the identity: a version alone does not start a campaign.
        changed = replace(self.policy, version='99.0.0', templates=copy.deepcopy(self.policy.templates))
        self.assertEqual(routing.observation_sampling_key(changed), self.key)
        self.assertEqual(routing.observation_status(codex_home=self.home, policy=changed)['campaign_id'], 'one')
        changed.templates['effective.md'] += 'Different policy.\n'
        self.assertNotEqual(routing.observation_sampling_key(changed), self.key)
        self.assertIsNone(routing.observation_status(codex_home=self.home, policy=changed)['campaign_id'])

    def test_active_campaign_hook_includes_reference_within_limit(self):
        campaign = 'c' * 64
        self.open(campaign)
        hook = routing.hook_payload(event='SessionStart', source='startup', codex_home=self.home, cwd=self.base)
        context = hook['hookSpecificOutput']['additionalContext']
        self.assertIn('campaign=' + campaign, context)
        self.assertIn('references/observation.md', context)
        self.assertLessEqual(len(context.encode("utf-8")), routing.MAX_ADDITIONAL_CONTEXT_BYTES)

    def test_migrates_legacy_without_erasing_records(self):
        for number in range(3):
            task = 'old-' + str(number)
            seed_legacy(routing, self.home, self.policy, task)
            routing.observation_complete(codex_home=self.home, sampling_key=self.key,
                                         task_id=task, completion=missing_completion())
        old = json.loads(self.path.read_text(encoding='utf-8'))['samples'][self.key]['tasks']
        self.open()
        new = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(new['schema_version'], 2)
        self.assertEqual(new['samples'][self.key]['tasks'], old)
        self.assertEqual(self.status()['remaining'], 0)
        self.assertEqual(self.status()['campaign']['attempts'], 0)
        self.assertEqual(self.start('new')['state'], 'started')
        routing.observation_complete(codex_home=self.home, sampling_key=self.key,
                                     task_id='old-0', completion=missing_completion())
        self.assertEqual(self.status()['campaign']['attempts'], 1)

    def test_missing_consumes_attempts_not_successes_and_stops_at_six(self):
        self.open()
        self.assertEqual(self.start('audit', kind='audit')['state'], 'skipped')
        for number in range(6):
            task = 'task-' + str(number)
            self.assertEqual(self.start(task)['state'], 'started')
            self.complete(task, missing_completion())
        status = self.status()['campaign']
        self.assertEqual((status['attempts'], status['missing'], status['measured'], status['remaining']), (6, 6, 0, 0))
        self.assertTrue(status['terminal'])
        before = self.path.read_bytes()
        self.assertEqual(self.start('seventh')['state'], 'full')
        self.assertEqual(before, self.path.read_bytes())
        self.open('two')
        self.assertEqual(self.status()['campaign_id'], 'two')
        self.assertEqual(self.status()['campaign']['attempts'], 0)
        previous = routing.observation_status(codex_home=self.home, sampling_key=self.key, campaign_id='one')
        self.assertEqual((previous['campaign']['attempts'], previous['campaign']['missing']), (6, 6))
        with self.assertRaises(routing.RoutingError):
            self.open('one')

    def test_pending_reservations_do_not_exceed_target_and_success_stops(self):
        self.open()
        for task in ('one', 'two', 'three'):
            self.assertEqual(self.start(task)['state'], 'started')
        self.assertEqual(self.start('four')['state'], 'full')
        for task in ('one', 'two', 'three'):
            parent = completed_child_run()
            parent.update(run_key='parent', relationship='parent')
            parent['usage']['turn_scope'] = 'full_turn'
            completion = missing_completion()
            completion.update(measurement={'state': 'measured', 'reason': None}, runs=[parent])
            self.complete(task, completion)
        stats = self.status()['campaign']
        self.assertEqual((stats['measured'], stats['attempts'], stats['remaining']), (3, 3, 0))
        self.assertTrue(stats['terminal'])
        self.assertEqual(self.start('four')['state'], 'full')

    def test_explicit_close_preserves_records_and_requires_no_pending(self):
        self.open()
        self.start('task')
        with self.assertRaises(routing.RoutingError):
            routing.observation_campaign_close(codex_home=self.home, sampling_key=self.key,
                                               campaign_id='one', reason='stop')
        self.complete('task', missing_completion())
        closed = routing.observation_campaign_close(codex_home=self.home, sampling_key=self.key,
                                                    campaign_id='one', reason='stop')
        self.assertEqual(closed['state'], 'closed')
        self.assertEqual(self.status()['campaign']['remaining'], 0)
        self.assertEqual(self.start('second')['state'], 'full')
        self.open('two')
        prior = routing.observation_status(codex_home=self.home, sampling_key=self.key, campaign_id='one')
        self.assertEqual((prior['campaign']['attempts'], prior['campaign']['missing']), (1, 1))

    def test_active_campaign_no_replacement_or_duplicate_id_and_corruption_safe(self):
        self.open()
        with self.assertRaises(routing.RoutingError):
            self.open('two')
        self.start('one')
        with routing._observation_write_lock(self.home, self.key):
            with self.assertRaises(routing.RoutingError):
                self.start('blocked')
        data = json.loads(self.path.read_text(encoding='utf-8'))
        data['samples'][self.key]['campaigns']['one']['tasks']['alias'] = copy.deepcopy(
            data['samples'][self.key]['campaigns']['one']['tasks']['one'])
        self.path.write_text(json.dumps(data), encoding='utf-8')
        before = self.path.read_bytes()
        self.assertEqual(self.status()['state'], 'unknown')
        with self.assertRaises(routing.RoutingError):
            self.open('two')
        self.assertEqual(before, self.path.read_bytes())

if __name__ == '__main__':
    unittest.main()
