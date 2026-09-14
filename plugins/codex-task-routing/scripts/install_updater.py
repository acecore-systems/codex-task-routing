#!/usr/bin/env python3
"""Explicitly register, inspect, or remove the per-user updater task.

This installer never starts Codex or upgrades a plugin. Windows registration is
an InteractiveToken, LeastPrivilege ScheduledTasks task; other platforms can
still execute updater.py one-shot but cannot register a scheduler here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import uuid
from typing import Any, Sequence


SCRIPTS = Path(__file__).resolve().parent
PLUGIN_NAME = "codex-task-routing"
PROTOCOL = 1
TASK_PREFIX = "CodexTaskRouting-Updater-"
TASK_DESCRIPTION = "Codex Task Routing background updater; owner protocol 1"
MAX_JSON_BYTES = 65536


def _absolute(path: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _unsafe(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def safe_path(path: Path) -> Path:
    absolute = _absolute(path)
    for candidate in (absolute, *absolute.parents):
        if not os.path.lexists(candidate):
            continue
        if _unsafe(candidate):
            raise ValueError("updater paths must not contain symlinks or junctions")
    return absolute


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".updater-stage-{uuid.uuid4().hex}"
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            try:
                temporary.unlink()
            except OSError:
                pass


def _rename_complete_directory(source: Path, destination: Path) -> None:
    """Windows can transiently retain a handle while a fresh tree is indexed."""

    for attempt in range(3):
        try:
            os.rename(source, destination)
            return
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.05 * (attempt + 1))


def _read_json(path: Path) -> dict[str, Any]:
    safe_path(path)
    if not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("invalid updater metadata")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid updater metadata")
    return value


def updater_base(codex_home: Path) -> Path:
    return safe_path(codex_home / PLUGIN_NAME / "updater")


def task_name(codex_home: Path) -> str:
    path = str(_absolute(codex_home))
    if os.name == "nt":
        path = ntpath.normcase(ntpath.normpath(path))
    else:
        path = os.path.normcase(os.path.normpath(path))
    return TASK_PREFIX + hashlib.sha256(path.encode("utf-8")).hexdigest()[:20]


def pythonw() -> Path:
    candidate = Path(sys.executable).with_name("pythonw.exe")
    if os.name == "nt" and candidate.is_file():
        return candidate
    return Path(sys.executable)


def current_generation(base: Path) -> tuple[dict[str, Any], Path]:
    pointer = _read_json(base / "current.json")
    generation = pointer.get("generation")
    if (
        pointer.get("owner") != PLUGIN_NAME
        or pointer.get("protocol") != PROTOCOL
        or not isinstance(generation, str)
        or len(generation) != 64
        or any(character not in "0123456789abcdef" for character in generation)
    ):
        raise ValueError("destination is not a recognized updater")
    runtime = safe_path(base / "runtimes" / generation / "updater.py")
    if not runtime.is_file() or digest(runtime.read_bytes()) != pointer.get("runtime_sha256"):
        raise ValueError("updater runtime was modified")
    entry = safe_path(base / "entry.py")
    compatible_entry = digest((SCRIPTS / "updater_entry.py").read_bytes())
    if not entry.is_file() or digest(entry.read_bytes()) not in {pointer.get("entry_sha256"), compatible_entry}:
        raise ValueError("updater entry was modified")
    return pointer, runtime


def install_files(base: Path, codex_home: Path, codex_cli: Path, *, cli_mode: str = "auto") -> Path:
    """Create a complete immutable runtime before atomically selecting it."""

    if cli_mode not in {"auto", "explicit"}:
        raise ValueError("invalid Codex CLI mode")
    base = updater_base(codex_home) if base == codex_home / PLUGIN_NAME / "updater" else safe_path(base)
    entry_data = (SCRIPTS / "updater_entry.py").read_bytes()
    runtime_data = (SCRIPTS / "updater.py").read_bytes()
    generation = digest(runtime_data)
    entry_hash = digest(entry_data)
    fresh = not base.exists()
    if not fresh:
        current_generation(base)
    base.parent.mkdir(parents=True, exist_ok=True)
    stage = base.parent / f".updater-root-{uuid.uuid4().hex}" if fresh else base
    if fresh:
        stage.mkdir()
    try:
        runtimes = safe_path(stage / "runtimes")
        runtimes.mkdir(exist_ok=True)
        runtime_dir = safe_path(runtimes / generation)
        runtime = runtime_dir / "updater.py"
        if runtime_dir.exists():
            if not runtime.is_file() or runtime.read_bytes() != runtime_data:
                raise ValueError("existing updater generation was modified")
        else:
            pending = runtimes / f".stage-{uuid.uuid4().hex}"
            pending.mkdir()
            try:
                atomic_write(pending / "updater.py", runtime_data)
                _rename_complete_directory(pending, runtime_dir)
            finally:
                if pending.exists():
                    shutil.rmtree(pending)
        # The entry protocol is compatible with the existing pointer. Replacing
        # it before the pointer makes an interrupted reinstall retain old work.
        atomic_write(stage / "entry.py", entry_data)
        pointer = {
            "codex_cli": str(_absolute(codex_cli)),
            "codex_cli_mode": cli_mode,
            "codex_home": str(_absolute(codex_home)),
            "entry_sha256": entry_hash,
            "generation": generation,
            "owner": PLUGIN_NAME,
            "protocol": PROTOCOL,
            "runtime_sha256": digest(runtime_data),
        }
        atomic_write(
            stage / "current.json",
            (json.dumps(pointer, ensure_ascii=True, sort_keys=True) + "\n").encode("utf-8"),
        )
        if fresh:
            _rename_complete_directory(stage, base)
    finally:
        if fresh and stage.exists():
            shutil.rmtree(stage)
    return base / "entry.py"


def _hidden_options() -> dict[str, int]:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _task_arguments(entry: Path) -> str:
    return subprocess.list2cmdline([str(entry), "--worker"])


def _task_environment(name: str, entry: Path, base: Path) -> dict[str, str]:
    return {
        **os.environ,
        "CTR_UPDATER_TASK": name,
        "CTR_UPDATER_ENTRY": str(entry),
        "CTR_UPDATER_PYTHON": str(pythonw()),
        "CTR_UPDATER_ARGUMENTS": _task_arguments(entry),
        "CTR_UPDATER_WORKDIR": str(base),
        "CTR_UPDATER_DESCRIPTION": TASK_DESCRIPTION,
    }


def _powershell(script: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        **_hidden_options(),
    )


def query_task(name: str, entry: Path, base: Path) -> str:
    """Return missing, managed, conflict, or unavailable without leaking PS output."""

    if os.name != "nt":
        return "unavailable"
    script = """
$ErrorActionPreference='Stop'
$rootPath=[string][char]92
try { $task=Get-ScheduledTask -TaskPath $rootPath -TaskName $env:CTR_UPDATER_TASK -ErrorAction Stop }
catch { if($_.CategoryInfo.Category -eq 'ObjectNotFound'){ [Console]::Out.Write('missing'); exit 0 }; throw }
if($null -eq $task){ [Console]::Out.Write('missing'); exit 0 }
$actions=@($task.Actions)
$expectedArgs=$env:CTR_UPDATER_ARGUMENTS
$identity=[System.Security.Principal.WindowsIdentity]::GetCurrent()
$principalId=[string]$task.Principal.UserId
try { $principalSid=([System.Security.Principal.NTAccount]::new($principalId)).Translate([System.Security.Principal.SecurityIdentifier]).Value }
catch { $principalSid=$principalId }
$sameUser=($principalSid -ieq [string]$identity.User.Value)
$pythonName=if($actions.Count -eq 1){ [IO.Path]::GetFileName($actions[0].Execute) } else { '' }
$owned=($task.TaskPath -ceq $rootPath -and $sameUser -and $task.Description -ceq $env:CTR_UPDATER_DESCRIPTION -and $actions.Count -eq 1 -and $pythonName -in @('python.exe','pythonw.exe') -and $actions[0].Arguments -ieq $expectedArgs -and $actions[0].WorkingDirectory -ieq $env:CTR_UPDATER_WORKDIR -and $task.Principal.LogonType -eq 'Interactive' -and $task.Principal.RunLevel -eq 'Limited')
if($owned){ [Console]::Out.Write('managed') } else { [Console]::Out.Write('conflict') }
"""
    try:
        result = _powershell(script, _task_environment(name, entry, base))
        if result.returncode != 0:
            return "unavailable"
        value = result.stdout.strip()
        return value if value in {"missing", "managed", "conflict"} else "unavailable"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def register_task(name: str, entry: Path, base: Path) -> bool:
    if os.name != "nt":
        raise ValueError("ScheduledTasks registration is supported on Windows only")
    # Do not use -Force against a task unless its action and owner were just
    # verified immediately before this registration attempt.
    if query_task(name, entry, base) not in {"missing", "managed"}:
        return False
    script = """
$ErrorActionPreference='Stop'
$rootPath=[string][char]92
$action=New-ScheduledTaskAction -Execute $env:CTR_UPDATER_PYTHON -Argument $env:CTR_UPDATER_ARGUMENTS -WorkingDirectory $env:CTR_UPDATER_WORKDIR
$trigger=New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
$principal=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew -StartWhenAvailable:$false
Register-ScheduledTask -TaskPath $rootPath -TaskName $env:CTR_UPDATER_TASK -Description $env:CTR_UPDATER_DESCRIPTION -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
"""
    try:
        return _powershell(script, _task_environment(name, entry, base)).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def unregister_task(name: str, entry: Path, base: Path) -> str:
    status = query_task(name, entry, base)
    if status != "managed":
        return status
    script = "$ErrorActionPreference='Stop'; Unregister-ScheduledTask -TaskPath ([string][char]92) -TaskName $env:CTR_UPDATER_TASK -Confirm:$false"
    try:
        result = _powershell(script, _task_environment(name, entry, base))
        return "removed" if result.returncode == 0 else "unavailable"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _last_sync(base: Path) -> dict[str, Any] | None:
    try:
        value = _read_json(base / "state" / "last-sync.json")
        return {key: value[key] for key in ("outcome", "reason", "timestamp") if key in value}
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _codex_home(value: Path | None) -> Path:
    return _absolute(value or os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _resolve_cli(value: str | None) -> Path:
    if value is not None:
        candidate = Path(value)
        if candidate.is_file():
            return _absolute(candidate)
        discovered = shutil.which(value)
        if discovered:
            return _absolute(discovered)
        raise ValueError("Codex CLI was not found")
    if os.name == "nt":
        package_script = (
            "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
            "$p=@(Get-AppxPackage -Name OpenAI.Codex); if($p.Count -ne 1){throw 'Codex package unavailable'}; "
            "($p[0].InstallLocation | ConvertTo-Json -Compress)"
        )
        try:
            package = Path(json.loads(_powershell(package_script, os.environ.copy()).stdout))
            candidate = package / "app" / "resources" / "codex.exe"
            if candidate.is_file():
                return candidate
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
            pass
    value = "codex"
    candidate = Path(value)
    if candidate.is_file():
        return _absolute(candidate)
    discovered = shutil.which(value)
    if discovered:
        return _absolute(discovered)
    raise ValueError("Codex CLI was not found")


def _result(*, name: str, entry: Path, registered: bool | None, base: Path, action: str, dry_run: bool = False) -> dict[str, Any]:
    return {
        "action": action,
        "dry_run": dry_run,
        "entry": str(entry),
        "last_sync": _last_sync(base),
        "registered": registered,
        "task_name": name,
    }


def _registered(status: str) -> bool | None:
    if status == "managed":
        return True
    if status == "missing":
        return False
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "status", "uninstall"))
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--codex", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    home = _codex_home(args.codex_home)
    base = updater_base(home)
    name = task_name(home)
    entry = base / "entry.py"
    try:
        if args.command == "status":
            status = query_task(name, entry, base)
            print(json.dumps(_result(name=name, entry=entry, registered=_registered(status), base=base, action="status"), ensure_ascii=True))
            return 0 if status in {"managed", "missing"} else 1
        if args.command == "uninstall":
            status = query_task(name, entry, base)
            if status not in {"managed", "missing"}:
                print(json.dumps(_result(name=name, entry=entry, registered=None, base=base, action="uninstall", dry_run=args.dry_run), ensure_ascii=True))
                return 1
            if args.dry_run:
                print(json.dumps(_result(name=name, entry=entry, registered=status == "managed", base=base, action="uninstall", dry_run=True), ensure_ascii=True))
                return 0
            final = unregister_task(name, entry, base)
            registered = False if final in {"removed", "missing"} else None
            print(json.dumps(_result(name=name, entry=entry, registered=registered, base=base, action="uninstall"), ensure_ascii=True))
            return 0 if final in {"removed", "missing"} else 1
        if args.dry_run:
            print(json.dumps(_result(name=name, entry=entry, registered=False, base=base, action="install", dry_run=True), ensure_ascii=True))
            return 0
        cli = _resolve_cli(args.codex)
        status = query_task(name, entry, base)
        if status == "conflict":
            raise ValueError("refusing to replace an unrelated scheduled task")
        if status not in {"missing", "managed"}:
            raise ValueError("scheduled task status is unavailable")
        entry = install_files(base, home, cli, cli_mode="explicit" if args.codex is not None else "auto")
        status = query_task(name, entry, base)
        if status == "conflict":
            raise ValueError("refusing to replace an unrelated scheduled task")
        if status not in {"missing", "managed"}:
            raise ValueError("scheduled task status is unavailable")
        if not register_task(name, entry, base):
            raise ValueError("scheduled task registration failed")
        if query_task(name, entry, base) != "managed":
            raise ValueError("scheduled task registration could not be verified")
        print(json.dumps(_result(name=name, entry=entry, registered=True, base=base, action="install"), ensure_ascii=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print("Codex Task Routing updater operation failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
