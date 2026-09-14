#!/usr/bin/env python3
"""Install the optional pre-launch entry point; does not run or update Codex."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import uuid

SCRIPTS = Path(__file__).resolve().parent


def safe_path(path):
    """Reject symlinks and Windows junctions before creating or replacing files."""
    for part in (path, *path.parents):
        if not part.exists() and not part.is_symlink():
            continue
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Launcher paths must not contain symlinks or junctions')
    return path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    safe_path(path)
    temporary = path.parent / ('.install-' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def current_generation(base):
    pointer = safe_path(base / 'current.json')
    if not pointer.is_file():
        raise ValueError('Destination already exists and is not a managed launcher')
    prior = json.loads(pointer.read_text(encoding='utf-8'))
    if not isinstance(prior, dict) or prior.get('owner') != 'codex-task-routing' or prior.get('schema_version') != 1:
        raise ValueError('Destination is not a recognized launcher')
    generation = prior.get('generation')
    if not isinstance(generation, str) or len(generation) != 64 or any(c not in '0123456789abcdef' for c in generation):
        raise ValueError('Invalid launcher generation')
    directory = safe_path(base / 'generations' / generation)
    metadata = json.loads(safe_path(directory / 'launcher.json').read_text(encoding='utf-8'))
    if not isinstance(metadata, dict) or not isinstance(metadata.get('hashes'), dict):
        raise ValueError('Invalid launcher metadata')
    for name in ('bootstrap.py', 'prelaunch_fallback.py'):
        if digest(safe_path(directory / name).read_bytes()) != metadata.get('hashes', {}).get(name):
            raise ValueError('Launcher files were modified; preserve them before reinstalling')
    entry_hash = digest(safe_path(base / 'bootstrap.py').read_bytes())
    # Protocol 1 also accepts the current entry after an interrupted compatible
    # entry refresh; the old generation remains complete and runnable.
    if entry_hash not in (prior.get('entry_sha256'), digest((SCRIPTS / 'launcher_entry.py').read_bytes())):
        raise ValueError('Launcher entry was modified')
    return directory


def install_files(base, codex_home, cli, mode):
    safe_path(base)
    entry = (SCRIPTS / 'launcher_entry.py').read_bytes()
    files = {'bootstrap.py': (SCRIPTS / 'launcher_bootstrap.py').read_bytes(),
             'prelaunch_fallback.py': (SCRIPTS / 'prelaunch.py').read_bytes()}
    metadata = {'owner': 'codex-task-routing', 'schema_version': 1, 'mode': mode,
                'codex_home': str(codex_home), 'codex_cli': str(cli),
                'hashes': {name: digest(data) for name, data in files.items()}}
    files['launcher.json'] = (json.dumps(metadata, sort_keys=True, indent=2) + '\n').encode('utf-8')
    generation = digest(json.dumps({name:digest(data) for name,data in files.items()},sort_keys=True).encode())
    pointer = (json.dumps({'owner':'codex-task-routing','schema_version':1,
        'generation':generation,'entry_sha256':digest(entry)},sort_keys=True)+'\n').encode('utf-8')
    fresh = not base.exists()
    if not fresh:
        current_generation(base)
    base.parent.mkdir(parents=True,exist_ok=True)
    stage = base.parent / ('.launcher-stage-' + uuid.uuid4().hex) if fresh else base
    if fresh:
        stage.mkdir()
    try:
        generations = safe_path(stage / 'generations')
        generations.mkdir(exist_ok=True)
        directory = safe_path(generations / generation)
        if directory.exists():
            if any(safe_path(directory / name).read_bytes() != data for name,data in files.items()):
                raise ValueError('Existing launcher generation was modified')
        else:
            pending = generations / ('.stage-' + uuid.uuid4().hex)
            pending.mkdir()
            try:
                for name,data in files.items():
                    atomic_write(pending / name,data)
                os.rename(pending,directory)
            finally:
                if pending.exists():
                    assert pending.resolve().is_relative_to(generations.resolve())
                    shutil.rmtree(pending)
        atomic_write(stage / 'bootstrap.py',entry)
        if os.name != 'nt':
            (stage / 'bootstrap.py').chmod(0o700)
        # Only this last atomic write changes the active complete generation.
        atomic_write(stage / 'current.json',pointer)
        if fresh:
            os.rename(stage,base)
    finally:
        if fresh and stage.exists():
            assert stage.resolve().is_relative_to(base.parent.resolve())
            shutil.rmtree(stage)
    return base / 'bootstrap.py'


def create_shortcut(bootstrap, directory=None):
    if os.name != 'nt':
        raise ValueError('Desktop shortcut installation supports Windows only')
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    if not pythonw.is_file():
        raise ValueError('pythonw.exe is required for the desktop shortcut')
    if directory is None:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); [Environment]::GetFolderPath('Programs') | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding='utf-8', check=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW)
        value = json.loads(result.stdout.strip())
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            raise ValueError('Windows Programs directory was not found')
        directory = Path(value)
    directory = safe_path(directory.absolute())
    directory.mkdir(parents=True, exist_ok=True)
    shortcut = safe_path(directory / 'Codex Task Routing.lnk')
    environment = {**os.environ, 'CTR_SHORTCUT': str(shortcut), 'CTR_PYTHON': str(pythonw),
                   'CTR_ARGUMENTS': subprocess.list2cmdline([str(bootstrap)]),
                   'CTR_CWD': str(Path.home())}
    script = ("$ErrorActionPreference='Stop'; "
        "$present = Test-Path -LiteralPath $env:CTR_SHORTCUT; "
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:CTR_SHORTCUT); "
        "if ($present) { if ($s.TargetPath -ine $env:CTR_PYTHON -or $s.Arguments -cne $env:CTR_ARGUMENTS) { throw 'Different shortcut exists' }; exit 0 }; "
        "$s.TargetPath=$env:CTR_PYTHON; $s.Arguments=$env:CTR_ARGUMENTS; "
        "$s.WorkingDirectory=$env:CTR_CWD; $s.Description='Codex with task routing updates'; "
        "$s.Save()")
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                   env=environment, capture_output=True, check=True, timeout=15,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    return shortcut


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('desktop', 'cli'), default='desktop' if os.name == 'nt' else 'cli')
    parser.add_argument('--codex', default='codex')
    parser.add_argument('--codex-home', type=Path)
    parser.add_argument('--shortcut-dir', type=Path, help='Use a different directory, including an isolated test directory')
    parser.add_argument('--no-shortcut', action='store_true')
    args = parser.parse_args(argv)
    codex_home = (args.codex_home or Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')).absolute()
    cli = shutil.which(args.codex)
    if cli is None:
        parser.error('Codex CLI was not found; install Codex before installing the launcher')
    if args.mode == 'desktop' and os.name != 'nt':
        parser.error('Use --mode cli on this platform')
    base = codex_home / 'codex-task-routing' / 'launcher'
    try:
        bootstrap = install_files(base, codex_home, Path(cli).absolute(), args.mode)
        result = {'bootstrap': str(bootstrap), 'mode': args.mode, 'codex_started': False,
                  'plugin_updated': False}
        if args.mode == 'desktop' and not args.no_shortcut:
            result['shortcut'] = str(create_shortcut(bootstrap, args.shortcut_dir))
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Launcher installation failed. Check the destination and existing launcher files.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
