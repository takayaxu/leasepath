from copy import deepcopy
import hashlib
import json
import math

import pytest

from leasepath.prediction_training import (
    assign_group_folds, check_distribution, load_development_dataset,
    observable_class, score_predictions, split_fold,
)


def features():
    return {'elapsed_idle_s': 0., 'compatibility_id': 'backend', 'prefix_tokens': 128,
            'tool_name': 'bash', 'tool_elapsed_s': None, 'phase': None,
            'retry_count': None, 'is_queued': None, 'queue_length': None}


def samples_for_runs(runs):
    return [{'sample_id': str(i), 'run_id': run, 'idle_episode_id': 'ep-' + str(i),
             'features': features(), 'sample_weight': 1., 'split': 'pilot_development_only'} for i, run in enumerate(runs)]


def prediction():
    return [{'interval_index': 0, 'right_s': .1, 'supported': True,
             'hU': .25, 'hD': .25, 'h0': .5, 'F_U': .25, 'F_D': .25, 'survival': .5},
            {'interval_index': 1, 'right_s': .2, 'supported': True,
             'hU': 0., 'hD': 1., 'h0': 0., 'F_U': .25, 'F_D': .75, 'survival': 0.}]


def test_parent_task_split_keeps_variants_together_and_is_order_independent():
    assignments = assign_group_folds(['a', 'b', 'c', 'd'], folds=2, seed=23)
    assert assignments == assign_group_folds(['d', 'a', 'c', 'b', 'a'], folds=2, seed=23)
    samples = samples_for_runs(['a-greedy', 'a-sampled', 'b', 'c', 'd'])
    groups = {s['run_id']: s['run_id'].split('-')[0] for s in samples}
    targets = [{'sample_id': s['sample_id'], 'outcome': 'legal_demand', 'remaining_s': .05} for s in samples]
    fold = assignments['a']
    train, train_targets, validation, _ = split_fold(samples, targets, groups, assignments, fold)
    assert {'a-greedy', 'a-sampled'} <= {s['run_id'] for s in validation}
    assert not {groups[s['run_id']] for s in train} & {groups[s['run_id']] for s in validation}
    changed = deepcopy(targets)
    for label in changed:
        if label['sample_id'] in {s['sample_id'] for s in validation}:
            label.update(outcome='retire', remaining_s=100.)
    assert split_fold(samples, changed, groups, assignments, fold)[1] == train_targets


def test_cumulative_brier_and_conditional_nll_match_hand_calculation():
    samples = samples_for_runs(['a', 'b'])
    targets = [{'sample_id': '0', 'outcome': 'legal_demand', 'remaining_s': .05},
               {'sample_id': '1', 'outcome': 'retire', 'remaining_s': .15}]
    scores = score_predictions(samples, targets, {'model': {'0': prediction(), '1': prediction()}},
                               {'a': 'parent-a', 'b': 'parent-b'}, [.1, .2])['model']['all']
    assert scores['horizons'][0]['macro_parent_task']['brier3'] == pytest.approx(.625)
    assert scores['horizons'][1]['macro_parent_task']['brier3'] == pytest.approx(.625)
    expected_nll = (-math.log(.25) + (-math.log(.5) - math.log(1.)) / 2) / 2
    assert scores['conditional_risk_nll']['macro_workflow']['risk_row_nll'] == pytest.approx(expected_nll)
    bin_row = scores['horizons'][0]['calibration_U'][1]
    assert bin_row['mean_predicted_U'] == .25 and bin_row['observed_U'] == .5


def test_unknown_after_censoring_is_excluded_instead_of_scored_as_negative():
    samples = samples_for_runs(['a'])
    targets = [{'sample_id': '0', 'outcome': 'right_censored', 'remaining_s': .12}]
    result = score_predictions(samples, targets, {'model': {'0': prediction()}}, {'a': 'parent'}, [.1, .2])['model']['all']
    assert result['horizons'][0]['records'] == 1
    assert result['horizons'][1]['censored_unknown_snapshots'] == 1
    assert result['horizons'][1]['records'] == 0
    assert result['horizons'][1]['macro_parent_task'] is None
    assert result['conditional_risk_nll']['masked_partial_intervals'] == 1
    assert observable_class({'outcome': 'right_censored', 'remaining_s': .1}, .1) == 2


def test_macro_parent_weighting_does_not_count_two_profiles_as_two_parent_tasks():
    samples = samples_for_runs(['a-greedy', 'a-sampled', 'b'])
    targets = [{'sample_id': s['sample_id'], 'outcome': 'legal_demand', 'remaining_s': .05} for s in samples]
    distributions = {}
    for sample in samples:
        wrong = sample['run_id'].startswith('a')
        pu, pd = (0., 1.) if wrong else (1., 0.)
        distributions[sample['sample_id']] = [{'right_s': .1, 'supported': True, 'hU': pu, 'hD': pd,
                                               'h0': 0., 'F_U': pu, 'F_D': pd, 'survival': 0.}]
    scores = score_predictions(samples, targets, {'model': distributions},
                               {'a-greedy': 'parent-a', 'a-sampled': 'parent-a', 'b': 'parent-b'}, [.1])['model']['all']['horizons'][0]
    assert scores['macro_parent_task']['brier3'] == 1.
    assert scores['macro_workflow']['brier3'] == pytest.approx(4/3)


def test_unsupported_forecasts_are_counted_and_all_models_use_common_support():
    samples = samples_for_runs(['a'])
    target = [{'sample_id': '0', 'outcome': 'retire', 'remaining_s': .15}]
    unsupported = prediction()
    unsupported[1].update(supported=False, hU=None, hD=None, h0=None, F_U=None, F_D=None, survival=None)
    scores = score_predictions(samples, target, {'supported': {'0': prediction()}, 'unsupported': {'0': unsupported}},
                               {'a': 'parent'}, [.1, .2])
    for model in scores.values():
        assert model['all']['horizons'][1]['unsupported_snapshots'] == 1
        assert model['all']['horizons'][1]['macro_parent_task'] is None
    broken = prediction()
    broken[1]['F_U'] = .5
    broken[1]['F_D'] = .5
    with pytest.raises(ValueError, match='recursion'):
        check_distribution(broken)


def test_source_hash_and_episode_workflow_weights_are_checked(tmp_path):
    samples = samples_for_runs(['a', 'a', 'b', 'b'])
    samples[2]['idle_episode_id'] = samples[3]['idle_episode_id'] = 'same-episode'
    samples[2]['sample_weight'] = samples[3]['sample_weight'] = .5
    files = {'events.jsonl': [], 'episode_labels.jsonl': [], 'snapshots.jsonl': samples,
             'targets.jsonl': [{'sample_id': s['sample_id'], 'outcome': 'legal_demand', 'remaining_s': .05} for s in samples]}
    hashes = {}
    for name, records in files.items():
        path = tmp_path / name
        path.write_text(''.join(json.dumps(r) + '\n' for r in records))
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / 'summary.json').write_text(json.dumps({'output_sha256': hashes}))
    weighted, _, _ = load_development_dataset(tmp_path, {'a', 'b'})
    for run in ['a', 'b']:
        assert sum(s['sample_weight'] for s in weighted if s['run_id'] == run) == 1.
    assert all(s['sample_weight'] == .5 for s in weighted)
    (tmp_path / 'snapshots.jsonl').write_text('[]\n')
    with pytest.raises(ValueError, match='hash mismatch'):
        load_development_dataset(tmp_path, {'a', 'b'})
