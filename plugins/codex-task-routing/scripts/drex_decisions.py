#!/usr/bin/env python3
"""Prepare and verify opt-in Drex Decisions requests without sending them."""
from __future__ import annotations

import argparse
import hashlib
import json
from math import fsum
import os
from pathlib import Path
import sys

from routing_core.common import RoutingError, _absolute, _assert_safe_ancestors, _canonical_json, _load_json

CONFIG_RELATIVE = Path("codex-task-routing") / "drex-decisions.json"
MODEL = "drex-max-v1.1"
TOOL = "local_auth_nace_decisions"
APPROVALS = ("host_authorized", "target_authorized", "materials_authorized", "cost_authorized")
RESULT_FIELDS = {"preset", "requestSha256", "model", "stage", "answers", "inputTokens", "elapsedMs",
                 "errorCategory", "credentialPresent", "valuesReturned"}
ANSWER_VALUES = {
    "predicate": {"probability"},
    "choice": {"choice", "confidence", "probabilities"},
    "score": {"score"},
}
ANSWER_FIELDS = {"name", "type", "choice", "probability", "score", "confidence", "probabilities"}


def load_config(home: Path) -> bool:
    path = _assert_safe_ancestors(_absolute(home) / CONFIG_RELATIVE)
    if not os.path.lexists(path):
        return False
    config = _load_json(path, "Drex Decisions configuration", limit=4096)
    if (set(config) != {"schema_version", "enabled"} or type(config["schema_version"]) is not int
            or config["schema_version"] != 1 or type(config["enabled"]) is not bool):
        raise RoutingError("invalid Drex Decisions configuration")
    return config["enabled"]


def request_binding(request: dict) -> tuple[bytes, str]:
    if (not isinstance(request, dict) or set(request) != {"input", "questions"}
            or not isinstance(request["input"], str) or not request["input"].strip()
            or len(request["input"].encode("utf-8")) > 32768):
        raise ValueError("invalid decision input")
    questions = request["questions"]
    if not isinstance(questions, list) or not 1 <= len(questions) <= 16:
        raise ValueError("invalid decision questions")
    names = set()
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError("invalid decision question")
        name = question.get("name")
        if (not isinstance(name, str) or not name or name in names
                or question.get("type") not in ("choice", "predicate", "score")):
            raise ValueError("invalid decision question")
        names.add(name)
    payload = _canonical_json(request)
    if len(payload) > 65536:
        raise ValueError("decision request too large")
    return payload, hashlib.sha256(payload).hexdigest()


def prepare(facts: dict, enabled: bool) -> dict:
    status = facts.get("status")
    if status is not None:
        if not isinstance(status, dict):
            raise ValueError("invalid decision status")
        if status.get("status") in ("unavailable", "waiting_for_local_worker"):
            return {"ready": False, "next_action": "reconcile_existing_request"}
        payload, digest = request_binding(facts["request"])
        if status.get("requestSha256") != digest or status.get("valuesReturned") is not False:
            raise ValueError("decision status does not match request")
        if status.get("stage") == "completed":
            return {"ready": False, "next_action": "review_existing_result"}
        if status.get("stage") != "not_started":
            return {"ready": False, "next_action": "reconcile_existing_request"}
    if (not enabled or facts.get("actor") != "root"
            or facts.get("data_class") != "public_or_synthetic"
            or any(facts.get(key) is not True for key in APPROVALS)):
        return {"ready": False, "next_action": "use_existing_policy"}
    if status is None:
        payload, digest = request_binding(facts["request"])
    args = {"requestJson": payload.decode("utf-8"), "requestSha256": digest}
    if status is None:
        return {"ready": False, "next_action": "check_request_status", "tool": TOOL,
                "arguments": {"operation": "status", **args}}
    if status.get("credentialPresent") is not True:
        return {"ready": False, "next_action": "adapter_unavailable"}
    return {"ready": True, "next_action": "manual_tool_call", "tool": TOOL,
            "arguments": {"operation": "run", "dataClass": "public_or_synthetic",
                          "explicitApproval": True, **args}}


def _number_in_range(value, maximum: int) -> bool:
    return type(value) in (int, float) and 0 <= value <= maximum


def _question_options(question: dict, key: str, field: str) -> set[str]:
    options = question.get(key)
    if not isinstance(options, list) or not 2 <= len(options) <= 16:
        raise ValueError("invalid decision options")
    values = set()
    for option in options:
        value = option.get(field) if isinstance(option, dict) else None
        if not isinstance(value, str) or not value.strip() or value in values:
            raise ValueError("invalid decision option")
        values.add(value)
    return values


def _verify_answer(question: dict, answer: dict) -> None:
    kind = question["type"]
    if (not isinstance(answer, dict) or set(answer) != ANSWER_FIELDS
            or answer["name"] != question["name"] or answer["type"] != kind):
        raise ValueError("decision answers do not match questions")
    if any(answer[key] is not None for key in ANSWER_FIELDS - {"name", "type"} - ANSWER_VALUES[kind]):
        raise ValueError("decision answer has incompatible fields")
    if kind == "predicate":
        if not _number_in_range(answer["probability"], 1):
            raise ValueError("invalid predicate probability")
        return
    if kind == "score":
        levels = _question_options(question, "levels", "label")
        if not _number_in_range(answer["score"], len(levels) - 1):
            raise ValueError("invalid decision score")
        return

    choices = _question_options(question, "choices", "value")
    if (not isinstance(answer["choice"], str) or answer["choice"] not in choices
            or not _number_in_range(answer["confidence"], 1)):
        raise ValueError("invalid choice answer")
    distribution = answer["probabilities"]
    if not isinstance(distribution, list) or len(distribution) != len(choices):
        raise ValueError("incomplete choice probabilities")
    probabilities = {}
    for entry in distribution:
        if not isinstance(entry, dict) or set(entry) != {"value", "probability"}:
            raise ValueError("invalid choice probability entry")
        value, probability = entry["value"], entry["probability"]
        if (not isinstance(value, str) or value not in choices or value in probabilities
                or not _number_in_range(probability, 1)):
            raise ValueError("invalid choice probability")
        probabilities[value] = probability
    if not 0.99 <= fsum(probabilities.values()) <= 1.01:
        raise ValueError("invalid choice probability total")


def verify(facts: dict) -> dict:
    request = facts["request"]
    _, digest = request_binding(request)
    result = facts["result"]
    if (not isinstance(result, dict) or set(result) != RESULT_FIELDS
            or result["preset"] != "nace-drex-local" or result["stage"] != "completed" or result["model"] != MODEL
            or result["requestSha256"] != digest or result["valuesReturned"] is not False
            or result["credentialPresent"] is not None or result["errorCategory"] is not None):
        raise ValueError("decision result does not match request")
    for field, maximum in (("inputTokens", 10_000_000), ("elapsedMs", 300_000)):
        value = result[field]
        if value is not None and (type(value) is not int or not 0 <= value <= maximum):
            raise ValueError("invalid decision usage metadata")
    answers = result["answers"]
    if not isinstance(answers, list) or len(answers) != len(request["questions"]):
        raise ValueError("incomplete decision result")
    for question, answer in zip(request["questions"], answers):
        _verify_answer(question, answer)
    return {"verified": True, "model": MODEL, "requestSha256": digest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("status", "prepare", "verify"))
    parser.add_argument("--facts", type=Path)
    parser.add_argument("--codex-home", type=Path)
    args = parser.parse_args(argv)
    if args.phase != "status" and args.facts is None:
        parser.error("--facts is required for prepare and verify")
    home = args.codex_home or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        if args.phase == "status":
            result = {"enabled": load_config(home), "execution_authorized": False}
        else:
            facts = _load_json(args.facts, "Drex Decisions facts")
            if type(facts.get("schema_version")) is not int or facts["schema_version"] != 1:
                raise ValueError("invalid facts schema")
            result = prepare(facts, load_config(home)) if args.phase == "prepare" else verify(facts)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (RoutingError, OSError, ValueError, KeyError, RecursionError):
        print(json.dumps({"error": "invalid_drex_decisions_input"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
