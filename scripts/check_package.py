#!/usr/bin/env python3
"""Check distribution files without accessing the user's Codex home."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/codex-task-routing'


def check():
    manifest = json.loads((PLUGIN / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
    market = json.loads((ROOT / '.agents/plugins/marketplace.json').read_text(encoding='utf-8'))
    assert manifest['name'] == PLUGIN.name == 'codex-task-routing'
    assert market['name'] == 'codex-task-routing'
    assert market['plugins'][0]['source']['path'] == './plugins/codex-task-routing'
    assert not {'hooks', 'mcpServers', 'apps'} & manifest.keys()
    assert re.fullmatch(r'\d+\.\d+\.\d+(?:[+-][\w.-]+)?', manifest['version'])
    assert manifest['author']['name'] == 'Acecore'
    for name in ['LICENSE', 'scripts/routing.py', 'scripts/updater.py', 'scripts/updater_entry.py', 'scripts/install_updater.py', 'hooks/hooks.json', 'defaults/config.json', 'skills/task-routing/SKILL.md', 'skills/task-routing/references/configuration.md']:
        assert (PLUGIN / name).is_file(), name
    hooks = json.loads((PLUGIN / 'hooks/hooks.json').read_text(encoding='utf-8'))['hooks']
    assert set(hooks) == {'SessionStart', 'SubagentStart'}
    for groups in hooks.values():
        for group in groups:
            for hook in group['hooks']:
                assert hook['type'] == 'command'
                assert hook['command'] == "python -c \"import os,runpy;runpy.run_path(os.path.join(os.environ['PLUGIN_ROOT'],'scripts','routing.py'),run_name='__main__')\" hook"
                assert hook['timeout'] <= 10
    config = json.loads((PLUGIN / 'defaults/config.json').read_text(encoding='utf-8'))
    assert config['policy_revision'] == '2026-09-14'
    assert config['models']['luna']['default_effort'] == 'max'
    assert config['models']['terra']['default_effort'] == 'xhigh'
    assert config['models']['sol']['default_effort'] == 'high'
    forbidden = re.compile(r'(?:[A-Za-z]:[/\\]Users[/\\](?!<|\$)[A-Za-z0-9_-]+)|(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)|(?:gh[pousr]_[A-Za-z0-9]{30,})')
    for path in PLUGIN.rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        content = path.read_text(encoding='utf-8')
        assert not forbidden.search(content), f'Private material: {path.relative_to(ROOT)}'
        assert '[TODO:' not in content, f'Unfinished scaffold: {path.relative_to(ROOT)}'
    print('Package checks passed (manifest, references, hooks, defaults, publication scan).')


if __name__ == '__main__':
    check()
