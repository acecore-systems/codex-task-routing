"""Synthetic hook tests; these do not establish live host delivery."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'plugins/codex-task-routing/scripts/parent_guard.py'
spec = importlib.util.spec_from_file_location('parent_guard_test', SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

class ParentGuardTests(unittest.TestCase):
    def setUp(self):
        self.home = ROOT / 'tests/.tmp-runtime-tests' / ('parent-guard-' + uuid.uuid4().hex)
        self.home.mkdir(parents=True)
    def tearDown(self):
        assert self.home.parent == ROOT / 'tests/.tmp-runtime-tests'
        shutil.rmtree(self.home)
    def event(self, kind, **kwargs):
        value = {'hook_event_name': kind, 'session_id': 'session-a', 'turn_id': 'turn-a'}
        value.update(kwargs)
        return value
    def send(self, kind, **kwargs):
        return guard.handle(self.event(kind, **kwargs), self.home)
    def work(self, number, **kwargs):
        return self.send('PreToolUse', tool_name='functions.exec', tool_use_id=str(number), **kwargs)

    def test_prompt_reminder_once_per_root_turn_without_prompt_content(self):
        result = self.send('UserPromptSubmit', prompt='private-user-prompt')
        self.assertEqual(result['hookSpecificOutput']['hookEventName'], 'UserPromptSubmit')
        context = result['hookSpecificOutput']['additionalContext']
        for phrase in ('substantial work', 'acceptance criteria', 'not the request label', 'one suitable execution owner',
                       'failure diagnosis', 'separate pure evidence', 'effective policy',
                       'internal coupling and parent familiarity', 'Continue a suitable delegated owner',
                       'answer short questions normally', 'host permission', 'custom policy',
                       'same suitable resumable owner', 'Do not invent work or force delegation'):
            self.assertIn(phrase, context)
        self.assertIsNone(self.send('UserPromptSubmit', prompt='different-private-prompt'))
        self.assertIsNone(self.send('UserPromptSubmit', turn_id='other', agent_id='child'))
        self.assertIsNone(self.send('UserPromptSubmit', turn_id=''))
        self.assertIsNotNone(self.send('UserPromptSubmit', turn_id='other'))
        content = (self.home / 'codex-task-routing/parent-guard/state.sqlite3').read_bytes()
        self.assertNotIn(b'private-user-prompt', content)
        self.assertNotIn(b'different-private-prompt', content)

    def test_shipped_prompt_command_outputs_official_context_shape(self):
        env = dict(os.environ, CODEX_HOME=str(self.home))
        payload = self.event('UserPromptSubmit', prompt='private-prompt')
        result = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload),
                                text=True, capture_output=True, env=env, check=True)
        response = json.loads(result.stdout)
        self.assertEqual(response['hookSpecificOutput']['hookEventName'], 'UserPromptSubmit')
        self.assertIn('additionalContext', response['hookSpecificOutput'])
        self.assertIn('acceptance criteria', response['hookSpecificOutput']['additionalContext'])
        self.assertNotIn('private-prompt', result.stdout)
        again = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload),
                               text=True, capture_output=True, env=env, check=True)
        self.assertEqual(again.stdout, '')
    def test_sustained_work_reminds_once_without_content_or_block(self):
        self.send('UserPromptSubmit', prompt='private-user-prompt')
        for i in range(guard.THRESHOLD - 1):
            self.assertIsNone(self.work(i, tool_input={'command': 'private-command'}))
        out = self.work(guard.THRESHOLD - 1)
        self.assertEqual(set(out['hookSpecificOutput']), {'hookEventName', 'additionalContext'})
        text = out['hookSpecificOutput']['additionalContext']
        for phrase in ('Short work', 'parent-only operations', 'parent acceptance', 'existing delegated owner', 'user prohibition', 'host restrictions', 'not a requirement'):
            self.assertIn(phrase, text)
        self.assertIsNone(self.work(100))
        content = (self.home / 'codex-task-routing/parent-guard/state.sqlite3').read_bytes()
        for private in (b'private-user-prompt', b'private-command', b'session-a', b'turn-a'):
            self.assertNotIn(private, content)
    def test_duplicate_prompt_and_tool_events_do_not_trigger_early(self):
        self.send('UserPromptSubmit')
        for _ in range(20):
            self.work(1)
            self.send('UserPromptSubmit')
        for i in range(2, guard.THRESHOLD):
            self.assertIsNone(self.work(i))
        self.assertIsNotNone(self.work(guard.THRESHOLD))
    def test_child_spawn_and_existing_owner_suppress(self):
        for index, kind in enumerate(('followup_task', 'send_input', 'wait_agent')):
            with self.subTest(kind=kind):
                turn = 'turn-' + str(index)
                self.send('UserPromptSubmit', turn_id=turn)
                self.send('PreToolUse', turn_id=turn, tool_name=kind, tool_use_id='owner')
                for i in range(guard.THRESHOLD + 2):
                    self.assertIsNone(self.work(i, turn_id=turn))
    def test_failed_spawn_attempt_and_generic_wait_do_not_suppress(self):
        self.send('UserPromptSubmit')
        self.send('PreToolUse', tool_name='spawn_agent', tool_use_id='attempt')
        self.send('PreToolUse', tool_name='functions.wait', tool_use_id='wait')
        for i in range(guard.THRESHOLD - 1):
            self.assertIsNone(self.work(i))
        self.assertIsNotNone(self.work('last'))
    def test_lifecycle_active_across_turns_then_stopped(self):
        self.send('UserPromptSubmit')
        self.send('SubagentStart', agent_id='child')
        self.send('UserPromptSubmit', turn_id='other')
        for i in range(guard.THRESHOLD + 1):
            self.assertIsNone(self.work(i, turn_id='other'))
        self.send('SubagentStop', agent_id='child')
        self.assertIn('same suitable, resumable owner', self.work('resume')['hookSpecificOutput']['additionalContext'])
        for i in range(guard.THRESHOLD - 1):
            self.assertIsNone(self.work('after-' + str(i), turn_id='other'))
        self.assertIsNotNone(self.work('after-last', turn_id='other'))
    def test_stopped_owner_retest_reminder_is_conditional_and_once(self):
        self.send('UserPromptSubmit')
        self.send('SubagentStart', agent_id='child')
        self.send('SubagentStop', agent_id='child')
        out = self.send('PreToolUse', tool_name='Bash', tool_use_id='parent-retest',
                        tool_input={'command': 'private-test-command'})
        context = out['hookSpecificOutput']['additionalContext']
        for phrase in ('permissions or environment', 'same suitable, resumable owner', 'concrete reason',
                       'does not establish unfinished work', 'Parent acceptance', 'host restrictions',
                       'do not force delegation'):
            self.assertIn(phrase, context)
        self.assertIsNone(self.work('next'))
        content = (self.home / 'codex-task-routing/parent-guard/state.sqlite3').read_bytes()
        self.assertNotIn(b'private-test-command', content)

    def test_contact_and_other_active_child_suppress_resume_reminder(self):
        self.send('UserPromptSubmit')
        self.send('SubagentStart', agent_id='first')
        self.send('SubagentStart', agent_id='second')
        self.send('SubagentStop', agent_id='first')
        self.assertIsNone(self.work('while-active'))
        self.send('PreToolUse', tool_name='followup_task', tool_use_id='contact')
        self.send('SubagentStop', agent_id='second')
        self.send('PreToolUse', tool_name='send_message', tool_use_id='contact-again')
        self.assertIsNone(self.work('after-contact'))

    def test_unknown_or_duplicate_stop_does_not_trigger(self):
        self.send('UserPromptSubmit')
        self.send('SubagentStop', agent_id='unknown')
        self.assertIsNone(self.work('unknown'))
        self.send('SubagentStart', agent_id='child')
        self.send('SubagentStop', agent_id='child')
        self.assertIsNotNone(self.work('first'))
        self.send('SubagentStop', agent_id='child')
        self.assertIsNone(self.work('duplicate'))
    def test_stop_reminder_survives_turn_change(self):
        self.send('UserPromptSubmit')
        self.send('SubagentStart', agent_id='child')
        self.send('SubagentStop', agent_id='child')
        self.send('UserPromptSubmit', turn_id='next')
        self.assertIn('same suitable, resumable owner', self.work('next-work', turn_id='next')['hookSpecificOutput']['additionalContext'])
    def test_children_missing_ids_and_unknown_turn_do_not_count(self):
        self.send('UserPromptSubmit', agent_id='child')
        for i in range(guard.THRESHOLD + 1):
            self.assertIsNone(self.work(i))
        self.send('UserPromptSubmit')
        for i in range(guard.THRESHOLD + 1):
            self.assertIsNone(self.work(i, agent_id='child'))
            self.assertIsNone(self.send('PreToolUse', tool_name='exec'))
            self.assertIsNone(self.send('PreToolUse', tool_name='exec', tool_use_id=str(i), session_id=''))
        self.assertIsNone(self.work('first'))
    def test_corrupt_state_is_reported_not_blocked(self):
        path = self.home / 'codex-task-routing/parent-guard/state.sqlite3'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'invalid')
        env = dict(os.environ, CODEX_HOME=str(self.home))
        result = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(self.event('UserPromptSubmit')), text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        status = subprocess.run([sys.executable, str(SCRIPT), 'status'], text=True, capture_output=True, env=env)
        self.assertEqual(json.loads(status.stdout)['state'], 'unavailable')
    def test_deeply_nested_input_does_not_fail_or_expose_content(self):
        env = dict(os.environ, CODEX_HOME=str(self.home))
        payload = '{"hook_event_name":"PreToolUse","tool_input":' + '[' * 5000 + '"private-sentinel"' + ']' * 5000 + '}'
        result = subprocess.run([sys.executable, str(SCRIPT)], input=payload, text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '')
        self.assertFalse((self.home / 'codex-task-routing/parent-guard/state.sqlite3').exists())

    def test_status_distinguishes_unobserved_and_zero_reminders(self):
        env = dict(os.environ, CODEX_HOME=str(self.home))
        def status():
            result = subprocess.run([sys.executable, str(SCRIPT), 'status'], text=True, capture_output=True, env=env, check=True)
            return json.loads(result.stdout)
        self.assertEqual(status()['state'], 'unobserved')
        self.send('UserPromptSubmit')
        result = status()
        self.assertEqual(result['state'], 'recorded')
        self.assertEqual(result['reminders'], 0)
        self.assertIn('unknown', result['note'])

if __name__ == '__main__':
    unittest.main()
