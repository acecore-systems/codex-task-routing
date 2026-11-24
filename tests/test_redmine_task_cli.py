import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'plugins/codex-task-routing/scripts/redmine_task_cli.py'


@unittest.skipUnless(shutil.which('node') and shutil.which('git'), 'Node and Git are required')
class RedmineTaskCliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='redmine-task-cli-')
        self.addCleanup(temp.cleanup)
        self.worktree = Path(temp.name)
        self.fixture = self.worktree / 'redmine-task-memory.ts'
        self.fixture.write_text(
            "const fs = require('node:fs');\n"
            "const [operation, reference] = process.argv.slice(2);\n"
            "const data = operation === 'resume' ? null : fs.readFileSync(0, 'utf8');\n"
            "const project = process.env.REDMINE_TASK_MEMORY_PROJECT;\n"
            "fs.appendFileSync('calls', JSON.stringify({ operation, reference, data, project }) + '\\n');\n"
            "const status = process.env.SYNTHETIC_STATUS || (project ? 'resumed' : 'configuration_required');\n"
            "const ok = status === 'resumed';\n"
            "console.log(JSON.stringify({ ok, status }));\n"
            "process.exitCode = ok ? 0 : 1;\n",
            encoding='utf-8',
        )
        self.env = {key: value for key, value in os.environ.items()
                    if not key.upper().startswith(('REDMINE_', 'GIT_'))}
        self.env.update(REDMINE_TASK_MEMORY_CLI=str(self.fixture),
                        REDMINE_URL='https://example.invalid/redmine/',
                        REDMINE_TASK_MEMORY_PROJECTS=json.dumps(
                            {'example-org/example.repo': 'current-project', 'other/repo': 'other-project'}
                        ))

        self.git('init', '-q')
        self.git('remote', 'add', 'origin', 'git@github.com:Example-Org/Example.Repo.git')

    def git(self, *args):
        result = subprocess.run(['git', *args], cwd=self.worktree, env=self.env, capture_output=True,
                                text=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def call(self, operation, reference, payload=None, **environment):
        calls = self.worktree / 'calls'
        calls.unlink(missing_ok=True)
        result = subprocess.run([sys.executable, str(SCRIPT), operation, reference],
                                input=json.dumps(payload) if payload else '', encoding='utf-8',
                                env={**self.env, **environment}, cwd=self.worktree,
                                capture_output=True, timeout=12, check=False)
        return result.returncode, json.loads(result.stdout)['status'], [
            json.loads(line) for line in calls.read_text(encoding='utf-8').splitlines()
        ]

    def test_worktree_mapping_is_used_for_direct_issue_and_turn_operations(self):
        for operation, reference, payload in (
            ('resume', '42', None),
            ('update', 'https://example.invalid/redmine/issues/42', {'objective': 'test'}),
            ('turn', 'handle', {'action': 'attach', 'reference': '#42'}),
            ('turn', 'handle', {'action': 'resume'}),
            ('turn', 'handle', {'action': 'recover'}),
            ('turn', 'handle', {'action': 'record', 'summary': {'objective': 'test'}}),
        ):
            with self.subTest(operation=operation, payload=payload):
                code, status, calls = self.call(operation, reference, payload)
                self.assertEqual((code, status), (0, 'resumed'))
                self.assertEqual([call.get('project') for call in calls], [None, 'current-project'])
                self.assertEqual(calls[0]['data'], calls[1]['data'])

    def test_explicit_or_successful_native_mapping_and_other_errors_are_preserved(self):
        code, status, calls = self.call('resume', '42', REDMINE_TASK_MEMORY_PROJECT='explicit')
        self.assertEqual((code, status), (0, 'resumed'))
        self.assertEqual([call.get('project') for call in calls], ['explicit'])
        for status in ('resumed', 'update_unverified', 'issue_scope_mismatch', 'invalid_configuration'):
            with self.subTest(status=status):
                code, actual, calls = self.call('turn', 'handle', {'action': 'record'}, SYNTHETIC_STATUS=status)
                self.assertEqual((code, actual), (0 if status == 'resumed' else 1, status))
                self.assertEqual(len(calls), 1)

    def test_wrong_or_ambiguous_repository_never_supplies_a_project(self):
        for remote in ('https://not-github.invalid/Example-Org/Example.Repo.git',
                       'https://github.com/unknown/repo.git',
                       'https://user@github.com/Example-Org/Example.Repo.git'):
            with self.subTest(remote=remote):
                self.git('remote', 'set-url', 'origin', remote)
                code, status, calls = self.call('resume', '42')
                self.assertEqual((code, status, len(calls)), (1, 'configuration_required', 1))
        self.git('remote', 'set-url', 'origin', 'https://github.com/Example-Org/Example.Repo.git')
        self.git('config', '--add', 'remote.origin.url', 'https://github.com/other/repo.git')
        code, status, calls = self.call('resume', '42')
        self.assertEqual((code, status, len(calls)), (1, 'configuration_required', 1))

    def test_github_reference_and_standalone_skip_do_not_use_origin(self):
        for operation, reference, payload in (
            ('resume', 'https://github.com/other/repo/issues/42', None),
            ('resume', 'https://other.invalid/redmine/issues/42', None),
            ('turn', 'handle', {'action': 'attach', 'reference': 'https://github.com/other/repo/issues/42'}),
            ('turn', 'handle', {'action': 'skip'}),
        ):
            with self.subTest(operation=operation, payload=payload):
                code, status, calls = self.call(operation, reference, payload)
                self.assertEqual((code, status, len(calls)), (1, 'configuration_required', 1))


if __name__ == '__main__':
    unittest.main()
