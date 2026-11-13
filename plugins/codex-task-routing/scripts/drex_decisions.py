#!/usr/bin/env python3
"""Prepare and verify opt-in Drex Decisions requests without sending them."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from routing_core.common import RoutingError, _absolute, _assert_safe_ancestors, _canonical_json, _load_json

CONFIG_RELATIVE = Path("codex-task-routing") / "drex-decisions.json"
MODEL = "drex-max-v1.1"
TOOL = "local_auth_nace_decisions"
APPROVALS = ("host_authorized", "target_authorized", "materials_authorized", "cost_authorized")


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
    if (not enabled or facts.get("actor") != "root"
            or facts.get("data_class") != "public_or_synthetic"
            or any(facts.get(key) is not True for key in APPROVALS)):
        return {"ready": False, "next_action": "use_existing_policy"}
    payload, digest = request_binding(facts["request"])
    status = facts.get("status")
    args = {"requestJson": payload.decode("utf-8"), "requestSha256": digest}
    if status is None:
        return {"ready": False, "next_action": "check_request_status", "tool": TOOL,
                "arguments": {"operation": "status", **args}}
    if not isinstance(status, dict):
        raise ValueError("invalid decision status")
    if status.get("status") in ("unavailable", "waiting_for_local_worker"):
        return {"ready": False, "next_action": "adapter_unavailable"}
    if status.get("requestSha256") != digest or status.get("valuesReturned") is not False:
        raise ValueError("decision status does not match request")
    if status.get("stage") == "not_started":
        if status.get("credentialPresent") is not True:
            return {"ready": False, "next_action": "adapter_unavailable"}
        return {"ready": True, "next_action": "manual_tool_call", "tool": TOOL,
                "arguments": {"operation": "run", "dataClass": "public_or_synthetic",
                              "explicitApproval": True, **args}}
    if status.get("stage") == "completed":
        return {"ready": False, "next_action": "review_existing_result"}
    return {"ready": False, "next_action": "reconcile_existing_request"}


def verify(facts: dict) -> dict:
    request = facts["request"]
    _, digest = request_binding(request)
    result = facts["result"]
    if (not isinstance(result, dict) or result.get("stage") != "completed" or result.get("model") != MODEL
            or result.get("requestSha256") != digest or result.get("valuesReturned") is not False):
        raise ValueError("decision result does not match request")
    answers = result.get("answers")
    if not isinstance(answers, list) or len(answers) != len(request["questions"]):
        raise ValueError("incomplete decision result")
    for question, answer in zip(request["questions"], answers):
        if (not isinstance(answer, dict) or answer.get("name") != question["name"]
                or answer.get("type") != question["type"]):
            raise ValueError("decision answers do not match questions")
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
