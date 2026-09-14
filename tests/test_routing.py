from __future__ import annotations

import importlib.util
import io
import json
import errno
import os
import shutil
import sys
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "plugins" / "codex-task-routing" / "scripts" / "routing.py"
SPEC = importlib.util.spec_from_file_location("routing_under_test", SCRIPT)
assert SPEC and SPEC.loader
routing = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = routing
SPEC.loader.exec_module(routing)


DEFAULTS = {
    "schema_version": 1,
    "policy_revision": "2026-09-14",
    "models": {
        "luna": {"id": "gpt-5.6-luna", "min_effort": "xhigh", "default_effort": "max", "max_effort": "max"},
        "terra": {"id": "gpt-5.6-terra", "min_effort": "high", "default_effort": "xhigh", "max_effort": "max"},
        "sol": {"id": "gpt-5.6-sol", "min_effort": "medium", "default_effort": "high", "max_effort": "xhigh"},
        "astra": {"id": "gpt-6-astra", "min_effort": "high", "default_effort": "high", "max_effort": "max"},
    },
    "principles": {
        "summary": "Keep {{models.terra.default_effort}} as the normal routing baseline.",
        "policy_timing": "Delegate only after the scope is concrete.",
        "policy_handoff": "Use {{models.luna.id}} for bounded mechanical work.",
    },
}

TEMPLATES = {
    "effective.md": "# Effective\n{{models.terra.id}}\n{{principles.summary}}\n",
    "model-routing-policy.md": "# Policy\n{{principles.policy_timing}}\n{{principles.policy_handoff}}\n",
    "model-routing-catalog.md": "# Catalog\n{{models.terra.default_effort}}\n",
    "model-routing-handoff.md": "# Handoff\n{{principles.summary}}\n",
}


class RoutingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # Keep fixtures below the test directory.  ``TemporaryDirectory`` uses
        # mode 0700, which is not writable by the restricted Windows runtime.
        fixture_root = Path(__file__).resolve().parent / ".tmp-runtime-tests"
        fixture_root.mkdir(exist_ok=True)
        self.base = fixture_root / f"routing-{uuid.uuid4().hex}"
        self.base.mkdir()
        self.plugin = self.base / "plugin"
        (self.plugin / ".codex-plugin").mkdir(parents=True)
        (self.plugin / "defaults" / "templates").mkdir(parents=True)
        (self.plugin / "hooks").mkdir()
        (self.plugin / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"name": "codex-task-routing", "version": "0.1.0"}), encoding="utf-8"
        )
        (self.plugin / "defaults" / "config.json").write_text(
            json.dumps(DEFAULTS), encoding="utf-8"
        )
        for name, contents in TEMPLATES.items():
            (self.plugin / "defaults" / "templates" / name).write_text(contents, encoding="utf-8")
        (self.plugin / "hooks" / "routing-hook.py").write_text("# entrypoint\n", encoding="utf-8")
        self.home = self.base / "home"

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def write_override(self, value: str | dict) -> Path:
        path = self.home / "codex-task-routing" / "overrides.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        return path

    def policy(self, *, config: Path | None = None):
        return routing.load_policy(plugin_root=self.plugin, codex_home=self.home, config_path=config)

    def test_defaults_reproduce_and_render_recursive_tokens(self) -> None:
        policy = self.policy()
        self.assertFalse(policy.override_present)
        output = self.base / "rendered"
        references = routing.render_policy(policy, output)
        self.assertEqual(set(references), set(routing.RENDERED_FILE_NAMES))
        self.assertIn("gpt-5.6-luna", (output / "model-routing-policy.md").read_text(encoding="utf-8"))
        self.assertIn("xhigh", (output / "model-routing-handoff.md").read_text(encoding="utf-8"))
        effective = (output / "effective.md").read_text(encoding="utf-8")
        self.assertIn("gpt-5.6-terra", effective)

    def test_bundled_defaults_match_public_detailed_fixtures(self) -> None:
        policy = routing.load_policy(plugin_root=routing.PLUGIN_ROOT, codex_home=self.home)
        output = self.base / "bundled-render"
        routing.render_policy(policy, output)
        fixture_dir = Path(__file__).resolve().parent / "fixtures"
        for name in routing.DETAILED_TEMPLATE_NAMES:
            with self.subTest(name=name):
                self.assertEqual(
                    (output / name).read_text(encoding="utf-8"),
                    (fixture_dir / name).read_text(encoding="utf-8"),
                )

    def test_override_is_read_without_rewriting_and_is_reflected(self) -> None:
        override = {
            "schema_version": 1,
            "models": {"terra": {"default_effort": "max"}},
            "principles": {"policy_handoff": "Use {{models.terra.default_effort}} with care."},
        }
        path = self.write_override(override)
        before = path.read_bytes()
        policy = self.policy()
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(policy.config["models"]["terra"]["default_effort"], "max")
        self.assertEqual(
            policy.applied_override_keys,
            ("models.terra.default_effort", "principles.policy_handoff"),
        )
        self.assertIn("max", policy.render_template(policy.templates["model-routing-policy.md"]))

    def test_invalid_override_types_ranges_unknowns_and_duplicates_reject(self) -> None:
        invalid = [
            {"schema_version": True, "models": {"terra": {"default_effort": "max"}}},
            {"schema_version": 1, "models": {"terra": {"default_effort": "low"}}},
            {"schema_version": 1, "models": {"terra": {"default_effort": []}}},
            {"schema_version": 1, "models": {"terra": {"min_effort": {}}}},
            {"schema_version": 1, "models": {"terra": {"min_effort": "max"}}},
            {"schema_version": 1, "models": {"unknown": {"id": "x"}}},
            {"schema_version": 1, "principles": {"other": "value"}},
            {"schema_version": 1, "principles": {"summary": ""}},
            {"schema_version": 1, "models": []},
        ]
        for value in invalid:
            with self.subTest(value=value):
                path = self.write_override(value)
                with self.assertRaises(routing.RoutingError):
                    self.policy(config=path)
        path = self.write_override('{"schema_version":1,"models":{},"models":{}}')
        with self.assertRaises(routing.RoutingError):
            self.policy(config=path)

    def test_unknown_cyclic_and_deep_tokens_reject(self) -> None:
        config = json.loads(json.dumps(DEFAULTS))
        config["principles"]["policy_timing"] = "{{principles.missing}}"
        (self.plugin / "defaults" / "config.json").write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaises(routing.RoutingError):
            self.policy().render_template("{{principles.policy_timing}}")
        config = json.loads(json.dumps(DEFAULTS))
        for index in range(routing.MAX_TOKEN_DEPTH + 1):
            key = f"deep_{index}"
            successor = f"deep_{index + 1}"
            config["principles"][key] = f"{{{{principles.{successor}}}}}"
        config["principles"][f"deep_{routing.MAX_TOKEN_DEPTH + 1}"] = "done"
        config["principles"]["policy_timing"] = "{{principles.deep_0}}"
        (self.plugin / "defaults" / "config.json").write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaises(routing.RoutingError):
            self.policy().render_template("{{principles.policy_timing}}")
        config["principles"]["policy_timing"] = "{{principles.policy_handoff}}"
        config["principles"]["policy_handoff"] = "{{principles.policy_timing}}"
        (self.plugin / "defaults" / "config.json").write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaises(routing.RoutingError):
            self.policy().render_template("{{principles.policy_timing}}")

    def test_hash_is_stable_and_changes_for_config_or_template(self) -> None:
        first = self.policy()
        second = self.policy()
        self.assertEqual(first.content_hash, second.content_hash)
        self.write_override({"schema_version": 1, "models": {"terra": {"default_effort": "max"}}})
        self.assertNotEqual(first.content_hash, self.policy().content_hash)
        (self.home / "codex-task-routing" / "overrides.json").unlink()
        path = self.plugin / "defaults" / "templates" / "model-routing-policy.md"
        path.write_text(path.read_text(encoding="utf-8") + "Changed\n", encoding="utf-8")
        self.assertNotEqual(first.content_hash, self.policy().content_hash)

    def test_empty_managed_and_foreign_output_directories(self) -> None:
        policy = self.policy()
        empty = self.base / "empty"
        empty.mkdir()
        routing.render_policy(policy, empty)
        routing.render_policy(policy, empty)  # managed directories are refreshable
        foreign = self.base / "foreign"
        foreign.mkdir()
        (foreign / "note.txt").write_text("do not overwrite", encoding="utf-8")
        with self.assertRaises(routing.RoutingError):
            routing.render_policy(policy, foreign)

    def test_concurrent_cache_winner_is_reused_for_supported_os_errors(self) -> None:
        policy = self.policy()
        original_rename = routing.os.rename

        for collision_errno in (errno.EEXIST, errno.ENOTEMPTY):
            with self.subTest(errno=collision_errno):
                collision_home = self.base / f"home-{collision_errno}"

                def winner_publish_then_signal_collision(source, destination):
                    original_rename(source, destination)
                    raise OSError(collision_errno, "simulated concurrent cache winner")

                with patch.object(routing.os, "rename", side_effect=winner_publish_then_signal_collision):
                    cache = routing._cache_directory(collision_home, policy)
                self.assertEqual(cache.name, policy.content_hash)
                self.assertTrue(routing._is_managed_directory(cache))

        with patch.object(routing.os, "rename", side_effect=OSError(errno.EACCES, "denied")):
            with self.assertRaises(routing.RoutingError):
                routing._cache_directory(self.base / "home-denied", policy)

    def test_mutated_cache_is_not_reused_as_effective_policy(self) -> None:
        policy = self.policy()
        cache = routing._cache_directory(self.home, policy)
        (cache / "effective.md").write_text("edited cache", encoding="utf-8")
        result = routing.hook_payload(
            event="SessionStart", source="startup", plugin_root=self.plugin, codex_home=self.home, cwd=self.base
        )
        self.assertNotIn("hookSpecificOutput", result)
        self.assertNotIn("edited cache", result["systemMessage"])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unsupported")
    def test_symlink_output_is_rejected(self) -> None:
        policy = self.policy()
        real = self.base / "real"
        real.mkdir()
        link = self.base / "linked-output"
        try:
            os.symlink(real, link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(str(exc))
        with self.assertRaises(routing.RoutingError):
            routing.render_policy(policy, link)
        self.assertFalse((real / "effective.md").exists())

    def test_hook_events_cache_conflict_invalid_config_and_secret_safety(self) -> None:
        for event, source in (("SessionStart", "startup"), ("SessionStart", "resume"), ("SessionStart", "compact"), ("SubagentStart", "startup")):
            with self.subTest(event=event, source=source):
                result = routing.hook_payload(
                    event=event, source=source, plugin_root=self.plugin, codex_home=self.home, cwd=self.base
                )
                output = result["hookSpecificOutput"]
                self.assertEqual(output["hookEventName"], event)
                self.assertIn(str((self.home / "codex-task-routing" / "cache").resolve()), output["additionalContext"])
                if event == "SubagentStart":
                    self.assertIn("does not change the parent", output["additionalContext"])
        (self.base / "AGENTS.md").write_text("## モデル選定・作業内の委譲\n", encoding="utf-8")
        conflict = routing.hook_payload(
            event="SessionStart", source="startup", plugin_root=self.plugin, codex_home=self.home, cwd=self.base
        )
        self.assertNotIn("hookSpecificOutput", conflict)
        self.assertIn("AGENTS.md", conflict["systemMessage"])
        (self.base / "AGENTS.md").unlink()
        self.write_override({"schema_version": 1, "principles": {"summary": "custom summary"}})
        result = routing.hook_payload(
            event="SessionStart", source="startup", plugin_root=self.plugin, codex_home=self.home, cwd=self.base
        )
        self.assertIn("custom summary", result["hookSpecificOutput"]["additionalContext"])
        (self.plugin / "defaults" / "config.json").write_text("{", encoding="utf-8")
        failed = routing.hook_payload(
            event="SessionStart", source="startup", plugin_root=self.plugin, codex_home=self.home, cwd=self.base
        )
        self.assertNotIn("hookSpecificOutput", failed)
        self.assertIn("not applied", failed["systemMessage"])

    def test_conflicts_include_repo_ancestors_and_codex_home(self) -> None:
        repo = self.base / "repo"
        nested = repo / "nested" / "work"
        nested.mkdir(parents=True)
        (repo / ".git").mkdir()
        (repo / "AGENTS.md").write_text("model-routing-policy.md\n", encoding="utf-8")
        self.home.mkdir()
        (self.home / "AGENTS.override.md").write_text(
            "## モデル選定・作業内の委譲\n", encoding="utf-8"
        )
        expected = {
            str((repo / "AGENTS.md").resolve()),
            str((self.home / "AGENTS.override.md").resolve()),
        }
        conflicts = set(routing.detect_routing_conflicts(nested, codex_home=self.home))
        self.assertEqual(conflicts, expected)
        status = routing.status_payload(
            plugin_root=self.plugin, codex_home=self.home, cwd=nested
        )
        self.assertEqual(set(status["routing_guidance_conflicts"]), expected)
        hook = routing.hook_payload(
            event="SessionStart", source="startup", plugin_root=self.plugin, codex_home=self.home, cwd=nested
        )
        self.assertNotIn("hookSpecificOutput", hook)
        for path in expected:
            self.assertIn(path, hook["systemMessage"])

    def test_hook_input_accepts_bom_and_rejects_non_string_events(self) -> None:
        old_stdin = sys.stdin
        try:
            sys.stdin = io.TextIOWrapper(
                io.BytesIO(b"\xef\xbb\xbf{\"hook_event_name\":\"SessionStart\",\"source\":\"resume\"}"),
                encoding="utf-8",
            )
            self.assertEqual(routing._read_hook_event(), ("SessionStart", "resume", None))
            for event in ([], {}):
                sys.stdin = io.TextIOWrapper(
                    io.BytesIO(json.dumps({"hook_event_name": event}).encode("utf-8")),
                    encoding="utf-8",
                )
                with self.subTest(event=event), self.assertRaises(routing.RoutingError):
                    routing._read_hook_event()
            deeply_nested = '{"hook_event_name":' + ("[" * 1100) + ("]" * 1100) + "}"
            sys.stdin = io.TextIOWrapper(io.BytesIO(deeply_nested.encode("utf-8")), encoding="utf-8")
            with self.assertRaises(routing.RoutingError):
                routing._read_hook_event()
            sys.stdin = io.TextIOWrapper(
                io.BytesIO(
                    json.dumps(
                        {"hook_event_name": "SessionStart", "cwd": "relative/path"}
                    ).encode("utf-8")
                ),
                encoding="utf-8",
            )
            with self.assertRaises(routing.RoutingError):
                routing._read_hook_event()
        finally:
            sys.stdin = old_stdin

    def test_hook_cli_uses_payload_cwd_for_conflicts(self) -> None:
        repo = self.base / "payload-cwd-repo"
        nested = repo / "nested"
        nested.mkdir(parents=True)
        (repo / ".git").mkdir()
        conflict_file = repo / "AGENTS.md"
        conflict_file.write_text("model-routing-policy.md\n", encoding="utf-8")
        old_stdin = sys.stdin
        try:
            sys.stdin = io.TextIOWrapper(
                io.BytesIO(
                    json.dumps(
                        {"hook_event_name": "SessionStart", "cwd": str(nested.resolve())}
                    ).encode("utf-8")
                ),
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()) as stdout:
                exit_code = routing.main(["--codex-home", str(self.home), "hook"])
            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertNotIn("hookSpecificOutput", payload)
            self.assertIn(str(conflict_file.resolve()), payload["systemMessage"])
        finally:
            sys.stdin = old_stdin

    def test_render_cli_hides_os_errors(self) -> None:
        with patch.object(routing, "render_policy", side_effect=OSError(errno.EACCES, "denied")):
            with redirect_stderr(io.StringIO()) as stderr:
                exit_code = routing.main(
                    [
                        "--codex-home",
                        str(self.home),
                        "render",
                        "--output-dir",
                        str(self.base / "render-output"),
                    ]
                )
        self.assertEqual(exit_code, 2)
        self.assertIn("safe rendering failed", stderr.getvalue())
        self.assertNotIn("denied", stderr.getvalue())

    def test_invalid_tokens_make_status_fail_visible(self) -> None:
        template = self.plugin / "defaults" / "templates" / "model-routing-policy.md"
        template.write_text("{{principles.not_present}}", encoding="utf-8")
        result = routing.status_payload(plugin_root=self.plugin, codex_home=self.home, cwd=self.base)
        self.assertFalse(result["ok"])
        self.assertNotIn("not_present", json.dumps(result))

    def test_oversized_session_context_is_not_partially_injected(self) -> None:
        oversized = "x" * (routing.MAX_ADDITIONAL_CONTEXT_CHARS + 100)
        self.write_override({"schema_version": 1, "principles": {"summary": oversized}})
        result = routing.hook_payload(
            event="SessionStart", source="startup", plugin_root=self.plugin, codex_home=self.home, cwd=self.base
        )
        self.assertNotIn("hookSpecificOutput", result)
        self.assertNotIn(oversized, json.dumps(result))

    def test_hook_cli_rejects_invalid_payload_without_echoing_it(self) -> None:
        old_stdin = sys.stdin
        try:
            sys.stdin = io.TextIOWrapper(io.BytesIO(b'{"token":"secret-value-never-stdout"}'), encoding="utf-8")
            with redirect_stdout(io.StringIO()) as stdout:
                exit_code = routing.main(["--codex-home", str(self.home), "hook"])
            self.assertEqual(exit_code, 0)
            self.assertNotIn("secret-value-never-stdout", stdout.getvalue())
            self.assertIn("systemMessage", json.loads(stdout.getvalue()))
        finally:
            sys.stdin = old_stdin

    def test_status_marks_runtime_state_unknown(self) -> None:
        result = routing.status_payload(plugin_root=self.plugin, codex_home=self.home, cwd=self.base)
        self.assertTrue(result["ok"])
        self.assertEqual(result["host"]["trust_state"], "unknown")
        self.assertTrue(result["bundled_hook_available"])
        (self.plugin / "defaults" / "config.json").write_text(
            '{"schema_version":1,"policy_revision":"secret-value-never-stdout"}', encoding="utf-8"
        )
        invalid = routing.status_payload(plugin_root=self.plugin, codex_home=self.home, cwd=self.base)
        self.assertFalse(invalid["ok"])
        self.assertNotIn("secret-value-never-stdout", json.dumps(invalid))


if __name__ == "__main__":
    unittest.main()
