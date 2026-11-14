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
        for status in ({"status": "unavailable", "errorCode": "nace_drex_history_invalid"},
                       {"status": "waiting_for_local_worker"},
                       {**self.status, "stage": "outcome_uncertain"}):
            for enabled in (True, False):
                with self.subTest(status=status, enabled=enabled):
                    plan = drex.prepare({**self.facts, "target_authorized": False, "status": status}, enabled)
                    self.assertEqual(plan, {"ready": False, "next_action": "reconcile_existing_request"})

    def test_verify_checks_the_typed_native_projection(self):
        request = {**self.request, "questions": self.request["questions"] + [
            {"name": "category", "type": "choice", "instructions": "Classify",
             "choices": [{"value": "a"}, {"value": "b"}]},
            {"name": "rating", "type": "score", "instructions": "Score",
             "levels": [{"label": "low"}, {"label": "high"}]},
        ]}
        empty = dict.fromkeys(("choice", "probability", "score", "confidence", "probabilities"))
        distribution = [{"value": "a", "probability": 0.75}, {"value": "b", "probability": 0.25}]
        answers = [
            {**empty, "name": "check", "type": "predicate", "probability": 0.95},
            {**empty, "name": "category", "type": "choice", "choice": "a",
             "confidence": 0.8, "probabilities": distribution},
            {**empty, "name": "rating", "type": "score", "score": 0.25},
        ]
        result = {
            "preset": "nace-drex-local", "requestSha256": drex.request_binding(request)[1],
            "model": "drex-max-v1.1", "stage": "completed", "answers": answers,
            "inputTokens": 10, "elapsedMs": 30, "credentialPresent": None,
            "errorCategory": None, "valuesReturned": False,
        }
        self.assertTrue(drex.verify({"request": request, "result": result})["verified"])
        result_faults = (
            {"stage": "outcome_uncertain"}, {"model": "drex-latest"}, {"requestSha256": "a" * 64},
            {"answers": []}, {"answers": answers[::-1]}, {"inputTokens": True}, {"credentialPresent": True},
            {"raw": "unexpected"},
        )
        for fault in result_faults:
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                drex.verify({"request": request, "result": {**result, **fault}})
        for index, delta in [
            *((0, {"probability": value}) for value in
              (None, True, "0.95", float("nan"), float("inf"), float("-inf"), -0.1, 1.1)),
            (0, {"name": "other"}), (0, {"type": "choice"}), (0, {"score": 0.5}),
            (1, {"choice": "c"}), (1, {"confidence": -1}),
            (1, {"probabilities": distribution[:1]}), (1, {"probabilities": [distribution[0]] * 2}),
            (1, {"probabilities": [distribution[0], {"value": "b", "probability": 0.5}]}),
            (1, {"probabilities": [distribution[0], {"value": "c", "probability": 0.25}]}),
            (2, {"score": 1.5}), (2, {"score": True}), (2, {"confidence": 0.9}), (2, {"extra": 1}),
        ]:
            altered = answers.copy()
            altered[index] = {**altered[index], **delta}
            with self.subTest(index=index, delta=delta), self.assertRaises(ValueError):
                drex.verify({"request": request, "result": {**result, "answers": altered}})
        no_probability = {key: value for key, value in answers[0].items() if key != "probability"}
        with self.assertRaises(ValueError):
            drex.verify({"request": request, "result": {**result, "answers": [no_probability, *answers[1:]]}})
