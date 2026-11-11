"""Real CLI regressions for JSON integer-conversion limits; no host access."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from runtime_fixtures import runtime_directory

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/codex-task-routing/scripts"
sys.path.insert(0, str(SCRIPTS))
import routing


class JsonEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.home = self.enterContext(runtime_directory())
        self.parent_config = self.home / "config.toml"
        self.parent_bytes = b'model = "fixture-parent"\n'
        self.parent_config.write_bytes(self.parent_bytes)
        # Set the limit only in the CLI process; never change the test runner.
        self.invalid = '{"private-fixture":' + "9" * 641 + '}'

    def cli(self, *args, payload=None):
        result = subprocess.run(
            [sys.executable, "-X", "int_max_str_digits=640", str(SCRIPTS / "routing.py"),
             "--codex-home", str(self.home), *args],
            input=payload, capture_output=True, text=True, encoding="utf-8",
            cwd=self.home, env={**os.environ, "CODEX_HOME": str(self.home)}, timeout=10,
        )
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("private-fixture", result.stdout + result.stderr)
        self.assertEqual(self.parent_config.read_bytes(), self.parent_bytes)
        return result

    def corrupt_override(self):
        path = self.home / "codex-task-routing/overrides.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.invalid, encoding="utf-8")
        return path

    def test_status_reports_invalid_json_without_changing_input(self):
        path = self.corrupt_override()
        result = self.cli("status", "--json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        status = json.loads(result.stdout)
        self.assertFalse(status["ok"])
        self.assertEqual(status["error_code"], "invalid_json")
        self.assertEqual(path.read_text(encoding="utf-8"), self.invalid)
        path.unlink()
        self.assertTrue(json.loads(self.cli("status", "--json").stdout)["ok"])

    def test_render_rejects_input_without_publishing_output(self):
        path = self.corrupt_override()
        output = self.home / "rendered"
        result = self.cli("render", "--output-dir", str(output))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertFalse(output.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), self.invalid)

    def test_malformed_hook_returns_nonblocking_diagnostic(self):
        result = self.cli("hook", payload=self.invalid)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {
            "systemMessage": "Codex Task Routing was not applied: hook input is invalid.",
        })

    def test_corrupt_optional_ledger_does_not_suppress_policy(self):
        policy = routing.load_policy(codex_home=self.home)
        key = routing.observation_sampling_key(policy)
        path = self.home / routing.OBSERVATIONS_RELATIVE / (key + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.invalid, encoding="utf-8")
        for event in ("SessionStart", "SubagentStart"):
            with self.subTest(event=event):
                result = self.cli("hook", payload=json.dumps({
                    "hook_event_name": event, "cwd": str(self.home),
                }))
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, "")
                output = json.loads(result.stdout)["hookSpecificOutput"]
                self.assertEqual(output["hookEventName"], event)
                self.assertIn("policy hash", output["additionalContext"])
                if event == "SessionStart":
                    self.assertIn("Observation sample state is unknown", output["additionalContext"])
                self.assertEqual(path.read_text(encoding="utf-8"), self.invalid)


if __name__ == "__main__":
    unittest.main()
