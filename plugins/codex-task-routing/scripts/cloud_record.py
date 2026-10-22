"""Local Cloud UI/native claims; no tools, browsers, credentials or payloads."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat

from routing_core.common import _assert_safe_ancestors, _canonical_json, _load_json
from session_plan import (_digest, _enum, _identifier, _object, _revision, _text,
                          validate_native_target)


class CloudRecordError(ValueError):
    """Fixed public error codes only; never include private input or OS output."""


STATES = {"unknown_outcome", "sent", "collected", "accepted"}
EVENTS = {"unknown_outcome": "submission_claimed", "sent": "submission_recorded",
          "collected": "collection_recorded", "accepted": "owner_review_recorded"}


def private_directory(path, *, create=True):
    """Refuse links and Git checkouts; UI task metadata stays in private storage."""
    target = _assert_safe_ancestors(path)
    for parent in (target, *target.parents):
        if os.path.lexists(parent / '.git'):
            raise CloudRecordError("private_storage_required")
    if create:
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not target.is_dir():
        raise CloudRecordError("private_storage_unavailable")
    _assert_safe_ancestors(target)
    return target


def _sync_directory(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _exclusive(path, content):
    """A partial claim is deliberately retained: uncertainty never permits resend."""
    _assert_safe_ancestors(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise CloudRecordError('submission_already_claimed') from exc
    with os.fdopen(fd, 'wb') as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    _sync_directory(path.parent)


def _atomic(path, content):
    import secrets
    _assert_safe_ancestors(path)
    temporary = path.parent / ('.cloud-write-' + secrets.token_hex(12))
    try:
        _exclusive(temporary, content)
        if path.exists() and (not path.is_file() or os.stat(path).st_nlink != 1):
            raise CloudRecordError('unsafe_record')
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _encoded(value):
    return _canonical_json(value) + b'\n'


def validate_record(value):
    _object(value, {'schema_version', 'binding', 'submission', 'task_id', 'event'})
    # Old or unknown records remain blocking evidence; never migrate or erase them.
    if type(value['schema_version']) is not int or value['schema_version'] != 2:
        raise CloudRecordError('unsupported_record_schema')
    binding = value['binding']
    _object(binding, {'request_id', 'input_sha256', 'handoff_sha256', 'cwd', 'repository', 'branch',
                      'expected_revision', 'environment_id', 'owner_id', 'tab_id'})
    for name in ('request_id', 'environment_id', 'owner_id', 'tab_id'):
        _identifier(binding[name])
    for name in ('input_sha256', 'handoff_sha256'):
        _digest(binding[name])
    _revision(binding['expected_revision'])
    _text(binding['cwd'], maximum=4096)
    _text(binding['repository'])
    _text(binding['branch'])
    _enum(value['submission'], STATES)
    if value['event'] != EVENTS[value['submission']]:
        raise CloudRecordError('invalid_record')
    if value['task_id'] is not None:
        _identifier(value['task_id'])
    if value['submission'] != 'unknown_outcome' and value['task_id'] is None:
        raise CloudRecordError('invalid_record')
    return value


class ClaimStore:
    """Fixed-inode OS lock plus permanent exclusive intent and atomic snapshots."""
    record_schema = 2
    validator = staticmethod(validate_record)

    def __init__(self, directory, request_id):
        _identifier(request_id)
        self.request_id = request_id
        self.root = private_directory(directory)
        self.key = hashlib.sha256(request_id.encode('utf-8')).hexdigest()
        self.directory = private_directory(self.root / self.key)
        self.claim_path = self.directory / 'claim.json'
        self.record_path = self.directory / 'record.json'

    @contextmanager
    def lock(self):
        path = _assert_safe_ancestors(self.directory / '.lock')
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:
                raise CloudRecordError('unsafe_lock')
            try:
                if os.name == 'nt':
                    import msvcrt
                    if os.fstat(fd).st_size == 0:
                        os.write(fd, b'0')
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise CloudRecordError('request_busy') from exc
            yield
        finally:
            os.close(fd)

    def read(self):
        if not os.path.lexists(self.claim_path):
            if os.path.lexists(self.record_path):
                raise CloudRecordError('record_without_original_claim')
            return None
        # Damaged or partly written intent remains a blocking claim.
        claim = self.validator(_load_json(self.claim_path, 'cloud claim', limit=64 * 1024))
        record = (self.validator(_load_json(self.record_path, 'cloud record', limit=64 * 1024))
                  if os.path.lexists(self.record_path) else claim)
        if (claim['binding']['request_id'] != self.request_id
                or claim['binding'] != record['binding']):
            raise CloudRecordError('record_scope_mismatch')
        return record

    def claim(self, binding):
        record = dict(schema_version=self.record_schema, binding=binding, submission='unknown_outcome',
                      task_id=None if self.record_schema == 2 else binding['target']['task_id'],
                      event='submission_claimed')
        self.validator(record)
        if binding['request_id'] != self.request_id:
            raise CloudRecordError('record_scope_mismatch')
        if self.read() is not None:
            raise CloudRecordError('submission_already_claimed')
        _exclusive(self.claim_path, _encoded(record))
        return record

    def save(self, record):
        self.validator(record)
        original = self.read()
        if original is None or original['binding'] != record['binding']:
            raise CloudRecordError('original_claim_required')
        old, new = original['submission'], record['submission']
        order = {'unknown_outcome': 0, 'sent': 1, 'collected': 2, 'accepted': 3}
        if order[new] < order[old] or (original['task_id'] is not None and record['task_id'] != original['task_id']):
            raise CloudRecordError('record_cannot_rewind_or_change_task')
        _atomic(self.record_path, _encoded(record))


def validate_native_record(value):
    """Schema 3 never invents a tab identifier for a native task."""
    _object(value, {'schema_version', 'binding', 'submission', 'task_id', 'event'})
    if type(value['schema_version']) is not int or value['schema_version'] != 3:
        raise CloudRecordError('unsupported_record_schema')
    binding = value['binding']
    _object(binding, {'request_id', 'input_sha256', 'handoff_sha256', 'cwd', 'repository', 'branch',
                      'expected_revision', 'environment_id', 'owner_id', 'target', 'previous_request_id'})
    validate_native_target(binding['target'])
    for key in ('request_id', 'environment_id', 'owner_id'):
        _identifier(binding[key])
    if binding['previous_request_id'] is not None:
        _identifier(binding['previous_request_id'])
        if binding['previous_request_id'] == binding['request_id']:
            raise CloudRecordError('invalid_predecessor')
    for key in ('input_sha256', 'handoff_sha256'):
        _digest(binding[key])
    _revision(binding['expected_revision'])
    _text(binding['cwd'], maximum=4096)
    for key in ('repository', 'branch'):
        _text(binding[key])
    _enum(value['submission'], STATES)
    if (value['event'] != EVENTS[value['submission']]
            or value['task_id'] != binding['target']['task_id']):
        raise CloudRecordError('invalid_record')
    return value


class NativeClaimStore(ClaimStore):
    """One immutable claim per message, with a permanent per-task successor slot.

    Use lock() for each read/claim/save transaction. Only the fresh claim owner
    may make the one native call outside it, then save under lock. A crash
    between the successor slot and claim creation leaves blocking evidence; it
    never permits another request ID to hide an uncertain previous submission.
    """
    record_schema = 3
    validator = staticmethod(validate_native_record)

    def __init__(self, directory, request_id, target):
        validate_native_target(target)
        self.target = dict(target)
        super().__init__(directory, request_id)
        target_key = hashlib.sha256(_canonical_json(target)).hexdigest()
        # Reuse only the filesystem mutex implementation; no UI record is made.
        self._target_mutex = ClaimStore(self.root / 'native-targets', target_key)

    @contextmanager
    def lock(self):
        with self._target_mutex.lock(), super().lock():
            yield

    def read(self):
        record = super().read()
        if record is not None and record['binding']['target'] != self.target:
            raise CloudRecordError('record_scope_mismatch')
        return record

    def claim(self, binding):
        # Validate before reserving a successor. Invalid caller input consumes
        # nothing; any later storage failure remains deliberately blocking.
        proposed = dict(schema_version=3, binding=binding, submission='unknown_outcome',
                        task_id=self.target['task_id'], event='submission_claimed')
        self.validator(proposed)
        if binding['request_id'] != self.request_id or binding['target'] != self.target:
            raise CloudRecordError('record_scope_mismatch')
        if self.read() is not None:
            raise CloudRecordError('submission_already_claimed')
        previous = binding['previous_request_id']
        if previous is not None:
            prior = NativeClaimStore(self.root, previous, self.target).read()
            if prior is None or prior['submission'] not in {'collected', 'accepted'}:
                raise CloudRecordError('previous_request_unresolved')
        slot = hashlib.sha256(_canonical_json(previous)).hexdigest()
        _exclusive(self._target_mutex.directory / ('after-' + slot + '.json'),
                   _encoded({'request_id': self.request_id, 'target': self.target}))
        return super().claim(binding)
