"""Build a bounded aggregate interval from two supplied metadata snapshots."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from routing_core.common import RoutingError, _absolute, _load_json
from routing_core.usage_metadata import _uuid

FIELDS = {'state', 'basis', 'thread_id', 'parent_id', 'depth', 'total_tokens',
          'configured_model', 'configured_effort', 'runtime_model_verified', 'observed_at', 'scope',
          'db_file_id', 'db_schema_version', 'db_user_version', 'cli_version'}


def interval(before: Any, after: Any, *, evidence_ref: str, start_boundary_ref: str | None = None,
             end_boundary_ref: str | None = None, continuity_ref: str | None = None) -> dict[str, Any]:
    if not isinstance(evidence_ref, str) or not evidence_ref.strip() or len(evidence_ref) > 4096 or any(ord(c) < 32 for c in evidence_ref):
        raise RoutingError('snapshot evidence is required')
    for snapshot in (before, after):
        if not isinstance(snapshot, dict) or set(snapshot) != FIELDS:
            raise RoutingError('snapshot fields are invalid')
        _uuid(snapshot['thread_id'])
        if snapshot['parent_id'] is not None:
            _uuid(snapshot['parent_id'])
        if type(snapshot['depth']) is not int or snapshot['depth'] not in (0, 1, 2):
            raise RoutingError('snapshot depth is invalid')
        if (snapshot['depth'] == 0) != (snapshot['parent_id'] is None):
            raise RoutingError('snapshot parent is invalid')
        if not isinstance(snapshot['db_file_id'], str) or not re.fullmatch(r'[0-9a-f]{64}', snapshot['db_file_id']):
            raise RoutingError('snapshot database identity is invalid')
        if any(type(snapshot[name]) is not int or snapshot[name] < 0 for name in ('db_schema_version', 'db_user_version')):
            raise RoutingError('snapshot database version is invalid')
        if snapshot['cli_version'] != '0.165.0-alpha.1.2':
            raise RoutingError('snapshot runtime version is unsupported')
        if snapshot['state'] != 'measured' or snapshot['basis'] != 'aggregate_total_tokens':
            raise RoutingError('snapshot basis is invalid')
        if snapshot['runtime_model_verified'] is not False:
            raise RoutingError('snapshot model verification is invalid')
        if type(snapshot['total_tokens']) is not int or snapshot['total_tokens'] < 0:
            raise RoutingError('snapshot counter is invalid')
        if snapshot['scope'] != 'instantaneous_snapshot; interval completeness not established':
            raise RoutingError('snapshot scope is invalid')
    for field in ('thread_id', 'parent_id', 'depth', 'configured_model', 'configured_effort',
                  'db_file_id', 'db_schema_version', 'db_user_version', 'cli_version'):
        if before[field] != after[field]:
            raise RoutingError('snapshot identity or settings changed')
    try:
        start_at = datetime.fromisoformat(before['observed_at'].replace('Z', '+00:00'))
        end_at = datetime.fromisoformat(after['observed_at'].replace('Z', '+00:00'))
        if start_at.utcoffset() is None or end_at.utcoffset() is None or end_at < start_at:
            raise RoutingError('snapshot ordering is invalid')
    except (TypeError, ValueError, AttributeError) as exc:
        raise RoutingError('snapshot time is invalid') from exc
    start, end = before['total_tokens'], after['total_tokens']
    if end < start:
        raise RoutingError('snapshot counter decreased')
    refs = (start_boundary_ref, end_boundary_ref, continuity_ref)
    if any(ref is not None and (not isinstance(ref, str) or not ref.strip() or len(ref) > 4096 or any(ord(c) < 32 for c in ref)) for ref in refs):
        raise RoutingError('interval evidence is invalid')
    full = all(ref is not None for ref in refs)
    return {
        'usage': {'basis': 'aggregate_total_tokens', 'state': 'measured', 'reason': None,
                  'evidence_ref': evidence_ref, 'turn_scope': 'full_turn' if full else 'partial_turn',
                  'start_tokens': start, 'end_tokens': end, 'total_tokens': end - start,
                  'start_boundary_ref': start_boundary_ref, 'end_boundary_ref': end_boundary_ref,
                  'continuity_ref': continuity_ref},
        'configured_model': before['configured_model'], 'configured_effort': before['configured_effort'],
        'runtime_model_verified': False,
        'note': 'Full scope depends on the supplied boundary and continuity evidence; not independently verified.',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--evidence-ref', required=True)
    parser.add_argument('--start-boundary-ref')
    parser.add_argument('--end-boundary-ref')
    parser.add_argument('--continuity-ref')
    args = parser.parse_args()
    try:
        before = _load_json(_absolute(args.before), 'start snapshot', 16 * 1024)
        after = _load_json(_absolute(args.after), 'end snapshot', 16 * 1024)
        result = interval(before, after, evidence_ref=args.evidence_ref,
                          start_boundary_ref=args.start_boundary_ref,
                          end_boundary_ref=args.end_boundary_ref, continuity_ref=args.continuity_ref)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (RoutingError, OSError, ValueError):
        print('error: usage snapshots or interval are invalid', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
