#!/usr/bin/env python3
"""Non-blocking PreToolUse reminder before a shell command reads a large file whole.

Only the parent's Bash, exec, and exec_command calls are inspected. The reminder
names the file's base name and its size in KB. It never reads or returns file
contents, keeps no state, and never denies a call. The detection is a heuristic
ported from block-large-read.js (Bash / PowerShell part); the Read tool is not covered.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys

LIMIT_KB = 100
INPUT_LIMIT = 512 * 1024
TARGET_TOOLS = frozenset({'Bash', 'exec', 'exec_command'})
# Commands that print a whole file. Compared lower-case, without a .exe / .ps1 suffix.
WHOLE_READ_COMMANDS = frozenset({'cat', 'type', 'get-content', 'gc', 'more', 'less', 'bat'})
# Options that take a value; the word after them is that value, not a file name.
VALUE_OPTIONS = frozenset({
    'encoding', 'delimiter', 'filter', 'include', 'exclude', 'stream', 'readcount',
    'totalcount', 'tail', 'head', 'credential',
    'erroraction', 'warningaction', 'informationaction',
    'errorvariable', 'warningvariable', 'informationvariable', 'outvariable', 'outbuffer', 'pipelinevariable',
})
OPERATORS = frozenset({'&&', '||', ';', '|'})
SHELL_NAMES = frozenset({'bash', 'sh', 'zsh', 'pwsh', 'powershell', 'cmd'})
SHELL_SCRIPT_OPTIONS = frozenset({'-c', '-lc', '-command', '/c'})
OPTION_PATTERN = re.compile(r'--?([^:=]+)([:=].*)?')
# A range or filter is already given somewhere in the command. The JavaScript \b and \d are
# written as ASCII classes so that the match stays the same as block-large-read.js.
RANGE_LIMIT_PATTERN = re.compile(
    r"(-TotalCount(?![A-Za-z0-9_])|-Tail(?![A-Za-z0-9_])|-First(?![A-Za-z0-9_])|-Head(?![A-Za-z0-9_])"
    r"|-Last(?![A-Za-z0-9_])|(\s-r\s*|--line-range[\s=])[0-9]*:[0-9]*|sed\s+-n|awk\s+['\"]?[^'\"]*NR"
    r"|Select-Object(?![A-Za-z0-9_])|\|\s*(grep|rg|Select-String|findstr|wc|head|tail)(?![A-Za-z0-9_]))",
    re.IGNORECASE,
)
REMINDER = (
    'Task Routing: {name} is {size}KB and this command would load the whole file into the parent context, '
    'which makes every later turn more expensive. Consider reading only a range (head / tail / sed -n, '
    'or Get-Content -TotalCount / -Tail), filtering it (rg / Select-String), or passing the file path to a '
    'suitable delegated owner and receiving a summary. This is non-blocking; a deliberate full read, '
    'user instructions and host restrictions remain valid.'
)


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _base_name(token):
    name = re.split(r'[\\/]', token)[-1].lower()
    return re.sub(r'\.(exe|ps1)\Z', '', name)


def _tokenize(command):
    # Same rules as block-large-read.js: operators are spaced out, and quotes group words.
    spaced = re.sub(r'(&&|\|\||;|\|)', r' \1 ', command)
    tokens = []
    current = []
    quote = None
    for char in spaced:
        if quote:
            if char == quote:
                quote = None
            else:
                current.append(char)
        elif char in ('"', "'"):
            quote = char
        elif char.isspace() or char == '\ufeff':
            if current:
                tokens.append(''.join(current))
                current = []
        else:
            current.append(char)
    if current:
        tokens.append(''.join(current))
    return tokens


def _segments(tokens):
    segments = []
    current = []
    for token in tokens:
        if token in OPERATORS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _shell_script(argv):
    # `bash -lc SCRIPT`, `cmd /c SCRIPT`, `pwsh -NoProfile -Command SCRIPT`, and similar shell launches.
    if len(argv) < 3 or _base_name(argv[0]) not in SHELL_NAMES:
        return None
    index = 1
    while index < len(argv):
        option = argv[index].lower()
        if option in SHELL_SCRIPT_OPTIONS:
            return argv[index + 1] if index + 1 < len(argv) else None
        if not option.startswith(('-', '/')):
            return None
        index += 1
    return None


def _command_units(value):
    # Returns (segments, text), or None for an unsupported shape.
    if isinstance(value, str):
        if not value:
            return None
        return _segments(_tokenize(value)), value
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        script = _shell_script(value)
        if script is not None:
            return _segments(_tokenize(script)), script
        # An argv list is one command. Its words are arguments and are not split again.
        return [value], ' '.join(value)
    return None


def _is_redirect(token):
    return re.match(r'[0-9]?>{1,2}', token) is not None and re.match(r'[0-9]?>&', token) is None


def _candidates(segment):
    # File-like words after the command name (a heuristic, as in block-large-read.js).
    found = []
    index = 1
    while index < len(segment):
        match = OPTION_PATTERN.fullmatch(segment[index])
        if match is None:
            found.append(segment[index])
        elif match.group(2):
            found.append(match.group(2)[1:])
        elif match.group(1).lower() in VALUE_OPTIONS:
            index += 1
        index += 1
    return found


def _resolve(token, cwd):
    path = token
    if path.startswith('~'):
        path = os.path.join(os.path.expanduser('~'), path[1:].lstrip('/\\'))
    # Git Bash style /c/... becomes C:/... on Windows only.
    msys = re.fullmatch(r'/([a-zA-Z])(/.*)?', path)
    if msys and sys.platform == 'win32':
        path = f"{msys.group(1).upper()}:{msys.group(2) or '/'}"
    if not os.path.isabs(path):
        path = os.path.join(cwd or os.getcwd(), path)
    return os.path.abspath(path)


def _first_large_file(candidates, cwd):
    for candidate in candidates:
        path = _resolve(candidate, cwd)
        try:
            info = os.stat(path)
        except (OSError, ValueError):
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        size_kb = (info.st_size + 512) // 1024
        if size_kb >= LIMIT_KB:
            return path, size_kb
    return None


def _checked_segment(segment, cwd):
    if _base_name(segment[0]) not in WHOLE_READ_COMMANDS:
        return None
    # Redirecting to a file keeps the output out of the context.
    if any(_is_redirect(token) for token in segment):
        return None
    return _first_large_file(_candidates(segment), cwd)


def _cwd(payload, tool_input):
    for value in (payload.get('cwd'), tool_input.get('workdir'), tool_input.get('cwd')):
        if isinstance(value, str) and value:
            return value
    return None


def analyze(payload):
    """Return the hook output for a large whole-file read, or None to stay silent."""
    if not isinstance(payload, dict) or payload.get('hook_event_name') != 'PreToolUse':
        return None
    # Only the parent is checked; a child's reads are left alone.
    if payload.get('agent_id') not in (None, ''):
        return None
    tool_name = payload.get('tool_name')
    if not isinstance(tool_name, str):
        return None
    if tool_name.rsplit('.', 1)[-1].rsplit('__', 1)[-1] not in TARGET_TOOLS:
        return None
    tool_input = payload.get('tool_input')
    if not isinstance(tool_input, dict):
        return None
    value = tool_input.get('command')
    if value is None:
        value = tool_input.get('cmd')
    units = _command_units(value)
    if units is None:
        return None
    segments, text = units
    if RANGE_LIMIT_PATTERN.search(text):
        return None
    cwd = _cwd(payload, tool_input)
    for segment in segments:
        found = _checked_segment(segment, cwd)
        if found is not None:
            path, size_kb = found
            message = REMINDER.format(name=os.path.basename(path), size=size_kb)
            return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'additionalContext': message}}
    return None


def main():
    try:
        raw = sys.stdin.buffer.read(INPUT_LIMIT + 1)
        while sys.stdin.buffer.read(64 * 1024):
            pass
        if len(raw) > INPUT_LIMIT:
            return 0
        payload = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_no_duplicate_keys)
        result = analyze(payload)
        if result is not None:
            print(json.dumps(result))
    except Exception:
        # Never block or spam: unreadable, deeply nested, or unexpected input stays silent.
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
