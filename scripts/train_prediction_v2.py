"""Frozen 50-parent multi-round empirical/GBDT development comparison."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.d2_hazards import derive_hazard_rows
from leasepath.prediction_merged import evaluation_strata, load_inventory, require
from leasepath.prediction_training import (
    assign_group_folds, check_distribution, decision_state, read_json,
    score_predictions, sha256, split_fold,
)
from train_reuse_predictor import build_model, model_details


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def write_lines(path, rows):
    with path.open('w') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')


def check_history(expected):
    for directory, digest in expected.items():
        root = ROOT / directory
        require(sha256(root / 'SHA256SUMS.json') == digest, 'historical manifest changed')
        for filename, file_hash in read_json(root / 'SHA256SUMS.json').items():
            require(sha256(root / filename) == file_hash, 'historical result changed: ' + filename)


def risk_support(samples, targets, run_groups, edges):
    rows = derive_hazard_rows(samples, targets, edges)
    output = []
    for k, edge in enumerate(edges):
        selected = [r for r in rows if r['interval_index'] == k and r['loss_mask']]
        output.append({'right_s': edge, 'risk_rows': len(selected),
                       'workflows': len({r['run_id'] for r in selected}),
                       'parent_tasks': len({run_groups[r['run_id']] for r in selected}),
                       'weighted_labels': {label: sum(r['sample_weight'] for r in selected if r['label'] == label)
                                           for label in ['U', 'D', 'waiting']}})
    return output


def paired_primary(samples, targets, predictions, run_groups, edges, seed, draws):
    """Descriptive paired parent bootstrap; folds are not independent test sets."""
    import numpy as np
    by_parent = {}
    for group in sorted(set(run_groups.values())):
        selected = [s for s in samples if run_groups[s['run_id']] == group]
        ids = {s['sample_id'] for s in selected}
        scores = score_predictions(selected, [t for t in targets if t['sample_id'] in ids],
                                   {m: {sid: p for sid, p in rows.items() if sid in ids}
                                    for m, rows in predictions.items()}, run_groups, edges)
        values = {name: next(h['macro_parent_task']['brier3'] for h in score['all']['horizons']
                             if h['horizon_s'] == .1) for name, score in scores.items()}
        by_parent[group] = values
    delta = np.asarray([v['gbdt'] - v['conditional'] for v in by_parent.values()])
    rng = np.random.default_rng(seed)
    means = delta[rng.integers(0, len(delta), size=(draws, len(delta)))].mean(axis=1)
    return {'scope': 'descriptive paired parent resampling of development OOF scores; not independent-test inference',
            'horizon_s': .1, 'comparison': 'GBDT minus conditional empirical; negative favors GBDT',
            'parent_scores': by_parent, 'mean_delta': float(delta.mean()),
            'bootstrap_percentile_95': [float(v) for v in np.quantile(means, [.025, .975])],
            'bootstrap_draws': draws, 'seed': seed,
            'gbdt_better_parents': int(sum(delta < 0)), 'conditional_better_parents': int(sum(delta > 0)),
            'tied_parents': int(sum(delta == 0))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/prediction/prediction_v2_50parents.json')
    parser.add_argument('--output', type=Path, default=ROOT / '.artifacts/prediction/prediction_v2_50parents')
    parser.add_argument('--summary-output', type=Path, default=ROOT / 'results/prediction/prediction_v2_50parents')
    args = parser.parse_args()
    config = read_json(args.config)
    require(config['models'] == ['global', 'conditional', 'gbdt'] and
            config['feature_protocol'] == 'same_tool_phase_elapsed_bucket_information_as_conditional_baseline' and
            config['selection_policy'] == 'single_predeclared_configuration_no_search_no_early_stopping',
            'unsupported comparison protocol')
    for filename, digest in config['reference_configuration_sha256'].items():
        require(sha256(ROOT / filename) == digest, 'reference configuration changed')
    baseline = read_json(ROOT / config['baseline_config'])
    previous = read_json(ROOT / config['previous_training_config'])
    require(config['gbdt'] == previous['gbdt'] and config['folds'] == previous['folds']
            and config['seed'] == previous['seed'], 'v2 must preserve predeclared model settings')
    check_history(config['historical_result_manifest_sha256'])
    loaded, inputs = load_inventory(ROOT, ROOT / config['inventory'],
                                   expected_inventory_sha256=config['inventory_sha256'],
                                   expected_result_manifests=config['round_result_manifest_sha256'])
    for scope, counts in config['expected_views'].items():
        require(all(loaded[scope]['summary'][k] == v for k, v in counts.items()), 'unexpected dataset size')
    groups = loaded['all-observed']['run_groups']
    assignments = assign_group_folds(groups.values(), config['folds'], config['seed'])
    args.output.mkdir(parents=True, exist_ok=False)
    args.summary_output.mkdir(parents=True, exist_ok=False)
    write(args.summary_output / 'split-manifest.json', {
        'scope': 'parent-task grouped development CV; same assignment for all models and both views',
        'assignment_rule': 'sort SHA256(seed:parent_group), round-robin folds',
        'seed': config['seed'], 'folds': config['folds'], 'assignments': assignments,
        'workflows': {w: {**loaded['all-observed']['workflow_metadata'][w], 'fold': assignments[g]}
                      for w, g in groups.items()}})
    sources = ['scripts/train_prediction_v2.py', 'scripts/train_reuse_predictor.py',
               'src/leasepath/prediction_merged.py', 'src/leasepath/prediction_training.py',
               'src/leasepath/gbdt_predictor.py', 'src/leasepath/reuse_predictor.py',
               'src/leasepath/d2_hazards.py', 'src/leasepath/prediction_state.py',
               'tests/test_prediction_merged.py', 'tests/test_prediction_training.py',
               'tests/test_gbdt_predictor.py', 'pyproject.toml']
    source_hashes = {}
    for filename in sources:
        dest = args.output / 'source' / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / filename, dest)
        source_hashes[filename] = sha256(dest)
    write(args.output / 'configuration.json', {'training': config, 'baseline': baseline})
    shutil.copy2(args.config, args.output / 'training-config.json')
    shutil.copy2(ROOT / config['inventory'], args.summary_output / 'input-inventory.json')
    import lightgbm, numpy, scipy
    manifest = {'status': 'started', 'started_at_utc': datetime.now(timezone.utc).isoformat(),
                'configuration': config, 'baseline_configuration': baseline,
                'configuration_sha256': sha256(args.config), 'input_sha256': inputs, 'source_sha256': source_hashes,
                'versions': {'python': platform.python_version(), 'lightgbm': lightgbm.__version__,
                             'numpy': numpy.__version__, 'scipy': scipy.__version__, 'machine': platform.machine()}}
    write(args.summary_output / 'run-manifest.json', manifest)
    all_metrics, strata_metrics, fold_records, summaries, examples, comparisons = {}, {}, {}, {}, {}, {}
    for scope, data in loaded.items():
        samples, targets, run_groups = data['samples'], data['targets'], data['run_groups']
        merge_dir = args.output / 'merged' / scope
        merge_dir.mkdir(parents=True, exist_ok=False)
        write_lines(merge_dir / 'snapshots-workflow-weighted.jsonl', samples)
        write_lines(merge_dir / 'targets.jsonl', targets)
        write_lines(merge_dir / 'episode_labels.jsonl', data['episodes'].values())
        write_lines(merge_dir / 'evaluation-metadata-only.jsonl',
                    ({'sample_id': sid, **meta} for sid, meta in data['metadata'].items()))
        write(merge_dir / 'summary.json', {**data['summary'],
              'weighting': 'each workflow total 1; episodes equal within workflow',
              'feature_protocol': config['feature_protocol'],
              'warning': 'evaluation metadata may contain future duration/quality; never model features',
              'output_sha256': {p.name: sha256(p) for p in merge_dir.iterdir() if p.is_file()}})
        predictions = {name: {} for name in config['models']}
        fold_records[scope] = []
        for fold in range(config['folds']):
            train, train_targets, validation, _ = split_fold(samples, targets, run_groups, assignments, fold)
            entry = {'fold': fold, 'training_groups': sorted({run_groups[s['run_id']] for s in train}),
                     'validation_groups': sorted({run_groups[s['run_id']] for s in validation}),
                     'training_workflows': len({s['run_id'] for s in train}),
                     'validation_workflows': len({s['run_id'] for s in validation}),
                     'training_snapshots': len(train), 'validation_snapshots': len(validation),
                     'training_risk_support': risk_support(train, train_targets, run_groups, baseline['right_edges']),
                     'models': {}}
            for name in config['models']:
                model = build_model(name, baseline, config).fit(train, train_targets)
                path = args.output / f'{scope}-fold-{fold}-{name}.json'
                model.save(path)
                restored = type(model).load(path)
                for sample in validation:
                    sid = sample['sample_id']
                    require(sid not in predictions[name], 'more than one OOF prediction')
                    distribution = restored.predict(sample['features'])
                    require(distribution == model.predict(sample['features']), 'save/load changed OOF prediction')
                    check_distribution(distribution)
                    predictions[name][sid] = distribution
                entry['models'][name] = {**model_details(model), 'sha256': sha256(path)}
            fold_records[scope].append(entry)
            print(json.dumps({'scope': scope, 'fold': fold, 'training_parents': len(entry['training_groups']),
                              'validation_parents': len(entry['validation_groups']), 'completed': True}), flush=True)
        all_metrics[scope] = score_predictions(samples, targets, predictions, run_groups,
                                               baseline['right_edges'], config['calibration_edges'])
        strata_metrics[scope] = {}
        for stratum, selected_ids in sorted(evaluation_strata(samples, data['metadata']).items()):
            ids = set(selected_ids)
            subset = [s for s in samples if s['sample_id'] in ids]
            subpred = {name: {sid: values for sid, values in rows.items() if sid in ids}
                       for name, rows in predictions.items()}
            scores = score_predictions(subset, [t for t in targets if t['sample_id'] in ids], subpred,
                                       run_groups, baseline['right_edges'], config['calibration_edges'])
            strata_metrics[scope][stratum] = {
                'selection_scope': 'evaluation only; duration and eventual quality are post-outcome strata',
                'snapshots': len(subset), 'episodes': len({(s['run_id'], s['idle_episode_id']) for s in subset}),
                'workflows': len({s['run_id'] for s in subset}),
                'parent_tasks': len({run_groups[s['run_id']] for s in subset}),
                'models': {name: rows['all'] for name, rows in scores.items()}}
        comparisons[scope] = paired_primary(samples, targets, predictions, run_groups, baseline['right_edges'],
                                            config['seed'], config['paired_bootstrap_draws'])
        selected_examples = {}
        oof_path = args.output / f'{scope}-oof-predictions.jsonl'
        with oof_path.open('w') as stream:
            for sample in samples:
                sid = sample['sample_id']
                record = {'sample_id': sid, 'run_id': sample['run_id'], 'group_id': run_groups[sample['run_id']],
                          'fold': assignments[run_groups[sample['run_id']]], 'decision_state': decision_state(sample),
                          'scope': 'held_out_parent_task_development_cv',
                          'models': {name: predictions[name][sid] for name in config['models']}}
                stream.write(json.dumps(record, allow_nan=False) + '\n')
                # Pick examples using current observed state, never future event time.
                keys = [decision_state(sample)]
                if sample['features']['elapsed_idle_s'] >= 5:
                    keys.append('elapsed_ge5s')
                if data['metadata'][sid]['current_suite_is_retry']:
                    keys.append('observed_current_retry')
                for key in keys:
                    selected_examples.setdefault(key, {**record, 'features': sample['features']})
        examples[scope] = selected_examples
        final = {}
        for name in config['models']:
            model = build_model(name, baseline, config).fit(samples, targets)
            path = args.summary_output / f'{scope}-{name}-model.json'
            model.save(path)
            restored = type(model).load(path)
            for sample in samples:
                distribution = restored.predict(sample['features'])
                require(distribution == model.predict(sample['features']), 'full-fit save/load changed probabilities')
                check_distribution(distribution)
            timings = []
            for sample in samples[:100]:
                start = time.perf_counter_ns()
                restored.predict(sample['features'])
                timings.append((time.perf_counter_ns() - start) / 1e6)
            final[name] = {**model_details(model), 'model_sha256': sha256(path), 'save_load_identical': True,
                           'api_predict_ms_median': float(numpy.median(timings)),
                           'api_predict_ms_p95': float(numpy.quantile(timings, .95)), 'timing_calls': len(timings)}
        summaries[scope] = {
            **data['summary'], 'decision_states': dict(Counter(decision_state(s) for s in samples)),
            'primary_oof_macro_parent_task_brier3_at_0.1s': {
                name: next(h['macro_parent_task']['brier3'] for h in rows['all']['horizons'] if h['horizon_s'] == .1)
                for name, rows in all_metrics[scope].items()},
            'full_development_risk_support': risk_support(samples, targets, run_groups, baseline['right_edges']),
            'final_development_models': final, 'oof_sha256': sha256(oof_path),
            'merged_files_sha256': {p.name: sha256(p) for p in merge_dir.iterdir() if p.is_file()}}
    # Check immutable inputs again after fitting, including previous-round models.
    for filename, digest in inputs.items():
        require(sha256(ROOT / filename) == digest, 'input changed during fitting: ' + filename)
    for filename, digest in source_hashes.items():
        require(sha256(ROOT / filename) == digest, 'source changed during fitting: ' + filename)
    check_history(config['historical_result_manifest_sha256'])
    write(args.summary_output / 'metrics.json', all_metrics)
    write(args.summary_output / 'stratified-metrics.json', strata_metrics)
    write(args.summary_output / 'cv-models.json', fold_records)
    write(args.summary_output / 'prediction-examples.json', examples)
    write(args.summary_output / 'paired-parent-comparison.json', comparisons)
    summary = {'status': 'passed', 'stage': config['stage'], 'scope': config['scope'],
               'primary_metric': config['primary_metric'], 'datasets': summaries,
               'feature_information': ['tool_name', 'phase', 'elapsed_idle_bin', 'interval_index'],
               'checks': {'same_parent_fold_across_rounds_profiles_scales_and_views': True,
                          'one_oof_prediction_per_sample': True, 'same_model_inputs_and_parameters_as_previous': True,
                          'workflow_weights_normalized': True, 'input_source_hashes_frozen_before_fitting': True,
                          'future_evaluation_metadata_excluded_from_model_features': True,
                          'probability_recursion_and_roundtrip_verified': True, 'historical_results_unchanged': True},
               'metric_definitions': read_json(ROOT / 'results/prediction/gbdt_review_2/summary.json')['metric_definitions'],
               'limitations': ['50 parent tasks from two development source families; 44 are public MBPP',
                   'only 6 file parent tasks; 3 parents supply the 3-10 second repair episodes',
                   'no natural C, >=10 second episodes, independent source/time test or online policy experiment',
                   'qualified filters on eventual success and covers 47 parents',
                   'same coarse tool/phase/elapsed information; enhancement sidecars used only for evaluation strata',
                   'same parameters, no search or early stopping; old/new metric differences also reflect data and fold changes',
                   'bootstrap is descriptive; development OOF folds share training data',
                   'CPU prediction API timing excludes state collection and scheduling'],
               'artifacts': str(args.output.resolve())}
    write(args.summary_output / 'summary.json', summary)
    manifest.update(status='passed', completed_at_utc=datetime.now(timezone.utc).isoformat())
    write(args.summary_output / 'run-manifest.json', manifest)
    write(args.summary_output / 'SHA256SUMS.json',
          {p.name: sha256(p) for p in sorted(args.summary_output.iterdir()) if p.is_file()})
    print(json.dumps({'status': 'passed', 'scores': {scope: d['primary_oof_macro_parent_task_brier3_at_0.1s']
                                                   for scope, d in summaries.items()}}), flush=True)


if __name__ == '__main__':
    main()
