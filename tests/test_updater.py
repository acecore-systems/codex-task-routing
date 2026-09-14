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


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins" / "codex-task-routing" / "scripts" / "updater.py"
SPEC = importlib.util.spec_from_file_location("updater_under_test", SCRIPT)
assert SPEC and SPEC.loader
updater = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = updater
SPEC.loader.exec_module(updater)
DEFAULT_PROCESS_NAMES = object()


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
        "pluginId": updater.PLUGIN_ID,
        "name": updater.PLUGIN_NAME,
        "marketplaceName": updater.MARKETPLACE_NAME,
        "version": version,
        "installed": installed,
        "enabled": enabled,
        "source": {"source": source_type, "path": "plugin snapshot"},
        "marketplaceSource": marketplace,
    }


def list_result(entry: dict | None) -> updater.CommandResult:
    return updater.CommandResult(0, stdout=json.dumps({"installed": [] if entry is None else [entry]}))


def upgrade_result(*, errors: list | None = None) -> updater.CommandResult:
    return updater.CommandResult(
        0,
        stdout=json.dumps(
            {
                "selectedMarketplaces": [updater.MARKETPLACE_NAME],
                "upgradedRoots": ["new-root"],
                "errors": [] if errors is None else errors,
            }
        ),
    )


class UpdaterTest(unittest.TestCase):
    def setUp(self) -> None:
        scratch = ROOT / "tests" / ".tmp-runtime-tests"
        scratch.mkdir(exist_ok=True)
        self.base = scratch / f"updater-{uuid.uuid4().hex}"
        self.base.mkdir()
        self.home = self.base / "home"
        self.home.mkdir()
        self.environment = patch.dict(os.environ, {"CODEX_HOME": str(self.home)}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(shutil.rmtree, self.base, True)

    @property
    def state_dir(self) -> Path:
        return self.home / updater.STATE_RELATIVE

    def diagnostic(self) -> dict:
        return json.loads((self.state_dir / updater.DIAGNOSTIC_NAME).read_text(encoding="utf-8"))

    def run_with(
        self,
        results: list[updater.CommandResult],
        *,
        process_names: set[str] | None | object = DEFAULT_PROCESS_NAMES,
    ) -> tuple[int, list[list[str]]]:
        calls: list[list[str]] = []
        queue = iter(results)

        def runner(codex: str, arguments: list[str], state_dir: Path) -> updater.CommandResult:
            self.assertEqual(codex, "codex with spaces")
            self.assertEqual(state_dir, self.state_dir)
            calls.append(list(arguments))
            return next(queue)

        with (
            patch.object(
                updater,
                "_running_process_names",
                return_value=set() if process_names is DEFAULT_PROCESS_NAMES else process_names,
            ),
            patch.object(updater, "_run_cli", side_effect=runner),
        ):
            code = updater.main(["--worker", "--codex", "codex with spaces"])
        return code, calls

    def test_update_compares_versions_and_keeps_pinned_ref(self) -> None:
        entry = plugin_entry(pinned_ref="293bf143570d317cb17629e7fb340dbcca0bf44e")
        code, calls = self.run_with([list_result(entry), upgrade_result(), list_result({**entry, "version": "0.1.1"})])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [
            ["list", "--marketplace", updater.MARKETPLACE_NAME, "--json"],
            ["marketplace", "upgrade", updater.MARKETPLACE_NAME, "--json"],
            ["list", "--marketplace", updater.MARKETPLACE_NAME, "--json"],
        ])
        self.assertNotIn("293bf143570d317cb17629e7fb340dbcca0bf44e", calls[1])
        self.assertEqual((self.diagnostic()["outcome"], self.diagnostic()["reason"]), ("updated", "version_changed"))

    def test_running_or_unknown_processes_skip_without_native_commands(self) -> None:
        for names, reason in (({"ChatGPT.exe"}, "process_running"), (None, "process_check_unknown")):
            with self.subTest(names=names):
                code, calls = self.run_with([], process_names=names)
                self.assertEqual(code, 0)
                self.assertEqual(calls, [])
                self.assertEqual(self.diagnostic()["reason"], reason)

    def test_recheck_prevents_upgrade_when_codex_appears(self) -> None:
        names = iter([set(), {"codex.exe"}])
        with (
            patch.object(updater, "_running_process_names", side_effect=lambda: next(names)),
            patch.object(updater, "_run_cli", return_value=list_result(plugin_entry())) as cli,
        ):
            self.assertEqual(updater.main(["--codex", "codex"]), 0)
        self.assertEqual(cli.call_count, 1)
        self.assertEqual(self.diagnostic()["reason"], "process_running_after_list")

    def test_untrusted_or_disabled_plugin_never_upgrades(self) -> None:
        cases = {
            "missing": (None, "plugin_not_installed"),
            "disabled": (plugin_entry(enabled=False), "plugin_disabled"),
            "not_local": (plugin_entry(source_type="git"), "plugin_source_invalid"),
            "wrong_repo": (plugin_entry(marketplace_source="https://github.com/other/repository"), "marketplace_source_not_authorized"),
        }
        for name, (entry, reason) in cases.items():
            with self.subTest(name=name):
                code, calls = self.run_with([list_result(entry)])
                self.assertEqual(code, 0)
                self.assertEqual(len(calls), 1)
                self.assertEqual(self.diagnostic()["reason"], reason)

    def test_failure_diagnostics_classify_without_raw_stderr(self) -> None:
        cases = {
            "authentication": updater.CommandResult(1, stderr="authentication failed token=never-store"),
            "connection": updater.CommandResult(1, stderr="network unreachable token=never-store"),
            "timeout": updater.CommandResult(None, timed_out=True),
            "missing": updater.CommandResult(None, missing=True),
        }
        for reason, result in cases.items():
            with self.subTest(reason=reason):
                code, _ = self.run_with([result])
                self.assertEqual(code, 0)
                text = (self.state_dir / updater.DIAGNOSTIC_NAME).read_text(encoding="utf-8")
                self.assertEqual(self.diagnostic()["reason"], "cli_missing" if reason == "missing" else reason)
                self.assertNotIn("never-store", text)

    def test_partial_upgrade_error_does_not_claim_success(self) -> None:
        code, calls = self.run_with([list_result(plugin_entry()), upgrade_result(errors=["server token=never-store"])])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.diagnostic()["outcome"], "failed")
        self.assertNotIn("never-store", (self.state_dir / updater.DIAGNOSTIC_NAME).read_text(encoding="utf-8"))

    def test_lock_collision_skips_and_crash_releases_os_lock(self) -> None:
        self.state_dir.mkdir(parents=True)
        lock_path = self.state_dir / updater.LOCK_NAME
        holder_code = "\n".join([
            "import os, sys",
            "fd=os.open(sys.argv[1], os.O_RDWR|os.O_CREAT, 0o600)",
            "if os.fstat(fd).st_size == 0: os.write(fd,b'0')",
            "os.lseek(fd,0,os.SEEK_SET)",
            "if os.name=='nt':",
            " import msvcrt; msvcrt.locking(fd,msvcrt.LK_NBLCK,1)",
            "else:",
            " import fcntl; fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)",
            "print('ready',flush=True)",
            "sys.stdin.read()",
        ])
        holder = subprocess.Popen([sys.executable, "-c", holder_code, str(lock_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "ready")
            code, calls = self.run_with([])
            self.assertEqual(code, 0)
            self.assertEqual(calls, [])
            holder.kill()
            holder.wait(timeout=5)
            code, calls = self.run_with([], process_names={"ChatGPT.exe"})
            self.assertEqual(code, 0)
            self.assertEqual(calls, [])
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=5)
            if holder.stdin is not None:
                holder.stdin.close()
            if holder.stdout is not None:
                holder.stdout.close()

    def test_override_is_unchanged(self) -> None:
        override = self.home / updater.PLUGIN_NAME / "overrides.json"
        override.parent.mkdir(parents=True)
        override.write_text('{"custom":true}', encoding="utf-8")
        before = override.read_bytes()
        self.run_with([], process_names={"codex"})
        self.assertEqual(override.read_bytes(), before)

    def test_native_cli_uses_explicit_home_instead_of_inherited_home(self) -> None:
        requested = self.base / "explicit-home"
        state = requested / updater.STATE_RELATIVE
        state.mkdir(parents=True)
        with patch.object(updater, "_run_command", return_value=updater.CommandResult(0)) as run:
            updater._run_cli("codex", ["list"], state)
        self.assertEqual(run.call_args.kwargs["codex_home"], requested)
        self.assertEqual(updater._cli_environment(requested)["CODEX_HOME"], str(requested))
        self.assertEqual(os.environ["CODEX_HOME"], str(self.home))

    def test_timeout_kills_child_after_parent_exit(self) -> None:
        self.state_dir.mkdir(parents=True)
        marker = self.base / "late.txt"
        ready = self.base / "ready.txt"
        release = self.base / "release.txt"
        # The write must only become possible after containment has returned.
        # A fixed child sleep races the test runner's scheduling on a busy host.
        child = "\n".join([
            "from pathlib import Path",
            "import sys,time",
            "Path(sys.argv[2]).write_text('ready')",
            "deadline = time.monotonic() + 15",
            "while not Path(sys.argv[3]).exists():",
            "    if time.monotonic() >= deadline: raise SystemExit(0)",
            "    time.sleep(.01)",
            "Path(sys.argv[1]).write_text('late')",
        ])
        parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(child) + ",*sys.argv[1:]],stdout=sys.stdout,stderr=sys.stderr)"
        try:
            result = updater._run_command(
                [sys.executable, "-c", parent, str(marker), str(ready), str(release)],
                self.state_dir, timeout=2,
            )
        finally:
            release.write_text("released", encoding="utf-8")
        self.assertTrue(result.timed_out)
        self.assertFalse(result.containment_failed)
        self.assertTrue(ready.exists(), "child fixture must start before testing containment")
        time.sleep(1)
        self.assertFalse(marker.exists())

    def test_uncontained_update_returns_nonzero(self) -> None:
        code, calls = self.run_with([updater.CommandResult(None, timed_out=True, containment_failed=True)])
        self.assertEqual(code, updater.UNCONTAINED_UPDATE_EXIT)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.diagnostic()["reason"], "update_process_uncontained")


if __name__ == "__main__":
    unittest.main()
