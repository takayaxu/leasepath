"""Causal extra features for designed execution collection, schema expansion-v1.

Kept separate from prediction_state.FEATURES and the frozen GBDT. Raw payloads
are accepted only by this collector projection; model inputs are an allowlist.
"""
EXTRA_FEATURES = frozenset({
    'command_category', 'workload_family', 'work_units', 'work_unit',
    'tool_timeout_s', 'prior_failed_tests', 'completed_tool_count',
    'last_completed_tool_s', 'last_completed_returncode', 'tool_state',
})


def project_extra(raw, workflow, episode, as_of_ns):
    """Read observed prefixes only. Failure count means failed tests, not retries."""
    running, completed = {}, []
    failed_tests = 0
    rid = episode.removeprefix('idle-')
    started = {}
    episode_ended = False
    for row in raw:
        if row.get('workflow_id') != workflow:
            continue
        if row['ts_ns'] > as_of_ns:
            break
        if row['kind'] == 'tool_start':
            started[row['tool_id']] = row
            if row.get('request_id') == rid:
                running[row['tool_id']] = row
        elif row['kind'] == 'tool_end':
            tool = started.get(row['tool_id'])
            if tool is None:
                raise ValueError('tool end without observed start')
            completed.append((tool, row))
            if tool.get('command_category') == 'test' and row['returncode'] != 0:
                failed_tests += 1
            if row['tool_id'] in running:
                running.pop(row['tool_id'])
                episode_ended = True
    current = next(iter(running.values())) if len(running) == 1 else None
    last = completed[-1] if completed else None
    features = {
        'command_category': current.get('command_category') if current else None,
        'workload_family': current.get('workload_family') if current else None,
        'work_units': current.get('work_units') if current else None,
        'work_unit': current.get('work_unit') if current else None,
        'tool_timeout_s': current.get('timeout_s') if current else None,
        'prior_failed_tests': failed_tests,
        'completed_tool_count': len(completed),
        'last_completed_tool_s': (last[1]['ts_ns'] - last[0]['ts_ns']) / 1e9 if last else None,
        'last_completed_returncode': last[1]['returncode'] if last else None,
        'tool_state': 'running' if running else 'completed' if episode_ended else None,
    }
    assert set(features) == EXTRA_FEATURES
    return features
