#!/usr/bin/env python3
"""Stable launcher entry point; loads the updater from the native marketplace.

This file and a fallback updater are copied outside the replaceable plugin cache.
It starts only the application explicitly selected during launcher installation.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

PLUGIN_ID = 'codex-task-routing@codex-task-routing'
REPOSITORY = 'https://github.com/acecore-systems/codex-task-routing'


def hidden_process_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def read_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('Invalid launcher file')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def windows_desktop():
    """Resolve the currently installed MSIX, rather than pinning an app version."""
    if os.name != 'nt':
        raise ValueError('Desktop launcher currently supports Windows only')
    script = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
              "$p = @(Get-AppxPackage -Name OpenAI.Codex); "
              "if ($p.Count -ne 1) { throw 'Expected one Codex package' }; "
              "$p[0].InstallLocation | ConvertTo-Json -Compress")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                             '-Command', script], capture_output=True, text=True,
                            encoding='utf-8', timeout=15, check=True,
                            **hidden_process_options())
    package = Path(json.loads(result.stdout.strip()))
    app = package / 'app' / 'Codex.exe'
    cli = package / 'app' / 'resources' / 'codex.exe'
    if not package.is_absolute() or not app.is_file() or not cli.is_file():
        raise ValueError('Codex package executables were not found')
    return app, cli


def launcher_settings(base):
    settings = read_json(base / 'launcher.json')
    if not isinstance(settings, dict) or settings.get('owner') != 'codex-task-routing' or settings.get('schema_version') != 1:
        raise ValueError('Unrecognized launcher configuration')
    if settings.get('mode') not in ('cli', 'desktop'):
        raise ValueError('Invalid launcher mode')
    for name in ('codex_home', 'codex_cli'):
        if not isinstance(settings.get(name), str) or not Path(settings[name]).is_absolute():
            raise ValueError('Invalid launcher path')
    return settings


def marketplace_updater(cli, base, environment):
    """Use the public native source path; never guess a versioned cache path."""
    try:
        result = subprocess.run([str(cli), 'plugin', 'list', '--marketplace',
                                 'codex-task-routing', '--json'], cwd=base,
                                env=environment, capture_output=True, text=True,
                                encoding='utf-8', timeout=15, check=True,
                                stdin=subprocess.DEVNULL, **hidden_process_options())
        installed = json.loads(result.stdout).get('installed', [])
        matches = [entry for entry in installed if entry.get('pluginId') == PLUGIN_ID]
        if len(matches) != 1:
            return None
        entry = matches[0]
        source = entry.get('marketplaceSource', {})
        if not (entry.get('installed') is True and entry.get('enabled') is True
                and source.get('sourceType') == 'git'
                and source.get('source', '').removesuffix('.git').rstrip('/') == REPOSITORY):
            return None
        local = entry.get('source', {})
        if local.get('source') != 'local' or not isinstance(local.get('path'), str):
            return None
        root = Path(local['path'])
        if not root.is_absolute() or root.is_symlink():
            return None
        manifest = read_json(root / '.codex-plugin' / 'plugin.json')
        script = root / 'scripts' / 'prelaunch.py'
        if manifest.get('name') != 'codex-task-routing' or not script.is_file() or script.is_symlink():
            return None
        return script
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return None


def load_updater(path):
    namespace = runpy.run_path(str(path), run_name='routing_prelaunch')
    if not callable(namespace.get('main')):
        raise ValueError('Updater entry point is missing')
    return namespace['main']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', action='store_true', help='Start the interactive CLI instead of the desktop app')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    base = Path(__file__).resolve().parent
    try:
        settings = launcher_settings(base)
        # The private installation records its Codex home; no parent config is edited.
        os.environ['CODEX_HOME'] = settings['codex_home']
        mode = 'cli' if args.cli else settings['mode']
        extra = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
        if mode == 'desktop':
            if extra:
                raise ValueError('Desktop mode takes no additional arguments')
            app, bundled_cli = windows_desktop()
            cli = bundled_cli
            target = [str(app)]
        else:
            cli = Path(shutil.which('codex') or settings['codex_cli'])
            target = [str(cli), *extra]
        environment = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never'}
        latest = marketplace_updater(cli, base, environment)
        fallback = base / 'prelaunch_fallback.py'
        try:
            updater = load_updater(latest or fallback)
        except (OSError, ValueError, ImportError, SyntaxError):
            updater = load_updater(fallback)
        # Fallback still performs the same process and lock checks. Never launch
        # directly here: another launcher might currently be replacing files.
        flags = ['--detach'] if mode == 'desktop' else []
        return updater(['--codex', str(cli), *flags, '--', *target])
    except (OSError, ValueError, ImportError, SyntaxError, subprocess.SubprocessError):
        message = 'Codex Task Routing: launcher could not start. Re-run the launcher installer.'
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        if os.name == 'nt' and sys.stderr is None:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'Codex Task Routing', 0x10)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
