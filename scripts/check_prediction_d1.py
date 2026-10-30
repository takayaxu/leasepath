"""Reproduce prediction D1's synthetic contract checks and export their evidence."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.contract_fixtures import expand_cases, check_case
from leasepath.prediction_state import snapshot, validate_features
from leasepath.source_replay import run
from leasepath.trace import derive_idle_labels, read_events, validate_events

FIXTURE = ROOT / 'data/fixtures/prediction_d1_acceptance.json'


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))


def run_checks(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(FIXTURE.read_text())
    original = json.loads((ROOT / 'data/fixtures/event_contract.json').read_text())
    results = []
    traces = {}

    def passed(name, group, detail):
        results.append({'id': name, 'group': group, 'status': 'passed', 'detail': detail})

    def reject(name, action, message, group):
        try:
            action()
        except ValueError as exc:
            if message not in str(exc):
                raise AssertionError(f'{name}: unexpected rejection: {exc}') from exc
            passed(name, group, str(exc))
        else:
            raise AssertionError(f'{name}: invalid input was accepted')

    # Reuse independent handwritten label expectations, not self-derived answers.
    for case, events in expand_cases(original):
        path = output / f"{case['id']}.events.jsonl"
        write_jsonl(path, events)
        row, labels = check_case(case, read_events(path))
        passed(case['id'], 'labels', row)
        if labels is not None:
            traces[case['id']] = events
            write_jsonl(output / f"{case['id']}.labels.jsonl", labels)

    def linked(name):
        events = deepcopy(traces[name])
        for event in events:
            if event['event_id'] in config['linked_event_ids']:
                event['idle_episode_id'] = config['idle_episode_id']
        validate_events(events)
        return events

    def take(events, t, complete=True):
        return snapshot(events, config['run_id'], config['idle_episode_id'], t,
                        complete_service_events=complete)

    baseline = linked('legal_demand_physical_miss')
    snapshots = []
    for expected in config['snapshots']:
        actual = take(baseline, expected['as_of_ns'])
        if actual['features'] != expected['features']:
            raise AssertionError(f'snapshot differs: {actual}')
        snapshots.append(actual)
        passed(f"snapshot_at_{expected['as_of_ns']}", 'state', actual)
    write_jsonl(output / 'snapshots.jsonl', snapshots)

    # Same observed past, different futures: labels should differ, features should not.
    variants = {
        'future_U': baseline,
        'future_D': linked('tool_end_then_explicit_retire'),
        'future_C': linked('observation_ends_tool_running'),
    }
    delayed = deepcopy(baseline)
    revised_times = {'tool-end': 18, 'ready': 18, 'enqueue': 18, 'dispatch': 19,
                     'lookup': 19, 'demand': 19, 'end': 19, 'workflow-end': 19}
    for event in delayed:
        event['ts_ns'] = revised_times.get(event['event_id'], event['ts_ns'])
    variants['future_tool_and_lookup_delayed'] = delayed
    extended = deepcopy(baseline)
    extended[-1]['ts_ns'] = 30
    variants['future_observation_extended'] = extended
    expected_outcomes = {
        'future_U': ('legal_demand', 14), 'future_D': ('retire', 16),
        'future_C': ('right_censored', 20),
        'future_tool_and_lookup_delayed': ('legal_demand', 19),
        'future_observation_extended': ('legal_demand', 14),
    }
    futures = []
    for name, events in variants.items():
        labels = derive_idle_labels(events)
        if (labels[0]['outcome'], labels[0]['end_ns']) != expected_outcomes[name]:
            raise AssertionError(f'{name}: wrong future label')
        actual = take(events, 9)
        if actual != snapshots[0]:
            raise AssertionError(f'{name}: future changed the snapshot')
        write_jsonl(output / f'{name}.events.jsonl', events)
        write_jsonl(output / f'{name}.labels.jsonl', labels)
        futures.append({'variant': name, 'snapshot': actual, 'label': labels[0]})
        passed(name, 'isolation', 'same snapshot at 9 ns; independently checked future outcome')
    write_json(output / 'future_invariance.json', futures)

    prefix = [e for e in baseline if e['ts_ns'] <= 9]
    if take(prefix, 9) != snapshots[0]:
        raise AssertionError('a future suffix was required to build the snapshot')
    passed('observed_prefix_is_sufficient', 'isolation', 'prefix-only input equals full-trace projection')

    injected = deepcopy(baseline)
    for event in injected:
        event['data']['future_tool_duration_s'] = 999999
        event['data']['outcome'] = 'future-secret'
    if take(injected, 9) != snapshots[0]:
        raise AssertionError('extra event payload entered features')
    passed('ignore_nonallowlisted_event_payload', 'isolation', 'injected future payload leaves features unchanged')
    for key in config['future_rejection_fields']:
        reject(f'reject_feature_{key}',
               lambda key=key: validate_features(snapshots[0]['features'] | {key: 123}),
               'allowlist mismatch', 'isolation')

    unlinked = snapshot(traces['legal_demand_physical_miss'], 'd2', 'idle-1', 9)
    if any(unlinked['features'][k] is not None for k in
           ('tool_name', 'tool_elapsed_s', 'phase', 'retry_count', 'is_queued', 'queue_length')):
        raise AssertionError('unavailable state was guessed or backfilled')
    write_json(output / 'missing_state_snapshot.json', unlinked)
    passed('missing_links_remain_unknown', 'state', unlinked)
    for t, message in [(4, 'has not started'), (14, 'open idle')]:
        reject(f'reject_snapshot_at_{t}', lambda t=t: take(baseline, t), message, 'state')
    reject('reject_snapshot_at_observation_end',
           lambda: take(variants['future_C'], 20), 'observation_end', 'state')
    if derive_idle_labels(traces['legal_demand_physical_miss']) != derive_idle_labels(
            traces['legal_demand_physical_hit']):
        raise AssertionError('physical hit/miss changed the logical label')
    passed('hit_miss_label_invariance', 'labels', 'full exported labels identical')

    # Two owners share a logical prefix. Retiring one must not close the other.
    shared = deepcopy(baseline)
    idle_index = next(i for i, e in enumerate(shared) if e['event_type'] == 'idle_start')
    other = deepcopy(shared[idle_index])
    other.update(event_id='other-idle', idle_episode_id='idle-2')
    other['data'].update(context_id='other-owner', context_version='other-v1')
    shared.insert(idle_index + 1, other)
    shared.append({
        **deepcopy(shared[idle_index]), 'event_id': 'retire-owner-1',
        'event_type': 'context_retire', 'ts_ns': 8,
        'data': {'reason': 'explicit_owner_abandonment'},
    })
    for event in shared:
        if event['event_type'] == 'legal_demand':
            event['idle_episode_id'] = 'idle-2'
    shared.sort(key=lambda e: e['ts_ns'])
    labels = derive_idle_labels(shared)
    actual = {r['idle_episode_id']: (r['outcome'], r['end_ns'], r['duration_ns']) for r in labels}
    if actual != {'idle-1': ('retire', 8, 3), 'idle-2': ('legal_demand', 14, 9)}:
        raise AssertionError('owner retire incorrectly closed a shared prefix relationship')
    snapshot(shared, 'd2', 'idle-2', 9)
    write_jsonl(output / 'shared_owners.events.jsonl', shared)
    write_jsonl(output / 'shared_owners.labels.jsonl', labels)
    passed('shared_owner_retire_is_local', 'labels', actual)

    # Exercise the real source replay permission gate, with a constant synthetic cost.
    case = {
        'id': 'd1-permissions', 'block_size': 128, 'arrivals': {'w': 0},
        'requests': [
            {'id': 'r0', 'workflow': 'w', 'context': 'c', 'tokens': list(range(257)), 'dependencies': []},
            {'id': 'r1', 'workflow': 'w', 'context': 'c', 'tokens': list(range(300)),
             'dependencies': [['r0', .1]], 'retire_context': True},
        ],
    }
    for policy in ('lru', 'retired_first_lru', 'fixed_ttl', 'fixed_ttl_retired_first'):
        reject(f'{policy}_rejects_future_script',
               lambda policy=policy: run(case, 6, 2, policy, script={'r0': .5},
                                         cost=lambda *args: 100),
               'future script privilege mismatch', 'permissions')
    diagnostic = run(case, 6, 2, 'offline_script', script={'r0': .5}, cost=lambda *args: 100)
    if diagnostic['decisions'][0]['ttl'] != .5:
        raise AssertionError('offline diagnostic did not apply the declared script')
    write_json(output / 'offline_diagnostic.json', diagnostic)
    passed('explicit_offline_script_is_diagnostic', 'permissions', 'offline policy can apply a future script')
    inputs = [
        FIXTURE, ROOT / 'data/fixtures/event_contract.json', Path(__file__),
        ROOT / 'src/leasepath/contract_fixtures.py', ROOT / 'src/leasepath/prediction_state.py',
        ROOT / 'src/leasepath/trace.py', ROOT / 'src/leasepath/source_replay.py',
        ROOT / 'src/leasepath/cache.py', ROOT / 'schemas/trace.schema.json',
        ROOT / 'docs/prediction_contract.md',
    ]
    groups = {group: sum(r['group'] == group for r in results)
              for group in ('labels', 'state', 'isolation', 'permissions')}
    summary = {
        'status': 'passed', 'scope': 'prediction D1 synthetic contract acceptance',
        'evidence_kind': 'synthetic', 'performance_claim_allowed': False,
        'checks_passed': len(results), 'groups': groups,
        'event_positive_cases': 8, 'event_negative_cases': 16,
        'runtime_callback_implemented': False,
        'limitations': [
            'Synthetic semantics only; no natural pilot, model accuracy or speedup evidence.',
            'Snapshot supports a minimal feature subset, not the complete planned dataset.',
            'Tool/request state requires explicit episode links; global queue needs complete service events.',
            'Shared-owner check covers logical labels only, not native physical block freeing.',
            'Existing validators do not discover omitted demands or prove retirement validity.',
            'Source replay permission tests are not a runtime predictor callback isolation proof.',
        ],
        'checks': results,
        'input_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in inputs},
        'output_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(output.glob('*.json*')) if p.name != 'summary.json'},
    }
    write_json(output / 'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'results/prediction/d1_contract')
    args = parser.parse_args()
    result = run_checks(args.output)
    print(json.dumps({k: result[k] for k in ('status', 'checks_passed', 'groups',
                                            'evidence_kind', 'runtime_callback_implemented')},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
