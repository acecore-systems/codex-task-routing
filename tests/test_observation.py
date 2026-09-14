from __future__ import annotations

import importlib.util
import io
import json
import shutil
import subprocess
import sys
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "plugins" / "codex-task-routing" / "scripts" / "routing.py"
SPEC = importlib.util.spec_from_file_location("routing_observation_under_test", SCRIPT)
assert SPEC and SPEC.loader
routing = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = routing
SPEC.loader.exec_module(routing)


def missing_usage(reason: str = "final parent turn is still active") -> dict:
    return {
        "state": "missing",
        "reason": reason,
        "evidence_ref": None,
        "turn_scope": "full_turn",
        "input_tokens": None,
        "cache_input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "unknown_reasons": {},
    }


def measured_usage() -> dict:
    return {
        "state": "measured",
        "reason": None,
        "evidence_ref": "tool-result:child-1",
        "turn_scope": "partial_turn",
        "input_tokens": 100,
        "cache_input_tokens": 40,
        "output_tokens": 30,
        "reasoning_tokens": 20,
        "unknown_reasons": {},
    }


def parent_ongoing_run() -> dict:
    return {
        "run_key": "parent-final",
        "relationship": "parent",
        "state": "ongoing",
        "run_id": None,
        "run_id_reason": "the parent has no final run identifier yet",
        "requested": {"model": "gpt-6-astra", "effort": "high", "reason": None},
        "observed": {
            "model": None,
            "effort": None,
            "evidence_ref": None,
            "reason": "the final parent turn is still active",
        },
        "usage": missing_usage(),
    }


def completed_child_run() -> dict:
    return {
        "run_key": "child-implementation",
        "relationship": "child",
        "state": "completed",
        "run_id": "019c0000-0000-7000-8000-000000000001",
        "run_id_reason": None,
        "requested": {"model": "gpt-5.6-terra", "effort": "xhigh", "reason": None},
        "observed": {
            "model": "gpt-5.6-terra",
            "effort": "xhigh",
            "evidence_ref": "tool-result:child-1",
            "reason": None,
        },
        "usage": measured_usage(),
    }


def missing_completion() -> dict:
    return {
        "state": "completed",
        "scope": "対象作業全体を閉じ、親の final turn は未完了のため最終利用量を取得していない。",
        "measurement": {"state": "missing", "reason": "final parent usage is unavailable"},
        "verification": {"reference": None, "reason": "no verification result was recorded"},
        "rework": "なし",
        "runs": [],
    }


def partial_completion() -> dict:
    return {
        "state": "completed",
        "scope": "実装と子の検証までを記録し、親の final turn は進行中のため親利用量は今回の範囲に含めない。",
        "measurement": {"state": "partial", "reason": "parent final usage remains unavailable"},
        "verification": {"reference": "tests/test_observation.py", "reason": None},
        "rework": "子の検証結果を受けて親の受入を一度確認した。",
        "runs": [parent_ongoing_run(), completed_child_run()],
    }


class ObservationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        fixture_root = Path(__file__).resolve().parent / ".tmp-runtime-tests"
        fixture_root.mkdir(exist_ok=True)
        self.base = fixture_root / f"observation-{uuid.uuid4().hex}"
        self.base.mkdir()
        self.plugin = self.base / "plugin"
        shutil.copytree(
            routing.PLUGIN_ROOT,
            self.plugin,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        self.home = self.base / "home"

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def policy(self):
        return routing.load_policy(plugin_root=self.plugin, codex_home=self.home)

    def sampling_key(self, *, policy=None) -> str:
        return routing.observation_sampling_key(policy or self.policy())

    def state_path(self, *, policy=None) -> Path:
        return self.home / "codex-task-routing" / "observations" / f"{self.sampling_key(policy=policy)}.json"

    def start(self, task_id: str, *, policy=None, kind: str = "normal") -> dict:
        return routing.observation_start(
            codex_home=self.home,
            policy=policy or self.policy(),
            task_id=task_id,
            kind=kind,
        )

    def complete(self, task_id: str, completion: dict, *, policy=None) -> dict:
        return routing.observation_complete(
            codex_home=self.home,
            sampling_key=self.sampling_key(policy=policy),
            task_id=task_id,
            completion=completion,
        )

    def test_start_complete_replay_and_missing_are_separate_from_closed(self) -> None:
        policy = self.policy()
        self.assertEqual(self.start("parent-thread:start-turn", policy=policy)["state"], "started")
        self.assertEqual(self.start("parent-thread:start-turn", policy=policy)["state"], "existing")
        pending = routing.observation_status(codex_home=self.home, policy=policy)
        self.assertEqual(
            {key: pending[key] for key in ("recorded", "remaining", "pending", "completed", "missing")},
            {"recorded": 1, "remaining": 2, "pending": 1, "completed": 0, "missing": 0},
        )

        closed = self.complete("parent-thread:start-turn", missing_completion(), policy=policy)
        self.assertEqual(closed["observation_state"], "completed")
        self.assertEqual(closed["measurement_state"], "missing")
        # Completion retransmission updates the same entry rather than using a fourth record.
        self.complete("parent-thread:start-turn", missing_completion(), policy=policy)
        status = routing.observation_status(codex_home=self.home, policy=policy)
        self.assertEqual(
            {key: status[key] for key in ("recorded", "remaining", "pending", "completed", "missing")},
            {"recorded": 1, "remaining": 2, "pending": 0, "completed": 1, "missing": 1},
        )

    def test_partial_parent_and_completed_child_do_not_claim_full_measurement(self) -> None:
        policy = self.policy()
        self.start("parent-thread:with-child", policy=policy)
        result = self.complete("parent-thread:with-child", partial_completion(), policy=policy)
        self.assertEqual(result["measurement_state"], "partial")
        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        record = next(iter(next(iter(state["samples"].values()))["tasks"].values()))
        self.assertEqual(record["runs"][0]["state"], "ongoing")
        self.assertEqual(record["runs"][0]["usage"]["state"], "missing")
        self.assertEqual(record["runs"][1]["usage"]["turn_scope"], "partial_turn")
        self.assertEqual(routing.observation_status(codex_home=self.home, policy=policy)["missing"], 1)

    def test_rejects_empty_measured_record_and_usage_in_an_unfinished_run(self) -> None:
        policy = self.policy()
        self.start("parent-thread:invalid", policy=policy)
        empty_measured = missing_completion()
        empty_measured["measurement"] = {"state": "measured", "reason": None}
        with self.assertRaises(routing.RoutingError):
            self.complete("parent-thread:invalid", empty_measured, policy=policy)

        invalid = partial_completion()
        invalid["runs"][0]["usage"] = measured_usage()
        with self.assertRaises(routing.RoutingError):
            self.complete("parent-thread:invalid", invalid, policy=policy)
        self.assertEqual(routing.observation_status(codex_home=self.home, policy=policy)["pending"], 1)

    def test_partial_ranges_and_unknown_counters_remain_missing_in_status(self) -> None:
        self.start("coverage")
        value = partial_completion()
        parent = completed_child_run()
        parent.update(run_key="parent-completed", relationship="parent")
        value["runs"] = [parent]
        self.complete("coverage", value)
        self.assertEqual(routing.observation_status(codex_home=self.home, policy=self.policy())["missing"], 1)
        value["measurement"] = {"state": "measured", "reason": None}
        with self.assertRaises(routing.RoutingError):
            self.complete("coverage", value)
        parent["usage"]["turn_scope"] = "full_turn"
        parent["usage"]["cache_input_tokens"] = None
        parent["usage"]["unknown_reasons"] = {"cache_input_tokens": "provider did not report caching"}
        with self.assertRaises(routing.RoutingError):
            self.complete("coverage", value)
        parent["usage"]["cache_input_tokens"] = 40
        parent["usage"]["unknown_reasons"] = {}
        self.complete("coverage", value)
        self.assertEqual(routing.observation_status(codex_home=self.home, policy=self.policy())["missing"], 0)

    def test_completion_replay_cannot_erase_previous_runs_or_known_values(self) -> None:
        self.start("retained-evidence")
        value = partial_completion()
        self.complete("retained-evidence", value)
        before = self.state_path().read_bytes()
        with self.assertRaises(routing.RoutingError):
            self.complete("retained-evidence", missing_completion())
        value["runs"][1]["usage"]["cache_input_tokens"] = None
        value["runs"][1]["usage"]["unknown_reasons"] = {"cache_input_tokens": "not included in this follow-up"}
        with self.assertRaises(routing.RoutingError):
            self.complete("retained-evidence", value)
        value = partial_completion()
        value["verification"] = {"reference": None, "reason": "not present in this follow-up"}
        with self.assertRaises(routing.RoutingError):
            self.complete("retained-evidence", value)
        self.assertEqual(self.state_path().read_bytes(), before)

    def test_three_reservations_skip_non_normal_work_and_do_not_create_a_fourth(self) -> None:
        policy = self.policy()
        self.assertEqual(self.start("audit-check", policy=policy, kind="audit")["state"], "skipped")
        self.assertFalse(self.state_path().exists())
        for index in range(3):
            self.assertEqual(self.start(f"normal-{index}", policy=policy)["state"], "started")
        before = self.state_path().read_bytes()
        self.assertEqual(self.start("normal-four", policy=policy)["state"], "full")
        self.assertEqual(self.state_path().read_bytes(), before)
        status = routing.observation_status(codex_home=self.home, policy=policy)
        self.assertEqual((status["recorded"], status["remaining"], status["pending"]), (3, 0, 3))

        hook = routing.hook_payload(
            event="SessionStart", source="startup", plugin_root=self.plugin,
            codex_home=self.home, cwd=self.base,
        )
        context = hook["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Observation capacity is full", context)
        self.assertNotIn("observation start --task-id", context)

    def test_sampling_groups_are_isolated_across_policy_changes(self) -> None:
        first = self.policy()
        first_key = routing.observation_sampling_key(first)
        self.start("same-render-a", policy=first)
        manifest = self.plugin / ".codex-plugin" / "plugin.json"
        manifest_value = json.loads(manifest.read_text(encoding="utf-8"))
        manifest_value["version"] = "99.0.0"
        manifest.write_text(json.dumps(manifest_value), encoding="utf-8")
        version_only = self.policy()
        self.assertNotEqual(first.content_hash, version_only.content_hash)
        self.assertEqual(first_key, routing.observation_sampling_key(version_only))
        self.start("same-render-b", policy=version_only)
        self.start("same-render-c", policy=version_only)
        self.assertEqual(routing.observation_status(codex_home=self.home, policy=version_only)["recorded"], 3)

        effective = self.plugin / "defaults" / "templates" / "effective.md"
        effective.write_text(effective.read_text(encoding="utf-8") + "Policy changed.\n", encoding="utf-8")
        changed = self.policy()
        changed_key = routing.observation_sampling_key(changed)
        self.assertNotEqual(first_key, changed_key)
        self.start("changed-policy-c", policy=changed)
        self.assertEqual(routing.observation_status(codex_home=self.home, policy=changed)["recorded"], 1)
        old_path = self.state_path(policy=first)
        changed_path = self.state_path(policy=changed)
        self.assertTrue(old_path.exists())
        self.assertTrue(changed_path.exists())
        self.assertNotEqual(old_path, changed_path)
        self.assertEqual(set(json.loads(old_path.read_text(encoding="utf-8"))["samples"]), {first_key})
        self.assertEqual(set(json.loads(changed_path.read_text(encoding="utf-8"))["samples"]), {changed_key})
        self.complete("changed-policy-c", missing_completion(), policy=changed)
        changed_path.write_text("{", encoding="utf-8")
        unreadable_other_group = changed_path.read_bytes()
        self.complete("same-render-a", missing_completion(), policy=first)
        self.assertEqual(changed_path.read_bytes(), unreadable_other_group)
        self.assertEqual(
            routing.observation_status(codex_home=self.home, sampling_key=first_key)["recorded"], 3
        )

    def test_bad_input_corruption_and_write_conflict_preserve_existing_state(self) -> None:
        policy = self.policy()
        self.start("parent-thread:safe", policy=policy)
        before = self.state_path().read_bytes()
        invalid = partial_completion()
        invalid["runs"][1]["usage"]["cache_input_tokens"] = 101
        with self.assertRaises(routing.RoutingError):
            self.complete("parent-thread:safe", invalid, policy=policy)
        self.assertEqual(self.state_path().read_bytes(), before)

        with routing._observation_write_lock(self.home, self.sampling_key(policy=policy)):
            with self.assertRaises(routing.RoutingError):
                self.start("parent-thread:blocked", policy=policy)
        self.assertEqual(self.state_path().read_bytes(), before)

        self.state_path().write_text("{", encoding="utf-8")
        self.assertEqual(routing.observation_status(codex_home=self.home, policy=policy)["state"], "unknown")
        corrupt = self.state_path().read_bytes()
        with self.assertRaises(routing.RoutingError):
            self.start("parent-thread:corrupt", policy=policy)
        self.assertEqual(self.state_path().read_bytes(), corrupt)
        hook = routing.hook_payload(
            event="SessionStart",
            source="startup",
            plugin_root=self.plugin,
            codex_home=self.home,
            cwd=self.base,
        )
        context = hook["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Observation sample state is unknown", context)
        self.assertIn("Effective policy:", context)

    def test_process_exit_releases_lock_without_manual_cleanup(self) -> None:
        code = (
            "import os,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); import routing\n"
            "with routing._observation_write_lock(Path(sys.argv[2])): os._exit(0)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(SCRIPT.parent), str(self.home)],
            capture_output=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / routing.OBSERVATIONS_RELATIVE / routing.OBSERVATION_LOCK_NAME).exists())
        self.assertEqual(self.start("after-exit")["state"], "started")

    def test_cli_start_complete_and_status_accept_the_documented_json_flag(self) -> None:
        completion_path = self.base / "completion.json"
        completion_path.write_text(json.dumps(missing_completion()), encoding="utf-8")
        with redirect_stdout(io.StringIO()) as stdout:
            exit_code = routing.main(
                [
                    "--codex-home", str(self.home), "observation", "start",
                    "--task-id", "cli-parent:start", "--kind", "normal", "--json",
                ]
            )
        self.assertEqual(exit_code, 0)
        started = json.loads(stdout.getvalue())
        self.assertEqual(started["state"], "started")
        sampling_key = started["sampling_key"]
        with redirect_stdout(io.StringIO()) as stdout:
            exit_code = routing.main(
                [
                    "--codex-home", str(self.home), "observation", "complete",
                    "--sampling-key", sampling_key, "--task-id", "cli-parent:start", "--input", str(completion_path),
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["measurement_state"], "missing")
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()):
            exit_code = routing.main(
                ["--codex-home", str(self.home), "observation", "status", "--json"]
            )
        self.assertEqual(exit_code, 0)
        status = json.loads(stdout.getvalue())
        self.assertEqual((status["completed"], status["missing"]), (1, 1))
        with redirect_stdout(io.StringIO()) as stdout:
            exit_code = routing.main([
                "--codex-home", str(self.home), "observation", "status", "--json",
                "--sampling-key", sampling_key, "--task-id", "cli-parent:start",
            ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["record"]["task_id"], "cli-parent:start")


if __name__ == "__main__":
    unittest.main()
