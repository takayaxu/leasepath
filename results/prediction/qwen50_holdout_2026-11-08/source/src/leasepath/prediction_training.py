"""Development-only grouping, workflow weighting, and observable-outcome scores."""
from bisect import bisect_right
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from .d2_hazards import derive_hazard_rows
from .prediction_state import validate_features
from .reuse_predictor import LABELS


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def load_development_dataset(path, expected_workflows):
    path = Path(path)
    summary = read_json(path / 'summary.json')
    for name in ['events.jsonl', 'episode_labels.jsonl', 'snapshots.jsonl', 'targets.jsonl']:
        if sha256(path / name) != summary['output_sha256'][name]:
            raise ValueError('dataset hash mismatch: ' + name)
    samples, targets = read_jsonl(path / 'snapshots.jsonl'), read_jsonl(path / 'targets.jsonl')
    if not samples or {s['run_id'] for s in samples} != set(expected_workflows):
        raise ValueError('dataset workflow membership differs')
    episode_weights, workflow_episodes = defaultdict(float), defaultdict(set)
    for sample in samples:
        validate_features(sample['features'])
        if sample['split'] != 'pilot_development_only':
            raise ValueError('only development data are authorized for this experiment')
        weight = sample['sample_weight']
        if type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0:
            raise ValueError('invalid source sample weight')
        episode_weights[sample['run_id'], sample['idle_episode_id']] += weight
        workflow_episodes[sample['run_id']].add(sample['idle_episode_id'])
    if not all(math.isclose(v, 1.) for v in episode_weights.values()):
        raise ValueError('source weights must sum to one per episode')
    samples = deepcopy(samples)
    for sample in samples:
        sample['sample_weight'] /= len(workflow_episodes[sample['run_id']])
    return samples, targets, summary


def assign_group_folds(group_ids, folds=5, seed=23):
    groups = set(group_ids)
    if type(folds) is not int or not 2 <= folds <= len(groups):
        raise ValueError('fold count requires at least two parent groups')
    ordered = sorted(groups, key=lambda group: (hashlib.sha256(f'{seed}:{group}'.encode()).hexdigest(), group))
    return {group: index % folds for index, group in enumerate(ordered)}


def split_fold(samples, targets, run_groups, assignments, held_out_fold):
    labels = {r['sample_id']: r for r in targets}
    if len(labels) != len(targets) or set(labels) != {s['sample_id'] for s in samples}:
        raise ValueError('sample/target identity mismatch')
    if len({s['sample_id'] for s in samples}) != len(samples):
        raise ValueError('duplicate sample identity')
    train, validation = [], []
    for sample in samples:
        group = run_groups[sample['run_id']]
        (validation if assignments[group] == held_out_fold else train).append(sample)
    train_groups = {run_groups[s['run_id']] for s in train}
    validation_groups = {run_groups[s['run_id']] for s in validation}
    if not train or not validation or train_groups & validation_groups:
        raise ValueError('empty or overlapping training/validation groups')
    return train, [labels[s['sample_id']] for s in train], validation, [labels[s['sample_id']] for s in validation]


def decision_state(sample):
    if sample['features']['elapsed_idle_s'] == 0:
        return 'idle_start'
    return sample['features']['phase'] if sample['features']['phase'] in {'queued', 'waiting_tool'} else 'other_observed'


def check_distribution(predictions):
    previous_u, previous_d, previous_s = 0., 0., 1.
    unsupported_seen = False
    for row in predictions:
        if not row['supported']:
            unsupported_seen = True
            if any(row[k] is not None for k in ['hU', 'hD', 'h0', 'F_U', 'F_D', 'survival']):
                raise ValueError('unsupported interval contains probabilities')
            continue
        if unsupported_seen:
            raise ValueError('support must be a contiguous prefix')
        values = [row[k] for k in ['hU', 'hD', 'h0', 'F_U', 'F_D', 'survival']]
        if any(not math.isfinite(v) or not -1e-12 <= v <= 1 + 1e-12 for v in values):
            raise ValueError('invalid probability')
        if not math.isclose(sum(values[:3]), 1.) or not math.isclose(sum(values[3:]), 1.):
            raise ValueError('probabilities do not sum to one')
        fu, fd, survival = values[3:]
        if fu + 1e-12 < previous_u or fd + 1e-12 < previous_d or survival > previous_s + 1e-12:
            raise ValueError('invalid cumulative monotonicity')
        if (not math.isclose(fu, previous_u + previous_s * values[0], abs_tol=1e-12)
                or not math.isclose(fd, previous_d + previous_s * values[1], abs_tol=1e-12)
                or not math.isclose(survival, previous_s * values[2], abs_tol=1e-12)):
            raise ValueError('cumulative probability differs from hazard recursion')
        previous_u, previous_d, previous_s = fu, fd, survival


def observable_class(target, horizon):
    if target['outcome'] == 'right_censored':
        return 2 if target['remaining_s'] >= horizon else None
    if target['outcome'] not in {'legal_demand', 'retire'}:
        raise ValueError('unknown target outcome')
    if target['remaining_s'] > horizon:
        return 2
    return 0 if target['outcome'] == 'legal_demand' else 1


def _aggregate(records, run_groups):
    """Each record is (workflow, sample/risk weight, metric mapping)."""
    workflow_sums, workflow_weights = {}, defaultdict(float)
    for workflow, weight, values in records:
        total = workflow_sums.setdefault(workflow, {k: 0. for k in values})
        workflow_weights[workflow] += weight
        for key, value in values.items():
            total[key] += weight * value
    if not records:
        return {'records': 0, 'workflows': 0, 'parent_tasks': 0, 'macro_workflow': None, 'macro_parent_task': None}
    means = {workflow: {k: v / workflow_weights[workflow] for k, v in total.items()}
             for workflow, total in workflow_sums.items()}
    groups = defaultdict(list)
    for workflow, values in means.items():
        groups[run_groups[workflow]].append(values)
    keys = next(iter(means.values()))
    task_means = [{k: sum(v[k] for v in values) / len(values) for k in keys} for values in groups.values()]
    return {'records': len(records), 'workflows': len(means), 'parent_tasks': len(groups),
            'macro_workflow': {k: sum(v[k] for v in means.values()) / len(means) for k in keys},
            'macro_parent_task': {k: sum(v[k] for v in task_means) / len(task_means) for k in keys}}


def score_predictions(samples, targets, predictions, run_groups, right_edges,
                      calibration_edges=(0., .2, .4, .6, .8, 1.)):
    """Score OOF distributions on common model support; never invent censor labels.

    Cumulative scores are observable-case only (no IPCW). This experiment has no
    naturally censored targets; counts expose missing outcomes if that changes.
    NLL is conditional risk-row NLL, clipped at 1e-15; not a path likelihood.
    """
    if (not calibration_edges or calibration_edges[0] != 0 or calibration_edges[-1] != 1
            or any(not math.isfinite(v) for v in calibration_edges)
            or any(a >= b for a, b in zip(calibration_edges, calibration_edges[1:]))):
        raise ValueError('invalid calibration edges')
    by_id = {s['sample_id']: s for s in samples}
    labels = {t['sample_id']: t for t in targets}
    # Also validate label alignment, durations and censoring through the risk exporter.
    risk_rows = derive_hazard_rows(samples, targets, right_edges)
    for model_predictions in predictions.values():
        if set(model_predictions) != set(by_id):
            raise ValueError('OOF predictions must cover each sample exactly once')
        for distribution in model_predictions.values():
            if [r['right_s'] for r in distribution] != list(right_edges):
                raise ValueError('prediction horizons differ')
            check_distribution(distribution)
    common_support = {(sid, k): all(model[sid][k]['supported'] for model in predictions.values())
                      for sid in by_id for k in range(len(right_edges))}
    scopes = ['all', 'idle_start', 'waiting_tool', 'queued', 'other_observed']
    result = {}
    for model_name, model_predictions in predictions.items():
        by_scope = {}
        for scope in scopes:
            selected = [s for s in samples if scope == 'all' or decision_state(s) == scope]
            horizons = []
            for k, horizon in enumerate(right_edges):
                records, calibration = [], [[] for _ in range(len(calibration_edges) - 1)]
                unknown, unsupported = 0, 0
                for sample in selected:
                    sid = sample['sample_id']
                    if not common_support[sid, k]:
                        unsupported += 1
                        continue
                    observed = observable_class(labels[sid], horizon)
                    if observed is None:
                        unknown += 1
                        continue
                    row = model_predictions[sid][k]
                    probabilities = [row['F_U'], row['F_D'], row['survival']]
                    squared = [(p - int(observed == index)) ** 2 for index, p in enumerate(probabilities)]
                    record = (sample['run_id'], sample['sample_weight'],
                              {'brier3': sum(squared), 'brier_U': squared[0], 'brier_D': squared[1]})
                    records.append(record)
                    # Descriptive workflow-balanced bins; task-macro Brier remains primary.
                    bin_index = min(len(calibration) - 1, bisect_right(calibration_edges, probabilities[0]) - 1)
                    calibration[bin_index].append((sample['sample_weight'], probabilities[0], int(observed == 0)))
                bins = []
                for index, values in enumerate(calibration):
                    weight = sum(v[0] for v in values)
                    bins.append({'left': calibration_edges[index], 'right': calibration_edges[index + 1],
                                 'records': len(values), 'weight': weight,
                                 'mean_predicted_U': sum(w * p for w, p, y in values) / weight if weight else None,
                                 'observed_U': sum(w * y for w, p, y in values) / weight if weight else None})
                horizons.append({'horizon_s': horizon, 'candidate_snapshots': len(selected),
                                 'unsupported_snapshots': unsupported, 'censored_unknown_snapshots': unknown,
                                 **_aggregate(records, run_groups), 'calibration_U': bins})
            selected_ids = {s['sample_id'] for s in selected}
            risk_records, clipped, masked, unsupported_rows = [], 0, 0, 0
            for row in risk_rows:
                sid, k = row['sample_id'], row['interval_index']
                if sid not in selected_ids:
                    continue
                if not row['loss_mask']:
                    masked += 1
                    continue
                if not common_support[sid, k]:
                    unsupported_rows += 1
                    continue
                sample = by_id[sid]
                probability = model_predictions[sid][k][['hU', 'hD', 'h0'][LABELS.index(row['label'])]]
                clipped += probability < 1e-15
                risk_records.append((sample['run_id'], row['sample_weight'], {'risk_row_nll': -math.log(max(probability, 1e-15))}))
            by_scope[scope] = {'horizons': horizons,
                               'conditional_risk_nll': {**_aggregate(risk_records, run_groups),
                                   'clipped_records': clipped, 'masked_partial_intervals': masked,
                                   'unsupported_records': unsupported_rows}}
        result[model_name] = by_scope
    return result
