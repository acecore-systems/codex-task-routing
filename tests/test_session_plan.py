"""Session/cloud planning boundaries; synthetic metadata, no live services."""
from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from runtime_fixtures import runtime_directory

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'plugins/codex-task-routing/scripts'
sys.path.insert(0, str(SCRIPTS))
import session_plan as sp
import routing


class SessionPlanningTests(unittest.TestCase):
    def setUp(self):
        self.facts = dict(schema_version=1, candidate='codex_cloud', current_owner_suitable=False,
                          existing_owner=dict(owner_id='old-owner', route='subagent', suitable=False),
                          independent_work_unit=True, handoff_benefit_exceeds_cost=True,
                          reasons=['independent_deliverable'])

    def test_independent_context_is_a_proposal_not_an_execution_or_model_choice(self):
        for candidate in sorted(sp.ROUTES - {'continue_current', 'existing_local_session'}):
            with self.subTest(candidate=candidate):
                output = sp.select_session({**self.facts, 'candidate': candidate})
                self.assertEqual(output['route'], candidate)
                self.assertEqual(output['next_action'], 'inspect_candidate')
                self.assertTrue(output['proposal_only'])
                self.assertFalse(output['execution_authorized'])
                self.assertNotIn('model', output)
                self.assertNotIn('effort', output)
        self.assertEqual(sp.select_session({**self.facts, 'candidate': 'chat_pro'})['required_gate'],
                         'existing_chat_policy')

    def test_reuses_suitable_owner_before_proposing_another_context(self):
        self.facts['existing_owner']['suitable'] = True
        output = sp.select_session(self.facts)
        self.assertEqual(output['route'], 'subagent')
        self.assertEqual(output['owner_id'], 'old-owner')
        self.assertEqual(output['next_action'], 'reuse_existing_owner')
        self.facts['existing_owner']['suitable'] = False
        self.facts['current_owner_suitable'] = True
        self.assertEqual(sp.select_session(self.facts)['route'], 'continue_current')

    def test_suitable_current_owner_has_priority_over_another_existing_owner(self):
        self.facts['existing_owner']['suitable'] = True
        self.facts['current_owner_suitable'] = True
        output = sp.select_session(self.facts)
        self.assertEqual(output['route'], 'continue_current')
        self.assertEqual(output['next_action'], 'continue_current')

    def test_known_unsuitable_existing_local_session_is_not_a_usable_candidate(self):
        self.facts.update(candidate='existing_local_session')
        self.facts['existing_owner'].update(route='existing_local_session', suitable=False)
        output = sp.select_session(self.facts)
        self.assertEqual(output['next_action'], 'choose_another_context')
        self.assertIn('existing_local_session_not_suitable', output['gaps'])
        self.assertFalse(output['execution_authorized'])

    def test_unknown_suitability_preserves_proposal_but_requires_reuse_check(self):
        for existing in (None, {**self.facts['existing_owner'], 'suitable': None}):
            with self.subTest(existing=existing):
                output = sp.select_session({**self.facts, 'existing_owner': existing,
                                            'current_owner_suitable': None})
                self.assertEqual(output['route'], 'codex_cloud')
                self.assertEqual(output['next_action'], 'check_owner_reuse')
                self.assertIn('existing_owner_reuse_unconfirmed', output['gaps'])
                self.assertIn('current_owner_suitable_unknown', output['gaps'])
        output = sp.select_session({**self.facts, 'handoff_benefit_exceeds_cost': None})
        self.assertEqual(output['route'], 'codex_cloud')
        self.assertEqual(output['next_action'], 'assess_proposal')

    def test_context_quota_and_same_model_alone_do_not_justify_splitting(self):
        for reasons in ([], ['long_context'], ['quota_avoidance'], ['same_model_transfer'],
                        ['long_context', 'quota_avoidance', 'same_model_transfer']):
            with self.subTest(reasons=reasons):
                output = sp.select_session({**self.facts, 'reasons': reasons})
                self.assertEqual(output['route'], 'continue_current')
                self.assertEqual(output['reason'], 'no_independent_purpose')

    def test_independence_and_handoff_cost_are_both_required(self):
        for name in ('independent_work_unit', 'handoff_benefit_exceeds_cost'):
            with self.subTest(name=name):
                self.assertEqual(sp.select_session({**self.facts, name: False})['route'], 'continue_current')
                result = sp.select_session({**self.facts, name: None})
                self.assertEqual(result['route'], 'codex_cloud')
                self.assertIn(name + '_unknown', result['gaps'])

    def test_new_session_does_not_inherit_standard_child_settings_or_grant_creation(self):
        output = sp.select_session({**self.facts, 'candidate': 'new_local_session'})
        self.assertFalse(output['execution_authorized'])
        for key in ('model', 'thinking', 'effort', 'worktree', 'send_message_to_thread'):
            with self.subTest(key=key), self.assertRaises(sp.SessionPlanError):
                sp.select_session({**self.facts, key: True})

    def test_strict_schema_boolean_unknown_keys_and_metadata(self):
        for changes in ({'schema_version': True}, {'independent_work_unit': 1},
                        {'reasons': ['independent_deliverable'] * 2}, {'candidate': 'work'},
                        {'secrets': 'synthetic-do-not-print'}, {'existing_owner': {}},
                        {'existing_owner': {**self.facts['existing_owner'], 'suitable': 'true'}}):
            with self.subTest(changes=changes), self.assertRaises(sp.SessionPlanError):
                sp.select_session({**self.facts, **changes})


class CloudPlanningTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 20, 8, tzinfo=timezone.utc)
        self.handoff = dict(schema_version=1, request_id='cloud-unit-1', input_sha256='a' * 64,
            owner_id='root-owner', return_to='root-task', repository='example/project',
            branch='codex/example', expected_revision='1' * 40, environment_id='environment-1',
            endpoint='Implement and verify the scoped change',
            acceptance_criteria=['Regression resolved with reproducible verification'],
            required_checks=['python -m unittest discover -s tests -v'],
            allowed_operations=['read', 'edit', 'test'], state=dict(submission='not_sent', task_id=None))
        self.live = dict(schema_version=1, handoff_sha256=sp.handoff_sha256(self.handoff),
            task_id=None, surface='codex_cloud', transport='codex_cloud_ui',
            actor=dict(role='root', depth=0, executor_id='root-owner'), observed_at=self.now.isoformat(),
            checks={name: dict(state='verified' if name in sp.OBSERVED_CHECKS else 'permission_checked',
                               reference='Existing synthetic scoped evidence')
                    for name in sp.CHECKS})
        self.result = dict(schema_version=1, request_id='cloud-unit-1', input_sha256='a' * 64,
            handoff_sha256=sp.handoff_sha256(self.handoff), task_id='task-1', status='completed',
            repository='example/project', base_revision='1' * 40, change_kind='changed',
            output_revision='2' * 40, diff_evidence=['artifact:scoped-diff'],
            checks_completed=self.handoff['required_checks'][:], verification_status='passed',
            verification_evidence=['artifact:test-command-environment-revision-result'],
            artifact_references=['artifact:implementation'])
        self.acceptance = dict(owner_id='root-owner', decision='accepted', criteria_checked=True,
                               reference='artifact:owner-semantic-review')

    def plan(self, phase='dispatch-plan', **kwargs):
        return sp.cloud_plan(self.handoff, phase=phase, live=self.live, now=self.now, **kwargs)

    def collected(self):
        self.handoff['state'] = dict(submission='collected', task_id='task-1')
        self.live['task_id'] = 'task-1'

    def accept(self):
        return sp.cloud_plan(self.handoff, phase='accept', result=self.result, acceptance=self.acceptance)

    def test_complete_dispatch_preflight_never_authorizes_or_executes(self):
        output = self.plan()
        self.assertTrue(output['preflight_complete'])
        self.assertTrue(output['planning_only'])
        self.assertFalse(output['runtime_implemented'])
        self.assertFalse(output['execution_authorized'])
        self.assertEqual(output['next_action'], 'review_actual_host_dispatch_contract')

    def test_inspection_requires_only_scoped_inspection_not_new_dispatch_approval(self):
        self.live['checks'] = {name: self.live['checks'][name]
                               for name in ('host_permission', 'inspect_capability', 'inspect_authorization')}
        self.assertTrue(self.plan('inspect')['preflight_complete'])
        self.assertFalse(self.plan()['preflight_complete'])
        output = sp.cloud_plan(self.handoff)
        self.assertEqual(output['phase'], 'inspect')
        self.assertIn('live_observations_missing', output['gaps'])

    def test_each_dispatch_gate_distinguishes_unknown_blocked_and_missing(self):
        required = ('host_permission', 'dispatch_capability', 'target_authorization',
                    'materials_authorization', 'environment', 'repository_revision', 'test_environment')
        for name in required:
            for state in ('unknown', 'blocked', 'unavailable', 'confirmation_required', None):
                with self.subTest(name=name, state=state):
                    original = self.live['checks'].pop(name)
                    if state is not None:
                        self.live['checks'][name] = dict(state=state)
                    output = self.plan()
                    self.assertFalse(output['preflight_complete'])
                    self.assertIn(name + ':' + (state or 'unknown'), output['gaps'])
                    self.live['checks'][name] = original

    def test_environment_revision_and_test_capability_require_real_observations(self):
        for name in ('environment', 'repository_revision', 'test_environment'):
            with self.subTest(name=name):
                self.live['checks'][name]['state'] = 'permission_checked'
                output = self.plan()
                self.assertFalse(output['preflight_complete'])
                self.assertIn(name + ':permission_checked', output['gaps'])
                self.live['checks'][name]['state'] = 'verified'
                self.assertTrue(self.plan()['preflight_complete'])

    def test_unknown_live_never_consumes_or_creates_approval(self):
        self.live['checks']['target_authorization'] = dict(state='unknown')
        before = copy.deepcopy(self.live)
        self.assertFalse(self.plan()['preflight_complete'])
        self.assertEqual(before, self.live)
        self.live['checks']['target_authorization'] = dict(state='permission_checked')
        with self.assertRaises(sp.SessionPlanError):
            self.plan()

    def test_removed_cli_transport_is_rejected_for_inspection_dispatch_and_collection(self):
        self.live['transport'] = 'codex_cloud_cli'
        for phase in ('inspect', 'dispatch-plan', 'receive'):
            if phase == 'receive':
                self.handoff['state'] = dict(submission='sent', task_id='original-task')
                self.live['task_id'] = 'original-task'
            before = copy.deepcopy(self.live)
            with self.subTest(phase=phase), self.assertRaises(sp.SessionPlanError):
                self.plan(phase)
            self.assertEqual(self.live, before)

    def test_scope_materials_environment_and_expected_revision_are_bound(self):
        for key, value in [('input_sha256', 'b' * 64), ('expected_revision', '3' * 40),
                           ('repository', 'example/other'), ('environment_id', 'other-environment'),
                           ('endpoint', 'Different endpoint'), ('owner_id', 'other-owner'),
                           ('allowed_operations', ['read'])]:
            with self.subTest(key=key):
                original = self.handoff[key]
                self.handoff[key] = value
                self.assertIn('handoff_scope_mismatch', self.plan()['gaps'])
                self.handoff[key] = original

    def test_actor_and_chat_work_unknown_surfaces_cannot_create_cloud_permission(self):
        for surface in ('chatgpt_work', 'normal_chat', 'unknown'):
            with self.subTest(surface=surface):
                self.live['surface'] = surface
                self.assertIn('cloud_surface_or_transport_unverified', self.plan()['gaps'])
        self.live['surface'] = 'codex_cloud'
        for actor in (dict(role='sol', depth=1, executor_id='root-owner'),
                      dict(role='root', depth=1, executor_id='root-owner'),
                      dict(role='root', depth=0, executor_id='other-owner')):
            with self.subTest(actor=actor):
                self.live['actor'] = actor
                self.assertIn('return_cloud_execution_scope_to_root', self.plan()['gaps'])
        self.live['actor'] = dict(role='root', depth=False, executor_id='root-owner')
        with self.assertRaises(sp.SessionPlanError):
            self.plan()

    def test_live_freshness_boundaries_and_timezones(self):
        for seconds, expected in [(0, True), (-300, True), (-301, False), (1, False)]:
            with self.subTest(seconds=seconds):
                self.live['observed_at'] = (self.now + timedelta(seconds=seconds)).isoformat()
                self.assertIs(self.plan()['preflight_complete'], expected)
        self.live['observed_at'] = '2026-10-20T08:00:00'
        with self.assertRaises(sp.SessionPlanError):
            self.plan()

    def test_sent_unknown_collected_accepted_never_plan_a_second_submission(self):
        actions = dict(sent='receive_original_task', unknown_outcome='reconcile_original_submission',
                       collected='accept_original_result', accepted='reuse_accepted_result')
        for state, action in actions.items():
            with self.subTest(state=state):
                self.handoff['state'] = dict(submission=state,
                    task_id=None if state == 'unknown_outcome' else 'task-1')
                output = sp.cloud_plan(self.handoff, phase='dispatch-plan')
                self.assertEqual(output['next_action'], action)
                self.assertFalse(output['preflight_complete'])
                self.assertIn('new_submission_forbidden', output['gaps'])

    def test_receive_needs_original_task_and_no_new_dispatch_checks(self):
        self.assertEqual(self.plan('receive')['next_action'], 'nothing_submitted')
        self.handoff['state'] = dict(submission='unknown_outcome', task_id=None)
        self.assertEqual(self.plan('receive')['next_action'], 'reconcile_original_submission')
        self.handoff['state'] = dict(submission='sent', task_id='task-1')
        self.live['task_id'] = 'task-1'
        self.live['checks'] = {name: self.live['checks'][name]
                               for name in ('host_permission', 'receive_capability', 'receive_authorization')}
        output = self.plan('receive')
        self.assertTrue(output['preflight_complete'])
        self.assertEqual(output['next_action'], 'collect_original_task_without_resend')
        self.assertFalse(output['execution_authorized'])
        self.live['checks']['receive_authorization'] = dict(state='unknown')
        self.assertFalse(self.plan('receive')['preflight_complete'])

    def test_receive_observations_cannot_be_reused_for_a_different_original_task(self):
        self.collected()
        self.assertTrue(self.plan('receive')['preflight_complete'])
        self.handoff['state']['task_id'] = 'other-task'
        self.assertEqual(self.live['handoff_sha256'], sp.handoff_sha256(self.handoff))
        for phase in ('receive', 'inspect'):
            with self.subTest(phase=phase):
                output = self.plan(phase)
                self.assertFalse(output['preflight_complete'])
                self.assertIn('original_task_identity_mismatch', output['gaps'])

    def test_state_schema_cannot_claim_sent_without_task_or_not_sent_with_task(self):
        for state in (dict(submission='sent', task_id=None), dict(submission='not_sent', task_id='task-1'),
                      dict(submission='failed', task_id='task-1')):
            with self.subTest(state=state), self.assertRaises(sp.SessionPlanError):
                sp.cloud_plan({**self.handoff, 'state': state})

    def test_accept_requires_original_collected_result_and_owner_semantic_review(self):
        with self.assertRaises(sp.SessionPlanError):
            self.accept()
        self.collected()
        output = self.accept()
        self.assertEqual(output['outcome'], 'completed')
        self.assertFalse(output['execution_authorized'])
        for changes in ({'criteria_checked': None}, {'criteria_checked': False}, {'decision': 'unverified'}):
            original = copy.deepcopy(self.acceptance)
            self.acceptance.update(changes)
            self.assertEqual(self.accept()['outcome'], 'unverified')
            self.acceptance = original
        self.acceptance['decision'] = 'needs_changes'
        self.assertEqual(self.accept()['next_action'], 'return_to_same_owner')

    def test_accept_rejects_wrong_id_hash_owner_repo_and_actual_base_revision(self):
        self.collected()
        for key, value in [('request_id', 'other-request'), ('task_id', 'other-task'),
                           ('input_sha256', 'b' * 64), ('handoff_sha256', 'c' * 64),
                           ('repository', 'example/other'), ('base_revision', '3' * 40)]:
            with self.subTest(key=key):
                original = self.result[key]
                self.result[key] = value
                self.assertEqual(self.accept()['next_action'], 'return_identity_mismatch_to_root')
                self.result[key] = original
        self.acceptance['owner_id'] = 'new-owner'
        self.assertEqual(self.accept()['outcome'], 'unverified')

    def test_accept_preserves_failed_partial_and_unverified_outcomes(self):
        self.collected()
        for status in ('failed', 'partial', 'unverified'):
            with self.subTest(status=status):
                self.result['status'] = status
                self.assertEqual(self.accept()['outcome'], status)

    def test_known_failure_and_partial_are_preserved_before_owner_review_finishes(self):
        self.collected()
        for status in ('failed', 'partial', 'unverified'):
            for decision, checked in (('unverified', None), ('accepted', False), ('needs_changes', True)):
                with self.subTest(status=status, decision=decision, checked=checked):
                    self.result['status'] = status
                    self.acceptance.update(decision=decision, criteria_checked=checked)
                    output = self.accept()
                    self.assertEqual(output['outcome'], status)
                    self.assertEqual(output['owner_review'], dict(decision=decision, criteria_checked=checked))
                    self.assertFalse(output['preflight_complete'])
                    if decision == 'needs_changes':
                        self.assertEqual(output['next_action'], 'return_to_same_owner')

    def test_required_checks_need_successful_verification_and_evidence(self):
        self.collected()
        for changes in ({'verification_status': 'unverified'}, {'verification_status': 'failed'},
                        {'verification_status': 'not_required'}, {'verification_evidence': []},
                        {'checks_completed': []}):
            with self.subTest(changes=changes):
                original = copy.deepcopy(self.result)
                self.result.update(changes)
                self.assertEqual(self.accept()['next_action'], 'verify_required_checks')
                self.result = original

    def test_changed_work_needs_output_revision_or_identified_uncommitted_diff(self):
        self.collected()
        self.result.update(output_revision=None, diff_evidence=[])
        self.assertEqual(self.accept()['next_action'], 'verify_returned_changes')
        self.result['output_revision'] = self.result['base_revision']
        self.assertEqual(self.accept()['outcome'], 'unverified')
        self.result['diff_evidence'] = ['artifact:verified-uncommitted-diff']
        self.assertEqual(self.accept()['outcome'], 'completed')

    def test_read_only_and_no_change_can_be_accepted_explicitly(self):
        self.collected()
        self.handoff['required_checks'] = []
        self.result.update(handoff_sha256=sp.handoff_sha256(self.handoff), output_revision=None,
                           diff_evidence=[], checks_completed=[], verification_status='not_required',
                           verification_evidence=[])
        for kind in ('read_only', 'no_change'):
            self.result['change_kind'] = kind
            self.assertEqual(self.accept()['outcome'], 'completed')
        self.result['diff_evidence'] = ['artifact:contradictory-diff']
        self.assertEqual(self.accept()['outcome'], 'unverified')

    def test_inputs_are_unchanged_and_no_dispatch_modules_are_used(self):
        original = copy.deepcopy((self.handoff, self.live))
        with (patch('subprocess.run', side_effect=AssertionError('must not execute')),
              patch('subprocess.Popen', side_effect=AssertionError('must not launch'))):
            self.plan()
        self.assertEqual(original, (self.handoff, self.live))
        source = (SCRIPTS / 'session_plan.py').read_text(encoding='utf-8')
        for name in ('import subprocess', 'import socket', 'import urllib', 'import requests', 'import os'):
            self.assertNotIn(name, source)

    def test_cli_is_read_only_safe_on_unknown_secret_keys_and_duplicate_json(self):
        with runtime_directory() as tmp:
            handoff = tmp / 'handoff.json'
            handoff.write_text(json.dumps(self.handoff), encoding='utf-8')
            before = handoff.read_bytes()
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(sp.main(['cloud', '--handoff', str(handoff)]), 0)
            self.assertTrue(json.loads(output.getvalue())['planning_only'])
            self.assertEqual(before, handoff.read_bytes())
            self.assertEqual(list(tmp.iterdir()), [handoff])
            for body in (json.dumps({**self.handoff, 'credential': 'synthetic-secret-never-output'}),
                         '{"schema_version":1,"schema_version":1,"secret":"synthetic-secret-never-output"}'):
                handoff.write_text(body, encoding='utf-8')
                with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(sp.main(['cloud', '--handoff', str(handoff)]), 2)
                self.assertEqual(out.getvalue(), '')
                self.assertEqual(json.loads(err.getvalue()), {'error': 'invalid_session_plan_input'})
                self.assertNotIn('synthetic-secret-never-output', err.getvalue())
                self.assertEqual(handoff.read_text(encoding='utf-8'), body)

    def test_session_policy_is_rendered_in_temporary_home_without_changing_chat_defaults(self):
        with runtime_directory() as tmp:
            policy = routing.load_policy(codex_home=tmp)
            rendered = policy.rendered_documents()
            self.assertIn('Cloudは現行UIで実行し、開始手順は依頼本文に明記。', rendered['effective.md'])
            self.assertNotIn('cloud_run.py', rendered['effective.md'])
            for name in ('model-routing-policy.md', 'model-routing-catalog.md', 'model-routing-handoff.md'):
                self.assertIn('session_plan.py', rendered[name])
            self.assertIn('現版', rendered['model-routing-handoff.md'])
            for name in ('model-routing-policy.md', 'model-routing-handoff.md'):
                self.assertIn('codex_cloud_ui', rendered[name])
                self.assertIn('旧形式・未知・破損', rendered[name])
                self.assertIn('README・AGENTS', rendered[name])
                self.assertIn('HTTP listener', rendered[name])
            self.assertEqual(policy.config['models']['sol']['default_effort'], 'xhigh')
            self.assertEqual(policy.config['models']['astra']['default_effort'], 'medium')
            self.assertEqual(policy.config['models']['luna']['default_effort'], 'max')
            self.assertFalse((tmp / 'config.toml').exists())
            self.assertFalse((tmp / 'AGENTS.md').exists())
            self.assertFalse((tmp / 'codex-task-routing/chatgpt.json').exists())


if __name__ == '__main__':
    unittest.main()
