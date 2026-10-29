#!/usr/bin/env python3
"""Opt-in PreToolUse guard against a child agent delegating to its own model.

The guard does nothing unless the user has set mode "observe" or "enforce" in
codex-task-routing/spawn-guard.json.  It classifies only spawn_agent calls made
by a child agent, and only a target model that is provably the caller's model
is denied.  Records keep fixed codes and counts; they never contain model
names, identifiers, session data, paths, or prompt text.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


# The shipped hook uses runpy.run_path, which does not add the script's
# directory to sys.path. Resolve bundled modules for both that entry and CLI use.
_SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if _SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, _SCRIPT_DIRECTORY)

from routing_core.common import (
    DuplicateKeyError, RoutingError, _absolute, _assert_safe_ancestors,
    _is_link_or_reparse, _is_schema_version_one, _load_json,
    _reject_duplicate_pairs, _safe_mkdir,
)


CONFIG_RELATIVE = Path("codex-task-routing") / "spawn-guard.json"
EVENTS_DIRECTORY_RELATIVE = Path("codex-task-routing") / "spawn-guard"
EVENTS_RELATIVE = EVENTS_DIRECTORY_RELATIVE / "events.jsonl"
MAX_HOOK_INPUT_BYTES = 512 * 1024
MAX_CONFIG_BYTES = 4096
MAX_EVENTS_BYTES = 128 * 1024
SCHEMA_VERSION = 1
MODES = ("off", "observe", "enforce")
RECORDED_MODES = frozenset({"observe", "enforce"})
SPAWN_TOOL_NAMES = ("spawn_agent", "Agent")
VERDICTS = frozenset({"same_model", "not_same_model", "undetermined"})
REASONS = frozenset({
    "same_model", "different_model", "tool_input_unreadable", "caller_model_missing",
    "role_may_override_model", "model_omitted", "model_invalid",
})
EVENT_KEYS = frozenset({"schema_version", "recorded_at", "mode", "verdict", "reason", "would_deny"})
# Fixed English text only: never interpolate model names, identifiers, or input.
DENY_REASON = (
    "Codex Task Routing spawn guard: a child agent must not delegate to the same model as itself. "
    "Finish the work in this agent or return it to your parent with the reason; do not spawn a same-model child."
)


def _default_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return _absolute(configured) if configured else _absolute(Path.home() / ".codex")


def _config_state(home: Path) -> tuple[str, bool]:
    """Return (mode, configured); mode is off, observe, enforce, or invalid."""

    try:
        path = home / CONFIG_RELATIVE
        if not os.path.lexists(path):
            return "off", False
        value = _load_json(path, "spawn guard configuration", MAX_CONFIG_BYTES)
        if set(value) != {"schema_version", "mode"}:
            return "invalid", True
        if not _is_schema_version_one(value["schema_version"]) or value["mode"] not in MODES:
            return "invalid", True
        return value["mode"], True
    except Exception:
        return "invalid", True


def load_mode(home: Path) -> str:
    """Return off unless a valid configuration selects observe or enforce."""

    mode, _configured = _config_state(home)
    return mode if mode in RECORDED_MODES else "off"


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _verdict(verdict: str, reason: str, would_deny: bool = False) -> dict[str, Any]:
    return {"verdict": verdict, "reason": reason, "would_deny": would_deny}


def evaluate(payload: Any) -> dict[str, Any] | None:
    """Classify one PreToolUse payload; None means outside the guard's scope."""

    if not isinstance(payload, dict):
        return None
    if payload.get("hook_event_name") != "PreToolUse" or payload.get("tool_name") not in SPAWN_TOOL_NAMES:
        return None
    # Root spawns carry no agent_id; they are outside this guard and are not recorded.
    if _clean_text(payload.get("agent_id")) is None:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return _verdict("undetermined", "tool_input_unreadable")
    caller_model = _clean_text(payload.get("model"))
    if caller_model is None:
        return _verdict("undetermined", "caller_model_missing")
    # A role may set its own model, so the effective target cannot be fixed from the call.
    agent_type = tool_input.get("agent_type")
    if agent_type is not None and (not isinstance(agent_type, str) or agent_type.strip()):
        return _verdict("undetermined", "role_may_override_model")
    model = tool_input.get("model")
    if model is None:
        return _verdict("undetermined", "model_omitted")
    if not isinstance(model, str):
        return _verdict("undetermined", "model_invalid")
    target_model = _clean_text(model)
    if target_model is None:
        return _verdict("undetermined", "model_omitted")
    if target_model == caller_model:
        return _verdict("same_model", "same_model", would_deny=True)
    # A higher role cannot be identified from the input, so a different model passes.
    return _verdict("not_same_model", "different_model")


def record_event(home: Path, mode: str, result: Mapping[str, Any]) -> None:
    """Append one fixed-code line for observe or enforce; never raises."""

    if mode not in RECORDED_MODES:
        return
    try:
        directory = _safe_mkdir(home / EVENTS_DIRECTORY_RELATIVE)
        path = _assert_safe_ancestors(directory / "events.jsonl")
        if os.path.lexists(path):
            metadata = os.lstat(path)
            if _is_link_or_reparse(path) or not stat.S_ISREG(metadata.st_mode):
                return
            # Existing records are capped, not rotated or deleted.
            if metadata.st_size >= MAX_EVENTS_BYTES:
                return
        record = {
            "schema_version": SCHEMA_VERSION,
            "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "mode": mode,
            "verdict": result["verdict"],
            "reason": result["reason"],
            "would_deny": bool(result["would_deny"]),
        }
        line = json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
    except Exception:
        return


def deny_response() -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": DENY_REASON,
        }
    }


def _read_hook_input(stream: Any) -> bytes:
    """Keep at most the limit plus one byte, then drain the rest without storing it."""

    data = stream.read(MAX_HOOK_INPUT_BYTES + 1)
    while stream.read(64 * 1024):
        pass
    return data


def _parse_hook_input(raw: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_reject_duplicate_pairs)
    except (ValueError, DuplicateKeyError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def hook(stdin_bytes: bytes, home: Path) -> dict[str, Any] | None:
    """Return a deny response only in enforce mode; every failure passes through."""

    try:
        mode = load_mode(home)
        if mode not in RECORDED_MODES:
            return None
        if len(stdin_bytes) > MAX_HOOK_INPUT_BYTES:
            return None
        payload = _parse_hook_input(stdin_bytes)
        if payload is None:
            return None
        result = evaluate(payload)
        if result is None:
            return None
        record_event(home, mode, result)
        if mode == "enforce" and result["would_deny"]:
            return deny_response()
        return None
    except Exception:
        return None


def _parse_event_line(line: bytes) -> dict[str, Any] | None:
    if not line.strip():
        return None
    try:
        value = json.loads(line.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (ValueError, DuplicateKeyError, RecursionError):
        return None
    if not isinstance(value, dict) or set(value) != EVENT_KEYS:
        return None
    if not _is_schema_version_one(value["schema_version"]):
        return None
    if not isinstance(value["mode"], str) or value["mode"] not in RECORDED_MODES:
        return None
    if not isinstance(value["verdict"], str) or value["verdict"] not in VERDICTS:
        return None
    if not isinstance(value["reason"], str) or value["reason"] not in REASONS:
        return None
    if not isinstance(value["would_deny"], bool):
        return None
    return value


def _summarize_events(home: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "total": 0, "by_verdict": {}, "by_reason": {}, "would_deny": 0, "capped": False,
    }
    path = _assert_safe_ancestors(home / EVENTS_RELATIVE)
    if not os.path.lexists(path):
        return summary
    if _is_link_or_reparse(path):
        raise RoutingError("spawn guard events are unsafe", code="unsafe_path")
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise RoutingError("spawn guard events are invalid")
    summary["capped"] = metadata.st_size >= MAX_EVENTS_BYTES
    with open(path, "rb") as handle:
        raw = handle.read(MAX_EVENTS_BYTES)
    by_verdict: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    total = 0
    would_deny = 0
    for line in raw.split(b"\n"):
        record = _parse_event_line(line)
        if record is None:
            continue
        total += 1
        by_verdict[record["verdict"]] = by_verdict.get(record["verdict"], 0) + 1
        by_reason[record["reason"]] = by_reason.get(record["reason"], 0) + 1
        if record["would_deny"]:
            would_deny += 1
    summary.update({"total": total, "by_verdict": by_verdict, "by_reason": by_reason, "would_deny": would_deny})
    return summary


def status_payload(home: Path) -> dict[str, Any]:
    mode, configured = _config_state(home)
    return {"mode": mode, "configured": configured, "events": _summarize_events(home)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Opt-in same-model spawn guard for Codex Task Routing")
    parser.add_argument("--codex-home", type=Path, help="Codex home; defaults to CODEX_HOME or ~/.codex")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("hook", help="Read a PreToolUse JSON payload from stdin")
    status = subparsers.add_parser("status", help="Inspect guard state without writing")
    status.add_argument("--json", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "hook":
        result: dict[str, Any] | None
        try:
            home = _absolute(args.codex_home) if args.codex_home is not None else _default_codex_home()
            result = hook(_read_hook_input(sys.stdin.buffer), home)
        except Exception:
            result = None
        if result is not None:
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    home = _absolute(args.codex_home) if args.codex_home is not None else _default_codex_home()
    try:
        result = status_payload(home)
    except (RoutingError, OSError):
        print("error: spawn guard events are unavailable", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
