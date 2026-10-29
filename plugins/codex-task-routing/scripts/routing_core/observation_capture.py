"""Capture only enrolled model-request counters from observation-only hooks."""
from __future__ import annotations

import copy
import re
from pathlib import Path
from datetime import datetime
from typing import Any
from .common import RoutingError
from .observation import (_observation_write_lock, _read_observation_state,
                          _write_observation_state, observation_sampling_key,
                          _observation_policy_metadata, _utc_now, _campaign_stats)
from .usage_metadata import _uuid

MAX_TURNS = 128
MAX_PAIRS = 1024


def _issue(capture: dict[str, Any], name: str) -> None:
    if name not in capture['issues']:
        capture['issues'].append(name)


def _counts(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, dict):
        return None
    fields = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens')
    if any(type(value.get(k)) is not int or value[k] < 0 for k in fields):
        return None
    inp, cached, out, reasoning, total = (value[k] for k in fields)
    extra = value.get('cache_write_input_tokens', 0)
    if type(extra) is not int or extra < 0 or extra > inp or cached > inp or reasoning > out or total != inp + out:
        return None
    return inp, cached, out, reasoning


def discard_unreadable_arm(*, codex_home: Path, policy: Any, root_id: str | None = None) -> None:
    """Disarm on an unreadable prompt event so it cannot attach to a later prompt."""
    key = observation_sampling_key(policy)
    state = _read_observation_state(codex_home, key)
    sample = state['samples'][key]
    if state['schema_version'] != 2 or sample['active_campaign'] is None:
        return
    with _observation_write_lock(codex_home, key):
        state = _read_observation_state(codex_home, key)
        sample = state['samples'][key]
        active = sample.get('active_campaign')
        if state['schema_version'] != 2 or active is None:
            return
        campaign = sample['campaigns'][active]
        arm = campaign['arm']
        if arm is not None and (root_id is None or arm['enrollment']['root_id'] == root_id):
            campaign['arm'] = None
            campaign['arm_failure'] = 'unreadable_prompt_event'
            _write_observation_state(codex_home, key, state)


def fire_arm(*, codex_home: Path, policy: Any, event: dict[str, Any], snapshot_reader: Any) -> str:
    """Consume an explicit arm at the next prompt, without inspecting its text."""
    if event.get('hook_event_name') != 'UserPromptSubmit':
        return 'ignored'
    try:
        root = _uuid(event.get('session_id'))
    except RoutingError:
        discard_unreadable_arm(codex_home=codex_home, policy=policy)
        return 'ignored'
    turn_id = event.get('turn_id')
    if not isinstance(turn_id, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', turn_id):
        discard_unreadable_arm(codex_home=codex_home, policy=policy, root_id=root)
        return 'ignored'
    key = observation_sampling_key(policy)
    initial = _read_observation_state(codex_home, key)
    sample = initial['samples'][key]
    active = sample.get('active_campaign')
    if initial['schema_version'] != 2 or active is None:
        return 'ignored'
    arm = sample['campaigns'][active]['arm']
    if arm is None or arm['enrollment']['root_id'] != root:
        return 'ignored'
    with _observation_write_lock(codex_home, key):
        state = _read_observation_state(codex_home, key)
        sample = state['samples'][key]
        active = sample.get('active_campaign')
        if state['schema_version'] != 2 or active is None:
            return 'ignored'
        campaign = sample['campaigns'][active]
        arm = campaign['arm']
        if arm is None or arm['enrollment']['root_id'] != root:
            return 'ignored'
        stats = _campaign_stats(campaign)
        if stats['attempts'] >= 6 or stats['measured'] + stats['pending'] >= 3:
            return 'ignored'
        task_id = arm['task_id']
        enrollment = copy.deepcopy(arm['enrollment'])
        enrollment['start_evidence_ref'] = 'user-prompt-submit:' + turn_id
        now = _utc_now()
        campaign['tasks'][task_id] = {
            'task_id': task_id, 'sampling_key': key, 'policy': _observation_policy_metadata(policy, key),
            'state': 'ongoing', 'measurement': {'state': 'missing', 'reason': 'completion is not recorded'},
            'started_at': now, 'updated_at': now, 'completed_at': None, 'scope': None,
            'rework': None, 'verification': None, 'runs': []}
        campaign['classifications'][task_id] = {'state': 'provisional', 'evidence_ref': None}
        campaign['enrollments'][task_id] = enrollment
        campaign['captures'][task_id] = {'threads': {}, 'issues': [], 'root_stopped': False, 'baselines': {},
            'verification': {'state': 'missing', 'first': {}, 'second': {}, 'reason': 'not verified', 'continuity_ref': None}}
        campaign['arm'] = None
        # Consume the arm durably before the external DB read: a crash cannot reuse it on another prompt.
        _write_observation_state(codex_home, key, state)
        try:
            baseline = snapshot_reader(enrollment, root)
            campaign['captures'][task_id]['baselines'][root] = baseline
        except (RoutingError, OSError, ValueError):
            _issue(campaign['captures'][task_id], 'invalid_event')
        _write_observation_state(codex_home, key, state)
    return 'started'


def record_event(*, codex_home: Path, policy: Any, event: dict[str, Any], child_reader: Any = None) -> str:
    """Write only counters and bounded identities; no model content is accessed."""
    kind = event.get('hook_event_name')
    if kind not in {'PreModelRequest', 'PostModelRequest', 'Stop', 'SubagentStart', 'SubagentStop'}:
        return 'ignored'
    try:
        session_id = _uuid(event.get('session_id'))
        agent = event.get('agent_id')
        invalid_agent = False
        if agent is not None:
            try:
                agent = _uuid(agent)
            except RoutingError:
                invalid_agent = True
                agent = None
        turn_id = event.get('turn_id')
        if not isinstance(turn_id, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', turn_id):
            return 'ignored'
    except RoutingError:
        return 'ignored'
    key = observation_sampling_key(policy)
    initial = _read_observation_state(codex_home, key)
    if initial['schema_version'] != 2 or initial['samples'][key]['active_campaign'] is None:
        return 'ignored'
    with _observation_write_lock(codex_home, key):
        state = _read_observation_state(codex_home, key)
        sample = state['samples'][key]
        campaign_id = sample.get('active_campaign')
        if state['schema_version'] != 2 or campaign_id is None:
            return 'ignored'
        campaign = sample['campaigns'][campaign_id]
        candidates = []
        for task_id, enrollment in campaign['enrollments'].items():
            capture = campaign['captures'][task_id]
            if capture['root_stopped'] or (campaign['tasks'][task_id]['state'] != 'ongoing' and not capture['threads']):
                continue
            known = {row['thread_id'] for row in enrollment['threads']}
            if session_id in known or (agent is not None and agent in known):
                candidates.append((task_id, enrollment))
        if len(candidates) != 1:
            return 'ignored'
        task_id, enrollment = candidates[0]
        capture = campaign['captures'][task_id]
        if capture['verification']['state'] != 'missing':
            capture['verification'] = {'state': 'missing', 'first': {}, 'second': {}, 'reason': 'new event after verification', 'continuity_ref': None}
        known = {row['thread_id']: row for row in enrollment['threads']}
        root = enrollment['root_id']
        if invalid_agent:
            _issue(capture, 'invalid_event')
        elif kind == 'SubagentStart':
            if agent is None:
                _issue(capture, 'invalid_event')
            elif agent not in known:
                parent = known.get(session_id)
                if not enrollment.get('allow_descendants', False) or parent is None or parent['depth'] >= 2 or child_reader is None:
                    _issue(capture, 'unregistered_child')
                elif len(known) >= 32:
                    _issue(capture, 'event_limit')
                else:
                    candidate = copy.deepcopy(enrollment)
                    candidate['threads'].append({'thread_id': agent, 'parent_id': session_id, 'depth': parent['depth'] + 1})
                    try:
                        child_snapshot = child_reader(candidate, agent)
                        if not isinstance(child_snapshot, dict) or child_snapshot.get('total_tokens') != 0:
                            raise RoutingError('child baseline is not zero')
                    except (RoutingError, OSError, ValueError):
                        _issue(capture, 'child_registration_failed')
                    else:
                        campaign['enrollments'][task_id] = candidate
                        enrollment = candidate
                        known[agent] = candidate['threads'][-1]
                        capture['baselines'][agent] = child_snapshot
            if agent is not None and agent in known:
                if agent not in capture['baselines'] and child_reader is not None:
                    try:
                        child_snapshot = child_reader(enrollment, agent)
                        if not isinstance(child_snapshot, dict) or child_snapshot.get('total_tokens') != 0:
                            raise RoutingError('child baseline is not zero')
                        capture['baselines'][agent] = child_snapshot
                    except (RoutingError, OSError, ValueError):
                        _issue(capture, 'child_registration_failed')
                if known[agent]['parent_id'] != session_id or agent == root:
                    _issue(capture, 'invalid_event')
                elif agent in capture['threads'] and capture['threads'][agent]['started']:
                    _issue(capture, 'ambiguous_pair')
                else:
                    capture['threads'].setdefault(agent, {'turns': {}, 'started': False, 'stopped': False})['started'] = True
        elif kind == 'Stop':
            if session_id == root and agent is None:
                capture['root_stopped'] = True
            else:
                _issue(capture, 'invalid_event')
        elif kind == 'SubagentStop':
            if agent is None or agent not in known or agent == root:
                _issue(capture, 'unregistered_child')
            elif agent in capture['threads']:
                capture['threads'][agent]['stopped'] = True
            else:
                _issue(capture, 'invalid_event')
        else:
            thread_id = agent or session_id
            if thread_id not in known:
                _issue(capture, 'unregistered_child')
            else:
                threads = capture['threads']
                if thread_id not in threads:
                    threads[thread_id] = {'turns': {}, 'started': thread_id == root, 'stopped': False}
                    if thread_id != root:
                        _issue(capture, 'invalid_event')
                thread = threads[thread_id]
                turns = thread['turns']
                if turn_id not in turns and len(turns) >= MAX_TURNS:
                    _issue(capture, 'event_limit')
                else:
                    turn = turns.setdefault(turn_id, {'pending': False, 'pairs': 0, 'input_tokens': 0,
                        'cache_input_tokens': 0, 'output_tokens': 0, 'reasoning_tokens': 0})
                    if kind == 'PreModelRequest':
                        if turn['pending']:
                            _issue(capture, 'ambiguous_pair')
                        else:
                            turn['pending'] = True
                        thread['stopped'] = False
                        if thread_id == root:
                            capture['root_stopped'] = False
                    else:
                        values = _counts(event.get('token_usage'))
                        if not turn['pending']:
                            _issue(capture, 'ambiguous_pair')
                        elif event.get('status') != 'completed' or values is None:
                            turn['pending'] = False
                            _issue(capture, 'invalid_event')
                        elif turn['pairs'] >= MAX_PAIRS:
                            turn['pending'] = False
                            _issue(capture, 'event_limit')
                        else:
                            turn['pending'] = False
                            turn['pairs'] += 1
                            for name, value in zip(('input_tokens', 'cache_input_tokens', 'output_tokens', 'reasoning_tokens'), values):
                                turn[name] += value
        _write_observation_state(codex_home, key, state)
    return 'recorded'


def _capture_task(state: dict[str, Any], key: str, campaign_id: str, task_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    sample = state['samples'][key]
    if state['schema_version'] != 2 or campaign_id not in sample['campaigns']:
        raise RoutingError('campaign is unavailable')
    campaign = sample['campaigns'][campaign_id]
    if task_id not in campaign['tasks'] or task_id not in campaign['enrollments'] or task_id not in campaign['captures']:
        raise RoutingError('capture is unavailable')
    return campaign, campaign['enrollments'][task_id], campaign['captures'][task_id]


def record_baseline(*, codex_home: Path, sampling_key: str, campaign_id: str, task_id: str,
                    snapshot_reader: Any) -> dict[str, Any]:
    with _observation_write_lock(codex_home, sampling_key):
        state = _read_observation_state(codex_home, sampling_key)
        campaign, enrollment, capture = _capture_task(state, sampling_key, campaign_id, task_id)
        root = enrollment['root_id']
        if campaign['tasks'][task_id]['state'] != 'ongoing' or capture['threads']:
            raise RoutingError('root baseline must precede recorded activity')
        if root in capture['baselines']:
            return {'state': 'existing', 'thread_id': root}
        snapshot = snapshot_reader(enrollment, root)
        capture['baselines'][root] = snapshot
        _write_observation_state(codex_home, sampling_key, state)
    return {'state': 'recorded', 'thread_id': root}


def verify_capture(*, codex_home: Path, sampling_key: str, campaign_id: str, task_id: str,
                   snapshot_reader: Any, continuity_ref: str | None = None) -> dict[str, Any]:
    """Cross-check hook totals against two stable post-stop cumulative reads."""
    with _observation_write_lock(codex_home, sampling_key):
        state = _read_observation_state(codex_home, sampling_key)
        campaign, enrollment, capture = _capture_task(state, sampling_key, campaign_id, task_id)
        previous = copy.deepcopy(capture['verification']['first'])
        # Persist invalidation before any I/O so a crash cannot retain an old success.
        capture['verification'] = {'state': 'pending', 'first': previous, 'second': {},
                                   'reason': 'verification_in_progress', 'continuity_ref': None}
        _write_observation_state(codex_home, sampling_key, state)
        known = {row['thread_id']: row for row in enrollment['threads']}
        def failed(reason: str) -> dict[str, Any]:
            capture['verification'] = {'state': 'missing', 'first': {}, 'second': {}, 'reason': reason, 'continuity_ref': None}
            _write_observation_state(codex_home, sampling_key, state)
            return {'state': 'partial', 'reason': reason}
        if continuity_ref is not None and (not isinstance(continuity_ref, str) or not continuity_ref.strip() or len(continuity_ref) > 4096 or any(ord(c) < 32 for c in continuity_ref)):
            return failed('continuity_evidence_invalid')
        if (capture['issues'] or not capture['root_stopped'] or not enrollment.get('start_evidence_ref')
                or set(capture['threads']) != set(known) or set(capture['baselines']) != set(known)):
            return failed('capture_or_baseline_incomplete')
        for thread_id, thread in capture['threads'].items():
            if (thread_id != enrollment['root_id'] and (not thread['started'] or not thread['stopped'])):
                return failed('child_lifecycle_incomplete')
            if not thread['turns'] or any(turn['pending'] or not turn['pairs'] for turn in thread['turns'].values()):
                return failed('request_pair_incomplete')
        try:
            current = {thread_id: snapshot_reader(enrollment, thread_id) for thread_id in known}
        except (RoutingError, OSError, ValueError):
            return failed('snapshot_unavailable')
        comparable = ('thread_id', 'parent_id', 'depth', 'configured_model', 'configured_effort',
                      'db_file_id', 'db_schema_version', 'db_user_version', 'cli_version')
        mismatch = False
        for thread_id, snapshot in current.items():
            baseline = capture['baselines'][thread_id]
            try:
                if datetime.fromisoformat(snapshot['observed_at']) < datetime.fromisoformat(baseline['observed_at']):
                    return failed('snapshot_time_reversed')
            except (TypeError, ValueError):
                return failed('snapshot_time_invalid')
            if any(snapshot[name] != baseline[name] for name in comparable):
                return failed('snapshot_identity_or_settings_changed')
            total = sum(t['input_tokens'] + t['output_tokens'] for t in capture['threads'][thread_id]['turns'].values())
            if snapshot['total_tokens'] < baseline['total_tokens'] or snapshot['total_tokens'] - baseline['total_tokens'] != total:
                mismatch = True
        stable = set(previous) == set(current)
        if stable:
            for thread_id, snapshot in current.items():
                prior = previous[thread_id]
                try:
                    delta = (datetime.fromisoformat(snapshot['observed_at']) - datetime.fromisoformat(prior['observed_at'])).total_seconds()
                except (ValueError, TypeError):
                    delta = -1
                if (delta < 1 or snapshot['total_tokens'] != prior['total_tokens']
                        or any(snapshot[name] != prior[name] for name in comparable)):
                    stable = False
                    break
        if stable and not mismatch:
            capture['verification'] = {'state': 'verified' if continuity_ref else 'matched',
                                       'first': previous, 'second': current,
                                       'reason': None if continuity_ref else 'continuity_unverified',
                                       'continuity_ref': continuity_ref}
        else:
            capture['verification'] = {'state': 'pending', 'first': current, 'second': {},
                                       'reason': 'counter_mismatch' if mismatch else 'awaiting_stable_read', 'continuity_ref': None}
        _write_observation_state(codex_home, sampling_key, state)
        return {'state': capture['verification']['state'], 'reason': capture['verification']['reason']}


def verify_stopped_task(*, codex_home: Path, policy: Any, session_id: str, turn_id: str,
                        snapshot_reader: Any, sleep: Any) -> str:
    """Freeze a matched result within a bounded Stop hook, with no new model call."""
    key = observation_sampling_key(policy)
    state = _read_observation_state(codex_home, key)
    sample = state['samples'][key]
    campaign_id = sample.get('active_campaign')
    if state['schema_version'] != 2 or campaign_id is None:
        return 'ignored'
    campaign = sample['campaigns'][campaign_id]
    candidates = []
    for task_id, enrollment in campaign['enrollments'].items():
        capture = campaign['captures'][task_id]
        root_thread = capture['threads'].get(session_id, {})
        if (enrollment['root_id'] == session_id and capture['root_stopped']
                and turn_id in root_thread.get('turns', {})):
            candidates.append(task_id)
    if len(candidates) != 1:
        return 'ignored'
    for index in range(3):
        if index:
            sleep(1.05)
        result = verify_capture(codex_home=codex_home, sampling_key=key, campaign_id=campaign_id,
                                task_id=candidates[0], snapshot_reader=snapshot_reader)
        if result['state'] == 'matched':
            return 'matched'
        if result['state'] == 'partial' and result.get('reason') != 'snapshot_unavailable':
            break
        if result['state'] not in {'pending', 'partial'}:
            break
    return 'partial'


def certify_capture(*, codex_home: Path, sampling_key: str, campaign_id: str,
                    task_id: str, continuity_ref: str) -> dict[str, Any]:
    if not isinstance(continuity_ref, str) or not continuity_ref.strip() or len(continuity_ref) > 4096 or any(ord(c) < 32 for c in continuity_ref):
        raise RoutingError('counter continuity evidence is required')
    with _observation_write_lock(codex_home, sampling_key):
        state = _read_observation_state(codex_home, sampling_key)
        campaign, enrollment, capture = _capture_task(state, sampling_key, campaign_id, task_id)
        verification = capture['verification']
        if verification['state'] != 'matched' or not capture['root_stopped']:
            raise RoutingError('stable counter match is unavailable')
        verification.update(state='verified', reason=None, continuity_ref=continuity_ref)
        _write_observation_state(codex_home, sampling_key, state)
    return {'state': 'verified'}


def export_capture(enrollment: dict[str, Any], capture: dict[str, Any], *, evidence_ref: str,
                   coverage_ref: str | None = None, work_completed_ref: str | None = None) -> dict[str, Any]:
    if not isinstance(evidence_ref, str) or not evidence_ref.strip() or len(evidence_ref) > 4096 or any(ord(c) < 32 for c in evidence_ref):
        raise RoutingError('capture evidence is invalid')
    if coverage_ref is not None and (not isinstance(coverage_ref, str) or not coverage_ref.strip() or len(coverage_ref) > 4096 or any(ord(c) < 32 for c in coverage_ref)):
        raise RoutingError('coverage evidence is invalid')
    if work_completed_ref is not None and (not isinstance(work_completed_ref, str) or not work_completed_ref.strip() or len(work_completed_ref) > 4096 or any(ord(c) < 32 for c in work_completed_ref)):
        raise RoutingError('work completion evidence is invalid')
    known = {row['thread_id']: row for row in enrollment['threads']}
    issues = list(capture['issues'])
    if not capture['root_stopped']:
        issues.append('root_stop_unobserved')
    if not enrollment.get('start_evidence_ref'):
        issues.append('start_boundary_unverified')
    if not coverage_ref:
        issues.append('event_coverage_unverified')
    if not work_completed_ref:
        issues.append('work_completion_unverified')
    if capture['verification']['state'] != 'verified':
        issues.append('counter_crosscheck_unverified')
    if set(capture['threads']) != set(known):
        issues.append('enrolled_thread_unobserved')
    for thread_id, thread in capture['threads'].items():
        if thread_id != enrollment['root_id'] and (not thread['started'] or not thread['stopped']):
            issues.append('child_lifecycle_unobserved')
        if not thread['turns'] or any(turn['pending'] or turn['pairs'] == 0 for turn in thread['turns'].values()):
            issues.append('request_pair_incomplete')
    full = not issues
    output = []
    for thread_id, thread in capture['threads'].items():
        for turn_id, turn in thread['turns'].items():
            if turn['pairs'] == 0:
                continue
            output.append({'thread_id': thread_id, 'turn_id': turn_id, 'parent_id': known[thread_id]['parent_id'],
                'relationship': ('parent', 'child', 'grandchild')[known[thread_id]['depth']],
                'usage': {'basis': 'hook_request_counters', 'state': 'measured', 'reason': None,
                          'evidence_ref': evidence_ref, 'turn_scope': 'full_turn' if full else 'partial_turn',
                          'input_tokens': turn['input_tokens'], 'cache_input_tokens': turn['cache_input_tokens'],
                          'output_tokens': turn['output_tokens'], 'reasoning_tokens': turn['reasoning_tokens'],
                          'unknown_reasons': {}}})
    return {'state': 'measured' if full else 'partial', 'reasons': sorted(set(issues)),
            'runtime_model_verified': False, 'coverage_evidence_ref': coverage_ref,
            'work_completed_evidence_ref': work_completed_ref, 'runs': output}
