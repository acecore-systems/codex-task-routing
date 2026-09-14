#!/usr/bin/env python3
"""Synchronize this marketplace before a new Codex process starts.

This file is deliberately standalone.  The durable launcher loads it from the
currently installed plugin snapshot, which may be replaced by the upgrade it
starts.  Do not import code from that snapshot after startup.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse


PLUGIN_NAME = "codex-task-routing"
PLUGIN_ID = f"{PLUGIN_NAME}@{PLUGIN_NAME}"
MARKETPLACE_NAME = "codex-task-routing"
EXPECTED_GIT_HOST = "github.com"
EXPECTED_GIT_PATH = "/acecore-systems/codex-task-routing"
SAFE_VERSION = re.compile(r"\d+\.\d+\.\d+(?:[+-][A-Za-z0-9._-]+)?$")

STATE_RELATIVE = Path(PLUGIN_NAME) / "launcher" / "state"
LOCK_NAME = "sync.lock"
DIAGNOSTIC_NAME = "last-sync.json"
SCHEMA_VERSION = 1
CLI_TIMEOUT_SECONDS = 60
TERMINATION_GRACE_SECONDS = 3
LOCK_BUSY_EXIT = 75
STATE_ERROR_EXIT = 76
UNCONTAINED_UPDATE_EXIT = 77
ERROR_NO_MORE_FILES = 18

# The root launcher owns this Job Object handle.  The helper assigns itself to
# the job before it starts the native CLI, so every descendant is terminated
# when the root closes the handle after a timeout or a normal update.
WINDOWS_JOB_HELPER = """import ctypes
from ctypes import wintypes
import subprocess
import sys
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
open_job = kernel32.OpenJobObjectW
open_job.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
open_job.restype = wintypes.HANDLE
assign = kernel32.AssignProcessToJobObject
assign.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
assign.restype = wintypes.BOOL
current = kernel32.GetCurrentProcess
current.restype = wintypes.HANDLE
close = kernel32.CloseHandle
close.argtypes = (wintypes.HANDLE,)
close.restype = wintypes.BOOL
job = open_job(0x0001, False, sys.argv[1])
if not job or not assign(job, current()):
    if job:
        close(job)
    raise SystemExit(125)
if not close(job):
    raise SystemExit(125)
try:
    raise SystemExit(subprocess.call(sys.argv[2:], stdin=subprocess.DEVNULL))
except FileNotFoundError:
    raise SystemExit(126)
"""

# These names are intentionally narrow: process inspection must not examine a
# command line, owner, working directory, or any application data.
BLOCKING_PROCESS_NAMES = frozenset(
    {
        "codex",
        "codex.exe",
        "chatgpt",
        "chatgpt.exe",
        "chatgptdesktop",
        "chatgptdesktop.exe",
    }
)


class StateError(Exception):
    """The launcher state directory cannot be safely used."""


class LockBusy(Exception):
    """Another launcher owns the per-CODEX_HOME synchronization lock."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    missing: bool = False
    containment_failed: bool = False


@dataclass(frozen=True)
class LauncherLock:
    path: Path
    descriptor: int


@dataclass(frozen=True)
class WindowsJob:
    handle: int
    name: str


class UpdateContainmentError(Exception):
    """A timed-out marketplace command could not be conclusively stopped."""


def _absolute(path: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise StateError("cannot inspect launcher state") from exc
    if stat.S_ISLNK(metadata.st_mode):
        return True
    try:
        attributes = metadata.st_file_attributes  # type: ignore[attr-defined]
    except AttributeError:
        return False
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def _assert_safe_ancestors(path: Path | str) -> Path:
    absolute = _absolute(path)
    ancestors: list[Path] = []
    current = absolute
    while True:
        ancestors.append(current)
        if current.parent == current:
            break
        current = current.parent
    for candidate in reversed(ancestors):
        if os.path.lexists(candidate) and _is_link_or_reparse(candidate):
            raise StateError("launcher state path is a link or reparse point")
    return absolute


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return _absolute(configured) if configured else _absolute(Path.home() / ".codex")


def _state_directory() -> Path:
    directory = _assert_safe_ancestors(_codex_home() / STATE_RELATIVE)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StateError("cannot create launcher state") from exc
    directory = _assert_safe_ancestors(directory)
    if not directory.is_dir() or _is_link_or_reparse(directory):
        raise StateError("launcher state is unsafe")
    return directory


def _write_diagnostic(state_dir: Path, outcome: str, reason: str, **details: str) -> None:
    """Persist only a small, non-secret machine-readable launcher result."""

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": int(time.time()),
        "outcome": outcome,
        "reason": reason,
    }
    for key, value in details.items():
        if isinstance(value, str) and value and len(value) <= 128:
            payload[key] = value
    destination = _assert_safe_ancestors(state_dir / DIAGNOSTIC_NAME)
    temporary = state_dir / f".diagnostic-{secrets.token_hex(12)}"
    try:
        with open(temporary, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if _is_link_or_reparse(temporary):
            raise StateError("launcher diagnostic is unsafe")
        os.replace(temporary, destination)
    except StateError:
        raise
    except OSError as exc:
        raise StateError("cannot write launcher diagnostic") from exc
    finally:
        if os.path.lexists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _acquire_lock(state_dir: Path) -> LauncherLock:
    path = _assert_safe_ancestors(state_dir / LOCK_NAME)
    flags = os.O_RDWR | os.O_CREAT
    if os.name != "nt" and hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise StateError("cannot acquire launcher lock") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise StateError("launcher lock is not a regular file")
        if os.name == "nt":
            import msvcrt

            # msvcrt locks a byte range.  Keep the fixed lock file after each
            # run, but ensure it contains the one byte that is locked.
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(descriptor)
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            raise LockBusy() from exc
        raise StateError("cannot initialize launcher lock") from exc
    except StateError:
        os.close(descriptor)
        raise
    return LauncherLock(path=path, descriptor=descriptor)


def _release_lock(lock: LauncherLock) -> None:
    """Release the operating-system lock; leave its fixed file for later runs."""

    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(lock.descriptor, 0, os.SEEK_SET)
            msvcrt.locking(lock.descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.descriptor, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        try:
            os.close(lock.descriptor)
        except OSError:
            pass


def _windows_process_names() -> set[str] | None:
    """Return executable basenames via Toolhelp32 without opening process handles."""

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    except (AttributeError, OSError):
        return None
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in (None, 0, invalid_handle):
        return None
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    names: set[str] = set()
    try:
        if not kernel32.Process32FirstW(handle, ctypes.byref(entry)):
            return None
        while True:
            if entry.szExeFile:
                names.add(entry.szExeFile.casefold())
            if not kernel32.Process32NextW(handle, ctypes.byref(entry)):
                # A failed partial scan must not be mistaken for an idle host.
                if ctypes.get_last_error() != ERROR_NO_MORE_FILES:
                    return None
                break
        return names
    finally:
        kernel32.CloseHandle(handle)


def _posix_process_names() -> set[str] | None:
    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "comm="],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return {
        os.path.basename(line.strip()).casefold()
        for line in result.stdout.splitlines()
        if line.strip()
    }


def _running_process_names() -> set[str] | None:
    return _windows_process_names() if os.name == "nt" else _posix_process_names()


def _has_blocking_process(names: Iterable[str]) -> bool:
    return any(name.casefold() in BLOCKING_PROCESS_NAMES for name in names)


def _subprocess_options() -> dict[str, Any]:
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def _create_windows_job() -> WindowsJob | None:
    """Create a private kill-on-close job before the helper starts the CLI."""

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateJobObjectW
        create.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        create.restype = wintypes.HANDLE
        configure = kernel32.SetInformationJobObject
        configure.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        configure.restype = wintypes.BOOL
        close = kernel32.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        name = f"CodexTaskRoutingPrelaunch-{secrets.token_hex(16)}"
        handle = create(None, name)
        if not handle:
            return None
        information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        information.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not configure(handle, 9, ctypes.byref(information), ctypes.sizeof(information)):
            close(handle)
            return None
        return WindowsJob(handle=int(handle), name=name)
    except (AttributeError, OSError):
        return None


def _close_windows_job(job: WindowsJob) -> None:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close = kernel32.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        close(wintypes.HANDLE(job.handle))
    except (AttributeError, OSError):
        pass


def _terminate_windows_job(job: WindowsJob) -> bool:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        terminate = kernel32.TerminateJobObject
        terminate.argtypes = (wintypes.HANDLE, wintypes.UINT)
        terminate.restype = wintypes.BOOL
        return bool(terminate(wintypes.HANDLE(job.handle), 1))
    except (AttributeError, OSError):
        return False


def _wait_for_command_stop(process: subprocess.Popen[str]) -> bool:
    try:
        process.communicate(timeout=TERMINATION_GRACE_SECONDS)
        return True
    except subprocess.TimeoutExpired:
        return False


def _terminate_process_tree(process: subprocess.Popen[str], job: WindowsJob | None = None) -> bool:
    """Stop every process this launcher started, even when the root has exited."""

    if os.name == "nt":
        if job is None or not _terminate_windows_job(job):
            return False
        return _wait_for_command_stop(process)

    # The root may already have exited while a child holds stdout/stderr open.
    # Its process group still identifies every child created by this command.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        return False
    if _wait_for_command_stop(process):
        return True
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        return False
    return _wait_for_command_stop(process)


def _cli_environment() -> dict[str, str]:
    environment = os.environ.copy()
    # Marketplace synchronization is non-interactive.  We neither inspect nor
    # serialize inherited authentication material.
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GCM_INTERACTIVE"] = "Never"
    environment["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    return environment


def _run_command(command: Sequence[str], state_dir: Path, *, timeout: int | float = CLI_TIMEOUT_SECONDS) -> CommandResult:
    job: WindowsJob | None = None
    actual_command = list(command)
    if os.name == "nt":
        job = _create_windows_job()
        if job is None:
            return CommandResult(returncode=None)
        actual_command = [sys.executable, "-c", WINDOWS_JOB_HELPER, job.name, *actual_command]
    try:
        process = subprocess.Popen(
            actual_command,
            cwd=state_dir,
            env=_cli_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_subprocess_options(),
        )
    except FileNotFoundError:
        if job is not None:
            _close_windows_job(job)
        return CommandResult(returncode=None, missing=True)
    except OSError:
        if job is not None:
            _close_windows_job(job)
        return CommandResult(returncode=None)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        contained = _terminate_process_tree(process, job)
        return CommandResult(returncode=None, timed_out=True, containment_failed=not contained)
    finally:
        if job is not None:
            _close_windows_job(job)
    # The helper maps a missing target executable to its reserved status.
    if os.name == "nt" and process.returncode == 126:
        return CommandResult(returncode=None, missing=True)
    return CommandResult(returncode=process.returncode, stdout=stdout, stderr=stderr)


def _run_cli(codex: str, arguments: Sequence[str], state_dir: Path) -> CommandResult:
    return _run_command([codex, "plugin", *arguments], state_dir)


def _text_category(text: str) -> str:
    normalized = text.casefold()
    if any(
        term in normalized
        for term in (
            "authentication failed",
            "not authenticated",
            "authorization failed",
            "sign in",
            "login",
            "credential",
            "access token",
            "token expired",
        )
    ):
        return "authentication"
    if any(
        term in normalized
        for term in (
            "network unreachable",
            "network error",
            "connection refused",
            "failed to connect",
            "could not resolve",
            "dns",
            "unreachable",
            "connection timed out",
            "tls handshake",
        )
    ):
        return "connection"
    return "cli_error"


def _failure_category(result: CommandResult) -> str:
    if result.missing:
        return "cli_missing"
    if result.timed_out:
        return "timeout"
    return _text_category(result.stderr + "\n" + result.stdout)


def _decode_json(result: CommandResult) -> dict[str, Any] | None:
    if result.returncode != 0 or result.timed_out or result.missing:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _expected_marketplace_source(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("sourceType") != "git":
        return False
    source = value.get("source")
    if not isinstance(source, str) or len(source) > 2048:
        return False
    try:
        parsed = urlparse(source)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() != EXPECTED_GIT_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return False
    path = parsed.path.rstrip("/")
    if path.casefold().endswith(".git"):
        path = path[:-4]
    return path.casefold() == EXPECTED_GIT_PATH


def _installed_plugin(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    installed = payload.get("installed")
    if not isinstance(installed, list):
        return None, "list_invalid"
    matches = [
        entry
        for entry in installed
        if isinstance(entry, dict)
        and entry.get("pluginId") == PLUGIN_ID
        and entry.get("name") == PLUGIN_NAME
        and entry.get("marketplaceName") == MARKETPLACE_NAME
    ]
    if len(matches) != 1:
        return None, "plugin_not_installed"
    entry = matches[0]
    if entry.get("installed") is not True:
        return None, "plugin_not_installed"
    if entry.get("enabled") is not True:
        return None, "plugin_disabled"
    source = entry.get("source")
    if not isinstance(source, dict) or source.get("source") != "local" or not isinstance(source.get("path"), str):
        return None, "plugin_source_invalid"
    if not _expected_marketplace_source(entry.get("marketplaceSource")):
        return None, "marketplace_source_not_authorized"
    version = entry.get("version")
    if version is not None and (
        not isinstance(version, str) or not SAFE_VERSION.fullmatch(version) or len(version) > 128
    ):
        return None, "plugin_version_invalid"
    return entry, None


def _list_plugin(codex: str, state_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    result = _run_cli(
        codex,
        ["list", "--marketplace", MARKETPLACE_NAME, "--json"],
        state_dir,
    )
    if result.containment_failed:
        raise UpdateContainmentError()
    if result.returncode != 0 or result.timed_out or result.missing:
        return None, _failure_category(result)
    payload = _decode_json(result)
    if payload is None:
        return None, "list_invalid"
    return _installed_plugin(payload)


def _upgrade_response_problem(payload: dict[str, Any]) -> str | None:
    selected = payload.get("selectedMarketplaces")
    roots = payload.get("upgradedRoots")
    errors = payload.get("errors")
    if not isinstance(selected, list) or MARKETPLACE_NAME not in selected or not isinstance(roots, list):
        return "upgrade_rejected"
    if not isinstance(errors, list):
        return "upgrade_rejected"
    if not errors:
        return None
    error_text = "\n".join(item for item in errors if isinstance(item, str))
    category = _text_category(error_text)
    return category if category != "cli_error" else "upgrade_rejected"


def _version(entry: dict[str, Any]) -> str | None:
    value = entry.get("version")
    return value if isinstance(value, str) else None


def _synchronize(codex: str, state_dir: Path) -> tuple[str, str, dict[str, str]]:
    before, problem = _list_plugin(codex, state_dir)
    if before is None:
        return "skipped", problem or "list_invalid", {}

    # The list process has exited.  Do not begin the mutating command if a
    # Codex process appeared while the read-only inspection was running.
    names = _running_process_names()
    if names is None:
        return "skipped", "process_check_unknown_after_list", {}
    if _has_blocking_process(names):
        return "skipped", "process_running_after_list", {}

    before_version = _version(before)
    result = _run_cli(
        codex,
        ["marketplace", "upgrade", MARKETPLACE_NAME, "--json"],
        state_dir,
    )
    if result.containment_failed:
        raise UpdateContainmentError()
    if result.returncode != 0 or result.timed_out or result.missing:
        return "failed", _failure_category(result), {}
    payload = _decode_json(result)
    if payload is None:
        return "failed", "upgrade_invalid_json", {}
    problem = _upgrade_response_problem(payload)
    if problem is not None:
        return "failed", problem, {}

    after, after_problem = _list_plugin(codex, state_dir)
    if after is None:
        return "synchronized", "after_list_unverified", {
            "version_before": before_version or "unknown",
        }
    after_version = _version(after)
    details = {
        "version_before": before_version or "unknown",
        "version_after": after_version or "unknown",
    }
    if before_version != after_version:
        return "updated", "version_changed", details
    return "unchanged", "version_unchanged", details


def _start_target(arguments: Sequence[str]) -> subprocess.Popen[Any]:
    # Deliberately inherit the caller's interactive stdio and current working
    # directory.  The update CLI alone runs from the neutral state directory.
    return subprocess.Popen(list(arguments), shell=False)


def _wait_for_target(arguments: Sequence[str]) -> int:
    try:
        process = _start_target(arguments)
    except OSError:
        return 127
    return process.wait()


def run_prelaunch(codex: str, target_arguments: Sequence[str], *, detach: bool = False) -> int:
    """Synchronize when safe, then start and wait for the requested target."""

    if not target_arguments:
        return 2
    try:
        state_dir = _state_directory()
    except StateError:
        return STATE_ERROR_EXIT

    try:
        lock = _acquire_lock(state_dir)
    except LockBusy:
        # Starting another Codex process while a pre-launch update is active
        # recreates the replacement race this launcher exists to prevent.
        return LOCK_BUSY_EXIT
    except StateError:
        return STATE_ERROR_EXIT

    target_process: subprocess.Popen[Any] | None = None
    try:
        try:
            names = _running_process_names()
            if names is None:
                outcome, reason, details = "skipped", "process_check_unknown", {}
            elif _has_blocking_process(names):
                outcome, reason, details = "skipped", "process_running", {}
            else:
                outcome, reason, details = _synchronize(codex, state_dir)
        except UpdateContainmentError:
            try:
                _write_diagnostic(state_dir, "failed", "update_process_uncontained")
            except StateError:
                pass
            return UNCONTAINED_UPDATE_EXIT
        except Exception:
            # A launcher defect must not make an otherwise safe, locked launch
            # impossible.  Keep the diagnostic generic and never serialize an
            # exception that might contain a path or command output.
            outcome, reason, details = "failed", "launcher_internal_error", {}
        try:
            _write_diagnostic(state_dir, outcome, reason, **details)
        except StateError:
            pass
        try:
            target_process = _start_target(target_arguments)
        except OSError:
            try:
                _write_diagnostic(state_dir, "failed", "target_launch_failed")
            except StateError:
                pass
            return 127
    finally:
        _release_lock(lock)
    if target_process is None:
        return 127
    return 0 if detach else target_process.wait()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex", help="Codex CLI path used only for marketplace commands")
    parser.add_argument(
        "--detach",
        action="store_true",
        help="Return after the target has started; intended for the desktop application.",
    )
    parser.add_argument("target", nargs=argparse.REMAINDER, help="Target command after --")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    target = list(args.target)
    if target[:1] == ["--"]:
        target = target[1:]
    if not target:
        return 2
    return run_prelaunch(args.codex, target, detach=args.detach)


if __name__ == "__main__":
    raise SystemExit(main())
