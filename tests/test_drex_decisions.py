import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/codex-task-routing/scripts"))
import drex_decisions as drex


class DrexDecisionsTests(unittest.TestCase):
    def setUp(self):
        self.request = {
            "input": "合成ケース",
            "questions": [{"type": "predicate", "name": "check", "instructions": "Check the text"}],
        }
        self.facts = {
            "request": self.request,
            "actor": "root",
            "data_class": "public_or_synthetic",
            **dict.fromkeys(drex.APPROVALS, True),
        }
        self.payload, self.digest = drex.request_binding(self.request)
        self.status = {"requestSha256": self.digest, "stage": "not_started",
                       "credentialPresent": True, "valuesReturned": False}

    def test_status_then_one_run_for_the_bound_request(self):
        lookup = drex.prepare(self.facts, True)
        self.assertEqual(lookup["next_action"], "check_request_status")
        self.assertEqual(lookup["tool"], "local_auth_nace_decisions")
        self.assertEqual(lookup["arguments"], {
            "operation": "status", "requestSha256": self.digest, "requestJson": self.payload.decode("utf-8"),
        })
        self.assertEqual(json.loads(self.payload), self.request)
        self.assertEqual(drex.request_binding(dict(reversed(list(self.request.items()))))[1], self.digest)

        prepared = drex.prepare({**self.facts, "status": self.status}, True)
        self.assertTrue(prepared["ready"])
        self.assertEqual(prepared["arguments"], {
            **lookup["arguments"], "operation": "run", "dataClass": "public_or_synthetic", "explicitApproval": True,
        })
        with self.assertRaises(ValueError):
            drex.prepare({**self.facts, "status": {**self.status, "requestSha256": "0" * 64}}, True)

    def test_disabled_approval_and_receipt_states(self):
        self.assertFalse(drex.prepare(self.facts, False)["ready"])
        for key, value in [("actor", "child"), ("data_class", "private"),
                           *((key, False) for key in drex.APPROVALS)]:
            with self.subTest(key=key):
                self.assertEqual(drex.prepare({**self.facts, key: value}, True)["next_action"], "use_existing_policy")
        missing = {**self.status, "credentialPresent": False}
        self.assertEqual(drex.prepare({**self.facts, "status": missing}, True)["next_action"], "adapter_unavailable")
        for stage, expected in (("completed", "review_existing_result"), ("outcome_uncertain", "reconcile_existing_request"),
                                ("response_invalid", "reconcile_existing_request"), ("provider_error", "reconcile_existing_request")):
            with self.subTest(stage=stage):
                plan = drex.prepare({**self.facts, "status": {**self.status, "stage": stage}}, True)
                self.assertEqual(plan["next_action"], expected)
                self.assertNotIn("arguments", plan)

    def test_verify_matches_model_digest_and_questions(self):
        result = {**self.status, "stage": "completed", "model": "drex-max-v1.1",
                  "answers": [{"name": "check", "type": "predicate", "probability": 0.95}]}
        self.assertTrue(drex.verify({"request": self.request, "result": result})["verified"])
        for change in ({"stage": "outcome_uncertain"}, {"model": "drex-latest"}, {"requestSha256": "a" * 64},
                       {"answers": []}, {"answers": [{"name": "another", "type": "predicate"}]},
                       {"answers": [{"name": "check", "type": "choice"}]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                drex.verify({"request": self.request, "result": {**result, **change}})
