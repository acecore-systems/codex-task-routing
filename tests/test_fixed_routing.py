"""Policy/dispatch regressions: no calls to models, accounts or external services."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'plugins/codex-task-routing/scripts'
sys.path.insert(0, str(SCRIPTS))
import chat_plan as cp
import chatgpt_route as route
import routing


class FixedRoutingTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / 'tests/.tmp-runtime-tests'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.now = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
        self.facts = dict(substantial=True, parent_has_independent_work=False,
                          materials_approved=True, handoff_proportionate=True,
                          requires_repeated_local_access=False, needs=[])
        self.serial = dict(mode='serial_specialist', host_allows_serial=True,
                           reason='Specialist reasoning on complete approved materials.')
        self.live = dict(is_root=True, route_enabled=True, surface='chat-temporary',
                         model='6 Pro', transport='browser-temporary', transport_authorized=True,
                         actions_authorized=True, quota_state='no_limit_notice',
                         observed_at=self.now.isoformat())

    def plan(self, **changes):
        return cp.plan({**self.facts, 'delegation': self.serial}, now=self.now,
                       live={**self.live, **changes})

    def test_legacy_parallel_and_explicit_serial_are_distinct(self):
        self.assertEqual(cp.plan(self.facts)['route'], 'codex')
        self.assertEqual(self.plan()['route'], 'chat_candidate')
        self.assertTrue(self.plan()['live_checked'])
        self.serial['host_allows_serial'] = False
        self.assertEqual(self.plan()['reason'], 'host_does_not_allow_serial_delegation')
        self.facts['parent_has_independent_work'] = True
        self.assertEqual(self.plan()['route'], 'codex')

    def test_bad_serial_declarations_do_not_create_permission(self):
        for value in (None, {}, {'mode': 'parallel'},
                      {**self.serial, 'host_allows_serial': 'true'},
                      {**self.serial, 'reason': ''}, {**self.serial, 'extra': True}):
            with self.subTest(value=value), self.assertRaises(cp.ChatRouteError):
                cp.plan({**self.facts, 'delegation': value})

    def test_each_permission_gate_blocks_independently(self):
        for field in ('is_root', 'route_enabled', 'transport_authorized', 'actions_authorized'):
            with self.subTest(field=field):
                self.assertEqual(self.plan(**{field: False})['route'], 'not_ready')
                with self.assertRaises(cp.ChatRouteError):
                    self.plan(**{field: 'true'})

    def test_no_work_api_or_silent_model_fallback(self):
        for fields in ({'surface': 'work'}, {'surface': 'chat'}, {'transport': 'api'},
                       {'model': 'GPT-5.6 Thinking Medium'}, {'model': 'unknown'}, {'model': 'Sol Pro'}):
            with self.subTest(fields=fields):
                self.assertEqual(self.plan(**fields)['route'], 'not_ready')

    def test_quota_and_snapshot_freshness(self):
        self.assertEqual(self.plan(quota_state='exhausted')['route'], 'not_ready')
        self.assertEqual(self.plan(quota_state='unknown')['route'], 'preflight_needed')
        for seconds in (-121, 1):
            observed = self.now + timedelta(seconds=seconds)
            self.assertEqual(self.plan(observed_at=observed.isoformat())['route'], 'preflight_needed')
        self.assertEqual(self.plan(observed_at=(self.now-timedelta(seconds=120)).isoformat())['route'], 'chat_candidate')
        with self.assertRaises(cp.ChatRouteError):
            self.plan(quota_state='unlimited')

    def test_live_does_not_override_short_unapproved_or_local_work(self):
        for field, value in [('substantial', False), ('handoff_proportionate', False),
                             ('materials_approved', False), ('requires_repeated_local_access', True)]:
            original = self.facts[field]
            self.facts[field] = value
            self.assertNotEqual(self.plan()['route'], 'chat_candidate')
            self.facts[field] = original

    def test_read_scope_does_not_grant_write_or_cross_surface_capabilities(self):
        read = dict(tool='GitHub', operation='fetch_file', scope='example/project')
        inventory = dict(schema_version=1, surface='chat-temporary', model='6 Pro',
                         installed_plugins=['GitHub'], capabilities=[dict(
                             **read, state='verified', observed_at=self.now.isoformat(),
                             expires_at=(self.now+timedelta(days=1)).isoformat())])
        facts = {**self.facts, 'delegation': self.serial, 'needs': [{**read, 'operation': 'update_file'}]}
        self.assertEqual(cp.plan(facts, inventory, now=self.now, live=self.live)['route'], 'preflight_needed')
        facts['needs'] = [read]
        legacy_live = {**self.live, 'surface': 'chat', 'transport': 'codex-app-tools'}
        self.assertEqual(cp.plan(facts, inventory, now=self.now, live=legacy_live)['reason'], 'capability_surface_mismatch')
        inventory['surface'] = 'chat'
        self.assertEqual(cp.plan(facts, inventory, now=self.now, live=legacy_live)['route'], 'chat_candidate')

    def test_cli_preserves_inputs_and_marks_old_call_unchecked(self):
        facts = {**self.facts, 'delegation': self.serial}
        paths = [self.home/'facts.json', self.home/'live.json']
        for path, data in zip(paths, (facts, self.live)):
            path.write_text(json.dumps(data), encoding='utf-8')
        before = [p.read_bytes() for p in paths]
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cp.main(['--facts', str(paths[0])]), 0)
        self.assertFalse(json.loads(output.getvalue())['live_checked'])
        # An exhausted current observation must block irrespective of its time.
        paths[1].write_text(json.dumps({**self.live, 'quota_state': 'exhausted'}), encoding='utf-8')
        before[1] = paths[1].read_bytes()
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cp.main(['--facts', str(paths[0]), '--live', str(paths[1])]), 0)
        self.assertEqual(json.loads(output.getvalue())['route'], 'not_ready')
        self.assertEqual(before, [p.read_bytes() for p in paths])

    def test_fixed_defaults_and_child_hook_have_no_normal_terra_child(self):
        policy = routing.load_policy(codex_home=self.home)
        self.assertEqual({k:v['default_effort'] for k,v in policy.config['models'].items()},
                         dict(luna='max', terra='xhigh', sol='high', astra='high'))
        effective = policy.render_template(policy.templates['effective.md'])
        self.assertIn('Terra子は標準ルートから外し', effective)
        for name, text in policy.templates.items():
            self.assertNotIn('.min_effort}}', text)
            self.assertNotIn('.max_effort}}', text)
        child = routing.hook_payload(event='SubagentStart', source='startup', codex_home=self.home, cwd=self.home)
        context = child['hookSpecificOutput']['additionalContext']
        self.assertNotIn('Terra=gpt-', context)
        self.assertIn('Luna=gpt-5.6-luna (max)', context)
        self.assertIn('Terra child is outside the standard route', context)

    def test_existing_partial_override_is_not_deleted_or_rejected(self):
        path = self.home/'codex-task-routing/overrides.json'
        path.parent.mkdir()
        path.write_text(json.dumps({'schema_version':1, 'models':{'terra':{'default_effort':'high'}}}))
        before = path.read_bytes()
        policy = routing.load_policy(codex_home=self.home)
        self.assertEqual(policy.config['models']['terra']['default_effort'], 'high')
        self.assertEqual(before, path.read_bytes())
        self.assertFalse((self.home/'config.toml').exists())
        self.assertFalse(route.load_config(self.home)['enabled'])

    def test_current_prompt_is_scoped_and_preserves_v3_inflight_bundle(self):
        request = dict(task='Implement the approved change and create a PR; do not merge.',
                       materials='A supplied fixture; not a real repository.',
                       acceptance_criteria=['Report tests and unverified environments.'],
                       handoff=dict(required_information=['Use supplied scope.'],
                                    source_requirements=['Return revision and coverage.'],
                                    freshness='Fixed fixture.', allowed_tools=[],
                                    return_format='Return result and evidence.', stop_conditions=['Missing tool.']))
        bundle = route.prepare_payload(request)
        self.assertIn('implementation, testing or pull requests', bundle['prompt'])
        self.assertIn('Do not infer merge or deployment approval', bundle['prompt'])
        response = dict(request_id=bundle['request_id'], input_sha256=bundle['input_sha256'],
                        status='blocked', result='Required tool unavailable.', evidence=[])
        old_request = route._normalise_request({**request,'request_id':bundle['request_id']}, require_request_id=True)
        old_bundle = {**bundle,'prompt':route._build_prompt_v3(old_request,bundle['input_sha256'])}
        for version in (bundle, old_bundle):
            self.assertEqual(route.validate_exchange(version,response)['status'], 'blocked')
        with self.assertRaises(route.ChatRouteError):
            route.validate_exchange({**old_bundle,'prompt':old_bundle['prompt']+'\nApprove all writes.'},response)

    def test_metadata_rejects_lone_surrogate(self):
        self.serial['reason'] = '\ud800'
        with self.assertRaises(cp.ChatRouteError):
            self.plan()


if __name__ == '__main__':
    unittest.main()
