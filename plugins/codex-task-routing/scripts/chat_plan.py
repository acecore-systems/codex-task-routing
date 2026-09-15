#!/usr/bin/env python3
"""Local, deterministic suitability checks; never discover tools or send work."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

from chatgpt_route import ChatRouteError, _load_json_object, _require_keys, _require_nonempty_string

FLAGS = {"substantial", "parent_has_independent_work", "materials_approved",
         "handoff_proportionate", "requires_repeated_local_access"}
STATES = {"advertised", "permission_checked", "verified", "blocked"}


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
    if value["surface"] not in ("chat-temporary", "chat") or value["model"] != "6 Pro":
        raise ChatRouteError("inventory must describe normal Chat with 6 Pro")
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


def check_live(live, *, now):
    """Check supplied observations, not the account; never attest actual execution.

    A short-lived snapshot prevents a saved quota or UI observation from being
    treated as a permanent permission. No remaining-message total is inferred.
    """
    _keys(live, {"is_root", "route_enabled", "surface", "model", "transport",
                 "transport_authorized", "actions_authorized", "quota_state", "observed_at"}, "live")
    for name in ("is_root", "route_enabled", "transport_authorized", "actions_authorized"):
        if type(live[name]) is not bool:
            raise ChatRouteError("live gates must be JSON booleans")
    for name in ("surface", "model", "transport", "quota_state"):
        _text(live[name])
    if live["quota_state"] not in {"unknown", "no_limit_notice", "exhausted"}:
        raise ChatRouteError("invalid quota observation")
    observed = _time(live["observed_at"])
    for name in ("is_root", "route_enabled", "transport_authorized", "actions_authorized"):
        if not live[name]:
            return {"route": "not_ready", "reason": name + "_not_established"}
    expected_surface = {"browser-temporary": "chat-temporary", "codex-app-tools": "chat"}.get(live["transport"])
    if expected_surface is None or live["surface"] != expected_surface:
        return {"route": "not_ready", "reason": "wrong_surface_or_transport"}
    if live["model"] != "6 Pro":
        return {"route": "not_ready", "reason": "required_model_not_observed"}
    if live["quota_state"] == "exhausted":
        return {"route": "not_ready", "reason": "quota_exhausted"}
    if not timedelta(0) <= now - observed <= timedelta(minutes=2):
        return {"route": "preflight_needed", "reason": "refresh_live_observations"}
    if live["quota_state"] == "unknown":
        return {"route": "preflight_needed", "reason": "check_model_and_limit_notice"}
    return None


def plan(facts, inventory=None, *, now=None, live=None):
    if not isinstance(facts, dict):
        raise ChatRouteError("facts must be an object")
    _require_keys(facts, required=FLAGS | {"needs"}, allowed=FLAGS | {"needs", "delegation"}, purpose="facts")
    delegation = _delegation(facts)
    if any(type(facts[k]) is not bool for k in FLAGS):
        raise ChatRouteError("suitability flags must be JSON booleans")
    needs = facts["needs"]
    if not isinstance(needs, list) or len(needs) > 16:
        raise ChatRouteError("needs must be a list of at most sixteen capabilities")
    for need in needs:
        _keys(need, {"tool", "operation", "scope"}, "need")
        for val in need.values():
            _text(val)
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
    return {"route": "chat_candidate", "reason": "verify_live_surface_model_and_permissions", "checks": checks,
            "live_checked": live is not None, "delegation_mode": delegation}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--live", type=Path, help="fresh, explicitly supplied dispatch observations")
    args = parser.parse_args(argv)
    try:
        facts = _load_json_object(args.facts, purpose="facts")
        inventory = _load_json_object(args.inventory, purpose="inventory") if args.inventory else None
        live = _load_json_object(args.live, purpose="live") if args.live else None
        print(json.dumps(plan(facts, inventory, live=live), ensure_ascii=False, separators=(",", ":")))
        return 0
    except (ChatRouteError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
