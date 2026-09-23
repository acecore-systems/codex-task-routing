"""Shared safe filesystem and JSON primitives for policy and observation code."""
from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any

MAX_JSON_BYTES = 512 * 1024
EFFORT_ORDER = {"medium": 0, "high": 1, "xhigh": 2, "max": 3}

# Fixed diagnostic text only: never interpolate keys, values, paths, or OS errors.
ERROR_HINTS = {
    "invalid_policy": "Check the configuration structure and required fields against references/configuration.md.",
    "invalid_json": "Use a UTF-8 JSON object with valid syntax and no duplicate keys.",
    "unsupported_key": "Remove unknown keys; compare model and principle keys with the bundled defaults.",
    "unsupported_schema": "Use the supported schema_version: 1 (an integer).",
    "unsupported_effort": "Use a supported effort: medium, high, xhigh, or max.",
    "invalid_effort_range": "Set min_effort <= default_effort <= max_effort.",
    "invalid_template": "Check template references for unknown names, cycles, and excessive expansion; restore damaged bundled templates from the same release.",
    "filesystem_unavailable": "Check that the configured files exist and are readable; do not change unrelated permissions.",
    "unsafe_path": "Use a regular file or directory without symlink or reparse-point ancestors.",
    "invalid_cache": "The existing cache is unsafe or differs from the policy; inspect it before replacing or removing it.",
}


class RoutingError(Exception):
    """A safe, user-actionable policy error.

    Messages deliberately contain neither parsed configuration values nor file
    bodies.  Hook diagnostics can therefore use a fixed error category.
    """

    def __init__(self, message: str, *, code: str = "invalid_policy") -> None:
        super().__init__(message)
        self.code = code if code in ERROR_HINTS else "invalid_policy"


def diagnostic_fields(code: str) -> dict[str, str]:
    code = code if code in ERROR_HINTS else "invalid_policy"
    return {"error_code": code, "hint": ERROR_HINTS[code]}


class DuplicateKeyError(RoutingError):
    pass


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError("JSON contains duplicate object keys")
        result[key] = value
    return result


def _is_link_or_reparse(path: Path) -> bool:
    """Return true for a symlink, Windows junction, or other reparse point."""

    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RoutingError("cannot inspect a filesystem path", code="filesystem_unavailable") from exc
    if stat.S_ISLNK(mode):
        return True
    try:
        attributes = os.lstat(path).st_file_attributes  # type: ignore[attr-defined]
    except AttributeError:
        return False
    except OSError as exc:
        raise RoutingError("cannot inspect a filesystem path", code="filesystem_unavailable") from exc
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def _absolute(path: Path | str) -> Path:
    # abspath normalizes ``.`` and ``..`` without resolving symlinks.
    return Path(os.path.abspath(os.fspath(path)))


def _assert_safe_ancestors(path: Path | str) -> Path:
    """Normalize a path and reject every existing link/reparse component."""

    absolute = _absolute(path)
    parts: list[Path] = []
    current = absolute
    while True:
        parts.append(current)
        if current.parent == current:
            break
        current = current.parent
    for candidate in reversed(parts):
        if os.path.lexists(candidate) and _is_link_or_reparse(candidate):
            raise RoutingError("refusing a symlink or reparse-point path", code="unsafe_path")
    return absolute


def _read_limited_utf8(path: Path, limit: int, purpose: str) -> str:
    path = _assert_safe_ancestors(path)
    if not os.path.lexists(path) or _is_link_or_reparse(path):
        raise RoutingError(f"{purpose} is unavailable", code="filesystem_unavailable")
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise RoutingError(f"{purpose} is unavailable", code="filesystem_unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
        raise RoutingError(f"{purpose} is invalid")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoutingError(f"{purpose} is unavailable", code="filesystem_unavailable") from exc
    except UnicodeDecodeError as exc:
        raise RoutingError(f"{purpose} is not valid UTF-8", code="invalid_json") from exc


def _load_json(path: Path, purpose: str, limit: int = MAX_JSON_BYTES) -> dict[str, Any]:
    text = _read_limited_utf8(path, limit, purpose)
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
    except (json.JSONDecodeError, DuplicateKeyError, RecursionError) as exc:
        raise RoutingError(f"{purpose} is not valid JSON", code="invalid_json") from exc
    if not isinstance(value, dict):
        raise RoutingError(f"{purpose} must be a JSON object", code="invalid_json")
    return value


def _is_schema_version_one(value: Any) -> bool:
    # ``bool`` is an ``int`` subclass in Python, but JSON true is not schema 1.
    return type(value) is int and value == 1


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_mkdir(path: Path) -> Path:
    path = _assert_safe_ancestors(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RoutingError("cannot create the policy output directory") from exc
    path = _assert_safe_ancestors(path)
    if not path.is_dir() or _is_link_or_reparse(path):
        raise RoutingError("policy output directory is unsafe")
    return path


def _write_atomic(directory: Path, name: str, content: str) -> None:
    directory = _assert_safe_ancestors(directory)
    target = directory / name
    if os.path.lexists(target) and _is_link_or_reparse(target):
        raise RoutingError("refusing to overwrite a link or reparse point")
    temporary = directory / f".routing-write-{secrets.token_hex(12)}"
    try:
        with open(temporary, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if _is_link_or_reparse(temporary):
            raise RoutingError("temporary policy output is unsafe")
        os.replace(temporary, target)
    except RoutingError:
        raise
    except OSError as exc:
        raise RoutingError("cannot write policy output") from exc
    finally:
        # Only clean up a file created by this invocation, never directory data.
        if os.path.lexists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass
