"""Existing-task plans and durable claims; no native app, browser or network."""
import copy
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/codex-task-routing/scripts'))
import session_plan as sp
import cloud_record as cr
import native_cloud_fixtures as fixtures
from runtime_fixtures import runtime_directory


class NativePlanningTests(unittest.TestCase):
    def setUp(self):
        self.handoff = fixtures.handoff()
        self.live = fixtures.live(self.handoff)

    def plan(self, phase='dispatch-plan'):
        return sp.native_cloud_plan(self.handoff, phase=phase, live=self.live, now=fixtures.NOW)

    def test_ready_first_native_call_is_a_nonexecuting_plan_not_creation_or_success(self):
        before = copy.deepcopy((self.handoff, self.live))
        output = self.plan()
        self.assertTrue(output['preflight_complete'])
        self.assertEqual(output['native_tool'], 'send_message_to_thread')
        self.assertEqual(output['next_action'], 'claim_then_call_native_tool_once')
        self.assertFalse(output['execution_authorized'])
        self.assertFalse(output['runtime_implemented'])
        self.assertFalse(output['creation_supported'])
        self.assertNotIn('model', output)
        self.assertNotIn('thinking', output)
        self.assertEqual((self.handoff, self.live), before)

    def test_each_policy_scope_and_capability_gap_blocks_dispatch_without_inference(self):
        gates = {'native_policy', 'host_permission', 'target_identity', 'previous_submission',
                 'dispatch_capability', 'target_authorization', 'materials_authorization',
                 'environment', 'repository_revision', 'test_environment'}
        for key in gates:
            for state in ('unknown', 'blocked', 'unavailable', 'confirmation_required'):
                with self.subTest(key=key, state=state):
                    self.live = fixtures.live(self.handoff)
                    self.live['checks'][key] = dict(state=state)
                    output = self.plan()
                    self.assertFalse(output['preflight_complete'])
                    self.assertIn(key + ':' + state, output['gaps'])
        self.live = fixtures.live(self.handoff)
        del self.live['checks']['native_policy']
        self.assertIn('native_policy:unknown', self.plan()['gaps'])

    def test_real_identity_and_environment_require_observations_not_permission_declarations(self):
        for key in sp.OBSERVED_CHECKS | {'target_identity', 'previous_submission'}:
            with self.subTest(key=key):
                self.live = fixtures.live(self.handoff)
                self.live['checks'][key]['state'] = 'permission_checked'
                self.assertFalse(self.plan()['preflight_complete'])

    def test_host_and_task_both_bound_and_work_or_local_identity_never_aliases_cloud(self):
        for key, value in [('host_id', 'local'), ('task_id', 'other-task'), ('kind', 'chatgpt_work')]:
            with self.subTest(key=key):
                self.live = fixtures.live(self.handoff)
                self.live['target'][key] = value
                self.assertIn('original_native_target_mismatch', self.plan()['gaps'])
        for target in (dict(kind='chatgpt_work', host_id='durable', task_id='original-task'),
                       dict(kind='codex', host_id='local', task_id='original-task')):
            with self.subTest(target=target), self.assertRaises(sp.SessionPlanError):
                sp.native_cloud_plan({**self.handoff, 'target': target})

    def test_native_contract_rejects_tab_creation_and_unknown_properties(self):
        for change in ({'tab_id': 'fake-tab'}, {'create': True}, {'schema_version': True},
                       {'state': dict(submission='not_sent', task_id='original-task')}):
            with self.subTest(change=change), self.assertRaises(sp.SessionPlanError):
                sp.native_cloud_plan({**self.handoff, **change})
        for transport in ('codex_cloud_ui', 'codex_cloud_cli', 'chatgptWorkCloud'):
            self.live['transport'] = transport
            with self.subTest(transport=transport), self.assertRaises(sp.SessionPlanError):
                self.plan()
        with self.assertRaises(sp.SessionPlanError):
            sp.native_cloud_plan(self.handoff, phase='create')

    def test_sent_unknown_collected_and_accepted_never_plan_another_send(self):
        for state, action in [('unknown_outcome', 'reconcile_original_submission'),
                              ('sent', 'receive_original_task'), ('collected', 'accept_original_result'),
                              ('accepted', 'reuse_accepted_result')]:
            with self.subTest(state=state):
                self.handoff['state']['submission'] = state
                output = sp.native_cloud_plan(self.handoff, phase='dispatch-plan')
                self.assertEqual(output['next_action'], action)
                self.assertIn('new_submission_forbidden', output['gaps'])

    def test_read_collection_uses_original_target_and_does_not_require_new_send_or_wait(self):
        self.handoff['state']['submission'] = 'unknown_outcome'
        keep = {'native_policy', 'host_permission', 'target_identity', 'receive_capability', 'receive_authorization'}
        self.live['checks'] = {key: value for key, value in self.live['checks'].items() if key in keep}
        output = self.plan('receive')
        self.assertTrue(output['preflight_complete'])
        self.assertEqual(output['native_tool'], 'read_thread')
        self.assertEqual(output['target'], self.handoff['target'])
        self.assertNotIn('dispatch_capability:unknown', output['gaps'])

    def test_wait_unknown_or_failure_returns_to_same_task_receive_never_resend(self):
        self.handoff['state']['submission'] = 'sent'
        for state in ('unknown', 'unavailable', 'blocked'):
            self.live['checks']['wait_capability'] = dict(state=state)
            output = self.plan('wait')
            self.assertEqual(output['next_action'], 'plan_receive_original_task')
            self.assertFalse(output['preflight_complete'])
            self.assertTrue(self.plan('receive')['preflight_complete'])
        self.live['checks']['native_policy'] = dict(state='blocked')
        self.assertEqual(self.plan('wait')['next_action'], 'resolve_phase_gaps')

    def test_actor_freshness_scope_and_target_identity_rechecked(self):
        for change in (dict(role='sol', depth=1, executor_id='root-owner'),
                       dict(role='root', depth=0, executor_id='different-owner')):
            self.live['actor'] = change
            self.assertIn('return_cloud_execution_scope_to_root', self.plan()['gaps'])
        self.live = fixtures.live(self.handoff)
        self.live['observed_at'] = (fixtures.NOW - timedelta(minutes=6)).isoformat()
        self.assertIn('live_observations_stale_or_future', self.plan()['gaps'])
        self.live = fixtures.live(self.handoff)
        self.handoff['endpoint'] = 'A changed scope'
        self.assertIn('handoff_scope_mismatch', self.plan()['gaps'])

    def test_accept_requires_current_request_correlated_response_and_actual_check_evidence(self):
        self.handoff['state']['submission'] = 'collected'
        result = fixtures.result(self.handoff)
        acceptance = dict(owner_id='root-owner', decision='accepted', criteria_checked=True, reference='Owner review')
        def accept():
            return sp.native_cloud_plan(self.handoff, phase='accept', result=result, acceptance=acceptance)
        self.assertEqual(accept()['outcome'], 'completed')
        self.assertEqual(accept()['schema_version'], 2)
        result['correlation_evidence'] = []
        self.assertEqual(accept()['next_action'], 'correlate_original_native_response')
        result = fixtures.result(self.handoff)
        result['verification_evidence'] = []
        self.assertEqual(accept()['outcome'], 'unverified')
        result = fixtures.result(self.handoff)
        result['request_id'] = 'a-previous-request'
        self.assertEqual(accept()['next_action'], 'return_identity_mismatch_to_root')
        result = fixtures.result(self.handoff)
        result['target']['host_id'] = 'local'
        self.assertEqual(accept()['outcome'], 'unverified')

    def test_hash_changes_with_each_scope_and_predecessor_but_not_submission_state(self):
        digest = sp.native_handoff_sha256(self.handoff)
        self.handoff['state']['submission'] = 'sent'
        self.assertEqual(sp.native_handoff_sha256(self.handoff), digest)
        for key, value in [('request_id', 'next-request'), ('previous_request_id', 'previous-request'),
                           ('expected_revision', '2' * 40), ('input_sha256', 'b' * 64)]:
            changed = copy.deepcopy(self.handoff)
            changed[key] = value
            self.assertNotEqual(sp.native_handoff_sha256(changed), digest)

    def test_cli_local_only_and_invalid_input_redacted(self):
        with runtime_directory() as root:
            file = root / 'handoff.json'
            file.write_text(json.dumps(self.handoff), encoding='utf-8')
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(sp.main(['cloud-native', '--handoff', str(file)]), 0)
            self.assertFalse(json.loads(out.getvalue())['runtime_implemented'])
            file.write_text('{"private-value-not-for-output":true}', encoding='utf-8')
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
                self.assertEqual(sp.main(['cloud-native', '--handoff', str(file)]), 2)
            self.assertEqual(json.loads(err.getvalue()), {'error': 'invalid_session_plan_input'})


class NativeClaimTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory(prefix='native-claim-'))
        self.root = Path(self.temp).resolve()
        self.handoff = fixtures.handoff()
        self.binding = fixtures.binding(self.handoff, self.root / 'workspace')
        self.store = cr.NativeClaimStore(self.root / 'state', self.handoff['request_id'], self.handoff['target'])

    def claim(self):
        with self.store.lock():
            return self.store.claim(copy.deepcopy(self.binding))

    def collect(self):
        record = self.claim()
        record.update(submission='collected', event='collection_recorded')
        with self.store.lock():
            self.store.save(record)
        return record

    def test_native_record_binds_original_host_task_without_tab_and_cannot_rewind(self):
        record = self.claim()
        self.assertEqual(record['schema_version'], 3)
        self.assertEqual(record['task_id'], 'original-task')
        self.assertNotIn('tab_id', record['binding'])
        for state, event in [('sent', 'submission_recorded'), ('collected', 'collection_recorded'),
                             ('accepted', 'owner_review_recorded')]:
            record.update(submission=state, event=event)
            with self.store.lock():
                self.store.save(record)
        record.update(submission='sent', event='submission_recorded')
        with self.store.lock(), self.assertRaises(cr.CloudRecordError):
            self.store.save(record)

    def test_reopen_claim_and_different_request_id_cannot_resubmit_uncertain_message(self):
        self.claim()
        with self.store.lock(), self.assertRaises(cr.CloudRecordError):
            self.store.claim(self.binding)
        other_binding = {**self.binding, 'request_id': 'other-request'}
        other = cr.NativeClaimStore(self.root / 'state', 'other-request', self.handoff['target'])
        with other.lock(), self.assertRaises(cr.CloudRecordError):
            other.claim(other_binding)
        other_binding['previous_request_id'] = self.binding['request_id']
        with other.lock(), self.assertRaises(cr.CloudRecordError):
            other.claim(other_binding)

    def test_collected_predecessor_retained_and_changed_scope_can_continue_same_task_once(self):
        self.collect()
        original = self.store.claim_path.read_bytes()
        next_binding = {**self.binding, 'request_id': 'next-request', 'previous_request_id': self.binding['request_id'],
                        'input_sha256': 'b' * 64, 'handoff_sha256': 'c' * 64}
        next_store = cr.NativeClaimStore(self.root / 'state', 'next-request', self.handoff['target'])
        with next_store.lock():
            next_store.claim(next_binding)
        self.assertEqual(self.store.claim_path.read_bytes(), original)
        sibling = cr.NativeClaimStore(self.root / 'state', 'sibling-request', self.handoff['target'])
        with sibling.lock(), self.assertRaises(cr.CloudRecordError):
            sibling.claim({**next_binding, 'request_id': 'sibling-request'})

    def test_host_task_scope_and_tab_cannot_change(self):
        record = self.collect()
        for key, value in [('input_sha256', 'b' * 64), ('handoff_sha256', 'c' * 64),
                           ('owner_id', 'other'), ('repository', 'example/other')]:
            changed = copy.deepcopy(record)
            changed['binding'][key] = value
            with self.subTest(key=key), self.store.lock(), self.assertRaises(cr.CloudRecordError):
                self.store.save(changed)
        for key, value in [('host_id', 'local'), ('task_id', 'other'), ('kind', 'chatgpt_work')]:
            changed = copy.deepcopy(record)
            changed['binding']['target'][key] = value
            with self.subTest(key=key), self.store.lock(), self.assertRaises((cr.CloudRecordError, sp.SessionPlanError)):
                self.store.save(changed)
        with self.assertRaises(sp.SessionPlanError):
            cr.validate_native_record({**record, 'binding': {**record['binding'], 'tab_id': 'invented-tab'}})

    def test_different_record_schemas_are_blocking_never_converted_or_deleted(self):
        self.claim()
        saved = self.store.claim_path.read_bytes()
        ui_store = cr.ClaimStore(self.root / 'state', self.binding['request_id'])
        with ui_store.lock(), self.assertRaises(cr.CloudRecordError):
            ui_store.read()
        self.assertEqual(self.store.claim_path.read_bytes(), saved)

    def test_failure_after_successor_reservation_blocks_new_id_and_preserves_intent(self):
        original = cr._exclusive
        with self.store.lock(), patch.object(cr, '_exclusive', wraps=cr._exclusive) as exclusive:
            def fail_claim(path, content):
                if path == self.store.claim_path:
                    raise OSError('synthetic failed claim write')
                return original(path, content)
            exclusive.side_effect = fail_claim
            with self.assertRaises(OSError):
                self.store.claim(self.binding)
        self.assertFalse(self.store.claim_path.exists())
        with self.store.lock(), self.assertRaises(cr.CloudRecordError):
            self.store.claim(self.binding)
        other = cr.NativeClaimStore(self.root / 'state', 'different-id', self.handoff['target'])
        with other.lock(), self.assertRaises(cr.CloudRecordError):
            other.claim({**self.binding, 'request_id': 'different-id'})

    def test_per_task_mutex_excludes_another_message_owner(self):
        other = cr.NativeClaimStore(self.root / 'state', 'other-request', self.handoff['target'])
        with self.store.lock(), self.assertRaises(cr.CloudRecordError):
            with other.lock():
                pass


if __name__ == '__main__':
    unittest.main()
