#!/usr/bin/env python3
"""Exercise one-shot background synchronization with native Codex and a local Git fixture.

No application/model session is started and no scheduled task is registered.
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
    next_version = initial_version.split('+')[0] + '+codex.updater-test'
    manifest['version'] = next_version
    manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
    next_runner = source / 'plugins/codex-task-routing/scripts/updater.py'
    with next_runner.open('a',encoding='utf-8') as stream:
        stream.write('\nNATIVE_FIXTURE_MARKER = "updated runner"\n')
    git('add','plugins/codex-task-routing/.codex-plugin/plugin.json',
        'plugins/codex-task-routing/scripts/updater.py')
    git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','Updated fixture')

    module_spec = importlib.util.spec_from_file_location('native_updater_fixture',ROOT/'plugins/codex-task-routing/scripts/updater.py')
    updater = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = updater
    module_spec.loader.exec_module(updater)

    def synchronize(home, env, *, running=False):
        assert home.is_relative_to(base) and Path(env['CODEX_HOME']) == home
        processes = {'Codex.exe'} if running else set()
        inherited = {**env, 'CODEX_HOME': str(base / 'unexpected-home')}
        interpreter = Path(sys.executable)
        if os.name == 'nt':
            interpreter = interpreter.with_name('pythonw.exe')
            assert interpreter.is_file(), 'Scheduler helper requires pythonw.exe'
        with mock.patch.dict(os.environ, inherited, clear=True), \
             mock.patch.object(updater,'_running_process_names',return_value=processes), \
             mock.patch.object(updater.sys,'executable',str(interpreter)):
            code = updater.main(['--codex',cli_path,'--codex-home',str(home)])
        assert not (base / 'unexpected-home').exists(), 'Native CLI used the inherited home'
        result = json.loads((home/'codex-task-routing/updater/state/last-sync.json').read_text())
        return code, result

    assert installed_version(env) == initial_version, 'Listing alone must not fetch main'
    skipped_code, skipped = synchronize(home,env,running=True)
    assert skipped_code == 0 and skipped['outcome'] == 'skipped', skipped
    assert installed_version(env) == initial_version
    success_code, success = synchronize(home,env)
    assert success_code == 0, success
    assert success['outcome'] == 'updated', success
    assert installed_version(env) == next_version
    entry = runpy.run_path(str(ROOT/'plugins/codex-task-routing/scripts/updater_entry.py'))
    current_runtime = entry['_current_runtime'](cli_path, home, str(home))
    assert current_runtime is not None
    assert runpy.run_path(str(current_runtime))['NATIVE_FIXTURE_MARKER'] == 'updated runner'
    pinned_code, pinned = synchronize(pinned_home,pinned_env)
    assert pinned_code == 0, pinned
    assert pinned['outcome'] == 'unchanged', pinned
    assert installed_version(pinned_env) == initial_version
    offline_env = {**env,'GIT_CONFIG_KEY_0':f'url.{(base / "missing-remote").as_uri()}.insteadOf'}
    failure_code, failure = synchronize(home,offline_env)
    assert failure['outcome'] == 'failed', failure
    assert installed_version(env) == next_version
    for path, content in {**protected,**pinned_protected}.items():
        assert path.read_bytes() == content, path
    report = {'ok':True,'checks':['running app skips native update',
        'one-shot update follows main without launching an application',
        'native source resolves the newer updater runtime',
        'explicit Codex home overrides a different inherited home',
        'Windows native helper works with the scheduled pythonw interpreter',
        'pinned commit remains pinned','unavailable remote retains installed version',
        'native config, guidance and overrides are unchanged'],
        'not_verified':['scheduled task registration','closed desktop process detection on other OSes',
                        'native upgrade crash atomicity','hook trust interaction']}
    (base/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
