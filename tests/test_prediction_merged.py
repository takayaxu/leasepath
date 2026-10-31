from copy import deepcopy
import json

import pytest

from leasepath.d2_hazards import derive_hazard_rows
from leasepath.prediction_merged import evaluation_strata, load_inventory
from leasepath.prediction_state import FEATURES
from leasepath.prediction_training import assign_group_folds, read_json, read_jsonl, sha256


def write(path, data):
    path.write_text(json.dumps(data))


def write_lines(path, rows):
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))


def refresh(root):
    """Keep file hashes coherent to test semantic failures beyond hash checks."""
    inventory = read_json(root / 'inventory.json')
    for record in inventory['rounds']:
        data = root / record['views']['all-observed']['dataset_path']
        summary = read_json(data / 'summary.json')
        summary['output_sha256'] = {p.name: sha256(p) for p in data.glob('*.jsonl')
                                    if not p.name.startswith('features-')}
        write(data / 'summary.json', summary)
        for view in record['views'].values():
            view['summary_sha256'] = sha256(data / 'summary.json')
            view['file_sha256'] = {p.name: sha256(p) for p in data.glob('*.jsonl')}
        results = root / record['result_root']
        write(results / 'SHA256SUMS.json', {p.name: sha256(p) for p in results.glob('*.json')
                                          if p.name != 'SHA256SUMS.json'})
    write(root / 'inventory.json', inventory)


@pytest.fixture
def source(tmp_path):
    records, all_samples, all_targets = [], [], []
    for n in range(2):
        data, results = tmp_path / f'data{n}', tmp_path / f'results{n}'
        data.mkdir()
        results.mkdir()
        workflow, group = f'run{n}', 'designed:csv'
        labels, samples, targets = [], [], []
        for ep, start, end, snapshots, outcome in [
                ('first', 0, 1000000000, [0, 500000000], 'legal_demand'),
                ('second', 2000000000, 4000000000, [2000000000], 'retire')]:
            labels.append({'run_id': workflow, 'idle_episode_id': ep, 'start_ns': start,
                           'end_ns': end, 'duration_ns': end-start, 'outcome': outcome})
            for ts in snapshots:
                sid = f'{workflow}:{ep}:{ts}'
                features = {'elapsed_idle_s': (ts-start)/1e9, 'compatibility_id': 'backend',
                            'prefix_tokens': 128, 'tool_name': 'bash', 'tool_elapsed_s': None,
                            'phase': 'waiting_tool', 'retry_count': None, 'is_queued': None, 'queue_length': None}
                samples.append({'sample_id': sid, 'run_id': workflow, 'idle_episode_id': ep,
                                'snapshot_ns': ts, 'features': features, 'sample_weight': 1/len(snapshots),
                                'split': 'pilot_development_only', 'source_group': 'designed'})
                targets.append({'sample_id': sid, 'outcome': outcome, 'remaining_s': (end-ts)/1e9})
        for name, rows in [('events.jsonl', []), ('snapshots.jsonl', samples),
                           ('targets.jsonl', targets), ('episode_labels.jsonl', labels)]:
            write_lines(data / name, rows)
        if n:
            write_lines(data / 'features-repair-v2.jsonl', [
                {'sample_id': s['sample_id'], 'group_id': group,
                 'features': {**s['features'], 'prior_failed_tests': 0, 'current_suite_is_retry': False}}
                for s in samples])
        write(data / 'summary.json', {'runs': 1, 'idle_episodes': 2, 'snapshots': 3})
        write(results / 'execution-quality.json', [
            {'workflow_id': workflow, 'group_id': group, 'quality_passed': True, 'criteria': {'backend_audit': True}}])
        write(results / 'workflow-groups.json', [{'workflow_id': workflow, 'group_id': group}])
        risk_rows = len(derive_hazard_rows(samples, targets))
        view = {'dataset_path': data.name, 'workflow_ids': [workflow], 'parent_group_ids': [group],
                'workflows': 1, 'parent_tasks': 1, 'episodes': 2, 'snapshots': 3, 'risk_rows': risk_rows,
                'outcomes': {'legal_demand': 1, 'retire': 1}}
        records.append({'round': f'round{n}', 'result_root': results.name,
                        'views': {'all-observed': deepcopy(view), 'qualified': deepcopy(view)}})
        all_samples.extend(samples)
        all_targets.extend(targets)
    combined = {'workflows': 2, 'parent_tasks': 1, 'episodes': 4, 'snapshots': 6,
                'risk_rows': len(derive_hazard_rows(all_samples, all_targets)), 'parent_group_ids': ['designed:csv'],
                'outcomes': {'legal_demand': 2, 'retire': 2}}
    write(tmp_path / 'inventory.json', {'rounds': records,
                                      'combined_views': {s: deepcopy(combined) for s in ['all-observed', 'qualified']}})
    refresh(tmp_path)
    return tmp_path


def test_shared_parent_workflow_weights_and_missing_history_survive_merge(source):
    loaded, _ = load_inventory(source, source / 'inventory.json')
    data = loaded['all-observed']
    assert data['summary']['parent_tasks'] == 1
    folds = assign_group_folds(['designed:csv', 'other:task'], 2)
    assert folds[data['run_groups']['run0']] == folds[data['run_groups']['run1']]
    for workflow in ['run0', 'run1']:
        assert sum(s['sample_weight'] for s in data['samples'] if s['run_id'] == workflow) == 1.
        assert sorted(s['sample_weight'] for s in data['samples'] if s['run_id'] == workflow) == [.25, .25, .5]
    assert all(m['prior_failed_tests'] is None for m in data['metadata'].values() if m['round'] == 'round0')
    assert all(m['prior_failed_tests'] == 0 for m in data['metadata'].values() if m['round'] == 'round1')
    assert all(set(s['features']) == FEATURES for s in data['samples'])
    assert loaded['qualified']['samples'] == data['samples']


@pytest.mark.parametrize('corruption,match', [('remaining', 'remaining time'), ('sidecar', 'sidecar/base')])
def test_matching_hashes_cannot_hide_time_origin_or_feature_mismatch(source, corruption, match):
    name = 'targets.jsonl' if corruption == 'remaining' else 'features-repair-v2.jsonl'
    path = source / 'data1' / name
    rows = read_jsonl(path)
    if corruption == 'remaining':
        rows[0]['remaining_s'] = .9
    else:
        rows[0]['features']['elapsed_idle_s'] = .1
    write_lines(path, rows)
    refresh(source)
    with pytest.raises(ValueError, match=match):
        load_inventory(source, source / 'inventory.json')


def test_conflicting_parent_map_is_rejected_even_with_consistent_result_hashes(source):
    write(source / 'results1/workflow-groups.json', [{'workflow_id': 'run1', 'group_id': 'designed:csv-large'}])
    refresh(source)
    with pytest.raises(ValueError, match='quality/group'):
        load_inventory(source, source / 'inventory.json')


def test_repeated_workflow_cannot_be_counted_as_new_round(source):
    inventory = read_json(source / 'inventory.json')
    for view in inventory['rounds'][1]['views'].values():
        view['workflow_ids'] = ['run0']
    write(source / 'inventory.json', inventory)
    for name in ['execution-quality.json', 'workflow-groups.json']:
        path = source / 'results1' / name
        rows = read_json(path)
        rows[0]['workflow_id'] = 'run0'
        write(path, rows)
    for name in ['snapshots.jsonl', 'episode_labels.jsonl']:
        path = source / 'data1' / name
        rows = read_jsonl(path)
        for row in rows:
            row['run_id'] = 'run0'
        write_lines(path, rows)
    refresh(source)
    with pytest.raises(ValueError, match='duplicate workflow across rounds'):
        load_inventory(source, source / 'inventory.json')


def test_pinned_provenance_and_tampered_data_are_rejected(source):
    with pytest.raises(ValueError, match='inventory hash'):
        load_inventory(source, source / 'inventory.json', expected_inventory_sha256='invalid')
    with pytest.raises(ValueError, match='result manifest hash'):
        load_inventory(source, source / 'inventory.json', expected_result_manifests={'round0': 'invalid'})
    (source / 'data1/targets.jsonl').write_text('')
    with pytest.raises(ValueError, match='data hash mismatch'):
        load_inventory(source, source / 'inventory.json')


def test_future_quality_and_duration_are_evaluation_metadata_only(source):
    data = load_inventory(source, source / 'inventory.json')[0]['all-observed']
    samples = deepcopy(data['samples'])
    strata = evaluation_strata(samples, data['metadata'])
    assert len(strata['episode_duration:1to3s']) == 6
    changed = deepcopy(data['metadata'])
    for meta in changed.values():
        meta.update(quality_passed=False, episode_duration_s=8)
    assert len(evaluation_strata(samples, changed)['episode_duration:3to10s']) == 6
    assert samples == data['samples']
    assert all('episode_duration_s' not in s['features'] and 'quality_passed' not in s['features'] for s in samples)
