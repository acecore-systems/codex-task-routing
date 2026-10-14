"""Synthetic caller scope; no model, Browser, authentication or dispatch occurs."""
import copy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/codex-task-routing/scripts'
sys.path.insert(0, str(SCRIPTS))
import chat_plan as cp
import chatgpt_route as route


class ChatActorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 14, tzinfo=timezone.utc)
        self.facts = dict(substantial=True, parent_has_independent_work=True,
                          materials_approved=True, handoff_proportionate=True,
                          requires_repeated_local_access=False, needs=[])
        self.live = dict(is_root=False, route_enabled=True, surface='chat-temporary',
                         model='Pro', transport='browser-temporary', actions_authorized=True,
                         quota_state='no_limit_notice', observed_at=self.now.isoformat(),
                         browser_checks=dict(inspect='permission_checked', send='permission_checked',
                                             receive='permission_checked'),
                         actor=dict(role='sol', depth=1, executor_id='synthetic-owner',
                                    owner_id='synthetic-owner', host_allows_chat=True))
        self.bundle = route.prepare_payload(dict(task='Review only supplied material.',
                                                materials='Synthetic test material.',
                                                acceptance_criteria=['Return bounded evidence.']))

    def evaluate(self, phase, live):
        if phase == 'inspect':
            return cp.inspection_plan(live)
        if phase == 'dispatch':
            return cp.plan(self.facts, now=self.now, live=live)
        return cp.collection_plan(live, self.bundle, now=self.now)

    def all_phases(self, live):
        return [(phase, self.evaluate(phase, live)) for phase in ('inspect', 'dispatch', 'receive')]

    def test_direct_sol_owner_is_candidate_in_all_phases_without_attestation(self):
        before = copy.deepcopy(self.live)
        results = dict(self.all_phases(self.live))
        self.assertEqual(results['inspect']['next_action'], 'inspect_chat_ui')
        self.assertEqual(results['inspect']['resolution']['owner'], 'sol_child_owner')
        self.assertFalse(results['inspect']['dispatch_authorized'])
        self.assertEqual(results['dispatch']['next_action'], 'send_once')
        self.assertEqual(results['receive']['next_action'], 'collect_reply')
        for phase in ('dispatch', 'receive'):
            self.assertFalse(results[phase]['dispatch_performed'])
            self.assertFalse(results[phase]['authorization_attested'])
            self.assertEqual(results[phase]['observation_source'], 'caller_supplied')
            self.assertEqual(results[phase]['backend_model'], 'unknown')
        self.assertEqual(self.live, before)

    def test_root_legacy_and_explicit_root_actor_are_compatible(self):
        legacy = {k: v for k, v in self.live.items() if k != 'actor'}
        legacy['is_root'] = True
        explicit = {**legacy, 'actor': {**self.live['actor'], 'role': 'root', 'depth': 0}}
        for phase in ('inspect', 'dispatch', 'receive'):
            self.assertEqual(self.evaluate(phase, legacy), self.evaluate(phase, explicit))

    def test_false_root_alone_cannot_establish_eligibility(self):
        live = {k: v for k, v in self.live.items() if k != 'actor'}
        for phase, result in self.all_phases(live):
            with self.subTest(phase=phase):
                self.assertEqual(result['route'], 'not_ready')
                self.assertEqual(result['reason'], 'is_root_not_established')

    def test_ineligible_role_or_depth_is_rejected_across_phases(self):
        for role, depth in (('astra', 1), ('luna', 1), ('sol', 2), ('astra', 2),
                            ('luna', 2), ('sol', 3), ('terra', 1), ('unknown', 1)):
            live = {**self.live, 'actor': {**self.live['actor'], 'role': role, 'depth': depth}}
            for phase, result in self.all_phases(live):
                with self.subTest(role=role, depth=depth, phase=phase):
                    self.assertEqual(result['reason'], 'actor_not_eligible')
                    self.assertEqual(result['route'], 'not_ready')

    def test_root_role_depth_and_boolean_are_not_silently_reconciled(self):
        for is_root, role, depth in ((True, 'sol', 1), (True, 'root', 1),
                                    (True, 'sol', 0), (False, 'root', 0), (False, 'sol', 0)):
            live = {**self.live, 'is_root': is_root,
                    'actor': {**self.live['actor'], 'role': role, 'depth': depth}}
            for phase, result in self.all_phases(live):
                with self.subTest(phase=phase, is_root=is_root, role=role, depth=depth):
                    self.assertEqual(result['reason'], 'actor_root_context_mismatch')

    def test_actor_requires_exact_types_and_fields_and_never_infers_model_role(self):
        for changes in ({'depth': True}, {'depth': False}, {'depth': 1.0}, {'depth': '1'},
                        {'depth': None}, {'depth': -1}, {'host_allows_chat': 'true'},
                        {'host_allows_chat': 1}, {'executor_id': ''}, {'owner_id': []},
                        {'model_id': 'gpt-6.1-sol'}):
            live = {**self.live, 'actor': {**self.live['actor'], **changes}}
            for phase in ('inspect', 'dispatch', 'receive'):
                with self.subTest(phase=phase, changes=changes), self.assertRaises(cp.ChatRouteError):
                    self.evaluate(phase, live)
        for field in self.live['actor']:
            actor = {k: v for k, v in self.live['actor'].items() if k != field}
            for phase in ('inspect', 'dispatch', 'receive'):
                with self.subTest(field=field, phase=phase), self.assertRaises(cp.ChatRouteError):
                    self.evaluate(phase, {**self.live, 'actor': actor})

    def test_host_permission_and_owner_mismatch_block_without_new_approval(self):
        for changes, reason in (({'host_allows_chat': False}, 'actor_host_permission_not_established'),
                                ({'owner_id': 'another-owner'}, 'actor_request_owner_mismatch')):
            live = {**self.live, 'actor': {**self.live['actor'], **changes}}
            before = copy.deepcopy(live)
            for phase, result in self.all_phases(live):
                with self.subTest(phase=phase, changes=changes):
                    self.assertEqual(result['reason'], reason)
                    self.assertEqual(result['route'], 'not_ready')
            self.assertEqual(live, before)

    def test_legacy_transport_is_not_opened_to_children(self):
        live = {k: v for k, v in self.live.items() if k != 'browser_checks'}
        live.update(surface='chat', transport='codex-app-tools', transport_authorized=True)
        for phase, result in self.all_phases(live):
            self.assertEqual(result['reason'], 'child_requires_browser_temporary')
        root = {k: v for k, v in live.items() if k != 'actor'}
        root['is_root'] = True
        self.assertEqual(self.evaluate('dispatch', root)['next_action'], 'send_once')
        self.assertEqual(self.evaluate('receive', root)['next_action'], 'collect_reply')

    def test_child_retains_existing_permission_model_and_freshness_gates(self):
        for changes, reason in (({'route_enabled': False}, 'route_enabled_not_established'),
                                ({'actions_authorized': False}, 'actions_authorized_not_established'),
                                ({'transport_authorized': False}, 'transport_authorized_not_established'),
                                ({'model': 'unknown'}, 'required_model_not_observed'),
                                ({'observed_at': '2026-10-13T00:00:00Z'}, 'refresh_live_observations')):
            live = {**self.live, **changes}
            for phase in ('dispatch', 'receive'):
                with self.subTest(phase=phase, changes=changes):
                    self.assertEqual(self.evaluate(phase, live)['reason'], reason)
        self.assertEqual(self.evaluate('dispatch', {**self.live, 'quota_state': 'exhausted'})['reason'],
                         'quota_exhausted')

    def test_child_receive_and_root_handoff_do_not_require_another_send(self):
        live = {**self.live, 'quota_state': 'exhausted',
                'browser_checks': {**self.live['browser_checks'], 'send': 'blocked'}}
        result = self.evaluate('receive', live)
        self.assertEqual(result['next_action'], 'collect_reply')
        self.assertFalse(result['dispatch_authorized'])
        root = {k: v for k, v in live.items() if k != 'actor'}
        root['is_root'] = True
        self.assertEqual(result, self.evaluate('receive', root))
        self.assertEqual(result['request_id'], self.bundle['request_id'])
        self.assertEqual(result['input_sha256'], self.bundle['input_sha256'])

    def test_parallel_owner_requires_its_own_independent_work(self):
        self.facts['parent_has_independent_work'] = False
        self.assertEqual(self.evaluate('dispatch', self.live)['reason'], 'parent_has_independent_work_false')
        self.facts['delegation'] = dict(mode='serial_specialist', host_allows_serial=True,
                                        reason='Bounded reasoning on complete approved material.')
        self.assertEqual(self.evaluate('dispatch', self.live)['next_action'], 'send_once')
        self.facts['delegation']['host_allows_serial'] = False
        self.assertEqual(self.evaluate('dispatch', self.live)['reason'], 'host_does_not_allow_serial_delegation')


if __name__ == '__main__':
    unittest.main()
