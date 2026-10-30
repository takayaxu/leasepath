"""Minimal causal snapshot for D1 contract checks, not a pilot dataset exporter.

Input must be a validated, timestamp-ordered trace (or its observed prefix).
Tool/request state requires explicit idle_episode_id links; absent links remain
unknown. Only the features mapping may be passed to a predictor. Full source
replay policy callbacks and the remaining feature groups are still unimplemented.
"""
from math import isfinite


FEATURES = frozenset({
    'elapsed_idle_s', 'compatibility_id', 'prefix_tokens', 'tool_name',
    'tool_elapsed_s', 'phase', 'retry_count', 'is_queued', 'queue_length',
})


def validate_features(features):
    """Reject extra fields instead of silently accepting label/future payloads."""
    if set(features) != FEATURES:
        raise ValueError('feature allowlist mismatch')
    for key in ('elapsed_idle_s', 'tool_elapsed_s'):
        value = features[key]
        if value is None and key == 'tool_elapsed_s':
            continue
        if type(value) not in (int, float) or not isfinite(value) or value < 0:
            raise ValueError(f'invalid {key}')
    for key in ('prefix_tokens', 'retry_count', 'queue_length'):
        value = features[key]
        if value is None and key != 'prefix_tokens':
            continue
        if type(value) is not int or value < (1 if key == 'prefix_tokens' else 0):
            raise ValueError(f'invalid {key}')
    for key in ('compatibility_id', 'tool_name', 'phase'):
        value = features[key]
        if value is None and key != 'compatibility_id':
            continue
        if not isinstance(value, str) or not value:
            raise ValueError(f'invalid {key}')
    if features['is_queued'] is not None and type(features['is_queued']) is not bool:
        raise ValueError('invalid is_queued')


def snapshot(events, run_id, episode_id, as_of_ns, *, complete_service_events=False):
    """Project events through as_of_ns, inclusive; never validate/read future data.

    complete_service_events is a collector assertion that all server enqueue and
    dispatch events have been recorded from an empty initial queue. Without that
    assertion queue_length is unknown, rather than a false zero.
    """
    if type(as_of_ns) is not int or as_of_ns < 0:
        raise ValueError('invalid snapshot time')
    if type(complete_service_events) is not bool:
        raise ValueError('invalid service event completeness')
    start = None
    running = {}
    last_tool = None
    queue = set()
    associated_requests = set()
    request_state_known = False
    for event in events:
        if event['run_id'] != run_id:
            continue
        if event['ts_ns'] > as_of_ns:
            break
        kind, data = event['event_type'], event['data']
        linked = event.get('idle_episode_id') == episode_id
        if kind == 'observation_end':
            raise ValueError('snapshot at or after observation_end')
        if kind == 'idle_start' and linked:
            start = event
        if linked and kind in {'legal_demand', 'context_retire'}:
            raise ValueError('snapshot requires an open idle episode')
        if kind == 'tool_start' and linked:
            running[data['tool_id']] = event
            last_tool = data.get('tool_kind')
        if kind == 'tool_end' and linked:
            running.pop(data['tool_id'], None)
        if kind == 'request_enqueue':
            queue.add(event['request_id'])
            if linked:
                associated_requests.add(event['request_id'])
                request_state_known = True
        if kind == 'request_dispatch':
            queue.discard(event['request_id'])
            if linked:
                request_state_known = True
    if start is None:
        raise ValueError('idle episode has not started')
    queued = bool(queue & associated_requests) if request_state_known else None
    # Multiple running tools need dependency summaries, not an arbitrary tool.
    tool = next(iter(running.values())) if len(running) == 1 else None
    features = {
        'elapsed_idle_s': (as_of_ns - start['ts_ns']) / 1e9,
        'compatibility_id': start['data']['compatibility_id'],
        'prefix_tokens': len(start['data']['prefix_token_ids']),
        'tool_name': tool['data'].get('tool_kind') if tool else (last_tool if not running else None),
        'tool_elapsed_s': (as_of_ns - tool['ts_ns']) / 1e9 if tool else None,
        'phase': 'queued' if queued else 'waiting_tool' if running else None,
        'retry_count': start['data'].get('retry_count'),
        'is_queued': queued,
        'queue_length': len(queue) if complete_service_events else None,
    }
    validate_features(features)
    return {
        'run_id': run_id, 'idle_episode_id': episode_id,
        'snapshot_ns': as_of_ns, 'features': features,
    }
