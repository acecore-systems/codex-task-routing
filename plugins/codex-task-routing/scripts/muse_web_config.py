#!/usr/bin/env python3
"""Read the optional Muse public web research preference without dispatch, secrets or mutation."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
from routing_core.common import RoutingError, _absolute, _assert_safe_ancestors, _load_json

CONFIG_RELATIVE = Path("codex-task-routing") / "muse-web.json"


def load_config(home):
    path = _assert_safe_ancestors(_absolute(home) / CONFIG_RELATIVE)
    if not os.path.lexists(path):
        return {"schema_version": 1, "enabled": False, "state": "absent"}
    config = _load_json(path, "Muse public web research route configuration", limit=4096)
    if (set(config) != {"schema_version", "enabled"} or type(config["schema_version"]) is not int
            or config["schema_version"] != 1 or type(config["enabled"]) is not bool):
        raise RoutingError("invalid Muse public web research route configuration")
    return {**config, "state": "configured"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("status",))
    parser.add_argument("--codex-home", type=Path)
    args = parser.parse_args(argv)
    home = args.codex_home or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        value = load_config(home)
        print(json.dumps({"enabled": value["enabled"], "state": value["state"],
                          "execution_authorized": False}))
        return 0
    except (RoutingError, OSError, ValueError):
        print(json.dumps({"enabled": False, "state": "invalid", "execution_authorized": False}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
