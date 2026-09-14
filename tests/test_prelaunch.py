from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest
import uuid
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "plugins" / "codex-task-routing" / "scripts" / "prelaunch.py"
SPEC = importlib.util.spec_from_file_location("prelaunch_under_test", SCRIPT)
assert SPEC and SPEC.loader
prelaunch = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prelaunch
SPEC.loader.exec_module(prelaunch)
DEFAULT_PROCESS_INVENTORY = object()


class TargetProcess:
    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.wait_called = False

    def wait(self) -> int:
        self.wait_called = True
        return self.returncode


def plugin_entry(
    *,
    version: str = "0.1.0",
    enabled: bool = True,
    installed: bool = True,
    source_type: str = "local",
    marketplace_source: str = "https://github.com/acecore-systems/codex-task-routing",
    pinned_ref: str | None = None,
) -> dict:
    marketplace: dict[str, object] = {"sourceType": "git", "source": marketplace_source}
    if pinned_ref is not None:
        marketplace["ref"] = pinned_ref
    return {
        "pluginId": prelaunch.PLUGIN_ID,
        "name": prelaunch.PLUGIN_NAME,
        "marketplaceName": prelaunch.MARKETPLACE_NAME,
        "version": version,
        "installed": installed,
        "enabled": enabled,
        "source": {"source": source_type, "path": "plugin snapshot"},
        "marketplaceSource": marketplace,
        "installPolicy": "AVAILABLE",
        "authPolicy": "ON_INSTALL",
    }


def list_result(entry: dict | None) -> prelaunch.CommandResult:
    installed = [] if entry is None else [entry]
    return prelaunch.CommandResult(0, stdout=json.dumps({"installed": installed, "available": []}))


def upgrade_result(*, errors: list | None = None) -> prelaunch.CommandResult:
    return prelaunch.CommandResult(
        0,
        stdout=json.dumps(
            {
                "selectedMarketplaces": [prelaunch.MARKETPLACE_NAME],
                "upgradedRoots": ["new-root"],
                "errors": [] if errors is None else errors,
            }
        ),
    )


class PrelaunchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        fixture_root = Path(__file__).resolve().parent / ".tmp-runtime-tests"
        fixture_root.mkdir(exist_ok=True)
        self.base = fixture_root / f"prelaunch-{uuid.uuid4().hex}"
        self.base.mkdir()
        self.home = self.base / "home"
        self.home.mkdir()
        self.environment = patch.dict(os.environ, {"CODEX_HOME": str(self.home)}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(shutil.rmtree, self.base, True)

    @property
    def state_dir(self) -> Path:
        return self.home / prelaunch.STATE_RELATIVE

    def diagnostic(self) -> dict:
        return json.loads((self.state_dir / prelaunch.DIAGNOSTIC_NAME).read_text(encoding="utf-8"))

    def run_with(
        self,
        command_results: list[prelaunch.CommandResult],
        *,
        process_names: set[str] | None | object = DEFAULT_PROCESS_INVENTORY,
        target_returncode: int = 0,
        detach: bool = False,
    ) -> tuple[int, list[list[str]], TargetProcess]:
        calls: list[list[str]] = []
        queue = iter(command_results)
        target = TargetProcess(target_returncode)

        def runner(codex: str, arguments: list[str], state_dir: Path) -> prelaunch.CommandResult:
            self.assertEqual(codex, "codex with spaces")
            self.assertEqual(state_dir, self.state_dir)
            calls.append(list(arguments))
            return next(queue)

        with (
            patch.object(
                prelaunch,
                "_running_process_names",
                return_value=set() if process_names is DEFAULT_PROCESS_INVENTORY else process_names,
            ),
            patch.object(prelaunch, "_run_cli", side_effect=runner),
            patch.object(prelaunch, "_start_target", return_value=target),
        ):
            code = prelaunch.main(
                ["--codex", "codex with spaces", *( ["--detach"] if detach else [] ), "--", "target with spaces", "argument with spaces"]
            )
        return code, calls, target

    def test_successful_update_compares_versions_and_preserves_target_argv(self) -> None:
        code, calls, target = self.run_with(
            [list_result(plugin_entry(version="0.1.0")), upgrade_result(), list_result(plugin_entry(version="0.1.1"))],
            target_returncode=9,
        )
        self.assertEqual(code, 9)
        self.assertTrue(target.wait_called)
        self.assertEqual(calls, [
            ["list", "--marketplace", prelaunch.MARKETPLACE_NAME, "--json"],
            ["marketplace", "upgrade", prelaunch.MARKETPLACE_NAME, "--json"],
            ["list", "--marketplace", prelaunch.MARKETPLACE_NAME, "--json"],
        ])
        diagnostic = self.diagnostic()
        self.assertEqual((diagnostic["outcome"], diagnostic["reason"]), ("updated", "version_changed"))
        self.assertEqual(diagnostic["version_before"], "0.1.0")
        self.assertEqual(diagnostic["version_after"], "0.1.1")

    def test_no_change_and_pinned_ref_leave_the_registered_ref_untouched(self) -> None:
        entry = plugin_entry(pinned_ref="293bf143570d317cb17629e7fb340dbcca0bf44e")
        code, calls, _ = self.run_with([list_result(entry), upgrade_result(), list_result(entry)])
        self.assertEqual(code, 0)
        self.assertEqual(self.diagnostic()["outcome"], "unchanged")
        self.assertEqual(calls[1], ["marketplace", "upgrade", prelaunch.MARKETPLACE_NAME, "--json"])
        self.assertNotIn("293bf143570d317cb17629e7fb340dbcca0bf44e", calls[1])

    def test_running_or_unavailable_process_inventory_skips_update_and_starts_target(self) -> None:
        for names, reason in (({"ChatGPT.exe"}, "process_running"), (None, "process_check_unknown")):
            with self.subTest(names=names):
                code, calls, target = self.run_with([], process_names=names)
                self.assertEqual(code, 0)
                self.assertEqual(calls, [])
                self.assertTrue(target.wait_called)
                self.assertEqual(self.diagnostic()["reason"], reason)

    def test_recheck_after_list_prevents_upgrade_when_a_process_appears(self) -> None:
        target = TargetProcess()
        names = iter([set(), {"codex.exe"}])
        with (
            patch.object(prelaunch, "_running_process_names", side_effect=lambda: next(names)),
            patch.object(prelaunch, "_run_cli", return_value=list_result(plugin_entry())) as cli,
            patch.object(prelaunch, "_start_target", return_value=target),
        ):
            code = prelaunch.main(["--", "target"])
        self.assertEqual(code, 0)
        self.assertEqual(cli.call_count, 1)
        self.assertEqual(self.diagnostic()["reason"], "process_running_after_list")

    def test_invalid_plugin_states_never_attempt_upgrade(self) -> None:
        cases = {
            "uninstalled": (None, "plugin_not_installed"),
            "disabled": (plugin_entry(enabled=False), "plugin_disabled"),
            "local_marketplace": (plugin_entry(source_type="git"), "plugin_source_invalid"),
            "wrong_source": (plugin_entry(marketplace_source="https://github.com/other/repository"), "marketplace_source_not_authorized"),
            "unsafe_version": (plugin_entry(version="token=never-record-this"), "plugin_version_invalid"),
        }
        for name, (entry, reason) in cases.items():
            with self.subTest(name=name):
                code, calls, _ = self.run_with([list_result(entry)])
                self.assertEqual(code, 0)
                self.assertEqual(len(calls), 1)
                diagnostic_text = (self.state_dir / prelaunch.DIAGNOSTIC_NAME).read_text(encoding="utf-8")
                self.assertEqual(self.diagnostic()["reason"], reason)
                self.assertNotIn("never-record-this", diagnostic_text)

    def test_cli_failures_are_classified_without_leaking_raw_output(self) -> None:
        cases = {
            "authentication": prelaunch.CommandResult(1, stderr="authentication failed token=never-record-this"),
            "connection": prelaunch.CommandResult(1, stderr="network unreachable token=never-record-this"),
            "timeout": prelaunch.CommandResult(None, timed_out=True),
            "missing": prelaunch.CommandResult(None, missing=True),
        }
        for name, result in cases.items():
            with self.subTest(name=name):
                code, _, _ = self.run_with([result])
                self.assertEqual(code, 0)
                diagnostic_text = (self.state_dir / prelaunch.DIAGNOSTIC_NAME).read_text(encoding="utf-8")
                self.assertEqual(self.diagnostic()["reason"], "cli_missing" if name == "missing" else name)
                self.assertNotIn("never-record-this", diagnostic_text)

    def test_partial_upgrade_errors_and_malformed_json_do_not_claim_success(self) -> None:
        cases = [
            upgrade_result(errors=["server error with token=never-record-this"]),
            prelaunch.CommandResult(0, stdout="{not-json"),
        ]
        for result in cases:
            with self.subTest(result=result):
                code, calls, _ = self.run_with([list_result(plugin_entry()), result])
                self.assertEqual(code, 0)
                self.assertEqual(len(calls), 2)
                diagnostic_text = (self.state_dir / prelaunch.DIAGNOSTIC_NAME).read_text(encoding="utf-8")
                self.assertEqual(self.diagnostic()["outcome"], "failed")
                self.assertNotIn("never-record-this", diagnostic_text)

    def test_upgrade_error_json_uses_safe_authentication_classification(self) -> None:
        code, _, _ = self.run_with(
            [
                list_result(plugin_entry()),
                upgrade_result(errors=["authentication failed token=never-record-this"]),
            ]
        )
        self.assertEqual(code, 0)
        diagnostic_text = (self.state_dir / prelaunch.DIAGNOSTIC_NAME).read_text(encoding="utf-8")
        self.assertEqual(self.diagnostic()["reason"], "authentication")
        self.assertNotIn("never-record-this", diagnostic_text)

    def test_os_lock_collision_does_not_launch_and_is_released_after_crash(self) -> None:
        self.state_dir.mkdir(parents=True)
        lock_path = self.state_dir / prelaunch.LOCK_NAME
        holder_code = "\n".join(
            [
                "import os, sys",
                "path = sys.argv[1]",
                "fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)",
                "if os.fstat(fd).st_size == 0: os.write(fd, b'0')",
                "os.lseek(fd, 0, os.SEEK_SET)",
                "if os.name == 'nt':",
                "    import msvcrt",
                "    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)",
                "else:",
                "    import fcntl",
                "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)",
                "print('ready', flush=True)",
                "sys.stdin.read()",
            ]
        )
        holder = subprocess.Popen(
            [sys.executable, "-c", holder_code, str(lock_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "ready")
            with patch.object(prelaunch, "_start_target") as target:
                code = prelaunch.main(["--", "target"])
            self.assertEqual(code, prelaunch.LOCK_BUSY_EXIT)
            target.assert_not_called()
            holder.kill()
            holder.wait(timeout=5)

            target_process = TargetProcess()
            with (
                patch.object(prelaunch, "_running_process_names", return_value={"ChatGPT.exe"}),
                patch.object(prelaunch, "_start_target", return_value=target_process),
            ):
                self.assertEqual(prelaunch.main(["--", "target"]), 0)
            self.assertTrue(target_process.wait_called)
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=5)
            for stream in (holder.stdin, holder.stdout, holder.stderr):
                if stream is not None:
                    stream.close()

    def test_state_error_does_not_launch_an_unlocked_target(self) -> None:
        with (
            patch.object(prelaunch, "_state_directory", side_effect=prelaunch.StateError("unsafe")),
            patch.object(prelaunch, "_start_target") as target,
        ):
            code = prelaunch.main(["--detach", "--", "target"])
        self.assertEqual(code, prelaunch.STATE_ERROR_EXIT)
        target.assert_not_called()

    def test_override_is_never_read_or_rewritten(self) -> None:
        override = self.home / prelaunch.PLUGIN_NAME / "overrides.json"
        override.parent.mkdir(parents=True)
        override.write_text('{"schema_version":1,"models":{"terra":{"default_effort":"high"}}}', encoding="utf-8")
        before = override.read_bytes()
        self.run_with([], process_names={"codex"})
        self.assertEqual(override.read_bytes(), before)

    def test_detached_desktop_target_returns_after_start(self) -> None:
        code, calls, target = self.run_with([], process_names={"ChatGPT"}, detach=True)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertFalse(target.wait_called)

    def test_marketplace_source_rejects_credentials_ports_queries_and_fragments(self) -> None:
        self.assertTrue(prelaunch._expected_marketplace_source({
            "sourceType": "git", "source": "https://github.com/acecore-systems/codex-task-routing.git"
        }))
        for source in (
            "https://user@github.com/acecore-systems/codex-task-routing",
            "https://github.com:443/acecore-systems/codex-task-routing",
            "https://github.com/acecore-systems/codex-task-routing?ref=main",
            "https://github.com/acecore-systems/codex-task-routing#fragment",
        ):
            with self.subTest(source=source):
                self.assertFalse(prelaunch._expected_marketplace_source({"sourceType": "git", "source": source}))

    def test_permission_denied_is_not_assumed_to_be_an_authentication_failure(self) -> None:
        self.assertEqual(
            prelaunch._failure_category(prelaunch.CommandResult(1, stderr="permission denied")),
            "cli_error",
        )

    def test_timeout_terminates_a_child_after_its_parent_has_exited(self) -> None:
        """The inherited output pipe keeps communicate blocked after root exit."""

        self.state_dir.mkdir(parents=True)
        marker = self.base / "child-marker.txt"
        child_code = "\n".join(
            [
                "from pathlib import Path",
                "import sys, time",
                "time.sleep(0.75)",
                "Path(sys.argv[1]).write_text('late', encoding='utf-8')",
            ]
        )
        parent_code = "\n".join(
            [
                "import subprocess, sys",
                "subprocess.Popen(",
                "    [sys.executable, '-c', " + repr(child_code) + ", sys.argv[1]],",
                "    stdout=sys.stdout, stderr=sys.stderr, stdin=subprocess.DEVNULL,",
                ")",
            ]
        )

        result = prelaunch._run_command(
            [sys.executable, "-c", parent_code, str(marker)],
            self.state_dir,
            timeout=0.1,
        )

        self.assertTrue(result.timed_out)
        self.assertFalse(result.containment_failed)
        time.sleep(1.0)
        self.assertFalse(marker.exists())

    def test_uncontained_update_never_starts_the_target(self) -> None:
        code, calls, target = self.run_with(
            [prelaunch.CommandResult(None, timed_out=True, containment_failed=True)]
        )
        self.assertEqual(code, prelaunch.UNCONTAINED_UPDATE_EXIT)
        self.assertEqual(len(calls), 1)
        self.assertFalse(target.wait_called)
        self.assertEqual(self.diagnostic()["reason"], "update_process_uncontained")

    def test_main_requires_a_target_command(self) -> None:
        self.assertEqual(prelaunch.main(["--codex", "codex"]), 2)


if __name__ == "__main__":
    unittest.main()
