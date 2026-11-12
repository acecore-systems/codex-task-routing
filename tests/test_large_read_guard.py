"""Synthetic tests for the non-blocking large-read reminder; these do not establish live host delivery."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/codex-task-routing'
SCRIPT = PLUGIN / 'scripts/large_read_guard.py'
spec = importlib.util.spec_from_file_location('large_read_guard_test', SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

SHIPPED_COMMAND = ("python -c \"import os,runpy;runpy.run_path(os.path.join(os.environ['PLUGIN_ROOT'],"
                   "'scripts','large_read_guard.py'),run_name='__main__')\"")
EXPECTED_REMINDER = (
    'Task Routing: big.log is 105KB and this command would load the whole file into the parent context, '
    'which makes every later turn more expensive. Consider reading only a range (head / tail / sed -n, '
    'or Get-Content -TotalCount / -Tail), filtering it (rg / Select-String), or passing the file path to a '
    'suitable delegated owner and receiving a summary. This is non-blocking; a deliberate full read, '
    'user instructions and host restrictions remain valid.'
)


class LargeReadGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix='large-read-guard-')).resolve()
        self.other = Path(tempfile.mkdtemp(prefix='large-read-guard-other-')).resolve()
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.addCleanup(shutil.rmtree, self.other, True)
        (self.directory / 'big.log').write_bytes(b'SENTINEL-CONTENT\n' + b'x' * (105 * 1024 - 17))
        (self.directory / 'small.txt').write_bytes(b'x' * 1024)
        (self.directory / '-x.log').write_bytes(b'x' * (105 * 1024))

    def event(self, tool_input, tool_name='Bash', **changes):
        value = {'hook_event_name': 'PreToolUse', 'session_id': 'session-a', 'turn_id': 'turn-a',
                 'tool_use_id': 'call-a', 'tool_name': tool_name, 'cwd': str(self.directory),
                 'tool_input': tool_input}
        value.update(changes)
        return value

    def context(self, command, **changes):
        result = guard.analyze(self.event({'command': command}, **changes))
        return None if result is None else result['hookSpecificOutput']['additionalContext']

    def sized_payload(self, size):
        base = json.dumps(self.event({'command': 'cat big.log'}, pad=''))
        padded = base.replace('"pad": ""', '"pad": "' + 'a' * (size - len(base)) + '"')
        self.assertEqual(len(padded.encode('utf-8')), size)
        return padded.encode('utf-8')

    def run_script(self, data, environment_extra=None):
        environment = {**os.environ, 'CODEX_HOME': str(self.other), **(environment_extra or {})}
        return subprocess.run([sys.executable, str(SCRIPT)], input=data, capture_output=True,
                              env=environment, timeout=20)

    def test_whole_file_reads_in_shell_forms_get_a_reminder(self):
        commands = [
            'cat big.log', 'cat ./big.log', 'cat -n big.log', 'cat "big.log"', f'cat {self.directory / "big.log"}',
            'cat small.txt big.log', 'echo a; cat big.log', 'echo start && cat big.log', 'cat big.log 2>&1',
            'bat -l log big.log', 'Get-Content -Encoding UTF8 big.log', 'Get-Content -Raw -Encoding UTF8 big.log',
            'Get-Content -Path big.log', 'Get-Content -Encoding:UTF8 big.log', 'type big.log', 'more big.log',
            'less big.log', 'gc big.log', 'cat.exe big.log',
        ]
        for command in commands:
            with self.subTest(command=command):
                context = self.context(command)
                self.assertIsNotNone(context)
                self.assertIn('big.log is 105KB', context)

    def test_ranges_pipes_redirects_small_missing_and_unknown_forms_stay_silent(self):
        commands = [
            'cat big.log | head -n 5', 'cat nonexist.log', 'cat small.txt', 'cat big.log > out.txt',
            'bat -r 1:10 big.log', 'bat --line-range=1:10 big.log', 'Get-Content -Encoding big.log',
            'Get-Content -Filter big.log small.txt', 'Get-Content -Encoding UTF8 -TotalCount 5 big.log',
            'Get-Content -Head 5 big.log', 'Get-Content -Tail 5 big.log', 'cat -x.log', 'F=big.log; cat $F',
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(self.context(command))

    def test_only_the_parent_shell_calls_are_checked(self):
        tool_input = {'command': 'cat big.log'}
        self.assertIsNotNone(guard.analyze(self.event(tool_input, agent_id='')))
        self.assertIsNone(guard.analyze(self.event(tool_input, agent_id='child')))
        self.assertIsNone(guard.analyze(self.event(tool_input, tool_name='apply_patch')))
        self.assertIsNone(guard.analyze(self.event(tool_input, tool_name='Read')))
        self.assertIsNone(guard.analyze(self.event(tool_input, hook_event_name='UserPromptSubmit')))
        self.assertIsNone(guard.analyze(self.event(tool_input, hook_event_name=None)))

    def test_tool_names_are_normalized_like_parent_guard(self):
        for name in ('Bash', 'exec', 'exec_command', 'functions.exec_command', 'functions__exec', 'mcp__host__exec'):
            with self.subTest(tool_name=name):
                self.assertIsNotNone(guard.analyze(self.event({'cmd': 'cat big.log'}, tool_name=name)))

    def test_command_shapes_and_working_directory(self):
        shapes = [
            {'command': ['bash', '-lc', 'cat big.log']},
            {'command': ['cat', 'big.log']},
            {'command': ['pwsh', '-NoProfile', '-Command', 'Get-Content big.log']},
            {'cmd': 'cat big.log'},
        ]
        for tool_input in shapes:
            with self.subTest(tool_input=tool_input):
                self.assertIsNotNone(guard.analyze(self.event(tool_input)))
        # Without a payload cwd, tool_input.workdir or tool_input.cwd names the directory.
        for tool_input in ({'cmd': 'cat big.log', 'workdir': str(self.directory)},
                           {'cmd': 'cat big.log', 'cwd': str(self.directory)}):
            with self.subTest(tool_input=tool_input):
                self.assertIsNotNone(guard.analyze(self.event(tool_input, cwd=None)))
                self.assertIsNone(guard.analyze(self.event(tool_input, cwd=None, agent_id='child')))
        # The payload cwd is preferred to tool_input.workdir.
        self.assertIsNone(guard.analyze(self.event({'cmd': 'cat big.log', 'workdir': str(self.directory)},
                                                   cwd=str(self.other))))
        # command takes precedence over cmd; a shell script with a range option stays silent.
        self.assertIsNone(guard.analyze(self.event({'command': 'cat small.txt', 'cmd': 'cat big.log'})))
        self.assertIsNone(guard.analyze(self.event({'command': ['bash', '-lc', 'cat big.log | head -n 5']})))
        self.assertIsNone(guard.analyze(self.event({'command': ['cat', 5]})))
        self.assertIsNone(guard.analyze(self.event({'command': []})))

    def test_malformed_or_oversized_input_is_silent_in_the_script(self):
        base = self.event({'command': 'cat big.log'})
        deep = ('{"hook_event_name":"PreToolUse","tool_name":"exec_command","tool_input":'
                + '[' * 5000 + '"private-sentinel"' + ']' * 5000 + '}')
        cases = {
            'not json': b'cat big.log',
            'truncated json': json.dumps(base).encode('utf-8')[:-5],
            'empty': b'',
            'not an object': b'[]',
            'deep nesting': deep.encode('utf-8'),
            'tool_input is a string': json.dumps({**base, 'tool_input': 'cat big.log'}).encode('utf-8'),
            'duplicate keys': (b'{"hook_event_name":"PreToolUse","hook_event_name":"PreToolUse",'
                               b'"tool_name":"Bash","tool_input":{"command":"cat big.log"}}'),
            'over the input limit': self.sized_payload(guard.INPUT_LIMIT + 1),
        }
        for label, data in cases.items():
            with self.subTest(case=label):
                result = self.run_script(data)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, b'')
                self.assertEqual(result.stderr, b'')
        self.assertEqual(list(self.other.iterdir()), [])

    def test_input_at_the_limit_is_still_checked(self):
        result = self.run_script(self.sized_payload(guard.INPUT_LIMIT))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('big.log is 105KB', json.loads(result.stdout)['hookSpecificOutput']['additionalContext'])

    def test_reminder_names_only_the_base_name_and_size(self):
        result = guard.analyze(self.event({'command': f'cat {self.directory / "big.log"}'}))
        self.assertEqual(set(result), {'hookSpecificOutput'})
        self.assertEqual(set(result['hookSpecificOutput']), {'hookEventName', 'additionalContext'})
        self.assertEqual(result['hookSpecificOutput']['hookEventName'], 'PreToolUse')
        context = result['hookSpecificOutput']['additionalContext']
        self.assertEqual(context, EXPECTED_REMINDER)
        self.assertNotIn(str(self.directory), context)
        self.assertNotIn(self.directory.name, context)
        self.assertNotIn('SENTINEL', context)
        self.assertNotIn('permissionDecision', json.dumps(result))

    def test_shipped_command_turns_stdin_into_the_reminder_on_stdout(self):
        hooks = json.loads((PLUGIN / 'hooks/hooks.json').read_text(encoding='utf-8'))['hooks']
        command = hooks['PreToolUse'][2]['hooks'][0]['command']
        environment = {**os.environ, 'PLUGIN_ROOT': str(PLUGIN), 'CODEX_HOME': str(self.other),
                       'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')}
        # The shell starts in another directory; the file is found through the payload cwd.
        result = subprocess.run(command, shell=True, cwd=self.other, env=environment, timeout=20,
                                input=json.dumps(self.event({'cmd': 'cat big.log'})).encode('utf-8'),
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b'')
        payload = json.loads(result.stdout)
        self.assertEqual(payload['hookSpecificOutput']['additionalContext'], EXPECTED_REMINDER)
        child = self.run_script(json.dumps(self.event({'command': 'cat big.log'}, agent_id='child')).encode('utf-8'))
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout, b'')


class LargeReadHooksRegistrationTests(unittest.TestCase):
    def test_reminder_is_a_separate_pretooluse_group_with_the_shipped_command(self):
        hooks = json.loads((PLUGIN / 'hooks/hooks.json').read_text(encoding='utf-8'))['hooks']
        group = hooks['PreToolUse'][2]
        self.assertEqual(group['matcher'], '.*(Bash|exec|exec_command)$')
        self.assertEqual(group['hooks'], [{
            'type': 'command', 'command': SHIPPED_COMMAND, 'timeout': 5,
            'statusMessage': 'Checking large file read',
        }])
        for name in ('Bash', 'exec', 'exec_command', 'functions.exec_command'):
            with self.subTest(tool_name=name):
                self.assertIsNotNone(re.fullmatch(group['matcher'], name))
        self.assertIsNone(re.fullmatch(group['matcher'], 'apply_patch'))


if __name__ == '__main__':
    unittest.main()
