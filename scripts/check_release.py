#!/usr/bin/env python3
"""Require a new plugin version when a PR changes distributed plugin files."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'plugins/codex-task-routing/'
MANIFEST = PREFIX + '.codex-plugin/plugin.json'
SEMVER = re.compile(
    r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
    r'(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)'
    r'(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?'
    r'(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$'
)


def parse_semver(version):
    """Return SemVer precedence fields, rejecting non-SemVer input.

    Numeric identifiers remain strings so even unusually large valid values can
    be compared without depending on Python's integer-conversion digit limit.
    Build metadata is validated but deliberately omitted from the result because
    SemVer excludes it from precedence.
    """
    if not isinstance(version, str):
        raise ValueError(f'Invalid semantic version: {version!r}')
    match = SEMVER.fullmatch(version)
    if match is None:
        raise ValueError(f'Invalid semantic version: {version!r}')
    major, minor, patch, prerelease, _build = match.groups()
    return (major, minor, patch), (() if prerelease is None else tuple(prerelease.split('.')))


def _compare_numeric(left, right):
    if len(left) != len(right):
        return -1 if len(left) < len(right) else 1
    return (left > right) - (left < right)


def compare_semver(left, right):
    """Compare two valid semantic versions by SemVer 2.0.0 precedence."""
    left_core, left_pre = parse_semver(left)
    right_core, right_pre = parse_semver(right)
    for left_value, right_value in zip(left_core, right_core):
        comparison = _compare_numeric(left_value, right_value)
        if comparison:
            return comparison

    if not left_pre or not right_pre:
        return (not left_pre) - (not right_pre)
    for left_value, right_value in zip(left_pre, right_pre):
        if left_value == right_value:
            continue
        left_numeric = left_value.isascii() and left_value.isdigit()
        right_numeric = right_value.isascii() and right_value.isdigit()
        if left_numeric and right_numeric:
            return _compare_numeric(left_value, right_value)
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return (left_value > right_value) - (left_value < right_value)
    return (len(left_pre) > len(right_pre)) - (len(left_pre) < len(right_pre))


def needs_version_change(paths, before, after):
    if not any(path.startswith(PREFIX) for path in paths):
        return False
    return compare_semver(after, before) <= 0


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
    try:
        invalid_change = needs_version_change(paths, before, after)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if invalid_change:
        raise SystemExit('Distributed plugin files changed without a higher SemVer manifest version.')
    print('Release version check passed.')


if __name__ == '__main__':
    main()
