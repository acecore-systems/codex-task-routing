#!/usr/bin/env python3
"""Stable protocol-1 entry for the background marketplace updater.

The scheduled task invokes a copy of this file outside plugin cache paths. It
selects a complete immutable runtime from an atomic pointer, then prefers the
current native plugin source when native `plugin list` can verify it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import sys
from typing import Any, Sequence
from urllib.parse import urlparse


PLUGIN_ID = "codex-task-routing@codex-task-routing"
PLUGIN_NAME = "codex-task-routing"
MARKETPLACE_NAME = "codex-task-routing"
PROTOCOL = 1
MAX_JSON_BYTES = 65536
BLOCKING_PROCESS_NAMES = {"codex", "codex.exe", "chatgpt", "chatgpt.exe", "chatgptdesktop", "chatgptdesktop.exe"}


def _unsafe(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _safe_file(path: Path) -> None:
    if _unsafe(path) or not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("unsafe updater file")


def _read_json(path: Path) -> dict[str, Any]:
    _safe_file(path)
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("updater JSON is too large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid updater JSON")
    return value


def _pointer(base: Path) -> dict[str, Any]:
    for path in (base, base / "current.json"):
        if _unsafe(path):
            raise ValueError("unsafe updater path")
    value = _read_json(base / "current.json")
    generation = value.get("generation")
    codex_home = value.get("codex_home")
    codex_cli = value.get("codex_cli")
    codex_cli_mode = value.get("codex_cli_mode")
    if (
        value.get("owner") != PLUGIN_NAME
        or value.get("protocol") != PROTOCOL
        or not isinstance(generation, str)
        or re.fullmatch(r"[0-9a-f]{64}", generation) is None
        or not isinstance(codex_home, str)
        or not Path(codex_home).is_absolute()
        or not isinstance(codex_cli, str)
        or codex_cli_mode not in {"auto", "explicit"}
    ):
        raise ValueError("unrecognized updater pointer")
    runtime = base / "runtimes" / generation / "updater.py"
    _safe_file(runtime)
    return {"codex_home": codex_home, "codex_cli": codex_cli, "codex_cli_mode": codex_cli_mode, "runtime": runtime}


def _hidden_options() -> dict[str, int]:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _process_names() -> set[str] | None:
    """Use executable names only; a failed scan is deliberately not idle."""

    if os.name != "nt":
        try:
            result = subprocess.run(
                ["ps", "-A", "-o", "comm="], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", timeout=5, check=False,
            )
            if result.returncode != 0:
                return None
            return {os.path.basename(line.strip()).casefold() for line in result.stdout.splitlines() if line.strip()}
        except (OSError, subprocess.SubprocessError):
            return None
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                       ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                       ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                       ("szExeFile", wintypes.WCHAR * 260)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
        invalid = ctypes.c_void_p(-1).value
        if snapshot in (None, 0, invalid):
            return None
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        names: set[str] = set()
        try:
            if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                return None
            while True:
                if entry.szExeFile:
                    names.add(entry.szExeFile.casefold())
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    return names if ctypes.get_last_error() == 18 else None
        finally:
            kernel32.CloseHandle(snapshot)
    except (AttributeError, OSError):
        return None


def _may_query_native_source() -> bool:
    names = _process_names()
    return names is not None and not any(name in BLOCKING_PROCESS_NAMES for name in names)


def _windows_codex_cli() -> Path | None:
    if os.name != "nt":
        return None
    script = (
        "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
        "$p=@(Get-AppxPackage -Name OpenAI.Codex); if($p.Count -ne 1){throw 'Codex package unavailable'}; "
        "($p[0].InstallLocation | ConvertTo-Json -Compress)"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=True,
            **_hidden_options(),
        )
        package = Path(json.loads(result.stdout))
        cli = package / "app" / "resources" / "codex.exe"
        return cli if package.is_absolute() and cli.is_file() else None
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        return None


def _authorized_marketplace(source: Any) -> bool:
    if not isinstance(source, dict) or source.get("sourceType") != "git":
        return False
    url = source.get("source")
    if not isinstance(url, str) or len(url) > 2048:
        return False
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    path = parsed.path.rstrip("/")
    if path.casefold().endswith(".git"):
        path = path[:-4]
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() == "github.com"
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and path.casefold() == "/acecore-systems/codex-task-routing"
    )


def _current_runtime(cli: str, base: Path, codex_home: str) -> Path | None:
    """Read the current native source; do not guess a versioned cache path."""

    environment = os.environ.copy()
    environment["CODEX_HOME"] = codex_home
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GCM_INTERACTIVE"] = "Never"
    environment["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    try:
        result = subprocess.run(
            [cli, "plugin", "list", "--marketplace", MARKETPLACE_NAME, "--json"],
            cwd=base,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
            **_hidden_options(),
        )
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout)
        installed = payload.get("installed") if isinstance(payload, dict) else None
        matches = [item for item in installed or [] if isinstance(item, dict) and item.get("pluginId") == PLUGIN_ID]
        if len(matches) != 1:
            return None
        entry = matches[0]
        local = entry.get("source")
        if (
            entry.get("name") != PLUGIN_NAME
            or entry.get("marketplaceName") != MARKETPLACE_NAME
            or entry.get("installed") is not True
            or entry.get("enabled") is not True
            or not _authorized_marketplace(entry.get("marketplaceSource"))
            or not isinstance(local, dict)
            or local.get("source") != "local"
            or not isinstance(local.get("path"), str)
        ):
            return None
        root = Path(local["path"])
        if not root.is_absolute() or _unsafe(root):
            return None
        manifest = _read_json(root / ".codex-plugin" / "plugin.json")
        runtime = root / "scripts" / "updater.py"
        if manifest.get("name") != PLUGIN_NAME:
            return None
        _safe_file(runtime)
        return runtime
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.SubprocessError):
        return None


def _load_main(script: Path):
    namespace = runpy.run_path(str(script), run_name="codex_task_routing_updater")
    main = namespace.get("main")
    if not callable(main):
        raise ValueError("updater runtime has no main")
    return main


def main(argv: Sequence[str] | None = None) -> int:
    if argv not in (None, [], ["--worker"]):
        return 2
    try:
        base = Path(__file__).resolve().parent
        pointer = _pointer(base)
        cli = Path(pointer["codex_cli"]) if pointer["codex_cli_mode"] == "explicit" else (_windows_codex_cli() or Path(pointer["codex_cli"]))
        # Do not even invoke native list while the app is running or its state
        # is unknown. The prior complete runtime writes that safe skip state.
        runtime = _current_runtime(str(cli), base, pointer["codex_home"]) if _may_query_native_source() else None
        # A failed current-source lookup retains the complete previous runtime.
        runtime = runtime or pointer["runtime"]
        try:
            worker = _load_main(runtime)
        except (OSError, ValueError, ImportError, SyntaxError):
            if runtime == pointer["runtime"]:
                raise
            worker = _load_main(pointer["runtime"])
        return int(worker(["--worker", "--codex", str(cli), "--codex-home", pointer["codex_home"]]))
    except (OSError, ValueError, ImportError, SyntaxError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
