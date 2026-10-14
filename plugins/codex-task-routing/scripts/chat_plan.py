#!/usr/bin/env python3
"""Local inspection/dispatch checks; never discover tools, grant access or send work."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

from chatgpt_route import (
    ChatRouteError, _load_json_object, _require_keys,
    _require_nonempty_string, _expected_request, is_required_model,
)

FLAGS = {"substantial", "parent_has_independent_work", "materials_approved",
         "handoff_proportionate", "requires_repeated_local_access"}
PRIVATE_AUTH_FLAG = "requires_private_auth"
STATES = {"advertised", "permission_checked", "verified", "blocked"}
BROWSER_OPERATIONS = ("inspect", "send", "receive")
BROWSER_STATES = {"unknown", "permission_checked", "verified", "confirmation_required",
                  "unavailable", "blocked", "provider_review"}
BROWSER_CONFIRMED = {"permission_checked", "verified"}



def _keys(value, required, purpose):
    if not isinstance(value, dict):
        raise ChatRouteError(f"{purpose} must be an object")
    _require_keys(value, required=required, allowed=required, purpose=purpose)


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ChatRouteError("metadata must be a nonempty string of at most 512 characters")
    return _require_nonempty_string(value, purpose="metadata", maximum=512)


def _time(value):
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ChatRouteError("observation time requires an ISO timestamp with timezone") from exc


def validate_inventory(value):
    _keys(value, {"schema_version", "surface", "model", "installed_plugins", "capabilities"}, "inventory")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ChatRouteError("unsupported inventory schema")
    if value["surface"] not in ("chat-temporary", "chat") or not is_required_model(value["model"]):
        raise ChatRouteError("inventory must describe normal Chat with the selected Pro UI label")
    installed, caps = value["installed_plugins"], value["capabilities"]
    if not isinstance(installed, list) or len(installed) > 128:
        raise ChatRouteError("installed_plugins must be a bounded list")
    for name in installed:
        _text(name)
    if len(set(installed)) != len(installed):
        raise ChatRouteError("duplicate installed plugin")
    if not isinstance(caps, list) or len(caps) > 256:
        raise ChatRouteError("capabilities must be a bounded list")
    seen = set()
    for cap in caps:
        required = {"tool", "operation", "scope", "state", "observed_at", "expires_at"}
        if not isinstance(cap, dict):
            raise ChatRouteError("capability must be an object")
        _require_keys(cap, required=required, allowed=required | {"permission_evidence"}, purpose="capability")
        key = tuple(_text(cap[k]) for k in ("tool", "operation", "scope"))
        if key in seen:
            raise ChatRouteError("duplicate capability scope")
        seen.add(key)
        if cap["tool"] not in installed or not isinstance(cap["state"], str) or cap["state"] not in STATES:
            raise ChatRouteError("unknown tool or capability state")
        # A first real operation must not require an unrelated trial write.
        # This state records scoped capability/permission checks, not execution.
        if cap["state"] == "permission_checked" and "permission_evidence" not in cap:
            raise ChatRouteError("permission_checked requires scoped permission evidence")
        if "permission_evidence" in cap:
            _text(cap["permission_evidence"])
        observed, expires = _time(cap["observed_at"]), _time(cap["expires_at"])
        if not timedelta(0) < expires - observed <= timedelta(days=7):
            raise ChatRouteError("capability lifetime must be positive and at most seven days")
    return value


def _delegation(facts):
    """Keep legacy calls parallel; serial delegation needs an explicit host check."""
    if "delegation" not in facts:
        return "parallel"
    decision = facts["delegation"]
    _keys(decision, {"mode", "host_allows_serial", "reason"}, "delegation")
    if decision["mode"] != "serial_specialist" or type(decision["host_allows_serial"]) is not bool:
        raise ChatRouteError("invalid specialist delegation")
    _text(decision["reason"])
    return "serial_specialist" if decision["host_allows_serial"] else "serial_disallowed"


def _transport_evidence(live, *, operations=("send", "receive")):
    """Validate scoped, caller-supplied evidence; never grant transport access.

    No provider exception is universally required: a supported host tool
    contract may establish the exact operation. It cannot override applicable
    provider terms or a host denial. References are not fetched or attested.
    """
    evidence = live.get("transport_evidence")
    if evidence is None:
        # A second serialized proof never established real permission. Reuse
        # the caller's existing scoped confirmation; do not demand paperwork.
        return None
    _keys(evidence, {"basis", "reference", "transport", "operations"}, "transport evidence")
    for name in ("basis", "reference", "transport"):
        _text(evidence[name])
    if evidence["basis"] not in {"host_tool_contract", "official_documentation", "provider_permission"}:
        raise ChatRouteError("transport evidence requires a supported permission basis")
    supplied = evidence["operations"]
    if (not isinstance(supplied, list) or len(supplied) > 2
            or any(not isinstance(item, str) or item not in {"send", "receive"} for item in supplied)
            or len(set(supplied)) != len(supplied)):
        raise ChatRouteError("transport evidence operations must be distinct send/receive values")
    if evidence["transport"] != live["transport"]:
        return "transport_evidence_scope_mismatch"
    if not set(operations) <= set(supplied):
        return "transport_evidence_incomplete"
    return None


def _browser_checks(live):
    """Read operation observations, not a new approval or permission database."""
    if "browser_checks" not in live:
        return None
    checks = live["browser_checks"]
    if not isinstance(checks, dict):
        raise ChatRouteError("browser_checks must be an object")
    _require_keys(checks, required=set(), allowed=set(BROWSER_OPERATIONS), purpose="browser checks")
    if any(not isinstance(state, str) or state not in BROWSER_STATES for state in checks.values()):
        raise ChatRouteError("unsupported browser check state")
    if live["transport"] != "browser-temporary":
        raise ChatRouteError("browser_checks only describe browser-temporary")
    return {op: checks.get(op, "unknown") for op in BROWSER_OPERATIONS}


def _browser_gap(operation, state):
    """Concrete next steps for supplied observations, never inferred denials."""
    resolutions = {
        "unknown": ("codex_root", "Current Browser skill and tool schemas in the Codex session",
                    "Check the documented controls for this operation, the exact destination, "
                    "existing user approval and applicable host/provider conditions. Reuse valid "
                    "checks; do not ask for a special delegation exception merely because the "
                    "operation uses ordinary page controls.", "inspect_operation_contract"),
        "confirmation_required": ("user", "The actual Browser site/action confirmation prompt",
                    "Respond only to the host's pending request for this site/action. Reuse an "
                    "existing valid site/action approval; do not ask again in repository prose.", "resolve_host_confirmation"),
        "unavailable": ("user_or_workspace_admin", "ChatGPT desktop app: Plugins > Browser; Settings > Browser",
                    "Enable the supported Browser in the intended Codex session when permitted. "
                    "CLI, another Chat session or a different browser is not evidence of access.", "enable_supported_browser"),
        "blocked": ("user_or_workspace_admin", "Browser site/action denial and managed browser policy",
                    "Read the exact denial. Only the authorized settings owner can change it; "
                    "otherwise keep this operation stopped. Do not switch tools to evade it.", "resolve_host_denial"),
        "provider_review": ("user_and_openai_support", "OpenAI Help Center support; Terms of Use > Using our Services",
                    "Ask whether this exact first-party Browser flow may submit one approved "
                    "request to Temporary Chat Pro and read its completed displayed reply into "
                    "the local form, and how the automatic Output-extraction clause applies. "
                    "Include the Browser version and relevant instruction, not credentials or chat history.", "resolve_provider_scope"),
    }
    owner, where, action, next_action = resolutions[state]
    return {"route": "not_ready" if state in {"blocked", "unavailable"} else "preflight_needed",
            "reason": "browser_" + operation + "_" + state,
            "constraint_layer": "provider_scope_unresolved" if state == "provider_review" else "host_observation",
            "dispatch_authorized": False, "preflight_complete": False,
            "next_action": next_action,
            "resolution": {"operation": operation, "owner": owner, "where": where, "action": action}}


def _transport_not_ready(reason, *, unknown=False):
    result = _browser_gap("send_and_receive", "unknown")
    result.update(route="preflight_needed" if unknown else "not_ready", reason=reason,
                  constraint_layer="repository_preflight")
    return result


def _actor_problem(live):
    """Check declared scope only; neither actor IDs nor flags attest the host.

    Existing root callers need no actor object. A non-root caller must declare
    its assigned routing role, root-relative depth and current request owner.
    host_allows_chat records applicable host instructions, not a new approval.
    """
    actor = live.get("actor")
    if "actor" not in live:
        return None if live["is_root"] else "is_root_not_established"
    _keys(actor, {"role", "depth", "executor_id", "owner_id", "host_allows_chat"}, "actor")
    for field in ("role", "executor_id", "owner_id"):
        _text(actor[field])
    if type(actor["depth"]) is not int or actor["depth"] < 0:
        raise ChatRouteError("actor depth must be a nonnegative JSON integer")
    if type(actor["host_allows_chat"]) is not bool:
        raise ChatRouteError("actor host gate must be a JSON boolean")
    if live["is_root"]:
        if actor["role"] != "root" or actor["depth"] != 0:
            return "actor_root_context_mismatch"
    else:
        if actor["role"] == "root" or actor["depth"] == 0:
            return "actor_root_context_mismatch"
        if actor["role"] != "sol" or actor["depth"] != 1:
            return "actor_not_eligible"
        if live["transport"] != "browser-temporary":
            return "child_requires_browser_temporary"
    if actor["executor_id"] != actor["owner_id"]:
        return "actor_request_owner_mismatch"
    if not actor["host_allows_chat"]:
        return "actor_host_permission_not_established"
    return None


def _owner_scoped(result, live):
    # Permission gaps stay with the eligible Sol owner; human/administrator
    # decisions keep their original owners. No private identity is echoed.
    if (not live["is_root"] and "resolution" in result
            and result["resolution"]["owner"] == "codex_root"):
        result["resolution"]["owner"] = "sol_child_owner"
    return result


def inspection_plan(live):
    """Plan only the non-dispatch inspection. No Chat prompt or reply is moved.

    In particular, an unknown/false *transfer* flag cannot prevent learning the
    Browser contract. Actual restrictions on inspection itself still apply.
    """
    if not isinstance(live, dict):
        raise ChatRouteError("inspection requires a live object")
    required = {"is_root", "route_enabled", "transport"}
    allowed = required | {"surface", "model", "transport_authorized", "transport_evidence",
                          "actions_authorized", "quota_state", "observed_at", "browser_checks", "actor"}
    _require_keys(live, required=required, allowed=allowed, purpose="inspection live")
    for field in ("is_root", "route_enabled"):
        if type(live[field]) is not bool:
            raise ChatRouteError("inspection gates must be JSON booleans")
    actor_problem = _actor_problem(live)
    if actor_problem is not None:
        return {"route": "not_ready", "reason": actor_problem,
                "dispatch_authorized": False, "preflight_complete": False}
    if not live["route_enabled"]:
        return {"route": "not_ready", "reason": "route_enabled_not_established",
                "dispatch_authorized": False, "preflight_complete": False}
    if live["transport"] != "browser-temporary":
        return {"route": "not_ready", "reason": "inspection_requires_browser_temporary",
                "dispatch_authorized": False, "preflight_complete": False}
    checks = _browser_checks(live)
    state = checks["inspect"] if checks else "unknown"
    if state not in BROWSER_CONFIRMED:
        return _owner_scoped(_browser_gap("inspect", state), live)
    return _owner_scoped({"route": "preflight_needed", "reason": "observe_live_chat_ui",
            "next_action": "inspect_chat_ui", "dispatch_authorized": False,
            "preflight_complete": False,
            "resolution": {"operation": "inspect", "owner": "codex_root",
                           "where": "The approved Browser on chatgpt.com, in a new empty Temporary Chat",
                           "action": "Observe Chat (not Work), Temporary Chat, selected Pro and limit notices. "
                                     "Do not enter a task, submit a prompt or collect a model reply. "
                                     "User handles sign-in; do not read credentials, old chats or memory."}}, live)


def _check_transport(live, *, phase="dispatch"):
    authorized = live.get("transport_authorized")
    if authorized is not None and type(authorized) is not bool:
        raise ChatRouteError("transport_authorized must be a JSON boolean or null")
    checks = _browser_checks(live)
    # Legacy false is never silently reinterpreted or overridden. The old
    # format conflated unknown with denied; resolving it requires a real check.
    if authorized is False:
        return _transport_not_ready("transport_authorized_not_established")
    if checks is not None:
        # More specific current observations take precedence over legacy true.
        # A reported denial must not be hidden behind another unknown step.
        operations = BROWSER_OPERATIONS if phase == "dispatch" else ("inspect", "receive")
        for state in ("blocked", "unavailable", "provider_review", "confirmation_required", "unknown"):
            for operation in operations:
                if checks[operation] == state:
                    return _browser_gap(operation, state)
    elif authorized is not True:
        return _transport_not_ready("transport_authorization_unobserved", unknown=True)
    evidence_problem = _transport_evidence(live, operations=("send", "receive") if phase == "dispatch" else ("receive",))
    if evidence_problem is not None:
        return _transport_not_ready(evidence_problem)
    return None


def check_live(live, *, now, phase="dispatch"):
    """Check supplied observations, not the account; never attest actual execution.

    A short-lived snapshot prevents a saved quota or UI observation from being
    treated as a permanent permission. No remaining-message total is inferred.
    """
    if phase not in {"dispatch", "receive"}:
        raise ChatRouteError("unsupported live phase")
    required = {"is_root", "route_enabled", "surface", "model", "transport",
                "actions_authorized", "observed_at"}
    if phase == "dispatch":
        required.add("quota_state")
    if not isinstance(live, dict):
        raise ChatRouteError("live must be an object")
    _require_keys(live, required=required,
                  allowed=required | {"quota_state", "transport_authorized", "transport_evidence", "browser_checks", "actor"}, purpose="live")
    for name in ("is_root", "route_enabled", "actions_authorized"):
        if type(live[name]) is not bool:
            raise ChatRouteError("live gates must be JSON booleans")
    for name in ("surface", "model", "transport"):
        _text(live[name])
    quota = live.get("quota_state", "unknown")
    _text(quota)
    if quota not in {"unknown", "no_limit_notice", "exhausted"}:
        raise ChatRouteError("invalid quota observation")
    observed = _time(live["observed_at"])
    actor_problem = _actor_problem(live)
    if actor_problem is not None:
        return {"route": "not_ready", "reason": actor_problem}
    for name in ("route_enabled", "actions_authorized"):
        if not live[name]:
            return {"route": "not_ready", "reason": name + "_not_established"}
    expected_surface = {"browser-temporary": "chat-temporary", "codex-app-tools": "chat"}.get(live["transport"])
    if expected_surface is None or live["surface"] != expected_surface:
        return {"route": "not_ready", "reason": "wrong_surface_or_transport"}
    transport_problem = _check_transport(live, phase=phase)
    if transport_problem is not None:
        return _owner_scoped(transport_problem, live)
    if not is_required_model(live["model"]):
        return {"route": "not_ready", "reason": "required_model_not_observed"}
    if phase == "dispatch" and quota == "exhausted":
        return {"route": "not_ready", "reason": "quota_exhausted"}
    if not timedelta(0) <= now - observed <= timedelta(minutes=2):
        return {"route": "preflight_needed", "reason": "refresh_live_observations"}
    if phase == "dispatch" and quota == "unknown":
        return {"route": "preflight_needed", "reason": "check_model_and_limit_notice"}
    return None


def collection_plan(live, request, *, now=None):
    """Collect only a known request; never restart generation to restore a form."""
    request_id, input_sha256 = _expected_request(request)
    instant = now or datetime.now(timezone.utc)
    if not isinstance(instant, datetime) or instant.tzinfo is None:
        raise ChatRouteError("now requires a timezone-aware datetime")
    blocked = check_live(live, now=instant, phase="receive")
    if blocked is not None:
        return blocked
    return {"route": "chat_collection", "reason": "supplied_collection_checks_passed",
            "next_action": "collect_reply", "preflight_complete": True,
            "dispatch_authorized": False, "dispatch_performed": False,
            "request_id": request_id, "input_sha256": input_sha256,
            "observation_source": "caller_supplied", "backend_model": "unknown",
            "authorization_attested": False}


def plan(facts, inventory=None, *, now=None, live=None):
    if not isinstance(facts, dict):
        raise ChatRouteError("facts must be an object")
    _require_keys(facts, required=FLAGS | {"needs"},
                  allowed=FLAGS | {"needs", "delegation", PRIVATE_AUTH_FLAG}, purpose="facts")
    delegation = _delegation(facts)
    if any(type(facts[k]) is not bool for k in FLAGS):
        raise ChatRouteError("suitability flags must be JSON booleans")
    if PRIVATE_AUTH_FLAG in facts and type(facts[PRIVATE_AUTH_FLAG]) is not bool:
        raise ChatRouteError("private authentication flag must be a JSON boolean")
    needs = facts["needs"]
    if not isinstance(needs, list) or len(needs) > 16:
        raise ChatRouteError("needs must be a list of at most sixteen capabilities")
    for need in needs:
        _keys(need, {"tool", "operation", "scope"}, "need")
        for val in need.values():
            _text(val)
    # Select the private boundary before Chat capabilities, quota or suitability.
    # This result is a handoff recommendation, never permission to authenticate.
    if facts.get(PRIVATE_AUTH_FLAG, False):
        return {"route": "local_auth", "reason": "private_authentication_requires_local_worker",
                "dispatch_authorized": False}
    if inventory is not None:
        validate_inventory(inventory)
    # The caller supplies semantic judgments once. No classifier model is run.
    if not facts["materials_approved"]:
        return {"route": "not_ready", "reason": "materials_approval_not_established"}
    for flag in ("substantial", "handoff_proportionate"):
        if not facts[flag]:
            return {"route": "codex", "reason": flag + "_false"}
    if delegation == "serial_disallowed":
        return {"route": "codex", "reason": "host_does_not_allow_serial_delegation"}
    if delegation == "parallel" and not facts["parent_has_independent_work"]:
        return {"route": "codex", "reason": "parent_has_independent_work_false"}
    if facts["requires_repeated_local_access"]:
        return {"route": "codex", "reason": "repeated_local_access"}
    instant = now or datetime.now(timezone.utc)
    if not isinstance(instant, datetime) or instant.tzinfo is None:
        raise ChatRouteError("now requires a timezone-aware datetime")
    if live is not None:
        blocked = check_live(live, now=instant)
        if blocked is not None:
            return blocked
        if inventory is not None and inventory["surface"] != live["surface"]:
            return {"route": "preflight_needed", "reason": "capability_surface_mismatch"}
    checks = []
    caps = inventory["capabilities"] if inventory else []
    for need in needs:
        cap = next((c for c in caps if all(c[k] == v for k, v in need.items())), None)
        state = "unknown"
        if cap:
            state = cap["state"] if _time(cap["observed_at"]) <= instant < _time(cap["expires_at"]) else "stale"
        checks.append({**need, "state": state, "installed": bool(inventory and need["tool"] in inventory["installed_plugins"]),
                       "execution_verified": state == "verified"})
    if any(c["state"] == "blocked" for c in checks):
        return {"route": "not_ready", "reason": "required_capability_blocked", "checks": checks}
    if any(c["state"] not in {"verified", "permission_checked"} for c in checks):
        return {"route": "preflight_needed", "reason": "verify_only_required_capabilities", "checks": checks}
    if live is None and any(c["state"] == "permission_checked" for c in checks):
        return {"route": "preflight_needed", "reason": "fresh_live_required_for_unexercised_capability", "checks": checks}
    return {"route": "chat_candidate",
            "reason": "supplied_dispatch_checks_passed" if live is not None else "inspect_live_surface_model_and_permissions",
            "next_action": "send_once" if live is not None else "inspect_browser",
            "preflight_complete": live is not None, "dispatch_performed": False,
            "checks": checks, "live_checked": live is not None, "delegation_mode": delegation,
            "observation_source": "caller_supplied", "backend_model": "unknown",
            "authorization_attested": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("inspect", "dispatch", "receive"), default="dispatch")
    parser.add_argument("--facts", type=Path)
    parser.add_argument("--request", type=Path, help="existing prepared bundle/request for receive only")
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--live", type=Path, help="fresh, explicitly supplied dispatch observations")
    args = parser.parse_args(argv)
    if args.phase == "dispatch" and args.facts is None:
        parser.error("--facts is required for dispatch")
    if args.phase in {"inspect", "receive"} and args.live is None:
        parser.error("--live is required for inspection/receive")
    if args.phase == "receive" and args.request is None:
        parser.error("--request is required for receive")
    try:
        facts = _load_json_object(args.facts, purpose="facts") if args.facts else {}
        if facts.get(PRIVATE_AUTH_FLAG) is True:
            # Private work must not read even a supplied Chat inventory/live file.
            print(json.dumps(plan(facts), ensure_ascii=False, separators=(",", ":")))
            return 0
        if args.phase == "inspect":
            # No task suitability, Chat capability inventory, quota, transfer
            # attestation or model observation is needed just to plan inspection.
            live = _load_json_object(args.live, purpose="inspection live")
            print(json.dumps(inspection_plan(live), ensure_ascii=False, separators=(",", ":")))
            return 0
        if args.phase == "receive":
            live = _load_json_object(args.live, purpose="receive live")
            request = _load_json_object(args.request, purpose="existing request")
            print(json.dumps(collection_plan(live, request), ensure_ascii=False, separators=(",", ":")))
            return 0
        inventory = _load_json_object(args.inventory, purpose="inventory") if args.inventory else None
        live = _load_json_object(args.live, purpose="live") if args.live else None
        print(json.dumps(plan(facts, inventory, live=live), ensure_ascii=False, separators=(",", ":")))
        return 0
    except (ChatRouteError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
