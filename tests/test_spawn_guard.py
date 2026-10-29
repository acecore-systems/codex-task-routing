"""The opt-in spawn guard must stay inert by default and record only fixed codes."""
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
import uuid


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/codex-task-routing"
TEMP_ROOT = ROOT / "tests/.tmp-runtime-tests"
SPEC = importlib.util.spec_from_file_location("spawn_guard_under_test", PLUGIN / "scripts/spawn_guard.py")
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)

CALLER_MODEL = "gpt-6-luna"
CHILD_AGENT = "child-7f3a"
SESSION = "session-9c1e"
RECORD_KEYS = {"schema_version", "recorded_at", "mode", "verdict", "reason", "would_deny"}
RECORDED_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$")
GUARD_COMMAND = ("python -c \"import os,runpy;runpy.run_path(os.path.join(os.environ['PLUGIN_ROOT'],"
                 "'scripts','spawn_guard.py'),run_name='__main__')\" hook")
VALID_RECORD = {
    "schema_version": 1, "recorded_at": "2026-10-29T06:57Z", "mode": "enforce",
    "verdict": "same_model", "reason": "same_model", "would_deny": True,
}


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.base = TEMP_ROOT / ("spawn-guard-" + uuid.uuid4().hex)
        self.home = self.base / "home"
        self.home.mkdir(parents=True)
        self.links = []

    def tearDown(self):
        # Remove test-created links first so rmtree never meets a directory link.
        for link in self.links:
            if os.path.islink(link):
                try:
                    os.unlink(link)
                except OSError:
                    os.rmdir(link)
        # Only this test's directory under the ignored temporary root is removed.
        assert self.base.parent == TEMP_ROOT
        shutil.rmtree(self.base)

    @property
    def config_path(self):
        return self.home / guard.CONFIG_RELATIVE

    @property
    def events_path(self):
        return self.home / guard.EVENTS_RELATIVE

    def write_config(self, value):
        text = value if isinstance(value, str) else json.dumps(value)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(text, encoding="utf-8")

    def set_mode(self, mode):
        self.write_config({"schema_version": 1, "mode": mode})

    def child_payload(self, tool_input=None, drop=(), **changes):
        value = {
            "session_id": SESSION,
            "turn_id": "turn-4d2b",
            "cwd": str(self.home),
            "hook_event_name": "PreToolUse",
            "model": CALLER_MODEL,
            "permission_mode": "default",
            "tool_name": "spawn_agent",
            "tool_input": ({"message": "Synthetic delegated task.", "model": CALLER_MODEL}
                           if tool_input is None else tool_input),
            "tool_use_id": "tool-88a1",
            "transcript_path": str(self.home / "transcript.jsonl"),
            "agent_id": CHILD_AGENT,
        }
        for key in drop:
            value.pop(key)
        value.update(changes)
        return value

    def run_hook(self, value):
        return guard.hook(json.dumps(value).encode("utf-8"), self.home)

    def records(self):
        if not self.events_path.is_file():
            return []
        return [json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines()]

    def assertNoGuardState(self):
        self.assertFalse((self.home / guard.EVENTS_DIRECTORY_RELATIVE).exists())
        self.assertFalse(self.events_path.exists())

    def symlink_or_skip(self, link, target, is_directory=False):
        try:
            os.symlink(target, link, target_is_directory=is_directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links are unavailable here ({type(exc).__name__})")
        self.links.append(link)

    def status(self):
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = guard.main(["--codex-home", str(self.home), "status", "--json"])
        return code, out.getvalue(), err.getvalue()


class ConfigGateTests(GuardCase):
    def test_missing_configuration_is_off_and_creates_nothing(self):
        self.assertIsNone(self.run_hook(self.child_payload()))
        self.assertEqual(guard.load_mode(self.home), "off")
        status = guard.status_payload(self.home)
        self.assertEqual(status["mode"], "off")
        self.assertFalse(status["configured"])
        self.assertFalse((self.home / "codex-task-routing").exists())

    def test_explicit_off_mode_is_inert_for_same_model_enforce_input(self):
        self.set_mode("off")
        self.assertIsNone(self.run_hook(self.child_payload()))
        self.assertNoGuardState()

    def test_invalid_configurations_are_off_and_create_nothing(self):
        invalid = [
            "{bad",
            '{"schema_version":1,"mode":"enforce","mode":"off"}',
            '{"schema_version":1,"mode":"ENFORCE"}',
            '{"schema_version":1,"mode":"enforce","extra":true}',
            '{"schema_version":"1","mode":"enforce"}',
            '{"schema_version":true,"mode":"enforce"}',
            '{"schema_version":2,"mode":"enforce"}',
            '{"mode":"enforce"}',
            '["enforce"]',
            " " * (guard.MAX_CONFIG_BYTES + 1) + '{"schema_version":1,"mode":"enforce"}',
        ]
        for index, text in enumerate(invalid):
            with self.subTest(case=index):
                self.write_config(text)
                self.assertIsNone(self.run_hook(self.child_payload()))
                self.assertEqual(guard.load_mode(self.home), "off")
                status = guard.status_payload(self.home)
                self.assertEqual(status["mode"], "invalid")
                self.assertTrue(status["configured"])
                self.assertNoGuardState()

    def test_symlinked_configuration_directory_is_off(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "spawn-guard.json").write_text('{"schema_version":1,"mode":"enforce"}', encoding="utf-8")
        self.symlink_or_skip(self.home / "codex-task-routing", outside, is_directory=True)
        self.assertEqual(guard.load_mode(self.home), "off")
        self.assertIsNone(self.run_hook(self.child_payload()))
        # The events path sits under the same unsafe ancestor, so status fails closed.
        code, out, err = self.status()
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("error:", err)
        self.assertFalse((outside / "spawn-guard").exists())

    def test_unexpected_errors_pass_through_without_raising(self):
        raw = json.dumps(self.child_payload()).encode("utf-8")
        self.assertIsNone(guard.hook(raw, None))

    def test_record_event_writes_nothing_when_off(self):
        result = {"verdict": "same_model", "reason": "same_model", "would_deny": True}
        guard.record_event(self.home, "off", result)
        self.assertNoGuardState()


class SameModelTests(GuardCase):
    def test_observe_records_one_fixed_line_and_passes_through(self):
        self.set_mode("observe")
        self.assertIsNone(self.run_hook(self.child_payload()))
        text = self.events_path.read_text(encoding="utf-8")
        self.assertEqual(text.count("\n"), 1)
        (record,) = self.records()
        self.assertEqual(set(record), RECORD_KEYS)
        self.assertEqual(record["schema_version"], 1)
        self.assertRegex(record["recorded_at"], RECORDED_AT)
        self.assertEqual(record["mode"], "observe")
        self.assertEqual(record["verdict"], "same_model")
        self.assertEqual(record["reason"], "same_model")
        self.assertIs(record["would_deny"], True)
        for secret in (CALLER_MODEL, CHILD_AGENT, SESSION, "turn-4d2b", "tool-88a1",
                       "Synthetic delegated task", str(self.home), str(self.base)):
            self.assertNotIn(secret, text)

    def test_enforce_denies_with_fixed_reason_and_records_once(self):
        self.set_mode("enforce")
        result = self.run_hook(self.child_payload())
        self.assertEqual(result, guard.deny_response())
        output = result["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertEqual(output["permissionDecisionReason"], guard.DENY_REASON)
        self.assertNotIn(CALLER_MODEL, json.dumps(result))
        (record,) = self.records()
        self.assertEqual(record["mode"], "enforce")
        self.assertIs(record["would_deny"], True)
        self.assertNotIn(CHILD_AGENT, self.events_path.read_text(encoding="utf-8"))

    def test_surrounding_whitespace_is_trimmed_before_comparison(self):
        self.set_mode("enforce")
        payload = self.child_payload({"model": f" {CALLER_MODEL} "}, model=f"{CALLER_MODEL}  ")
        self.assertEqual(guard.evaluate(payload),
                         {"verdict": "same_model", "reason": "same_model", "would_deny": True})
        self.assertEqual(self.run_hook(payload), guard.deny_response())

    def test_blank_agent_type_does_not_block_same_model(self):
        self.set_mode("enforce")
        payload = self.child_payload({"model": CALLER_MODEL, "agent_type": "   "})
        self.assertEqual(self.run_hook(payload), guard.deny_response())

    def test_agent_tool_alias_is_guarded(self):
        self.set_mode("enforce")
        self.assertEqual(self.run_hook(self.child_payload(tool_name="Agent")), guard.deny_response())

    def test_utf8_bom_input_is_accepted(self):
        self.set_mode("enforce")
        raw = b"\xef\xbb\xbf" + json.dumps(self.child_payload()).encode("utf-8")
        self.assertEqual(guard.hook(raw, self.home), guard.deny_response())


class PassThroughTests(GuardCase):
    def setUp(self):
        super().setUp()
        self.set_mode("enforce")

    def test_root_spawns_are_outside_the_guard_and_not_recorded(self):
        for label, payload in (
            ("missing", self.child_payload(drop=("agent_id",))),
            ("empty", self.child_payload(agent_id="")),
            ("blank", self.child_payload(agent_id="   ")),
            ("not text", self.child_payload(agent_id=5)),
        ):
            with self.subTest(label):
                self.assertIsNone(guard.evaluate(payload))
                self.assertIsNone(self.run_hook(payload))
        self.assertNoGuardState()

    def test_other_tools_and_events_are_outside_the_guard(self):
        for label, payload in (
            ("other tool", self.child_payload(tool_name="shell")),
            ("unhashable tool", self.child_payload(tool_name=["spawn_agent"])),
            ("other event", self.child_payload(hook_event_name="PostToolUse")),
        ):
            with self.subTest(label):
                self.assertIsNone(guard.evaluate(payload))
                self.assertIsNone(self.run_hook(payload))
        self.assertNoGuardState()

    def test_undetermined_child_spawns_pass_through_and_record_the_reason(self):
        cases = [
            (self.child_payload(tool_input="gpt-6-luna"), "tool_input_unreadable"),
            (self.child_payload(drop=("model",)), "caller_model_missing"),
            (self.child_payload(model="  "), "caller_model_missing"),
            (self.child_payload({"message": "x"}), "model_omitted"),
            (self.child_payload({"message": "x", "model": None}), "model_omitted"),
            (self.child_payload({"message": "x", "model": ""}), "model_omitted"),
            (self.child_payload({"message": "x", "model": " "}), "model_omitted"),
            (self.child_payload({"model": 5}), "model_invalid"),
            (self.child_payload({"model": [CALLER_MODEL]}), "model_invalid"),
            (self.child_payload({"model": CALLER_MODEL, "agent_type": "reviewer"}), "role_may_override_model"),
            (self.child_payload({"model": CALLER_MODEL, "agent_type": 0}), "role_may_override_model"),
            (self.child_payload({"model": CALLER_MODEL, "agent_type": False}), "role_may_override_model"),
        ]
        for index, (payload, reason) in enumerate(cases):
            with self.subTest(case=index, reason=reason):
                self.assertEqual(guard.evaluate(payload),
                                 {"verdict": "undetermined", "reason": reason, "would_deny": False})
                self.assertIsNone(self.run_hook(payload))
        records = self.records()
        self.assertEqual([record["reason"] for record in records], [reason for _, reason in cases])
        self.assertTrue(all(record["verdict"] == "undetermined" for record in records))
        self.assertTrue(all(record["would_deny"] is False for record in records))

    def test_different_model_passes_through_and_is_recorded(self):
        payload = self.child_payload({"model": "gpt-6-sol"})
        self.assertEqual(guard.evaluate(payload),
                         {"verdict": "not_same_model", "reason": "different_model", "would_deny": False})
        self.assertIsNone(self.run_hook(payload))
        (record,) = self.records()
        self.assertEqual(record["verdict"], "not_same_model")
        self.assertEqual(record["reason"], "different_model")
        self.assertIs(record["would_deny"], False)

    def test_model_comparison_is_case_sensitive(self):
        payload = self.child_payload({"model": CALLER_MODEL.upper()})
        self.assertEqual(guard.evaluate(payload)["reason"], "different_model")
        self.assertIsNone(self.run_hook(payload))

    def test_malformed_input_is_ignored_without_state(self):
        same = self.child_payload()
        duplicate_top = b'{"hook_event_name":"PreToolUse","hook_event_name":"PreToolUse"}'
        duplicate_tool_input = (
            b'{"hook_event_name":"PreToolUse","tool_name":"spawn_agent","agent_id":"c","model":"gpt-6-luna",'
            b'"tool_input":{"model":"gpt-6-luna","model":"gpt-6-sol"}}'
        )
        for label, raw in (
            ("invalid json", b"{bad"),
            ("array", b"[]"),
            ("empty", b""),
            ("invalid utf-8", b"\xff\xfe{}"),
            ("duplicate top-level key", duplicate_top),
            ("duplicate tool_input key", duplicate_tool_input),
        ):
            with self.subTest(label):
                self.assertIsNone(guard.hook(raw, self.home))
        self.assertIsNone(guard.hook(json.dumps(same).encode("utf-8")[:-1], self.home))
        self.assertNoGuardState()

    def test_oversized_input_is_ignored_without_state(self):
        payload = self.child_payload()
        payload["tool_input"]["message"] = "x" * guard.MAX_HOOK_INPUT_BYTES
        self.assertGreater(len(json.dumps(payload).encode("utf-8")), guard.MAX_HOOK_INPUT_BYTES)
        self.assertIsNone(self.run_hook(payload))
        self.assertNoGuardState()


class RecordLimitTests(GuardCase):
    def test_existing_record_at_the_cap_is_not_appended(self):
        self.set_mode("observe")
        self.events_path.parent.mkdir(parents=True)
        self.events_path.write_bytes(b"x" * guard.MAX_EVENTS_BYTES)
        before = self.events_path.read_bytes()
        self.assertIsNone(self.run_hook(self.child_payload()))
        self.assertEqual(self.events_path.read_bytes(), before)

    def test_records_append_below_the_cap(self):
        self.set_mode("observe")
        self.run_hook(self.child_payload())
        self.run_hook(self.child_payload())
        self.assertEqual(len(self.records()), 2)

    def test_symlinked_record_file_is_never_written(self):
        self.set_mode("observe")
        self.events_path.parent.mkdir(parents=True)
        target = self.base / "outside.jsonl"
        target.write_text("keep\n", encoding="utf-8")
        self.symlink_or_skip(self.events_path, target)
        self.assertIsNone(self.run_hook(self.child_payload()))
        self.assertEqual(target.read_text(encoding="utf-8"), "keep\n")

    def test_recording_failure_keeps_the_enforce_decision(self):
        self.set_mode("enforce")
        blocked_directory = self.home / guard.EVENTS_DIRECTORY_RELATIVE
        blocked_directory.parent.mkdir(parents=True, exist_ok=True)
        blocked_directory.write_text("not a directory", encoding="utf-8")
        self.assertEqual(self.run_hook(self.child_payload()), guard.deny_response())


class StatusTests(GuardCase):
    def test_status_reports_each_mode_and_configuration_presence(self):
        code, out, _ = self.status()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["mode"], "off")
        self.assertFalse(json.loads(out)["configured"])
        for mode in ("off", "observe", "enforce"):
            with self.subTest(mode=mode):
                self.set_mode(mode)
                code, out, _ = self.status()
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out)["mode"], mode)
                self.assertTrue(json.loads(out)["configured"])
        self.write_config("{bad")
        code, out, _ = self.status()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["mode"], "invalid")
        self.assertTrue(json.loads(out)["configured"])

    def test_status_counts_only_valid_fixed_records(self):
        self.set_mode("enforce")
        self.events_path.parent.mkdir(parents=True)
        same_observe = dict(VALID_RECORD, mode="observe")
        different = dict(VALID_RECORD, verdict="not_same_model", reason="different_model", would_deny=False)
        lines = [
            json.dumps(VALID_RECORD),
            json.dumps(same_observe),
            json.dumps(different),
            "{bad",
            "",
            "[]",
            json.dumps(dict(VALID_RECORD, reason="other_reason")),
            json.dumps(dict(VALID_RECORD, model=CALLER_MODEL)),
            json.dumps(dict(VALID_RECORD, schema_version=2)),
            json.dumps(dict(VALID_RECORD, would_deny="yes")),
            '{"schema_version":1,"schema_version":1,"mode":"enforce"}',
        ]
        self.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        code, out, _ = self.status()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["events"], {
            "total": 3,
            "by_verdict": {"same_model": 2, "not_same_model": 1},
            "by_reason": {"same_model": 2, "different_model": 1},
            "would_deny": 2,
            "capped": False,
        })
        self.assertNotIn(CALLER_MODEL, out)
        self.assertNotIn(str(self.home), out)

    def test_status_does_not_modify_any_file(self):
        self.set_mode("observe")
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self.events_path.write_text(json.dumps(VALID_RECORD) + "\n{bad\n", encoding="utf-8")

        def snapshot():
            return {str(path.relative_to(self.home)): path.read_bytes()
                    for path in sorted(self.home.rglob("*")) if path.is_file()}

        before = snapshot()
        self.status()
        self.assertEqual(snapshot(), before)

    def test_status_reports_capped_when_the_record_reaches_the_limit(self):
        self.set_mode("observe")
        self.events_path.parent.mkdir(parents=True)
        line = (json.dumps(VALID_RECORD, separators=(",", ":")) + "\n").encode("utf-8")
        count = guard.MAX_EVENTS_BYTES // len(line)
        padding = guard.MAX_EVENTS_BYTES - count * len(line)
        self.events_path.write_bytes(line * count + b" " * padding)
        code, out, _ = self.status()
        self.assertEqual(code, 0)
        events = json.loads(out)["events"]
        self.assertTrue(events["capped"])
        self.assertEqual(events["total"], count)

    def test_status_on_unsafe_records_fails_closed_without_output(self):
        self.set_mode("observe")
        self.events_path.parent.mkdir(parents=True)
        target = self.base / "outside.jsonl"
        target.write_text(json.dumps(VALID_RECORD) + "\n", encoding="utf-8")
        self.symlink_or_skip(self.events_path, target)
        code, out, err = self.status()
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("error:", err)


class CliHookTests(GuardCase):
    def test_hook_command_prints_one_deny_line_in_enforce_mode(self):
        self.set_mode("enforce")
        raw = json.dumps(self.child_payload()).encode("utf-8")
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(raw))), redirect_stdout(out):
            code = guard.main(["--codex-home", str(self.home), "hook"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().count("\n"), 1)
        self.assertEqual(json.loads(out.getvalue()), guard.deny_response())

    def test_oversized_stdin_is_drained_and_passes_through(self):
        self.set_mode("enforce")
        raw = b" " * (guard.MAX_HOOK_INPUT_BYTES + 100)
        stream = io.BytesIO(raw)
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", types.SimpleNamespace(buffer=stream)), redirect_stdout(out):
            code = guard.main(["--codex-home", str(self.home), "hook"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(stream.read(), b"")

    def test_bounded_reader_keeps_at_most_the_limit_plus_one_byte(self):
        stream = io.BytesIO(b"a" * (guard.MAX_HOOK_INPUT_BYTES + 5))
        self.assertEqual(len(guard._read_hook_input(stream)), guard.MAX_HOOK_INPUT_BYTES + 1)
        self.assertEqual(stream.read(), b"")


class ShippedCommandTests(GuardCase):
    def setUp(self):
        super().setUp()
        self.plugin = self.base / "plugin with spaces"
        for directory in ("scripts", "hooks"):
            shutil.copytree(PLUGIN / directory, self.plugin / directory,
                            ignore=shutil.ignore_patterns("__pycache__"))
        hooks = json.loads((self.plugin / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.command = hooks["PreToolUse"][0]["hooks"][0]["command"]

    def run_command(self, value):
        environment = {**os.environ, "CODEX_HOME": str(self.home), "PLUGIN_ROOT": str(self.plugin),
                       "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
        # This is the reviewed command shipped in this checkout, run through the shell.
        return subprocess.run(self.command, shell=True, cwd=self.base, env=environment,
                              input=json.dumps(value), text=True, encoding="utf-8",
                              capture_output=True, timeout=10)

    def test_default_off_prints_nothing_and_exits_zero(self):
        result = self.run_command(self.child_payload())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNoGuardState()

    def test_enforce_same_model_prints_deny_json(self):
        self.set_mode("enforce")
        result = self.run_command(self.child_payload())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), guard.deny_response())

    def test_observe_prints_nothing_and_records_one_line(self):
        self.set_mode("observe")
        result = self.run_command(self.child_payload())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(self.records()), 1)


class HooksJsonTests(unittest.TestCase):
    def setUp(self):
        self.hooks = json.loads((PLUGIN / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]

    def test_pretooluse_guard_is_registered_for_spawn_tools_only(self):
        self.assertTrue({"SessionStart", "SubagentStart", "PreToolUse"} <= set(self.hooks))
        groups = self.hooks["PreToolUse"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], "spawn_agent|Agent")
        self.assertEqual(len(groups[0]["hooks"]), 1)
        hook = groups[0]["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertEqual(hook["timeout"], 5)
        self.assertEqual(hook["command"], GUARD_COMMAND)
        self.assertIn("spawn_guard.py", hook["command"])
        self.assertNotIn("routing.py", hook["command"])

    def test_existing_session_and_subagent_hooks_still_run_routing(self):
        for event in ("SessionStart", "SubagentStart"):
            with self.subTest(event=event):
                command = self.hooks[event][0]["hooks"][0]["command"]
                self.assertIn("'routing.py'", command)
                self.assertNotIn("spawn_guard", command)


if __name__ == "__main__":
    unittest.main()
