import importlib.util
from contextlib import ExitStack
import os
import shutil
import stat
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from runtime_fixtures import runtime_directory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/codex-task-routing/scripts"))
import haiku_route as route
import routing

spec = importlib.util.spec_from_file_location("haiku_smoke_fixture", ROOT / "plugins/codex-task-routing/scripts/haiku_smoke_fixture.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class PreflightTests(unittest.TestCase):
    def test_doctor_only_reports_presence_and_never_grants_permission(self):
        with runtime_directory() as root:
            exe = root / ("claude.exe" if sys.platform == "win32" else "claude")
            exe.write_text("fake", encoding="utf-8")
            request = {"schema_version": 1, "enabled": False, "actor": "root", "request_id": "sample",
                "input_sha256": "a" * 64, "cwd": str(root), "branch": "codex/test", "head": "a" * 40,
                "tools": ["Read"], "bash_rules": [], "max_turns": 2, "max_budget_usd": 1,
                "host_execution_allowed": False, "target_operations_allowed": False,
                "materials_transfer_allowed": False, "official_endpoint_verified": False}
            calls = []
            def fake(args, **kwargs):
                calls.append((args, kwargs))
                return subprocess.CompletedProcess(args, 0, " ".join(route.HELP_FLAGS), "")
            env = {"PATH": "synthetic", "ANTHROPIC_API_KEY": "not-a-real-key", "OTHER_SECRET": "hidden"}
            with patch.object(route, "verify_repository"), patch.object(route, "verify_version"), patch.object(route.subprocess, "run", side_effect=fake):
                result = route.doctor(request, exe, environ=env)
            self.assertEqual(result["cli"], "version_and_help_checked")
            self.assertEqual(result["max_turns_option"], "help_parser_accepted")
            self.assertTrue(result["api_key_environment_variable_present"])
            self.assertEqual(result["key_validity"], "not_verified")
            self.assertEqual(result["host_authorization"], "not_verified")
            self.assertFalse(result["execution_authorized"])
            self.assertIn("enabled", result["declared_gaps"])
            self.assertNotIn("not-a-real-key", str(result))
            self.assertEqual(len(calls), 2)
            for args, kwargs in calls:
                self.assertIn("--help", args)
                self.assertNotIn("ANTHROPIC_API_KEY", kwargs["env"])
                self.assertNotIn("OTHER_SECRET", kwargs["env"])
                self.assertNotIn("--print", args)
            env.pop("ANTHROPIC_API_KEY")
            with patch.object(route, "verify_repository", side_effect=route.RouteError("mismatch")):
                missing = route.doctor(request, None, environ=env)
            self.assertFalse(missing["api_key_environment_variable_present"])
            self.assertEqual(missing["repository"], "unavailable_or_mismatch")
            self.assertEqual(missing["cli"], "executable_unspecified")

    def test_blank_key_is_not_accepted_by_runtime(self):
        for value in ("", "   "):
            with self.subTest(value=value), self.assertRaises(route.RouteError):
                route.child_environment({"ANTHROPIC_API_KEY": value}, 1)

    def test_missing_cli_flags_are_not_reported_ready(self):
        with runtime_directory() as root:
            exe = root / ("claude.exe" if sys.platform == "win32" else "claude")
            exe.write_text("fake", encoding="utf-8")
            request = {"schema_version": 1, "enabled": True, "actor": "root", "request_id": "sample",
                "input_sha256": "a" * 64, "cwd": str(root), "branch": "codex/test", "head": "a" * 40,
                "tools": ["Read"], "bash_rules": [], "max_turns": 2, "max_budget_usd": 1,
                "host_execution_allowed": True, "target_operations_allowed": True,
                "materials_transfer_allowed": True, "official_endpoint_verified": True}
            fake = [subprocess.CompletedProcess([], 0, "", ""), subprocess.CompletedProcess([], 1, "", "")]
            with patch.object(route, "verify_repository"), patch.object(route, "verify_version"), patch.object(route.subprocess, "run", side_effect=fake):
                result = route.doctor(request, exe, environ={})
            self.assertEqual(result["cli"], "flags_missing")
            self.assertEqual(result["max_turns_option"], "not_accepted")
            self.assertFalse(result["execution_authorized"])
            self.assertEqual(result["host_authorization"], "not_verified")

    def test_hook_separates_child_mechanism_from_host_conditional_routes(self):
        with runtime_directory() as root:
            for event in ("SessionStart", "SubagentStart"):
                payload = routing.hook_payload(event=event, source="startup", codex_home=root, cwd=root)
                text = payload["hookSpecificOutput"]["additionalContext"]
                self.assertIn("Use standard Codex agents for child delegation", text)
                self.assertIn("separately enabled routes require actual host permission", text)
                self.assertIn("Hooks do not dispatch work", text)
                self.assertNotIn("Standard child agents only", text)

    def test_fixture_prepares_without_calling_model_and_verifies_changes_independently(self):
        with runtime_directory() as root, ExitStack() as stack:
            base = root / "new-fixture"
            def cleanup_error(function, path, _):
                os.chmod(path, stat.S_IWRITE)
                function(path)
            stack.callback(shutil.rmtree, base, onerror=cleanup_error)
            self.assertFalse(smoke.prepare(base)["model_called"])
            request = route._load_json(base / "request.json", "request")
            self.assertFalse(request["enabled"])
            self.assertFalse(request["host_execution_allowed"])
            with self.assertRaises(ValueError):
                smoke.verify(base)
            (base / "repo" / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            self.assertTrue(smoke.verify(base)["fixture_tests_passed"])
            (base / "repo" / "test_calculator.py").write_text("pass\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                smoke.verify(base)
            with self.assertRaises(FileExistsError):
                smoke.prepare(base)


if __name__ == "__main__":
    unittest.main()
