"""Static reminder regressions; these do not establish live delivery or savings."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/codex-task-routing'


def prompt_reminder():
    tree = ast.parse((PLUGIN / 'scripts/parent_guard.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == 'PROMPT_REMINDER'
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError('PROMPT_REMINDER is missing')


class ParentPromptContractTests(unittest.TestCase):
    def test_per_turn_reminder_has_a_smaller_byte_budget(self):
        self.assertLessEqual(len(prompt_reminder().encode('utf-8')), 1200)

    def test_owner_explanation_is_limited_to_retained_substantial_work(self):
        phrase = 'explain only retained substantial transferable work once'
        self.assertIn(phrase, prompt_reminder().lower())
        skill = (PLUGIN / 'skills/task-routing/SKILL.md').read_text(encoding='utf-8')
        self.assertIn(phrase, skill)
        self.assertIn('answer short questions normally', prompt_reminder())

    def test_valid_approvals_and_checks_are_reused(self):
        self.assertIn('Reuse valid approvals and checks.', prompt_reminder())
        self.assertIn('preserve parent model/effort and overrides', prompt_reminder())
        self.assertIn('Do not invent work or force delegation.', prompt_reminder())


if __name__ == '__main__':
    unittest.main()
