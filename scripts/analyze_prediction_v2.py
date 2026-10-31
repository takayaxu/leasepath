"""Descriptive diagnosis of frozen v2 models; no model fitting or tuning."""
from bisect import bisect_right
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import numpy as np
from leasepath.d2_hazards import derive_hazard_rows
from leasepath.gbdt_predictor import GBDTRiskPredictor
from leasepath.prediction_training import read_json, read_jsonl, score_predictions, sha256
from leasepath.reuse_predictor import EmpiricalRiskPredictor, LABELS


def main():
    source = ROOT / 'results/prediction/prediction_v2_50parents'
    summary, manifest = read_json(source / 'summary.json'), read_json(source / 'run-manifest.json')
    artifact = Path(summary['artifacts'])
    baseline = manifest['baseline_configuration']
    metrics = read_json(source / 'metrics.json')
    groups = {w: v['group_id'] for w, v in read_json(source / 'split-manifest.json')['workflows'].items()}
    input_hashes = {str(source.relative_to(ROOT) / 'SHA256SUMS.json'): sha256(source / 'SHA256SUMS.json')}
    for filename, digest in read_json(source / 'SHA256SUMS.json').items():
        assert sha256(source / filename) == digest, filename
    outputs = {}
    for scope in ['all-observed', 'qualified']:
        data = artifact / 'merged' / scope
        for filename, digest in summary['datasets'][scope]['merged_files_sha256'].items():
            assert sha256(data / filename) == digest, filename
            input_hashes[str((data / filename).relative_to(ROOT))] = digest
        samples = read_jsonl(data / 'snapshots-workflow-weighted.jsonl')
        targets = read_jsonl(data / 'targets.jsonl')
        episodes = read_jsonl(data / 'episode_labels.jsonl')
        metadata = {m['sample_id']: m for m in read_jsonl(data / 'evaluation-metadata-only.jsonl')}
        by_id, target_by_id = {s['sample_id']: s for s in samples}, {t['sample_id']: t for t in targets}
        key = lambda s: (s['features']['tool_name'], s['features']['phase'],
                         bisect_right(baseline['elapsed_edges'], s['features']['elapsed_idle_s']))
        risk = [r for r in derive_hazard_rows(samples, targets, baseline['right_edges']) if r['loss_mask']]
        models, predictions = {}, {}
        for name in ['global', 'conditional', 'gbdt']:
            cls = GBDTRiskPredictor if name == 'gbdt' else EmpiricalRiskPredictor
            models[name] = cls.load(source / f'{scope}-{name}-model.json')
            predictions[name] = {s['sample_id']: models[name].predict(s['features']) for s in samples}
        training = score_predictions(samples, targets, predictions, groups, baseline['right_edges'])
        scores = {state: {name: {
            'in_sample_brier_0.1': next(r['macro_parent_task']['brier3'] for r in training[name][state]['horizons'] if r['horizon_s'] == .1),
            'oof_brier_0.1': next(r['macro_parent_task']['brier3'] for r in metrics[scope][name][state]['horizons'] if r['horizon_s'] == .1),
            'in_sample_conditional_nll': training[name][state]['conditional_risk_nll']['macro_parent_task']['risk_row_nll'],
            'oof_conditional_nll': metrics[scope][name][state]['conditional_risk_nll']['macro_parent_task']['risk_row_nll'],
        } for name in models} for state in ['all', 'idle_start', 'waiting_tool', 'queued', 'other_observed']}
        # This matches the weighted cross-entropy data term used by LightGBM,
        # unlike the workflow/parent-macro diagnostic NLL above. It omits penalties.
        weighted_nll = {}
        for name, predicted in predictions.items():
            total = 0.
            for row in risk:
                hazard = predicted[row['sample_id']][row['interval_index']][['hU', 'hD', 'h0'][LABELS.index(row['label'])]]
                total -= row['sample_weight'] * np.log(max(hazard, 1e-15))
            weighted_nll[name] = float(total / sum(r['sample_weight'] for r in risk))
        dump = models['gbdt'].booster.dump_model()
        hessians, split_counts = [], Counter()
        def walk(node):
            if 'leaf_index' in node:
                hessians.append(node['leaf_weight'])
            else:
                split_counts[dump['feature_names'][node['split_feature']]] += 1
                walk(node['left_child'])
                walk(node['right_child'])
        for tree in dump['tree_info']:
            walk(tree['tree_structure'])
        command_samples = defaultdict(list)
        for sample in samples:
            if key(sample)[:2] == ('bash', 'waiting_tool') and key(sample)[2] == 0:
                category = metadata[sample['sample_id']]['command_category']
                if category is not None:
                    command_samples[category].append(sample)
        command_signal = {}
        for category, selected in command_samples.items():
            durations = [target_by_id[s['sample_id']]['remaining_s'] for s in selected]
            command_signal[category] = {
                'snapshots': len(selected), 'workflows': len({s['run_id'] for s in selected}),
                'parents': len({groups[s['run_id']] for s in selected}),
                'median_remaining_s': float(np.median(durations)),
                'still_waiting_at_0.1s': sum(t > .1 for t in durations)}
        estimate_sources = {name: Counter() for name in models}
        oof = artifact / f'{scope}-oof-predictions.jsonl'
        assert sha256(oof) == summary['datasets'][scope]['oof_sha256']
        input_hashes[str(oof.relative_to(ROOT))] = sha256(oof)
        with oof.open() as stream:
            for line in stream:
                record = json.loads(line)
                for name in models:
                    row = next(r for r in record['models'][name] if r['right_s'] == .1)
                    estimate_sources[name][row['estimate_source']] += 1
        outputs[scope] = {
            'unique_encoded_snapshot_states': len({key(s) for s in samples}),
            'observed_state_interval_cells': len({(key(by_id[r['sample_id']]), r['interval_index']) for r in risk}),
            'idle_start_unique_encoded_states': len({key(s) for s in samples if s['features']['elapsed_idle_s'] == 0}),
            'episode_duration_counts': dict(Counter('lt1s' if e['duration_ns'] < 1e9 else '1to3s' if e['duration_ns'] < 3e9 else '3to10s' if e['duration_ns'] < 1e10 else 'ge10s' for e in episodes)),
            'categorical_values': {k: dict(Counter(str(s['features'][k]) for s in samples)) for k in ['tool_name', 'phase']},
            'scores': scores, 'in_sample_weighted_risk_cross_entropy_data_term': weighted_nll,
            'training_risk_weight_sum': sum(r['sample_weight'] for r in risk),
            'leaf_hessian_quantiles': dict(zip(['min', 'p25', 'median', 'p75', 'max'], np.quantile(hessians, [0, .25, .5, .75, 1]).tolist())),
            'lambda_l2': models['gbdt'].parameters['lambda_l2'], 'split_counts': dict(split_counts),
            'command_signal_within_identical_encoded_state': command_signal,
            'oof_0.1s_estimate_sources': {k: dict(v) for k, v in estimate_sources.items()},
            'retire_duration_s': {'max': max(e['duration_ns'] / 1e9 for e in episodes if e['outcome'] == 'retire')},
        }
    for filename, digest in input_hashes.items():
        assert sha256(ROOT / filename) == digest, filename
    out = ROOT / 'results/prediction/prediction_v2_diagnosis'
    out.mkdir(parents=True, exist_ok=False)
    result = {'status': 'completed', 'scope': 'descriptive diagnosis of existing frozen models; no fitting or parameter selection',
              'input_sha256': input_hashes, 'script_sha256': sha256(Path(__file__)), 'datasets': outputs,
              'interpretation_limits': ['in-sample scores do not estimate generalization and full fits use more training parents than CV fits',
                  'train/OOF Brier at 0.1s has full support in both; qualified risk NLL has different support and is not a clean train/test-gap comparison',
                  'command comparisons are descriptive correlated snapshots, not held-out evidence of an enhanced model',
                  'risk weight sum is a normalization scale, not independent sample count',
                  'leaf Hessian scale indicates regularization magnitude; no isolated causal attribution without an ablation']}
    (out / 'diagnostics.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    (out / 'SHA256SUMS.json').write_text(json.dumps({'diagnostics.json': sha256(out / 'diagnostics.json')}, indent=2) + '\n')
    print(json.dumps({'status': 'completed', 'output': str(out), 'weighted_training_nll': {
        scope: d['in_sample_weighted_risk_cross_entropy_data_term'] for scope, d in outputs.items()}}, indent=2))


if __name__ == '__main__':
    main()
