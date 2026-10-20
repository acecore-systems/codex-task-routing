"""Explicit cloud runner tests: fixtures only, no cloud, models or credentials."""
from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'plugins/codex-task-routing/scripts'
sys.path.insert(0, str(SCRIPTS))
import cloud_run as run
from cloud_record import ClaimStore, CloudRunError, private_directory
import session_plan as plan


class FixtureTransport:
    def __init__(self):
        self.calls = []
        self.submit_result = run.CommandResult(0, b'https://chatgpt.com/codex/tasks/task-1\n')
        self.status_result = run.CommandResult(0, b'[READY] Example\nenvironment-1 1m\n+1/-0 1 file\n')
        self.diff_result = run.CommandResult(0, b'diff --git a/a b/a\r\n+\xe6\x97\xa5\xe6\x9c\xac\xe8\xaa\x9e\n')
        self.on_submit = None

    def submit(self, handoff, prompt, cwd):
        self.calls.append(('exec', handoff['environment_id'], handoff['branch'], prompt))
        if self.on_submit:
            self.on_submit()
        return self.submit_result

    def status(self, task_id, cwd):
        self.calls.append(('status', task_id))
        return self.status_result

    def diff(self, task_id, cwd):
        self.calls.append(('diff', task_id, 1))
        return self.diff_result


class CloudRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory(prefix='routing-cloud-fixture-'))
        # macOS /var aliases /private/var; canonicalize fixtures, not runtime guards.
        self.base = Path(self.temp).resolve()
        self.cwd = self.base / 'workspace'
        self.cwd.mkdir()
        self.state = self.base / 'caller-private-state'
        self.now = datetime(2026, 10, 21, 1, tzinfo=timezone.utc)
        self.prompt = b'Approved task: quote " & $(nothing); `nothing`\r\nJapanese: \xe6\x97\xa5\xe6\x9c\xac\xe8\xaa\x9e'
        self.handoff = dict(schema_version=1, request_id='cloud-unit-1',
            input_sha256=hashlib.sha256(self.prompt).hexdigest(), owner_id='root-owner', return_to='root-task',
            repository='example/project', branch='codex/example', expected_revision='1' * 40,
            environment_id='environment-1', endpoint='Implement and return verified evidence',
            acceptance_criteria=['Scoped regression fixed'], required_checks=['test command'],
            allowed_operations=['read', 'edit', 'test'], state=dict(submission='not_sent', task_id=None))
        self.live = dict(schema_version=1, handoff_sha256=plan.handoff_sha256(self.handoff),
            task_id=None, surface='codex_cloud', transport='codex_cloud_cli',
            actor=dict(role='root', depth=0, executor_id='root-owner'), observed_at=self.now.isoformat(),
            checks={name: dict(state='verified' if name in plan.OBSERVED_CHECKS else 'permission_checked',
                               reference='Synthetic existing scoped evidence') for name in plan.CHECKS})
        self.transport = FixtureTransport()
        self.repo_checks = []

    def repository_check(self, handoff, cwd):
        self.repo_checks.append((handoff['expected_revision'], cwd))

    def dispatch(self, **kwargs):
        return run.dispatch(self.handoff, self.live, self.prompt, cwd=self.cwd, state_dir=self.state,
            transport=self.transport, repository_check=self.repository_check, now=self.now, **kwargs)

    def store(self):
        return ClaimStore(self.state, self.handoff['request_id'])

    def receive(self):
        live = {**self.live, 'task_id': 'task-1', 'checks': {key: self.live['checks'][key]
            for key in ('host_permission', 'receive_capability', 'receive_authorization')}}
        return run.receive(self.handoff, live, cwd=self.cwd, state_dir=self.state,
                           transport=self.transport, now=self.now)

    def test_claim_is_durable_before_send_and_exact_wire_hash_is_retained(self):
        def inspect_before_send():
            record = self.store().read()
            self.assertEqual(record['submission'], 'unknown_outcome')
            self.assertIsNone(record['task_id'])
            self.assertTrue(self.store().claim_path.is_file())
        self.transport.on_submit = inspect_before_send
        output = self.dispatch()
        self.assertEqual(output['submission'], 'sent')
        self.assertEqual(output['task_id'], 'task-1')
        wire = self.transport.calls[0][3]
        self.assertTrue(wire.endswith(self.prompt))
        self.assertIn(b'actual repository and starting HEAD', wire)
        self.assertIn(self.handoff['expected_revision'].encode(), wire)
        record = self.store().read()
        self.assertEqual(record['binding']['wire_sha256'], hashlib.sha256(wire).hexdigest())
        self.assertNotEqual(record['binding']['wire_sha256'], record['binding']['input_sha256'])
        for key in ('cloud_checkout_verified', 'reply_task_identity_attested', 'authorization_issued'):
            self.assertFalse(output[key])
        self.assertEqual(output['verification_status'], 'unverified')

    def test_repeat_and_stale_not_sent_input_reuse_original_record_without_exec(self):
        self.dispatch()
        self.dispatch()
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(len(self.repo_checks), 1)
        self.assertEqual(self.handoff['state'], dict(submission='not_sent', task_id=None))

    def test_parallel_dispatch_has_one_exclusive_owner(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        def block():
            entered.set()
            self.assertTrue(release.wait(5))
        self.transport.on_submit = block
        thread = threading.Thread(target=lambda: results.append(self.dispatch()))
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaises(CloudRunError):
                self.dispatch()
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(results[0]['submission'], 'sent')

    def test_timeout_failure_and_unrecognized_or_ambiguous_exec_never_allow_resend(self):
        responses = [run.CommandResult(None, failure='command_timeout'), run.CommandResult(1, b'private error'),
            run.CommandResult(0, b''), run.CommandResult(0, b'Created https://chatgpt.com/codex/tasks/task-1\n'),
            run.CommandResult(0, b'https://chatgpt.com/codex/tasks/task-1\nhttps://chatgpt.com/codex/tasks/task-2\n'),
            run.CommandResult(0, b'https://other.invalid/codex/tasks/task-1\n'),
            run.CommandResult(0, b'https://chatgpt.com/codex/tasks/task-1?token=private\n'),
            run.CommandResult(0, b'\xff')]
        for index, response in enumerate(responses):
            with self.subTest(index=index):
                self.handoff['request_id'] = 'request-' + str(index)
                self.live['handoff_sha256'] = plan.handoff_sha256(self.handoff)
                self.transport.submit_result = response
                output = self.dispatch()
                self.assertEqual(output['submission'], 'unknown_outcome')
                count = len(self.transport.calls)
                self.dispatch()
                self.assertEqual(len(self.transport.calls), count)

    def test_crash_after_claim_and_record_write_failure_remain_sticky(self):
        def crash():
            raise SystemExit('synthetic crash')
        self.transport.on_submit = crash
        with self.assertRaises(SystemExit):
            self.dispatch()
        self.transport.on_submit = None
        self.assertEqual(self.dispatch()['submission'], 'unknown_outcome')
        self.assertEqual(len(self.transport.calls), 1)

    def test_real_process_crash_keeps_claim_and_releases_os_lock_without_resend(self):
        expected = {**run.binding(self.handoff, self.cwd),
                    'wire_sha256': hashlib.sha256(run.wire_prompt(self.handoff, self.prompt)).hexdigest()}
        script = ("import json,os,sys; sys.path.insert(0,sys.argv[1]); "
                  "from cloud_record import ClaimStore; "
                  "store=ClaimStore(sys.argv[2],sys.argv[3]); "
                  "guard=store.lock(); guard.__enter__(); "
                  "store.claim(json.loads(sys.argv[4])); os._exit(17)")
        response = subprocess.run([sys.executable, '-c', script, str(SCRIPTS), str(self.state),
            self.handoff['request_id'], json.dumps(expected)], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=10, check=False)
        self.assertEqual(response.returncode, 17)
        self.assertEqual(response.stdout, b'')
        self.assertEqual(self.dispatch()['submission'], 'unknown_outcome')
        self.assertEqual(self.transport.calls, [])

    def test_corrupt_record_status_cannot_echo_arbitrary_private_text(self):
        self.dispatch()
        path = self.store().record_path
        data = json.loads(path.read_text(encoding='utf-8'))
        data['remote_status'] = 'synthetic-private-text-never-print'
        path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(CloudRunError):
            self.store().read()

    def test_known_receipt_is_reported_without_false_saved_claim_on_snapshot_failure(self):
        with patch.object(ClaimStore, 'save', side_effect=OSError('never expose this')):
            output = self.dispatch()
        self.assertEqual(output['submission'], 'unknown_outcome')
        self.assertEqual(output['observed_task_id'], 'task-1')
        self.assertEqual(self.store().read()['submission'], 'unknown_outcome')
        self.dispatch()
        self.assertEqual(len(self.transport.calls), 1)

    def test_corrupt_or_partial_claim_blocks_dispatch(self):
        store = self.store()
        store.claim_path.write_bytes(b'{')
        with self.assertRaises((ValueError, run.RoutingError)):
            self.dispatch()
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(store.claim_path.read_bytes(), b'{')

    def test_prompt_hash_scope_cwd_and_expected_revision_cannot_change(self):
        self.dispatch()
        for field, value in [('expected_revision', '2' * 40), ('repository', 'example/other'),
                             ('environment_id', 'other-env'), ('owner_id', 'other-owner')]:
            with self.subTest(field=field):
                original = self.handoff[field]
                self.handoff[field] = value
                with self.assertRaises(CloudRunError):
                    self.dispatch()
                self.handoff[field] = original
        original_prompt = self.prompt
        self.prompt += b'Changed'
        with self.assertRaises(CloudRunError):
            self.dispatch()
        self.handoff['input_sha256'] = hashlib.sha256(self.prompt).hexdigest()
        with self.assertRaises(CloudRunError):
            self.dispatch()
        self.prompt = original_prompt
        other = self.base / 'other-workspace'; other.mkdir()
        with self.assertRaises(CloudRunError):
            run.receive(self.handoff, self.live, cwd=other, state_dir=self.state, transport=self.transport, now=self.now)
        self.assertEqual(len(self.transport.calls), 1)

    def test_wrong_gate_surface_actor_or_option_branch_never_claims_or_submits(self):
        for changes in ({'transport': 'codex_cloud_ui'}, {'surface': 'chatgpt_work'},
                        {'actor': dict(role='sol', depth=1, executor_id='root-owner')},
                        {'checks': {}}):
            with self.subTest(changes=changes), self.assertRaises(CloudRunError):
                run.dispatch(self.handoff, {**self.live, **changes}, self.prompt,
                    cwd=self.cwd, state_dir=self.state, transport=self.transport,
                    repository_check=self.repository_check, now=self.now)
        self.handoff['branch'] = '--config=unsafe'
        with self.assertRaises(CloudRunError):
            self.dispatch()
        self.assertEqual(self.transport.calls, [])
        self.assertFalse(self.store().claim_path.exists())
        for field in ('environment_id', 'request_id'):
            bad = {**self.handoff, field: '-unsafe'}
            with self.assertRaises(plan.SessionPlanError):
                plan.validate_handoff(bad)

    def test_receive_uses_original_read_scope_and_retains_exact_diff_bytes(self):
        self.dispatch()
        output = self.receive()
        self.assertEqual(self.transport.calls[1:], [('status', 'task-1'), ('diff', 'task-1', 1)])
        self.assertEqual(output['submission'], 'collected')
        artifact = self.store().directory / output['collection']['diff_file']
        self.assertEqual(artifact.read_bytes(), self.transport.diff_result.stdout)
        self.assertEqual(output['collection']['diff_sha256'], hashlib.sha256(artifact.read_bytes()).hexdigest())
        self.assertEqual(output['verification_status'], 'unverified')
        self.assertFalse(output['cloud_checkout_verified'])
        self.dispatch()
        self.assertEqual(sum(call[0] == 'exec' for call in self.transport.calls), 1)

    def test_pending_error_and_applied_are_not_transport_failure_or_test_success(self):
        self.dispatch()
        for marker, expected in [('PENDING', 'running'), ('ERROR', 'failed'), ('APPLIED', 'applied')]:
            with self.subTest(marker=marker):
                self.transport.status_result = run.CommandResult(1, f'[{marker}] Example\nenv 1m\nno diff\n'.encode())
                before = len(self.transport.calls)
                output = self.receive()
                self.assertEqual(output['remote_status'], expected)
                self.assertEqual(output['verification_status'], 'unverified')
                self.assertEqual(len(self.transport.calls) - before, 1 if marker == 'PENDING' else 2)

    def test_status_shape_and_exit_mismatch_do_not_infer_completion(self):
        self.dispatch()
        for response in (run.CommandResult(1, b'[READY] Example\nenv 1m\nno diff\n'),
                         run.CommandResult(0, b'[READY] private content\n'),
                         run.CommandResult(0, b'\xff'), run.CommandResult(None, failure='command_timeout')):
            self.transport.status_result = response
            output = self.receive()
            self.assertEqual(output['event'], 'collection_failed')
            self.assertIsNone(output['collection'])
        self.assertFalse(any(call[0] == 'diff' for call in self.transport.calls))

    def test_received_task_cannot_be_replaced_by_live_or_handoff(self):
        self.dispatch()
        live = {**self.live, 'task_id': 'other-task'}
        with self.assertRaises(CloudRunError):
            run.receive(self.handoff, live, cwd=self.cwd, state_dir=self.state, transport=self.transport, now=self.now)
        self.handoff['state'] = dict(submission='sent', task_id='other-task')
        with self.assertRaises(CloudRunError):
            self.receive()
        self.assertEqual(len(self.transport.calls), 1)

    def test_record_cannot_rewind_or_replace_known_task(self):
        self.dispatch()
        record = self.store().read()
        record['task_id'] = 'other-task'
        with self.assertRaises(CloudRunError):
            self.store().save(record)
        record = self.store().read(); record.update(submission='unknown_outcome', task_id=None)
        with self.assertRaises(CloudRunError):
            self.store().save(record)
        self.assertEqual(self.store().read()['task_id'], 'task-1')

    def test_private_store_refuses_any_git_checkout_and_contains_no_prompt_or_raw_logs(self):
        (self.cwd / '.git').write_text('synthetic repository marker', encoding='utf-8')
        with self.assertRaises(CloudRunError):
            private_directory(self.cwd / 'private-but-in-repo')
        self.dispatch()
        for path in self.state.rglob('*.json'):
            content = path.read_bytes()
            self.assertNotIn(self.prompt, content)
            self.assertNotIn(b'Approved task', content)
            self.assertNotIn(b'private error', content)

    def test_cli_diagnostics_do_not_echo_paths_material_or_native_output(self):
        source = self.base / 'handoff.json'
        source.write_text(json.dumps({**self.handoff, 'credential': 'synthetic-secret-never-print'}), encoding='utf-8')
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            code = run.main(['inspect-record', '--handoff', str(source), '--cwd', str(self.cwd), '--state-dir', str(self.state)])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(json.loads(err.getvalue()), {'error': 'cloud_transport_not_completed'})
        self.assertNotIn(str(source), err.getvalue())
        self.assertNotIn('synthetic-secret-never-print', err.getvalue())

    def test_native_argv_stdin_protocol_and_endpoint_override_checks(self):
        binary = self.base / 'native codex.exe'; binary.write_bytes(b'fixture-not-executed')
        calls = []
        def runner(argv, **kwargs):
            calls.append((argv, kwargs)); return run.CommandResult(0)
        with patch.dict(os.environ, {}, clear=True):
            transport = run.NativeCLITransport(binary, run.SOURCE_PROTOCOL, runner=runner)
            transport.submit(self.handoff, self.prompt, self.cwd)
            self.assertEqual(calls[0][0][-1], '-')
            self.assertNotIn(self.prompt.decode(), calls[0][0])
            self.assertEqual(calls[0][1]['input_bytes'], self.prompt)
            self.assertEqual(calls[0][0][2:6], ['exec', '--env', 'environment-1', '--branch'])
            transport.status('task-1', self.cwd); transport.diff('task-1', self.cwd)
            self.assertFalse(any('apply' in argv or 'list' in argv for argv, kw in calls))
            for name in ('CODEX_CLOUD_TASKS_BASE_URL', 'CODEX_CLOUD_TASKS_MODE'):
                with patch.dict(os.environ, {name: 'synthetic-private-override'}):
                    with self.assertRaises(CloudRunError):
                        run.NativeCLITransport(binary, run.SOURCE_PROTOCOL, runner=runner)
                    with self.assertRaises(CloudRunError):
                        transport.submit(self.handoff, self.prompt, self.cwd)
        with self.assertRaises(CloudRunError):
            run.NativeCLITransport(binary, 'unknown-profile', runner=runner)
        if os.name == 'nt':
            wrapper = self.base / 'codex.cmd'; wrapper.write_text('echo unsafe')
            with self.assertRaises(CloudRunError):
                run.NativeCLITransport(wrapper, run.SOURCE_PROTOCOL, runner=runner)

    def test_process_uses_shell_false_stdin_bytes_and_discards_stderr(self):
        response = subprocess.CompletedProcess(['fixture'], 0, stdout=b'exact\r\nbytes', stderr=b'never-print')
        with patch.object(subprocess, 'run', return_value=response) as native:
            output = run.run_process(['native path.exe', 'cloud', 'exec', '-'], cwd=self.cwd, input_bytes=self.prompt)
        self.assertEqual(output.stdout, b'exact\r\nbytes')
        self.assertFalse(native.call_args.kwargs['shell'])
        self.assertEqual(native.call_args.kwargs['input'], self.prompt)
        self.assertEqual(native.call_args.kwargs['stderr'], subprocess.DEVNULL)
        with patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 60, output=b'private')):
            self.assertEqual(run.run_process(['fixture'], cwd=self.cwd).failure, 'command_timeout')

    def test_real_fixture_git_check_rejects_local_remote_or_scope_mismatch(self):
        git = self.base / 'git.exe'; git.write_bytes(b'fixture-not-executed')
        replies = {
            ('rev-parse', '--show-toplevel'): str(self.cwd),
            ('check-ref-format', '--branch', 'codex/example'): 'codex/example',
            ('rev-parse', '--abbrev-ref', 'HEAD'): 'codex/example',
            ('rev-parse', 'HEAD'): '1' * 40,
            ('status', '--porcelain=v1', '--untracked-files=normal'): '',
            ('remote', 'get-url', '--all', 'origin'): 'git@github.com:example/project.git',
            ('ls-remote', '--exit-code', '--refs', 'origin', 'refs/heads/codex/example'):
                '1' * 40 + '\trefs/heads/codex/example'}
        def fixture(argv, **kwargs):
            return run.CommandResult(0, (replies[tuple(argv[1:])] + '\n').encode())
        run.verify_repository(self.handoff, self.cwd, git=git, runner=fixture)
        for key, value in [(('rev-parse', 'HEAD'), '2' * 40),
            (('status', '--porcelain=v1', '--untracked-files=normal'), ' M changed'),
            (('remote', 'get-url', '--all', 'origin'), 'https://token@github.com/example/project.git'),
            (('ls-remote', '--exit-code', '--refs', 'origin', 'refs/heads/codex/example'), '2' * 40 + '\trefs/heads/codex/example')]:
            with self.subTest(key=key):
                original = replies[key]; replies[key] = value
                with self.assertRaises(CloudRunError):
                    run.verify_repository(self.handoff, self.cwd, git=git, runner=fixture)
                replies[key] = original


if __name__ == '__main__':
    unittest.main()
