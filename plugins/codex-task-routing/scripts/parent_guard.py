#!/usr/bin/env python3
"""Best-effort, non-blocking reminder for sustained root work without delegation.

Only fixed metadata and SHA-256 identifiers are persisted; prompt, tool arguments,
model names, paths, and transcripts are never stored or inspected.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import sys
import time
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent)
if SCRIPT not in sys.path:
    sys.path.insert(0, SCRIPT)
from routing_core.common import RoutingError, _absolute, _assert_safe_ancestors, _is_link_or_reparse, _safe_mkdir, _reject_duplicate_pairs

LIMIT = 512 * 1024
THRESHOLD = 8
TTL_SECONDS = 7 * 86400
MAX_ROWS = 512
WORK_TOOLS = frozenset({'Bash', 'exec', 'exec_command', 'apply_patch', 'write_stdin'})
OWNER_TOOLS = frozenset({'send_input', 'followup_task', 'wait_agent'})
CONTACT_TOOLS = frozenset({'send_input', 'followup_task', 'send_message'})
RESUME_REMINDER = (
    'Task Routing: a delegated owner stopped. If their unit still needs ordinary verification, '
    'fixes or retesting, make only the necessary parent decision on permissions or environment, '
    'then consider returning the remaining work to the same suitable, resumable owner. If that is '
    'not possible, state the concrete reason. The stop does not establish unfinished work. Parent '
    'acceptance, useful independent work, parent-only operations, active children, host restrictions '
    'and approvals remain valid; do not force delegation or automate execution.'
)
REMINDER = (
    'Task Routing: sustained local work without an observed delegate. Consider handing a complete '
    'unit to an existing delegated owner or an allowed suitable child. Parent familiarity alone '
    'is not a reason to retain future work: give a concise handoff and consider separation of '
    'future editing, trials and tests. Short work, genuine parent-only operations, parent acceptance, '
    'an existing delegated owner, user prohibition and host restrictions can justify continuing; '
    'this is not a requirement to delegate.'
)
PROMPT_REMINDER = (
    'Task Routing: for substantial work, choose an owner before substantial investigation or '
    'implementation. Use acceptance criteria and the steps needed to finish, not the request label, '
    'to choose the overall owner under the effective policy. Keep testing, failure diagnosis, ordinary '
    'fixes and retesting together with one suitable execution owner; consider separate pure evidence '
    'collection only when separable and delegation conditions allow. A creative unit can include tightly coupled edits, materials and checks; '
    'internal coupling and parent familiarity alone do not require parent ownership. Consider '
    'separating future edits, trials and tests in a child context, with a concise handoff of scope, '
    'materials, decisions, constraints, acceptance and authority. Check actual access, a useful '
    'independent parent task for parallel work or host permission and added value for serial work. '
    'Use the allowed delegation tool when suitable; if the parent continues, give a concrete reason. '
    'After necessary parent permission or environment decisions, return remaining ordinary checks, '
    'fixes and retests to the same suitable resumable owner where possible; otherwise state why. '
    'Continue a suitable delegated owner, answer short questions normally, and respect the effective '
    'custom policy, user prohibitions and host restrictions. Do not invent work or force delegation.'
)


def _identifier(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        return None
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _database(home):
    directory = _safe_mkdir(home / 'codex-task-routing' / 'parent-guard')
    path = _assert_safe_ancestors(directory / 'state.sqlite3')
    for candidate in (path, Path(str(path) + '-journal'), Path(str(path) + '-wal'), Path(str(path) + '-shm')):
        if os.path.lexists(candidate):
            if _is_link_or_reparse(candidate) or not stat.S_ISREG(os.lstat(candidate).st_mode):
                raise OSError('unsafe state')
    db = sqlite3.connect(str(path), timeout=0.1, isolation_level=None)
    db.execute('PRAGMA busy_timeout=100')
    db.execute('CREATE TABLE IF NOT EXISTS turns (session TEXT NOT NULL, turn TEXT NOT NULL, created INTEGER NOT NULL, delegated INTEGER NOT NULL DEFAULT 0, owner_contact INTEGER NOT NULL DEFAULT 0, reminded INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(session,turn))')
    db.execute('CREATE TABLE IF NOT EXISTS tools (session TEXT NOT NULL, turn TEXT NOT NULL, tool TEXT NOT NULL, PRIMARY KEY(session,turn,tool))')
    db.execute('CREATE TABLE IF NOT EXISTS active (session TEXT NOT NULL, agent TEXT NOT NULL, created INTEGER NOT NULL, PRIMARY KEY(session,agent))')
    db.execute('CREATE TABLE IF NOT EXISTS stopped (session TEXT PRIMARY KEY, created INTEGER NOT NULL)')
    return db


def handle(payload, home):
    if not isinstance(payload, dict):
        return None
    event = payload.get('hook_event_name')
    if event not in {'UserPromptSubmit', 'PreToolUse', 'SubagentStart', 'SubagentStop'}:
        return None
    session, turn = _identifier(payload.get('session_id')), _identifier(payload.get('turn_id'))
    if session is None or turn is None:
        return None
    agent = payload.get('agent_id')
    # Lifecycle agent_id names the child, whereas tool/prompt agent_id identifies caller.
    if event in {'UserPromptSubmit', 'PreToolUse'} and agent not in (None, ''):
        return None
    if event in {'SubagentStart', 'SubagentStop'} and _identifier(agent) is None:
        return None
    name = payload.get('tool_name')
    if event == 'PreToolUse':
        if not isinstance(name, str):
            return None
        name = name.rsplit('.', 1)[-1].rsplit('__', 1)[-1]
        tool = _identifier(payload.get('tool_use_id'))
        if tool is None or name not in WORK_TOOLS | OWNER_TOOLS | CONTACT_TOOLS | {'spawn_agent', 'Agent'}:
            return None
    db = _database(home)
    try:
        db.execute('BEGIN IMMEDIATE')
        now = int(time.time())
        if event == 'UserPromptSubmit':
            db.execute('DELETE FROM turns WHERE created < ?', (now - TTL_SECONDS,))
            db.execute('DELETE FROM active WHERE created < ?', (now - TTL_SECONDS,))
            db.execute('DELETE FROM stopped WHERE created < ?', (now - TTL_SECONDS,))
            db.execute('DELETE FROM stopped WHERE rowid IN (SELECT rowid FROM stopped ORDER BY created DESC, rowid DESC LIMIT -1 OFFSET ?)', (MAX_ROWS,))
            db.execute('DELETE FROM active WHERE rowid IN (SELECT rowid FROM active ORDER BY created DESC, rowid DESC LIMIT -1 OFFSET ?)', (MAX_ROWS,))
            db.execute('DELETE FROM tools WHERE NOT EXISTS (SELECT 1 FROM turns WHERE turns.session=tools.session AND turns.turn=tools.turn)')
            # Duplicate prompt deliveries must not reset counts or a reminder.
            created = db.execute('INSERT OR IGNORE INTO turns(session,turn,created) VALUES(?,?,?)', (session, turn, now)).rowcount == 1
            db.execute('DELETE FROM turns WHERE rowid IN (SELECT rowid FROM turns ORDER BY created DESC, rowid DESC LIMIT -1 OFFSET ?)', (MAX_ROWS,))
            if created:
                db.execute('COMMIT')
                return {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': PROMPT_REMINDER}}
        elif event == 'SubagentStart':
            db.execute('INSERT OR REPLACE INTO active VALUES(?,?,?)', (session, _identifier(agent), now))
            db.execute('UPDATE turns SET delegated=1 WHERE session=? AND turn=?', (session, turn))
        elif event == 'SubagentStop':
            removed = db.execute('DELETE FROM active WHERE session=? AND agent=?', (session, _identifier(agent))).rowcount
            if removed:
                db.execute('INSERT OR REPLACE INTO stopped VALUES(?,?)', (session, now))
        else:
            row = db.execute('SELECT delegated, owner_contact, reminded FROM turns WHERE session=? AND turn=?', (session, turn)).fetchone()
            if row is not None:
                if name in CONTACT_TOOLS:
                    db.execute('DELETE FROM stopped WHERE session=?', (session,))
                    db.execute('UPDATE turns SET owner_contact=1 WHERE session=? AND turn=?', (session, turn))
                elif name in WORK_TOOLS and db.execute('SELECT 1 FROM stopped WHERE session=?', (session,)).fetchone() and not db.execute('SELECT 1 FROM active WHERE session=? LIMIT 1', (session,)).fetchone():
                    db.execute('DELETE FROM stopped WHERE session=?', (session,))
                    db.execute('COMMIT')
                    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'additionalContext': RESUME_REMINDER}}
                elif name in {'spawn_agent', 'Agent'}:
                    pass  # an attempted spawn is not a started child
                elif name in OWNER_TOOLS:
                    db.execute('UPDATE turns SET owner_contact=1 WHERE session=? AND turn=?', (session, turn))
                elif not row[0] and not row[1] and not row[2]:
                    active = db.execute('SELECT 1 FROM active WHERE session=? LIMIT 1', (session,)).fetchone()
                    if active is not None:
                        db.execute('COMMIT')
                        return None
                    db.execute('INSERT OR IGNORE INTO tools VALUES(?,?,?)', (session, turn, tool))
                    count = db.execute('SELECT COUNT(*) FROM tools WHERE session=? AND turn=?', (session, turn)).fetchone()[0]
                    if count >= THRESHOLD:
                        db.execute('UPDATE turns SET reminded=1 WHERE session=? AND turn=?', (session, turn))
                        db.execute('COMMIT')
                        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'additionalContext': REMINDER}}
        db.execute('COMMIT')
    finally:
        db.close()
    return None


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'status':
        home = _absolute(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
        path = home / 'codex-task-routing' / 'parent-guard' / 'state.sqlite3'
        if not os.path.lexists(path):
            print(json.dumps({'state': 'unobserved', 'note': 'No persisted hook state; trust and live delivery unknown.'}))
            return 0
        try:
            _assert_safe_ancestors(path)
            if _is_link_or_reparse(path) or not stat.S_ISREG(os.lstat(path).st_mode):
                raise OSError('unsafe state')
            db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0.1)
            try:
                turns = db.execute('SELECT COUNT(*) FROM turns').fetchone()[0]
                reminded = db.execute('SELECT COUNT(*) FROM turns WHERE reminded=1').fetchone()[0]
            finally:
                db.close()
            print(json.dumps({'state': 'recorded' if turns else 'unobserved', 'retained_turns': turns, 'reminders': reminded,
                              'note': 'Local state only; trust, complete event coverage and delegation success unknown.'}))
            return 0
        except (OSError, sqlite3.Error, RoutingError, ValueError):
            print(json.dumps({'state': 'unavailable', 'note': 'State cannot be inspected; hook coverage unknown.'}))
            return 2
    try:
        raw = sys.stdin.buffer.read(LIMIT + 1)
        while sys.stdin.buffer.read(64 * 1024):
            pass
        if len(raw) > LIMIT:
            return 0
        payload = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_reject_duplicate_pairs)
        home = _absolute(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
        result = handle(payload, home)
        if result:
            print(json.dumps(result))
    except (ValueError, UnicodeError, OSError, sqlite3.Error, TypeError, RoutingError, RecursionError):
        # Never spam or block the tool; status reports unavailable state.
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
