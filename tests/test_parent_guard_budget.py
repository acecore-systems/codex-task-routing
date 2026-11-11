"""Synthetic regressions for the shared per-turn work-reminder budget."""
import importlib.util
from pathlib import Path
import shutil
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'plugins/codex-task-routing/scripts/parent_guard.py'
spec = importlib.util.spec_from_file_location('parent_guard_budget_test', SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ParentGuardBudgetTests(unittest.TestCase):
    def setUp(self):
        self.home = ROOT / 'tests/.tmp-runtime-tests' / ('parent-budget-' + uuid.uuid4().hex)
        self.home.mkdir(parents=True)
        self.send('UserPromptSubmit')

    def tearDown(self):
        assert self.home.parent == ROOT / 'tests/.tmp-runtime-tests'
        shutil.rmtree(self.home)

    def send(self, event, **kwargs):
        payload = {'hook_event_name': event, 'session_id': 'session', 'turn_id': 'root'}
        payload.update(kwargs)
        return guard.handle(payload, self.home)

    def work(self, tool_id, **kwargs):
        return self.send('PreToolUse', tool_name='exec', tool_use_id=tool_id, **kwargs)

    def start(self, child):
        self.send('SubagentStart', agent_id=child, turn_id='turn-' + child)

    def stop(self, child):
        self.send('SubagentStop', agent_id=child, turn_id='turn-' + child)

    def edit(self):
        out = self.send('PreToolUse', tool_name='apply_patch', tool_use_id='edit')
        self.assertEqual(out['hookSpecificOutput']['additionalContext'], guard.EDIT_REMINDER)

    def assert_deferred_stop(self):
        db = guard._database(self.home)
        try:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM stopped').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT reminded FROM turns').fetchone()[0], 1)
        finally:
            db.close()

    def test_edit_before_child_stop_notifies_once(self):
        self.start('owner')
        self.edit()
        self.stop('owner')
        self.assertIsNone(self.work('after-stop'))
        self.send('UserPromptSubmit')
        self.stop('owner')
        self.assertIsNone(self.work('after-stop'))
        self.assert_deferred_stop()

    def test_threshold_before_child_stop_notifies_once(self):
        for i in range(guard.THRESHOLD - 1):
            self.assertIsNone(self.work(str(i)))
        out = self.work('threshold')
        self.assertEqual(out['hookSpecificOutput']['additionalContext'], guard.REMINDER)
        self.start('owner')
        self.stop('owner')
        self.assertIsNone(self.work('after-stop'))
        self.assert_deferred_stop()

    def test_two_child_cycles_notifies_once(self):
        self.start('first')
        self.stop('first')
        out = self.work('first-stop')
        self.assertEqual(out['hookSpecificOutput']['additionalContext'], guard.RESUME_REMINDER)
        self.start('second')
        self.stop('second')
        self.assertIsNone(self.work('second-stop'))
        self.assert_deferred_stop()

    def test_deferred_stop_survives_next_turn(self):
        self.start('owner')
        self.edit()
        self.stop('owner')
        self.assertIsNone(self.work('old-turn'))
        self.send('Stop')
        self.send('UserPromptSubmit', turn_id='next')
        out = self.work('next-turn', turn_id='next')
        self.assertEqual(out['hookSpecificOutput']['additionalContext'], guard.RESUME_REMINDER)
        self.assertIsNone(self.work('next-again', turn_id='next'))

    def test_contact_clears_deferred_stop(self):
        self.start('owner')
        self.edit()
        self.stop('owner')
        self.assertIsNone(self.work('old-turn'))
        self.send('PreToolUse', tool_name='send_message', tool_use_id='contact')
        self.send('UserPromptSubmit', turn_id='next')
        self.assertIsNone(self.work('next-turn', turn_id='next'))

    def test_waits_preserve_pending_resume_notice(self):
        self.start('owner')
        self.stop('owner')
        for i in range(20):
            self.assertIsNone(self.send('PreToolUse', tool_name='write_stdin', tool_use_id=str(i)))
        out = self.work('after-waits')
        self.assertEqual(out['hookSpecificOutput']['additionalContext'], guard.RESUME_REMINDER)
        self.assertIsNone(self.work('after-notice'))


if __name__ == '__main__':
    unittest.main()
