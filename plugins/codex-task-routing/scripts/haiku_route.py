#!/usr/bin/env python3
"""Explicit Claude Code API route; hooks never invoke this module.

All declarations are caller-supplied evidence, not authorization. No network or
credential lookup occurs during planning. A durable exclusive claim is made
before starting the process so an uncertain outcome cannot be blindly retried.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

from routing_core.common import RoutingError, _assert_safe_ancestors, _load_json, _read_limited_utf8

MODEL = "claude-haiku-5-5"
TOOLS = {"Read", "Glob", "Grep", "Edit", "Write", "Bash"}
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
REV = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


class RouteError(ValueError):
    pass


def validate(request):
    fields = {"schema_version", "enabled", "actor", "request_id", "input_sha256",
              "cwd", "branch", "head", "tools", "bash_rules", "max_turns", "max_budget_usd",
              "host_execution_allowed", "target_operations_allowed", "materials_transfer_allowed",
              "official_endpoint_verified"}
    if not isinstance(request, dict) or set(request) != fields or type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise RouteError("invalid request structure")
    for key in ("enabled", "host_execution_allowed", "target_operations_allowed", "materials_transfer_allowed", "official_endpoint_verified"):
        if type(request[key]) is not bool:
            raise RouteError("invalid authorization declaration")
    if request["actor"] != "root" or not isinstance(request["request_id"], str) or not ID.fullmatch(request["request_id"]):
        raise RouteError("invalid actor or request id")
    if not isinstance(request["input_sha256"], str) or not HASH.fullmatch(request["input_sha256"]):
        raise RouteError("invalid input hash")
    if not isinstance(request["head"], str) or not REV.fullmatch(request["head"]):
        raise RouteError("invalid source revision")
    if not isinstance(request["branch"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", request["branch"]):
        raise RouteError("invalid branch")
    if not isinstance(request["cwd"], str) or not Path(request["cwd"]).is_absolute():
        raise RouteError("absolute working directory required")
    tools = request["tools"]
    if not isinstance(tools, list) or not tools or any(not isinstance(t, str) or t not in TOOLS for t in tools) or len(set(tools)) != len(tools):
        raise RouteError("invalid tool scope")
    rules = request["bash_rules"]
    if not isinstance(rules, list) or len(rules) > 16 or ("Bash" in tools) != bool(rules):
        raise RouteError("Bash requires explicit scoped rules")
    for rule in rules:
        if (not isinstance(rule, str) or len(rule) > 200
                or not re.fullmatch(r"Bash\([A-Za-z0-9_./:-][A-Za-z0-9_./: *=-]*\)", rule)
                or rule == "Bash(*)"):
            raise RouteError("invalid Bash rule")
    if len(set(rules)) != len(rules):
        raise RouteError("duplicate Bash rule")
    if type(request["max_turns"]) is not int or not 1 <= request["max_turns"] <= 100:
        raise RouteError("invalid turn limit")
    budget = request["max_budget_usd"]
    if type(budget) not in (int, float) or not math.isfinite(budget) or not 0 < budget <= 100:
        raise RouteError("invalid budget")
    return request


def plan(request):
    validate(request)
    gaps = [key for key in ("enabled", "host_execution_allowed", "target_operations_allowed", "materials_transfer_allowed", "official_endpoint_verified") if not request[key]]
    return {"schema_version": 1, "route": "haiku_api", "model": MODEL, "effort": "xhigh",
            "planning_only": True, "execution_authorized": False, "gaps": gaps,
            "next_action": "resolve_gaps" if gaps else "verify_runtime_and_existing_authorization"}


def _git(cwd, args):
    allowed = ("PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run(["git", "-c", "core.fsmonitor=false", *args], cwd=cwd, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=15, check=True)
    return result.stdout.strip()


def verify_repository(request):
    cwd = _assert_safe_ancestors(request["cwd"])
    if not cwd.is_dir():
        raise RouteError("working directory unavailable")
    top = Path(_git(cwd, ["rev-parse", "--show-toplevel"]))
    if os.path.normcase(os.path.abspath(top)) != os.path.normcase(os.path.abspath(cwd)):
        raise RouteError("working directory must be the repository root")
    if _git(cwd, ["symbolic-ref", "--short", "HEAD"]) != request["branch"] or _git(cwd, ["rev-parse", "HEAD"]) != request["head"]:
        raise RouteError("repository identity mismatch")
    if _git(cwd, ["status", "--porcelain", "--untracked-files=normal"]):
        raise RouteError("working tree must be clean")
    return cwd


def child_environment(environ, turns):
    # Do not inherit provider endpoints, OAuth, proxies, CLI settings, or arbitrary secrets.
    allowed = ("PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL")
    env = {key: environ[key] for key in allowed if key in environ}
    key = environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RouteError("API key unavailable")
    env["ANTHROPIC_API_KEY"] = key
    env["ANTHROPIC_BASE_URL"] = "https://api.anthropic.com"
    env["CLAUDE_CODE_MAX_TURNS"] = str(turns)
    return env


def command(executable, request):
    names = ",".join(request["tools"])
    allowed = ",".join([tool for tool in request["tools"] if tool != "Bash"] + request["bash_rules"])
    return [str(executable), "--bare", "--restricted", "--disable-slash-commands", "--strict-mcp-config",
            "--no-chrome", "--no-session-persistence", "--permission-mode", "dontAsk",
            "--permission-prompts", "none", "--tools", names, "--allowedTools", allowed,
            "--model", MODEL, "--effort", "xhigh", "--max-turns", str(request["max_turns"]),
            "--max-budget-usd", str(request["max_budget_usd"]), "--output-format", "json", "--print"]


def verify_version(executable, environ):
    allowed = ("PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR")
    env = {key: environ[key] for key in allowed if key in environ}
    result = subprocess.run([str(executable), "--version"], env=env, shell=False, capture_output=True,
                            text=True, encoding="utf-8", timeout=15, check=True)
    match = re.fullmatch(r"\s*(\d+)\.(\d+)\.(\d+)(?:\s+\(Claude Code\))?\s*", result.stdout)
    if not match or tuple(map(int, match.groups())) < (2, 1, 286):
        raise RouteError("unsupported Claude Code version")


def record_failure(state, request_id, category, returncode=None):
    """Record fixed non-secret metadata, never overwrite an existing result."""
    try:
        with (state / (request_id + ".failure.json")).open("x", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "category": category, "returncode": returncode}, handle)
    except OSError:
        pass  # The permanent claim still blocks a second submission.


def run(request, prompt, state_dir, executable, *, environ=None, runner=subprocess.run):
    result = plan(request)
    if result["gaps"]:
        raise RouteError("route or authorization declarations incomplete")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > 128 * 1024:
        raise RouteError("invalid input")
    if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != request["input_sha256"]:
        raise RouteError("input hash mismatch")
    executable = _assert_safe_ancestors(executable)
    if not executable.is_file() or (os.name == "nt" and executable.suffix.lower() != ".exe"):
        raise RouteError("explicit executable unavailable")
    source_env = os.environ if environ is None else environ
    env = child_environment(source_env, request["max_turns"])
    verify_version(executable, source_env)
    cwd = verify_repository(request)
    state = _assert_safe_ancestors(state_dir)
    try:
        inside_repo = os.path.normcase(os.path.commonpath([str(cwd), str(state)])) == os.path.normcase(str(cwd))
    except ValueError:  # Separate Windows volumes cannot contain one another.
        inside_repo = False
    if not state.is_dir() or inside_repo:
        raise RouteError("existing state directory outside the repository required")
    claim = state / (request["request_id"] + ".claim.json")
    # Never replace, delete or retry a claim, including after process failure.
    try:
        with claim.open("x", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "request_id": request["request_id"],
                       "input_sha256": request["input_sha256"], "status": "outcome_unknown"}, handle)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise RouteError("request already claimed; reconcile existing outcome") from exc
    returncode = None
    category = "unknown"
    try:
        completed = runner(command(executable, request), input=prompt, cwd=str(cwd), env=env,
                           shell=False, capture_output=True, text=True, encoding="utf-8", timeout=900, check=False)
        returncode = completed.returncode
        if completed.returncode != 0:
            category = "nonzero_exit"
            raise RouteError("execution failed; reconcile claim")
        if len(completed.stdout.encode("utf-8")) > 1024 * 1024:
            category = "output_too_large"
            raise RouteError("execution failed or output exceeded limit; reconcile claim")
        category = "invalid_result"
        value = json.loads(completed.stdout)
        if not isinstance(value, dict) or value.get("is_error") is not False or value.get("subtype") != "success":
            raise RouteError("execution did not report success; reconcile claim")
        usage = value.get("modelUsage")
        model_verified = isinstance(usage, dict) and bool(usage) and set(usage) == {MODEL}
        denials = value.get("permission_denials")
        complete = model_verified and isinstance(denials, list) and not denials
        category = "result_storage_failed"
        output = state / (request["request_id"] + ".result.json")
        with output.open("x", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "request_id": request["request_id"],
                       "input_sha256": request["input_sha256"], "requested_effort": "xhigh",
                       "observed_effort": None, "model_verified": model_verified,
                       "completion": "reported" if complete else "partial_or_unverified", "result": value}, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
    except subprocess.TimeoutExpired as exc:
        record_failure(state, request["request_id"], "timeout", returncode)
        raise RouteError("execution outcome unknown; reconcile claim") from exc
    except RouteError:
        record_failure(state, request["request_id"], category, returncode)
        raise
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        record_failure(state, request["request_id"], category, returncode)
        raise RouteError("execution outcome unknown; reconcile claim") from exc
    return {"status": "result_recorded" if complete else "partial_or_unverified", "request_id": request["request_id"], "accepted": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("inspect", "plan", "run"))
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--claude-executable", type=Path)
    args = parser.parse_args(argv)
    try:
        request = _load_json(args.request, "request")
        if args.phase == "run":
            if not all((args.input, args.state_dir, args.claude_executable)):
                raise RouteError("run requires input, state directory and executable")
            _read_limited_utf8(args.input, 128 * 1024, "input")  # validate path and size
            prompt = args.input.read_bytes().decode("utf-8")  # preserve CRLF in the hashed bytes
            output = run(request, prompt, args.state_dir, args.claude_executable)
        else:
            output = plan(request)
        print(json.dumps(output, ensure_ascii=False))
        return 0
    except (RouteError, RoutingError, ValueError, OSError, subprocess.SubprocessError):
        print('{"error":"route check failed; consult the route reference"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
