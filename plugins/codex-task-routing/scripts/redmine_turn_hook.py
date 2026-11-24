"""Bridge root lifecycle metadata to the configured local Agent Toolkit."""

import json
import os
from pathlib import Path
import subprocess
import sys


# The shipped hook uses runpy, so resolve the sibling module explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from redmine_task_cli import executable, toolkit_script, valid_script


MAX_INPUT = 1024 * 1024


def prompt_context(context):
    return {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': context}}


FAILURE = prompt_context(
    'Redmine turn sync is unavailable. For continuing work that needs Redmine, verify the local Agent Toolkit '
    'and state directory, and report the unresolved blocker once if it has not been reported for this task. '
    'For a standalone request, continue normally.'
)


def run(payload, script):
    if not isinstance(payload, dict) or payload.get('hook_event_name') != 'UserPromptSubmit':
        return {}
    if payload.get('agent_id') not in (None, ''):
        return {}
    if not payload.get('session_id') or not payload.get('turn_id'):
        return {}
    if not valid_script(script):
        return FAILURE
    fields = ('hook_event_name', 'session_id', 'turn_id')
    metadata = {key: payload[key] for key in fields}
    node = executable('node')
    if node is None:
        return FAILURE
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith('REDMINE_') or key.upper() == 'REDMINE_TASK_MEMORY_STATE_DIR'}
    result = subprocess.run([str(node), str(script), 'turn-hook', 'codex'], input=json.dumps(metadata).encode(),
                            capture_output=True, env=env, timeout=5, check=False)
    if result.returncode != 0 or len(result.stdout) > 8192:
        return FAILURE
    value = json.loads(result.stdout)
    if value == {}:
        return {}
    output = value.get('hookSpecificOutput') if isinstance(value, dict) else None
    if isinstance(output, dict) and output.get('hookEventName') == 'UserPromptSubmit':
        context = output.get('additionalContext')
        if isinstance(context, str) and context.strip():
            return prompt_context(
                'For Redmine CLI operations, run python <plugin-root>/scripts/redmine_task_cli.py from '
                'the task worktree in place of calling node directly. Pass the same operation, reference or '
                'handle, and JSON stdin described below. This uses the configured project or approved '
                'repository mapping.\n' + context
            )
    return FAILURE


def main():
    script = toolkit_script()
    if script is None:
        return
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        payload = json.loads(raw) if len(raw) <= MAX_INPUT else None
    except (OSError, UnicodeError, ValueError):
        return
    try:
        value = run(payload, script)
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError):
        value = FAILURE
    if value:
        print(json.dumps(value))


if __name__ == '__main__':
    main()
