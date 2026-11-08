import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from runtime_fixtures import runtime_directory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/codex-task-routing/scripts'))
import haiku_route as route
import session_plan
from test_session_recommend import repository_unit


class HaikuRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = runtime_directory()
        self.root = self.temp.__enter__()
        self.addCleanup(self.temp.__exit__, None, None, None)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.state = self.root / 'state'
        self.state.mkdir()
        self.exe = self.root / ('claude.exe' if sys.platform == 'win32' else 'claude')
        self.exe.write_text('fake')
        self.prompt = '限定した実装を行う'
        self.request = dict(schema_version=1, enabled=True, actor='root', request_id='task-1',
            input_sha256=hashlib.sha256(self.prompt.encode()).hexdigest(), cwd=str(self.repo),
            branch='codex/test', head='a' * 40, tools=['Read', 'Glob', 'Grep', 'Edit'], bash_rules=[],
            max_turns=3, max_budget_usd=1.0, host_execution_allowed=True,
            target_operations_allowed=True, materials_transfer_allowed=True, official_endpoint_verified=True)
        self.env = {'PATH': 'allowed', 'ANTHROPIC_API_KEY': 'secret',
                    'ANTHROPIC_BASE_URL': 'https://evil.invalid', 'AWS_SECRET_ACCESS_KEY': 'secret2',
                    'CLAUDE_CODE_USE_BEDROCK': '1', 'CLAUDE_CODE_OAUTH_TOKEN': 'secret3'}

    def execute(self, runner, *, metadata_only=False):
        with patch.object(route, 'doctor', return_value={'cli': 'version_and_help_checked', 'max_turns_option': 'help_parser_accepted', 'repository': 'matches_clean'}):
            return route.run(self.request, self.prompt, self.state, self.exe, environ=self.env, runner=runner, metadata_only=metadata_only)

    def test_metadata_only_discards_all_model_output_and_keeps_claim(self):
        def fake(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, json.dumps({'subtype': 'success', 'is_error': False,
                'result': 'private model text', 'modelUsage': {route.MODEL: {}}, 'permission_denials': []}), 'private stderr')
        self.assertEqual(self.execute(fake, metadata_only=True)['status'], 'result_recorded')
        stored = json.loads((self.state / 'task-1.result.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['output_policy'], 'metadata_only')
        self.assertEqual(stored['completion'], 'reported')
        self.assertNotIn('result', stored)
        self.assertNotIn('private', json.dumps(stored))
        with self.assertRaises(route.RouteError):
            self.execute(fake, metadata_only=True)

    def test_metadata_only_rejects_literal_key_in_either_stream_and_never_resends(self):
        for stream in ('stdout', 'stderr'):
            self.request['request_id'] = stream
            calls = []
            def fake(argv, **kwargs):
                calls.append(1)
                response = json.dumps({'subtype':'success','is_error':False,'modelUsage':{route.MODEL:{}},'permission_denials':[]})
                return subprocess.CompletedProcess(argv, 0, response + ('secret' if stream=='stdout' else ''), 'secret' if stream=='stderr' else '')
            with self.subTest(stream=stream), self.assertRaises(route.RouteError):
                self.execute(fake, metadata_only=True)
            self.assertTrue((self.state / (stream+'.claim.json')).exists())
            self.assertFalse((self.state / (stream+'.result.json')).exists())
            self.assertEqual(json.loads((self.state / (stream+'.failure.json')).read_text())['category'], 'sensitive_output_rejected')
            with self.assertRaises(route.RouteError): self.execute(fake, metadata_only=True)
            self.assertEqual(len(calls), 1)

    def test_metadata_only_rejects_key_in_prompt_before_claim(self):
        self.prompt = 'secret'; self.request['input_sha256'] = hashlib.sha256(self.prompt.encode()).hexdigest()
        with self.assertRaises(route.RouteError): self.execute(lambda *a, **k: self.fail('must not send'), metadata_only=True)
        self.assertEqual(list(self.state.iterdir()), [])

    def test_success_claim_result_and_environment_boundary(self):
        calls = []
        def fake(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, json.dumps({'subtype': 'success', 'is_error': False, 'result': 'done',
                'modelUsage': {route.MODEL: {}}, 'permission_denials': []}), '')
        result = self.execute(fake)
        self.assertEqual(result['status'], 'result_recorded')
        self.assertFalse(result['accepted'])
        cmd, kwargs = calls[0]
        self.assertFalse(kwargs['shell'])
        self.assertEqual(kwargs['input'], self.prompt)
        self.assertEqual(kwargs['env']['ANTHROPIC_API_KEY'], 'secret')
        self.assertEqual(kwargs['env']['CLAUDE_CODE_MAX_TURNS'], '3')
        self.assertEqual(kwargs['env']['ANTHROPIC_BASE_URL'], 'https://api.anthropic.com')
        self.assertNotIn('AWS_SECRET_ACCESS_KEY', kwargs['env'])
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', kwargs['env'])
        self.assertTrue({'--bare', '--restricted', '--strict-mcp-config', '--disable-slash-commands', '--max-turns'} <= set(cmd))
        self.assertNotIn('--dangerously-skip-permissions', cmd)
        self.assertEqual(json.loads((self.state / 'task-1.result.json').read_text(encoding='utf-8'))['result']['result'], 'done')
        self.assertNotIn('secret', (self.state / 'task-1.claim.json').read_text(encoding='utf-8'))
        with self.assertRaises(route.RouteError):
            self.execute(fake)
        self.assertEqual(len(calls), 1)

    def test_failed_and_uncertain_execution_never_retries(self):
        def fake(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, '', 'sensitive error')
        with self.assertRaises(route.RouteError):
            self.execute(fake)
        self.assertEqual(json.loads((self.state / 'task-1.claim.json').read_text())['status'], 'outcome_unknown')
        failure = json.loads((self.state / 'task-1.failure.json').read_text())
        self.assertEqual(failure, {'schema_version': 1, 'category': 'nonzero_exit', 'returncode': 1})
        self.assertFalse((self.state / 'task-1.result.json').exists())
        with self.assertRaises(route.RouteError):
            self.execute(fake)

    def test_invalid_inputs_and_missing_permissions_fail_before_claim(self):
        for field, value in [('enabled', False), ('actor', 'child'), ('host_execution_allowed', False),
                             ('materials_transfer_allowed', False), ('target_operations_allowed', False),
                             ('official_endpoint_verified', False),
                             ('tools', ['Bash']), ('max_turns', 0), ('max_budget_usd', float('nan')),
                             ('input_sha256', '0' * 64), ('request_id', '../unsafe')]:
            with self.subTest(field=field):
                original = self.request[field]
                self.request[field] = value
                with self.assertRaises(route.RouteError):
                    self.execute(lambda *a, **k: self.fail('must not execute'))
                self.request[field] = original
        self.assertEqual(list(self.state.iterdir()), [])

    def test_missing_key_and_state_inside_repository_fail_before_claim(self):
        with patch.object(route, 'doctor', return_value={'cli': 'version_and_help_checked', 'max_turns_option': 'help_parser_accepted', 'repository': 'matches_clean'}):
            with self.assertRaises(route.RouteError):
                route.run(self.request, self.prompt, self.state, self.exe, environ={}, runner=lambda *a, **k: self.fail())
            with self.assertRaises(route.RouteError):
                route.run(self.request, self.prompt, self.repo, self.exe, environ=self.env, runner=lambda *a, **k: self.fail())
        self.assertFalse((self.state / 'task-1.claim.json').exists())
        self.assertFalse((self.repo / 'task-1.claim.json').exists())

    def test_cli_version_check_rejects_old_unknown_and_accepts_supported(self):
        for output, accepted in [('2.1.285 (Claude Code)', False), ('unknown', False),
                                 ('2.1.308 (Claude Code)', True)]:
            with self.subTest(output=output), patch.object(route.subprocess, 'run',
                return_value=subprocess.CompletedProcess([], 0, output, '')):
                if accepted:
                    route.verify_version(self.exe, self.env)
                else:
                    with self.assertRaises(route.RouteError):
                        route.verify_version(self.exe, self.env)

    def test_failed_local_preflight_stops_before_claim_or_send(self):
        checks = {'cli': 'version_and_help_checked', 'max_turns_option': 'help_parser_accepted', 'repository': 'matches_clean'}
        for field, value in [('cli', 'flags_missing'), ('max_turns_option', 'not_accepted'),
                             ('repository', 'unavailable_or_mismatch')]:
            with self.subTest(field=field), patch.object(route, 'doctor', return_value={**checks, field: value}):
                with self.assertRaises(route.RouteError):
                    route.run(self.request, self.prompt, self.state, self.exe, environ=self.env,
                              runner=lambda *args, **kwargs: self.fail('must not send'))
                self.assertFalse((self.state / 'task-1.claim.json').exists())

    def test_timeout_and_malformed_response_preserve_unknown_claim(self):
        for case in ('timeout', 'malformed'):
            self.request['request_id'] = case
            def fake(argv, **kwargs):
                if case == 'timeout':
                    raise subprocess.TimeoutExpired(argv, 900)
                return subprocess.CompletedProcess(argv, 0, '{', '')
            with self.subTest(case=case), self.assertRaises(route.RouteError):
                self.execute(fake)
            meta = json.loads((self.state / (case + '.failure.json')).read_text())
            self.assertEqual(meta['category'], 'timeout' if case == 'timeout' else 'invalid_result')
            self.assertTrue((self.state / (case + '.claim.json')).exists())

    def test_model_and_permission_denials_are_not_treated_as_completed(self):
        def fake(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, json.dumps({'subtype': 'success', 'is_error': False,
                'modelUsage': {'other-model': {}}, 'permission_denials': [{'tool_name': 'Bash'}]}), '')
        result = self.execute(fake)
        self.assertEqual(result['status'], 'partial_or_unverified')
        saved = json.loads((self.state / 'task-1.result.json').read_text(encoding='utf-8'))
        self.assertFalse(saved['model_verified'])
        self.assertIsNone(saved['observed_effort'])

    def test_plan_is_not_authorization_and_disabled_by_default(self):
        self.request['enabled'] = False
        result = route.plan(self.request)
        self.assertFalse(result['execution_authorized'])
        self.assertIn('enabled', result['gaps'])

    def test_bash_requires_scoped_rules_and_never_grants_bare_bash(self):
        self.request['tools'].append('Bash')
        with self.assertRaises(route.RouteError):
            route.plan(self.request)
        for rule in ('Bash(*)', 'Bash(python *; curl example)', 'Bash(python $(command))'):
            self.request['bash_rules'] = [rule]
            with self.assertRaises(route.RouteError):
                route.plan(self.request)
        self.request['bash_rules'] = ['Bash(python -m unittest *)']
        route.plan(self.request)
        cmd = route.command(self.exe, self.request)
        self.assertIn('Bash(python -m unittest *)', cmd[cmd.index('--allowedTools') + 1])
        self.assertNotIn(',Bash,', ',' + cmd[cmd.index('--allowedTools') + 1] + ',')

    def test_git_identity_and_dirty_worktree_rejected(self):
        for values in ([str(self.repo), 'other', 'a' * 40, ''],
                       [str(self.repo), 'codex/test', 'b' * 40, ''],
                       [str(self.repo), 'codex/test', 'a' * 40, ' M file']):
            with self.subTest(values=values), patch.object(route, '_git', side_effect=values):
                with self.assertRaises(route.RouteError):
                    route.verify_repository(self.request)

    def test_recommendation_requires_explicit_eligible_route(self):
        facts = repository_unit()
        facts['execution']['cloud']['available'] = False
        self.assertEqual(session_plan.recommend_session(facts)['route'], 'new_local_session')
        facts['work']['parent_only'] = False
        facts['execution']['haiku'] = dict(enabled=True, can_complete=True, root_actor=True, text_only_suitable=True, adapter_ready=True, target_authorized=True, materials_authorized=True, delegation_conditions_met=True)
        facts['host']['haiku_api'] = True
        self.assertEqual(session_plan.recommend_session(facts)['route'], 'haiku_api')
        facts['host']['haiku_api'] = None
        self.assertEqual(session_plan.recommend_session(facts)['route'], 'new_local_session')
        facts['host']['haiku_api'] = True
        facts['execution']['cloud']['available'] = True
        self.assertEqual(session_plan.recommend_session(facts)['route'], 'codex_cloud')


if __name__ == '__main__':
    unittest.main()
