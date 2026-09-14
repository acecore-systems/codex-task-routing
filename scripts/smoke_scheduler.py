#!/usr/bin/env python3
"""Check the Windows scheduled task using an isolated home, then unregister it.

Requires a running Codex/ChatGPT instance: the real detector must skip updates.
Does not register anything against the user's real Codex home or start an app.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', default='codex')
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('Windows Task Scheduler is required')
    base = args.output_dir.absolute()
    base.mkdir(parents=True, exist_ok=False)
    home = base / 'isolated home'
    home.mkdir()
    (home / 'codex-task-routing').mkdir()
    protected = {
        home / 'config.toml': b'model_reasoning_effort="high"\n',
        home / 'AGENTS.md': b'Preserve test guidance.\n',
        home / 'codex-task-routing/overrides.json': b'{"schema_version":1}\n',
    }
    for path, content in protected.items():
        path.write_bytes(content)
    cli = shutil.which(args.codex)
    if not cli:
        raise SystemExit('Codex CLI was not found')
    environment = {**os.environ, 'CODEX_HOME': str(home)}

    def installer(action, *extra, home_argument=home):
        command = [sys.executable, str(ROOT / 'scripts/install_updater.py'), action,
                   '--codex-home', str(home_argument), '--codex', cli, *extra]
        result = subprocess.run(command, cwd=base, env=environment, capture_output=True,
                                text=True, encoding='utf-8', timeout=60)
        if result.returncode:
            raise RuntimeError(f'Isolated installer {action} failed: {result.stdout}')
        return json.loads(result.stdout)

    preview = installer('install', '--dry-run')
    assert not (home / 'codex-task-routing/updater').exists(), preview
    installed = False
    try:
        registration = installer('install')
        installed = True
        status = installer('status')
        assert status['registered'] is True, status
        name = status['task_name']
        # Task name comes from this isolated installation, never an enumeration.
        quoted_name = "'" + name.replace("'", "''") + "'"
        check_script = (
            "$ErrorActionPreference='Stop'; $t=Get-ScheduledTask -TaskPath ([string][char]92) -TaskName " + quoted_name + "; "
            "$i=Get-ScheduledTaskInfo -InputObject $t; "
            "@{interval=$t.Triggers[0].Repetition.Interval; next=$i.NextRunTime.ToUniversalTime().ToString('o')} | ConvertTo-Json -Compress"
        )
        schedule = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', check_script],
                                  check=True, capture_output=True, text=True, encoding='utf-8', timeout=30,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        schedule = json.loads(schedule.stdout)
        assert schedule['interval'] == 'PT15M', schedule
        next_run = datetime.fromisoformat(schedule['next'])
        assert -60 < (next_run - datetime.now(timezone.utc)).total_seconds() < 17 * 60, schedule
        script = "$ErrorActionPreference='Stop'; Start-ScheduledTask -TaskName " + quoted_name
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                       check=True, capture_output=True, timeout=30,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        diagnostic = home / 'codex-task-routing/updater/state/last-sync.json'
        deadline = time.monotonic() + 45
        while not diagnostic.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert diagnostic.is_file(), 'The scheduled entry did not produce a result'
        outcome = json.loads(diagnostic.read_text(encoding='utf-8'))
        assert outcome['outcome'] == 'skipped' and outcome['reason'] == 'process_running', outcome
        repeated = installer('install')
        assert installer('status')['task_name'] == name, repeated
        alternate_case = installer('status', home_argument=Path(str(home).swapcase()))
        assert alternate_case['registered'] is True and alternate_case['task_name'] == name, alternate_case
        spec = importlib.util.spec_from_file_location('scheduler_fixture_installer', ROOT / 'plugins/codex-task-routing/scripts/install_updater.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        updater_base = home / 'codex-task-routing/updater'
        with mock.patch.object(module, 'pythonw', return_value=base / 'new-python-version/pythonw.exe'):
            assert module.query_task(name, updater_base / 'entry.py', updater_base) == 'managed'
        # Broken runtime metadata must not make an otherwise owned task unremovable.
        (home / 'codex-task-routing/updater/current.json').write_text('broken fixture metadata', encoding='utf-8')
        assert installer('status')['registered'] is True
        for path, content in protected.items():
            assert path.read_bytes() == content, path
    finally:
        if installed:
            installer('uninstall')
    assert installer('status')['registered'] is False
    for path, content in protected.items():
        assert path.read_bytes() == content, path
    report = {'ok': True, 'checks': [
        'dry-run creates no updater files or scheduled task',
        'per-home task registration and idempotent reinstallation',
        'scheduler reports a 15-minute repetition and a future scheduled run',
        'Windows path case differences resolve the same registered task',
        'Python installation path changes retain scheduled task ownership',
        'Task Scheduler runs the installed hidden entry with the real process detector',
        'active Codex skips native updates without launching or stopping an app',
        'uninstall removes only the isolated scheduled task',
        'corrupted runtime metadata does not prevent task status or removal',
        'Codex config, instructions and overrides are unchanged'],
        'not_verified': ['natural 15-minute timer firing', 'closed-app live GitHub update']}
    (base / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
