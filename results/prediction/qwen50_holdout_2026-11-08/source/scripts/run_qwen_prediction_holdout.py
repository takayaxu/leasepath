"""Train on frozen parent tasks, predict test features, then open test labels."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.prediction_training import check_distribution, read_json, score_predictions, sha256
from leasepath.qwen_prediction_holdout import load_dataset, partition, require, select_parents
from train_reuse_predictor import build_model, model_details


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def lines(path, rows):
    path.write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False) + '\n' for r in rows))


def counts(samples, targets, metadata):
    episodes = {(s['run_id'], s['idle_episode_id']) for s in samples}
    episode_outcomes = {(s['run_id'], s['idle_episode_id']): t['outcome'] for s, t in zip(samples, targets)}
    return {'parents': len({metadata[s['sample_id']]['task'] for s in samples}),
            'episodes': len(episodes), 'snapshots': len(samples),
            'episode_outcomes': dict(Counter(episode_outcomes.values()))}


def verify(output):
    """Fresh model loads, train-only table reconstruction and independent rescoring."""
    from leasepath.gbdt_predictor import GBDTRiskPredictor
    from leasepath.reuse_predictor import EmpiricalRiskPredictor
    from leasepath.prediction_training import read_jsonl
    config = read_json(output / 'configuration.json')
    catalog, samples, targets, metadata, groups = load_dataset(ROOT / config['dataset'], config['input_sha256'])
    assignments = select_parents(catalog, config['strata'], config['seed'])
    require(assignments == read_json(output / 'split-manifest.json')['assignments'], 'split changed')
    parts = partition(samples, targets, metadata, assignments)
    train, train_targets = parts['train']
    test, test_targets = parts['test']
    predictions = {name: {} for name in config['models']}
    saved = {r['sample_id']: r for r in read_jsonl(output / 'test-predictions.jsonl')}
    require(len(saved) == len(test), 'prediction count differs')
    for name in config['models']:
        model = (GBDTRiskPredictor if name == 'gbdt' else EmpiricalRiskPredictor).load(output / f'{name}-model.json')
        expected = build_model('global' if name == 'gbdt' else name, config['baseline'], config).fit(train, train_targets)
        require((model.support if name == 'gbdt' else model).to_dict() == expected.to_dict(),
                'model counts include data outside training split')
        for sample in test:
            sid = sample['sample_id']
            predictions[name][sid] = model.predict(sample['features'])
            require(predictions[name][sid] == saved[sid]['models'][name], 'prediction replay differs')
    scores = score_predictions(test, test_targets, predictions, groups, config['baseline']['right_edges'])
    require(scores == read_json(output / 'metrics.json'), 'recomputed metrics differ')
    for name, digest in read_json(output / 'run-manifest.json')['source_sha256'].items():
        require(sha256(ROOT / name) == digest, 'experiment source changed')
    result = {'status': 'passed', 'train_only_empirical_and_gbdt_support': True,
              'saved_predictions_replayed': len(test) * len(config['models']),
              'metrics_recomputed': True, 'parent_split_disjoint': True,
              'input_hashes_verified': True, 'source_hashes_verified': True}
    write(output / 'verification.json', result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/prediction/qwen50_holdout_2026-11-08.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'results/prediction/qwen50_holdout_2026-11-08')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.verify_only:
        print(json.dumps(verify(args.output)))
        return
    config = read_json(args.config)
    require(config['models'] == ['global', 'conditional', 'gbdt'], 'unexpected models')
    require(config['selection_policy'] == 'single_configuration_no_search_no_early_stopping', 'unexpected tuning policy')
    catalog, samples, targets, metadata, groups = load_dataset(ROOT / config['dataset'], config['input_sha256'])
    assignments = select_parents(catalog, config['strata'], config['seed'])
    parts = partition(samples, targets, metadata, assignments)
    require(sum(v == 'test' for v in assignments.values()) == 10 and len(assignments) == 50, 'unexpected split size')
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output / 'configuration.json', config)
    write(args.output / 'split-manifest.json', {
        'scope': 'internal held-out analysis intents from previously inspected development data',
        'rule': 'within fixed semantic strata, sort SHA256(seed:source_group); select fixed test_count',
        'seed': config['seed'], 'assignments': assignments,
        'strata': config['strata'],
        'tasks': [{k: r[k] for k in ['task', 'source_group', 'intent', 'run_id']} | {'split': assignments[r['task']]}
                  for r in catalog],
        'sizes': {name: counts(*part, metadata) for name, part in parts.items()}})
    sources = ['scripts/run_qwen_prediction_holdout.py', 'scripts/train_reuse_predictor.py',
               'src/leasepath/qwen_prediction_holdout.py', 'src/leasepath/prediction_training.py',
               'src/leasepath/prediction_state.py', 'src/leasepath/d2_hazards.py',
               'src/leasepath/gbdt_predictor.py', 'src/leasepath/reuse_predictor.py']
    for name in sources:
        dest = args.output / 'source' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dest)
    import lightgbm, numpy, scipy
    manifest = {'status': 'started', 'started_at_utc': datetime.now(timezone.utc).isoformat(),
                'config_sha256': sha256(args.config), 'input_sha256': config['input_sha256'],
                'source_sha256': {p: sha256(ROOT / p) for p in sources},
                'versions': {'python': platform.python_version(), 'lightgbm': lightgbm.__version__,
                             'numpy': numpy.__version__, 'scipy': scipy.__version__}}
    write(args.output / 'run-manifest.json', manifest)
    for name, (subset, labels) in parts.items():
        lines(args.output / f'{name}-features.jsonl', subset)
        lines(args.output / f'{name}-targets.jsonl', labels)
    lines(args.output / 'evaluation-metadata-only.jsonl', ({'sample_id': sid, **row} for sid, row in metadata.items()))
    train, train_targets = parts['train']
    test, test_targets = parts['test']
    predictions, details = {}, {}
    # Test labels are not passed to fit/predict, support, category encoding or model selection.
    for name in config['models']:
        began = time.perf_counter()
        model = build_model(name, config['baseline'], config).fit(train, train_targets)
        model.save(args.output / f'{name}-model.json')
        restored = type(model).load(args.output / f'{name}-model.json')
        predictions[name] = {}
        sources_used, timings = Counter(), []
        for sample in test:
            start = time.perf_counter_ns()
            distribution = restored.predict(sample['features'])
            timings.append((time.perf_counter_ns() - start) / 1e6)
            require(distribution == model.predict(sample['features']), 'save/load mismatch')
            check_distribution(distribution)
            predictions[name][sample['sample_id']] = distribution
            sources_used.update(r['estimate_source'] for r in distribution if r['supported'])
        details[name] = {**model_details(model), 'fit_and_predict_wall_s': time.perf_counter() - began,
                         'model_sha256': sha256(args.output / f'{name}-model.json'),
                         'prediction_estimate_sources_by_supported_interval': dict(sources_used),
                         'predict_ms_median': float(numpy.median(timings))}
    lines(args.output / 'test-predictions.jsonl',
          ({'sample_id': s['sample_id'], 'run_id': s['run_id'], 'task': metadata[s['sample_id']]['task'],
            'models': {m: predictions[m][s['sample_id']] for m in config['models']}} for s in test))
    scores = score_predictions(test, test_targets, predictions, groups, config['baseline']['right_edges'])
    write(args.output / 'metrics.json', scores)
    per_parent = {}
    for task in sorted(t for t, split in assignments.items() if split == 'test'):
        subset = [s for s in test if metadata[s['sample_id']]['task'] == task]
        ids = {s['sample_id'] for s in subset}
        per_parent[task] = score_predictions(subset, [t for t in test_targets if t['sample_id'] in ids],
             {m: {sid: d for sid, d in rows.items() if sid in ids} for m, rows in predictions.items()},
             groups, config['baseline']['right_edges'])
    write(args.output / 'per-parent-metrics.json', per_parent)
    strata_scores = {}
    for label, predicate in [('elapsed_ge1s', lambda s: s['features']['elapsed_idle_s'] >= 1),
                             ('eventual_U_evaluation_only', lambda s: next(t for t in test_targets if t['sample_id'] == s['sample_id'])['outcome'] == 'legal_demand')]:
        subset = [s for s in test if predicate(s)]
        ids = {s['sample_id'] for s in subset}
        if subset:
            strata_scores[label] = score_predictions(subset, [t for t in test_targets if t['sample_id'] in ids],
                {m: {sid: d for sid, d in rows.items() if sid in ids} for m, rows in predictions.items()},
                groups, config['baseline']['right_edges'])
    write(args.output / 'stratified-metrics.json', strata_scores)
    summary = {'status': 'passed', 'scope': '40 train / 10 internal held-out parent tasks; no tuning',
               'primary_metric': config['primary_metric'],
               'sizes': {name: counts(*part, metadata) for name, part in parts.items()},
               'test_tasks': sorted(t for t, split in assignments.items() if split == 'test'),
               'primary_brier3_at_0.1s': {m: next(h['macro_parent_task']['brier3'] for h in rows['all']['horizons'] if h['horizon_s'] == .1)
                                         for m, rows in scores.items()},
               'models': details, 'learned_inputs': ['tool_name', 'phase', 'elapsed_idle_bin', 'interval_index'],
               'weighting': 'each task total 1; each episode equal within task; each snapshot equal within episode',
               'limitations': ['previously inspected development data: internal holdout, not blind external test',
                   'shared data/library families across train and test; only analysis intents held out',
                   'small test set; all-success filtered collection; no natural censoring',
                   'coarse features; per-operation identity and future durations excluded for both models',
                   'unsupported tail intervals excluded and counted, not filled using test labels',
                   'no online cache policy, JCT or new model-generation run']}
    write(args.output / 'summary.json', summary)
    verify(args.output)
    manifest.update(status='passed', completed_at_utc=datetime.now(timezone.utc).isoformat())
    write(args.output / 'run-manifest.json', manifest)
    write(args.output / 'SHA256SUMS.json', {str(p.relative_to(args.output)): sha256(p)
          for p in sorted(args.output.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS.json'})
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
