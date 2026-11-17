#!/usr/bin/env python3
"""Drex helper retired; preserve existing requests for reconciliation."""
import json
import sys

if __name__ == "__main__":
    print(json.dumps({"error": "drex_route_retired", "next_action": "reconcile_existing_with_nace_tool"}), file=sys.stderr)
    raise SystemExit(1)
