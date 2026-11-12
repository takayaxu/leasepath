"""Frozen-predictor transfer on audited live idle-owner snapshots.

Use the actual cache decision timestamp. Demand requires a subsequent native
lookup with the complete observed block prefix; do not equate eviction with D.
Exclude unresolved prefix changes rather than inventing a retirement label.
No training, holdout execution, causal-JCT claim, or success-only filtering.
"""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def read(path):
    return json.loads(Path(path).read_text())


def lines(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines()]


def horizon_label(event, remaining_s, horizon):
    if event == 'C' and remaining_s < horizon:
        return None
    return int(event == 'U' and remaining_s <= horizon)


def event_probability(distribution, event, remaining_s):
    rows = [r for r in distribution if r['supported'] and r['right_s'] <= 30]
    if not rows:
        return None
    previous = {'F_U': 0., 'F_D': 0.}
    for row in rows:
        if event in ('U', 'D') and remaining_s <= row['right_s']:
            key = 'F_' + event
            return max(0., row[key] - previous[key])
        previous = row
    if event == 'C':
        observed = [r for r in rows if r['right_s'] <= remaining_s]
        return observed[-1]['survival'] if observed else None
    return rows[-1]['survival']


def extract_snapshots(batch, tasks):
    from leasepath.real_observer import validate_stream
    for name in ('controller', 'backend'):
        validate_stream(batch / 'raw' / (name + '.jsonl'))
    controller = lines(batch / 'raw/controller.jsonl')
    requests = [e['data'] for e in lines(batch / 'raw/backend.jsonl') if e['kind'] == 'engine_request']
    retire = {e['data']['owner']: e['ts_ns'] / 1e9 for e in controller if e['kind'] == 'context_retire'}
    end = next(e['ts_ns'] / 1e9 for e in controller if e['kind'] == 'observation_end' and e['data']['complete'])
    by_owner = defaultdict(list)
    for row in requests:
        by_owner[row['owner']].append(row)
    for values in by_owner.values():
        values.sort(key=lambda r: r['lookup_ns'])
    snapshots, exclusions = [], []
    for request in requests:
        audit = request['policy_decision']['distribution_audit']
        at = audit['t0_s']
        for owner, features in audit['observed_idle_features'].items():
            previous = [r for r in by_owner[owner] if r['release_ns'] / 1e9 <= at]
            if not previous:
                exclusions.append({'owner': owner, 'at_s': at, 'reason': 'missing_previous_release'})
                continue
            origin = previous[-1]
            count = features['prefix_tokens']
            if count != (len(origin['tokens']) - 1) // 128 * 128:
                raise ValueError('observed prefix geometry differs')
            prefix = origin['tokens'][:count]
            death = retire.get(owner, end)
            future = [r for r in by_owner[owner] if at <= r['lookup_ns'] / 1e9 <= min(death, end)]
            if future and future[0]['tokens'][:count] != prefix:
                exclusions.append({'owner': owner, 'at_s': at, 'reason': 'next_lookup_changes_observed_prefix'})
                continue
            if future:
                event, terminal = 'U', future[0]['lookup_ns'] / 1e9
            elif owner in retire and death <= end:
                event, terminal = 'D', death
            else:
                event, terminal = 'C', end
            if terminal < at:
                raise ValueError('negative residual target')
            snapshots.append({'snapshot_id': request['request_id'] + ':' + owner,
                'episode_id': str(batch) + ':' + owner + ':' + origin['request_id'],
                'owner': owner, 'source_family': tasks[owner]['source_family'],
                'features': features, 'snapshot_s': at, 'terminal_s': terminal,
                'event': event, 'remaining_s': terminal - at})
    counts = Counter(s['episode_id'] for s in snapshots)
    for row in snapshots:
        row['weight'] = 1 / counts[row['episode_id']]
    return snapshots, exclusions


def summarize(rows):
    result = {}
    for model in ('conditional_empirical', 'frozen_gbdt'):
        selected = [r for r in rows if r['model'] == model]
        scores = {}
        for horizon in (1, 3, 10, 30):
            pairs = []
            for row in selected:
                label = horizon_label(row['event'], row['remaining_s'], horizon)
                pred = next((p for p in row['distribution'] if p['right_s'] == horizon and p['supported']), None)
                if label is not None and pred is not None:
                    pairs.append((row['weight'], label, pred['F_U']))
            weight = sum(w for w, y, p in pairs)
            scores[str(horizon)] = {'snapshots': len(pairs), 'episode_weight_sum': weight,
                'Brier_U': sum(w * (p - y)**2 for w, y, p in pairs) / weight if weight else None,
                'mean_predicted_U': sum(w * p for w, y, p in pairs) / weight if weight else None,
                'observed_U_fraction': sum(w * y for w, y, p in pairs) / weight if weight else None}
        losses = [(r['weight'], event_probability(r['distribution'], r['event'], r['remaining_s'])) for r in selected]
        losses = [(w, p) for w, p in losses if p is not None]
        denom = sum(w for w, p in losses)
        result[model] = {'snapshots': len(selected), 'horizons_s': scores,
            'finite_window_event_NLL': sum(w * -math.log(max(p, 1e-12)) for w, p in losses) / denom if denom else None,
            'NLL_probability_floor': 1e-12,
            'unseen_state_fallback_snapshots': sum(any(p.get('estimate_source') == 'global_fallback_unseen_state' for p in r['distribution']) for r in selected),
            'estimate_sources': dict(Counter(p.get('estimate_source', 'unspecified') for r in selected for p in r['distribution']))}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('analysis output already exists')
    from leasepath.frozen_distribution_baseline import FrozenEqualCostBaseline, file_digest
    from leasepath.reuse_predictor import EmpiricalRiskPredictor
    from leasepath.cache_policy_models import TransferredPredictor
    plan = read(args.plan)
    state = read(args.run / 'run-manifest.json')
    tasks = {t['id']: t for t in read(Path(plan['suite']) / 'tasks.json')}
    baseline = FrozenEqualCostBaseline(ROOT, ROOT / plan['baseline_manifest'])
    if baseline.evidence() != plan['baseline_evidence']:
        raise ValueError('prediction baseline identity changed')
    compatibility = state['runtime_compatibility']
    conditional_path = ROOT / 'results/prediction/qwen50_holdout_2026-11-08/conditional-model.json'
    conditional = EmpiricalRiskPredictor.load(conditional_path)
    models = {'conditional_empirical': TransferredPredictor(conditional, runtime_compatibility=compatibility,
                training_compatibility=baseline.document['predictor']['training_compatibility']),
              'frozen_gbdt': baseline.predictor(compatibility)}
    snapshots, exclusions, included, excluded = [], [], [], []
    for directory in sorted(args.run.glob('batch-*')):
        if not (directory / 'loop-audit.json').exists() or not (directory / 'decision-audit.json').exists():
            excluded.append({'batch': str(directory), 'reason': 'missing protocol/action audit'})
            continue
        if read(directory / 'loop-audit.json')['status'] != 'passed' or read(directory / 'decision-audit.json')['status'] != 'passed':
            raise ValueError('batch audit failed')
        part, skipped = extract_snapshots(directory / 'batch', tasks)
        snapshots.extend(part); exclusions.extend(skipped); included.append(str(directory))
    if any(tasks[s['owner']]['split'] != 'development' for s in snapshots):
        raise ValueError('holdout observations not permitted')
    rows = [{**s, 'model': name, 'distribution': model.predict(s['features'])} for s in snapshots for name, model in models.items()]
    result = {'status': 'completed_descriptive_transfer_audit', 'new_model_generations': 0,
        'source_plan_sha256': file_digest(args.plan), 'source_run_manifest_sha256': file_digest(args.run / 'run-manifest.json'),
        'conditional_model_sha256': file_digest(conditional_path), 'baseline': baseline.evidence(),
        'included_batches': included, 'excluded_batches': excluded, 'excluded_snapshots': exclusions,
        'episodes': len({s['episode_id'] for s in snapshots}), 'snapshots': len(snapshots),
        'families': sorted({s['source_family'] for s in snapshots}), 'event_counts': dict(Counter(s['event'] for s in snapshots)),
        'metrics': summarize(rows), 'prediction_rows': rows,
        'scope': 'Correlated decision-time snapshots of development toy fixtures under one policy. Equal total weight per observed idle episode; no holdout or causal-JCT inference.',
        'retirement_rule': 'Only explicit context_retire; eviction is never D. Nonmatching next lookup is excluded with reason.',
        'natural_JCT_acceptance': False}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: result[k] for k in ('episodes', 'snapshots', 'families', 'event_counts', 'metrics')}))


if __name__ == '__main__':
    main()
