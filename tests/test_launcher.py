import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import unittest
import uuid
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = load('routing_bootstrap', 'plugins/codex-task-routing/scripts/launcher_bootstrap.py')
installer = load('routing_install', 'plugins/codex-task-routing/scripts/install_launcher.py')
release = load('routing_release', 'scripts/check_release.py')


class LauncherTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / 'tests/.tmp-runtime-tests'
        scratch.mkdir(parents=True, exist_ok=True)
        self.base = scratch / ('launcher-' + uuid.uuid4().hex)
        self.base.mkdir()
        def cleanup():
            assert self.base.resolve().is_relative_to(scratch.resolve())
            shutil.rmtree(self.base)
        self.addCleanup(cleanup)

    def test_install_is_explicit_and_preserves_other_codex_files(self):
        home = self.base / 'home'
        home.mkdir()
        config = home / 'config.toml'
        config.write_text('model="example"\n', encoding='utf-8')
        (home / 'codex-task-routing').mkdir()
        overrides = home / 'codex-task-routing' / 'overrides.json'
        overrides.write_text('{"custom":true}', encoding='utf-8')
        destination = home / 'codex-task-routing' / 'launcher'
        with mock.patch.object(installer.subprocess, 'run', side_effect=AssertionError('No app or plugin commands')):
            path = installer.install_files(destination, home, Path('/codex').absolute(), 'cli')
            installer.install_files(destination, home, Path('/codex').absolute(), 'cli')
        self.assertTrue(path.is_file())
        self.assertEqual(bootstrap.launcher_settings(installer.current_generation(destination))['mode'], 'cli')
        self.assertEqual(config.read_text(), 'model="example"\n')
        self.assertEqual(overrides.read_text(), '{"custom":true}')
        path.write_text('user modifications', encoding='utf-8')
        with self.assertRaises(ValueError):
            installer.install_files(destination, home, Path('/codex').absolute(), 'cli')
        self.assertEqual(path.read_text(), 'user modifications')

    def test_existing_unmanaged_directory_is_not_overwritten(self):
        destination = self.base / 'launcher'
        destination.mkdir()
        valuable = destination / 'notes.txt'
        valuable.write_text('keep', encoding='utf-8')
        with self.assertRaises(ValueError):
            installer.install_files(destination, self.base, Path('/codex').absolute(), 'cli')
        self.assertEqual(valuable.read_text(), 'keep')

    def test_native_source_is_resolved_without_guessing_cache_version(self):
        source = self.base / 'marketplace source' / 'plugin'
        (source / '.codex-plugin').mkdir(parents=True)
        (source / 'scripts').mkdir()
        (source / '.codex-plugin/plugin.json').write_text(json.dumps({'name':'codex-task-routing'}))
        expected = source / 'scripts/prelaunch.py'
        expected.write_text('def main(argv=None): return 0\n')
        entry = {'pluginId':bootstrap.PLUGIN_ID, 'installed':True, 'enabled':True,
                 'marketplaceSource':{'sourceType':'git','source':bootstrap.REPOSITORY+'.git'},
                 'source':{'source':'local','path':str(source)}}
        result = subprocess.CompletedProcess([], 0, json.dumps({'installed':[entry]}), '')
        with mock.patch.object(bootstrap.subprocess, 'run', return_value=result):
            self.assertEqual(bootstrap.marketplace_updater('codex', self.base, {}), expected)
        entry['marketplaceSource']['source'] = 'https://example.invalid/same-name.git'
        result.stdout = json.dumps({'installed':[entry]})
        with mock.patch.object(bootstrap.subprocess, 'run', return_value=result):
            self.assertIsNone(bootstrap.marketplace_updater('codex', self.base, {}))

    def test_unavailable_source_uses_guarded_fallback_and_preserves_cli_arguments(self):
        destination = self.base / 'launcher'
        installer.install_files(destination, self.base, Path('/codex').absolute(), 'cli')
        generation = installer.current_generation(destination)
        updater = mock.Mock(return_value=0)
        args = ['--cli', '--', '--cwd', 'directory with spaces', 'a;b$literal']
        with mock.patch.object(bootstrap, '__file__', str(generation / 'bootstrap.py')), \
             mock.patch.object(bootstrap.shutil, 'which', return_value=None), \
             mock.patch.object(bootstrap, 'marketplace_updater', return_value=None), \
             mock.patch.object(bootstrap, 'load_updater', return_value=updater) as load_mock, \
             mock.patch.dict(os.environ, {}, clear=False):
            self.assertEqual(bootstrap.main(args), 0)
        load_mock.assert_called_once_with(generation / 'prelaunch_fallback.py')
        self.assertEqual(updater.call_args.args[0][-3:], ['--cwd', 'directory with spaces', 'a;b$literal'])

    def test_release_guard(self):
        self.assertTrue(release.needs_version_change(['plugins/codex-task-routing/scripts/routing.py'], '1', '1'))
        self.assertFalse(release.needs_version_change(['plugins/codex-task-routing/defaults/config.json'], '1', '2'))
        self.assertFalse(release.needs_version_change(['README.md'], '1', '1'))

    def test_interrupted_reinstall_keeps_old_generation_and_can_be_retried(self):
        destination = self.base / 'launcher'
        cli = Path('/codex').absolute()
        installer.install_files(destination,self.base,cli,'cli')
        previous = installer.current_generation(destination)
        real_replace = os.replace
        def fail_pointer(source,target):
            if Path(target).name == 'current.json':
                raise OSError('simulated interrupted install')
            return real_replace(source,target)
        with mock.patch.object(installer.os,'replace',side_effect=fail_pointer):
            with self.assertRaises(OSError):
                installer.install_files(destination,self.base,cli,'desktop')
        self.assertEqual(installer.current_generation(destination),previous)
        self.assertEqual(bootstrap.launcher_settings(previous)['mode'],'cli')
        installer.install_files(destination,self.base,cli,'desktop')
        self.assertEqual(bootstrap.launcher_settings(installer.current_generation(destination))['mode'],'desktop')

    def test_interrupted_first_install_leaves_no_incomplete_destination(self):
        destination = self.base / 'launcher'
        with mock.patch.object(installer.os,'replace',side_effect=OSError('simulated disk full')):
            with self.assertRaises(OSError):
                installer.install_files(destination,self.base,Path('/codex').absolute(),'cli')
        self.assertFalse(destination.exists())
        installer.install_files(destination,self.base,Path('/codex').absolute(),'cli')
        self.assertTrue(installer.current_generation(destination).is_dir())


if __name__ == '__main__':
    unittest.main()
