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
    _canonical_json, _is_link_or_reparse, _is_schema_version_one,
    _load_json, _safe_mkdir, _write_atomic,
)

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
            and all(run["usage"][field] is not None for field in token_fields)
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


def _validate_observation_state(value: Any, sampling_key: str) -> dict[str, Any]:
    sampling_key = _observation_sampling_key(sampling_key)
    if not isinstance(value, dict) or set(value) != {"schema_version", "samples"}:
        raise RoutingError("observation state is invalid")
    if not _is_schema_version_one(value.get("schema_version")) or not isinstance(value.get("samples"), dict):
        raise RoutingError("observation state is invalid")
    if set(value["samples"]) != {sampling_key}:
        raise RoutingError("observation state has an unexpected sampling key")
    sample = value["samples"][sampling_key]
    if not isinstance(sample, dict) or set(sample) != {"tasks"} or not isinstance(sample.get("tasks"), dict):
        raise RoutingError("observation sample is invalid")
    if len(sample["tasks"]) > 3:
        raise RoutingError("observation sample exceeds its limit")
    for task_id, record in sample["tasks"].items():
        _observation_task_id(task_id)
        _validate_observation_record(record, sampling_key, task_id)
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


def _find_observation_task(state: dict[str, Any], sampling_key: str, task_id: str) -> dict[str, Any] | None:
    sampling_key = _observation_sampling_key(sampling_key)
    return state["samples"][sampling_key]["tasks"].get(task_id)


def observation_status(
    *, codex_home: Path, policy: Policy | None = None, sampling_key: str | None = None
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
    return {
        "state": "valid",
        "sampling_key": sampling_key,
        "remaining": 3 - len(tasks),
        "recorded": len(tasks),
        "pending": pending,
        "completed": completed,
        "missing": missing,
        "task_ids": [record["task_id"] for record in tasks],
    }


def observation_start(*, codex_home: Path, policy: Policy, task_id: str, kind: str = "normal") -> dict[str, Any]:
    """Reserve one normal-work observation, idempotently by the parent task key."""

    task_id = _observation_task_id(task_id)
    if kind != "normal":
        if kind not in {"audit", "config"}:
            raise RoutingError("observation kind is invalid")
        return {"state": "skipped", "reason": "non_normal_work", "recorded": False}
    sampling_key = observation_sampling_key(policy)
    with _observation_write_lock(codex_home, sampling_key):
        state = _read_observation_state(codex_home, sampling_key)
        existing = _find_observation_task(state, sampling_key, task_id)
        now = _utc_now()
        if existing is not None:
            existing["updated_at"] = now
            _write_observation_state(codex_home, sampling_key, state)
            return {"state": "existing", "recorded": True, "sampling_key": sampling_key}
        sample = state["samples"][sampling_key]
        if len(sample["tasks"]) >= 3:
            return {"state": "full", "recorded": False, "sampling_key": sampling_key}
        sample["tasks"][task_id] = {
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
    return {"state": "started", "recorded": True, "sampling_key": sampling_key}


def observation_complete(
    *, codex_home: Path, sampling_key: str, task_id: str, completion: dict[str, Any]
) -> dict[str, Any]:
    """Close one reserved group without loading current policy or other groups."""

    task_id = _observation_task_id(task_id)
    sampling_key = _observation_sampling_key(sampling_key)
    _validate_observation_completion(completion)
    with _observation_write_lock(codex_home, sampling_key):
        state = _read_observation_state(codex_home, sampling_key)
        record = _find_observation_task(state, sampling_key, task_id)
        if record is None:
            raise RoutingError("observation task is not reserved")
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
            for section, names in (
                ("usage", ("input_tokens", "cache_input_tokens", "output_tokens", "reasoning_tokens")),
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
