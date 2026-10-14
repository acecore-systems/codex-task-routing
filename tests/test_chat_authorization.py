"""Pro UI compatibility and scoped transfer evidence; no real authorization."""
from contextlib import redirect_stdout, redirect_stderr
import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import unittest

from runtime_fixtures import runtime_directory

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'plugins/codex-task-routing/scripts'
sys.path.insert(0, str(SCRIPTS))
import chat_plan as plan
import chatgpt_route as route
import routing


class ChatAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.home = self.enterContext(runtime_directory())
        self.now = datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc)
        self.facts = dict(substantial=True, parent_has_independent_work=True,
                          materials_approved=True, handoff_proportionate=True,
                          requires_repeated_local_access=False, needs=[])
        self.evidence = dict(basis='host_tool_contract',
                             reference='Synthetic test contract: this specific transfer is permitted.',
                             transport='browser-temporary', operations=['send', 'receive'])
        self.live = dict(is_root=True, route_enabled=True, surface='chat-temporary',
                         model='Pro', transport='browser-temporary', transport_authorized=True,
                         transport_evidence=self.evidence, actions_authorized=True,
                         quota_state='no_limit_notice', observed_at=self.now.isoformat())
        self.inventory = dict(schema_version=1, surface='chat-temporary', model='Pro',
                              installed_plugins=[], capabilities=[])

    def evaluate(self, **changes):
        return plan.plan(self.facts, self.inventory, now=self.now, live={**self.live, **changes})

    def test_all_selected_label_aliases_share_config_inventory_and_live_validation(self):
        for label in ('Pro', '6 Pro', 'GPT-6 Pro', ' pro ', ' 6  PRO ', 'GPT-6\tPro'):
            with self.subTest(label=label):
                self.assertTrue(route.is_required_model(label))
                config = route._validate_config({**route.DEFAULT_CONFIG, 'required_model': label})
                self.assertEqual(config['required_model'], 'Pro')
                self.inventory['model'] = label
                result = self.evaluate(model=label)
                self.assertEqual(result['route'], 'chat_candidate')
                self.assertEqual(result['backend_model'], 'unknown')
                self.assertEqual(result['observation_source'], 'caller_supplied')
                self.assertFalse(result['authorization_attested'])

    def test_alias_only_changes_between_inventory_and_live_do_not_block(self):
        for saved, current in [('6 Pro', 'Pro'), ('Pro', 'GPT-6 Pro')]:
            self.inventory['model'] = saved
            self.assertEqual(self.evaluate(model=current)['route'], 'chat_candidate')

    def test_substrings_plan_badges_backend_ids_and_other_models_are_not_aliases(self):
        for label in ('', 'GPT-6', 'Sol Pro', 'Thinking', 'ChatGPT Pro plan', 'Pro account',
                      '6 Pro fallback', 'gpt-6-astra', 'Prox', 'GPT-5.6 Pro', 'unknown',
                      'Ｐｒｏ', 'Pro' + ' ' * 65):
            with self.subTest(label=label):
                self.assertFalse(route.is_required_model(label))
                with self.assertRaises(route.ChatRouteError):
                    route._validate_config({**route.DEFAULT_CONFIG, 'required_model': label})
                with self.assertRaises(route.ChatRouteError):
                    plan.validate_inventory({**self.inventory, 'model': label})
                if label.strip():
                    self.assertEqual(self.evaluate(model=label)['reason'], 'required_model_not_observed')
        for value in (None, [], {}, 6, True):
            self.assertFalse(route.is_required_model(value))
            with self.assertRaises(route.ChatRouteError):
                route._validate_config({**route.DEFAULT_CONFIG, 'required_model': value})

    def test_status_and_root_hook_accept_legacy_config_without_rewriting_it(self):
        path = self.home / route.CONFIG_RELATIVE
        path.parent.mkdir(parents=True)
        for label in route.REQUIRED_MODEL_LABELS:
            for transport in sorted(route.SUPPORTED_TRANSPORTS):
                with self.subTest(label=label, transport=transport):
                    path.write_text(json.dumps({**route.DEFAULT_CONFIG, 'enabled': True,
                                               'required_model': label, 'transport': transport}), encoding='utf-8')
                    before = path.read_bytes()
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(route.main(['status', '--codex-home', str(self.home)]), 0)
                    status = json.loads(output.getvalue())
                    self.assertEqual(status['required_model'], 'Pro')
                    self.assertEqual(status['accepted_ui_labels'], list(route.REQUIRED_MODEL_LABELS))
                    self.assertFalse(status['model_selection_observed'])
                    self.assertEqual(status['backend_model'], 'unknown')
                    context = routing._chatgpt_context(self.home, routing.PLUGIN_ROOT)
                    self.assertIn('required UI model: Pro', context)
                    self.assertIn('6 Pro / GPT-6 Pro', context)
                    self.assertIn('transport_evidence', context)
                    self.assertEqual(path.read_bytes(), before)

    def test_confirmed_transport_needs_no_second_evidence_object(self):
        for remove in (False, True):
            live = {**self.live, 'transport_evidence': None}
            if remove:
                del live['transport_evidence']
            before = copy.deepcopy(live)
            result = plan.plan(self.facts, now=self.now, live=live)
            self.assertEqual(result['reason'], 'supplied_dispatch_checks_passed')
            self.assertEqual(result['next_action'], 'send_once')
            self.assertTrue(result['preflight_complete'])
            self.assertFalse(result['authorization_attested'])
            self.assertFalse(result['dispatch_performed'])
            self.assertEqual(live, before)

    def test_evidence_never_turns_false_gates_true(self):
        before = copy.deepcopy((self.facts, self.live, self.inventory))
        for field in ('is_root', 'route_enabled', 'transport_authorized', 'actions_authorized'):
            with self.subTest(field=field):
                result = self.evaluate(**{field: False})
                self.assertEqual(result['route'], 'not_ready')
                self.assertEqual(result['reason'], field + '_not_established')
        self.assertEqual((self.facts, self.live, self.inventory), before)

    def test_each_supported_evidence_basis_is_an_assertion_not_execution(self):
        for basis in ('host_tool_contract', 'official_documentation', 'provider_permission'):
            result = self.evaluate(transport_evidence={**self.evidence, 'basis': basis})
            self.assertEqual(result['route'], 'chat_candidate')
            self.assertFalse(result['authorization_attested'])
            self.assertEqual(result['observation_source'], 'caller_supplied')

    def test_opt_in_paid_plan_advertisement_and_success_are_not_permission_bases(self):
        for basis in ('user_opt_in', 'paid_plan', 'tool_advertised', 'execution_success', 'unknown'):
            with self.subTest(basis=basis), self.assertRaises(route.ChatRouteError):
                self.evaluate(transport_evidence={**self.evidence, 'basis': basis})

    def test_evidence_scope_and_both_directions_are_required(self):
        result = self.evaluate(transport_evidence={**self.evidence, 'transport': 'codex-app-tools'})
        self.assertEqual(result['reason'], 'transport_evidence_scope_mismatch')
        for operations in ([], ['send'], ['receive']):
            with self.subTest(operations=operations):
                result = self.evaluate(transport_evidence={**self.evidence, 'operations': operations})
                self.assertEqual(result['reason'], 'transport_evidence_incomplete')
        result = self.evaluate(transport_evidence={**self.evidence, 'operations': ['receive', 'send']})
        self.assertEqual(result['route'], 'chat_candidate')

    def test_malformed_evidence_is_rejected_without_echoing_reference(self):
        invalid = [True, [], {}, {**self.evidence, 'extra': True},
                   {**self.evidence, 'reference': ''}, {**self.evidence, 'reference': '\ud800'},
                   {**self.evidence, 'reference': 'x' * 513}]
        for operations in ('send', ['send', 'send'], ['send', 'receive', 'receive'], [True], [{}], ['write']):
            invalid.append({**self.evidence, 'operations': operations})
        for evidence in invalid:
            with self.subTest(evidence=evidence), self.assertRaises(route.ChatRouteError):
                self.evaluate(transport_evidence=evidence)
        facts_path, live_path = self.home / 'facts.json', self.home / 'live.json'
        facts_path.write_text(json.dumps(self.facts), encoding='utf-8')
        live_path.write_text(json.dumps({**self.live, 'transport_evidence': {
            **self.evidence, 'basis': 'invalid', 'reference': 'DO_NOT_ECHO_SENTINEL'}}), encoding='utf-8')
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            self.assertEqual(plan.main(['--facts', str(facts_path), '--live', str(live_path)]), 2)
        self.assertEqual(output.getvalue(), '')
        self.assertNotIn('DO_NOT_ECHO_SENTINEL', error.getvalue())

    def test_model_quota_freshness_and_host_denial_are_not_bypassed_by_evidence(self):
        for changes, expected in [({'model': 'Thinking'}, 'not_ready'),
                                  ({'quota_state': 'exhausted'}, 'not_ready'),
                                  ({'quota_state': 'unknown'}, 'preflight_needed'),
                                  ({'observed_at': (self.now - timedelta(minutes=3)).isoformat()}, 'preflight_needed'),
                                  ({'surface': 'work'}, 'not_ready'), ({'transport': 'api'}, 'not_ready')]:
            self.assertEqual(self.evaluate(**changes)['route'], expected)
        self.facts['delegation'] = dict(mode='serial_specialist', host_allows_serial=False,
                                        reason='Synthetic host denial')
        self.assertEqual(self.evaluate()['reason'], 'host_does_not_allow_serial_delegation')

    def test_evidence_and_aliases_do_not_change_child_or_parent_settings(self):
        parent = self.home / 'config.toml'
        parent.write_text('model = "user-selected-parent"\nmodel_reasoning_effort = "high"\n', encoding='utf-8')
        before = parent.read_bytes()
        policy = routing.load_policy(codex_home=self.home)
        self.assertEqual(policy.config['models']['sol']['id'], 'gpt-6.1-sol')
        self.assertEqual(policy.config['models']['sol']['default_effort'], 'max')
        self.evaluate()
        self.assertEqual(parent.read_bytes(), before)

    def test_legacy_classification_remains_advisory_without_live(self):
        result = plan.plan(self.facts, self.inventory, now=self.now)
        self.assertEqual(result['route'], 'chat_candidate')
        self.assertFalse(result['live_checked'])
        self.assertFalse(result['authorization_attested'])
        self.assertEqual(result['backend_model'], 'unknown')


if __name__ == '__main__':
    unittest.main()
