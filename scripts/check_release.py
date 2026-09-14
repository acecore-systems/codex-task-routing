#!/usr/bin/env python3
"""Require a new plugin version when a PR changes distributed plugin files."""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'plugins/codex-task-routing/'
MANIFEST = PREFIX + '.codex-plugin/plugin.json'


def needs_version_change(paths, before, after):
    return any(path.startswith(PREFIX) for path in paths) and before == after


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default=os.environ.get('RELEASE_BASE'))
    args = parser.parse_args()
    if not args.base:
        parser.error('--base or RELEASE_BASE is required')
    # Resolve a revision before passing it into git show syntax.
    base = subprocess.check_output(['git', 'rev-parse', '--verify', '--end-of-options',
                                    args.base + '^{commit}'], cwd=ROOT, text=True, encoding='utf-8').strip()
    paths = subprocess.check_output(['git', 'diff', '--name-only', '--no-renames',
                                     base, 'HEAD', '--', PREFIX], cwd=ROOT, text=True, encoding='utf-8').splitlines()
    before = json.loads(subprocess.check_output(['git', 'show', base + ':' + MANIFEST],
                                               cwd=ROOT, text=True, encoding='utf-8'))['version']
    after = json.loads((ROOT / MANIFEST).read_text(encoding='utf-8'))['version']
    if needs_version_change(paths, before, after):
        raise SystemExit('Distributed plugin files changed without a new manifest version.')
    print('Release version check passed.')


if __name__ == '__main__':
    main()
