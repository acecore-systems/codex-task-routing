"""Static checks for the CI workflow definition; these do not run GitHub Actions."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / '.github/workflows/checks.yml').read_text(encoding='utf-8').replace('\r\n', '\n')


class CiWorkflowTests(unittest.TestCase):
    def test_older_runs_for_the_same_ref_are_cancelled(self):
        self.assertIn(
            'concurrency:\n'
            '  group: checks-${{ github.workflow }}-${{ github.ref }}\n'
            '  cancel-in-progress: true\n',
            WORKFLOW,
        )
        self.assertLess(WORKFLOW.index('concurrency:'), WORKFLOW.index('\njobs:\n'))

    def test_token_stays_read_only(self):
        self.assertIn('permissions:\n  contents: read\n', WORKFLOW)

    def test_matrix_keeps_three_systems_and_windows_python_313(self):
        self.assertIn('os: [windows-latest, ubuntu-latest, macos-latest]', WORKFLOW)
        self.assertIn("python: ['3.11']", WORKFLOW)
        self.assertIn("- os: windows-latest\n            python: '3.13'", WORKFLOW)

    def test_checks_run_in_order_and_release_check_is_pull_request_only(self):
        steps = [
            'node --test tests/test_chat_browser.cjs tests/test_chat_browser_run.cjs',
            'python -m unittest discover -s tests -v',
            'python scripts/check_package.py',
            "if: github.event_name == 'pull_request'",
            'python scripts/check_release.py',
        ]
        positions = [WORKFLOW.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(set(positions)), len(steps))


if __name__ == '__main__':
    unittest.main()
