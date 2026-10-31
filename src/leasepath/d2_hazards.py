"""Offline risk-interval targets; censoring masks unknown intervals."""
from collections import Counter
import math

RIGHT_EDGES = (.01, .03, .1, .3, 1, 3, 10, 30, 60, 120, 300, 600)


def derive_hazard_rows(samples, targets, right_edges=RIGHT_EDGES):
    edges = tuple(right_edges)
    if not edges or any(not math.isfinite(x) or x <= 0 for x in edges) or any(a >= b for a, b in zip(edges, edges[1:])):
        raise ValueError('time edges must be finite, positive and strictly increasing')
    labels = {r['sample_id']: r for r in targets}
    if len(labels) != len(targets) or len({s['sample_id'] for s in samples}) != len(samples):
        raise ValueError('duplicate sample or target identity')
    if set(labels) != {s['sample_id'] for s in samples}:
        raise ValueError('snapshot/target identity mismatch')
    rows = []
    for sample in samples:
        target = labels[sample['sample_id']]
        remaining, outcome = target['remaining_s'], target['outcome']
        if type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining <= 0:
            raise ValueError('remaining time must be positive and finite')
        if outcome not in {'legal_demand', 'retire', 'right_censored'}:
            raise ValueError('unknown observation outcome')
        left = 0.
        for index, right in enumerate(edges):
            row = {'sample_id': sample['sample_id'], 'run_id': sample['run_id'],
                   'idle_episode_id': sample['idle_episode_id'], 'interval_index': index,
                   'left_s': left, 'right_s': right, 'sample_weight': sample['sample_weight'],
                   'split': sample['split'], 'loss_mask': True}
            if outcome == 'right_censored' and remaining < right:
                row.update(label=None, loss_mask=False, reason='partial_interval_before_observation_end')
                rows.append(row)
                break
            if outcome != 'right_censored' and remaining <= right:
                row['label'] = 'U' if outcome == 'legal_demand' else 'D'
                rows.append(row)
                break
            row['label'] = 'waiting'
            rows.append(row)
            left = right
    return rows


def administrative_views(targets, horizons=(.01, .1, 1, 10)):
    """Explicit derived truncation view; never overwrite full observation labels."""
    rows = []
    for target in targets:
        for horizon in horizons:
            if not math.isfinite(horizon) or horizon <= 0:
                raise ValueError('invalid administrative horizon')
            remaining = target['remaining_s']
            event = target['outcome'] != 'right_censored' and remaining <= horizon
            rows.append({'sample_id': target['sample_id'], 'label_scope': 'administrative_horizon',
                         'horizon_s': horizon, 'observed_time_s': min(remaining, horizon),
                         'event_observed': event,
                         'outcome': target['outcome'] if event else 'right_censored',
                         'censor_reason': None if event else 'original_observation_end' if target['outcome'] == 'right_censored' and remaining <= horizon else 'fixed_horizon'})
    return rows


def summarize_horizons(targets, horizons=RIGHT_EDGES):
    output = []
    for horizon in horizons:
        counts = Counter()
        for t in targets:
            if t['outcome'] == 'right_censored':
                counts['known_waiting' if t['remaining_s'] >= horizon else 'unknown_after_censoring'] += 1
            elif t['remaining_s'] <= horizon:
                counts[t['outcome']] += 1
            else:
                counts['known_waiting'] += 1
        output.append({'horizon_s': horizon, 'counts': {k: counts[k] for k in
                       ['legal_demand', 'retire', 'known_waiting', 'unknown_after_censoring']}})
    return output
