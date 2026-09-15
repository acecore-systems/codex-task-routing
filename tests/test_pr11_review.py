"""PR #11 regressions: round-trippable bundles and safe first-use capabilities."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/codex-task-routing/scripts'
sys.path.insert(0, str(SCRIPTS))
import chat_plan as plan
import chat_transfer as transfer
import chatgpt_route as route


class PreparedBundleSizeTests(unittest.TestCase):
    def setUp(self):
        # macOS's system temporary directory has a symlink ancestor. Keep test
        # files under the checkout to exercise the same safe-path rules on all OSes.
        root = Path(__file__).resolve().parent / '.tmp-runtime-tests'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.request = dict(request_id='00000000-0000-4000-8000-000000000011',
                            task='Review supplied code.', materials='Fixture.',
                            acceptance_criteria=['Report findings.'])

    def test_prepare_rejects_expanded_bundle_before_returning_it(self):
        for material in ('"' * 120000, '漢' * 86500):
            request = {**self.request, 'materials': material}
            raw = json.dumps(request, ensure_ascii=False).encode('utf-8')
            self.assertLess(len(raw), route.MAX_JSON_BYTES)
            with self.subTest(character=material[0]), self.assertRaisesRegex(
                    route.ChatRouteError, 'prepared bundle exceeds the size limit'):
                route.prepare_payload(request)

    def test_cli_rejection_has_no_partial_bundle_or_source_mutation(self):
        request = {**self.request, 'materials': '"' * 120000}
        path = self.home / 'request.json'
        path.write_text(json.dumps(request), encoding='utf-8')
        original = path.read_bytes()
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            status = route.main(['prepare', '--input', str(path)])
        self.assertEqual(status, 2)
        self.assertEqual(output.getvalue(), '')
        self.assertIn('prepared bundle exceeds the size limit', error.getvalue())
        self.assertEqual(original, path.read_bytes())

    def test_largest_quote_bundle_roundtrips_through_cli_and_transfer(self):
        sample = route.prepare_payload({**self.request, 'materials': '"'})
        serialized_size = len((json.dumps(sample, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8'))
        count = 1 + (route.MAX_JSON_BYTES - serialized_size) // 4
        request = {**self.request, 'materials': '"' * count}
        source = self.home / 'request.json'
        source.write_text(json.dumps(request), encoding='utf-8')
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(route.main(['prepare', '--input', str(source)]), 0)
        encoded = output.getvalue().encode('utf-8')
        self.assertLessEqual(len(encoded), route.MAX_JSON_BYTES)
        self.assertLess(route.MAX_JSON_BYTES - len(encoded), 4)
        bundle_path = self.home / 'bundle.json'
        bundle_path.write_bytes(encoded)
        state = transfer.TransferState.from_paths(bundle_path, self.home / 'reply.json')
        self.assertEqual(state.request_id, self.request['request_id'])
        reply = dict(request_id=state.request_id, input_sha256=state.input_sha256,
                     status='completed', result='Review complete.', evidence=[])
        self.assertTrue(state.save_response(reply)[1])
        with self.assertRaises(route.ChatRouteError):
            route.prepare_payload({**request, 'materials': '"' * (count + 1)})


class FirstUseCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
        self.need = dict(tool='GitHub', operation='create_pull_request', scope='example/project')
        self.capability = dict(**self.need, state='permission_checked',
                               permission_evidence='Fixture: operation schema and repository push permission checked; no trial write.',
                               observed_at=self.now.isoformat(),
                               expires_at=(self.now + timedelta(days=1)).isoformat())
        self.inventory = dict(schema_version=1, surface='chat-temporary', model='6 Pro',
                              installed_plugins=['GitHub'], capabilities=[self.capability])
        self.facts = dict(substantial=True, parent_has_independent_work=True,
                          materials_approved=True, handoff_proportionate=True,
                          requires_repeated_local_access=False, needs=[self.need])
        self.live = dict(is_root=True, route_enabled=True, surface='chat-temporary', model='6 Pro',
                         transport='browser-temporary', transport_authorized=True,
                         actions_authorized=True, quota_state='no_limit_notice',
                         observed_at=self.now.isoformat())

    def evaluate(self, **changes):
        return plan.plan(self.facts, self.inventory, now=self.now,
                         live={**self.live, **changes})

    def test_first_authorized_operation_is_not_claimed_executed(self):
        before = copy.deepcopy(self.inventory)
        result = self.evaluate()
        self.assertEqual(result['route'], 'chat_candidate')
        self.assertTrue(result['live_checked'])
        self.assertEqual(result['checks'][0]['state'], 'permission_checked')
        self.assertFalse(result['checks'][0]['execution_verified'])
        self.assertEqual(self.inventory, before)

    def test_permission_check_requires_scoped_evidence(self):
        for evidence in (None, '', 'x' * 513, '\ud800', False):
            cap = dict(self.capability)
            if evidence is None:
                del cap['permission_evidence']
            else:
                cap['permission_evidence'] = evidence
            with self.subTest(evidence=repr(evidence)), self.assertRaises(route.ChatRouteError):
                plan.validate_inventory({**self.inventory, 'capabilities': [cap]})

    def test_no_fresh_live_check_means_no_first_use_candidate(self):
        result = plan.plan(self.facts, self.inventory, now=self.now)
        self.assertEqual(result['route'], 'preflight_needed')
        for changes in ({'actions_authorized': False}, {'transport_authorized': False},
                        {'is_root': False}, {'route_enabled': False}, {'model': 'other'},
                        {'quota_state': 'exhausted'}, {'quota_state': 'unknown'},
                        {'observed_at': (self.now - timedelta(minutes=3)).isoformat()}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.evaluate(**changes)['route'], 'chat_candidate')

    def test_advertised_and_executed_states_remain_distinct(self):
        self.capability['state'] = 'advertised'
        self.assertEqual(self.evaluate()['route'], 'preflight_needed')
        self.capability['state'] = 'verified'
        result = self.evaluate()
        self.assertEqual(result['route'], 'chat_candidate')
        self.assertTrue(result['checks'][0]['execution_verified'])
        self.capability['state'] = 'blocked'
        self.assertEqual(self.evaluate()['route'], 'not_ready')

    def test_first_use_cannot_cross_operation_scope_surface_or_expiry(self):
        for key, value in [('operation', 'merge_pull_request'), ('scope', 'other/project')]:
            self.facts['needs'] = [{**self.need, key: value}]
            with self.subTest(key=key):
                self.assertEqual(self.evaluate()['route'], 'preflight_needed')
        self.facts['needs'] = [self.need]
        self.assertEqual(self.evaluate(surface='chat', transport='codex-app-tools')['reason'],
                         'capability_surface_mismatch')
        self.capability['expires_at'] = self.now.isoformat()
        self.capability['observed_at'] = (self.now - timedelta(hours=1)).isoformat()
        self.assertEqual(self.evaluate()['route'], 'preflight_needed')


if __name__ == '__main__':
    unittest.main()
