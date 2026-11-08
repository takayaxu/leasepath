"""Frozen parent-task CV and full-development fitting for GBDT review part 2."""
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
from leasepath.gbdt_predictor import GBDTRiskPredictor
from leasepath.prediction_training import (
    assign_group_folds, check_distribution, decision_state, load_development_dataset,
    read_json, score_predictions, sha256, split_fold,
)
from leasepath.reuse_predictor import EmpiricalRiskPredictor


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def build_model(name, baseline, config):
    if name == 'gbdt':
        return GBDTRiskPredictor(right_edges=baseline['right_edges'], elapsed_edges=baseline['elapsed_edges'],
                                 parameters=config['gbdt'], seed=config['seed'])
    return EmpiricalRiskPredictor(right_edges=baseline['right_edges'], elapsed_edges=baseline['elapsed_edges'],
                                  conditional=name == 'conditional', prior_weight=baseline['prior_weight'],
                                  min_group_workflows=baseline['min_group_workflows'])


def model_details(model):
    if isinstance(model, GBDTRiskPredictor):
        return {'training_risk_rows': model.training_rows, 'boosting_iterations': model.booster.current_iteration(),
                'trees': model.booster.num_trees(), 'maximum_leaves': max(
                    tree['num_leaves'] for tree in model.booster.dump_model()['tree_info']),
                'vocabularies': model.vocabularies, 'elapsed_bins': model.elapsed_bins,
                'supported_right_edges': [edge for edge, record in zip(model.support.right_edges, model.support.global_counts)
                                          if sum(record['counts'])]}
    return {'fitted_groups': len(model.groups), 'supported_right_edges': [
        edge for edge, record in zip(model.right_edges, model.global_counts) if sum(record['counts'])]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/prediction/gbdt_review_2.json')
    parser.add_argument('--output', type=Path, default=ROOT / '.artifacts/prediction/gbdt_review_2')
    parser.add_argument('--summary-output', type=Path, default=ROOT / 'results/prediction/gbdt_review_2')
    args = parser.parse_args()
    config = read_json(args.config)
    baseline_path = ROOT / config['baseline_config']
    baseline = read_json(baseline_path)
    previous = read_json(ROOT / config['baseline_provenance'])
    if baseline != previous['configuration']:
        raise ValueError('baseline configuration changed after review part 1')
    if (config['models'] != ['global', 'conditional', 'gbdt']
            or config['feature_protocol'] != 'same_tool_phase_elapsed_bucket_information_as_conditional_baseline'
            or config['selection_policy'] != 'single_predeclared_configuration_no_search_no_early_stopping'):
        raise ValueError('configuration differs from the implemented comparison protocol')
    quality_path = ROOT / baseline['execution_quality']
    if sha256(quality_path) != previous['execution_quality_sha256']:
        raise ValueError('execution-quality provenance changed')
    quality = read_json(quality_path)['executions']
    run_groups = {r['workflow_id']: r['group_id'] for r in quality}
    if len(run_groups) != len(quality):
        raise ValueError('duplicate workflow provenance')
    loaded, input_hashes = {}, {}
    for name, relative in baseline['datasets'].items():
        path = ROOT / relative
        if name not in {'all-main', 'qualified'}:
            raise ValueError('unknown dataset scope')
        for filename, digest in previous['input_sha256'][name].items():
            if sha256(path / filename) != digest:
                raise ValueError('input differs from baseline review: ' + name + '/' + filename)
        selected = {r['workflow_id'] for r in quality if r['criteria']['backend_event_audit']
                    and (name == 'all-main' or r['quality_passed'])}
        loaded[name] = load_development_dataset(path, selected)
        input_hashes[name] = {p: sha256(path / p) for p in [
            'summary.json', 'events.jsonl', 'episode_labels.jsonl', 'snapshots.jsonl', 'targets.jsonl']}
    assignments = assign_group_folds({run_groups[s['run_id']] for s in loaded['all-main'][0]},
                                      config['folds'], config['seed'])
    args.output.mkdir(parents=True, exist_ok=False)
    args.summary_output.mkdir(parents=True, exist_ok=False)
    # Save split membership and copy exact source/config before any model fitting.
    split_manifest = {'scope': 'development_parent_task_grouped_cv_not_independent_source_test',
                      'assignment_rule': 'sort SHA256(seed:parent_group), round-robin folds',
                      'seed': config['seed'], 'folds': config['folds'], 'assignments': assignments,
                      'workflows': {s['run_id']: {'group_id': run_groups[s['run_id']], 'fold': assignments[run_groups[s['run_id']]]}
                                    for s in loaded['all-main'][0]}}
    write(args.summary_output / 'split-manifest.json', split_manifest)
    sources = ['src/leasepath/gbdt_predictor.py', 'src/leasepath/prediction_training.py',
               'src/leasepath/reuse_predictor.py', 'src/leasepath/d2_hazards.py',
               'src/leasepath/prediction_state.py', 'scripts/train_reuse_predictor.py',
               'tests/test_gbdt_predictor.py', 'tests/test_prediction_training.py', 'pyproject.toml']
    source_hashes = {}
    for relative in sources:
        source = ROOT / relative
        destination = args.output / 'source' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        source_hashes[relative] = sha256(source)
    write(args.output / 'configuration.json', {'training': config, 'baseline': baseline})
    import lightgbm, numpy, scipy
    write(args.summary_output / 'run-manifest.json', {
        'status': 'started', 'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'configuration': config, 'baseline_configuration': baseline,
        'config_sha256': sha256(args.config), 'baseline_provenance_sha256': sha256(ROOT / config['baseline_provenance']),
        'quality_sha256': sha256(quality_path), 'input_sha256': input_hashes, 'source_sha256': source_hashes,
        'versions': {'python': platform.python_version(), 'lightgbm': lightgbm.__version__,
                     'numpy': numpy.__version__, 'scipy': scipy.__version__, 'machine': platform.machine()}})
    all_metrics, dataset_summaries, examples, cv_models = {}, {}, {}, {}
    for dataset, (samples, targets, source_summary) in loaded.items():
        predictions = {name: {} for name in config['models']}
        cv_models[dataset] = []
        for fold in range(config['folds']):
            train, train_targets, validation, _ = split_fold(samples, targets, run_groups, assignments, fold)
            entry = {'fold': fold,
                     'training_groups': sorted({run_groups[s['run_id']] for s in train}),
                     'validation_groups': sorted({run_groups[s['run_id']] for s in validation}),
                     'training_workflows': len({s['run_id'] for s in train}),
                     'validation_workflows': len({s['run_id'] for s in validation}),
                     'training_snapshots': len(train), 'validation_snapshots': len(validation), 'models': {}}
            for name in config['models']:
                model = build_model(name, baseline, config).fit(train, train_targets)
                model_path = args.output / f'{dataset}-fold-{fold}-{name}.json'
                model.save(model_path)
                restored = type(model).load(model_path)
                for sample in validation:
                    sid = sample['sample_id']
                    if sid in predictions[name]:
                        raise ValueError('sample predicted in more than one held-out fold')
                    distribution = restored.predict(sample['features'])
                    if distribution != model.predict(sample['features']):
                        raise ValueError('save/load changed held-out probabilities')
                    check_distribution(distribution)
                    predictions[name][sid] = distribution
                entry['models'][name] = {**model_details(model), 'sha256': sha256(model_path)}
            cv_models[dataset].append(entry)
            print(json.dumps({'dataset': dataset, 'fold': fold, 'training_groups': entry['training_groups'],
                              'validation_groups': entry['validation_groups'], 'completed': True}), flush=True)
        all_metrics[dataset] = score_predictions(samples, targets, predictions, run_groups,
                                                 baseline['right_edges'], config['calibration_edges'])
        selected_examples = {}
        prediction_path = args.output / (dataset + '-oof-predictions.jsonl')
        with prediction_path.open('w') as stream:
            for sample in samples:
                sid, state = sample['sample_id'], decision_state(sample)
                record = {'sample_id': sid, 'run_id': sample['run_id'], 'group_id': run_groups[sample['run_id']],
                          'fold': assignments[run_groups[sample['run_id']]], 'decision_state': state,
                          'scope': 'held_out_parent_task_development_cv',
                          'models': {name: predictions[name][sid] for name in config['models']}}
                stream.write(json.dumps(record, allow_nan=False) + '\n')
                if state not in selected_examples:
                    selected_examples[state] = {**record, 'features': sample['features']}
        examples[dataset] = selected_examples
        final = {}
        for name in config['models']:
            model = build_model(name, baseline, config).fit(samples, targets)
            model_path = args.summary_output / f'{dataset}-{name}-model.json'
            model.save(model_path)
            restored = type(model).load(model_path)
            for sample in samples:
                distribution = restored.predict(sample['features'])
                if distribution != model.predict(sample['features']):
                    raise ValueError('save/load changed full-development model')
                check_distribution(distribution)
            # Descriptive CPU API cost on current data, not full online overhead.
            timings = []
            for sample in samples[:100]:
                start = time.perf_counter_ns()
                restored.predict(sample['features'])
                timings.append((time.perf_counter_ns() - start) / 1e6)
            final[name] = {**model_details(model), 'model_sha256': sha256(model_path),
                           'save_load_identical': True, 'api_predict_ms_median': float(numpy.median(timings)),
                           'api_predict_ms_p95': float(numpy.quantile(timings, .95)), 'timing_calls': len(timings)}
        primary = {}
        for name in config['models']:
            primary[name] = next(h['macro_parent_task']['brier3'] for h in all_metrics[dataset][name]['all']['horizons']
                                 if h['horizon_s'] == .1)
        dataset_summaries[dataset] = {
            'workflows': source_summary['runs'], 'parent_tasks': len({run_groups[s['run_id']] for s in samples}),
            'episodes': source_summary['idle_episodes'], 'snapshots': len(samples), 'outcomes': source_summary['outcomes'],
            'decision_states': dict(Counter(decision_state(s) for s in samples)),
            'primary_oof_macro_parent_task_brier3_at_0.1s': primary,
            'final_development_models': final, 'oof_sha256': sha256(prediction_path)}
    write(args.summary_output / 'metrics.json', all_metrics)
    write(args.summary_output / 'cv-models.json', cv_models)
    write(args.summary_output / 'prediction-examples.json', examples)
    summary = {'status': 'passed', 'stage': config['stage'], 'scope': config['scope'],
               'primary_metric': config['primary_metric'], 'datasets': dataset_summaries,
               'checks': {'parent_group_disjoint_folds': True, 'one_oof_prediction_per_sample': True,
                          'input_source_hashes_frozen_before_training': True, 'same_feature_information': True,
                          'probability_and_recursion_checks': True, 'save_load_identical': True},
               'metric_definitions': {'brier3': 'sum of squared errors for cumulative [F_U,F_D,S], range [0,2]',
                   'macro_parent_task': 'weighted within workflow, mean workflows within parent, equal mean parents',
                   'risk_row_nll': 'conditional interval log loss, not whole-path likelihood; floor=1e-15',
                   'censoring': 'cumulative observable cases only, no IPCW; unknown cases counted, not negatives',
                   'calibration': 'descriptive bins weighted by workflow-balanced snapshot weights; no post-fit calibration'},
               'limitations': ['ten parent tasks from one public source; all folds are development diagnostics',
                   'all observed episodes below one second; longer risk sets unsupported',
                   'qualified subset depends on eventual task success', 'no parameter search or early stopping; no independent model-selection test',
                   'shared states include queued requests; inspect idle_start and waiting_tool separately',
                   'CPU API timings exclude state collection, scheduling and cache policy overhead',
                   'no online policy integration or cache benefit claim'], 'artifacts': str(args.output.resolve())}
    write(args.summary_output / 'summary.json', summary)
    manifest = read_json(args.summary_output / 'run-manifest.json')
    manifest.update(status='passed', completed_at_utc=datetime.now(timezone.utc).isoformat())
    write(args.summary_output / 'run-manifest.json', manifest)
    write(args.summary_output / 'SHA256SUMS.json', {p.name: sha256(p) for p in sorted(args.summary_output.iterdir()) if p.is_file()})
    print(json.dumps({'status': 'passed', 'primary_metric': summary['primary_metric'],
                      'scores': {name: d['primary_oof_macro_parent_task_brier3_at_0.1s'] for name, d in dataset_summaries.items()}}))


if __name__ == '__main__':
    main()
