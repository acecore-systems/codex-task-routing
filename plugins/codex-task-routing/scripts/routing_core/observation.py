"""Bounded observation validation and storage, independent of hook delivery."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Iterator, Protocol

from .common import (
    EFFORT_ORDER, RoutingError, _absolute, _assert_safe_ancestors,
    _canonical_json, _is_link_or_reparse,
    _load_json, _safe_mkdir, _write_atomic,
)

from .usage_metadata import _enrollment

OBSERVATIONS_RELATIVE = Path("codex-task-routing") / "observations"
OBSERVATION_LOCK_NAME = ".state.lock"
MAX_OBSERVATION_STATE_BYTES = 512 * 1024
MAX_OBSERVATION_INPUT_BYTES = 128 * 1024
MAX_OBSERVATION_TASK_ID_CHARS = 256
MAX_OBSERVATION_REFERENCE_CHARS = 4096
MAX_OBSERVATION_SCOPE_CHARS = 500
MAX_OBSERVATION_RUNS = 32


class Policy(Protocol):
    """Only the policy data needed by observations; no runtime import cycle."""

    config: dict[str, Any]
    version: str
    content_hash: str

    def rendered_documents(self) -> dict[str, str]: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _observation_text(value: Any, purpose: str, limit: int = MAX_OBSERVATION_REFERENCE_CHARS) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise RoutingError(f"{purpose} must be a non-empty string")
    if any(ord(character) < 32 for character in value):
        raise RoutingError(f"{purpose} contains control characters")
    return value


def _observation_optional_text(value: Any, purpose: str) -> str | None:
    if value is None:
        return None
    return _observation_text(value, purpose)


def _observation_task_id(value: Any) -> str:
    return _observation_text(value, "observation task id", MAX_OBSERVATION_TASK_ID_CHARS)


def _observation_sampling_key(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise RoutingError("observation sampling key is invalid")
    return value


def observation_sampling_key(policy: Policy) -> str:
    """Hash rendered policy content, deliberately excluding the manifest version.

    The policy's usual content hash includes the package version because it is
    useful for cache invalidation.  Sampling must instead continue across a
    package-only release when the rendered policy is identical.
    """

    documents = policy.rendered_documents()
    canonical_documents = {name: documents[name] for name in sorted(documents)}
    return hashlib.sha256(_canonical_json(canonical_documents)).hexdigest()


def _observation_policy_metadata(policy: Policy, sampling_key: str) -> dict[str, str]:
    return {
        "policy_revision": policy.config["policy_revision"],
        "manifest_version": policy.version,
        "content_hash": policy.content_hash,
        "sampling_key": sampling_key,
    }


def _empty_observation_state(sampling_key: str) -> dict[str, Any]:
    sampling_key = _observation_sampling_key(sampling_key)
    return {"schema_version": 1, "samples": {sampling_key: {"tasks": {}}}}


def _validate_observation_measurement(value: Any, *, allow_partial: bool) -> None:
    if not isinstance(value, dict):
        raise RoutingError("observation measurement must be an object")
    if set(value) != {"state", "reason"}:
        raise RoutingError("observation measurement has unsupported fields")
    state = value.get("state")
    reason = value.get("reason")
    allowed = {"measured", "missing"}
    if allow_partial:
        allowed.add("partial")
    if state not in allowed:
        raise RoutingError("observation measurement state is invalid")
    if state == "measured":
        if reason is not None:
            raise RoutingError("measured observation must not have a missing reason")
    elif _observation_optional_text(reason, "observation measurement reason") is None:
        raise RoutingError("missing observation measurement needs a reason")


def _validate_observation_verification(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"reference", "reason"}:
        raise RoutingError("observation verification has unsupported fields")
    reference = _observation_optional_text(value.get("reference"), "verification reference")
    reason = _observation_optional_text(value.get("reason"), "verification missing reason")
    if reference is None and reason is None:
        raise RoutingError("missing verification needs a reason")
    if reference is not None and reason is not None:
        raise RoutingError("verified observation must not have a missing reason")


def _validate_observation_requested(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"model", "effort", "reason"}:
        raise RoutingError("requested run settings have unsupported fields")
    model = _observation_optional_text(value.get("model"), "requested model")
    effort = value.get("effort")
    if effort is not None and effort not in EFFORT_ORDER:
        raise RoutingError("requested effort is invalid")
    reason = _observation_optional_text(value.get("reason"), "requested settings reason")
    if (model is None or effort is None) and reason is None:
        raise RoutingError("unknown requested settings need a reason")
    if model is not None and effort is not None and reason is not None:
        raise RoutingError("known requested settings must not have an unknown reason")


def _validate_observation_observed(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"model", "effort", "evidence_ref", "reason"}:
        raise RoutingError("observed run settings have unsupported fields")
    model = _observation_optional_text(value.get("model"), "observed model")
    effort = value.get("effort")
    if effort is not None and effort not in EFFORT_ORDER:
        raise RoutingError("observed effort is invalid")
    evidence = _observation_optional_text(value.get("evidence_ref"), "observed settings reference")
    reason = _observation_optional_text(value.get("reason"), "observed settings reason")
    if model is not None or effort is not None:
        if evidence is None:
            raise RoutingError("observed settings need a verification reference")
    elif evidence is not None:
        raise RoutingError("unknown observed settings must not have a verification reference")
    if (model is None or effort is None) and reason is None:
        raise RoutingError("unknown observed settings need a reason")
    if model is not None and effort is not None and reason is not None:
        raise RoutingError("known observed settings must not have an unknown reason")


def _validate_observation_usage(value: Any) -> None:
    if isinstance(value, dict) and value.get("basis") == "aggregate_total_tokens":
        fields = {"basis", "state", "reason", "evidence_ref", "turn_scope",
                  "start_tokens", "end_tokens", "total_tokens", "start_boundary_ref",
                  "end_boundary_ref", "continuity_ref"}
        if set(value) != fields or value.get("turn_scope") not in {"full_turn", "partial_turn"}:
            raise RoutingError("aggregate run usage has unsupported fields")
        reason = _observation_optional_text(value.get("reason"), "aggregate usage reason")
        evidence = _observation_optional_text(value.get("evidence_ref"), "aggregate usage reference")
        numbers = [value.get(name) for name in ("start_tokens", "end_tokens", "total_tokens")]
        boundaries = [_observation_optional_text(value.get(name), "aggregate interval evidence")
                      for name in ("start_boundary_ref", "end_boundary_ref", "continuity_ref")]
        if value.get("state") == "missing":
            if reason is None or evidence is not None or any(number is not None for number in numbers) or any(boundaries):
                raise RoutingError("missing aggregate usage is invalid")
        elif value.get("state") == "measured":
            if reason is not None or evidence is None or any(type(number) is not int or number < 0 for number in numbers):
                raise RoutingError("measured aggregate usage is invalid")
            if value["turn_scope"] == "full_turn" and any(ref is None for ref in boundaries):
                raise RoutingError("full aggregate usage needs interval evidence")
            start, end, total = numbers
            if end < start or total != end - start:
                raise RoutingError("aggregate usage interval is invalid")
        else:
            raise RoutingError("aggregate usage state is invalid")
        return
    fields = {
        "state",
        "reason",
        "evidence_ref",
        "turn_scope",
        "input_tokens",
        "cache_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "unknown_reasons",
    }
    if isinstance(value, dict) and value.get("basis") == "hook_request_counters":
        fields.add("basis")
    if not isinstance(value, dict) or set(value) != fields:
        raise RoutingError("run usage has unsupported fields")
    state = value.get("state")
    reason = _observation_optional_text(value.get("reason"), "run usage reason")
    evidence = _observation_optional_text(value.get("evidence_ref"), "run usage reference")
    if value.get("turn_scope") not in {"full_turn", "partial_turn"}:
        raise RoutingError("run usage turn scope is invalid")
    token_fields = ("input_tokens", "cache_input_tokens", "output_tokens", "reasoning_tokens")
    unknown_reasons = value.get("unknown_reasons")
    if not isinstance(unknown_reasons, dict) or set(unknown_reasons) - set(token_fields):
        raise RoutingError("run usage unknown reasons are invalid")
    for name in token_fields:
        amount = value.get(name)
        if amount is not None and (type(amount) is not int or amount < 0):
            raise RoutingError("run usage token count is invalid")
        missing_reason = unknown_reasons.get(name)
        if amount is None:
            if state == "measured" and _observation_optional_text(
                missing_reason, "run usage unknown reason"
            ) is None:
                raise RoutingError("unknown measured token count needs a reason")
        elif missing_reason is not None:
            raise RoutingError("known token count must not have an unknown reason")
    if state == "missing":
        if reason is None or evidence is not None or any(value.get(name) is not None for name in token_fields):
            raise RoutingError("missing run usage is invalid")
        if unknown_reasons:
            raise RoutingError("missing run usage uses one shared reason")
    elif state == "measured":
        if reason is not None or evidence is None or not any(value.get(name) is not None for name in token_fields):
            raise RoutingError("measured run usage is invalid")
    else:
        raise RoutingError("run usage state is invalid")
    input_tokens = value.get("input_tokens")
    cache_input_tokens = value.get("cache_input_tokens")
    output_tokens = value.get("output_tokens")
    reasoning_tokens = value.get("reasoning_tokens")
    if input_tokens is not None and cache_input_tokens is not None and cache_input_tokens > input_tokens:
        raise RoutingError("cache input exceeds input")
    if output_tokens is not None and reasoning_tokens is not None and reasoning_tokens > output_tokens:
        raise RoutingError("reasoning exceeds output")


def _validate_observation_run(value: Any) -> None:
    fields = {"run_key", "relationship", "state", "run_id", "run_id_reason", "requested", "observed", "usage"}
    if not isinstance(value, dict) or set(value) != fields:
        raise RoutingError("observation run has unsupported fields")
    _observation_text(value.get("run_key"), "run key", 128)
    if value.get("relationship") not in {"parent", "child", "grandchild"}:
        raise RoutingError("run relationship is invalid")
    state = value.get("state")
    if state not in {"ongoing", "completed", "interrupted"}:
        raise RoutingError("run state is invalid")
    run_id = _observation_optional_text(value.get("run_id"), "run id")
    run_id_reason = _observation_optional_text(value.get("run_id_reason"), "run id missing reason")
    if run_id is None and run_id_reason is None:
        raise RoutingError("unknown run id needs a reason")
    if run_id is not None and run_id_reason is not None:
        raise RoutingError("known run id must not have an unknown reason")
    _validate_observation_requested(value.get("requested"))
    _validate_observation_observed(value.get("observed"))
    _validate_observation_usage(value.get("usage"))
    if state != "completed" and value["usage"]["state"] == "measured":
        raise RoutingError("unfinished run usage cannot be recorded as completed usage")


def _validate_observation_completion(value: Any) -> None:
    fields = {"state", "scope", "measurement", "verification", "rework", "runs"}
    if not isinstance(value, dict) or set(value) != fields:
        raise RoutingError("observation completion has unsupported fields")
    state = value.get("state")
    if state not in {"completed", "interrupted"}:
        raise RoutingError("observation completion state is invalid")
    _observation_text(value.get("scope"), "observation scope", MAX_OBSERVATION_SCOPE_CHARS)
    _validate_observation_measurement(value.get("measurement"), allow_partial=True)
    _validate_observation_verification(value.get("verification"))
    _observation_text(value.get("rework"), "rework record")
    runs = value.get("runs")
    if not isinstance(runs, list) or len(runs) > MAX_OBSERVATION_RUNS:
        raise RoutingError("observation runs are invalid")
    run_keys: set[str] = set()
    for run in runs:
        _validate_observation_run(run)
        if run["run_key"] in run_keys:
            raise RoutingError("observation run keys must be unique")
        run_keys.add(run["run_key"])
    measurement = value["measurement"]["state"]
    measured_runs = [run for run in runs if run["usage"]["state"] == "measured"]
    token_fields = ("input_tokens", "cache_input_tokens", "output_tokens", "reasoning_tokens")
    full_coverage = (
        state == "completed"
        and any(run["relationship"] == "parent" for run in runs)
        and all(
            run["state"] == "completed"
            and run["usage"]["state"] == "measured"
            and run["usage"]["turn_scope"] == "full_turn"
            and (run["usage"].get("basis") == "aggregate_total_tokens"
                 or all(run["usage"][field] is not None for field in token_fields))
            for run in runs
        )
    )
    if measurement == "measured":
        if not full_coverage:
            raise RoutingError("measured observation has incomplete run usage")
    elif measurement == "missing" and measured_runs:
        raise RoutingError("missing observation cannot contain measured run usage")
    elif measurement == "partial" and (not measured_runs or full_coverage):
        raise RoutingError("partial observation measurement is inconsistent")


def _validate_observation_record(value: Any, sampling_key: str, task_id: str) -> None:
    fields = {
        "task_id", "sampling_key", "policy", "state", "measurement", "started_at",
        "updated_at", "completed_at", "scope", "rework", "verification", "runs",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RoutingError("observation record is invalid")
    if value.get("task_id") != task_id or value.get("sampling_key") != sampling_key:
        raise RoutingError("observation record identity is invalid")
    _observation_task_id(value["task_id"])
    policy = value.get("policy")
    if not isinstance(policy, dict) or set(policy) != {"policy_revision", "manifest_version", "content_hash", "sampling_key"}:
        raise RoutingError("observation record policy is invalid")
    _observation_text(policy.get("policy_revision"), "observation policy revision")
    _observation_text(policy.get("manifest_version"), "observation manifest version")
    if not isinstance(policy.get("content_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", policy["content_hash"]):
        raise RoutingError("observation policy hash is invalid")
    if policy.get("sampling_key") != sampling_key:
        raise RoutingError("observation policy sampling key is invalid")
    if value.get("state") not in {"ongoing", "completed", "interrupted"}:
        raise RoutingError("observation record state is invalid")
    _validate_observation_measurement(value.get("measurement"), allow_partial=True)
    for name in ("started_at", "updated_at"):
        _observation_text(value.get(name), "observation timestamp", 64)
    completed_at = value.get("completed_at")
    if value["state"] == "ongoing":
        if completed_at is not None or value.get("scope") is not None or value.get("rework") is not None or value.get("verification") is not None or value.get("runs") != []:
            raise RoutingError("ongoing observation record is invalid")
    else:
        _observation_text(completed_at, "observation completion timestamp", 64)
        _validate_observation_completion(
            {
                "state": value["state"],
                "scope": value["scope"],
                "measurement": value["measurement"],
                "verification": value["verification"],
                "rework": value["rework"],
                "runs": value["runs"],
            }
        )


def _campaign_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise RoutingError("campaign id is invalid")
    return value


def _campaign_stats(campaign: dict[str, Any]) -> dict[str, Any]:
    tasks = list(campaign["tasks"].values())
    measured = sum(r["state"] == "completed" and r["measurement"]["state"] == "measured" for r in tasks)
    pending = sum(r["state"] == "ongoing" for r in tasks)
    available = 0 if campaign["closed_at"] is not None else max(
        0, min(6 - len(tasks), 3 - measured - pending) - (1 if campaign["arm"] is not None else 0))
    capture_pending = sum(bool(capture["threads"]) and not capture["root_stopped"]
                          for capture in campaign["captures"].values())
    terminal = campaign["closed_at"] is not None or (pending == 0 and capture_pending == 0 and (measured >= 3 or len(tasks) >= 6))
    return {"attempts": len(tasks), "measured": measured, "pending": pending,
            "armed": campaign["arm"] is not None, "arm_failure": campaign["arm_failure"], "capture_pending": capture_pending, "remaining": available, "terminal": terminal,
            "missing": sum(r["state"] != "ongoing" and r["measurement"]["state"] != "measured" for r in tasks)}


def _validate_capture_snapshot(thread_id: str, snapshot: Any) -> None:
    from .usage_metadata import _uuid
    _uuid(thread_id)
    fields = {"state", "basis", "thread_id", "parent_id", "depth", "total_tokens", "configured_model",
              "configured_effort", "runtime_model_verified", "observed_at", "scope", "db_file_id",
              "db_schema_version", "db_user_version", "cli_version"}
    if not isinstance(snapshot, dict) or set(snapshot) != fields or snapshot["thread_id"] != thread_id:
        raise RoutingError("capture snapshot is invalid")
    if snapshot["state"] != "measured" or snapshot["basis"] != "aggregate_total_tokens" or snapshot["runtime_model_verified"] is not False:
        raise RoutingError("capture snapshot basis is invalid")
    if type(snapshot["total_tokens"]) is not int or snapshot["total_tokens"] < 0:
        raise RoutingError("capture snapshot total is invalid")
    if not isinstance(snapshot["db_file_id"], str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot["db_file_id"]):
        raise RoutingError("capture snapshot database identity is invalid")
    if type(snapshot["depth"]) is not int or snapshot["depth"] not in (0, 1, 2):
        raise RoutingError("capture snapshot depth is invalid")
    if snapshot["parent_id"] is not None:
        _uuid(snapshot["parent_id"])
    if (snapshot["depth"] == 0) != (snapshot["parent_id"] is None):
        raise RoutingError("capture snapshot parent is invalid")
    if type(snapshot["db_schema_version"]) is not int or type(snapshot["db_user_version"]) is not int or snapshot["cli_version"] != "0.165.0-alpha.1.2":
        raise RoutingError("capture snapshot version is invalid")
    if snapshot["scope"] != "instantaneous_snapshot; interval completeness not established":
        raise RoutingError("capture snapshot scope is invalid")
    _observation_text(snapshot["observed_at"], "capture snapshot timestamp", 64)
    _observation_optional_text(snapshot["configured_model"], "capture model setting")
    _observation_optional_text(snapshot["configured_effort"], "capture effort setting")


def _validate_capture(capture: Any) -> None:
    if not isinstance(capture, dict) or set(capture) != {"threads", "issues", "root_stopped", "baselines", "verification"}:
        raise RoutingError("capture is invalid")
    if type(capture["root_stopped"]) is not bool or not isinstance(capture["threads"], dict) or len(capture["threads"]) > 32:
        raise RoutingError("capture threads are invalid")
    if not isinstance(capture["baselines"], dict) or len(capture["baselines"]) > 32:
        raise RoutingError("capture baselines are invalid")
    for thread_id, snapshot in capture["baselines"].items():
        _validate_capture_snapshot(thread_id, snapshot)
    verification = capture["verification"]
    if not isinstance(verification, dict) or set(verification) != {"state", "first", "second", "reason", "continuity_ref"} or verification["state"] not in {"missing", "pending", "matched", "verified"} or not isinstance(verification["first"], dict) or len(verification["first"]) > 32 or not isinstance(verification["second"], dict) or len(verification["second"]) > 32:
        raise RoutingError("capture verification is invalid")
    if verification["state"] == "verified":
        if verification["reason"] is not None or _observation_optional_text(verification["continuity_ref"], "counter continuity evidence") is None:
            raise RoutingError("verified capture lacks continuity evidence")
    elif not isinstance(verification["reason"], str) or not verification["reason"].strip():
        raise RoutingError("unverified capture needs a reason")
    if verification["state"] != "verified" and verification["continuity_ref"] is not None:
        raise RoutingError("unverified capture has continuity evidence")
    for snapshot_set in (verification["first"], verification["second"]):
        for thread_id, snapshot in snapshot_set.items():
            _validate_capture_snapshot(thread_id, snapshot)
    if verification["state"] in {"matched", "verified"} and (not verification["first"] or set(verification["first"]) != set(verification["second"])):
        raise RoutingError("matched capture snapshots are incomplete")
    allowed = {"unregistered_child", "child_registration_failed", "invalid_event", "ambiguous_pair", "event_limit", "child_not_stopped"}
    if not isinstance(capture["issues"], list) or len(capture["issues"]) > len(allowed) or any(issue not in allowed for issue in capture["issues"]) or len(set(capture["issues"])) != len(capture["issues"]):
        raise RoutingError("capture issues are invalid")
    for thread_id, thread in capture["threads"].items():
        from .usage_metadata import _uuid
        _uuid(thread_id)
        if not isinstance(thread, dict) or set(thread) != {"turns", "started", "stopped"} or type(thread["started"]) is not bool or type(thread["stopped"]) is not bool or not isinstance(thread["turns"], dict) or len(thread["turns"]) > 128:
            raise RoutingError("capture thread is invalid")
        for turn_id, turn in thread["turns"].items():
            if not isinstance(turn_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", turn_id):
                raise RoutingError("capture turn identity is invalid")
            fields = {"pending", "pairs", "input_tokens", "cache_input_tokens", "output_tokens", "reasoning_tokens"}
            if not isinstance(turn, dict) or set(turn) != fields or type(turn["pending"]) is not bool:
                raise RoutingError("capture turn is invalid")
            if any(type(turn[k]) is not int or turn[k] < 0 for k in fields - {"pending"}) or turn["pairs"] > 1024:
                raise RoutingError("capture counters are invalid")
            if turn["cache_input_tokens"] > turn["input_tokens"] or turn["reasoning_tokens"] > turn["output_tokens"]:
                raise RoutingError("capture counters are inconsistent")


def _validate_observation_state(value: Any, sampling_key: str) -> dict[str, Any]:
    sampling_key = _observation_sampling_key(sampling_key)
    if not isinstance(value, dict) or set(value) != {"schema_version", "samples"}:
        raise RoutingError("observation state is invalid")
    version = value.get("schema_version")
    if type(version) is not int or version not in (1, 2) or not isinstance(value.get("samples"), dict):
        raise RoutingError("observation state is invalid")
    if set(value["samples"]) != {sampling_key}:
        raise RoutingError("observation state has an unexpected sampling key")
    sample = value["samples"][sampling_key]
    expected = {"tasks"} if version == 1 else {"tasks", "campaigns", "active_campaign"}
    if not isinstance(sample, dict) or set(sample) != expected or not isinstance(sample.get("tasks"), dict):
        raise RoutingError("observation sample is invalid")
    if len(sample["tasks"]) > 3:
        raise RoutingError("observation sample exceeds its limit")
    task_ids: set[str] = set()
    def validate_tasks(tasks: dict[str, Any]) -> None:
        for task_id, record in tasks.items():
            _observation_task_id(task_id)
            if task_id in task_ids:
                raise RoutingError("observation task id is duplicated")
            task_ids.add(task_id)
            _validate_observation_record(record, sampling_key, task_id)
    validate_tasks(sample["tasks"])
    if version == 2:
        campaigns = sample["campaigns"]
        if not isinstance(campaigns, dict) or len(campaigns) > 32:
            raise RoutingError("observation campaigns are invalid")
        active = sample["active_campaign"]
        if active is not None and (not isinstance(active, str) or active not in campaigns):
            raise RoutingError("active campaign is invalid")
        for campaign_id, campaign in campaigns.items():
            _campaign_id(campaign_id)
            if not isinstance(campaign, dict) or set(campaign) != {"created_at", "purpose", "tasks", "closed_at", "close_reason", "enrollments", "captures", "arm", "arm_failure", "classifications"}:
                raise RoutingError("campaign is invalid")
            _observation_text(campaign["created_at"], "campaign timestamp", 64)
            _observation_text(campaign["purpose"], "campaign purpose", MAX_OBSERVATION_SCOPE_CHARS)
            closed_at, close_reason = campaign["closed_at"], campaign["close_reason"]
            if closed_at is None:
                if close_reason is not None:
                    raise RoutingError("open campaign has a close reason")
            else:
                _observation_text(closed_at, "campaign close timestamp", 64)
                _observation_text(close_reason, "campaign close reason", MAX_OBSERVATION_SCOPE_CHARS)
            if not isinstance(campaign["tasks"], dict) or len(campaign["tasks"]) > 6:
                raise RoutingError("campaign attempts exceed limit")
            validate_tasks(campaign["tasks"])
            classifications = campaign["classifications"]
            if not isinstance(classifications, dict) or set(classifications) - set(campaign["tasks"]):
                raise RoutingError("campaign classifications are invalid")
            for entry in classifications.values():
                if not isinstance(entry, dict) or set(entry) != {"state", "evidence_ref"} or entry["state"] not in {"provisional", "normal", "excluded"}:
                    raise RoutingError("campaign classification is invalid")
                if entry["state"] == "provisional":
                    if entry["evidence_ref"] is not None:
                        raise RoutingError("provisional classification has evidence")
                else:
                    _observation_text(entry["evidence_ref"], "classification evidence")
            if campaign["arm_failure"] not in {None, "unreadable_prompt_event"}:
                raise RoutingError("campaign arm failure is invalid")
            arm = campaign["arm"]
            if arm is not None and campaign["arm_failure"] is not None:
                raise RoutingError("active arm has a failure")
            if arm is not None and (campaign_id != active or closed_at is not None):
                raise RoutingError("inactive campaign cannot be armed")
            if arm is not None:
                if not isinstance(arm, dict) or set(arm) != {"task_id", "enrollment", "armed_at"}:
                    raise RoutingError("campaign arm is invalid")
                _observation_task_id(arm["task_id"])
                _observation_text(arm["armed_at"], "arm timestamp", 64)
                if arm["task_id"] in task_ids or not isinstance(arm["enrollment"], dict):
                    raise RoutingError("campaign arm task is invalid")
                _enrollment(arm["enrollment"], arm["enrollment"].get("root_id"))
                if arm["enrollment"].get("start_evidence_ref") is not None:
                    raise RoutingError("arm start evidence must come from the next prompt")
            enrollments = campaign["enrollments"]
            if not isinstance(enrollments, dict) or set(enrollments) - set(campaign["tasks"]):
                raise RoutingError("campaign enrollments are invalid")
            captures = campaign["captures"]
            if not isinstance(captures, dict) or set(captures) - set(enrollments):
                raise RoutingError("campaign captures are invalid")
            for capture in captures.values():
                _validate_capture(capture)
            for enrollment in enrollments.values():
                if not isinstance(enrollment, dict):
                    raise RoutingError("campaign enrollment is invalid")
                _enrollment(enrollment, enrollment.get("root_id"))
            for task_id, capture in captures.items():
                allowed_ids = {row["thread_id"] for row in enrollments[task_id]["threads"]}
                if (set(capture["threads"]) | set(capture["baselines"]) | set(capture["verification"]["first"]) | set(capture["verification"]["second"])) - allowed_ids:
                    raise RoutingError("capture contains an unenrolled thread")
            if closed_at is not None and any(r["state"] == "ongoing" for r in campaign["tasks"].values()):
                raise RoutingError("closed campaign has pending tasks")
            if campaign_id != active and not _campaign_stats(campaign)["terminal"]:
                raise RoutingError("inactive campaign is unfinished")
    return value


def _observation_directory(codex_home: Path, *, create: bool) -> Path:
    directory = _absolute(codex_home) / OBSERVATIONS_RELATIVE
    if create:
        return _safe_mkdir(directory)
    directory = _assert_safe_ancestors(directory)
    if os.path.lexists(directory) and (not directory.is_dir() or _is_link_or_reparse(directory)):
        raise RoutingError("observation directory is unsafe")
    return directory


def _observation_state_path(codex_home: Path, sampling_key: str, *, create: bool) -> Path:
    sampling_key = _observation_sampling_key(sampling_key)
    return _observation_directory(codex_home, create=create) / f"{sampling_key}.json"


def _read_observation_state(codex_home: Path, sampling_key: str) -> dict[str, Any]:
    sampling_key = _observation_sampling_key(sampling_key)
    directory = _observation_directory(codex_home, create=False)
    path = directory / f"{sampling_key}.json"
    if not os.path.lexists(path):
        return _empty_observation_state(sampling_key)
    return _validate_observation_state(
        _load_json(path, "observation state", MAX_OBSERVATION_STATE_BYTES), sampling_key
    )


@contextmanager
def _observation_write_lock(codex_home: Path, sampling_key: str | None = None) -> Iterator[None]:
    """Serialize writes with an OS lock that is released if the process exits."""

    directory = _observation_directory(codex_home, create=True)
    lock_name = (
        f".{_observation_sampling_key(sampling_key)}.lock"
        if sampling_key is not None
        else OBSERVATION_LOCK_NAME
    )
    lock_path = directory / lock_name
    if os.path.lexists(lock_path) and _is_link_or_reparse(lock_path):
        raise RoutingError("observation lock is unsafe")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise RoutingError("cannot lock observation state") from exc
    try:
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise RoutingError("observation lock is not a regular file")
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RoutingError("observation state is busy or unavailable") from exc
        yield
    finally:
        # Keep the inode fixed: unlinking allows two writers to lock different
        # files. Closing (including process termination) releases the OS lock.
        os.close(descriptor)


def _write_observation_state(codex_home: Path, sampling_key: str, state: dict[str, Any]) -> None:
    sampling_key = _observation_sampling_key(sampling_key)
    _validate_observation_state(state, sampling_key)
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if len(encoded.encode("utf-8")) > MAX_OBSERVATION_STATE_BYTES:
        raise RoutingError("observation state is too large")
    path = _observation_state_path(codex_home, sampling_key, create=True)
    _write_atomic(path.parent, path.name, encoded)


def _find_observation_task(state: dict[str, Any], sampling_key: str, task_id: str,
                           campaign_id: str | None = None) -> dict[str, Any] | None:
    sample = state["samples"][_observation_sampling_key(sampling_key)]
    if campaign_id is None:
        return sample["tasks"].get(task_id)
    campaign_id = _campaign_id(campaign_id)
    if state["schema_version"] != 2 or campaign_id not in sample["campaigns"]:
        raise RoutingError("campaign is unavailable")
    return sample["campaigns"][campaign_id]["tasks"].get(task_id)


def observation_campaign_open(*, codex_home: Path, policy: Policy, campaign_id: str,
                              purpose: str) -> dict[str, Any]:
    """Explicitly open one finite campaign without reserving a task."""
    campaign_id = _campaign_id(campaign_id)
    purpose = _observation_text(purpose, "campaign purpose", MAX_OBSERVATION_SCOPE_CHARS)
    sampling_key = observation_sampling_key(policy)
    with _observation_write_lock(codex_home, sampling_key):
        state = _read_observation_state(codex_home, sampling_key)
        sample = state["samples"][sampling_key]
        if state["schema_version"] == 1:
            state["schema_version"] = 2
            sample["campaigns"] = {}
            sample["active_campaign"] = None
        campaigns = sample["campaigns"]
        if campaign_id in campaigns:
            raise RoutingError("campaign id already exists")
        if len(campaigns) >= 32:
            raise RoutingError("campaign history is full")
        active = sample["active_campaign"]
        if active is not None and not _campaign_stats(campaigns[active])["terminal"]:
            raise RoutingError("active campaign is not finished")
        campaigns[campaign_id] = {"created_at": _utc_now(), "purpose": purpose, "tasks": {},
                                  "closed_at": None, "close_reason": None, "enrollments": {}, "captures": {},
                                  "arm": None, "arm_failure": None, "classifications": {}}
        sample["active_campaign"] = campaign_id
        _write_observation_state(codex_home, sampling_key, state)
    return {"state": "opened", "sampling_key": sampling_key, "campaign_id": campaign_id}


def observation_arm(*, codex_home: Path, sampling_key: str, campaign_id: str,
                    task_id: str, enrollment: dict[str, Any]) -> dict[str, Any]:
    """Arm one root for its next prompt without creating a work record."""
    sampling_key = _observation_sampling_key(sampling_key)
    campaign_id = _campaign_id(campaign_id)
    task_id = _observation_task_id(task_id)
    if not isinstance(enrollment, dict):
        raise RoutingError("enrollment is invalid")
    _enrollment(enrollment, enrollment.get("root_id"))
    if enrollment.get("start_evidence_ref") is not None:
        raise RoutingError("arm start evidence must come from the next prompt")
    with _observation_write_lock(codex_home, sampling_key):
        state = _read_observation_state(codex_home, sampling_key)
        sample = state["samples"][sampling_key]
        if state["schema_version"] != 2 or sample["active_campaign"] != campaign_id:
            raise RoutingError("campaign is not active")
        campaign = sample["campaigns"][campaign_id]
        if campaign["arm"] is not None or _campaign_stats(campaign)["remaining"] == 0:
            raise RoutingError("campaign cannot be armed")
        if task_id in sample["tasks"] or any(task_id in item["tasks"] for item in sample["campaigns"].values()):
            raise RoutingError("observation task id is already in use")
        root = enrollment["root_id"]
        if any(campaign["tasks"][key]["state"] == "ongoing" and value["root_id"] == root
               for key, value in campaign["enrollments"].items()):
            raise RoutingError("root already has an ongoing observation")
        campaign["arm"] = {"task_id": task_id, "enrollment": enrollment, "armed_at": _utc_now()}
        campaign["arm_failure"] = None
        _write_observation_state(codex_home, sampling_key, state)
    return {"state": "armed", "campaign_id": campaign_id, "sampling_key": sampling_key}


def observation_confirm_normal(*, codex_home: Path, sampling_key: str, campaign_id: str,
                               task_id: str, evidence_ref: str) -> dict[str, Any]:
    sampling_key = _observation_sampling_key(sampling_key)
    campaign_id = _campaign_id(campaign_id)
    task_id = _observation_task_id(task_id)
    evidence_ref = _observation_text(evidence_ref, "normal work evidence")
    with _observation_write_lock(codex_home, sampling_key):
        state = _read_observation_state(codex_home, sampling_key)
        sample = state["samples"][sampling_key]
        if state["schema_version"] != 2 or campaign_id not in sample["campaigns"]:
            raise RoutingError("campaign is unavailable")
        campaign = sample["campaigns"][campaign_id]
        classification = campaign["classifications"].get(task_id)
        if classification is None or classification["state"] != "provisional" or campaign["tasks"][task_id]["state"] != "ongoing":
            raise RoutingError("provisional work is unavailable")
        classification.update(state="normal", evidence_ref=evidence_ref)
        _write_observation_state(codex_home, sampling_key, state)
    return {"state": "confirmed", "task_id": task_id}


def observation_enroll(*, codex_home: Path, sampling_key: str, campaign_id: str,
                       task_id: str, enrollment: dict[str, Any]) -> dict[str, Any]:
    """Attach an explicit thread scope to an already reserved normal task."""
    sampling_key = _observation_sampling_key(sampling_key)
    campaign_id = _campaign_id(campaign_id)
    task_id = _observation_task_id(task_id)
    if not isinstance(enrollment, dict):
        raise RoutingError("enrollment is invalid")
    _enrollment(enrollment, enrollment.get("root_id"))
    with _observation_write_lock(codex_home, sampling_key):
        state = _read_observation_state(codex_home, sampling_key)
        sample = state["samples"][sampling_key]
        if state["schema_version"] != 2 or sample["active_campaign"] != campaign_id:
            raise RoutingError("campaign is not active")
        campaign = sample["campaigns"][campaign_id]
        record = campaign["tasks"].get(task_id)
        if record is None or record["state"] != "ongoing":
            raise RoutingError("normal observation task is not ongoing")
        previous = campaign["enrollments"].get(task_id)
        if previous is not None:
            if previous["root_id"] != enrollment["root_id"] or any(
                    row not in enrollment["threads"] for row in previous["threads"]):
                raise RoutingError("enrollment must retain existing threads")
        if previous is not None and (previous.get("allow_descendants", False) != enrollment.get("allow_descendants", False)
                                     or previous.get("start_evidence_ref") != enrollment.get("start_evidence_ref")):
            raise RoutingError("enrollment scope and start evidence cannot change")
        for other_task, other in campaign["enrollments"].items():
            if other_task != task_id and other["root_id"] == enrollment["root_id"] and campaign["tasks"][other_task]["state"] == "ongoing":
                raise RoutingError("root thread is already enrolled for an ongoing task")
        campaign["enrollments"][task_id] = enrollment
        capture = campaign["captures"].setdefault(task_id, {"threads": {}, "issues": [], "root_stopped": False,
            "baselines": {}, "verification": {"state": "missing", "first": {}, "second": {}, "reason": "not verified", "continuity_ref": None}})
        if previous is not None and previous != enrollment:
            capture["verification"] = {"state": "missing", "first": {}, "second": {}, "reason": "enrollment changed", "continuity_ref": None}
        _write_observation_state(codex_home, sampling_key, state)
    return {"state": "enrolled", "sampling_key": sampling_key,
            "campaign_id": campaign_id, "task_id": task_id}


def observation_campaign_close(*, codex_home: Path, sampling_key: str, campaign_id: str,
                               reason: str) -> dict[str, Any]:
    """Stop future reservations without discarding completed evidence."""
    sampling_key = _observation_sampling_key(sampling_key)
    campaign_id = _campaign_id(campaign_id)
    reason = _observation_text(reason, "campaign close reason", MAX_OBSERVATION_SCOPE_CHARS)
    with _observation_write_lock(codex_home, sampling_key):
        state = _read_observation_state(codex_home, sampling_key)
        sample = state["samples"][sampling_key]
        if state["schema_version"] != 2 or sample["active_campaign"] != campaign_id:
            raise RoutingError("campaign is not active")
        campaign = sample["campaigns"][campaign_id]
        if campaign["closed_at"] is not None:
            if campaign["close_reason"] != reason:
                raise RoutingError("campaign was already closed")
            return {"state": "closed", "campaign_id": campaign_id, "sampling_key": sampling_key}
        if _campaign_stats(campaign)["pending"]:
            raise RoutingError("campaign has pending tasks")
        campaign["closed_at"] = _utc_now()
        campaign["close_reason"] = reason
        campaign["arm"] = None
        _write_observation_state(codex_home, sampling_key, state)
    return {"state": "closed", "campaign_id": campaign_id, "sampling_key": sampling_key}


def observation_status(
    *, codex_home: Path, policy: Policy | None = None, sampling_key: str | None = None,
    campaign_id: str | None = None
) -> dict[str, Any]:
    """Read one sampling group without reading or writing any other group."""

    if sampling_key is None:
        if policy is None:
            raise RoutingError("observation status needs a policy or sampling key")
        sampling_key = observation_sampling_key(policy)
    else:
        sampling_key = _observation_sampling_key(sampling_key)
    try:
        state = _read_observation_state(codex_home, sampling_key)
        sample = state["samples"][sampling_key]
        tasks = list(sample["tasks"].values())
    except (RoutingError, OSError):
        return {
            "state": "unknown",
            "remaining": None,
            "recorded": None,
            "pending": None,
            "completed": None,
            "missing": None,
        }
    pending = sum(record["state"] == "ongoing" for record in tasks)
    completed = sum(record["state"] == "completed" for record in tasks)
    missing = sum(
        record["state"] != "ongoing" and record["measurement"]["state"] != "measured"
        for record in tasks
    )
    active_id = sample.get("active_campaign")
    if campaign_id is not None:
        campaign_id = _campaign_id(campaign_id)
        if state["schema_version"] != 2 or campaign_id not in sample["campaigns"]:
            raise RoutingError("campaign is unavailable")
    else:
        campaign_id = active_id
    campaign = (_campaign_stats(sample["campaigns"][campaign_id]) if campaign_id else None)
    return {
        "state": "valid",
        "sampling_key": sampling_key,
        "campaign_id": campaign_id,
        "campaign": campaign,
        "remaining": 3 - len(tasks),
        "recorded": len(tasks),
        "pending": pending,
        "completed": completed,
        "missing": missing,
        "task_ids": [record["task_id"] for record in tasks],
    }


def observation_start(*, codex_home: Path, policy: Policy, task_id: str, kind: str = "normal",
                      campaign_id: str | None = None) -> dict[str, Any]:
    """Reserve one normal-work observation, idempotently by the parent task key."""

    task_id = _observation_task_id(task_id)
    if kind != "normal":
        if kind not in {"audit", "config"}:
            raise RoutingError("observation kind is invalid")
        return {"state": "skipped", "reason": "non_normal_work", "recorded": False}
    sampling_key = observation_sampling_key(policy)
    if campaign_id is None:
        previous = _read_observation_state(codex_home, sampling_key)
        if _find_observation_task(previous, sampling_key, task_id) is None:
            return {"state": "no_campaign", "recorded": False, "sampling_key": sampling_key}
    with _observation_write_lock(codex_home, sampling_key):
        state = _read_observation_state(codex_home, sampling_key)
        existing = _find_observation_task(state, sampling_key, task_id, campaign_id)
        now = _utc_now()
        if existing is not None:
            existing["updated_at"] = now
            _write_observation_state(codex_home, sampling_key, state)
            return {"state": "existing", "recorded": True, "sampling_key": sampling_key}
        sample = state["samples"][sampling_key]
        if campaign_id is None:
            return {"state": "no_campaign", "recorded": False, "sampling_key": sampling_key}
        else:
            if sample["active_campaign"] != campaign_id:
                raise RoutingError("campaign is not active")
            campaign = sample["campaigns"][campaign_id]
            if _campaign_stats(campaign)["remaining"] == 0:
                return {"state": "full", "recorded": False, "sampling_key": sampling_key,
                        "campaign_id": campaign_id}
            tasks = campaign["tasks"]
        # Unique task IDs prevent an old or different campaign from being relabelled.
        if task_id in sample["tasks"] or (state["schema_version"] == 2 and
                any(task_id in c["tasks"] for c in sample["campaigns"].values())):
            raise RoutingError("observation task id is already in use")
        tasks[task_id] = {
            "task_id": task_id,
            "sampling_key": sampling_key,
            "policy": _observation_policy_metadata(policy, sampling_key),
            "state": "ongoing",
            "measurement": {"state": "missing", "reason": "completion is not recorded"},
            "started_at": now,
            "updated_at": now,
            "completed_at": None,
            "scope": None,
            "rework": None,
            "verification": None,
            "runs": [],
        }
        _write_observation_state(codex_home, sampling_key, state)
    return {"state": "started", "recorded": True, "sampling_key": sampling_key, "campaign_id": campaign_id}


def observation_complete(
    *, codex_home: Path, sampling_key: str, task_id: str, completion: dict[str, Any],
    campaign_id: str | None = None
) -> dict[str, Any]:
    """Close one reserved group without loading current policy or other groups."""

    task_id = _observation_task_id(task_id)
    sampling_key = _observation_sampling_key(sampling_key)
    _validate_observation_completion(completion)
    with _observation_write_lock(codex_home, sampling_key):
        state = _read_observation_state(codex_home, sampling_key)
        record = _find_observation_task(state, sampling_key, task_id, campaign_id)
        if record is None:
            raise RoutingError("observation task is not reserved")
        if campaign_id is not None:
            campaign = state["samples"][sampling_key]["campaigns"][campaign_id]
            classification = campaign["classifications"].get(task_id)
            if classification is not None and classification["state"] == "provisional":
                if completion["state"] != "interrupted":
                    raise RoutingError("provisional work requires normal-work confirmation")
                classification.update(state="excluded", evidence_ref=completion["measurement"]["reason"])
        hook_runs = [run for run in completion["runs"] if run["usage"].get("basis") == "hook_request_counters"]
        if completion["measurement"]["state"] == "measured" and hook_runs:
            if campaign_id is None or len(hook_runs) != len(completion["runs"]) or completion["verification"]["reference"] is None:
                raise RoutingError("hook measurement requires a verified campaign capture")
            from .observation_capture import export_capture
            campaign = state["samples"][sampling_key]["campaigns"][campaign_id]
            if task_id not in campaign["captures"] or task_id not in campaign["enrollments"]:
                raise RoutingError("hook capture is unavailable")
            evidence = completion["verification"]["reference"]
            captured = export_capture(campaign["enrollments"][task_id], campaign["captures"][task_id],
                                      evidence_ref=evidence, coverage_ref=evidence, work_completed_ref=evidence)
            expected = {item["thread_id"] + ":" + item["turn_id"]: item for item in captured["runs"]}
            if captured["state"] != "measured" or len(expected) != len(hook_runs):
                raise RoutingError("hook capture is incomplete")
            names = ("input_tokens", "cache_input_tokens", "output_tokens", "reasoning_tokens")
            for run in hook_runs:
                item = expected.pop(run["run_id"], None)
                if item is None or run["relationship"] != item["relationship"] or any(run["usage"][name] != item["usage"][name] for name in names):
                    raise RoutingError("hook completion does not match captured counters")
            if expected:
                raise RoutingError("hook completion omits captured counters")
        if record["verification"] is not None and record["verification"]["reference"] is not None and completion["verification"]["reference"] is None:
            raise RoutingError("completion must retain existing verification evidence")
        # Each completion is a full snapshot.  A resumed task must not erase
        # earlier runs or replace acquired evidence with unknown values.
        incoming_runs = {run["run_key"]: run for run in completion["runs"]}
        for previous in record["runs"]:
            current = incoming_runs.get(previous["run_key"])
            if current is None:
                raise RoutingError("completion must retain existing runs")
            if current["relationship"] != previous["relationship"] or (
                previous["run_id"] is not None and current["run_id"] != previous["run_id"]
            ):
                raise RoutingError("a different run needs a new run key")
            if previous["state"] == "completed" and current["state"] != "completed":
                raise RoutingError("completion must retain completed run evidence")
            old_basis = previous["usage"].get("basis", "separate_counters")
            new_basis = current["usage"].get("basis", "separate_counters")
            if old_basis != new_basis:
                raise RoutingError("completion must retain usage basis")
            usage_names = (("start_tokens", "end_tokens", "total_tokens")
                           if old_basis == "aggregate_total_tokens" else
                           ("input_tokens", "cache_input_tokens", "output_tokens", "reasoning_tokens"))
            for section, names in (
                ("usage", usage_names),
                ("observed", ("model", "effort")),
                ("requested", ("model", "effort")),
            ):
                if any(previous[section][name] is not None and current[section][name] is None for name in names):
                    raise RoutingError("completion must retain acquired run values")
        now = _utc_now()
        record.update(
            {
                "state": completion["state"],
                "scope": completion["scope"],
                "measurement": completion["measurement"],
                "verification": completion["verification"],
                "rework": completion["rework"],
                "runs": completion["runs"],
                "completed_at": now,
                "updated_at": now,
            }
        )
        _write_observation_state(codex_home, sampling_key, state)
    return {
        "state": "closed",
        "observation_state": completion["state"],
        "measurement_state": completion["measurement"]["state"],
        "sampling_key": sampling_key,
    }
