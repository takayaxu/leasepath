"""Fit review-stage empirical baselines; predictions are training previews only."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.reuse_predictor import EmpiricalRiskPredictor


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def load_dataset(path, expected_workflows):
    summary = read(path / 'summary.json')
    for name in ['events.jsonl', 'episode_labels.jsonl', 'snapshots.jsonl', 'targets.jsonl']:
        if sha(path / name) != summary['output_sha256'][name]:
            raise ValueError(f'dataset provenance differs: {path / name}')
    samples, targets = rows(path / 'snapshots.jsonl'), rows(path / 'targets.jsonl')
    if {s['run_id'] for s in samples} != expected_workflows:
        raise ValueError('dataset workflow membership differs from quality manifest')
    episodes = defaultdict(set)
    episode_weights = defaultdict(float)
    for sample in samples:
        if sample['split'] != 'pilot_development_only':
            raise ValueError('this review runner accepts development data only')
        episodes[sample['run_id']].add(sample['idle_episode_id'])
        episode_weights[sample['run_id'], sample['idle_episode_id']] += sample['sample_weight']
    if not all(math.isclose(v, 1., rel_tol=1e-9) for v in episode_weights.values()):
        raise ValueError('source snapshots are not episode-balanced')
    weighted = deepcopy(samples)
    for sample in weighted:
        sample['sample_weight'] /= len(episodes[sample['run_id']])
    return weighted, targets, summary


def check_probabilities(predictions):
    previous_u, previous_d, previous_s = 0., 0., 1.
    for row in predictions:
        if not row['supported']:
            if any(row[k] is not None for k in ['hU', 'hD', 'h0', 'F_U', 'F_D', 'survival']):
                raise ValueError('unsupported interval contains an estimate')
            continue
        hu, hd, h0 = (row[k] for k in ['hU', 'hD', 'h0'])
        fu, fd, survival = (row[k] for k in ['F_U', 'F_D', 'survival'])
        if not all(math.isfinite(v) and -1e-12 <= v <= 1 + 1e-12 for v in [hu, hd, h0, fu, fd, survival]):
            raise ValueError('invalid probability')
        if not math.isclose(hu + hd + h0, 1.) or not math.isclose(fu + fd + survival, 1.):
            raise ValueError('probabilities do not sum to one')
        if fu + 1e-12 < previous_u or fd + 1e-12 < previous_d or survival > previous_s + 1e-12:
            raise ValueError('cumulative probabilities are not monotone')
        previous_u, previous_d, previous_s = fu, fd, survival


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/prediction/empirical_risk_baseline.json')
    parser.add_argument('--output', type=Path, default=ROOT / '.artifacts/prediction/empirical_baseline_review_1')
    parser.add_argument('--summary-output', type=Path, default=ROOT / 'results/prediction/empirical_baseline_review_1')
    args = parser.parse_args()
    config = read(args.config)
    if config['weighting'] != 'each_workflow_total_1_each_episode_equal_within_workflow':
        raise ValueError('unsupported weighting')
    if config['variants'] != ['global', 'conditional'] or config['conditioning_features'] != ['tool_name', 'phase', 'elapsed_idle_s']:
        raise ValueError('configuration does not match implemented empirical models')
    quality_path = ROOT / config['execution_quality']
    quality = read(quality_path)['executions']
    loaded = {}
    for name, relative in config['datasets'].items():
        if name not in {'all-main', 'qualified'}:
            raise ValueError('unknown dataset scope')
        selected = {r['workflow_id'] for r in quality if r['criteria']['backend_event_audit']
                    and (name == 'all-main' or r['quality_passed'])}
        loaded[name] = load_dataset(ROOT / relative, selected)
    args.output.mkdir(parents=True, exist_ok=False)
    args.summary_output.mkdir(parents=True, exist_ok=False)
    summaries, examples, input_hashes = {}, {}, {}
    for name, (samples, targets, source_summary) in loaded.items():
        path = ROOT / config['datasets'][name]
        input_hashes[name] = {p: sha(path / p) for p in ['summary.json', 'snapshots.jsonl', 'targets.jsonl']}
        workflow_weights = defaultdict(float)
        for sample in samples:
            workflow_weights[sample['run_id']] += sample['sample_weight']
        if not all(math.isclose(v, 1.) for v in workflow_weights.values()):
            raise ValueError('workflow balancing failed')
        for variant in config['variants']:
            key = name + '-' + variant
            model = EmpiricalRiskPredictor(
                right_edges=config['right_edges'], elapsed_edges=config['elapsed_edges'],
                conditional=variant == 'conditional', min_group_workflows=config['min_group_workflows'],
                prior_weight=config['prior_weight']).fit(samples, targets)
            model_path = args.summary_output / (key + '-model.json')
            model.save(model_path)
            restored = EmpiricalRiskPredictor.load(model_path)
            prediction_path = args.output / (key + '-training-preview.jsonl')
            sources = Counter()
            selected_examples = {}
            with prediction_path.open('w') as stream:
                for sample in samples:
                    prediction = restored.predict(sample['features'])
                    if prediction != model.predict(sample['features']):
                        raise ValueError('save/load changed predictions')
                    check_probabilities(prediction)
                    sources.update(p['estimate_source'] for p in prediction)
                    record = {'sample_id': sample['sample_id'], 'run_id': sample['run_id'],
                              'prediction_scope': 'training_preview_not_held_out', 'intervals': prediction}
                    stream.write(json.dumps(record, allow_nan=False) + '\n')
                    phase = sample['features']['phase'] or 'unknown'
                    if phase not in selected_examples:
                        selected_examples[phase] = {**record, 'features': sample['features']}
            selected_runs = {s['run_id'] for s in samples}
            summaries[key] = {
                'workflows': len(selected_runs), 'tasks': len({r['group_id'] for r in quality if r['workflow_id'] in selected_runs}),
                'episodes': source_summary['idle_episodes'], 'snapshots': len(samples),
                'outcomes': source_summary['outcomes'], 'workflow_weight': 1.,
                'total_sample_weight': sum(workflow_weights.values()),
                'fitted_groups': len(model.groups),
                'supported_right_edges': [edge for edge, r in zip(model.right_edges, model.global_counts) if sum(r['counts'])],
                'estimate_sources_over_preview_intervals': dict(sources),
                'model_sha256': sha(model_path), 'training_preview_sha256': sha(prediction_path),
                'save_load_identical': True, 'probability_checks_passed': True}
            examples[key] = selected_examples
    summary = {'status': 'passed', 'stage': config['stage'], 'scope': config['scope'],
               'models': summaries, 'held_out_evaluation': 'not implemented in review part 1',
               'gbdt': 'not implemented; awaiting review',
               'limitations': ['ten parent tasks from one public source; development data only',
                               'previews use the fitting data and measure no generalization',
                               'all observed episodes below one second; no long-horizon extrapolation',
                               'conditional baseline uses tool/phase/elapsed buckets; not a full CacheWise reproduction',
                               'qualified subset conditions on eventual task success'],
               'artifacts': str(args.output.resolve())}
    write(args.summary_output / 'summary.json', summary)
    write(args.summary_output / 'prediction-examples.json', examples)
    write(args.summary_output / 'run-manifest.json', {
        'configuration': config, 'input_sha256': input_hashes,
        'execution_quality_sha256': sha(quality_path),
        'source_sha256': {name: sha(ROOT / name) for name in [
            'src/leasepath/reuse_predictor.py', 'src/leasepath/d2_hazards.py',
            'src/leasepath/prediction_state.py', 'scripts/fit_empirical_risk.py']}})
    write(args.summary_output / 'SHA256SUMS.json', {
        p.name: sha(p) for p in sorted(args.summary_output.iterdir()) if p.is_file()})
    print(json.dumps({'status': 'passed', 'scope': summary['scope'],
                      'models': {k: {'workflows': v['workflows'], 'snapshots': v['snapshots'],
                                     'supported_right_edges': v['supported_right_edges']} for k, v in summaries.items()}}))


if __name__ == '__main__':
    main()
