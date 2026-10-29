"""Read one explicitly enrolled thread's aggregate usage metadata, without content."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from routing_core.common import RoutingError, _absolute, _assert_safe_ancestors
from routing_core.usage_metadata import _uuid, _enrollment
from routing_core.observation import _read_observation_state, _observation_sampling_key, _campaign_id, _observation_task_id


def read_snapshot(db_path: Path, enrollment: Any, thread_id: str) -> dict[str, Any]:
    thread_id = _uuid(thread_id)
    target = _enrollment(enrollment, thread_id)  # Check enrollment before opening the database.
    path = _assert_safe_ancestors(_absolute(db_path))
    if path.name != 'state_5.sqlite' or not path.is_file():
        raise RoutingError('usage database is unavailable')
    file_stat = path.stat()
    if not file_stat.st_ino:
        raise RoutingError("usage database identity is unavailable")
    file_id = hashlib.sha256(f'{file_stat.st_dev}:{file_stat.st_ino}'.encode('ascii')).hexdigest()
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            db.execute('PRAGMA query_only = ON')
            schema_version = db.execute('PRAGMA schema_version').fetchone()[0]
            user_version = db.execute('PRAGMA user_version').fetchone()[0]
            if (schema_version, user_version) != (123, 0):
                raise RoutingError('usage database schema version is unsupported')
            columns = {row[1]: str(row[2]).upper() for row in db.execute('PRAGMA table_info(threads)')}
            if any(columns.get(name) != expected for name, expected in
                   (('id', 'TEXT'), ('source', 'TEXT'), ('tokens_used', 'INTEGER'),
                    ('model', 'TEXT'), ('reasoning_effort', 'TEXT'), ('cli_version', 'TEXT'))):
                raise RoutingError('usage database schema is unsupported')
            rows = db.execute('SELECT id, source, tokens_used, model, reasoning_effort, cli_version FROM threads WHERE id = ?', (thread_id,)).fetchmany(2)
    except sqlite3.Error as exc:
        raise RoutingError('usage database is unavailable') from exc
    final_stat = path.stat()
    if (file_stat.st_dev, file_stat.st_ino) != (final_stat.st_dev, final_stat.st_ino):
        raise RoutingError('usage database identity changed')
    if len(rows) != 1 or rows[0][0] != thread_id:
        raise RoutingError('enrolled thread is unavailable')
    _, source, total, model, effort, cli_version = rows[0]
    if cli_version != "0.165.0-alpha.1.2":
        raise RoutingError("thread runtime version is unsupported")
    if model is not None and (not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', model)):
        raise RoutingError('thread model setting is invalid')
    if effort is not None and effort not in {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}:
        raise RoutingError('thread effort setting is invalid')
    if type(total) is not int or total < 0:
        raise RoutingError('aggregate usage is unavailable')
    if target['depth'] == 0:
        if source != 'vscode':
            raise RoutingError('root source is unsupported')
    else:
        try:
            parsed = json.loads(source)
            spawn = parsed['subagent']['thread_spawn']
            if (not isinstance(spawn, dict) or spawn['parent_thread_id'] != target['parent_id']
                    or type(spawn['depth']) is not int or spawn['depth'] != target['depth']):
                raise RoutingError('thread ancestry is inconsistent')
        except (TypeError, ValueError, KeyError) as exc:
            raise RoutingError('thread source is unsupported') from exc
    return {'state': 'measured', 'basis': 'aggregate_total_tokens', 'thread_id': thread_id,
            'parent_id': target['parent_id'], 'depth': target['depth'], 'total_tokens': total,
            'configured_model': model, 'configured_effort': effort, 'runtime_model_verified': False,
            'db_file_id': file_id, 'db_schema_version': schema_version, 'db_user_version': user_version,
            'cli_version': cli_version,
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'scope': 'instantaneous_snapshot; interval completeness not established'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--codex-home', required=True, type=Path)
    parser.add_argument('--sampling-key', required=True)
    parser.add_argument('--campaign-id', required=True)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--thread-id', required=True)
    args = parser.parse_args()
    try:
        key = _observation_sampling_key(args.sampling_key)
        campaign_id = _campaign_id(args.campaign_id)
        task_id = _observation_task_id(args.task_id)
        state = _read_observation_state(args.codex_home, key)
        sample = state['samples'][key]
        if state['schema_version'] != 2 or campaign_id not in sample['campaigns']:
            raise RoutingError('campaign is unavailable')
        campaign = sample['campaigns'][campaign_id]
        if task_id not in campaign['tasks'] or task_id not in campaign['enrollments']:
            raise RoutingError('observation task is not enrolled')
        result = read_snapshot(args.db, campaign['enrollments'][task_id], args.thread_id)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (RoutingError, OSError, ValueError):
        print('error: usage metadata is unavailable or invalid', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
