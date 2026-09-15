"""The optional route must not change default routing or start chat from children."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("routing_chat_hook", ROOT / "plugins/codex-task-routing/scripts/routing.py")
routing = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = routing
SPEC.loader.exec_module(routing)


class ChatHookTests(unittest.TestCase):
    def setUp(self):
        self.home = ROOT / "tests/.tmp-runtime-tests" / ("chat-hook-" + uuid.uuid4().hex)
        self.home.mkdir(parents=True)

    def tearDown(self):
        # Only this test's verified descendant, never a computed external path.
        assert self.home.parent == ROOT / "tests/.tmp-runtime-tests"
        shutil.rmtree(self.home)

    def settings(self, value):
        path = self.home / "codex-task-routing/chatgpt.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def payload(self, event="SessionStart"):
        return routing.hook_payload(event=event, source="startup", codex_home=self.home, cwd=self.home)

    def test_missing_and_disabled_do_not_advertise_chat(self):
        before = self.payload()
        self.assertNotIn("Opt-in ChatGPT", before["hookSpecificOutput"]["additionalContext"])
        self.settings({"schema_version": 1, "enabled": False, "required_model": "6 Pro", "transport": "browser-temporary"})
        self.assertEqual(before, self.payload())

    def test_enabled_only_advertised_to_session_root(self):
        self.settings({"schema_version": 1, "enabled": True, "required_model": "6 Pro", "transport": "browser-temporary"})
        parent = self.payload()["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Opt-in ChatGPT Chat route is enabled", parent)
        self.assertIn("transport: browser-temporary", parent)
        self.assertIn("Temporary Chat", parent)
        self.assertIn("evaluate this Chat route first", parent)
        self.assertIn("verified tools", parent)
        self.assertIn("Parallel delegation requires useful independent parent work", parent)
        self.assertIn("Serial specialist delegation requires explicit host permission", parent)
        self.assertIn("implementation/testing/PR", parent)
        self.assertIn("unknown authorization blocks dispatch", parent)
        self.assertIn("never use send_message_to_thread", parent)
        self.assertIn("never Work or a model API", parent)
        self.assertLessEqual(len(parent), routing.MAX_ADDITIONAL_CONTEXT_CHARS)
        child = self.payload("SubagentStart")["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("Opt-in ChatGPT", child)
        self.assertNotIn("evaluate this Chat route first", child)

    def test_invalid_route_does_not_disable_standard_policy_or_echo_values(self):
        self.settings({"schema_version": 1, "enabled": True, "required_model": "secret-value", "transport": "api"})
        payload = self.payload()
        self.assertIn("policy hash", payload["hookSpecificOutput"]["additionalContext"])
        self.assertIn("ChatGPT route not applied", payload["systemMessage"])
        self.assertNotIn("secret-value", json.dumps(payload))

    def test_turning_off_restores_policy_and_keeps_observation_sample(self):
        policy = routing.load_policy(codex_home=self.home)
        routing.observation_start(codex_home=self.home, policy=policy, task_id="retained-task")
        settings = {"schema_version": 1, "enabled": True, "required_model": "6 Pro", "transport": "browser-temporary"}
        self.settings(settings)
        self.assertIn("Opt-in ChatGPT", self.payload()["hookSpecificOutput"]["additionalContext"])
        settings["enabled"] = False
        self.settings(settings)
        off = self.payload()
        self.assertNotIn("Opt-in ChatGPT", off["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("systemMessage", off)
        status = routing.observation_status(codex_home=self.home, policy=policy)
        self.assertEqual(status["task_ids"], ["retained-task"])
        self.assertEqual(status["remaining"], 2)
        self.assertEqual(routing.load_policy(codex_home=self.home).content_hash, policy.content_hash)

    def test_explicit_legacy_transport_remains_available_but_is_not_temporary(self):
        self.settings({"schema_version": 1, "enabled": True, "required_model": "6 Pro", "transport": "codex-app-tools"})
        context = self.payload()["hookSpecificOutput"]["additionalContext"]
        self.assertIn("transport: codex-app-tools (legacy)", context)
        self.assertIn("cannot start or operate a Temporary Chat", context)


if __name__ == "__main__":
    unittest.main()
