import io
import json
import os
import subprocess
from contextlib import redirect_stdout
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from runtime_fixtures import runtime_directory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/codex-task-routing/scripts"))
import haiku_config
import routing


class HaikuConfigTests(unittest.TestCase):
    def test_absent_disabled_enabled_and_root_only(self):
        with runtime_directory() as home:
            def hook(event):
                return routing.hook_payload(event=event, source="startup", codex_home=home, cwd=home)
            def context(event):
                return hook(event)["hookSpecificOutput"]["additionalContext"]
            self.assertNotIn("Opt-in Haiku text-only", context("SessionStart"))
            config = home / "codex-task-routing/haiku.json"
            config.parent.mkdir(exist_ok=True)
            config.write_text(json.dumps({"schema_version": 1, "enabled": False}), encoding="utf-8")
            self.assertNotIn("Opt-in Haiku text-only", context("SessionStart"))
            config.write_text(json.dumps({"schema_version": 1, "enabled": True}), encoding="utf-8")
            original = config.read_bytes()
            with patch("subprocess.run", side_effect=AssertionError("hook must not execute")):
                parent = context("SessionStart")
                child = context("SubagentStart")
            self.assertIn("Opt-in Haiku text-only proposal candidate enabled for root", parent)
            self.assertIn("Follow effective policy and overrides", parent)
            self.assertIn("per-request material authorization", parent)
            self.assertIn("no automatic execution or resend", parent)
            self.assertNotIn("Opt-in Haiku text-only", child)
            self.assertEqual(config.read_bytes(), original)
            self.assertLessEqual(len(parent.encode("utf-8")), routing.MAX_ADDITIONAL_CONTEXT_BYTES)

    def test_invalid_config_fails_closed_and_does_not_echo_or_break_policy(self):
        with runtime_directory() as home:
            config = home / "codex-task-routing/haiku.json"
            config.parent.mkdir()
            for value in ({"schema_version": 1, "enabled": "secret-value"},
                          {"schema_version": True, "enabled": True},
                          {"schema_version": 1, "enabled": True, "extra": "secret-value"}):
                with self.subTest(value=value):
                    config.write_text(json.dumps(value), encoding="utf-8")
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(haiku_config.main(["status", "--codex-home", str(home)]), 1)
                    self.assertEqual(json.loads(output.getvalue())["state"], "invalid")
                    payload = routing.hook_payload(event="SessionStart", source="startup", codex_home=home, cwd=home)
                    self.assertIn("Haiku route not applied", payload["systemMessage"])
                    self.assertNotIn("Opt-in Haiku text-only", payload["hookSpecificOutput"]["additionalContext"])
                    self.assertNotIn("secret-value", json.dumps(payload))
                    self.assertIn("policy hash", payload["hookSpecificOutput"]["additionalContext"])

    def test_isolated_home_real_hook_reports_enabled_root_only(self):
        with runtime_directory() as home:
            path = home / "codex-task-routing/haiku.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"schema_version": 1, "enabled": True}), encoding="utf-8")
            env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
            entry = ROOT / "plugins/codex-task-routing/scripts/routing.py"
            for event in ("SessionStart", "SubagentStart"):
                result = subprocess.run([sys.executable, str(entry), "--codex-home", str(home), "hook"],
                    input=json.dumps({"hook_event_name": event, "source": "startup", "cwd": str(home)}),
                    cwd=home, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15, check=True)
                payload = json.loads(result.stdout)
                self.assertNotIn("systemMessage", payload)
                context = payload["hookSpecificOutput"]["additionalContext"]
                self.assertEqual("Opt-in Haiku text-only" in context, event == "SessionStart")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["enabled"], True)

    def test_haiku_and_chat_preserve_custom_principles(self):
        with runtime_directory() as home:
            settings = home / "codex-task-routing"
            settings.mkdir()
            (settings / "haiku.json").write_text(json.dumps({"schema_version": 1, "enabled": True}), encoding="utf-8")
            (settings / "chatgpt.json").write_text(json.dumps({"schema_version": 1, "enabled": True,
                "required_model": "Pro", "transport": "browser-temporary"}), encoding="utf-8")
            override = settings / "overrides.json"
            override.write_text(json.dumps({"schema_version": 1, "principles": {"summary": "custom user policy"}}), encoding="utf-8")
            original = override.read_bytes()
            payload = routing.hook_payload(event="SessionStart", source="startup", codex_home=home, cwd=home)
            self.assertNotIn("systemMessage", payload)
            context = payload["hookSpecificOutput"]["additionalContext"]
            self.assertIn("Custom principles replace the bundled summary", context)
            self.assertIn("Opt-in ChatGPT Chat route is enabled", context)
            self.assertIn("Opt-in Haiku text-only", context)
            self.assertIn("Follow effective policy and overrides", context)
            self.assertEqual(original, override.read_bytes())
            self.assertLessEqual(len(context.encode("utf-8")), routing.MAX_ADDITIONAL_CONTEXT_BYTES)

    def test_status_never_enables_or_creates_configuration(self):
        with runtime_directory() as home:
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(haiku_config.main(["status", "--codex-home", str(home)]), 0)
            result = json.loads(out.getvalue())
            self.assertEqual(result, {"enabled": False, "state": "absent", "execution_authorized": False})
            self.assertFalse((home / "codex-task-routing/haiku.json").exists())


if __name__ == "__main__":
    unittest.main()
