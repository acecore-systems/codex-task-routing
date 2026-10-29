"""Exercise the shipped shell command and official hook protocol without Codex."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class HookCommandTest(unittest.TestCase):
    def test_shipped_command_supports_spaces_and_both_events(self):
        root = Path(__file__).resolve().parents[1]
        source = root / "plugins/codex-task-routing"
        # Keep the scratch tree outside the checkout: base is the child's working directory,
        # and Windows refuses a working directory of about 256 characters or more (WinError 267)
        # before the shipped command runs. Resolve links as test_updater does.
        base = Path(tempfile.mkdtemp(prefix="hook-command-")).resolve()
        plugin = base / "plugin with spaces"
        try:
            for directory in ("scripts", "defaults", "hooks", ".codex-plugin", "skills"):
                shutil.copytree(source / directory, plugin / directory,
                                ignore=shutil.ignore_patterns("__pycache__"))
            home = base / "home"
            home.mkdir()
            environment = {**os.environ, "CODEX_HOME": str(home), "PLUGIN_ROOT": str(plugin),
                           "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
            hooks = json.loads((plugin / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
            for event in ("SessionStart", "SubagentStart"):
                with self.subTest(event=event):
                    # This is the reviewed command shipped in this checkout.
                    result = subprocess.run(hooks[event][0]["hooks"][0]["command"],
                                            shell=True, cwd=base, env=environment,
                                            input=json.dumps({"hook_event_name": event, "cwd": str(base),
                                                              "source": "startup"}),
                                            text=True, encoding="utf-8", capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertNotIn("systemMessage", payload)
                    self.assertNotIn("additionalContext", payload)
                    context = payload["hookSpecificOutput"]
                    self.assertEqual(context["hookEventName"], event)
                    self.assertIn("policy hash", context["additionalContext"])
                    self.assertIn("gpt-6-luna", context["additionalContext"])
                    reference = plugin / "skills/task-routing/references/local-auth.md"
                    self.assertTrue(reference.is_file())
                    # The installed root is stated once; the reference is relative to it.
                    self.assertIn(f"Plugin root: {plugin}.", context["additionalContext"])
                    self.assertEqual(context["additionalContext"].count(str(plugin)), 1)
                    self.assertIn("<plugin-root>/skills/task-routing/references/local-auth.md",
                                  context["additionalContext"])
                    self.assertLessEqual(len(context["additionalContext"]), 8000)
        finally:
            shutil.rmtree(base)
