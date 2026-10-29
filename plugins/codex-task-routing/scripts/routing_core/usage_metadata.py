"""Validate explicit thread enrollment without reading the runtime database."""
from __future__ import annotations
from typing import Any
from uuid import UUID
from .common import RoutingError


def _uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise RoutingError('thread identifier is invalid')
    try:
        canonical = str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise RoutingError('thread identifier is invalid') from exc
    if canonical != value:
        raise RoutingError('thread identifier is not canonical')
    return canonical


def _enrollment(value: Any, thread_id: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {'schema_version', 'root_id', 'threads', 'allow_descendants', 'start_evidence_ref'} or not {'schema_version', 'root_id', 'threads'} <= set(value):
        raise RoutingError('enrollment is invalid')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise RoutingError('enrollment version is unsupported')
    if 'allow_descendants' in value and type(value['allow_descendants']) is not bool:
        raise RoutingError('descendant scope is invalid')
    ref = value.get('start_evidence_ref')
    if ref is not None and (not isinstance(ref, str) or not ref.strip() or len(ref) > 4096 or any(ord(c) < 32 for c in ref)):
        raise RoutingError('start evidence is invalid')
    root = _uuid(value['root_id'])
    rows = value['threads']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        raise RoutingError('enrollment threads are invalid')
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'thread_id', 'parent_id', 'depth'}:
            raise RoutingError('enrollment thread is invalid')
        identifier = _uuid(row['thread_id'])
        if identifier in by_id or type(row['depth']) is not int or row['depth'] not in (0, 1, 2):
            raise RoutingError('enrollment thread identity is invalid')
        if row['parent_id'] is not None:
            _uuid(row['parent_id'])
        by_id[identifier] = row
    if root not in by_id or by_id[root]['parent_id'] is not None or by_id[root]['depth'] != 0:
        raise RoutingError('enrollment root is invalid')
    for identifier, row in by_id.items():
        if identifier == root:
            continue
        parent = by_id.get(row['parent_id'])
        if parent is None or row['depth'] != parent['depth'] + 1:
            raise RoutingError('enrollment ancestry is invalid')
    if thread_id not in by_id:
        raise RoutingError('thread is not enrolled')
    return by_id[thread_id]
