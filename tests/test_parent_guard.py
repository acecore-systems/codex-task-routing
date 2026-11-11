"""Synthetic hook tests; these do not establish live host delivery."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
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
                       'failure diagnosis', 'builds, offline tests', 'from the start', 'separate pure evidence', 'effective policy',
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
        for phrase in ('Short work', 'parent-only operations', 'acceptance', 'if unassigned delegate a complete unit', 'user prohibitions', 'host limits', 'non-blocking'):
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
    def test_contact_does_not_suppress_sustained_work(self):
        for index, kind in enumerate(('followup_task', 'send_input', 'send_message')):
            with self.subTest(kind=kind):
                turn = 'turn-' + str(index)
                self.send('UserPromptSubmit', turn_id=turn)
                self.send('PreToolUse', turn_id=turn, tool_name=kind, tool_use_id='owner')
                for i in range(guard.THRESHOLD - 1):
                    self.assertIsNone(self.work(i, turn_id=turn))
                out = self.work('last', turn_id=turn)
                self.assertEqual(out['hookSpecificOutput']['additionalContext'], guard.OWNER_REMINDER)
                self.assertIsNone(self.work('extra', turn_id=turn))

    def test_wait_is_neither_contact_nor_work(self):
        self.send('UserPromptSubmit')
        for i in range(10):
            self.assertIsNone(self.send('PreToolUse', tool_name='wait_agent', tool_use_id=str(i)))
        for i in range(guard.THRESHOLD - 1):
            self.assertIsNone(self.work(i))
        out = self.work('last')
        self.assertEqual(out['hookSpecificOutput']['additionalContext'], guard.REMINDER)

    def test_started_child_and_wait_without_parent_work_are_quiet(self):
        self.send('UserPromptSubmit')
        self.send('SubagentStart', agent_id='child')
        for i in range(10):
            self.assertIsNone(self.send('PreToolUse', tool_name='wait_agent', tool_use_id=str(i)))
        for i in range(guard.THRESHOLD - 1):
            self.assertIsNone(self.work(i))
        out = self.work('last')
        context = out['hookSpecificOutput']['additionalContext']
        self.assertEqual(context, guard.OWNER_REMINDER)
        for phrase in ('scope', 'actual access', 'same owner', 'allowed resume or handoff', 'Parent-only', 'acceptance',
                       'user prohibitions', 'custom policy', 'host restrictions', 'non-blocking'):
            self.assertIn(phrase, context)
        self.assertIsNone(self.work('last'))
        self.assertIsNone(self.work('extra'))

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
        for i in range(guard.THRESHOLD - 1):
            self.assertIsNone(self.work(i, turn_id='other'))
        out = self.work('last', turn_id='other')
        self.assertEqual(out['hookSpecificOutput']['additionalContext'], guard.OWNER_REMINDER)
        self.send('SubagentStop', agent_id='child')
        self.assertIn('same suitable, resumable owner', self.work('resume')['hookSpecificOutput']['additionalContext'])
        self.assertIsNone(self.work('after', turn_id='other'))
    def delegated(self, session, turn):
        path = self.home / 'codex-task-routing/parent-guard/state.sqlite3'
        db = sqlite3.connect(path)
        try:
            row = db.execute('SELECT delegated FROM turns WHERE session=? AND turn=?',
                             (guard._identifier(session), guard._identifier(turn))).fetchone()
            return None if row is None else row[0]
        finally:
            db.close()

    def test_child_turn_is_attributed_to_observed_open_root_turn(self):
        self.send('UserPromptSubmit')
        self.send('UserPromptSubmit', session_id='session-b', turn_id='root-b')
        self.send('SubagentStart', turn_id='child-turn', agent_id='child')
        self.assertEqual(self.delegated('session-a', 'turn-a'), 1)
        self.assertEqual(self.delegated('session-b', 'root-b'), 0)
        self.send('SubagentStop', turn_id='child-turn', agent_id='child')
        self.assertIsNotNone(self.work('resume'))
        for i in range(guard.THRESHOLD + 1):
            self.assertIsNone(self.work('after-' + str(i)))

    def test_closed_and_duplicate_turns_are_not_reopened(self):
        for end in ('Stop', 'Interrupt'):
            with self.subTest(end=end):
                session = 'session-' + end
                self.send('UserPromptSubmit', session_id=session, turn_id='first')
                self.send(end, session_id=session, turn_id='first')
                self.send('UserPromptSubmit', session_id=session, turn_id='first')
                self.send('SubagentStart', session_id=session, turn_id='child-1', agent_id='child-1')
                self.assertEqual(self.delegated(session, 'first'), 0)
                self.send('UserPromptSubmit', session_id=session, turn_id='second')
                self.send('UserPromptSubmit', session_id=session, turn_id='first')
                self.send(end, session_id=session, turn_id='first')
                self.send('SubagentStart', session_id=session, turn_id='child-2', agent_id='child-2')
                self.assertEqual(self.delegated(session, 'first'), 0)
                self.assertEqual(self.delegated(session, 'second'), 1)

    def test_interrupt_before_prompt_cannot_reopen_old_turn(self):
        self.send('Interrupt', turn_id='old')
        self.send('Interrupt', turn_id='old')
        self.send('UserPromptSubmit', turn_id='new')
        self.send('UserPromptSubmit', turn_id='old')
        self.send('SubagentStart', turn_id='child', agent_id='agent')
        self.assertEqual(self.delegated('session-a', 'old'), 0)
        self.assertEqual(self.delegated('session-a', 'new'), 1)
        self.send('UserPromptSubmit', session_id='session-b', turn_id='old')
        self.send('SubagentStart', session_id='session-b', turn_id='child', agent_id='agent')
        self.assertEqual(self.delegated('session-b', 'old'), 1)

    def test_interrupt_retention_is_bounded_and_expires(self):
        db = guard._database(self.home)
        now = int(time.time())
        db.execute('INSERT INTO interrupted_turns VALUES(?,?,?)',
                   (guard._identifier('session-a'), guard._identifier('expired'), now - guard.TTL_SECONDS - 1))
        db.close()
        for i in range(guard.MAX_ROWS + 2):
            self.send('Interrupt', turn_id='interrupt-' + str(i))
        db = guard._database(self.home)
        try:
            self.assertLessEqual(db.execute('SELECT COUNT(*) FROM interrupted_turns').fetchone()[0], guard.MAX_ROWS)
            self.assertIsNone(db.execute('SELECT 1 FROM interrupted_turns WHERE turn=?',
                                         (guard._identifier('expired'),)).fetchone())
        finally:
            db.close()

    def test_legacy_status_is_read_only_and_does_not_count_delegated(self):
        path = self.home / 'codex-task-routing/parent-guard/state.sqlite3'
        path.parent.mkdir(parents=True)
        db = sqlite3.connect(path)
        db.execute('CREATE TABLE turns (session TEXT NOT NULL, turn TEXT NOT NULL, created INTEGER NOT NULL, delegated INTEGER NOT NULL DEFAULT 0, owner_contact INTEGER NOT NULL DEFAULT 0, reminded INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(session,turn))')
        db.execute('INSERT INTO turns VALUES(?,?,?,1,0,0)', (guard._identifier('session-a'), guard._identifier('legacy'), int(time.time())))
        db.commit()
        db.close()
        before = path.read_bytes()
        env = dict(os.environ, CODEX_HOME=str(self.home))
        def status():
            return json.loads(subprocess.run([sys.executable, str(SCRIPT), 'status'], text=True,
                                             capture_output=True, env=env, check=True).stdout)
        self.assertEqual(status()['turns_with_observed_child_start'], 0)
        self.assertEqual(path.read_bytes(), before)
        self.send('UserPromptSubmit', turn_id='new')
        self.send('SubagentStart', turn_id='child', agent_id='agent')
        self.assertEqual(status()['turns_with_observed_child_start'], 1)
        self.assertEqual(self.delegated('session-a', 'legacy'), 1)

    def test_duplicate_child_start_does_not_mark_new_turn(self):
        self.send('UserPromptSubmit')
        self.send('SubagentStart', agent_id='child', turn_id='child-turn')
        self.send('SubagentStop', agent_id='child', turn_id='child-turn')
        self.send('PreToolUse', tool_name='followup_task', tool_use_id='contact')
        self.send('Stop')
        self.send('UserPromptSubmit', turn_id='next')
        self.send('SubagentStart', agent_id='child', turn_id='child-turn')
        self.assertEqual(self.delegated('session-a', 'turn-a'), 1)
        self.assertEqual(self.delegated('session-a', 'next'), 0)
        for i in range(guard.THRESHOLD - 1):
            self.assertIsNone(self.work('next-' + str(i), turn_id='next'))
        self.assertIsNotNone(self.work('next-last', turn_id='next'))
        self.send('SubagentStart', agent_id='child', turn_id='new-child-turn')
        self.assertEqual(self.delegated('session-a', 'next'), 1)

    def test_existing_active_child_does_not_mark_open_turn(self):
        self.send('UserPromptSubmit')
        db = guard._database(self.home)
        db.execute('INSERT INTO active VALUES(?,?,?)',
                   (guard._identifier('session-a'), guard._identifier('child'), 1))
        db.close()
        self.send('SubagentStart', agent_id='child', turn_id='child-turn')
        self.assertEqual(self.delegated('session-a', 'turn-a'), 0)

    def test_existing_database_does_not_infer_old_turn_is_open(self):
        db = guard._database(self.home)
        db.execute('INSERT INTO turns(session,turn,created) VALUES(?,?,?)',
                   (guard._identifier('session-a'), guard._identifier('turn-a'), 1))
        db.close()
        self.send('SubagentStart', agent_id='child', turn_id='child-turn')
        self.assertEqual(self.delegated('session-a', 'turn-a'), 0)

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

    def test_first_edit_reminds_before_threshold_and_shares_turn_budget(self):
        self.send('UserPromptSubmit')
        self.assertIsNone(self.work('inspect'))
        out = self.send('PreToolUse', tool_name='functions.apply_patch', tool_use_id='edit',
                        tool_input={'patch': 'private-edit-patch'})
        self.assertEqual(out['hookSpecificOutput'], {
            'hookEventName': 'PreToolUse', 'additionalContext': guard.EDIT_REMINDER})
        self.assertIn('effective custom policy', guard.EDIT_REMINDER)
        self.assertIn('not a requirement to delegate', guard.EDIT_REMINDER)
        self.assertIsNone(self.send('PreToolUse', tool_name='functions.apply_patch', tool_use_id='edit'))
        for i in range(guard.THRESHOLD + 1):
            self.assertIsNone(self.work('after-' + str(i)))
        content = (self.home / 'codex-task-routing/parent-guard/state.sqlite3').read_bytes()
        self.assertNotIn(b'private-edit-patch', content)

    def test_early_edit_respects_existing_owner_child_and_open_turn(self):
        for mode in ('child', 'closed', 'interrupted', 'unknown'):
            with self.subTest(mode=mode):
                session = 'edit-' + mode
                if mode != 'unknown':
                    self.send('UserPromptSubmit', session_id=session)
                if mode in ('closed', 'interrupted'):
                    self.send('Stop' if mode == 'closed' else 'Interrupt', session_id=session)
                self.assertIsNone(self.send(
                    'PreToolUse', session_id=session, tool_name='functions__apply_patch', tool_use_id='edit',
                    agent_id='child' if mode == 'child' else None))

    def test_edit_after_child_start_or_contact_checks_scope_once(self):
        for mode in ('active', 'contact', 'stopped-contact'):
            with self.subTest(mode=mode):
                session = 'scope-' + mode
                self.send('UserPromptSubmit', session_id=session)
                if mode != 'contact':
                    self.send('SubagentStart', session_id=session, agent_id='evidence-child')
                if mode == 'stopped-contact':
                    self.send('SubagentStop', session_id=session, agent_id='evidence-child')
                if mode != 'active':
                    self.send('PreToolUse', session_id=session, tool_name='send_message', tool_use_id='contact')
                out = self.send('PreToolUse', session_id=session, tool_name='apply_patch', tool_use_id='edit')
                self.assertEqual(out['hookSpecificOutput']['additionalContext'], guard.EDIT_REMINDER)
                self.assertIn('existing owner and their scope', guard.EDIT_REMINDER)
                self.assertIsNone(self.send('PreToolUse', session_id=session, tool_name='apply_patch', tool_use_id='edit'))

    def test_short_checks_and_waits_do_not_trigger(self):
        self.send('UserPromptSubmit')
        for i in range(20):
            self.assertIsNone(self.send('PreToolUse', tool_name='write_stdin', tool_use_id='wait-' + str(i)))
        self.assertIsNone(self.work('check-1'))
        self.assertIsNone(self.work('check-2'))
        self.assertIsNone(self.work('check-3'))
        self.assertIsNotNone(self.work('check-4'))

    def test_resume_notice_prevents_following_edit_and_threshold_duplicates(self):
        self.send('UserPromptSubmit', turn_id='first')
        self.send('SubagentStart', agent_id='owner', turn_id='child-turn')
        self.send('Stop', turn_id='first')
        self.send('SubagentStop', agent_id='owner', turn_id='child-turn')
        self.send('UserPromptSubmit')
        out = self.send('PreToolUse', tool_name='apply_patch', tool_use_id='edit')
        self.assertEqual(out['hookSpecificOutput']['additionalContext'], guard.RESUME_REMINDER)
        self.assertIsNone(self.send('PreToolUse', tool_name='apply_patch', tool_use_id='edit'))
        for i in range(guard.THRESHOLD + 1):
            self.assertIsNone(self.work('after-' + str(i)))
        self.assertEqual(self.delegated('session-a', 'turn-a'), 0)

    def test_failed_resume_notice_rolls_back_stop_and_reminder(self):
        self.send('UserPromptSubmit')
        self.send('SubagentStart', agent_id='owner')
        self.send('SubagentStop', agent_id='owner')
        db = guard._database(self.home)
        db.execute("CREATE TRIGGER fail_reminder BEFORE UPDATE OF reminded ON turns "
                   "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        db.close()
        payload = self.event('PreToolUse', tool_name='apply_patch', tool_use_id='edit')
        result = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload),
                                text=True, capture_output=True, env=dict(os.environ, CODEX_HOME=str(self.home)))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        db = guard._database(self.home)
        try:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM stopped').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT reminded FROM turns').fetchone()[0], 0)
            db.execute('DROP TRIGGER fail_reminder')
        finally:
            db.close()
        out = guard.handle(payload, self.home)
        self.assertEqual(out['hookSpecificOutput']['additionalContext'], guard.RESUME_REMINDER)


class ExecutionOwnerPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = ROOT / 'plugins/codex-task-routing/scripts/routing.py'
        spec = importlib.util.spec_from_file_location('execution_owner_policy_test', script)
        cls.routing = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.routing
        spec.loader.exec_module(cls.routing)

    def setUp(self):
        self.home = ROOT / 'tests/.tmp-runtime-tests' / ('execution-owner-' + uuid.uuid4().hex)
        self.home.mkdir(parents=True)

    def tearDown(self):
        assert self.home.parent == ROOT / 'tests/.tmp-runtime-tests'
        shutil.rmtree(self.home)

    def override(self, *, principles, models=None):
        value = {'schema_version': 1, 'principles': principles}
        if models:
            value['models'] = models
        path = self.home / self.routing.OVERRIDE_RELATIVE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    def context(self, event):
        # Do not inspect developer/user guidance while testing policy rendering.
        with patch.object(self.routing, 'detect_routing_conflicts', return_value=[]):
            result = self.routing.hook_payload(event=event, source='startup', codex_home=self.home)
        return result

    def test_default_checkpoint_reaches_documents_and_both_start_hooks(self):
        policy = self.routing.load_policy(codex_home=self.home)
        checkpoint = policy.resolve_token('principles.policy_execution_owner')
        for text in ('Luna/max', 'Terra/high', 'Sol/high', 'Pro', '失敗診断', '再試験',
                     '最小確認', '分離できる通常の調査も先取りせず担当へ渡す', '同時作業数・モデル別使用件数を目的にしない',
                     '現在の実model・effort', '有効な明示override', '完了成果と副作用',
                     '未完了単位だけ', '文章指示を実際のeffort変更と扱わず', '親が先取りする前に',
                     'クライアントとnative hostの部品境界'):
            self.assertIn(text, checkpoint)
        for name in ('effective.md', 'model-routing-policy.md'):
            self.assertEqual(policy.rendered_documents()[name].count(checkpoint), 1)
        for event in ('SessionStart', 'SubagentStart'):
            result = self.context(event)['hookSpecificOutput']
            self.assertEqual(result['hookEventName'], event)
            self.assertIn('execution-owner checkpoint in effective.md', result['additionalContext'])
            self.assertLessEqual(len(result['additionalContext'].encode('utf-8')),
                                 self.routing.MAX_ADDITIONAL_CONTEXT_BYTES)

    def test_summary_override_keeps_update_visible_and_model_override_effective(self):
        summary = '独自要約：この対象では親が継続し、委譲しない。'
        path = self.override(principles={'summary': summary},
                             models={'terra61': {'id': 'custom-terra', 'default_effort': 'max'}})
        before = path.read_bytes()
        policy = self.routing.load_policy(codex_home=self.home)
        checkpoint = policy.resolve_token('principles.policy_execution_owner')
        self.assertEqual(policy.config['principles']['summary'], summary)
        self.assertIn('Terra/max', checkpoint)
        self.assertIn('有効な原則overrideを優先', checkpoint)
        self.assertIn('矛盾はrootへ返す', checkpoint)
        effective = policy.rendered_documents()['effective.md']
        self.assertIn(summary, effective)
        self.assertIn(checkpoint, effective)
        self.assertIn('custom-terra', effective)
        for event in ('SessionStart', 'SubagentStart'):
            context = self.context(event)['hookSpecificOutput']['additionalContext']
            self.assertIn('execution-owner checkpoint in effective.md', context)
            self.assertIn('custom-terra (max)', context)
            self.assertIn('Custom routing principles preserved:', context)
        self.assertEqual(path.read_bytes(), before)

    def test_checkpoint_override_is_preserved_and_reviewed(self):
        checkpoint = '独自担当確認：既存担当を継続し、不一致はrootへ返す。'
        path = self.override(principles={'policy_execution_owner': checkpoint})
        before = path.read_bytes()
        policy = self.routing.load_policy(codex_home=self.home)
        self.assertEqual(policy.resolve_token('principles.policy_execution_owner'), checkpoint)
        self.assertIn('principles.policy_execution_owner', policy.applied_override_keys)
        for name in ('effective.md', 'model-routing-policy.md'):
            self.assertIn(checkpoint, policy.rendered_documents()[name])
        for event in ('SessionStart', 'SubagentStart'):
            context = self.context(event)['hookSpecificOutput']['additionalContext']
            self.assertIn('execution-owner checkpoint in effective.md', context)
            self.assertIn('Custom routing principles preserved:', context)
            self.assertNotIn('### 実行担当のチェックポイント', context)
        self.assertEqual(path.read_bytes(), before)

    def test_large_checkpoint_is_referenced_without_inflating_hook(self):
        path = self.override(principles={
            'policy_execution_owner': 'x' * self.routing.MAX_ADDITIONAL_CONTEXT_BYTES})
        before = path.read_bytes()
        for event in ('SessionStart', 'SubagentStart'):
            self.assertIn('execution-owner checkpoint in effective.md',
                          self.context(event)['hookSpecificOutput']['additionalContext'])
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
