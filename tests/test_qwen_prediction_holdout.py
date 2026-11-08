from copy import deepcopy
import json
from pathlib import Path

import pytest

from leasepath.prediction_training import sha256
from leasepath.qwen_prediction_holdout import load_dataset, partition, select_parents


def fixture_dataset(tmp_path):
    features = {'elapsed_idle_s': 0., 'compatibility_id': 'same', 'prefix_tokens': 128,
                'tool_name': None, 'tool_elapsed_s': None, 'phase': None,
                'retry_count': None, 'is_queued': None, 'queue_length': None}
    rows = {
        'catalog.jsonl': [{'run_id': 'batch', 'task': t, 'source_group': t, 'generations': 1} for t in ['a', 'b']],
        'events.jsonl': [{'run_id': 'batch', 'event_type': 'idle_start', 'idle_episode_id': f'p{i}',
                          'workflow_id': t} for i, t in enumerate(['a', 'b'])],
        'episodes.jsonl': [{'run_id': 'batch', 'idle_episode_id': f'p{i}', 'start_ns': 0,
                            'end_ns': 100, 'duration_ns': 100, 'outcome': 'legal_demand'} for i in range(2)],
        'features.jsonl': [{'run_id': 'batch', 'idle_episode_id': f'p{i}', 'snapshot_ns': 0,
                            'features': features} for i in range(2)],
        'targets.jsonl': [{'run_id': 'batch', 'idle_episode_id': f'p{i}', 'snapshot_ns': 0,
                           'outcome': 'legal_demand', 'remaining_ns': 100} for i in range(2)],
    }
    for name, values in rows.items():
        (tmp_path / name).write_text(''.join(json.dumps(r) + '\n' for r in values))
    hashes = {name: sha256(tmp_path / name) for name in rows}
    (tmp_path / 'SHA256SUMS').write_text(''.join(f'{digest}  {name}\n' for name, digest in hashes.items()))
    hashes['SHA256SUMS'] = sha256(tmp_path / 'SHA256SUMS')
    return hashes


def test_batch_run_is_split_into_distinct_parent_workflows_and_test_labels_stay_out(tmp_path):
    hashes = fixture_dataset(tmp_path)
    catalog, samples, targets, metadata, groups = load_dataset(tmp_path, hashes)
    assert len(groups) == 2  # Both parents have the same raw batch run_id.
    assignments = select_parents(catalog, {'x': {'tasks': ['a', 'b'], 'test_count': 1}}, 23)
    parts = partition(samples, targets, metadata, assignments)
    assert all(sum(s['sample_weight'] for s in samples if s['run_id'] == run) == 1 for run in groups)
    changed = deepcopy(targets)
    test_ids = {s['sample_id'] for s in parts['test'][0]}
    for t in changed:
        if t['sample_id'] in test_ids:
            t.update(outcome='retire', remaining_s=100.)
    assert partition(samples, changed, metadata, assignments)['train'] == parts['train']
    assert select_parents(list(reversed(catalog)), {'x': {'tasks': ['b', 'a'], 'test_count': 1}}, 23) == assignments


def test_import_rejects_tampered_inputs_and_inconsistent_target(tmp_path):
    hashes = fixture_dataset(tmp_path)
    target_file = tmp_path / 'targets.jsonl'
    target_file.write_text(target_file.read_text().replace('100', '99'))
    with pytest.raises(ValueError, match='hash differs'):
        load_dataset(tmp_path, hashes)
    hashes['targets.jsonl'] = sha256(target_file)
    (tmp_path / 'SHA256SUMS').write_text(''.join(f'{digest}  {name}\n' for name, digest in hashes.items()
                                               if name != 'SHA256SUMS'))
    hashes['SHA256SUMS'] = sha256(tmp_path / 'SHA256SUMS')
    with pytest.raises(ValueError, match='target inconsistent'):
        load_dataset(tmp_path, hashes)


def test_overlapping_semantic_strata_rejected():
    catalog = [{'task': t, 'source_group': t} for t in ['a', 'b', 'c']]
    with pytest.raises(ValueError, match='overlapping strata'):
        select_parents(catalog, {'x': {'tasks': ['a', 'b'], 'test_count': 1},
                                'y': {'tasks': ['b', 'c'], 'test_count': 1}}, 23)
