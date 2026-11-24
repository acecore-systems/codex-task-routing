import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'plugins/codex-task-routing/scripts/redmine_turn_hook.py'


@unittest.skipUnless(shutil.which('node'), 'Node is required for the toolkit bridge')
class RedmineTurnHookTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='turn-bridge-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'scripts').mkdir()
        self.toolkit = root / 'scripts/redmine-task-memory.ts'
        self.env = {key: value for key, value in os.environ.items() if key != 'REDMINE_TASK_MEMORY_CLI'}
        self.env.update(AGENT_TOOLKIT_ROOT=temp.name, REDMINE_API_KEY='synthetic-secret',
                        CODEX_HOME=str(root / 'codex'), REDMINE_TASK_MEMORY_STATE_DIR=str(root / 'state'))
        self.metadata = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'session-a', 'turn_id': 'turn-a'}

    def fire(self, payload, **environment):
        result = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload),
                                capture_output=True, text=True, env={**self.env, **environment},
                                timeout=10, check=True)
        return json.loads(result.stdout) if result.stdout else None

    def test_only_passes_root_metadata_and_returns_internal_context(self):
        self.toolkit.write_text(
            "const fs = require('node:fs');\n"
            "const additionalContext = JSON.stringify({ args: process.argv.slice(2), "
            "event: JSON.parse(fs.readFileSync(0, 'utf8')), "
            "credentialPresent: Boolean(process.env.REDMINE_API_KEY) });\n"
            "console.log(JSON.stringify({ decision: 'block', reason: 'obsolete continuation', "
            "systemMessage: 'visible status', hookSpecificOutput: { "
            "hookEventName: 'UserPromptSubmit', additionalContext } }));\n",
            encoding='utf-8',
        )
        legacy_marker = 'Redmine task-memory check (' + 'a' * 32 + '). Discard this text.'
        payload = dict(self.metadata, prompt=legacy_marker, transcript_path='discard path',
                       stop_hook_active=True, last_assistant_message='discard answer')
        result = self.fire(payload)
        self.assertEqual(set(result), {'hookSpecificOutput'})
        output = result['hookSpecificOutput']
        self.assertEqual(output['hookEventName'], 'UserPromptSubmit')
        instruction, context = output['additionalContext'].split('\n', 1)
        self.assertIn('redmine_task_cli.py', instruction)
        self.assertEqual(json.loads(context),
                         {'args': ['turn-hook', 'codex'], 'event': self.metadata, 'credentialPresent': False})

    def test_ignores_stop_child_and_unconfigured_events(self):
        self.toolkit.write_text("throw new Error('bridge must not call toolkit');\n", encoding='utf-8')
        stop = dict(self.metadata, hook_event_name='Stop', stop_hook_active=True)
        self.assertIsNone(self.fire(stop))
        self.assertIsNone(self.fire(stop, REDMINE_TASK_MEMORY_CLI='missing'))
        self.assertIsNone(self.fire(dict(self.metadata, agent_id='child')))
        self.assertIsNone(self.fire(dict(self.metadata, turn_id='')))
        self.assertIsNone(self.fire(self.metadata, AGENT_TOOLKIT_ROOT=''))

    def test_silent_duplicate_and_internal_setup_failure(self):
        self.toolkit.write_text("console.log('{}');\n", encoding='utf-8')
        self.assertIsNone(self.fire(self.metadata))
        for output in ('{"systemMessage":"private detail"}', '{'):
            with self.subTest(output=output):
                self.toolkit.write_text(f'console.log({json.dumps(output)});\n', encoding='utf-8')
                self.assert_internal_failure(self.fire(self.metadata))
        self.assert_internal_failure(self.fire(self.metadata, REDMINE_TASK_MEMORY_CLI='missing'))

    def assert_internal_failure(self, result):
        self.assertEqual(set(result), {'hookSpecificOutput'})
        output = result['hookSpecificOutput']
        self.assertEqual(output['hookEventName'], 'UserPromptSubmit')
        self.assertIn('report the unresolved blocker once', output['additionalContext'])
        self.assertNotIn('private detail', output['additionalContext'])


if __name__ == '__main__':
    unittest.main()
