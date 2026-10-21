"""UI-local claim safety with real files and locks; no Cloud or browser calls."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/codex-task-routing/scripts'
sys.path.insert(0, str(SCRIPTS))
import cloud_record as records
from routing_core.common import RoutingError
from session_plan import SessionPlanError

RECORD_ERRORS = (records.CloudRecordError, SessionPlanError, RoutingError)


class CloudRecordTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix='routing-ui-claim-'))
        self.base = Path(directory).resolve()
        self.binding = dict(request_id='ui-unit-1', input_sha256='a' * 64,
            handoff_sha256='b' * 64, cwd=str(self.base / 'workspace'),
            repository='example/project', branch='codex/example', expected_revision='1' * 40,
            environment_id='environment-1', owner_id='root-owner', tab_id='original-ui-tab')
        self.store = records.ClaimStore(self.base / 'private-state', self.binding['request_id'])

    def claim(self):
        with self.store.lock():
            return self.store.claim(copy.deepcopy(self.binding))

    def sent(self):
        record = self.claim()
        record.update(submission='sent', task_id='original-task', event='submission_recorded')
        with self.store.lock():
            self.store.save(record)
        return record

    def test_claim_precedes_send_and_ui_lifecycle_keeps_original_binding(self):
        record = self.claim()
        self.assertEqual(record['schema_version'], 2)
        self.assertEqual(record['submission'], 'unknown_outcome')
        self.assertIsNone(record['task_id'])
        self.assertTrue(self.store.claim_path.is_file())
        self.assertFalse(self.store.record_path.exists())
        for state, event in [('sent', 'submission_recorded'), ('collected', 'collection_recorded'),
                             ('accepted', 'owner_review_recorded')]:
            record.update(submission=state, task_id='original-task', event=event)
            with self.store.lock():
                self.store.save(record)
                self.assertEqual(self.store.read(), record)
        self.assertEqual(record['binding'], self.binding)
        self.assertEqual(set(record), {'schema_version', 'binding', 'submission', 'task_id', 'event'})

    def test_saved_claim_is_permanent_across_new_store_and_lock_release(self):
        record = self.sent()
        reopened = records.ClaimStore(self.store.root, self.binding['request_id'])
        before = self.store.claim_path.read_bytes()
        with reopened.lock():
            self.assertEqual(reopened.read(), record)
            with self.assertRaises(records.CloudRecordError):
                reopened.claim(self.binding)
        self.assertEqual(self.store.claim_path.read_bytes(), before)

    def test_parallel_owners_can_create_only_one_claim(self):
        barrier = threading.Barrier(2)
        outcomes = []
        stores = [records.ClaimStore(self.store.root, self.binding['request_id']) for _ in range(2)]
        def attempt(store):
            barrier.wait(timeout=5)
            try:
                with store.lock():
                    store.claim(self.binding)
                outcomes.append('claimed')
            except records.CloudRecordError as exc:
                outcomes.append(str(exc))
        threads = [threading.Thread(target=attempt, args=(store,)) for store in stores]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes.count('claimed'), 1)
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(set(outcomes) <= {'claimed', 'request_busy', 'submission_already_claimed'})

    def test_os_lock_excludes_another_owner_and_is_reusable(self):
        another = records.ClaimStore(self.store.root, self.binding['request_id'])
        with self.store.lock():
            with self.assertRaises(records.CloudRecordError):
                with another.lock():
                    self.fail('A second owner acquired the active lock')
        with another.lock():
            self.assertIsNone(another.read())

    def test_process_crash_preserves_claim_and_releases_os_lock(self):
        script = (
            'import json, os, sys\n'
            'sys.path.insert(0, sys.argv[1])\n'
            'from cloud_record import ClaimStore\n'
            'binding = json.loads(sys.argv[3])\n'
            'store = ClaimStore(sys.argv[2], binding["request_id"])\n'
            'with store.lock():\n'
            '    store.claim(binding)\n'
            '    os._exit(17)\n')
        result = subprocess.run([sys.executable, '-c', script, str(SCRIPTS), str(self.store.root),
                                 json.dumps(self.binding)], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=15, check=False)
        self.assertEqual(result.returncode, 17)
        with self.store.lock():
            self.assertEqual(self.store.read()['submission'], 'unknown_outcome')
            with self.assertRaises(records.CloudRecordError):
                self.store.claim(self.binding)

    def test_store_request_id_must_match_binding_on_claim_and_read(self):
        other = {**self.binding, 'request_id': 'different-request'}
        with self.store.lock():
            with self.assertRaises(records.CloudRecordError):
                self.store.claim(other)
            self.assertFalse(self.store.claim_path.exists())
        self.claim()
        tampered = json.loads(self.store.claim_path.read_bytes())
        tampered['binding'] = other
        self.store.claim_path.write_text(json.dumps(tampered), encoding='utf-8')
        before = self.store.claim_path.read_bytes()
        with self.store.lock():
            for operation in (self.store.read, lambda: self.store.claim(self.binding)):
                with self.assertRaises(records.CloudRecordError):
                    operation()
        self.assertEqual(self.store.claim_path.read_bytes(), before)

    def test_material_scope_environment_owner_and_original_tab_cannot_change(self):
        record = self.sent()
        before = self.store.record_path.read_bytes()
        for key, value in [('input_sha256', 'c' * 64), ('handoff_sha256', 'd' * 64),
                           ('request_id', 'other-request'), ('cwd', str(self.base / 'other')),
                           ('repository', 'example/other'), ('branch', 'codex/other'),
                           ('expected_revision', '2' * 40), ('environment_id', 'other-env'),
                           ('owner_id', 'other-owner'), ('tab_id', 'other-tab')]:
            changed = copy.deepcopy(record)
            changed['binding'][key] = value
            with self.subTest(field=key), self.store.lock(), self.assertRaises(records.CloudRecordError):
                self.store.save(changed)
        self.assertEqual(self.store.record_path.read_bytes(), before)

    def test_task_identity_and_monotonic_state_survive_collection(self):
        record = self.sent()
        record.update(submission='accepted', event='owner_review_recorded')
        with self.store.lock():
            self.store.save(record)
            for change in ({'task_id': 'different-task'},
                           {'submission': 'sent', 'event': 'submission_recorded'},
                           {'submission': 'unknown_outcome', 'event': 'submission_claimed'}):
                with self.subTest(change=change), self.assertRaises(records.CloudRecordError):
                    self.store.save({**record, **change})
            self.assertEqual(self.store.read(), record)

    def test_old_unknown_and_corrupt_claims_block_without_deletion_or_conversion(self):
        old = dict(schema_version=1, binding={**self.binding, 'wire_sha256': 'c' * 64},
                   submission='unknown_outcome', task_id=None, event='submission_claimed',
                   collection=None, owner_review=None, remote_status=None)
        unknown = dict(schema_version=99, binding=self.binding, submission='unknown_outcome',
                       task_id=None, event='submission_claimed')
        for index, content in enumerate((b'{', b'', json.dumps(old).encode(), json.dumps(unknown).encode(),
                                          b'{"schema_version":2,"schema_version":2}')):
            store = records.ClaimStore(self.base / f'broken-{index}', self.binding['request_id'])
            store.claim_path.write_bytes(content)
            with self.subTest(index=index), store.lock():
                for operation in (store.read, lambda: store.claim(self.binding)):
                    with self.assertRaises(RECORD_ERRORS):
                        operation()
                self.assertEqual(store.claim_path.read_bytes(), content)
                self.assertFalse(store.record_path.exists())

    def test_snapshot_without_claim_cannot_create_a_new_submission(self):
        self.store.record_path.write_bytes(b'{"schema_version":1}')
        before = self.store.record_path.read_bytes()
        with self.store.lock():
            for operation in (self.store.read, lambda: self.store.claim(self.binding)):
                with self.assertRaises(records.CloudRecordError):
                    operation()
        self.assertFalse(self.store.claim_path.exists())
        self.assertEqual(self.store.record_path.read_bytes(), before)

    def test_corrupt_or_scope_mismatched_snapshot_never_falls_back_to_original_claim(self):
        record = self.sent()
        mismatched = copy.deepcopy(record)
        mismatched['binding']['tab_id'] = 'changed-tab'
        claim_before = self.store.claim_path.read_bytes()
        for content in (b'{', json.dumps(mismatched).encode()):
            self.store.record_path.write_bytes(content)
            with self.store.lock():
                for operation in (self.store.read, lambda: self.store.claim(self.binding)):
                    with self.assertRaises(RECORD_ERRORS):
                        operation()
            self.assertEqual(self.store.record_path.read_bytes(), content)
            self.assertEqual(self.store.claim_path.read_bytes(), claim_before)

    def test_atomic_snapshot_failure_keeps_original_claim_and_blocks_resend(self):
        record = self.claim()
        updated = {**record, 'submission': 'sent', 'task_id': 'original-task',
                   'event': 'submission_recorded'}
        with self.store.lock():
            with patch.object(records.os, 'replace', side_effect=OSError('synthetic write failure')):
                with self.assertRaises(OSError):
                    self.store.save(updated)
            self.assertEqual(self.store.read(), record)
            with self.assertRaises(records.CloudRecordError):
                self.store.claim(self.binding)
        self.assertEqual(list(self.store.directory.glob('.cloud-write-*')), [])

    def test_private_storage_refuses_git_and_symlink_paths(self):
        checkout = self.base / 'checkout'
        checkout.mkdir()
        (checkout / '.git').write_text('synthetic repository marker', encoding='utf-8')
        with self.assertRaises(records.CloudRecordError):
            records.ClaimStore(checkout / 'state', self.binding['request_id'])
        link = self.base / 'state-link'
        link.symlink_to(self.store.root, target_is_directory=True)
        with self.assertRaises(RoutingError):
            records.ClaimStore(link, self.binding['request_id'])

    def test_lock_and_snapshot_reject_hardlink_aliases(self):
        record = self.sent()
        os.link(self.store.directory / '.lock', self.base / 'lock-alias')
        with self.assertRaises(records.CloudRecordError):
            with self.store.lock():
                self.fail('A hardlinked lock was accepted')
        (self.base / 'lock-alias').unlink()
        os.link(self.store.record_path, self.base / 'record-alias')
        with self.store.lock(), self.assertRaises(records.CloudRecordError):
            self.store.save(record)

    def test_state_metadata_rejects_false_schema_missing_task_and_invalid_events(self):
        record = self.claim()
        for changes in ({'schema_version': True}, {'schema_version': 1},
                        {'submission': 'sent', 'event': 'submission_recorded'},
                        {'submission': 'not_sent'}, {'submission': []},
                        {'event': 'command_timeout'}):
            with self.subTest(changes=changes), self.assertRaises(RECORD_ERRORS):
                records.validate_record({**record, **changes})
        self.assertEqual(hashlib.sha256(self.binding['request_id'].encode()).hexdigest(), self.store.key)


if __name__ == '__main__':
    unittest.main()
