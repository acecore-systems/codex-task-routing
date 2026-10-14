"""Synthetic host observations and local protocol only; not a real Browser run."""
from contextlib import redirect_stdout, redirect_stderr
import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import unittest

from runtime_fixtures import runtime_directory

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/codex-task-routing/scripts'
sys.path.insert(0, str(SCRIPTS))
import chat_plan as cp
import chatgpt_route as route
import chat_transfer as transfer


class BrowserPreflightTests(unittest.TestCase):
    def setUp(self):
        self.home = self.enterContext(runtime_directory())
        self.now = datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc)
        self.facts = dict(substantial=True, parent_has_independent_work=True,
                          materials_approved=True, handoff_proportionate=True,
                          requires_repeated_local_access=False, needs=[])
        # Test-only observations, not a statement about any installed Browser.
        self.checks = {op: 'permission_checked' for op in cp.BROWSER_OPERATIONS}
        self.live = dict(is_root=True, route_enabled=True, transport='browser-temporary',
                         surface='chat-temporary', model='Pro', actions_authorized=True,
                         quota_state='no_limit_notice', observed_at=self.now.isoformat(),
                         browser_checks=self.checks)
        self.bundle = route.prepare_payload(dict(task='Compare the supplied numbers.',
                                                materials='A=1, B=2',
                                                acceptance_criteria=['Identify the smaller value.']))

    def evaluate(self, **changes):
        return cp.plan(self.facts, now=self.now, live={**self.live, **changes})

    def collect(self, **changes):
        return cp.collection_plan({**self.live, **changes}, self.bundle, now=self.now)

    def test_operation_checks_replace_not_duplicate_aggregate_and_evidence(self):
        before = copy.deepcopy(self.live)
        result = self.evaluate()
        self.assertEqual(result['next_action'], 'send_once')
        self.assertTrue(result['preflight_complete'])
        self.assertFalse(result['dispatch_performed'])
        self.assertFalse(result['authorization_attested'])
        self.assertEqual(result['backend_model'], 'unknown')
        self.assertEqual(self.live, before)
        self.assertNotIn('transport_authorized', self.live)
        self.assertNotIn('transport_evidence', self.live)

    def test_missing_null_and_false_do_not_turn_into_permission(self):
        legacy = {k: v for k, v in self.live.items() if k != 'browser_checks'}
        for extra in ({}, {'transport_authorized': None}, {'transport_authorized': False}):
            live = {**legacy, **extra}
            before = copy.deepcopy(live)
            result = cp.plan(self.facts, now=self.now, live=live)
            self.assertFalse(result['preflight_complete'])
            self.assertFalse(result['dispatch_authorized'])
            self.assertEqual(live, before)
        self.assertEqual(cp.plan(self.facts, now=self.now, live=legacy)['route'], 'preflight_needed')

    def test_legacy_false_wins_over_new_positive_checks(self):
        result = self.evaluate(transport_authorized=False)
        self.assertEqual(result['reason'], 'transport_authorized_not_established')
        self.assertFalse(result['dispatch_authorized'])
        self.assertEqual(self.collect(transport_authorized=False)['route'], 'not_ready')

    def test_legacy_true_cannot_override_more_specific_unknown_or_denied_operations(self):
        for state in cp.BROWSER_STATES - cp.BROWSER_CONFIRMED:
            with self.subTest(state=state):
                result = self.evaluate(transport_authorized=True,
                                       browser_checks={**self.checks, 'receive': state})
                self.assertEqual(result['reason'], 'browser_receive_' + state)
                self.assertFalse(result['dispatch_authorized'])

    def test_each_gap_has_specific_owner_place_action_and_operation(self):
        for op in cp.BROWSER_OPERATIONS:
            for state in cp.BROWSER_STATES - cp.BROWSER_CONFIRMED:
                with self.subTest(op=op, state=state):
                    result = self.evaluate(browser_checks={**self.checks, op: state})
                    self.assertFalse(result['preflight_complete'])
                    for field in ('owner', 'where', 'action'):
                        self.assertTrue(result['resolution'][field])
                    self.assertEqual(result['resolution']['operation'], op)
                    if state == 'provider_review':
                        self.assertEqual(result['next_action'], 'resolve_provider_scope')
                        self.assertIn('Output-extraction', result['resolution']['action'])
                    if state == 'confirmation_required':
                        self.assertIn('actual Browser', result['resolution']['where'])

    def test_denial_is_not_hidden_behind_an_unknown_inspection(self):
        result = self.evaluate(browser_checks={'inspect': 'unknown', 'send': 'blocked'})
        self.assertEqual(result['reason'], 'browser_send_blocked')

    def test_omitted_operation_is_unknown_not_confirmed(self):
        for omitted in cp.BROWSER_OPERATIONS:
            result = self.evaluate(browser_checks={k: v for k, v in self.checks.items() if k != omitted})
            self.assertEqual(result['reason'], 'browser_' + omitted + '_unknown')
        self.assertFalse(self.evaluate(browser_checks={})['preflight_complete'])

    def test_inspection_does_not_need_task_payload_quota_model_or_transfer_authorization(self):
        minimal = {k: self.live[k] for k in ('is_root', 'route_enabled', 'transport')}
        for value in (None, False):
            live = {**minimal, 'transport_authorized': value,
                    'browser_checks': {'inspect': 'permission_checked'}}
            before = copy.deepcopy(live)
            result = cp.inspection_plan(live)
            self.assertEqual(result['next_action'], 'inspect_chat_ui')
            self.assertFalse(result['dispatch_authorized'])
            self.assertFalse(result['preflight_complete'])
            self.assertEqual(live, before)

    def test_unknown_inspection_loads_contract_not_send_or_blank_model_probe(self):
        result = cp.inspection_plan({k: self.live[k] for k in ('is_root', 'route_enabled', 'transport')})
        self.assertEqual(result['next_action'], 'inspect_operation_contract')
        self.assertFalse(result['dispatch_authorized'])
        self.assertEqual(result['resolution']['operation'], 'inspect')

    def test_inspection_itself_respects_its_actual_denial_or_pending_confirmation(self):
        for state in cp.BROWSER_STATES - cp.BROWSER_CONFIRMED:
            result = cp.inspection_plan({**self.live, 'browser_checks': {'inspect': state}})
            self.assertNotEqual(result['next_action'], 'inspect_chat_ui')
            self.assertFalse(result['dispatch_authorized'])
        for gate in ('is_root', 'route_enabled'):
            self.assertEqual(cp.inspection_plan({**self.live, gate: False})['route'], 'not_ready')

    def test_inspection_is_not_silently_applied_to_other_transports(self):
        result = cp.inspection_plan({**self.live, 'transport': 'codex-app-tools'})
        self.assertEqual(result['reason'], 'inspection_requires_browser_temporary')
        with self.assertRaises(cp.ChatRouteError):
            self.evaluate(surface='chat', transport='codex-app-tools')

    def test_malformed_browser_observations_are_not_truthy_permissions(self):
        for value in (None, True, [], {'unexpected': 'verified'}, {'send': True},
                      {'send': 'true'}, {'send': 'advertised'}, {'send': {}}, {'send': None}):
            with self.subTest(value=value), self.assertRaises(cp.ChatRouteError):
                self.evaluate(browser_checks=value)
        for value in ('true', 1, [], {}):
            with self.subTest(value=value), self.assertRaises(cp.ChatRouteError):
                self.evaluate(transport_authorized=value)

    def test_legacy_optional_audit_conflicts_still_need_reconciliation(self):
        evidence = dict(basis='host_tool_contract', reference='Synthetic conflicting test metadata',
                        transport='codex-app-tools', operations=['send', 'receive'])
        self.assertEqual(self.evaluate(transport_evidence=evidence)['reason'], 'transport_evidence_scope_mismatch')

    def test_ui_refresh_does_not_reset_an_unchanged_permission(self):
        old = {**self.live, 'observed_at': (self.now - timedelta(minutes=3)).isoformat()}
        before = copy.deepcopy(old)
        self.assertEqual(cp.plan(self.facts, now=self.now, live=old)['reason'], 'refresh_live_observations')
        old['observed_at'] = self.now.isoformat()
        self.assertEqual(cp.plan(self.facts, now=self.now, live=old)['next_action'], 'send_once')
        self.assertEqual(old['browser_checks'], before['browser_checks'])

    def test_permission_checked_needs_no_trial_write_or_verified_execution(self):
        for state in ('permission_checked', 'verified'):
            result = self.evaluate(browser_checks={op: state for op in cp.BROWSER_OPERATIONS})
            self.assertEqual(result['next_action'], 'send_once')
            self.assertFalse(result['dispatch_performed'])
        result = cp.plan(self.facts, now=self.now)
        self.assertEqual(result['next_action'], 'inspect_browser')
        self.assertFalse(result['preflight_complete'])

    def test_other_dispatch_gates_are_unchanged(self):
        for changes in ({'model': 'Thinking'}, {'surface': 'work'}, {'actions_authorized': False},
                        {'is_root': False}, {'route_enabled': False}, {'quota_state': 'exhausted'}):
            self.assertEqual(self.evaluate(**changes)['route'], 'not_ready')
        self.assertEqual(self.evaluate(quota_state='unknown')['route'], 'preflight_needed')
        self.facts['materials_approved'] = False
        self.assertEqual(self.evaluate()['reason'], 'materials_approval_not_established')

    def test_reading_existing_reply_does_not_need_new_generation_quota_or_send_permission(self):
        for quota in ('exhausted', 'unknown', 'no_limit_notice'):
            result = self.collect(quota_state=quota,
                                  browser_checks={'inspect': 'permission_checked', 'send': 'blocked',
                                                  'receive': 'permission_checked'})
            self.assertEqual(result['next_action'], 'collect_reply')
            self.assertFalse(result['dispatch_authorized'])
            self.assertFalse(result['dispatch_performed'])
            self.assertEqual(result['request_id'], self.bundle['request_id'])
        live = {k: v for k, v in self.live.items() if k != 'quota_state'}
        self.assertEqual(cp.collection_plan(live, self.bundle, now=self.now)['route'], 'chat_collection')

    def test_receive_still_requires_its_own_permission_and_fresh_model_observation(self):
        for state in cp.BROWSER_STATES - cp.BROWSER_CONFIRMED:
            result = self.collect(browser_checks={**self.checks, 'receive': state})
            self.assertFalse(result['preflight_complete'])
        for changes in ({'model': 'Thinking'}, {'actions_authorized': False}, {'surface': 'work'},
                        {'observed_at': (self.now - timedelta(minutes=3)).isoformat()}):
            result = self.collect(**changes)
            self.assertNotEqual(result.get('next_action'), 'collect_reply')

    def test_receive_only_evidence_does_not_authorize_sending(self):
        evidence = dict(basis='host_tool_contract', reference='Synthetic receive-only record',
                        transport='browser-temporary', operations=['receive'])
        self.assertEqual(self.collect(transport_evidence=evidence)['next_action'], 'collect_reply')
        self.assertEqual(self.evaluate(transport_evidence=evidence)['reason'], 'transport_evidence_incomplete')

    def test_collection_must_correlate_with_an_unmodified_existing_request(self):
        for change in ({'input_sha256': '0' * 64}, {'prompt': 'not a generated request'}):
            with self.assertRaises(cp.ChatRouteError):
                cp.collection_plan(self.live, {**self.bundle, **change}, now=self.now)
        with self.assertRaises(cp.ChatRouteError):
            cp.collection_plan(self.live, self.bundle, now=datetime(2026, 10, 14))

    def test_inspect_cli_does_not_read_task_or_inventory_and_receive_does_not_resend(self):
        live_path, request_path = self.home / 'live.json', self.home / 'bundle.json'
        live_path.write_text(json.dumps({**self.live, 'observed_at': datetime.now(timezone.utc).isoformat()}), encoding='utf-8')
        request_path.write_text(json.dumps(self.bundle), encoding='utf-8')
        for phase in ('inspect', 'receive'):
            args = ['--phase', phase, '--live', str(live_path), '--inventory', str(self.home / 'absent.json')]
            if phase == 'receive':
                args.extend(['--request', str(request_path)])
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(cp.main(args), 0)
            result = json.loads(output.getvalue())
            self.assertFalse(result['dispatch_authorized'])
            self.assertNotIn(self.bundle['prompt'], output.getvalue())
            self.assertEqual(result['next_action'], 'inspect_chat_ui' if phase == 'inspect' else 'collect_reply')

    def test_cli_requires_phase_inputs_and_private_auth_never_reads_live_or_request(self):
        for args in ([], ['--phase', 'inspect'], ['--phase', 'receive', '--live', 'absent']):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                cp.main(args)
            self.assertEqual(error.exception.code, 2)
        facts_path = self.home / 'facts.json'
        facts_path.write_text(json.dumps({**self.facts, 'requires_private_auth': True}), encoding='utf-8')
        for phase in ('inspect', 'receive'):
            output = io.StringIO()
            with redirect_stdout(output):
                rc = cp.main(['--phase', phase, '--facts', str(facts_path), '--live', 'absent', '--request', 'absent'])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(output.getvalue())['route'], 'local_auth')

    def test_fixture_exchange_reaches_local_validation_without_any_browser_or_model_call(self):
        # This tests protocol composition, NOT Codex -> real ChatGPT execution.
        self.assertEqual(self.evaluate()['next_action'], 'send_once')
        self.assertEqual(self.collect()['next_action'], 'collect_reply')
        path = self.home / 'reply.json'
        state = transfer.TransferState(bundle=self.bundle, reply_path=path)
        response = dict(request_id=self.bundle['request_id'], input_sha256=self.bundle['input_sha256'],
                        status='completed', result='A', evidence=['Synthetic fixed test response.'])
        validated, created = state.save_response(response)
        self.assertTrue(created)
        self.assertEqual(validated['status'], 'completed')
        self.assertEqual(state.save_response(response)[1], False)
        with self.assertRaises(transfer.ChatTransferError):
            state.save_response({**response, 'result': 'B'})
        self.assertEqual(json.loads(path.read_text())['result'], 'A')


if __name__ == '__main__':
    unittest.main()
