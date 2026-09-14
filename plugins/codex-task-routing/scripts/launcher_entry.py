#!/usr/bin/env python3
"""Protocol-1 entry point. An atomic pointer selects a complete launcher copy."""
import json
from pathlib import Path
import re
import runpy
import stat
import sys


def selected_launcher(base):
    pointer = base / 'current.json'
    for path in (base, pointer):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Unsafe launcher path')
    if pointer.stat().st_size > 65536:
        raise ValueError('Invalid launcher pointer')
    value = json.loads(pointer.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('owner') != 'codex-task-routing' or value.get('schema_version') != 1:
        raise ValueError('Unknown launcher pointer')
    generation = value.get('generation')
    if not isinstance(generation, str) or re.fullmatch('[0-9a-f]{64}', generation) is None:
        raise ValueError('Invalid launcher generation')
    directory = base / 'generations' / generation
    script = directory / 'bootstrap.py'
    for path in (directory.parent, directory, script):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Unsafe launcher generation')
    return script


if __name__ == '__main__':
    try:
        script = selected_launcher(Path(__file__).resolve().parent)
    except (OSError, ValueError):
        if sys.stderr is not None:
            print('Codex Task Routing: reinstall the launcher to repair its entry point.',file=sys.stderr)
        raise SystemExit(1)
    runpy.run_path(str(script), run_name='__main__')
