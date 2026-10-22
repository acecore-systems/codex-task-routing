"""Synthetic native metadata, never an account or real Cloud task."""
from datetime import datetime, timezone
import copy
import session_plan as sp

NOW = datetime(2026, 10, 22, 2, tzinfo=timezone.utc)


def handoff():
    return dict(schema_version=2, request_id='native-request-1', input_sha256='a' * 64,
        owner_id='root-owner', return_to='root-task', repository='example/project',
        branch='codex/example', expected_revision='1' * 40, environment_id='environment-1',
        endpoint='Return the scoped read-only verification', acceptance_criteria=['Required checks pass'],
        required_checks=['python -m unittest'], allowed_operations=['read', 'test'],
        target=dict(kind='codex', host_id='durable', task_id='original-task'),
        previous_request_id=None, state=dict(submission='not_sent'))


def live(value):
    observed = sp.OBSERVED_CHECKS | {'target_identity', 'previous_submission'}
    return dict(schema_version=2, handoff_sha256=sp.native_handoff_sha256(value),
        transport='codex_cloud_native', actor=dict(role='root', depth=0, executor_id='root-owner'),
        observed_at=NOW.isoformat(), target=copy.deepcopy(value['target']),
        checks={key: dict(state='verified' if key in observed else 'permission_checked',
                          reference='Synthetic scoped observation or authorization') for key in sp.NATIVE_CHECKS})


def result(value):
    return dict(schema_version=2, request_id=value['request_id'], input_sha256=value['input_sha256'],
        handoff_sha256=sp.native_handoff_sha256(value), task_id=value['target']['task_id'],
        target=copy.deepcopy(value['target']), status='completed', repository=value['repository'],
        base_revision=value['expected_revision'], change_kind='read_only', output_revision=None,
        diff_evidence=[], checks_completed=value['required_checks'][:], verification_status='passed',
        verification_evidence=['artifact:actual-check-results'], artifact_references=['artifact:report'],
        response_reference='observed:original-task-response',
        correlation_evidence=['Observed request ID and input hash in this returned response'])


def binding(value, cwd):
    return dict(request_id=value['request_id'], input_sha256=value['input_sha256'],
        handoff_sha256=sp.native_handoff_sha256(value), cwd=str(cwd), repository=value['repository'],
        branch=value['branch'], expected_revision=value['expected_revision'],
        environment_id=value['environment_id'], owner_id=value['owner_id'],
        target=copy.deepcopy(value['target']), previous_request_id=value['previous_request_id'])
