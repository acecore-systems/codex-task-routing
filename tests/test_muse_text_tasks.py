import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/codex-task-routing/scripts"))
import muse_text_tasks as muse
import routing


class MuseTextTasksTest(unittest.TestCase):
    def setUp(self):
        self.prompt = "Summarize these two synthetic observations."
        self.sha = hashlib.sha256(self.prompt.encode()).hexdigest()
        self.status = {"state": "not_started", "preset": muse.PRESET, "inputSha256": self.sha}
        self.facts = dict(schema_version=1, actor="root", data_class="public_or_synthetic",
                          purpose="summarize", prompt=self.prompt, prompt_sha256=self.sha,
                          status=self.status, **dict.fromkeys(muse.FLAGS, True))

    def test_prepare_only_for_matching_scope_and_status(self):
        prepared = muse.prepare(self.facts)
        self.assertTrue(prepared["ready"])
        self.assertEqual(prepared["tool"], muse.TOOL)
        self.assertTrue(prepared["arguments"]["explicitApproval"])
        for key, value in [("actor", "child"), ("purpose", "web_search"), ("data_class", "private"),
                           ("cost_authorized", False), ("target_authorized", False),
                           ("status", {"state": "not_started", "inputSha256": self.sha})]:
            with self.subTest(key=key):
                self.assertFalse(muse.prepare({**self.facts, key: value})["ready"])
        with self.assertRaises(ValueError):
            muse.prepare({**self.facts, "prompt": "different input"})

    def test_existing_intent_and_completion_never_redispatch(self):
        for state, expected in [("intent", "reconcile_existing_request"),
                                ("unverified", "reconcile_existing_request"),
                                ("completed", "review_existing_result")]:
            with self.subTest(state=state):
                result = muse.prepare({**self.facts, "status": {**self.status, "state": state}})
                self.assertFalse(result["ready"])
                self.assertEqual(result["next_action"], expected)

    def test_verify_binds_provider_and_acceptance(self):
        result = dict(state="completed", preset=muse.PRESET, inputSha256=self.sha,
                      model="muse-ember-1.0", effort=None, inputTokens=9, outputTokens=3,
                      seconds=1.2, text="One result.")
        facts = dict(schema_version=1, prompt_sha256=self.sha, result=result)
        observed = muse.verify(facts)
        self.assertIsNone(observed["observed_effort"])
        self.assertFalse(observed["accepted"])
        for key, value in [("preset", "other-adapter"), ("model", "other"), ("effort", "low"),
                           ("inputSha256", "0" * 64)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                muse.verify({**facts, "result": {**result, key: value}})

    def test_only_root_start_hook_opts_in(self):
        with tempfile.TemporaryDirectory() as path:
            home = Path(path)
            phrase = "prefer Muse Ember for bounded summaries"

            def context(event):
                result = routing.hook_payload(event=event, source="startup", codex_home=home, cwd=home)
                return result["hookSpecificOutput"]["additionalContext"]

            self.assertNotIn(phrase, context("SessionStart"))
            config = home / muse.CONFIG_RELATIVE
            config.parent.mkdir(exist_ok=True)
            config.write_text('{"schema_version":1,"enabled":true}', encoding="utf-8")
            self.assertIn(phrase, context("SessionStart"))
            self.assertNotIn(phrase, context("SubagentStart"))
