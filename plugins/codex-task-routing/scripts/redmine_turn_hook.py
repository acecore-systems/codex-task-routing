"""Bridge root lifecycle metadata to the configured local Agent Toolkit."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys


MAX_INPUT = 1024 * 1024
FAILURE = {'systemMessage': 'Redmine turn check is unavailable. Verify the local Agent Toolkit and state directory.'}


def run(payload, script):
    if not isinstance(payload, dict) or payload.get('hook_event_name') not in ('UserPromptSubmit', 'Stop'):
        return {}
    if payload.get('agent_id') not in (None, ''):
        return {}
    if not payload.get('session_id') or not payload.get('turn_id'):
        return {}
    if not script.is_absolute() or script.name != 'redmine-task-memory.ts' or not script.is_file():
        return FAILURE
    fields = ('hook_event_name', 'session_id', 'turn_id', 'stop_hook_active')
    metadata = {key: payload[key] for key in fields if key in payload}
    prompt = payload.get('prompt')
    marker = re.match(r'^Redmine task-memory check \(([a-f0-9]{32})\)\. ', prompt) if isinstance(prompt, str) else None
    if marker:
        metadata['continuation_token'] = marker[1]
    name = 'node.exe' if os.name == 'nt' else 'node'
    node = next((directory / name for directory in map(Path, os.get_exec_path())
                 if directory.is_absolute() and (directory / name).is_file()), None)
    if node is None:
        return FAILURE
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith('REDMINE_') or key.upper() == 'REDMINE_TASK_MEMORY_STATE_DIR'}
    result = subprocess.run([str(node), str(script), 'turn-hook', 'codex'], input=json.dumps(metadata).encode(),
                            capture_output=True, env=env, timeout=5, check=False)
    if result.returncode != 0 or len(result.stdout) > 8192:
        return FAILURE
    value = json.loads(result.stdout)
    return value if isinstance(value, dict) else FAILURE


def main():
    script = os.environ.get('REDMINE_TASK_MEMORY_CLI')
    if not script:
        root = os.environ.get('AGENT_TOOLKIT_ROOT')
        if not root:
            return
        script = Path(root) / 'scripts' / 'redmine-task-memory.ts'
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        value = run(json.loads(raw), Path(script)) if len(raw) <= MAX_INPUT else FAILURE
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError):
        value = FAILURE
    if value:
        print(json.dumps(value))


if __name__ == '__main__':
    main()
