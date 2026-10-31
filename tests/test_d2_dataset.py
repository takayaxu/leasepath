"""Collector audits must fail closed when real evidence is incomplete."""
from copy import deepcopy
import json

import pytest

from leasepath.d2_dataset import export
from leasepath.trace import validate_events


RID1, RID2 = 'a' * 32, 'b' * 32


def records():
    rows = []

    def add(kind, t, **data):
        rows.append({'kind': kind, 'ts_ns': t, 'pid': 1, 'seq': len(rows) + 1, **data})

    add('observer_start', 0, boot_id='unit-test-clock')
    add('probe_installed', 0, compatibility_id='compat')
    add('workflow_submit', 1, workflow_id='w', source_group='test-fixture', task_id='t', compatibility_id='compat')
    for rid, t, prompt, cached in [(RID1, 10, [1] * 256, 0), (RID2, 100, [1] * 256 + [2] * 64, 256)]:
        backend = 'chatcmpl-' + rid
        blocks = [[{'slot': n, 'hash': f'block{n}', 'ref_count': 0, 'is_null': False} for n in range(3)]]
        add('request_ready', t, workflow_id='w', request_id=rid, owner='w', context_version=rid)
        add('request_enqueue', t + 1, request_id=backend, prompt_token_ids=prompt)
        add('request_dispatch', t + 2, request_id=backend)
        add('cache_lookup', t + 2, request_id=backend, prompt_token_ids=prompt,
            cached_tokens=cached, blocks=blocks, block_size=128, preemptions=0, lookup_duration_ns=5)
        add('prefill_scheduled', t + 3, request_id=backend, tokens=len(prompt) - cached)
        add('request_release', t + 4, request_id=backend, prompt_token_ids=prompt,
            prefix_token_ids=prompt[:256], block_size=128, blocks=blocks,
            scheduled_prefill_tokens=len(prompt) - cached, preemptions=0, status='FINISHED_STOPPED')
    add('context_close', 200, workflow_id='w', reason='explicit_close')
    add('workflow_end', 201, workflow_id='w')
    return rows


def run(tmp_path, rows, name='data'):
    raw = tmp_path / (name + '.raw.jsonl')
    raw.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    return export(raw, tmp_path / name, 'compat')


def test_measured_lookup_and_explicit_retire_produce_separate_targets(tmp_path):
    summary = run(tmp_path, records())
    assert summary['outcomes'] == {'legal_demand': 1, 'retire': 1}
    assert summary['cache_hit_requests'] == 1
    assert summary['snapshots'] >= 2
    features = [json.loads(line)['features'] for line in (tmp_path / 'data/snapshots.jsonl').read_text().splitlines()]
    assert all('outcome' not in f and f['queue_length'] is None for f in features)
    samples = [json.loads(line) for line in (tmp_path / 'data/snapshots.jsonl').read_text().splitlines()]
    for episode in {s['idle_episode_id'] for s in samples}:
        assert sum(s['sample_weight'] for s in samples if s['idle_episode_id'] == episode) == pytest.approx(1)


def test_missing_backend_lookup_is_rejected_even_when_client_request_exists(tmp_path):
    rows = [r for r in records() if not (r['kind'] == 'cache_lookup' and RID2 in r['request_id'])]
    for n, row in enumerate(rows, 1):
        row['seq'] = n
    with pytest.raises(ValueError, match='missing/duplicate backend stage'):
        run(tmp_path, rows)


def test_observer_sequence_gap_is_not_converted_to_censoring(tmp_path):
    rows = records()
    rows.pop(3)
    with pytest.raises(ValueError, match='sequence gap'):
        run(tmp_path, rows)


def test_seconds_schedule_emits_only_open_points_and_preserves_episode_weight(tmp_path):
    rows = records()
    for row in rows:
        row['ts_ns'] *= 100_000_000
    path = tmp_path / 'raw.jsonl'
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    export(path, tmp_path / 'seconds', 'compat', elapsed_points_s=[0, 1, 2, 3, 5, 10],
           max_snapshots_per_episode=20)
    samples = [json.loads(l) for l in (tmp_path / 'seconds/snapshots.jsonl').read_text().splitlines()]
    first = [s for s in samples if s['idle_episode_id'] == 'idle-' + RID1]
    assert {s['features']['elapsed_idle_s'] for s in first} >= {0, 1, 2, 3, 5}
    assert all(s['features']['elapsed_idle_s'] < 8.8 for s in first)
    assert sum(s['sample_weight'] for s in first) == pytest.approx(1)


def test_scheduled_prefill_must_agree_with_real_lookup(tmp_path):
    rows = records()
    next(r for r in rows if r['kind'] == 'prefill_scheduled')['tokens'] -= 1
    with pytest.raises(ValueError, match='prefill and lookup counters disagree'):
        run(tmp_path, rows)


def test_future_compatible_request_cannot_change_earlier_snapshot(tmp_path):
    original = records()
    run(tmp_path, original, 'original')
    changed = deepcopy(original)
    for row in changed:
        if RID2 in row.get('request_id', ''):
            for field in ['prompt_token_ids', 'prefix_token_ids']:
                if field in row:
                    row[field][0] = 9
    run(tmp_path, changed, 'changed')
    def first(name):
        return json.loads((tmp_path / name / 'snapshots.jsonl').read_text().splitlines()[0])['features']
    assert first('original') == first('changed')
    assert json.loads((tmp_path / 'original/summary.json').read_text())['outcomes'] != json.loads((tmp_path / 'changed/summary.json').read_text())['outcomes']


def test_aborted_request_is_not_claimed_as_completed_prefill(tmp_path):
    rows = records()
    next(r for r in rows if r['kind'] == 'request_release')['status'] = 'FINISHED_ABORTED'
    with pytest.raises(ValueError, match='normal request completion'):
        run(tmp_path, rows)


def test_export_cannot_relabel_another_backend_configuration(tmp_path):
    rows = records()
    next(r for r in rows if r['kind'] == 'probe_installed')['compatibility_id'] = 'another-model'
    with pytest.raises(ValueError, match='probe compatibility'):
        run(tmp_path, rows)


def test_enqueue_and_lookup_must_use_the_same_actual_tokens(tmp_path):
    rows = deepcopy(records())
    enqueue = next(r for r in rows if r['kind'] == 'request_enqueue')
    enqueue['prompt_token_ids'] = [8] + enqueue['prompt_token_ids'][1:]
    with pytest.raises(ValueError, match='enqueued tokens differ'):
        run(tmp_path, rows)


def test_measured_demand_cannot_forge_the_lookup_suffix(tmp_path):
    run(tmp_path, records())
    events = [json.loads(line) for line in (tmp_path / 'data/events.jsonl').read_text().splitlines()]
    # The idle prefix is still compatible, but U must reference exact lookup tokens.
    demand = next(e for e in events if e['event_type'] == 'legal_demand')
    demand['data']['request_token_ids'][-1] = 7
    with pytest.raises(ValueError, match='differs from measured backend lookup'):
        validate_events(events)


def test_collection_profile_is_provenance_and_not_a_future_feature(tmp_path):
    rows = records()
    submit = next(r for r in rows if r['kind'] == 'workflow_submit')
    submit.update(variant='sampled', generation={'temperature': .7})
    run(tmp_path, rows)
    events = [json.loads(line) for line in (tmp_path / 'data/events.jsonl').read_text().splitlines()]
    assert events[0]['data']['generation'] == {'temperature': .7}
    samples = [json.loads(line) for line in (tmp_path / 'data/snapshots.jsonl').read_text().splitlines()]
    assert all('generation' not in s['features'] and 'variant' not in s['features'] for s in samples)
