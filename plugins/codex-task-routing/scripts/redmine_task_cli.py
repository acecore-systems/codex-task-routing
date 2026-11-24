"""Run the local Agent Toolkit Redmine CLI with its existing project mappings."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit


SCRIPT_NAME = 'redmine-task-memory.ts'
PROJECT = 'REDMINE_TASK_MEMORY_PROJECT'
PROJECTS = 'REDMINE_TASK_MEMORY_PROJECTS'
# Keep one extra byte so the toolkit still rejects oversized input.
MAX_INPUT = 24 * 1024 + 1
REPOSITORY = r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+'
GITHUB_REMOTE = re.compile(
    rf'(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)({REPOSITORY})/?', re.IGNORECASE
)
ISSUE_ID = re.compile(r'#?[1-9][0-9]{0,15}')


def executable(name):
    filename = f'{name}.exe' if os.name == 'nt' else name
    return next((directory / filename for directory in map(Path, os.get_exec_path())
                 if directory.is_absolute() and (directory / filename).is_file()), None)


def toolkit_script():
    configured = os.environ.get('REDMINE_TASK_MEMORY_CLI')
    root = os.environ.get('AGENT_TOOLKIT_ROOT')
    if configured:
        return Path(configured)
    if root:
        return Path(root) / 'scripts' / SCRIPT_NAME
    return None


def valid_script(script):
    return script is not None and script.is_absolute() and script.name == SCRIPT_NAME and script.is_file()


def origin_repository():
    git = executable('git')
    if git is None:
        return None
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(('GIT_', 'REDMINE_'))}
    try:
        result = subprocess.run([str(git), 'config', '--local', '--no-includes', '--null', '--get-all', 'remote.origin.url'],
                                capture_output=True, env=env, timeout=5, check=False)
        if result.returncode != 0:
            return None
        urls = result.stdout.decode('utf-8').removesuffix('\0').split('\0')
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    match = GITHUB_REMOTE.fullmatch(urls[0]) if len(urls) == 1 else None
    if match:
        return match[1].lower().removesuffix('.git')
    return None


def worktree_project():
    repository = origin_repository()
    if not repository:
        return None
    try:
        mappings = json.loads(os.environ[PROJECTS], object_pairs_hook=unique_mapping)
    except (KeyError, ValueError):
        return None
    if isinstance(mappings, dict):
        return mappings.get(repository)
    return None


def unique_mapping(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('duplicate project mapping')
    return result


def direct_redmine_reference(reference):
    if not isinstance(reference, str):
        return False
    if ISSUE_ID.fullmatch(reference):
        return True
    try:
        target = urlsplit(reference)
        server = urlsplit(os.environ.get('REDMINE_URL', ''))
        prefix = server.path.rstrip('/') + '/issues/'
    except ValueError:
        return False
    issue = target.path.removeprefix(prefix).rstrip('/')
    return (target.scheme == server.scheme == 'https' and target.netloc == server.netloc
            and target.path.startswith(prefix) and issue.isascii() and issue.isdecimal())


def may_select_project(operation, reference, data):
    if os.environ.get(PROJECT) or not os.environ.get(PROJECTS):
        return False
    if operation != 'turn':
        return direct_redmine_reference(reference)
    if data is None:
        return False
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get('action') == 'attach':
        return direct_redmine_reference(payload.get('reference'))
    return payload.get('action') in ('resume', 'record', 'recover')


def needs_project(result):
    if result.returncode != 1:
        return False
    try:
        response = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        return False
    return response == {'ok': False, 'status': 'configuration_required'}


def main(args):
    if len(args) != 2 or args[0] not in ('turn', 'resume', 'update'):
        print('Usage: redmine_task_cli.py {turn|resume|update} HANDLE_OR_REFERENCE', file=sys.stderr)
        return 2
    script = toolkit_script()
    node = executable('node')
    if node is None or not valid_script(script):
        print(json.dumps({'ok': False, 'status': 'toolkit_unavailable'}))
        return 1
    operation, reference = args
    data = sys.stdin.buffer.read(MAX_INPUT) if operation != 'resume' and not sys.stdin.isatty() else None
    command = [str(node), str(script), operation, reference]
    if not may_select_project(operation, reference, data):
        return subprocess.run(command, input=data, check=False).returncode
    result = subprocess.run(command, input=data, capture_output=True, check=False)
    # The toolkit returns configuration_required before any request using a project.
    project = worktree_project() if needs_project(result) else None
    if isinstance(project, str) and project:
        return subprocess.run(command, input=data, env={**os.environ, PROJECT: project}, check=False).returncode
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return result.returncode


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
