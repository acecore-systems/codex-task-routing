"""Synthetic schema-1 records representing work reserved by an older release."""
def seed_legacy(routing, home, policy, task_id, kind='normal'):
    from routing_core.observation import (_read_observation_state, _write_observation_state,
        _observation_write_lock, _observation_policy_metadata, observation_sampling_key)
    if kind != 'normal':
        return routing.observation_start(codex_home=home, policy=policy, task_id=task_id, kind=kind)
    key = observation_sampling_key(policy)
    with _observation_write_lock(home, key):
        state = _read_observation_state(home, key)
        tasks = state['samples'][key]['tasks']
        if task_id in tasks:
            return {'state': 'existing', 'recorded': True, 'sampling_key': key}
        if len(tasks) == 3:
            return {'state': 'full', 'recorded': False, 'sampling_key': key}
        tasks[task_id] = {
            'task_id': task_id, 'sampling_key': key, 'policy': _observation_policy_metadata(policy, key),
            'state': 'ongoing', 'measurement': {'state': 'missing', 'reason': 'not complete'},
            'started_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:00:00Z',
            'completed_at': None, 'scope': None, 'rework': None, 'verification': None, 'runs': [],
        }
        _write_observation_state(home, key, state)
    return {'state': 'started', 'recorded': True, 'sampling_key': key}
