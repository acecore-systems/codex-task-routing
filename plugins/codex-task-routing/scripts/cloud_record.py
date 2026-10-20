"""Durable local claims for explicit cloud transport; no credentials or payloads."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat

from routing_core.common import _assert_safe_ancestors, _canonical_json, _load_json
from session_plan import _digest, _identifier, _object, _schema, _revision, _text, _truth


class CloudRunError(ValueError):
    """Fixed public error codes only; never include input, argv or native output."""


STATES = {"unknown_outcome", "sent", "collected", "accepted"}
EVENTS = {"submission_claimed", "submission_recorded", "response_unrecognized", "command_failed",
          "command_timeout", "command_unavailable", "collection_pending", "collection_recorded",
          "collection_failed", "owner_review_recorded"}


def private_directory(path, *, create=True):
    """Refuse links and Git checkouts; this store can contain a private diff."""
    target = _assert_safe_ancestors(path)
    for parent in (target, *target.parents):
        if os.path.lexists(parent / '.git'):
            raise CloudRunError("private_storage_required")
    if create:
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not target.is_dir():
        raise CloudRunError("private_storage_unavailable")
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
        raise CloudRunError('submission_already_claimed') from exc
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
            raise CloudRunError('unsafe_record')
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _encoded(value):
    return _canonical_json(value) + b'\n'


def validate_record(value):
    _object(value, {'schema_version', 'binding', 'submission', 'task_id', 'event', 'collection', 'owner_review', 'remote_status'})
    _schema(value['schema_version'])
    binding = value['binding']
    _object(binding, {'request_id', 'input_sha256', 'handoff_sha256', 'cwd', 'repository', 'branch',
                      'expected_revision', 'environment_id', 'owner_id', 'wire_sha256'})
    for name in ('request_id', 'environment_id', 'owner_id'):
        _identifier(binding[name])
    for name in ('input_sha256', 'handoff_sha256', 'wire_sha256'):
        _digest(binding[name])
    _revision(binding['expected_revision'])
    _text(binding['cwd'], maximum=4096)
    _text(binding['repository'])
    _text(binding['branch'])
    if value['remote_status'] not in {None, 'running', 'completed', 'failed', 'applied'}:
        raise CloudRunError('invalid_remote_status')
    if value['submission'] not in STATES or value['event'] not in EVENTS:
        raise CloudRunError('invalid_record')
    if value['task_id'] is not None:
        _identifier(value['task_id'])
    if value['submission'] != 'unknown_outcome' and value['task_id'] is None:
        raise CloudRunError('invalid_record')
    if value['collection'] is not None:
        _object(value['collection'], {'remote_status', 'attempt', 'diff_sha256', 'diff_bytes', 'diff_file',
                                      'verification_status'})
        entry = value['collection']
        if (entry['remote_status'] not in {'queued', 'running', 'completed', 'failed', 'unknown', 'applied'}
                or entry['attempt'] != 1 or type(entry['attempt']) is not int
                or entry['verification_status'] != 'unverified'):
            raise CloudRunError('invalid_collection_record')
        _digest(entry['diff_sha256'])
        if type(entry['diff_bytes']) is not int or entry['diff_bytes'] < 0:
            raise CloudRunError('invalid_collection_record')
        expected = 'diff-1-' + entry['diff_sha256'] + '.patch'
        if entry['diff_file'] != expected:
            raise CloudRunError('invalid_collection_record')
    if value['owner_review'] is not None:
        _object(value['owner_review'], {'outcome', 'decision', 'criteria_checked'})
        _truth(value['owner_review']['criteria_checked'])
        if (value['owner_review']['outcome'] not in {'completed', 'failed', 'partial', 'unverified', 'needs_changes'}
                or value['owner_review']['decision'] not in {'accepted', 'needs_changes', 'unverified'}
                or value['owner_review']['criteria_checked'] not in (True, False, None)):
            raise CloudRunError('invalid_review_record')
    return value


class ClaimStore:
    """Fixed-inode OS lock plus permanent exclusive intent and atomic snapshots."""
    def __init__(self, directory, request_id):
        _identifier(request_id)
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
                raise CloudRunError('unsafe_lock')
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
                raise CloudRunError('request_busy') from exc
            yield
        finally:
            os.close(fd)

    def read(self):
        if not self.claim_path.exists():
            if self.record_path.exists():
                raise CloudRunError('record_without_original_claim')
            return None
        # Damaged or partly written intent remains a blocking claim.
        claim = validate_record(_load_json(self.claim_path, 'cloud claim', limit=64 * 1024))
        record = (validate_record(_load_json(self.record_path, 'cloud record', limit=64 * 1024))
                  if self.record_path.exists() else claim)
        if claim['binding'] != record['binding']:
            raise CloudRunError('record_scope_mismatch')
        return record

    def claim(self, binding):
        record = dict(schema_version=1, binding=binding, submission='unknown_outcome', task_id=None,
                      event='submission_claimed', collection=None, owner_review=None, remote_status=None)
        validate_record(record)
        _exclusive(self.claim_path, _encoded(record))
        return record

    def save(self, record):
        validate_record(record)
        original = self.read()
        if original is None or original['binding'] != record['binding']:
            raise CloudRunError('original_claim_required')
        old, new = original['submission'], record['submission']
        order = {'unknown_outcome': 0, 'sent': 1, 'collected': 2, 'accepted': 3}
        if order[new] < order[old] or (original['task_id'] is not None and record['task_id'] != original['task_id']):
            raise CloudRunError('record_cannot_rewind_or_change_task')
        _atomic(self.record_path, _encoded(record))

    def save_diff(self, data):
        if not isinstance(data, bytes):
            raise CloudRunError('invalid_diff_bytes')
        digest = hashlib.sha256(data).hexdigest()
        name = 'diff-1-' + digest + '.patch'
        path = self.directory / name
        if path.exists():
            _assert_safe_ancestors(path)
            if not path.is_file() or os.stat(path).st_nlink != 1 or path.read_bytes() != data:
                raise CloudRunError('diff_artifact_mismatch')
        else:
            _exclusive(path, data)
        return dict(diff_sha256=digest, diff_bytes=len(data), diff_file=name)
