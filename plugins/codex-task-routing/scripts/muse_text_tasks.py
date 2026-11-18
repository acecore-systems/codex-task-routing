#!/usr/bin/env python3
"""Plan and verify approved Muse Ember text work without dispatch."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

from muse_pilot_plan import _hash, _text, verify as verify_text
from muse_web_config import load_config
from routing_core.common import RoutingError, _load_json


CONFIG_RELATIVE = Path("codex-task-routing") / "muse-text.json"
PRESET = "muse-ember-text-v1"
TOOL = "local_auth_muse_ember_text_task"
PURPOSES = ("summarize", "extract", "compare", "format")
FLAGS = ("explicit_opt_in", "host_authorized", "target_authorized",
         "materials_authorized", "cost_authorized")


def enabled(home):
    return load_config(home, relative=CONFIG_RELATIVE)["enabled"]


def prepare(data):
    fields = {"schema_version", "actor", "data_class", "purpose", "prompt",
              "prompt_sha256", "status", *FLAGS}
    if not isinstance(data, dict) or set(data) != fields or type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("input")
    if any(type(data[key]) is not bool for key in FLAGS):
        raise ValueError("authorization")
    digest = _hash(data["prompt_sha256"])
    if hashlib.sha256(_text(data["prompt"], 4096)).hexdigest() != digest:
        raise ValueError("hash")

    status = data["status"]
    identity = {"preset": PRESET, "inputSha256": digest}
    if status in ({"state": "intent", **identity}, {"state": "unverified", **identity}):
        return {"ready": False, "next_action": "reconcile_existing_request", "sent": "unknown"}
    if status == {"state": "completed", **identity}:
        return {"ready": False, "next_action": "review_existing_result", "sent": True}
    if data["actor"] != "root" or data["data_class"] != "public_or_synthetic" or data["purpose"] not in PURPOSES:
        return {"ready": False, "next_action": "use_existing_policy", "sent": False}

    gaps = [key for key in FLAGS if not data[key]]
    if gaps:
        return {"ready": False, "next_action": "use_existing_policy", "gaps": gaps, "sent": False}
    if status != {"state": "not_started", **identity}:
        return {"ready": False, "next_action": "check_request_status", "sent": "unknown"}

    return {
        "ready": True, "next_action": "manual_tool_call", "tool": TOOL, "sent": False,
        "arguments": {
            "operation": "run", "dataClass": "public_or_synthetic", "explicitApproval": True,
            "purpose": data["purpose"], "prompt": data["prompt"], "promptSha256": digest,
        },
        "parent_acceptance_required": True,
    }


def verify(data):
    if not isinstance(data, dict) or set(data) != {"schema_version", "prompt_sha256", "result"}:
        raise ValueError("input")
    result = data["result"]
    if not isinstance(result, dict) or result.get("preset") != PRESET or result.get("effort") not in (None, "max"):
        raise ValueError("result")
    verified = verify_text({**data, "result": {k: v for k, v in result.items() if k != "preset"}})
    return {**verified, "requested_effort": "max", "text": result["text"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--facts", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        data = _load_json(args.facts, "Muse text task facts", limit=131072)
        result = prepare(data) if args.action == "prepare" else verify(data)
    except (ValueError, TypeError, RecursionError, OSError, RoutingError):
        print(json.dumps({"error": "invalid_muse_text_task_input"}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
