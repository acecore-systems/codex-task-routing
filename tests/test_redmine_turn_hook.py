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
    def test_uses_legacy_toolkit_root_and_only_forwards_root_metadata(self):
        with tempfile.TemporaryDirectory(prefix='turn-bridge-') as name:
            root = Path(name)
            (root / 'scripts').mkdir()
            (root / 'scripts/redmine-task-memory.ts').write_text(
                "const fs = require('node:fs');\n"
                "console.log(JSON.stringify({ args: process.argv.slice(2), "
                "event: JSON.parse(fs.readFileSync(0, 'utf8')), "
                "credentialPresent: Boolean(process.env.REDMINE_API_KEY) }));\n",
                encoding='utf-8',
            )
            env = {key: value for key, value in os.environ.items() if key != 'REDMINE_TASK_MEMORY_CLI'}
            env.update(AGENT_TOOLKIT_ROOT=name, REDMINE_API_KEY='synthetic-secret')
            payload = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'session-a', 'turn_id': 'turn-a',
                       'prompt': 'discard prompt', 'transcript_path': 'discard path', 'last_assistant_message': 'discard answer'}

            def fire(message, settings=env):
                result = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(message),
                                        capture_output=True, text=True, env=settings, timeout=10, check=True)
                return json.loads(result.stdout) if result.stdout else None

            expected = {key: payload[key] for key in ('hook_event_name', 'session_id', 'turn_id')}
            self.assertEqual(fire(payload), {'args': ['turn-hook', 'codex'], 'event': expected, 'credentialPresent': False})
            token = 'a' * 32
            continued = fire(dict(payload, prompt=f'Redmine task-memory check ({token}). Discard remaining text.'))
            self.assertEqual(continued['event'], {**expected, 'continuation_token': token})
            self.assertIsNone(fire(dict(payload, agent_id='child')))
            self.assertIsNone(fire(payload, {**env, 'AGENT_TOOLKIT_ROOT': ''}))


if __name__ == '__main__':
    unittest.main()
