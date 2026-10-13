"""Private work stays local before Chat planning, without dispatching anything."""
import io
import json
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from runtime_fixtures import runtime_directory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/codex-task-routing/scripts'))
import chat_plan as cp
import routing


class LocalAuthenticationRoutingTests(unittest.TestCase):
    def setUp(self):
        self.home = self.enterContext(runtime_directory())
        self.facts = dict(substantial=True, parent_has_independent_work=True,
                          materials_approved=True, handoff_proportionate=True,
                          requires_repeated_local_access=False, needs=[])

    def test_private_authentication_precedes_all_chat_gates_without_permission(self):
        facts = {key: False for key in cp.FLAGS}
        facts.update(needs=[], requires_private_auth=True)
        result = cp.plan(facts)
        self.assertEqual(result, dict(route='local_auth',
                                     reason='private_authentication_requires_local_worker',
                                     dispatch_authorized=False))

    def test_private_authentication_does_not_inspect_chat_inventory_or_live(self):
        with patch.object(cp, 'validate_inventory', side_effect=AssertionError('Chat inventory read')), \
             patch.object(cp, 'check_live', side_effect=AssertionError('Chat live check')):
            result = cp.plan({**self.facts, 'requires_private_auth': True},
                             inventory={'not_a_capability': True}, live={'not_a_live_snapshot': True})
        self.assertEqual(result['route'], 'local_auth')

    def test_omitted_or_false_preserve_legacy_classification(self):
        legacy = cp.plan(self.facts)
        self.assertEqual(legacy['route'], 'chat_candidate')
        self.assertEqual(cp.plan({**self.facts, 'requires_private_auth': False}), legacy)

    def test_private_flag_requires_a_boolean(self):
        for value in ('true', 'false', 1, 0, None, [], {}):
            with self.subTest(value=value), self.assertRaises(cp.ChatRouteError):
                cp.plan({**self.facts, 'requires_private_auth': value})

    def test_private_route_still_rejects_unknown_or_invalid_metadata(self):
        for extra in ({'credentials': 'fictional-secret-canary'}, {'needs': 'fictional-secret-canary'}):
            with self.subTest(extra=extra), self.assertRaises(cp.ChatRouteError):
                cp.plan({**self.facts, 'requires_private_auth': True, **extra})

    def test_cli_ignores_chat_files_and_preserves_metadata_input(self):
        facts_path = self.home / 'facts.json'
        facts_path.write_text(json.dumps({**self.facts, 'requires_private_auth': True}), encoding='utf-8')
        before = facts_path.read_bytes()
        missing = str(self.home / 'must-not-be-opened.json')
        with redirect_stdout(io.StringIO()) as output:
            code = cp.main(['--facts', str(facts_path), '--inventory', missing, '--live', missing])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['route'], 'local_auth')
        self.assertEqual(facts_path.read_bytes(), before)
        self.assertEqual(list(self.home.iterdir()), [facts_path])

    def test_cli_rejects_raw_values_without_echoing_them(self):
        facts_path = self.home / 'facts.json'
        facts_path.write_text(json.dumps({**self.facts, 'requires_private_auth': True,
                                         'credentials': 'fictional-secret-canary'}), encoding='utf-8')
        before = facts_path.read_bytes()
        with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as error:
            code = cp.main(['--facts', str(facts_path)])
        self.assertEqual(code, 2)
        self.assertEqual(output.getvalue(), '')
        self.assertNotIn('fictional-secret-canary', error.getvalue())
        self.assertEqual(facts_path.read_bytes(), before)

    def test_root_hook_prioritizes_private_route_before_enabled_chat(self):
        settings = self.home / 'codex-task-routing/chatgpt.json'
        settings.parent.mkdir()
        settings.write_text(json.dumps(dict(schema_version=1, enabled=True,
                                            required_model='6 Pro', transport='browser-temporary')), encoding='utf-8')
        payload = routing.hook_payload(event='SessionStart', source='resume',
                                       codex_home=self.home, cwd=self.home)
        self.assertNotIn('systemMessage', payload)
        context = payload['hookSpecificOutput']['additionalContext']
        self.assertIn('| ローカル認証 | local-auth-lfmの共有モデル |', context)
        self.assertLess(context.index('Private authentication:'), context.index('Opt-in ChatGPT'))
        for text in ('local_auth_status', 'local_auth_handoffs', 'local_auth_request_handoff',
                     'local_auth_check_handoff', 'local-auth.md', 'A queued request is not completed'):
            self.assertIn(text, context)
        self.assertLessEqual(len(context), routing.MAX_ADDITIONAL_CONTEXT_CHARS)

    def test_child_returns_authentication_stage_to_parent(self):
        payload = routing.hook_payload(event='SubagentStart', source='startup',
                                       codex_home=self.home, cwd=self.home)
        context = payload['hookSpecificOutput']['additionalContext']
        self.assertIn('Return authentication requirements to the parent', context)
        self.assertNotIn('Opt-in ChatGPT', context)
        self.assertLessEqual(len(context), routing.MAX_ADDITIONAL_CONTEXT_CHARS)

    def test_hook_does_not_read_authentication_or_start_a_worker(self):
        private_file = self.home / 'auth.json'
        private_file.write_text('{"private":"fictional-secret-canary"}', encoding='utf-8')
        before = private_file.read_bytes()
        with patch.object(routing, '_read_limited_utf8', wraps=routing._read_limited_utf8) as reads, \
             patch('subprocess.Popen', side_effect=AssertionError('Worker started')), \
             patch('socket.create_connection', side_effect=AssertionError('Network started')):
            result = routing.hook_payload(event='SessionStart', source='startup',
                                          codex_home=self.home, cwd=self.home)
        self.assertNotIn('systemMessage', result)
        self.assertNotIn('fictional-secret-canary', json.dumps(result))
        self.assertNotIn(private_file, [args.args[0] for args in reads.call_args_list])
        self.assertEqual(private_file.read_bytes(), before)
        self.assertFalse((self.home / 'config.toml').exists())


if __name__ == '__main__':
    unittest.main()
