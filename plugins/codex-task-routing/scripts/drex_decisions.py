#!/usr/bin/env python3
"""Compatibility entry point for the Liquid Decisions route."""
from liquid_decisions import *  # noqa: F401,F403
from liquid_decisions import main

if __name__ == "__main__":
    raise SystemExit(main())
