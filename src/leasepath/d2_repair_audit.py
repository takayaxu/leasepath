"""Independent consistency audit of measured test-first repair observations."""
from .d2_repair import SUITE_ID


def audit_repair(raw, workflow, initial_hash):
    starts, ends, suites, chains = {}, {}, [], []
    pending = None
    attempts = retries = failed_tests = 0
    for row in raw:
        if row.get('workflow_id') != workflow:
            continue
        kind = row['kind']
        if kind == 'tool_start':
            tid = row['tool_id']
            if tid in starts:
                raise ValueError('duplicate tool start')
            if row['prior_failed_tests'] != failed_tests:
                raise ValueError('failed test counter not causal')
            starts[tid] = row
            if row.get('test_suite_id') == SUITE_ID:
                attempts += 1
                retries += pending is not None
                if row['suite_attempt_no'] != attempts or row['suite_retries_started'] != retries:
                    raise ValueError('suite attempt/retry counters disagree')
                expected = pending['tool_id'] if pending else None
                changed = (row['source_sha256'] != pending['source_sha256']) if pending else None
                if row.get('retry_of_tool_id') != expected or row.get('source_changed_since_failure') != changed:
                    raise ValueError('retry link has no prior observed failed suite or wrong source change')
                suites.append(row)
        elif kind == 'tool_end':
            tid = row['tool_id']
            if tid not in starts or tid in ends or starts[tid]['ts_ns'] > row['ts_ns']:
                raise ValueError('unpaired or reordered tool end')
            start = starts[tid]
            if row['request_id'] != start['request_id']:
                raise ValueError('tool request association changed')
            ends[tid] = row
            if start['command_category'] == 'test' and row['returncode'] != 0:
                failed_tests += 1
            if start.get('test_suite_id') == SUITE_ID:
                if row['returncode'] == 0 and pending and start['source_changed_since_failure']:
                    edits = [e for e in ends.values() if pending['ts_ns'] < e['ts_ns'] < start['ts_ns'] and e['source_changed']]
                    if edits and start['source_sha256'] == row['source_sha256']:
                        chains.append({'failed_tool_id': pending['tool_id'], 'retry_tool_id': tid,
                                       'failed_source_sha256': pending['source_sha256'],
                                       'repaired_source_sha256': row['source_sha256'],
                                       'failure_end_ns': pending['ts_ns'], 'retry_start_ns': start['ts_ns'],
                                       'retry_end_ns': row['ts_ns'], 'source_edit_tool_ids': [e['tool_id'] for e in edits],
                                       'failed_duration_s': pending['duration_ns']/1e9,
                                       'retry_duration_s': row['duration_ns']/1e9})
                pending = row if row['returncode'] != 0 else None
    if set(starts) != set(ends):
        raise ValueError('tool start has no end')
    initial = suites[0] if suites else None
    initial_end = ends[initial['tool_id']] if initial else None
    reproduced = bool(initial and initial['source_sha256'] == initial_hash
                      and initial_end['source_sha256'] == initial_hash and initial_end['returncode'] == 1
                      and not any(e['source_changed'] and e['ts_ns'] < initial['ts_ns'] for e in ends.values()))
    return {'tool_pairs': len(starts), 'full_suite_attempts': attempts, 'suite_retries': retries,
            'failed_test_calls': failed_tests, 'initial_unchanged_defect_reproduced': reproduced,
            'source_repair_then_successful_retry': bool(chains), 'chains': chains}
