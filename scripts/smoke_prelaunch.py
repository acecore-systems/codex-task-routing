#!/usr/bin/env python3
"""Exercise pre-launch synchronization with native Codex and a local Git fixture.

No application/model session is started. The target is a Python marker command.
Only this isolated home's process detector is mocked to model a closed app.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = 'codex-task-routing@codex-task-routing'
URL = 'https://github.com/acecore-systems/codex-task-routing.git'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', default='codex')
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    base = args.output_dir.absolute()
    base.mkdir(parents=True, exist_ok=False)
    source = base / 'source'
    shutil.copytree(ROOT / '.agents', source / '.agents')
    shutil.copytree(ROOT / 'plugins', source / 'plugins', ignore=shutil.ignore_patterns('__pycache__'))
    environment = {**os.environ, 'GIT_TERMINAL_PROMPT':'0', 'GCM_INTERACTIVE':'Never',
                   'GIT_CONFIG_COUNT':'1', 'GIT_CONFIG_KEY_0':f'url.{source.as_uri()}.insteadOf',
                   'GIT_CONFIG_VALUE_0':URL}
    cli_path = shutil.which(args.codex)
    if not cli_path:
        raise SystemExit('Codex CLI was not found')

    def run(command, *, cwd=base, env=environment):
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                                text=True, encoding='utf-8', timeout=60)
        if result.returncode:
            raise RuntimeError(f'Fixture command failed with exit {result.returncode}: {command[:3]}')
        return result.stdout

    def git(*arguments):
        return run(['git', *arguments], cwd=source)

    def cli(env, *arguments):
        return json.loads(run([cli_path,'plugin',*arguments,'--json'],env=env))

    def installed_version(env):
        entries = cli(env,'list','--marketplace','codex-task-routing')['installed']
        return next(entry['version'] for entry in entries if entry['pluginId'] == PLUGIN_ID)

    git('init','-b','main')
    git('add','.agents','plugins')
    git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','Initial fixture')
    first_sha = git('rev-parse','HEAD').strip()
    manifest_path = source / 'plugins/codex-task-routing/.codex-plugin/plugin.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    initial_version = manifest['version']

    def make_home(name, ref):
        home = base / name
        home.mkdir()
        (home / 'AGENTS.md').write_text('Preserve this test guidance.\n',encoding='utf-8')
        (home / 'config.toml').write_text('model_reasoning_effort="high"\n',encoding='utf-8')
        private = home / 'codex-task-routing'
        private.mkdir()
        (private / 'overrides.json').write_text('{"schema_version":1,"models":{"terra":{"default_effort":"high"}}}',encoding='utf-8')
        env = {**environment,'CODEX_HOME':str(home)}
        cli(env,'marketplace','add',URL,'--ref',ref)
        cli(env,'add',PLUGIN_ID)
        protected = {p:p.read_bytes() for p in (home/'AGENTS.md',home/'config.toml',private/'overrides.json')}
        return home,env,protected

    home, env, protected = make_home('main-home','main')
    pinned_home,pinned_env,pinned_protected = make_home('pinned-home',first_sha)
    next_version = initial_version.split('+')[0] + '+codex.prelaunch-test'
    manifest['version'] = next_version
    manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
    next_runner = source / 'plugins/codex-task-routing/scripts/prelaunch.py'
    with next_runner.open('a',encoding='utf-8') as stream:
        stream.write('\nNATIVE_FIXTURE_MARKER = "updated runner"\n')
    git('add','plugins/codex-task-routing/.codex-plugin/plugin.json',
        'plugins/codex-task-routing/scripts/prelaunch.py')
    git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','Updated fixture')

    module_spec = importlib.util.spec_from_file_location('native_prelaunch_fixture',ROOT/'plugins/codex-task-routing/scripts/prelaunch.py')
    updater = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = updater
    module_spec.loader.exec_module(updater)

    def launch_marker(home, env, name):
        assert home.is_relative_to(base) and Path(env['CODEX_HOME']) == home
        marker = base / (name+'.txt')
        target = [sys.executable,'-c','from pathlib import Path; import sys; Path(sys.argv[1]).write_text("launched")',str(marker)]
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(updater,'_running_process_names',return_value=set()):
            assert updater.main(['--codex',cli_path,'--',*target]) == 0
        assert marker.read_text() == 'launched'
        return json.loads((home/'codex-task-routing/launcher/state/last-sync.json').read_text())

    assert installed_version(env) == initial_version, 'Listing alone must not fetch main'
    success = launch_marker(home,env,'success')
    assert success['outcome'] == 'updated', success
    assert installed_version(env) == next_version
    bootstrap = runpy.run_path(str(ROOT/'plugins/codex-task-routing/scripts/launcher_bootstrap.py'))
    current_runner = bootstrap['marketplace_updater'](cli_path,home,env)
    assert current_runner is not None
    assert runpy.run_path(str(current_runner))['NATIVE_FIXTURE_MARKER'] == 'updated runner'
    pinned = launch_marker(pinned_home,pinned_env,'pinned')
    assert pinned['outcome'] == 'unchanged', pinned
    assert installed_version(pinned_env) == initial_version
    offline_env = {**env,'GIT_CONFIG_KEY_0':f'url.{(base / "missing-remote").as_uri()}.insteadOf'}
    failure = launch_marker(home,offline_env,'offline')
    assert failure['outcome'] == 'failed', failure
    assert installed_version(env) == next_version
    for path, content in {**protected,**pinned_protected}.items():
        assert path.read_bytes() == content, path
    report = {'ok':True,'checks':['main update precedes marker target',
        'bootstrap resolves the updated runner from the native source',
        'pinned commit remains pinned','unavailable remote retains installed version and launches target',
        'native config, guidance and overrides are unchanged'],
        'not_verified':['real desktop launch','closed desktop process detection on other OSes',
                        'native upgrade crash atomicity','hook trust interaction']}
    (base/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
