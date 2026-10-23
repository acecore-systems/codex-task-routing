#!/usr/bin/env python3
"""Deterministic session proposals and cloud handoff checks; never execute work."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

from routing_core.common import RoutingError, _canonical_json, _load_json

ROUTES = {"continue_current", "subagent", "existing_local_session", "new_local_session",
          "codex_cloud", "chat_pro"}
SEPARATION_REASONS = {"separate_goal", "independent_deliverable", "separate_repo_or_pr",
                      "ongoing_management"}
REASONS = SEPARATION_REASONS | {"long_context", "quota_avoidance", "same_model_transfer"}
DECISION_REASONS = REASONS | {"long_running"}
HOST_FACTS = {"subagent", "new_session", "codex_cloud", "chat_pro", "materials_transfer"}
CHECK_STATES = {"unknown", "verified", "permission_checked", "blocked", "unavailable",
                "confirmation_required"}
CONFIRMED = {"verified", "permission_checked"}
OBSERVED_CHECKS = {"environment", "repository_revision", "test_environment"}
CHECKS = {"host_permission", "inspect_capability", "inspect_authorization",
          "dispatch_capability", "target_authorization", "materials_authorization",
          "environment", "repository_revision", "test_environment",
          "receive_capability", "receive_authorization"}
NATIVE_CHECKS = CHECKS | {"native_policy", "target_identity", "previous_submission",
                          "wait_capability", "wait_authorization"}
OPERATIONS = {"read", "edit", "test", "commit", "push", "create_draft_pr"}
SUBMISSION_STATES = {"not_sent", "sent", "unknown_outcome", "collected", "accepted"}
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class SessionPlanError(ValueError):
    """Fixed diagnostics: never echo caller material, paths or OS errors."""


def _object(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise SessionPlanError("invalid object fields")


def _schema(value):
    if type(value) is not int or value != 1:
        raise SessionPlanError("unsupported schema version")


def _native_schema(value):
    if type(value) is not int or value != 2:
        raise SessionPlanError("unsupported native schema version")


def validate_native_target(value, *, observed=False):
    """Only an existing Codex durable task; never alias Work or a UI tab."""
    _object(value, {"kind", "host_id", "task_id"})
    _enum(value["kind"], {"codex", "chatgpt_work", "unknown"} if observed else {"codex"})
    _identifier(value["host_id"])
    _identifier(value["task_id"])
    if not observed and value["host_id"] != "durable":
        raise SessionPlanError("existing durable Codex target required")
    return value


def _text(value, maximum=512):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 for char in value)):
        raise SessionPlanError("invalid metadata string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SessionPlanError("invalid metadata encoding") from exc
    return value


def _identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise SessionPlanError("invalid identifier")
    return value


def _enum(value, choices):
    if not isinstance(value, str) or value not in choices:
        raise SessionPlanError("unsupported state")
    return value


def _truth(value):
    if value is not None and type(value) is not bool:
        raise SessionPlanError("expected boolean or null")
    return value


def _list(value, *, choices=None, maximum=32, allow_empty=False):
    if not isinstance(value, list) or len(value) > maximum or (not value and not allow_empty):
        raise SessionPlanError("invalid metadata list")
    for item in value:
        _enum(item, choices) if choices is not None else _text(item)
    if len(set(value)) != len(value):
        raise SessionPlanError("duplicate metadata item")
    return value


def _digest(value):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise SessionPlanError("invalid SHA-256")
    return value


def _revision(value):
    if not isinstance(value, str) or not REVISION.fullmatch(value):
        raise SessionPlanError("expected a full source revision")
    return value


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise SessionPlanError("expected a timestamp with timezone") from exc


def select_session(facts):
    """Propose an execution context independently of model/effort selection."""
    _object(facts, {"schema_version", "candidate", "current_owner_suitable", "existing_owner",
                    "independent_work_unit", "handoff_benefit_exceeds_cost", "reasons"})
    _schema(facts["schema_version"])
    candidate = _enum(facts["candidate"], ROUTES)
    for name in ("current_owner_suitable", "independent_work_unit", "handoff_benefit_exceeds_cost"):
        _truth(facts[name])
    reasons = _list(facts["reasons"], choices=REASONS, allow_empty=True)
    existing = facts["existing_owner"]
    if existing is not None:
        _object(existing, {"owner_id", "route", "suitable"})
        _identifier(existing["owner_id"])
        _enum(existing["route"], ROUTES)
        _truth(existing["suitable"])
    result = {"schema_version": 1, "candidate": candidate, "route": "continue_current",
              "proposal_only": True, "execution_authorized": False, "gaps": []}
    if facts["current_owner_suitable"] is True or candidate == "continue_current":
        return {**result, "next_action": "continue_current", "reason": "keep_current_owner"}
    if existing is not None and existing["suitable"] is True:
        return {**result, "route": existing["route"], "owner_id": existing["owner_id"],
                "next_action": "reuse_existing_owner", "reason": "suitable_owner_exists"}
    if (candidate == "existing_local_session" and existing is not None
            and existing["route"] == candidate and existing["suitable"] is False):
        return {**result, "next_action": "choose_another_context", "reason": "existing_owner_not_suitable",
                "gaps": ["existing_local_session_not_suitable"]}
    if not SEPARATION_REASONS.intersection(reasons):
        return {**result, "next_action": "continue_current", "reason": "no_independent_purpose"}
    if facts["independent_work_unit"] is False or facts["handoff_benefit_exceeds_cost"] is False:
        return {**result, "next_action": "continue_current", "reason": "handoff_not_worthwhile"}
    for name in ("current_owner_suitable", "independent_work_unit", "handoff_benefit_exceeds_cost"):
        if facts[name] is None:
            result["gaps"].append(name + "_unknown")
    # null means no owner has been identified, not proof that no owner exists.
    if existing is None or existing["suitable"] is None:
        result["gaps"].append("existing_owner_reuse_unconfirmed")
    if candidate == "existing_local_session" and (existing is None or existing["route"] != candidate):
        result["gaps"].append("existing_local_session_unconfirmed")
    return {**result, "route": candidate, "reason": "independent_context_proposal",
            "next_action": "check_owner_reuse" if "existing_owner_reuse_unconfirmed" in result["gaps"]
                           else "assess_proposal" if result["gaps"] else "inspect_candidate",
            "required_gate": "existing_chat_policy" if candidate == "chat_pro" else "current_host_policy"}


def recommend_session(facts):
    """Compare contexts for a work unit, without choosing a candidate in advance.

    ``owner.status=continuing`` means ownership of this same completion unit,
    not merely that the current parent could do the work. Missing optional
    capability/host facts stay unknown; none of these declarations grant access.
    The original session/cloud schema-1 commands remain separate and unchanged.
    """
    _object(facts, {"schema_version", "work", "owner", "execution", "host"})
    _schema(facts["schema_version"])
    work, owner, execution, host = (facts[key] for key in ("work", "owner", "execution", "host"))
    _object(work, {"purpose", "kind", "independent", "tightly_coupled", "handoff_benefit", "reasons"},
            {"repository"})
    _text(work["purpose"])
    if "repository" in work:
        _text(work["repository"])
    _enum(work["kind"], {"repository", "analysis", "support"})
    for key in ("independent", "tightly_coupled", "handoff_benefit"):
        _truth(work[key])
    _list(work["reasons"], choices=DECISION_REASONS, allow_empty=True)
    _object(owner, {"status"}, {"owner_id", "route", "suitable"})
    _enum(owner["status"], {"new", "continuing", "unknown"})
    if owner["status"] == "continuing":
        _object(owner, {"status", "owner_id", "route", "suitable"})
        _identifier(owner["owner_id"])
        _enum(owner["route"], ROUTES)
        _truth(owner["suitable"])
    elif set(owner) != {"status"}:
        raise SessionPlanError("only continuing owners have identity and suitability")
    _object(execution, set(), {"local_required", "cloud", "chat_can_complete"})
    for key in ("local_required", "chat_can_complete"):
        _truth(execution.get(key))
    cloud = execution.get("cloud", {})
    _object(cloud, set(), {"available", "materials_ready", "tests_ready", "environment", "parallel"})
    for key in ("available", "materials_ready", "tests_ready"):
        _truth(cloud.get(key))
    environment = cloud.get("environment", {"status": "unknown"})
    _object(environment, {"status"}, {"repository", "environment_id", "ready", "setup_supported"})
    _enum(environment["status"], {"unknown", "absent", "existing"})
    if environment["status"] == "unknown":
        _object(environment, {"status"})
    elif environment["status"] == "absent":
        _object(environment, {"status", "repository", "setup_supported"})
    else:
        _object(environment, {"status", "repository", "environment_id", "ready"}, {"setup_supported"})
        _identifier(environment["environment_id"])
        _truth(environment["ready"])
    if "repository" in environment:
        _text(environment["repository"])
    _truth(environment.get("setup_supported"))
    parallel = cloud.get("parallel")
    if "parallel" in cloud:
        _object(parallel, {"scopes_independent", "branches_isolated", "integration_owner",
                           "host_allows", "delegator_has_independent_work"})
        for key in ("scopes_independent", "branches_isolated", "host_allows", "delegator_has_independent_work"):
            _truth(parallel[key])
        if parallel["integration_owner"] is not None:
            _identifier(parallel["integration_owner"])
    _object(host, set(), HOST_FACTS)
    for value in host.values():
        _truth(value)

    assessments = []

    def assess(route, why, checks=(), exclusions=()):
        gaps, rejected = [], []
        for key, value in exclusions:
            if value is True:
                rejected.append(key)
        for key, value in checks:
            if value is None:
                gaps.append(key + ":unknown")
            elif value is False:
                rejected.append(key + ":false")
        state = ("blocked" if any(key.startswith("host.") for key in rejected)
                 else "unavailable" if "cloud.available:false" in rejected
                 else "not_suitable" if rejected else "conditional" if gaps else "eligible")
        item = {"route": route, "suitability": state, "why": [why] + rejected, "gaps": gaps}
        assessments.append(item)
        return item

    local_only = execution.get("local_required")
    separate = bool(SEPARATION_REASONS.intersection(work["reasons"]))
    split_exclusions = [("tight_coupling", work["tightly_coupled"]),
                        ("handoff_cost_exceeds_benefit", work["handoff_benefit"] is False),
                        ("not_independent_unit", work["independent"] is False),
                        ("no_separation_reason", not separate)]
    cloud_option = assess("codex_cloud", "independent_repository_completion",
        [("cloud." + key, cloud.get(key)) for key in ("available", "materials_ready", "tests_ready")]
        + [("host.codex_cloud", host.get("codex_cloud")),
           ("host.materials_transfer", host.get("materials_transfer"))],
        split_exclusions + [("local_execution_required", local_only),
                            ("not_repository_work", work["kind"] != "repository")])
    chat_option = assess("chat_pro", "complete_with_existing_chat_policy",
        [("execution.chat_can_complete", execution.get("chat_can_complete")),
         ("host.chat_pro", host.get("chat_pro")),
         ("host.materials_transfer", host.get("materials_transfer"))],
        split_exclusions + [("local_execution_required", local_only)])
    local_option = assess("new_local_session", "independent_local_ownership",
                         [("host.new_session", host.get("new_session"))], split_exclusions)
    child_option = assess("subagent", "bounded_support_for_same_goal",
                         [("host.subagent", host.get("subagent"))],
                         [("not_same_goal_support", work["kind"] != "support" or work["independent"] is True),
                          ("tight_coupling", work["tightly_coupled"]),
                          ("handoff_cost_exceeds_benefit", work["handoff_benefit"] is False)])
    current_option = assess("continue_current", "current_context_available")
    output = {"schema_version": 1, "decision_kind": "recommendation", "proposal_only": True,
              "execution_authorized": False, "gaps": []}

    def finish(option, why, action, gaps=(), owner_id=None):
        combined = list(dict.fromkeys([*gaps, *option["gaps"]]))
        route = option["route"]
        required = ["purpose_and_scope", "completion_criteria", "allowed_operations", "return_conditions"]
        if route in {"codex_cloud", "new_local_session", "existing_local_session", "subagent"}:
            required += ["owner_and_endpoint", "latest_progress_and_expected_revision"]
        if route == "codex_cloud":
            required += ["repository_branch_environment", "startup_cwd_and_guidance",
                         "materials_hash_and_transfer_scope", "required_tests_and_evidence",
                         "request_id_and_original_tab_task", "durable_claim_and_submission_state",
                         "repository_environment_reuse", "parallel_scopes_branches_and_integration_owner"]
        if route == "chat_pro":
            required += ["existing_chat_policy_actor_capabilities_and_authorization",
                         "request_id_input_hash_and_original_tab"]
        result = {**output, "route": route, "why": why, "gaps": combined,
                  "alternatives": [item for item in assessments if item["route"] != route],
                  "proposal": {"purpose": work["purpose"], "route": route,
                               "conditional": bool(combined)},
                  "handoff_required": required, "next_action": action}
        if owner_id is not None:
            result["owner_id"] = owner_id
        return result

    # Preserve an active, suitable owner through fixes, tests, acceptance and
    # collection. Status requests or a timeout never make a new work unit.
    if owner["status"] == "continuing" and owner["suitable"] is True:
        # New-dispatch capability facts are not gates on resuming the original.
        return finish({"route": owner["route"], "gaps": []}, ["keep_suitable_owner_of_same_work_unit"],
                      "continue_current" if owner["route"] == "continue_current" else "reuse_existing_owner",
                      owner_id=owner["owner_id"])
    if work["tightly_coupled"] is True or work["handoff_benefit"] is False:
        return finish(current_option, ["tight_coupling" if work["tightly_coupled"] is True
                                      else "handoff_cost_exceeds_benefit"], "continue_current")

    common_gaps = ["work." + key + ":unknown" for key in
                   ("independent", "tightly_coupled", "handoff_benefit") if work[key] is None]
    if owner["status"] == "unknown" or (owner["status"] == "continuing" and owner["suitable"] is None):
        common_gaps.append("owner_reuse:unknown")
    # Unknown local dependence must be checked before leaving this environment.
    if local_only is None:
        common_gaps.append("execution.local_required:unknown")
    if work["independent"] is False or not separate:
        if work["kind"] == "support" and work["independent"] is False and child_option["suitability"] in {"eligible", "conditional"}:
            action = ("check_owner_reuse" if "owner_reuse:unknown" in common_gaps
                      else "assess_work_unit" if common_gaps else "resolve_candidate_gaps"
                      if child_option["gaps"] else "review_subagent_contract")
            return finish(child_option, ["same_goal_bounded_support"], action, common_gaps)
        return finish(current_option, ["no_independent_purpose"], "assess_work_unit" if common_gaps else "continue_current", common_gaps)

    usable = {"eligible", "conditional"}
    # Choose execution context before the specialist. Suitable independent repo
    # work is Cloud-first even when approved Chat tools could also finish it.
    if cloud_option["suitability"] in usable:
        choice, why, action = cloud_option, "cloud_for_independent_repository_unit", "prepare_cloud_handoff"
    elif execution.get("chat_can_complete") is True and chat_option["suitability"] in usable:
        choice, why, action = chat_option, "chat_can_finish_complete_unit", "evaluate_existing_chat_policy"
    elif work["kind"] == "analysis" and chat_option["suitability"] in usable:
        choice, why, action = chat_option, "assess_chat_for_complete_analysis", "evaluate_existing_chat_policy"
    elif local_option["suitability"] in usable:
        choice, why, action = local_option, "independent_unit_needs_local_context", "review_new_session_contract"
    else:
        return finish(current_option, ["no_available_separate_context"], "report_context_limitations", common_gaps)
    lifecycle = None
    if choice["route"] == "codex_cloud":
        lifecycle = _cloud_lifecycle(work, environment, parallel)
        action = lifecycle["next_action"]
        choice = {**choice, "gaps": [*choice["gaps"], *lifecycle["gaps"]]}
    if common_gaps:
        action = "check_owner_reuse" if "owner_reuse:unknown" in common_gaps else "assess_work_unit"
    elif choice["gaps"] and lifecycle is None:
        action = "resolve_candidate_gaps"
    elif lifecycle is not None and cloud_option["gaps"]:
        action = "resolve_candidate_gaps"
    result = finish(choice, [why], action, common_gaps)
    if lifecycle is not None:
        result["cloud_lifecycle"] = lifecycle
    return result


def _cloud_lifecycle(work, environment, parallel):
    """Plan UI environment reuse/setup and independent tasks, never execute."""
    status = environment["status"]
    result = {"environment_status": status, "planning_only": True,
              "creation_transport": "codex_cloud_ui", "native_creation_supported": False,
              "session_action": "new_task_for_independent_unit", "gaps": []}
    if status == "unknown":
        result.update(next_action="inspect_cloud_environment", gaps=["cloud.environment:unknown"])
    elif work.get("repository") is None or work["repository"] != environment["repository"]:
        result.update(next_action="inspect_repository_environment_identity",
                      gaps=["work.repository:unknown" if work.get("repository") is None
                            else "cloud.environment.repository:mismatch"])
    elif status == "absent" or environment["ready"] is not True:
        result["gaps"].append("cloud.environment:absent" if status == "absent" else "cloud.environment.ready:unverified")
        result["environment_action"] = ("create_prepare_publish" if status == "absent"
                                        else "prepare_test_republish_existing")
        result["next_action"] = "create_prepare_publish_cloud_environment" if status == "absent" else "prepare_existing_cloud_environment"
        if environment.get("setup_supported") is not True:
            state = "false" if environment.get("setup_supported") is False else "unknown"
            result["next_action"] = "resolve_cloud_environment_setup_capability"
            result["gaps"].append("cloud.environment.setup_supported:" + state)
        if status == "existing" and environment["ready"] is None:
            # Unknown readiness needs inspection before preparing or republishing.
            result.update(next_action="inspect_cloud_environment_readiness",
                          gaps=["cloud.environment.ready:unknown"])
            result.pop("environment_action")
    else:
        result.update(environment_action="reuse_existing", environment_id=environment["environment_id"],
                      next_action="prepare_cloud_handoff")
    if parallel is not None:
        parallel_gaps = ["cloud.parallel." + key + (":unknown" if parallel[key] is None else ":false")
                         for key in ("scopes_independent", "branches_isolated", "host_allows", "delegator_has_independent_work")
                         if parallel[key] is not True]
        if parallel["integration_owner"] is None:
            parallel_gaps.append("cloud.parallel.integration_owner:unknown")
        result["parallel_sessions"] = "coordinate_before_dispatch" if parallel_gaps else "independent_sessions_allowed"
        if parallel_gaps:
            result["gaps"].extend(parallel_gaps)
            result["next_action"] = "coordinate_parallel_cloud_sessions"
    return result


def validate_handoff(value):
    _object(value, {"schema_version", "request_id", "input_sha256", "owner_id", "return_to",
                    "repository", "branch", "expected_revision", "environment_id", "endpoint",
                    "acceptance_criteria", "required_checks", "allowed_operations", "state"})
    _schema(value["schema_version"])
    for name in ("request_id", "owner_id", "return_to", "environment_id"):
        _identifier(value[name])
    _digest(value["input_sha256"])
    _revision(value["expected_revision"])
    for name in ("repository", "branch", "endpoint"):
        _text(value[name])
    _list(value["acceptance_criteria"])
    _list(value["required_checks"], allow_empty=True)
    _list(value["allowed_operations"], choices=OPERATIONS)
    state = value["state"]
    _object(state, {"submission", "task_id"})
    _enum(state["submission"], SUBMISSION_STATES)
    if state["task_id"] is not None:
        _identifier(state["task_id"])
    if state["submission"] == "not_sent" and state["task_id"] is not None:
        raise SessionPlanError("not-sent state cannot reference a submitted task")
    if state["submission"] in {"sent", "collected", "accepted"} and state["task_id"] is None:
        raise SessionPlanError("submitted state requires its original task identifier")
    return value


def handoff_sha256(value):
    """Bind the immutable scope; state advances without changing this digest."""
    validate_handoff(value)
    return hashlib.sha256(_canonical_json({k: v for k, v in value.items() if k != "state"})).hexdigest()


def _validate_live(value):
    _object(value, {"schema_version", "handoff_sha256", "surface", "transport", "actor",
                    "observed_at", "checks", "task_id"})
    _schema(value["schema_version"])
    if value["task_id"] is not None:
        _identifier(value["task_id"])
    _digest(value["handoff_sha256"])
    _enum(value["surface"], {"codex_cloud", "chatgpt_work", "normal_chat", "unknown"})
    _enum(value["transport"], {"codex_cloud_ui", "unknown"})
    actor = value["actor"]
    _object(actor, {"role", "depth", "executor_id"})
    _enum(actor["role"], {"root", "sol", "astra", "luna", "unknown"})
    if type(actor["depth"]) is not int or actor["depth"] < 0:
        raise SessionPlanError("invalid actor depth")
    _identifier(actor["executor_id"])
    _timestamp(value["observed_at"])
    _object(value["checks"], set(), CHECKS)
    for item in value["checks"].values():
        _object(item, {"state"}, {"reference"})
        _enum(item["state"], CHECK_STATES)
        if item["state"] in CONFIRMED and "reference" not in item:
            raise SessionPlanError("confirmed checks require an existing scoped reference")
        if "reference" in item:
            _text(item["reference"])
    return value


def _base(handoff, phase):
    return {"schema_version": 1, "phase": phase, "planning_only": True,
            "runtime_implemented": False, "execution_authorized": False,
            "preflight_complete": False, "request_id": handoff["request_id"],
            "input_sha256": handoff["input_sha256"], "handoff_sha256": handoff_sha256(handoff),
            "task_id": handoff["state"]["task_id"], "gaps": []}


def _accept(handoff, result, acceptance, *, native=False):
    if handoff["state"]["submission"] not in {"collected", "accepted"}:
        raise SessionPlanError("acceptance requires a collected original result")
    fields = {"schema_version", "request_id", "input_sha256", "handoff_sha256", "task_id",
                     "status", "repository", "base_revision", "change_kind", "output_revision",
                     "diff_evidence", "checks_completed", "verification_status",
                     "verification_evidence", "artifact_references"}
    _object(result, fields | ({"target", "response_reference", "correlation_evidence"} if native else set()))
    (_native_schema if native else _schema)(result["schema_version"])
    if native:
        validate_native_target(result["target"], observed=True)
        if result["response_reference"] is not None:
            _text(result["response_reference"])
        _list(result["correlation_evidence"], allow_empty=True)
    for name in ("request_id", "task_id"):
        _identifier(result[name])
    for name in ("input_sha256", "handoff_sha256"):
        _digest(result[name])
    _text(result["repository"])
    _revision(result["base_revision"])
    _enum(result["change_kind"], {"read_only", "no_change", "changed"})
    if result["output_revision"] is not None:
        _revision(result["output_revision"])
    _enum(result["status"], {"completed", "failed", "partial", "unverified"})
    _enum(result["verification_status"], {"passed", "failed", "partial", "unverified", "not_required"})
    for name in ("diff_evidence", "checks_completed", "verification_evidence", "artifact_references"):
        _list(result[name], allow_empty=True)
    _object(acceptance, {"owner_id", "decision", "criteria_checked", "reference"})
    _identifier(acceptance["owner_id"])
    _enum(acceptance["decision"], {"accepted", "needs_changes", "unverified"})
    _truth(acceptance["criteria_checked"])
    _text(acceptance["reference"])
    matched = (result["request_id"] == handoff["request_id"]
               and result["input_sha256"] == handoff["input_sha256"]
               and result["handoff_sha256"] == (native_handoff_sha256(handoff) if native else handoff_sha256(handoff))
               and result["task_id"] == (handoff["target"]["task_id"] if native else handoff["state"]["task_id"])
               and result["repository"] == handoff["repository"]
               and result["base_revision"] == handoff["expected_revision"]
               and acceptance["owner_id"] == handoff["owner_id"]
               and (not native or result["target"] == handoff["target"]))
    output = _native_base(handoff, "accept") if native else _base(handoff, "accept")
    if not matched:
        return {**output, "next_action": "return_identity_mismatch_to_root", "outcome": "unverified",
                "gaps": ["result_or_owner_identity_mismatch"]}
    output["owner_review"] = {"decision": acceptance["decision"],
                              "criteria_checked": acceptance["criteria_checked"]}
    if result["status"] in {"failed", "partial", "unverified"}:
        return {**output, "next_action": "return_to_same_owner" if acceptance["decision"] == "needs_changes"
                else "report_remaining_work", "outcome": result["status"]}
    if acceptance["decision"] == "needs_changes":
        return {**output, "next_action": "return_to_same_owner", "outcome": "needs_changes"}
    if acceptance["decision"] != "accepted" or acceptance["criteria_checked"] is not True:
        return {**output, "next_action": "owner_semantic_acceptance_needed", "outcome": "unverified"}
    if native and (result["response_reference"] is None or not result["correlation_evidence"]):
        return {**output, "next_action": "correlate_original_native_response", "outcome": "unverified",
                "gaps": ["native_response_correlation_missing"]}
    if result["change_kind"] == "changed":
        change_verified = ("edit" in handoff["allowed_operations"]
                           and (bool(result["diff_evidence"]) or (result["output_revision"] is not None
                                and result["output_revision"] != result["base_revision"])))
    else:
        change_verified = (not result["diff_evidence"] and result["output_revision"] in
                           {None, result["base_revision"]})
    if not change_verified:
        return {**output, "next_action": "verify_returned_changes", "outcome": "unverified",
                "gaps": ["change_evidence_missing_or_inconsistent"]}
    required = set(handoff["required_checks"])
    verified = ((required and result["verification_status"] == "passed"
                 and required <= set(result["checks_completed"]) and bool(result["verification_evidence"]))
                or (not required and result["verification_status"] in {"passed", "not_required"}))
    if not verified:
        return {**output, "next_action": "verify_required_checks", "outcome": "unverified",
                "gaps": ["required_verification_not_confirmed"]}
    return {**output, "preflight_complete": True, "next_action": "record_accepted_result",
            "outcome": "completed"}


def validate_native_handoff(value):
    _object(value, {"schema_version", "request_id", "input_sha256", "owner_id", "return_to",
                    "repository", "branch", "expected_revision", "environment_id", "endpoint",
                    "acceptance_criteria", "required_checks", "allowed_operations", "target",
                    "previous_request_id", "state"})
    _native_schema(value["schema_version"])
    validate_native_target(value["target"])
    for key in ("request_id", "owner_id", "return_to", "environment_id"):
        _identifier(value[key])
    if value["previous_request_id"] is not None:
        _identifier(value["previous_request_id"])
        if value["previous_request_id"] == value["request_id"]:
            raise SessionPlanError("native request cannot follow itself")
    _digest(value["input_sha256"])
    _revision(value["expected_revision"])
    for key in ("repository", "branch", "endpoint"):
        _text(value[key])
    _list(value["acceptance_criteria"])
    _list(value["required_checks"], allow_empty=True)
    _list(value["allowed_operations"], choices=OPERATIONS)
    _object(value["state"], {"submission"})
    _enum(value["state"]["submission"], SUBMISSION_STATES)
    return value


def native_handoff_sha256(value):
    validate_native_handoff(value)
    return hashlib.sha256(_canonical_json({k: v for k, v in value.items() if k != "state"})).hexdigest()


def _native_base(handoff, phase):
    return {"schema_version": 2, "phase": phase, "planning_only": True,
            "runtime_implemented": False, "execution_authorized": False,
            "preflight_complete": False, "request_id": handoff["request_id"],
            "input_sha256": handoff["input_sha256"], "handoff_sha256": native_handoff_sha256(handoff),
            "target": dict(handoff["target"]), "gaps": [], "creation_supported": False}


def native_cloud_plan(handoff, *, phase="inspect", live=None, result=None, acceptance=None, now=None):
    """Plan existing-task native operations only; never call an app tool."""
    validate_native_handoff(handoff)
    _enum(phase, {"inspect", "dispatch-plan", "receive", "wait", "accept"})
    if phase == "accept":
        if live is not None:
            raise SessionPlanError("accept uses original result and owner review")
        return _accept(handoff, result, acceptance, native=True)
    if result is not None or acceptance is not None:
        raise SessionPlanError("results are only used during acceptance")
    output = _native_base(handoff, phase)
    state = handoff["state"]["submission"]
    if phase == "dispatch-plan" and state != "not_sent":
        actions = {"sent": "receive_original_task", "unknown_outcome": "reconcile_original_submission",
                   "collected": "accept_original_result", "accepted": "reuse_accepted_result"}
        return {**output, "next_action": actions[state], "gaps": ["new_submission_forbidden"]}
    if phase in {"receive", "wait"} and state == "not_sent":
        return {**output, "next_action": "inspect_existing_task", "gaps": ["continuation_not_submitted"]}
    if live is None:
        return {**output, "next_action": "inspect_host_and_existing_target", "gaps": ["live_observations_missing"]}
    _object(live, {"schema_version", "handoff_sha256", "transport", "actor", "observed_at", "checks", "target"})
    _native_schema(live["schema_version"])
    _digest(live["handoff_sha256"])
    _enum(live["transport"], {"codex_cloud_native", "unknown"})
    validate_native_target(live["target"], observed=True)
    _object(live["actor"], {"role", "depth", "executor_id"})
    actor = live["actor"]
    _enum(actor["role"], {"root", "sol", "astra", "luna", "unknown"})
    if type(actor["depth"]) is not int or actor["depth"] < 0:
        raise SessionPlanError("invalid actor depth")
    _identifier(actor["executor_id"])
    _object(live["checks"], set(), NATIVE_CHECKS)
    for item in live["checks"].values():
        _object(item, {"state"}, {"reference"})
        _enum(item["state"], CHECK_STATES)
        if item["state"] in CONFIRMED and "reference" not in item:
            raise SessionPlanError("confirmed checks require scoped references")
        if "reference" in item:
            _text(item["reference"])
    if live["target"] != handoff["target"]:
        output["gaps"].append("original_native_target_mismatch")
    if live["handoff_sha256"] != output["handoff_sha256"]:
        output["gaps"].append("handoff_scope_mismatch")
    if live["transport"] != "codex_cloud_native":
        output["gaps"].append("native_transport_unverified")
    if actor["role"] != "root" or actor["depth"] != 0 or actor["executor_id"] != handoff["owner_id"]:
        output["gaps"].append("return_cloud_execution_scope_to_root")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise SessionPlanError("current time requires timezone")
    if not timedelta(0) <= current - _timestamp(live["observed_at"]) <= timedelta(minutes=5):
        output["gaps"].append("live_observations_stale_or_future")
    required = {"native_policy", "host_permission", "target_identity"}
    required |= {"inspect": {"inspect_capability", "inspect_authorization"},
                 "dispatch-plan": {"dispatch_capability", "target_authorization", "materials_authorization",
                                   "environment", "repository_revision", "test_environment", "previous_submission"},
                 "receive": {"receive_capability", "receive_authorization"},
                 "wait": {"wait_capability", "wait_authorization"}}[phase]
    for key in sorted(required):
        check = live["checks"].get(key, {"state": "unknown"})["state"]
        permitted = {"verified"} if key in OBSERVED_CHECKS | {"target_identity", "previous_submission"} else CONFIRMED
        if check not in permitted:
            output["gaps"].append(key + ":" + check)
    if output["gaps"]:
        wait_only = all(gap.startswith(("wait_capability:", "wait_authorization:")) for gap in output["gaps"])
        return {**output, "next_action": "plan_receive_original_task" if phase == "wait" and wait_only
                else "resolve_phase_gaps"}
    tool = {"inspect": "read_thread", "dispatch-plan": "send_message_to_thread",
            "receive": "read_thread", "wait": "wait_threads"}[phase]
    return {**output, "preflight_complete": True, "native_tool": tool,
            "next_action": "claim_then_call_native_tool_once" if phase == "dispatch-plan"
            else "collect_original_task_without_resend" if phase == "receive"
            else "inspect_existing_task" if phase == "inspect" else "wait_on_original_task"}


def cloud_plan(handoff, *, phase="inspect", live=None, result=None, acceptance=None, now=None):
    """Validate supplied observations, never attest host authority or call a runtime."""
    validate_handoff(handoff)
    _enum(phase, {"inspect", "dispatch-plan", "receive", "accept"})
    if phase == "accept":
        if live is not None:
            raise SessionPlanError("accept uses the original result and owner review, not dispatch checks")
        return _accept(handoff, result, acceptance)
    if result is not None or acceptance is not None:
        raise SessionPlanError("results are only used during acceptance")
    output = _base(handoff, phase)
    state = handoff["state"]["submission"]
    # This check precedes new-dispatch observations and quota/permission checks.
    # Unknown outcome includes a timed-out submission: never manufacture not_sent.
    if phase == "dispatch-plan" and state != "not_sent":
        actions = {"sent": "receive_original_task", "unknown_outcome": "reconcile_original_submission",
                   "collected": "accept_original_result", "accepted": "reuse_accepted_result"}
        return {**output, "next_action": actions[state], "gaps": ["new_submission_forbidden"]}
    if phase == "receive" and (state == "not_sent" or handoff["state"]["task_id"] is None):
        return {**output, "next_action": "reconcile_original_submission" if state == "unknown_outcome"
                else "nothing_submitted", "gaps": ["original_task_identifier_unavailable"]}
    if live is None:
        return {**output, "next_action": "inspect_host_and_target", "gaps": ["live_observations_missing"]}
    _validate_live(live)
    if live["task_id"] != handoff["state"]["task_id"]:
        output["gaps"].append("original_task_identity_mismatch")
    if live["handoff_sha256"] != output["handoff_sha256"]:
        output["gaps"].append("handoff_scope_mismatch")
    if live["surface"] != "codex_cloud" or live["transport"] == "unknown":
        output["gaps"].append("cloud_surface_or_transport_unverified")
    actor = live["actor"]
    # Native child policy is unchanged. A separate cloud execution contract must
    # be established by root; actor metadata cannot override host instructions.
    if actor["role"] != "root" or actor["depth"] != 0 or actor["executor_id"] != handoff["owner_id"]:
        output["gaps"].append("return_cloud_execution_scope_to_root")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise SessionPlanError("current time requires timezone")
    if not timedelta(0) <= current - _timestamp(live["observed_at"]) <= timedelta(minutes=5):
        output["gaps"].append("live_observations_stale_or_future")
    required = {"host_permission"}
    if phase == "inspect":
        required |= {"inspect_capability", "inspect_authorization"}
    elif phase == "dispatch-plan":
        required |= {"dispatch_capability", "target_authorization", "materials_authorization",
                     "environment", "repository_revision", "test_environment"}
    else:
        required |= {"receive_capability", "receive_authorization"}
    for name in sorted(required):
        state_observed = live["checks"].get(name, {"state": "unknown"})["state"]
        allowed_states = {"verified"} if name in OBSERVED_CHECKS else CONFIRMED
        if state_observed not in allowed_states:
            output["gaps"].append(name + ":" + state_observed)
    if output["gaps"]:
        return {**output, "next_action": "resolve_phase_gaps"}
    action = {"inspect": "inspect_original_task" if state != "not_sent" else "inspect_environment",
              "dispatch-plan": "review_actual_host_dispatch_contract",
              "receive": "collect_original_task_without_resend"}[phase]
    return {**output, "preflight_complete": True, "next_action": action}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    session = commands.add_parser("session", help="propose a context; never create a session")
    session.add_argument("--facts", required=True, type=Path)
    recommend = commands.add_parser("recommend", help="compare contexts for a work unit; never execute")
    recommend.add_argument("--facts", required=True, type=Path)
    cloud = commands.add_parser("cloud", help="check a cloud handoff; never contact Codex cloud")
    cloud.add_argument("--handoff", required=True, type=Path)
    cloud.add_argument("--phase", choices=("inspect", "dispatch-plan", "receive", "accept"), default="inspect")
    cloud.add_argument("--live", type=Path)
    cloud.add_argument("--result", type=Path)
    cloud.add_argument("--acceptance", type=Path)
    native = commands.add_parser("cloud-native", help="plan native continuation of an existing Codex Cloud task")
    native.add_argument("--handoff", required=True, type=Path)
    native.add_argument("--phase", choices=("inspect", "dispatch-plan", "receive", "wait", "accept"), default="inspect")
    for key in ("live", "result", "acceptance"):
        native.add_argument("--" + key, type=Path)
    args = parser.parse_args(argv)
    try:
        def read(path):
            return _load_json(path, "session planning input", limit=64 * 1024) if path else None
        if args.command == "session":
            output = select_session(read(args.facts))
        elif args.command == "recommend":
            output = recommend_session(read(args.facts))
        elif args.command == "cloud-native":
            output = native_cloud_plan(read(args.handoff), phase=args.phase, live=read(args.live),
                                       result=read(args.result), acceptance=read(args.acceptance))
        else:
            output = cloud_plan(read(args.handoff), phase=args.phase, live=read(args.live),
                                result=read(args.result), acceptance=read(args.acceptance))
    except (SessionPlanError, RoutingError, OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({"error": "invalid_session_plan_input"}), file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
