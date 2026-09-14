#!/usr/bin/env python3
"""Resolve and expose one local Codex task-routing policy.

The module intentionally has no network, subprocess, or Codex-runtime API
dependency.  It only reads the bundled defaults and the opt-in override file.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_RELATIVE = Path("defaults") / "config.json"
TEMPLATE_RELATIVE = Path("defaults") / "templates"
OVERRIDE_RELATIVE = Path("codex-task-routing") / "overrides.json"
CACHE_RELATIVE = Path("codex-task-routing") / "cache"
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
MAX_TOKEN_DEPTH = 20
# hooks.json uses a token-oriented platform limit.  Keep this independent,
# conservative character ceiling so the runtime never asks the host to truncate
# an effective policy mid-document.
MAX_ADDITIONAL_CONTEXT_CHARS = 8000

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


def _cache_directory(codex_home: Path, policy: Policy) -> Path:
    """Create a fresh content-addressed cache directory without touching an old one."""

    cache_root = _safe_mkdir(_absolute(codex_home) / CACHE_RELATIVE)
    destination = cache_root / policy.content_hash
    if os.path.lexists(destination):
        if _is_link_or_reparse(destination) or not destination.is_dir():
            raise RoutingError("policy cache entry is unsafe")
        if not _cache_matches_policy(destination, policy):
            raise RoutingError("policy cache entry does not match the effective policy")
        return destination
    staging = cache_root / f".routing-staging-{policy.content_hash}-{secrets.token_hex(8)}"
    try:
        # ``exist_ok=False`` makes every write occur in a new directory.
        staging.mkdir()
        render_policy(policy, staging)
        # os.rename does not overwrite an existing destination on Windows.
        os.rename(staging, destination)
    except OSError as exc:
        # Windows uses EEXIST and Linux may use ENOTEMPTY when a concurrent
        # hook has already atomically published this non-empty directory.
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            if (
                os.path.lexists(destination)
                and not _is_link_or_reparse(destination)
                and destination.is_dir()
                and _cache_matches_policy(destination, policy)
            ):
                return destination
            raise RoutingError("policy cache entry appeared during rendering") from exc
        raise RoutingError("cannot create the policy cache entry") from exc
    return destination


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
                ]
            )
        else:
            model_matrix = "; ".join(
                f"{role.title()}={policy.config['models'][role]['id']} "
                f"({policy.config['models'][role]['default_effort']})"
                for role in REQUIRED_MODEL_ROLES
            )
            context = "\n".join(
                [
                    f"Codex Task Routing manifest {policy.version}; policy hash {policy.content_hash}.",
                    f"Configured child-routing reference only; it does not change the parent: {model_matrix}.",
                    "Follow the parent-specified scope and acceptance criteria; re-delegate only under the detailed policy, and distinguish unverified work from confirmed results.",
                    f"Effective policy: {references['effective.md']}",
                    "Detailed policy references:",
                    detail_refs,
                ]
            )
        context += "\nUse this single effective policy set. Standard child agents only; do not change the parent model or effort."
        if len(context) > MAX_ADDITIONAL_CONTEXT_CHARS:
            raise RoutingError("hook context exceeds the configured limit")
    except (RoutingError, OSError):
        return _unapplied_hook_response()
    return {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        }
    }


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
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
