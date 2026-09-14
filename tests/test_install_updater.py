from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


entry = load("updater_entry_under_test", "plugins/codex-task-routing/scripts/updater_entry.py")
installer = load("install_updater_under_test", "plugins/codex-task-routing/scripts/install_updater.py")


class InstallUpdaterTest(unittest.TestCase):
    def setUp(self) -> None:
        scratch = ROOT / "tests" / ".tmp-runtime-tests"
        scratch.mkdir(exist_ok=True)
        self.base = scratch / f"install-updater-{uuid.uuid4().hex}"
        self.base.mkdir()
        self.addCleanup(shutil.rmtree, self.base, True)

    def test_install_files_are_atomic_and_preserve_other_codex_files(self) -> None:
        home = self.base / "home"
        home.mkdir()
        config = home / "config.toml"
        config.write_text('model="existing"\n', encoding="utf-8")
        overrides = home / "codex-task-routing" / "overrides.json"
        overrides.parent.mkdir()
        overrides.write_text('{"custom":true}', encoding="utf-8")
        target = home / "codex-task-routing" / "updater"
        installed = installer.install_files(target, home, Path(sys.executable))
        pointer, runtime = installer.current_generation(target)
        self.assertTrue(installed.is_file())
        self.assertTrue(runtime.is_file())
        self.assertEqual(pointer["codex_home"], str(home.absolute()))
        self.assertEqual(config.read_text(encoding="utf-8"), 'model="existing"\n')
        self.assertEqual(overrides.read_text(encoding="utf-8"), '{"custom":true}')

    def test_interrupted_reinstall_retains_complete_old_generation(self) -> None:
        home = self.base / "home"
        home.mkdir()
        target = home / "codex-task-routing" / "updater"
        installer.install_files(target, home, Path(sys.executable))
        old_pointer, old_runtime = installer.current_generation(target)
        real_replace = installer.os.replace

        def fail_pointer(source, destination):
            if Path(destination).name == "current.json":
                raise OSError("simulated interruption")
            return real_replace(source, destination)

        with mock.patch.object(installer.os, "replace", side_effect=fail_pointer):
            with self.assertRaises(OSError):
                installer.install_files(target, home, Path(sys.executable))
        pointer, runtime = installer.current_generation(target)
        self.assertEqual(pointer["generation"], old_pointer["generation"])
        self.assertEqual(runtime, old_runtime)

    def test_dry_run_creates_no_management_files_or_task(self) -> None:
        home = self.base / "home"
        home.mkdir()
        output = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            mock.patch.object(installer, "_resolve_cli", side_effect=AssertionError("dry run must not resolve CLI")),
            mock.patch.object(installer, "query_task", side_effect=AssertionError("dry run must not query task")),
        ):
            self.assertEqual(installer.main(["install", "--codex-home", str(home), "--dry-run"]), 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["registered"])
        self.assertFalse((home / "codex-task-routing" / "updater").exists())

    def test_conflicting_task_is_never_overwritten(self) -> None:
        home = self.base / "home"
        home.mkdir()
        target = home / "codex-task-routing" / "updater"
        installer.install_files(target, home, Path(sys.executable))
        with (
            mock.patch.object(installer, "_resolve_cli", return_value=Path(sys.executable)),
            mock.patch.object(installer, "query_task", return_value="conflict"),
            mock.patch.object(installer, "register_task") as register,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(installer.main(["install", "--codex-home", str(home)]), 1)
        register.assert_not_called()

    def test_uninstall_only_removes_a_managed_task(self) -> None:
        home = self.base / "home"
        home.mkdir()
        target = home / "codex-task-routing" / "updater"
        installer.install_files(target, home, Path(sys.executable))
        with mock.patch.object(installer, "query_task", return_value="conflict"), mock.patch.object(installer, "_powershell") as power:
            self.assertEqual(installer.unregister_task(installer.task_name(home), target / "entry.py", target), "conflict")
        power.assert_not_called()

    def test_task_name_isolated_by_codex_home(self) -> None:
        self.assertNotEqual(installer.task_name(self.base / "one"), installer.task_name(self.base / "two"))
        self.assertTrue(installer.task_name(self.base / "one").startswith(installer.TASK_PREFIX))

    @unittest.skipUnless(os.name == "nt", "Windows path identity")
    def test_windows_home_case_variants_share_task_and_action_identity(self) -> None:
        home = self.base / "Home"
        alternate = Path(str(home).upper())
        self.assertEqual(installer.task_name(home), installer.task_name(alternate))
        entry = home / "codex-task-routing" / "updater" / "entry.py"
        alternate_entry = Path(str(entry).upper())
        self.assertEqual(installer._task_arguments(entry).casefold(), installer._task_arguments(alternate_entry).casefold())

    def test_status_queries_orphan_task_even_when_base_is_missing(self) -> None:
        home = self.base / "home"
        home.mkdir()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(installer, "query_task", return_value="missing") as query:
            self.assertEqual(installer.main(["status", "--codex-home", str(home)]), 0)
        result = json.loads(output.getvalue())
        query.assert_called_once()
        self.assertFalse(result["registered"])

    def test_status_reports_unavailable_task_state_as_unknown_and_fails(self) -> None:
        home = self.base / "home"
        home.mkdir()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(installer, "query_task", return_value="unavailable"):
            self.assertEqual(installer.main(["status", "--codex-home", str(home)]), 1)
        self.assertIsNone(json.loads(output.getvalue())["registered"])

    def test_uninstall_removes_managed_orphan_without_reading_broken_base(self) -> None:
        home = self.base / "home"
        home.mkdir()
        broken = home / "codex-task-routing" / "updater"
        broken.mkdir(parents=True)
        (broken / "current.json").write_text("{not-json", encoding="utf-8")
        output = io.StringIO()
        completed = subprocess.CompletedProcess([], 0, "", "")
        with (
            contextlib.redirect_stdout(output),
            mock.patch.object(installer, "query_task", return_value="managed") as query,
            mock.patch.object(installer, "_powershell", return_value=completed),
            mock.patch.object(installer, "current_generation", side_effect=AssertionError("must not read base")),
        ):
            self.assertEqual(installer.main(["uninstall", "--codex-home", str(home)]), 0)
        self.assertGreaterEqual(query.call_count, 2)
        self.assertFalse(json.loads(output.getvalue())["registered"])

    def test_native_lookup_uses_registered_home_instead_of_shell_home(self) -> None:
        configured = str(self.base / "registered-home")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.base / "different-home")}), \
             mock.patch.object(entry.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"installed": []}')) as run:
            self.assertIsNone(entry._current_runtime("codex", self.base, configured))
        self.assertEqual(run.call_args.kwargs["env"]["CODEX_HOME"], configured)

    def test_task_query_requires_matching_action_and_interactive_principal(self) -> None:
        if os.name != "nt":
            self.assertEqual(installer.query_task("name", self.base / "entry.py", self.base), "unavailable")
            return
        completed = subprocess.CompletedProcess([], 0, "managed", "ignored secret")
        with mock.patch.object(installer, "_powershell", return_value=completed) as call:
            self.assertEqual(installer.query_task("name", self.base / "entry.py", self.base), "managed")
        script = call.call_args.args[0]
        self.assertIn("$rootPath=[string][char]92", script)
        self.assertIn("TaskPath -ceq $rootPath", script)
        self.assertIn("$identity.User.Value", script)
        self.assertIn("Arguments -ieq", script)
        self.assertIn("LogonType -eq 'Interactive'", script)
        self.assertIn("RunLevel -eq 'Limited'", script)

    def test_explicit_cli_does_not_probe_msix_and_is_persisted(self) -> None:
        home = self.base / "home"
        home.mkdir()
        target = home / "codex-task-routing" / "updater"
        with mock.patch.object(installer, "_powershell", side_effect=AssertionError("explicit CLI must win")):
            self.assertEqual(installer._resolve_cli(str(Path(sys.executable))), Path(sys.executable).absolute())
            with mock.patch.object(installer.shutil, "which", return_value=sys.executable):
                self.assertEqual(installer._resolve_cli("codex"), Path(sys.executable).absolute())
        installer.install_files(target, home, Path(sys.executable), cli_mode="explicit")
        pointer, _ = installer.current_generation(target)
        self.assertEqual(pointer["codex_cli_mode"], "explicit")

    def test_stable_entry_uses_explicit_cli_without_msix_resolution(self) -> None:
        home = self.base / "home"
        home.mkdir()
        target = home / "codex-task-routing" / "updater"
        stable = installer.install_files(target, home, Path(sys.executable), cli_mode="explicit")
        pointer, fallback = installer.current_generation(target)
        worker = mock.Mock(return_value=0)
        with (
            mock.patch.object(entry, "__file__", str(stable)),
            mock.patch.object(entry, "_windows_codex_cli", side_effect=AssertionError("explicit CLI must win")),
            mock.patch.object(entry, "_may_query_native_source", return_value=False),
            mock.patch.object(entry, "_load_main", return_value=worker) as load,
        ):
            self.assertEqual(entry.main(["--worker"]), 0)
        load.assert_called_once_with(fallback)
        self.assertEqual(worker.call_args.args[0][2], pointer["codex_cli"])

    def test_stable_entry_prefers_current_native_runtime_and_falls_back(self) -> None:
        home = self.base / "home"
        home.mkdir()
        target = home / "codex-task-routing" / "updater"
        stable = installer.install_files(target, home, Path(sys.executable))
        pointer, fallback = installer.current_generation(target)
        current = self.base / "current-updater.py"
        current.write_text("def main(argv=None): return 0\n", encoding="utf-8")
        worker = mock.Mock(return_value=0)
        with (
            mock.patch.object(entry, "__file__", str(stable)),
            mock.patch.object(entry, "_windows_codex_cli", return_value=None),
            mock.patch.object(entry, "_may_query_native_source", return_value=True),
            mock.patch.object(entry, "_current_runtime", return_value=current),
            mock.patch.object(entry, "_load_main", return_value=worker) as load,
        ):
            self.assertEqual(entry.main(["--worker"]), 0)
        load.assert_called_once_with(current)
        self.assertEqual(worker.call_args.args[0], ["--worker", "--codex", str(Path(pointer["codex_cli"])), "--codex-home", str(home.absolute())])
        with (
            mock.patch.object(entry, "__file__", str(stable)),
            mock.patch.object(entry, "_windows_codex_cli", return_value=None),
            mock.patch.object(entry, "_may_query_native_source", return_value=False),
            mock.patch.object(entry, "_load_main", return_value=worker) as load,
        ):
            self.assertEqual(entry.main(["--worker"]), 0)
        load.assert_called_once_with(fallback)

    def test_unloadable_new_runtime_uses_complete_fallback(self) -> None:
        home = self.base / "home"
        home.mkdir()
        target = home / "codex-task-routing" / "updater"
        stable = installer.install_files(target, home, Path(sys.executable), cli_mode="explicit")
        _, fallback = installer.current_generation(target)
        broken = self.base / "broken-updater.py"
        broken.write_text("invalid python syntax", encoding="utf-8")
        worker = mock.Mock(return_value=0)
        with mock.patch.object(entry, "__file__", str(stable)), \
             mock.patch.object(entry, "_may_query_native_source", return_value=True), \
             mock.patch.object(entry, "_current_runtime", return_value=broken), \
             mock.patch.object(entry, "_load_main", side_effect=[SyntaxError("broken version"), worker]) as load:
            self.assertEqual(entry.main(["--worker"]), 0)
        self.assertEqual(load.call_args_list, [mock.call(broken), mock.call(fallback)])
        worker.assert_called_once()


if __name__ == "__main__":
    unittest.main()
