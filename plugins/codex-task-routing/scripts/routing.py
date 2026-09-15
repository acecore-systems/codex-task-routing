#!/usr/bin/env python3
"""Resolve and expose one local Codex task-routing policy.

The module intentionally has no network, subprocess, or Codex-runtime API
dependency.  It only reads the bundled defaults and the opt-in override file.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import hashlib
import importlib.util
import json
import os
import re
import secrets
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_RELATIVE = Path("defaults") / "config.json"
TEMPLATE_RELATIVE = Path("defaults") / "templates"
OVERRIDE_RELATIVE = Path("codex-task-routing") / "overrides.json"
CACHE_RELATIVE = Path("codex-task-routing") / "cache"
OBSERVATIONS_RELATIVE = Path("codex-task-routing") / "observations"
OBSERVATION_LOCK_NAME = ".state.lock"
MARKER_NAME = ".codex-task-routing-managed.json"
RENDERED_TEMPLATE_NAMES = (
    "effective.md",
    "model-routing-policy.md",
    "model-routing-catalog.md",
    "model-routing-handoff.md",
)
RENDERED_FILE_NAMES = RENDERED_TEMPLATE_NAMES
DETAILED_TEMPLATE_NAMES = tuple(name for name in RENDERED_TEMPLATE_NAMES if name != "effective.md")

MAX_JSON_BYTES = 512 * 1024
MAX_TEMPLATE_BYTES = 1024 * 1024
MAX_HOOK_INPUT_BYTES = 128 * 1024
MAX_VALUE_CHARS = 100 * 1024
MAX_RENDERED_BYTES = 1024 * 1024
MAX_OBSERVATION_STATE_BYTES = 512 * 1024
MAX_OBSERVATION_INPUT_BYTES = 128 * 1024
MAX_OBSERVATION_TASK_ID_CHARS = 256
MAX_OBSERVATION_REFERENCE_CHARS = 4096
MAX_OBSERVATION_SCOPE_CHARS = 500
MAX_OBSERVATION_RUNS = 32
MAX_TOKEN_DEPTH = 20
# hooks.json uses a token-oriented platform limit.  Keep this independent,
# conservative character ceiling so the runtime never asks the host to truncate
# an effective policy mid-document.
MAX_ADDITIONAL_CONTEXT_CHARS = 8000

# Windows can briefly retain a directory handle immediately after another
# process creates a fresh cache tree.  Only retry the Windows errors that can
# describe that short sharing/access window, and leave all other filesystem
# errors visible to the caller.
CACHE_RENAME_MAX_ATTEMPTS = 3
CACHE_RENAME_RETRY_SECONDS = 0.05
WINDOWS_TRANSIENT_RENAME_WINERRORS = frozenset({5, 32, 33})

EFFORT_ORDER = {"medium": 0, "high": 1, "xhigh": 2, "max": 3}
MODEL_FIELDS = ("id", "min_effort", "default_effort", "max_effort")
REQUIRED_MODEL_ROLES = ("luna", "terra", "sol", "astra")
SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_TOKEN = re.compile(
    r"{{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*}}"
)


class RoutingError(Exception):
    """A safe, user-actionable policy error.

    Messages deliberately contain neither parsed configuration values nor file
    bodies.  Hook diagnostics can therefore use a fixed error category.
    """


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
        raise RoutingError("cannot inspect a filesystem path") from exc
    if stat.S_ISLNK(mode):
        return True
    try:
        attributes = os.lstat(path).st_file_attributes  # type: ignore[attr-defined]
    except AttributeError:
        return False
    except OSError as exc:
        raise RoutingError("cannot inspect a filesystem path") from exc
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
            raise RoutingError("refusing a symlink or reparse-point path")
    return absolute


def _read_limited_utf8(path: Path, limit: int, purpose: str) -> str:
    path = _assert_safe_ancestors(path)
    if not os.path.lexists(path) or _is_link_or_reparse(path):
        raise RoutingError(f"{purpose} is unavailable")
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise RoutingError(f"{purpose} is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
        raise RoutingError(f"{purpose} is invalid")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RoutingError(f"{purpose} is not valid UTF-8") from exc


def _load_json(path: Path, purpose: str, limit: int = MAX_JSON_BYTES) -> dict[str, Any]:
    text = _read_limited_utf8(path, limit, purpose)
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
    except (json.JSONDecodeError, DuplicateKeyError, RecursionError) as exc:
        raise RoutingError(f"{purpose} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RoutingError(f"{purpose} must be a JSON object")
    return value


def _require_exact_keys(value: Mapping[str, Any], allowed: set[str], purpose: str) -> None:
    if set(value) - allowed:
        raise RoutingError(f"{purpose} contains an unsupported key")


def _validate_string(value: Any, purpose: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_VALUE_CHARS:
        raise RoutingError(f"{purpose} must be a non-empty string")
    return value


def _is_schema_version_one(value: Any) -> bool:
    # ``bool`` is an ``int`` subclass in Python, but JSON true is not schema 1.
    return type(value) is int and value == 1


def _validate_model(model: Mapping[str, Any], purpose: str, *, partial: bool) -> None:
    _require_exact_keys(model, set(MODEL_FIELDS), purpose)
    if not model:
        raise RoutingError(f"{purpose} cannot be empty")
    if not partial and set(model) != set(MODEL_FIELDS):
        raise RoutingError(f"{purpose} must contain every model field")
    if "id" in model:
        identifier = _validate_string(model["id"], purpose)
        if not SAFE_MODEL_ID.fullmatch(identifier):
            raise RoutingError(f"{purpose} has an invalid model id")
    for effort_key in ("min_effort", "default_effort", "max_effort"):
        if effort_key in model:
            effort = model[effort_key]
            if not isinstance(effort, str) or effort not in EFFORT_ORDER:
                raise RoutingError(f"{purpose} has an unsupported effort")


def _validate_effort_range(model: Mapping[str, Any], purpose: str) -> None:
    try:
        valid = (
            EFFORT_ORDER[model["min_effort"]]
            <= EFFORT_ORDER[model["default_effort"]]
            <= EFFORT_ORDER[model["max_effort"]]
        )
    except (KeyError, TypeError) as exc:
        raise RoutingError(f"{purpose} is incomplete") from exc
    if not valid:
        raise RoutingError(f"{purpose} has an invalid effort range")


def _validate_defaults(config: dict[str, Any]) -> None:
    _require_exact_keys(
        config, {"schema_version", "policy_revision", "models", "principles"}, "defaults"
    )
    if not _is_schema_version_one(config.get("schema_version")):
        raise RoutingError("defaults has an unsupported schema version")
    _validate_string(config.get("policy_revision"), "defaults policy revision")
    models = config.get("models")
    if not isinstance(models, dict) or set(models) != set(REQUIRED_MODEL_ROLES):
        raise RoutingError("defaults must contain the supported model roles")
    for role, model in models.items():
        if not isinstance(model, dict):
            raise RoutingError("defaults model must be an object")
        _validate_model(model, "defaults model", partial=False)
        _validate_effort_range(model, "defaults model")
    principles = config.get("principles")
    if not isinstance(principles, dict) or not principles:
        raise RoutingError("defaults principles must be a non-empty object")
    for key, value in principles.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise RoutingError("defaults contains an invalid principle key")
        _validate_string(value, "defaults principle")


def _merge_override(defaults: dict[str, Any], override: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    _require_exact_keys(override, {"schema_version", "models", "principles"}, "override")
    if not _is_schema_version_one(override.get("schema_version")):
        raise RoutingError("override has an unsupported schema version")
    has_models = "models" in override
    has_principles = "principles" in override
    if not has_models and not has_principles:
        raise RoutingError("override has no changes")

    merged = json.loads(json.dumps(defaults))
    applied: list[str] = []
    if has_models:
        models = override["models"]
        if not isinstance(models, dict) or not models:
            raise RoutingError("override models must be a non-empty object")
        for role, patch in models.items():
            if role not in merged["models"] or not isinstance(patch, dict):
                raise RoutingError("override contains an unsupported model")
            _validate_model(patch, "override model", partial=True)
            for field, value in patch.items():
                merged["models"][role][field] = value
                applied.append(f"models.{role}.{field}")
    if has_principles:
        principles = override["principles"]
        if not isinstance(principles, dict) or not principles:
            raise RoutingError("override principles must be a non-empty object")
        for key, value in principles.items():
            if key not in merged["principles"]:
                raise RoutingError("override contains an unsupported principle")
            merged["principles"][key] = _validate_string(value, "override principle")
            applied.append(f"principles.{key}")

    for model in merged["models"].values():
        _validate_effort_range(model, "effective model")
    return merged, applied


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _manifest_version(plugin_root: Path) -> str:
    manifest = _load_json(plugin_root / ".codex-plugin" / "plugin.json", "plugin manifest")
    version = manifest.get("version")
    return _validate_string(version, "plugin version")


def _load_templates(plugin_root: Path) -> dict[str, str]:
    directory = _assert_safe_ancestors(plugin_root / TEMPLATE_RELATIVE)
    if not directory.is_dir() or _is_link_or_reparse(directory):
        raise RoutingError("template directory is unavailable")
    templates: dict[str, str] = {}
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise RoutingError("template directory is unavailable") from exc
    for path in entries:
        if path.suffix.lower() == ".md":
            if _is_link_or_reparse(path) or not path.is_file():
                raise RoutingError("template is unavailable")
            templates[path.name] = _read_limited_utf8(path, MAX_TEMPLATE_BYTES, "template")
    if set(templates) != set(RENDERED_TEMPLATE_NAMES):
        raise RoutingError("templates do not match the packaged policy set")
    return templates


def _default_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return _absolute(configured) if configured else _absolute(Path.home() / ".codex")


@dataclass(frozen=True)
class Policy:
    plugin_root: Path
    version: str
    config: dict[str, Any]
    templates: dict[str, str]
    applied_override_keys: tuple[str, ...]
    override_present: bool
    config_hash: str
    content_hash: str

    def resolve_token(self, token: str) -> str:
        """Resolve a dotted config token, including recursive value references."""

        cache: dict[str, str] = {}

        def value_for(path: str) -> Any:
            current: Any = self.config
            for part in path.split("."):
                if not isinstance(current, dict) or part not in current:
                    raise RoutingError("policy contains an unresolved token")
                current = current[part]
            return current

        def resolve(path: str, stack: tuple[str, ...]) -> str:
            if path in cache:
                return cache[path]
            if path in stack:
                raise RoutingError("policy contains a cyclic token")
            if len(stack) >= MAX_TOKEN_DEPTH:
                raise RoutingError("policy token expansion is too deep")
            value = value_for(path)
            if not isinstance(value, str):
                raise RoutingError("policy token does not resolve to text")
            expanded = substitute(value, lambda child: resolve(child, stack + (path,)))
            if len(expanded.encode("utf-8")) > MAX_RENDERED_BYTES:
                raise RoutingError("policy token expansion is too large")
            cache[path] = expanded
            return expanded

        return resolve(token, ())

    def render_template(self, text: str) -> str:
        rendered = substitute(text, self.resolve_token)
        if len(rendered.encode("utf-8")) > MAX_RENDERED_BYTES:
            raise RoutingError("rendered policy is too large")
        return rendered

    def rendered_documents(self) -> dict[str, str]:
        # Every packaged Markdown file is a template.  In particular,
        # ``effective.md`` is maintained as the concise canonical policy rather
        # than synthesized in the runtime, so a default render is reproducible.
        return {
            name: self.render_template(template)
            for name, template in self.templates.items()
        }


def substitute(text: str, resolver: Callable[[str], str]) -> str:
    """Expand all dotted tokens, rejecting malformed or residual delimiters."""

    def replace(match: re.Match[str]) -> str:
        return resolver(match.group(1))

    rendered = SAFE_TOKEN.sub(replace, text)
    if "{{" in rendered or "}}" in rendered:
        raise RoutingError("policy contains an unresolved token")
    return rendered


def load_policy(
    *,
    plugin_root: Path = PLUGIN_ROOT,
    codex_home: Path | None = None,
    config_path: Path | None = None,
) -> Policy:
    plugin_root = _assert_safe_ancestors(plugin_root)
    defaults = _load_json(plugin_root / DEFAULT_CONFIG_RELATIVE, "defaults")
    _validate_defaults(defaults)
    home = _absolute(codex_home) if codex_home is not None else _default_codex_home()
    override_path = _absolute(config_path) if config_path is not None else home / OVERRIDE_RELATIVE
    explicit_override = config_path is not None
    override_present = os.path.lexists(override_path)
    if explicit_override and not override_present:
        raise RoutingError("override configuration is unavailable")
    if override_present:
        override = _load_json(override_path, "override configuration")
        config, applied = _merge_override(defaults, override)
    else:
        config, applied = defaults, []
    templates = _load_templates(plugin_root)
    version = _manifest_version(plugin_root)
    config_hash = hashlib.sha256(_canonical_json(config)).hexdigest()
    hash_input = {
        "manifest_version": version,
        "config": config,
        "templates": {name: templates[name] for name in sorted(templates)},
    }
    content_hash = hashlib.sha256(_canonical_json(hash_input)).hexdigest()
    return Policy(
        plugin_root=plugin_root,
        version=version,
        config=config,
        templates=templates,
        applied_override_keys=tuple(applied),
        override_present=override_present,
        config_hash=config_hash,
        content_hash=content_hash,
    )


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


def _safe_directory_entries(directory: Path) -> list[Path]:
    directory = _assert_safe_ancestors(directory)
    if not directory.is_dir() or _is_link_or_reparse(directory):
        raise RoutingError("policy output directory is unsafe")
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise RoutingError("cannot inspect the policy output directory") from exc
    for entry in entries:
        if _is_link_or_reparse(entry):
            raise RoutingError("policy output directory contains a link or reparse point")
    return entries


def _is_managed_directory(directory: Path) -> bool:
    entries = _safe_directory_entries(directory)
    names = {entry.name for entry in entries}
    allowed = set(RENDERED_FILE_NAMES) | {MARKER_NAME}
    if names != allowed:
        return False
    marker_path = directory / MARKER_NAME
    try:
        marker = _load_json(marker_path, "output marker")
    except RoutingError:
        return False
    return (
        marker.get("managed_by") == "codex-task-routing"
        and _is_schema_version_one(marker.get("schema_version"))
    )


def _prepare_render_directory(output_dir: Path) -> Path:
    output_dir = _assert_safe_ancestors(output_dir)
    if os.path.lexists(output_dir):
        if _is_link_or_reparse(output_dir) or not output_dir.is_dir():
            raise RoutingError("output directory is unsafe")
        entries = _safe_directory_entries(output_dir)
        if entries and not _is_managed_directory(output_dir):
            raise RoutingError("output directory is not empty or plugin-managed")
        return output_dir
    return _safe_mkdir(output_dir)


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


def render_policy(policy: Policy, output_dir: Path) -> dict[str, Path]:
    """Render into a new, empty, or explicitly plugin-managed directory."""

    output_dir = _prepare_render_directory(output_dir)
    documents = policy.rendered_documents()
    for name in RENDERED_FILE_NAMES:
        _write_atomic(output_dir, name, documents[name])
    marker = json.dumps(
        {
            "schema_version": 1,
            "managed_by": "codex-task-routing",
            "manifest_version": policy.version,
            "policy_hash": policy.content_hash,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    _write_atomic(output_dir, MARKER_NAME, marker)
    return {name: _absolute(output_dir / name) for name in RENDERED_FILE_NAMES}


def _cache_matches_policy(directory: Path, policy: Policy) -> bool:
    """Verify a cache entry is the exact generated policy, not just marked ours."""

    if not _is_managed_directory(directory):
        return False
    marker = _load_json(directory / MARKER_NAME, "output marker")
    if (
        marker.get("policy_hash") != policy.content_hash
        or marker.get("manifest_version") != policy.version
    ):
        return False
    expected_documents = policy.rendered_documents()
    for name in RENDERED_FILE_NAMES:
        cached = _read_limited_utf8(directory / name, MAX_RENDERED_BYTES, "cached policy")
        if cached != expected_documents[name]:
            return False
    return True


def _verified_existing_cache(destination: Path, policy: Policy) -> bool:
    """Return whether a published cache is exact, rejecting unsafe entries."""

    if not os.path.lexists(destination):
        return False
    if _is_link_or_reparse(destination) or not destination.is_dir():
        raise RoutingError("policy cache entry is unsafe")
    if not _cache_matches_policy(destination, policy):
        raise RoutingError("policy cache entry does not match the effective policy")
    return True


def _is_transient_windows_rename_error(exc: OSError) -> bool:
    """Recognize only bounded-retry candidates from Windows directory sharing."""

    return os.name == "nt" and getattr(exc, "winerror", None) in WINDOWS_TRANSIENT_RENAME_WINERRORS


def _cache_directory(codex_home: Path, policy: Policy) -> Path:
    """Create a fresh content-addressed cache directory without touching an old one."""

    cache_root = _safe_mkdir(_absolute(codex_home) / CACHE_RELATIVE)
    destination = cache_root / policy.content_hash
    if _verified_existing_cache(destination, policy):
        return destination
    staging = cache_root / f".routing-staging-{policy.content_hash}-{secrets.token_hex(8)}"
    try:
        # ``exist_ok=False`` makes every write occur in a new directory.
        staging.mkdir()
        render_policy(policy, staging)
        # os.rename does not overwrite an existing destination on Windows.
        for attempt in range(CACHE_RENAME_MAX_ATTEMPTS):
            try:
                os.rename(staging, destination)
                return destination
            except OSError as exc:
                # Windows uses EEXIST and Linux may use ENOTEMPTY when a
                # concurrent hook has already atomically published this
                # non-empty directory.  Never accept a link or mismatched
                # directory as a concurrent winner.
                if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                    if _verified_existing_cache(destination, policy):
                        return destination
                    raise RoutingError("policy cache entry appeared during rendering") from exc
                # A fresh directory can be temporarily held by Windows file
                # indexing or another local process.  WinError 5 is also used
                # for permanent denial, so this remains short and bounded; a
                # persistent failure still escapes as a RoutingError.
                if (
                    _is_transient_windows_rename_error(exc)
                    and attempt + 1 < CACHE_RENAME_MAX_ATTEMPTS
                ):
                    if _verified_existing_cache(destination, policy):
                        return destination
                    time.sleep(CACHE_RENAME_RETRY_SECONDS * (attempt + 1))
                    continue
                raise RoutingError("cannot create the policy cache entry") from exc
    except OSError as exc:
        raise RoutingError("cannot create the policy cache entry") from exc


def _guidance_directories(cwd: Path, codex_home: Path | None) -> list[Path]:
    """Return cwd through its repo root, followed by the opted-in Codex home."""

    directories = [_absolute(cwd)]
    current = directories[0]
    repo_root: Path | None = None
    while True:
        git_marker = current / ".git"
        if os.path.lexists(git_marker) and not _is_link_or_reparse(git_marker):
            repo_root = current
            break
        # Do not inspect arbitrary ancestor guidance when cwd is not a repo.
        if current.parent == current:
            break
        current = current.parent
    if repo_root is not None:
        current = directories[0]
        while current != repo_root:
            current = current.parent
            directories.append(current)
    if codex_home is not None:
        directories.append(_absolute(codex_home))
    unique: list[Path] = []
    seen: set[str] = set()
    for directory in directories:
        marker = os.path.normcase(os.fspath(directory))
        if marker not in seen:
            seen.add(marker)
            unique.append(directory)
    return unique


def _active_guidance_file(directory: Path) -> tuple[Path, str] | None:
    """Return the one non-empty AGENTS file active in a directory.

    Codex gives a non-empty ``AGENTS.override.md`` precedence over the regular
    file at the same directory level.  An empty override is skipped, so the
    regular file remains active.  Never scan both files: inactive guidance
    must not make this policy appear to conflict.
    """

    for name in ("AGENTS.override.md", "AGENTS.md"):
        path = directory / name
        if not os.path.lexists(path) or _is_link_or_reparse(path):
            continue
        try:
            text = _read_limited_utf8(path, MAX_TEMPLATE_BYTES, "local guidance")
        except RoutingError:
            continue
        if text.strip():
            return path, text
    return None


def detect_routing_conflicts(
    cwd: Path | None = None, *, codex_home: Path | None = None
) -> list[str]:
    """Report absolute conflicting guidance paths without exposing their body."""

    base = _absolute(cwd or Path.cwd())
    conflicts: list[str] = []
    for directory in _guidance_directories(base, codex_home):
        active = _active_guidance_file(directory)
        if active is None:
            continue
        path, text = active
        lowered = text.casefold()
        if "model-routing-policy.md" in lowered or "モデル選定・作業内の委譲" in text:
            conflicts.append(str(_absolute(path)))
    return conflicts


def _hook_available(plugin_root: Path) -> bool:
    hooks = plugin_root / "hooks"
    if not hooks.is_dir() or _is_link_or_reparse(hooks):
        return False
    try:
        return any(path.is_file() and not _is_link_or_reparse(path) for path in hooks.iterdir())
    except OSError:
        return False


def _cache_status(codex_home: Path, policy: Policy) -> tuple[str, Path]:
    """Inspect the expected cache entry without creating or repairing it."""

    destination = _absolute(codex_home) / CACHE_RELATIVE / policy.content_hash
    try:
        # Check every existing parent before classifying an absent destination.
        # Otherwise a reparse-point cache root would be incorrectly reported as
        # a harmless cache miss.
        destination = _assert_safe_ancestors(destination)
        if not os.path.lexists(destination):
            return "missing", destination
        if _is_link_or_reparse(destination) or not destination.is_dir():
            return "unsafe", destination
        if _cache_matches_policy(destination, policy):
            return "valid", destination
        return "mismatch", destination
    except (RoutingError, OSError):
        return "unsafe", destination


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _observation_text(value: Any, purpose: str, limit: int = MAX_OBSERVATION_REFERENCE_CHARS) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise RoutingError(f"{purpose} must be a non-empty string")
    if any(ord(character) < 32 for character in value):
        raise RoutingError(f"{purpose} contains control characters")
    return value


def _observation_optional_text(value: Any, purpose: str) -> str | None:
    if value is None:
        return None
    return _observation_text(value, purpose)


def _observation_task_id(value: Any) -> str:
    return _observation_text(value, "observation task id", MAX_OBSERVATION_TASK_ID_CHARS)


def _observation_sampling_key(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise RoutingError("observation sampling key is invalid")
    return value


def observation_sampling_key(policy: Policy) -> str:
    """Hash rendered policy content, deliberately excluding the manifest version.

    The policy's usual content hash includes the package version because it is
    useful for cache invalidation.  Sampling must instead continue across a
    package-only release when the rendered policy is identical.
    """

    documents = policy.rendered_documents()
    canonical_documents = {name: documents[name] for name in sorted(documents)}
    return hashlib.sha256(_canonical_json(canonical_documents)).hexdigest()


def _observation_policy_metadata(policy: Policy, sampling_key: str) -> dict[str, str]:
    return {
        "policy_revision": policy.config["policy_revision"],
        "manifest_version": policy.version,
        "content_hash": policy.content_hash,
        "sampling_key": sampling_key,
    }


def _empty_observation_state(sampling_key: str) -> dict[str, Any]:
    sampling_key = _observation_sampling_key(sampling_key)
    return {"schema_version": 1, "samples": {sampling_key: {"tasks": {}}}}


def _validate_observation_measurement(value: Any, *, allow_partial: bool) -> None:
    if not isinstance(value, dict):
        raise RoutingError("observation measurement must be an object")
    if set(value) != {"state", "reason"}:
        raise RoutingError("observation measurement has unsupported fields")
    state = value.get("state")
    reason = value.get("reason")
    allowed = {"measured", "missing"}
    if allow_partial:
        allowed.add("partial")
    if state not in allowed:
        raise RoutingError("observation measurement state is invalid")
    if state == "measured":
        if reason is not None:
            raise RoutingError("measured observation must not have a missing reason")
    elif _observation_optional_text(reason, "observation measurement reason") is None:
        raise RoutingError("missing observation measurement needs a reason")


def _validate_observation_verification(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"reference", "reason"}:
        raise RoutingError("observation verification has unsupported fields")
    reference = _observation_optional_text(value.get("reference"), "verification reference")
    reason = _observation_optional_text(value.get("reason"), "verification missing reason")
    if reference is None and reason is None:
        raise RoutingError("missing verification needs a reason")
    if reference is not None and reason is not None:
        raise RoutingError("verified observation must not have a missing reason")


def _validate_observation_requested(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"model", "effort", "reason"}:
        raise RoutingError("requested run settings have unsupported fields")
    model = _observation_optional_text(value.get("model"), "requested model")
    effort = value.get("effort")
    if effort is not None and effort not in EFFORT_ORDER:
        raise RoutingError("requested effort is invalid")
    reason = _observation_optional_text(value.get("reason"), "requested settings reason")
    if (model is None or effort is None) and reason is None:
        raise RoutingError("unknown requested settings need a reason")
    if model is not None and effort is not None and reason is not None:
        raise RoutingError("known requested settings must not have an unknown reason")


def _validate_observation_observed(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"model", "effort", "evidence_ref", "reason"}:
        raise RoutingError("observed run settings have unsupported fields")
    model = _observation_optional_text(value.get("model"), "observed model")
    effort = value.get("effort")
    if effort is not None and effort not in EFFORT_ORDER:
        raise RoutingError("observed effort is invalid")
    evidence = _observation_optional_text(value.get("evidence_ref"), "observed settings reference")
    reason = _observation_optional_text(value.get("reason"), "observed settings reason")
    if model is not None or effort is not None:
        if evidence is None:
            raise RoutingError("observed settings need a verification reference")
    elif evidence is not None:
        raise RoutingError("unknown observed settings must not have a verification reference")
    if (model is None or effort is None) and reason is None:
        raise RoutingError("unknown observed settings need a reason")
    if model is not None and effort is not None and reason is not None:
        raise RoutingError("known observed settings must not have an unknown reason")


def _validate_observation_usage(value: Any) -> None:
    fields = {
        "state",
        "reason",
        "evidence_ref",
        "turn_scope",
        "input_tokens",
        "cache_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "unknown_reasons",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RoutingError("run usage has unsupported fields")
    state = value.get("state")
    reason = _observation_optional_text(value.get("reason"), "run usage reason")
    evidence = _observation_optional_text(value.get("evidence_ref"), "run usage reference")
    if value.get("turn_scope") not in {"full_turn", "partial_turn"}:
        raise RoutingError("run usage turn scope is invalid")
    token_fields = ("input_tokens", "cache_input_tokens", "output_tokens", "reasoning_tokens")
    unknown_reasons = value.get("unknown_reasons")
    if not isinstance(unknown_reasons, dict) or set(unknown_reasons) - set(token_fields):
        raise RoutingError("run usage unknown reasons are invalid")
    for name in token_fields:
        amount = value.get(name)
        if amount is not None and (type(amount) is not int or amount < 0):
            raise RoutingError("run usage token count is invalid")
        missing_reason = unknown_reasons.get(name)
        if amount is None:
            if state == "measured" and _observation_optional_text(
                missing_reason, "run usage unknown reason"
            ) is None:
                raise RoutingError("unknown measured token count needs a reason")
        elif missing_reason is not None:
            raise RoutingError("known token count must not have an unknown reason")
    if state == "missing":
        if reason is None or evidence is not None or any(value.get(name) is not None for name in token_fields):
            raise RoutingError("missing run usage is invalid")
        if unknown_reasons:
            raise RoutingError("missing run usage uses one shared reason")
    elif state == "measured":
        if reason is not None or evidence is None or not any(value.get(name) is not None for name in token_fields):
            raise RoutingError("measured run usage is invalid")
    else:
        raise RoutingError("run usage state is invalid")
    input_tokens = value.get("input_tokens")
    cache_input_tokens = value.get("cache_input_tokens")
    output_tokens = value.get("output_tokens")
    reasoning_tokens = value.get("reasoning_tokens")
    if input_tokens is not None and cache_input_tokens is not None and cache_input_tokens > input_tokens:
        raise RoutingError("cache input exceeds input")
    if output_tokens is not None and reasoning_tokens is not None and reasoning_tokens > output_tokens:
        raise RoutingError("reasoning exceeds output")


def _validate_observation_run(value: Any) -> None:
    fields = {"run_key", "relationship", "state", "run_id", "run_id_reason", "requested", "observed", "usage"}
    if not isinstance(value, dict) or set(value) != fields:
        raise RoutingError("observation run has unsupported fields")
    _observation_text(value.get("run_key"), "run key", 128)
    if value.get("relationship") not in {"parent", "child", "grandchild"}:
        raise RoutingError("run relationship is invalid")
    state = value.get("state")
    if state not in {"ongoing", "completed", "interrupted"}:
        raise RoutingError("run state is invalid")
    run_id = _observation_optional_text(value.get("run_id"), "run id")
    run_id_reason = _observation_optional_text(value.get("run_id_reason"), "run id missing reason")
    if run_id is None and run_id_reason is None:
        raise RoutingError("unknown run id needs a reason")
    if run_id is not None and run_id_reason is not None:
        raise RoutingError("known run id must not have an unknown reason")
    _validate_observation_requested(value.get("requested"))
    _validate_observation_observed(value.get("observed"))
    _validate_observation_usage(value.get("usage"))
    if state != "completed" and value["usage"]["state"] == "measured":
        raise RoutingError("unfinished run usage cannot be recorded as completed usage")


def _validate_observation_completion(value: Any) -> None:
    fields = {"state", "scope", "measurement", "verification", "rework", "runs"}
    if not isinstance(value, dict) or set(value) != fields:
        raise RoutingError("observation completion has unsupported fields")
    state = value.get("state")
    if state not in {"completed", "interrupted"}:
        raise RoutingError("observation completion state is invalid")
    _observation_text(value.get("scope"), "observation scope", MAX_OBSERVATION_SCOPE_CHARS)
    _validate_observation_measurement(value.get("measurement"), allow_partial=True)
    _validate_observation_verification(value.get("verification"))
    _observation_text(value.get("rework"), "rework record")
    runs = value.get("runs")
    if not isinstance(runs, list) or len(runs) > MAX_OBSERVATION_RUNS:
        raise RoutingError("observation runs are invalid")
    run_keys: set[str] = set()
    for run in runs:
        _validate_observation_run(run)
        if run["run_key"] in run_keys:
            raise RoutingError("observation run keys must be unique")
        run_keys.add(run["run_key"])
    measurement = value["measurement"]["state"]
    measured_runs = [run for run in runs if run["usage"]["state"] == "measured"]
    token_fields = ("input_tokens", "cache_input_tokens", "output_tokens", "reasoning_tokens")
    full_coverage = (
        state == "completed"
        and any(run["relationship"] == "parent" for run in runs)
        and all(
            run["state"] == "completed"
            and run["usage"]["state"] == "measured"
            and run["usage"]["turn_scope"] == "full_turn"
            and all(run["usage"][field] is not None for field in token_fields)
            for run in runs
        )
    )
    if measurement == "measured":
        if not full_coverage:
            raise RoutingError("measured observation has incomplete run usage")
    elif measurement == "missing" and measured_runs:
        raise RoutingError("missing observation cannot contain measured run usage")
    elif measurement == "partial" and (not measured_runs or full_coverage):
        raise RoutingError("partial observation measurement is inconsistent")


def _validate_observation_record(value: Any, sampling_key: str, task_id: str) -> None:
    fields = {
        "task_id", "sampling_key", "policy", "state", "measurement", "started_at",
        "updated_at", "completed_at", "scope", "rework", "verification", "runs",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RoutingError("observation record is invalid")
    if value.get("task_id") != task_id or value.get("sampling_key") != sampling_key:
        raise RoutingError("observation record identity is invalid")
    _observation_task_id(value["task_id"])
    policy = value.get("policy")
    if not isinstance(policy, dict) or set(policy) != {"policy_revision", "manifest_version", "content_hash", "sampling_key"}:
        raise RoutingError("observation record policy is invalid")
    _observation_text(policy.get("policy_revision"), "observation policy revision")
    _observation_text(policy.get("manifest_version"), "observation manifest version")
    if not isinstance(policy.get("content_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", policy["content_hash"]):
        raise RoutingError("observation policy hash is invalid")
    if policy.get("sampling_key") != sampling_key:
        raise RoutingError("observation policy sampling key is invalid")
    if value.get("state") not in {"ongoing", "completed", "interrupted"}:
        raise RoutingError("observation record state is invalid")
    _validate_observation_measurement(value.get("measurement"), allow_partial=True)
    for name in ("started_at", "updated_at"):
        _observation_text(value.get(name), "observation timestamp", 64)
    completed_at = value.get("completed_at")
    if value["state"] == "ongoing":
        if completed_at is not None or value.get("scope") is not None or value.get("rework") is not None or value.get("verification") is not None or value.get("runs") != []:
            raise RoutingError("ongoing observation record is invalid")
    else:
        _observation_text(completed_at, "observation completion timestamp", 64)
        _validate_observation_completion(
            {
                "state": value["state"],
                "scope": value["scope"],
                "measurement": value["measurement"],
                "verification": value["verification"],
                "rework": value["rework"],
                "runs": value["runs"],
            }
        )


def _validate_observation_state(value: Any, sampling_key: str) -> dict[str, Any]:
    sampling_key = _observation_sampling_key(sampling_key)
    if not isinstance(value, dict) or set(value) != {"schema_version", "samples"}:
        raise RoutingError("observation state is invalid")
    if not _is_schema_version_one(value.get("schema_version")) or not isinstance(value.get("samples"), dict):
        raise RoutingError("observation state is invalid")
    if set(value["samples"]) != {sampling_key}:
        raise RoutingError("observation state has an unexpected sampling key")
    sample = value["samples"][sampling_key]
    if not isinstance(sample, dict) or set(sample) != {"tasks"} or not isinstance(sample.get("tasks"), dict):
        raise RoutingError("observation sample is invalid")
    if len(sample["tasks"]) > 3:
        raise RoutingError("observation sample exceeds its limit")
    for task_id, record in sample["tasks"].items():
        _observation_task_id(task_id)
        _validate_observation_record(record, sampling_key, task_id)
    return value


def _observation_directory(codex_home: Path, *, create: bool) -> Path:
    directory = _absolute(codex_home) / OBSERVATIONS_RELATIVE
    if create:
        return _safe_mkdir(directory)
    directory = _assert_safe_ancestors(directory)
    if os.path.lexists(directory) and (not directory.is_dir() or _is_link_or_reparse(directory)):
        raise RoutingError("observation directory is unsafe")
    return directory


def _observation_state_path(codex_home: Path, sampling_key: str, *, create: bool) -> Path:
    sampling_key = _observation_sampling_key(sampling_key)
    return _observation_directory(codex_home, create=create) / f"{sampling_key}.json"


def _read_observation_state(codex_home: Path, sampling_key: str) -> dict[str, Any]:
    sampling_key = _observation_sampling_key(sampling_key)
    directory = _observation_directory(codex_home, create=False)
    path = directory / f"{sampling_key}.json"
    if not os.path.lexists(path):
        return _empty_observation_state(sampling_key)
    return _validate_observation_state(
        _load_json(path, "observation state", MAX_OBSERVATION_STATE_BYTES), sampling_key
    )


@contextmanager
def _observation_write_lock(codex_home: Path, sampling_key: str | None = None) -> Iterator[None]:
    """Serialize writes with an OS lock that is released if the process exits."""

    directory = _observation_directory(codex_home, create=True)
    lock_name = (
        f".{_observation_sampling_key(sampling_key)}.lock"
        if sampling_key is not None
        else OBSERVATION_LOCK_NAME
    )
    lock_path = directory / lock_name
    if os.path.lexists(lock_path) and _is_link_or_reparse(lock_path):
        raise RoutingError("observation lock is unsafe")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise RoutingError("cannot lock observation state") from exc
    try:
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise RoutingError("observation lock is not a regular file")
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RoutingError("observation state is busy or unavailable") from exc
        yield
    finally:
        # Keep the inode fixed: unlinking allows two writers to lock different
        # files. Closing (including process termination) releases the OS lock.
        os.close(descriptor)


def _write_observation_state(codex_home: Path, sampling_key: str, state: dict[str, Any]) -> None:
    sampling_key = _observation_sampling_key(sampling_key)
    _validate_observation_state(state, sampling_key)
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if len(encoded.encode("utf-8")) > MAX_OBSERVATION_STATE_BYTES:
        raise RoutingError("observation state is too large")
    path = _observation_state_path(codex_home, sampling_key, create=True)
    _write_atomic(path.parent, path.name, encoded)


def _find_observation_task(state: dict[str, Any], sampling_key: str, task_id: str) -> dict[str, Any] | None:
    sampling_key = _observation_sampling_key(sampling_key)
    return state["samples"][sampling_key]["tasks"].get(task_id)


def observation_status(
    *, codex_home: Path, policy: Policy | None = None, sampling_key: str | None = None
) -> dict[str, Any]:
    """Read one sampling group without reading or writing any other group."""

    if sampling_key is None:
        if policy is None:
            raise RoutingError("observation status needs a policy or sampling key")
        sampling_key = observation_sampling_key(policy)
    else:
        sampling_key = _observation_sampling_key(sampling_key)
    try:
        state = _read_observation_state(codex_home, sampling_key)
        sample = state["samples"][sampling_key]
        tasks = list(sample["tasks"].values())
    except (RoutingError, OSError):
        return {
            "state": "unknown",
            "remaining": None,
            "recorded": None,
            "pending": None,
            "completed": None,
            "missing": None,
        }
    pending = sum(record["state"] == "ongoing" for record in tasks)
    completed = sum(record["state"] == "completed" for record in tasks)
    missing = sum(
        record["state"] != "ongoing" and record["measurement"]["state"] != "measured"
        for record in tasks
    )
    return {
        "state": "valid",
        "sampling_key": sampling_key,
        "remaining": 3 - len(tasks),
        "recorded": len(tasks),
        "pending": pending,
        "completed": completed,
        "missing": missing,
        "task_ids": [record["task_id"] for record in tasks],
    }


def observation_start(*, codex_home: Path, policy: Policy, task_id: str, kind: str = "normal") -> dict[str, Any]:
    """Reserve one normal-work observation, idempotently by the parent task key."""

    task_id = _observation_task_id(task_id)
    if kind != "normal":
        if kind not in {"audit", "config"}:
            raise RoutingError("observation kind is invalid")
        return {"state": "skipped", "reason": "non_normal_work", "recorded": False}
    sampling_key = observation_sampling_key(policy)
    with _observation_write_lock(codex_home, sampling_key):
        state = _read_observation_state(codex_home, sampling_key)
        existing = _find_observation_task(state, sampling_key, task_id)
        now = _utc_now()
        if existing is not None:
            existing["updated_at"] = now
            _write_observation_state(codex_home, sampling_key, state)
            return {"state": "existing", "recorded": True, "sampling_key": sampling_key}
        sample = state["samples"][sampling_key]
        if len(sample["tasks"]) >= 3:
            return {"state": "full", "recorded": False, "sampling_key": sampling_key}
        sample["tasks"][task_id] = {
            "task_id": task_id,
            "sampling_key": sampling_key,
            "policy": _observation_policy_metadata(policy, sampling_key),
            "state": "ongoing",
            "measurement": {"state": "missing", "reason": "completion is not recorded"},
            "started_at": now,
            "updated_at": now,
            "completed_at": None,
            "scope": None,
            "rework": None,
            "verification": None,
            "runs": [],
        }
        _write_observation_state(codex_home, sampling_key, state)
    return {"state": "started", "recorded": True, "sampling_key": sampling_key}


def observation_complete(
    *, codex_home: Path, sampling_key: str, task_id: str, completion: dict[str, Any]
) -> dict[str, Any]:
    """Close one reserved group without loading current policy or other groups."""

    task_id = _observation_task_id(task_id)
    sampling_key = _observation_sampling_key(sampling_key)
    _validate_observation_completion(completion)
    with _observation_write_lock(codex_home, sampling_key):
        state = _read_observation_state(codex_home, sampling_key)
        record = _find_observation_task(state, sampling_key, task_id)
        if record is None:
            raise RoutingError("observation task is not reserved")
        if record["verification"] is not None and record["verification"]["reference"] is not None and completion["verification"]["reference"] is None:
            raise RoutingError("completion must retain existing verification evidence")
        # Each completion is a full snapshot.  A resumed task must not erase
        # earlier runs or replace acquired evidence with unknown values.
        incoming_runs = {run["run_key"]: run for run in completion["runs"]}
        for previous in record["runs"]:
            current = incoming_runs.get(previous["run_key"])
            if current is None:
                raise RoutingError("completion must retain existing runs")
            if current["relationship"] != previous["relationship"] or (
                previous["run_id"] is not None and current["run_id"] != previous["run_id"]
            ):
                raise RoutingError("a different run needs a new run key")
            if previous["state"] == "completed" and current["state"] != "completed":
                raise RoutingError("completion must retain completed run evidence")
            for section, names in (
                ("usage", ("input_tokens", "cache_input_tokens", "output_tokens", "reasoning_tokens")),
                ("observed", ("model", "effort")),
                ("requested", ("model", "effort")),
            ):
                if any(previous[section][name] is not None and current[section][name] is None for name in names):
                    raise RoutingError("completion must retain acquired run values")
        now = _utc_now()
        record.update(
            {
                "state": completion["state"],
                "scope": completion["scope"],
                "measurement": completion["measurement"],
                "verification": completion["verification"],
                "rework": completion["rework"],
                "runs": completion["runs"],
                "completed_at": now,
                "updated_at": now,
            }
        )
        _write_observation_state(codex_home, sampling_key, state)
    return {
        "state": "closed",
        "observation_state": completion["state"],
        "measurement_state": completion["measurement"]["state"],
        "sampling_key": sampling_key,
    }


def status_payload(
    *,
    plugin_root: Path = PLUGIN_ROOT,
    codex_home: Path | None = None,
    config_path: Path | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Return only inspectable state; runtime trust/model status stays unknown."""

    home = _absolute(codex_home) if codex_home is not None else _default_codex_home()
    try:
        policy = load_policy(
            plugin_root=plugin_root, codex_home=home, config_path=config_path
        )
        # Status must not claim a policy is usable until every bundled template
        # has resolved its recursive config tokens.
        policy.rendered_documents()
        cache_state, cache_path = _cache_status(home, policy)
        result = {
            "ok": cache_state in {"missing", "valid"},
            "manifest_version": policy.version,
            "policy_revision": policy.config["policy_revision"],
            "config_hash": policy.config_hash,
            "policy_hash": policy.content_hash,
            "cache": {"path": str(cache_path), "state": cache_state},
            "override": {
                "present": policy.override_present,
                "applied_keys": list(policy.applied_override_keys),
            },
            "python": {"version": sys.version.split()[0], "executable": sys.executable},
            "bundled_hook_available": _hook_available(policy.plugin_root),
            "host": {
                "trust_state": "unknown",
                "runtime_model": "unknown",
                "runtime_effort": "unknown",
            },
            "routing_guidance_conflicts": detect_routing_conflicts(cwd, codex_home=home),
        }
        if not result["ok"]:
            result["error"] = "policy cache is unsafe or does not match the effective policy"
        return result
    except (RoutingError, OSError):
        return {
            "ok": False,
            "error": "policy configuration or templates are invalid",
            "host": {
                "trust_state": "unknown",
                "runtime_model": "unknown",
                "runtime_effort": "unknown",
            },
        }


def _read_hook_event() -> tuple[str, str, Path | None]:
    try:
        raw = sys.stdin.buffer.read(MAX_HOOK_INPUT_BYTES + 1)
    except OSError as exc:
        raise RoutingError("hook input is unavailable") from exc
    if len(raw) > MAX_HOOK_INPUT_BYTES:
        raise RoutingError("hook input is too large")
    try:
        payload = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateKeyError, RecursionError) as exc:
        raise RoutingError("hook input is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RoutingError("hook input must be an object")
    event = payload.get("hook_event_name", payload.get("event"))
    source = payload.get("source", payload.get("session_source", "startup"))
    payload_cwd = payload.get("cwd")
    if not isinstance(event, str) or event not in {"SessionStart", "SubagentStart"}:
        raise RoutingError("unsupported hook event")
    if not isinstance(source, str) or not source.strip() or len(source) > 80:
        raise RoutingError("invalid hook source")
    if payload_cwd is None:
        hook_cwd = None
    elif not isinstance(payload_cwd, str) or not payload_cwd or not os.path.isabs(payload_cwd):
        raise RoutingError("invalid hook cwd")
    else:
        hook_cwd = Path(payload_cwd)
    return event, source, hook_cwd


def _unapplied_hook_response() -> dict[str, Any]:
    """Keep hook failures non-blocking and avoid exposing local policy content."""

    return {
        "systemMessage": (
            "Codex Task Routing was not applied because policy validation or safe rendering failed. "
            "Run status --json to inspect the local policy."
        ),
    }


def _chatgpt_context(home: Path, plugin_root: Path) -> str:
    """Expose opt-in instructions only; never start a chat from a hook."""
    config = _assert_safe_ancestors(home / "codex-task-routing" / "chatgpt.json")
    if not os.path.lexists(config):
        return ""
    helper = _assert_safe_ancestors(plugin_root / "scripts" / "chatgpt_route.py")
    spec = importlib.util.spec_from_file_location("chatgpt_route_hook", helper)
    if spec is None or spec.loader is None:
        raise RoutingError("ChatGPT route helper unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        settings = module.load_config(home)
    except ValueError as exc:
        raise RoutingError("ChatGPT route configuration invalid") from exc
    if not settings["enabled"]:
        return ""
    reference = _assert_safe_ancestors(
        plugin_root / "skills" / "task-routing" / "references" / "chatgpt.md"
    )
    _read_limited_utf8(reference, MAX_TEMPLATE_BYTES, "ChatGPT route instructions")
    transport = settings["transport"]
    transport_note = (
        "Start a new Temporary Chat and use supported Browser operations to send one actual request and read its "
        "final answer; never use send_message_to_thread for this Temporary Chat. "
        if transport == "browser-temporary"
        else "This legacy direct-thread transport is retained for existing explicit configurations; it cannot start "
        "or operate a Temporary Chat. "
    )
    legacy_suffix = " (legacy)" if transport == "codex-app-tools" else ""
    return (
        f"\nOpt-in ChatGPT Chat route is enabled (required UI model: 6 Pro; transport: {transport}{legacy_suffix}). "
        "This is a user-enabled alternative to standard Codex children for substantial, independent "
        "research, analysis, design, drafting, review and explicitly authorized implementation/testing/PR tasks. Before assigning a substantial work unit, "
        "evaluate this Chat route first: prefer 6 Pro when approved materials and verified tools suffice and "
        "handoff, waiting, acceptance and rework costs are proportionate. Do not require another plugin mention. "
        "Use Chat as the initial main specialist, not a redundant final review. Include implementation/PR work only with actual tools, scoped write approval and a sufficient test environment. Keep short work or repeated access to Codex-only local resources in Codex. "
        "Parallel delegation requires useful independent parent work. Serial specialist delegation requires explicit host permission and concrete added value; never invent parent work or bypass host rules. Check required information, sources/freshness, "
        "allowed tool capabilities, return format and stop conditions before dispatch. "
        "Read the route instructions before use: "
        f"{reference}. "
        "Before dispatch use chat_plan.py --live with fresh observations; supplied flags are not proof of permission. Confirm the permitted transport separately from quota and opt-in; unknown authorization blocks dispatch. Use normal Chat only; never Work or a model API. Verify the Chat surface and 6 Pro in the UI "
        "before dispatch and verify the model after completion; tool replies do not attest a backend model. "
        + transport_note
        + "Use an isolated conversation per task, request IDs and input hashes. Missing tools, unknown model "
        "or quota failure blocks this route; report it, do not silently substitute a billed API or Work. "
        "Do not route from subagents or start models from hooks. Preserve the parent model and effort."
    )


def hook_payload(
    *,
    event: str,
    source: str,
    plugin_root: Path = PLUGIN_ROOT,
    codex_home: Path | None = None,
    config_path: Path | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Build a hook response without emitting configuration values or input."""

    home = _absolute(codex_home) if codex_home is not None else _default_codex_home()
    try:
        conflicts = detect_routing_conflicts(cwd, codex_home=home)
    except (RoutingError, OSError):
        return _unapplied_hook_response()
    if conflicts:
        files = ", ".join(conflicts)
        return {
            "systemMessage": f"Routing policy not applied; conflicting guidance: {files}",
        }
    try:
        policy = load_policy(
            plugin_root=plugin_root, codex_home=home, config_path=config_path
        )
        cache_dir = _cache_directory(home, policy)
        references = {name: _absolute(cache_dir / name) for name in RENDERED_FILE_NAMES}
        detail_refs = "\n".join(
            f"- {name}: {references[name]}" for name in DETAILED_TEMPLATE_NAMES
        )
        # Observation state is deliberately treated as optional diagnostics.
        # A corrupt or unavailable ledger must never suppress policy delivery.
        observation = observation_status(codex_home=home, policy=policy)
        observation_cli = _absolute(policy.plugin_root / "scripts" / "routing.py")
        if observation["state"] != "valid":
            observation_instruction = (
                "Observation sample state is unknown; do not start observation work and continue the original task."
            )
        elif observation["remaining"] == 0:
            observation_instruction = (
                "Observation capacity is full; do not start a new record. Update an existing task only during its normal continuation, using its saved sampling key."
            )
        else:
            observation_instruction = (
                f"Record only a normal parent task with: python {observation_cli} observation start "
                "--task-id <stable-parent-thread-and-start-turn> --kind normal --json. "
                f"Close it from a checked JSON file with: python {observation_cli} observation complete "
                "--sampling-key <start-result-key> --task-id <same-id> --input <completion.json>."
            )
        if event == "SessionStart":
            # A session receives the compact effective document once.  The
            # detailed rendered policy remains referenced by path.
            effective_text = _read_limited_utf8(
                references["effective.md"], MAX_RENDERED_BYTES, "effective policy"
            ).rstrip()
            context = "\n".join(
                [
                    f"Codex Task Routing manifest {policy.version}; policy hash {policy.content_hash}.",
                    f"Effective policy path: {references['effective.md']}",
                    "Effective policy:",
                    effective_text,
                    "Detailed policy references:",
                    detail_refs,
                    (
                        "Observation sample (normal work only): "
                        f"remaining={observation['remaining']}, pending={observation['pending']}, "
                        f"completed={observation['completed']}, measurement-missing={observation['missing']}."
                        if observation["state"] == "valid"
                        else "Observation sample state is unknown; policy delivery continues without reading its contents."
                    ),
                    observation_instruction,
                ]
            )
        else:
            model_matrix = "; ".join(
                f"{role.title()}={policy.config['models'][role]['id']} "
                f"({policy.config['models'][role]['default_effort']})"
                for role in ("luna", "sol", "astra")
            )
            context = "\n".join(
                [
                    f"Codex Task Routing manifest {policy.version}; policy hash {policy.content_hash}.",
                    f"Configured child-routing reference only; it does not change the parent: {model_matrix}.",
                    "Terra child is outside the standard route. Follow the parent-specified scope and acceptance criteria; return specialist questions to the parent rather than re-delegating, and distinguish unverified work from confirmed results.",
                    f"Effective policy: {references['effective.md']}",
                    "Detailed policy references:",
                    detail_refs,
                    "Do not start a new observation from this child. Return bounded run evidence and missing reasons to the parent task's observation record.",
                ]
            )
        route_diagnostic = None
        route_context = ""
        if event == "SessionStart":
            try:
                route_context = _chatgpt_context(home, plugin_root)
            except (RoutingError, OSError, ImportError, ValueError):
                route_diagnostic = "ChatGPT route not applied: invalid configuration or unavailable helper. Run chatgpt_route.py status."
        context += "\nUse this single effective policy set. Standard child agents only; do not change the parent model or effort."
        if route_context:
            context += "\nException explicitly enabled by the user for the root parent:" + route_context
        if len(context) > MAX_ADDITIONAL_CONTEXT_CHARS:
            raise RoutingError("hook context exceeds the configured limit")
    except (RoutingError, OSError):
        return _unapplied_hook_response()
    result = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        }
    }
    if route_diagnostic:
        result["systemMessage"] = route_diagnostic
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve local Codex task-routing policy")
    parser.add_argument("--codex-home", type=Path, help="Codex home; defaults to CODEX_HOME or ~/.codex")
    parser.add_argument("--config", type=Path, help="Optional override configuration path")
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status", help="Inspect policy state")
    status.add_argument("--json", action="store_true", required=True)
    render = subparsers.add_parser("render", help="Render an effective policy directory")
    render.add_argument("--output-dir", type=Path, required=True)
    subparsers.add_parser("hook", help="Read a SessionStart/SubagentStart JSON payload from stdin")
    observation = subparsers.add_parser("observation", help="Record up to three normal-work observations per rendered policy")
    observation_commands = observation.add_subparsers(dest="observation_command", required=True)
    observation_start_parser = observation_commands.add_parser("start", help="Reserve one normal-work observation")
    observation_start_parser.add_argument("--task-id", required=True)
    observation_start_parser.add_argument("--kind", choices=("normal", "audit", "config"), default="normal")
    observation_start_parser.add_argument("--json", action="store_true")
    observation_complete_parser = observation_commands.add_parser("complete", help="Close an existing observation from JSON")
    observation_complete_parser.add_argument("--sampling-key", required=True)
    observation_complete_parser.add_argument("--task-id", required=True)
    observation_complete_parser.add_argument("--input", type=Path, required=True)
    observation_status_parser = observation_commands.add_parser("status", help="Inspect observation capacity without writing")
    observation_status_parser.add_argument("--json", action="store_true", required=True)
    observation_status_parser.add_argument("--sampling-key", help="Read one explicit policy sampling group")
    observation_status_parser.add_argument("--task-id", help="Read exactly one existing record for a continuation")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    home = _absolute(args.codex_home) if args.codex_home is not None else _default_codex_home()
    config = _absolute(args.config) if args.config is not None else None
    if args.command == "status":
        result = status_payload(codex_home=home, config_path=config)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0 if result["ok"] else 2
    if args.command == "render":
        try:
            policy = load_policy(codex_home=home, config_path=config)
            references = render_policy(policy, _absolute(args.output_dir))
        except (RoutingError, OSError):
            print("error: policy validation or safe rendering failed", file=sys.stderr)
            return 2
        print(f"Rendered policy {policy.content_hash} to {references['effective.md'].parent}")
        return 0
    if args.command == "hook":
        try:
            event, source, hook_cwd = _read_hook_event()
            result = hook_payload(
                event=event,
                source=source,
                codex_home=home,
                config_path=config,
                cwd=hook_cwd,
            )
        except (RoutingError, OSError):
            result = {
                "systemMessage": "Codex Task Routing was not applied: hook input is invalid.",
            }
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    if args.command == "observation":
        try:
            if args.observation_command == "start":
                policy = load_policy(codex_home=home, config_path=config)
                result = observation_start(
                    codex_home=home,
                    policy=policy,
                    task_id=args.task_id,
                    kind=args.kind,
                )
            elif args.observation_command == "complete":
                completion = _load_json(
                    _absolute(args.input),
                    "observation completion input",
                    MAX_OBSERVATION_INPUT_BYTES,
                )
                result = observation_complete(
                    codex_home=home,
                    sampling_key=args.sampling_key,
                    task_id=args.task_id,
                    completion=completion,
                )
            elif args.observation_command == "status":
                if args.task_id is not None:
                    if args.sampling_key is None:
                        raise RoutingError("observation task status needs a sampling key")
                    sampling_key = _observation_sampling_key(args.sampling_key)
                    existing = _find_observation_task(
                        _read_observation_state(home, sampling_key),
                        sampling_key,
                        _observation_task_id(args.task_id),
                    )
                    if existing is None:
                        raise RoutingError("observation task is not reserved")
                    result = {"state": "valid", "sampling_key": sampling_key, "record": existing}
                elif args.sampling_key is not None:
                    result = observation_status(
                        codex_home=home,
                        sampling_key=args.sampling_key,
                    )
                else:
                    policy = load_policy(codex_home=home, config_path=config)
                    result = observation_status(codex_home=home, policy=policy)
            else:
                raise AssertionError("unreachable observation command")
        except RoutingError as exc:
            # RoutingError messages contain categories, never supplied values.
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except OSError:
            print("error: observation files are unavailable", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0 if result.get("state") != "unknown" else 2
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
