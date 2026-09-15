#!/usr/bin/env python3
"""Local, deterministic suitability checks; never discover tools or send work."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

from chatgpt_route import ChatRouteError, _load_json_object, _require_keys

FLAGS = {"substantial", "parent_has_independent_work", "materials_approved",
         "handoff_proportionate", "requires_repeated_local_access"}
STATES = {"advertised", "verified", "blocked"}


def _keys(value, required, purpose):
    if not isinstance(value, dict):
        raise ChatRouteError(f"{purpose} must be an object")
    _require_keys(value, required=required, allowed=required, purpose=purpose)


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ChatRouteError("metadata must be a nonempty string of at most 512 characters")
    return value


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
    if value["surface"] != "chat-temporary" or value["model"] != "6 Pro":
        raise ChatRouteError("inventory must describe Temporary Chat with 6 Pro")
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
        _keys(cap, {"tool", "operation", "scope", "state", "observed_at", "expires_at"}, "capability")
        key = tuple(_text(cap[k]) for k in ("tool", "operation", "scope"))
        if key in seen:
            raise ChatRouteError("duplicate capability scope")
        seen.add(key)
        if cap["tool"] not in installed or not isinstance(cap["state"], str) or cap["state"] not in STATES:
            raise ChatRouteError("unknown tool or capability state")
        observed, expires = _time(cap["observed_at"]), _time(cap["expires_at"])
        if not timedelta(0) < expires - observed <= timedelta(days=7):
            raise ChatRouteError("capability lifetime must be positive and at most seven days")
    return value


def plan(facts, inventory=None, *, now=None):
    _keys(facts, FLAGS | {"needs"}, "facts")
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
    for flag in ("substantial", "parent_has_independent_work", "handoff_proportionate"):
        if not facts[flag]:
            return {"route": "codex", "reason": flag + "_false"}
    if facts["requires_repeated_local_access"]:
        return {"route": "codex", "reason": "repeated_local_access"}
    instant = now or datetime.now(timezone.utc)
    checks = []
    caps = inventory["capabilities"] if inventory else []
    for need in needs:
        cap = next((c for c in caps if all(c[k] == v for k, v in need.items())), None)
        state = "unknown"
        if cap:
            state = cap["state"] if _time(cap["observed_at"]) <= instant < _time(cap["expires_at"]) else "stale"
        checks.append({**need, "state": state, "installed": bool(inventory and need["tool"] in inventory["installed_plugins"])})
    if any(c["state"] == "blocked" for c in checks):
        return {"route": "not_ready", "reason": "required_capability_blocked", "checks": checks}
    if any(c["state"] != "verified" for c in checks):
        return {"route": "preflight_needed", "reason": "verify_only_required_capabilities", "checks": checks}
    return {"route": "chat_candidate", "reason": "verify_live_surface_model_and_permissions", "checks": checks}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--inventory", type=Path)
    args = parser.parse_args(argv)
    try:
        facts = _load_json_object(args.facts, purpose="facts")
        inventory = _load_json_object(args.inventory, purpose="inventory") if args.inventory else None
        print(json.dumps(plan(facts, inventory), ensure_ascii=False, separators=(",", ":")))
        return 0
    except (ChatRouteError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
