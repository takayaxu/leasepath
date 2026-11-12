"""Fixed six-fold development-family validation; independent holdout is sealed.

Repeated fixtures stay in the same fold. Each family has unit total weight,
each of its observed episodes has equal weight, then snapshots share that
episode's weight. Family IDs also conservatively bound support counts.
"""
from collections import Counter, defaultdict
import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.agent_risk_predictor_v2 import AgentGBDTV2, AgentEmpiricalV2, FEATURE_NAMES
from leasepath.cache_policy_models import TransferredPredictor
from leasepath.distribution_cache import distribution_scores
from leasepath.frozen_distribution_baseline import FrozenEqualCostBaseline, file_digest
from leasepath.gbdt_predictor import GBDTRiskPredictor
from leasepath.d2_hazards import RIGHT_EDGES
from leasepath.reuse_predictor import EmpiricalRiskPredictor
from evaluate_independent_predictions_v1 import extract_snapshots, horizon_label, event_probability

DOMAIN = 'independent-development-causal-state-v2'
MODELS = ('frozen_empirical', 'frozen_gbdt', 'refit_legacy_gbdt', 'adapted_empirical_v2', 'adapted_gbdt_v2')


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def training_rows(rows):
    episodes = defaultdict(set)
    counts = Counter(row['episode_id'] for row in rows)
    for row in rows:
        episodes[row['source_family']].add(row['episode_id'])
    samples, targets = [], []
    for row in rows:
        family = row['source_family']
        samples.append(dict(sample_id=row['sample_id'], run_id=family,
                            idle_episode_id=row['episode_id'], split='development',
                            sample_weight=1 / len(episodes[family]) / counts[row['episode_id']],
                            features={**row['features'], 'compatibility_id': DOMAIN}))
        targets.append(dict(sample_id=row['sample_id'], remaining_s=row['remaining_s'],
                            outcome={'U': 'legal_demand', 'D': 'retire', 'C': 'right_censored'}[row['event']]))
    return samples, targets


def metrics(predictions):
    result = {}
    # Compare Brier on the same supported/labeled rows, and expose any exclusions.
    by_sample = defaultdict(dict)
    for row in predictions:
        by_sample[row['sample_id']][row['model']] = row
    for model in MODELS:
        chosen = [row for row in predictions if row['model'] == model]
        horizons = {}
        for horizon in (1, 3, 10, 30):
            pairs = []
            for variants in by_sample.values():
                row = variants[model]
                label = horizon_label(row['event'], row['remaining_s'], horizon)
                common = all(any(p['right_s'] == horizon and p['supported'] for p in v['distribution'])
                             for v in variants.values())
                if label is None or not common:
                    continue
                estimate = next(p['F_U'] for p in row['distribution'] if p['right_s'] == horizon)
                pairs.append((row['weight'], label, estimate))
            weight = sum(w for w, y, p in pairs)
            horizons[str(horizon)] = dict(common_snapshots=len(pairs), evaluated_weight=weight,
                Brier_U=sum(w*(p-y)**2 for w, y, p in pairs)/weight if weight else None,
                predicted_U=sum(w*p for w, y, p in pairs)/weight if weight else None,
                observed_U=sum(w*y for w, y, p in pairs)/weight if weight else None)
        losses = [(row['weight'], event_probability(row['distribution'], row['event'], row['remaining_s']))
                  for row in chosen]
        losses = [(w, p) for w, p in losses if p is not None]
        denominator = sum(w for w, p in losses)
        result[model] = dict(horizons=horizons, snapshots=len(chosen),
            distribution_policy_eligible_snapshots=sum(row['policy_eligible'] for row in chosen),
            unseen_state_fallback_snapshots=sum(any(p['estimate_source'] == 'global_fallback_unseen_state'
                                                   for p in row['distribution']) for row in chosen),
            finite_window_NLL=sum(-w*math.log(max(p, 1e-12)) for w, p in losses)/denominator if denominator else None)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    plan = read(args.plan)
    output = Path(plan['output'])
    output.mkdir(parents=True, exist_ok=False)
    for source, digest in plan['source_sha256'].items():
        if file_digest(source) != digest:
            raise ValueError('training source changed since registration')
    suite = Path(plan['suite'])
    tasks = {t['id']: t for t in read(suite / 'tasks.json')}
    rows, exclusions, provenance = [], [], []
    for directory in map(Path, plan['batches']):
        for name in ('loop-audit.json', 'decision-audit.json'):
            if read(directory / name)['status'] != 'passed':
                raise ValueError('input batch lacks protocol/action acceptance')
        batch_rows, skipped = extract_snapshots(directory / 'batch', tasks)
        for row in batch_rows:
            if tasks[row['owner']]['split'] != 'development':
                raise ValueError('holdout features or targets forbidden')
            row['sample_id'] = str(directory) + ':' + row['snapshot_id']
            row['source_batch'] = str(directory)
        rows.extend(batch_rows); exclusions.extend(skipped)
        provenance.append(dict(batch=str(directory), files={str(path):file_digest(path) for path in [
            directory/'loop-audit.json', directory/'decision-audit.json',
            directory/'batch/raw/controller.jsonl', directory/'batch/raw/backend.jsonl']}))
    families = sorted({r['source_family'] for r in rows})
    if len(families) != 6:
        raise ValueError('all six development families must have observed episodes')
    samples, targets = training_rows(rows)
    by_sample = {sample['sample_id']: sample for sample in samples}
    baseline = FrozenEqualCostBaseline(ROOT, ROOT / 'configs/baselines/kv_equal_cost_distribution_v1.json')
    conditional = EmpiricalRiskPredictor.load(ROOT / 'results/prediction/qwen50_holdout_2026-11-08/conditional-model.json')
    frozen = dict(frozen_gbdt=baseline.predictor(DOMAIN), frozen_empirical=TransferredPredictor(
        conditional, runtime_compatibility=DOMAIN, training_compatibility=baseline.document['predictor']['training_compatibility']))
    predictions, folds = [], []
    for family in families:
        train_rows = [r for r in rows if r['source_family'] != family]
        test_rows = [r for r in rows if r['source_family'] == family]
        train, labels = training_rows(train_rows)
        models = {**frozen, 'refit_legacy_gbdt': GBDTRiskPredictor(right_edges=RIGHT_EDGES).fit(train, labels),
                  'adapted_empirical_v2': AgentEmpiricalV2().fit(train, labels),
                  'adapted_gbdt_v2': AgentGBDTV2().fit(train, labels)}
        train_families = sorted({r['source_family'] for r in train_rows})
        assert family not in train_families
        folds.append(dict(validation_development_family=family, training_families=train_families,
                          train_snapshots=len(train), validation_snapshots=len(test_rows),
                          note='development cross-validation, not the untouched holdout'))
        for row in test_rows:
            sample = by_sample[row['sample_id']]
            for name, model in models.items():
                dist = model.predict(sample['features'])
                predictions.append(dict(sample_id=row['sample_id'], family=family, model=name,
                    weight=sample['sample_weight'], event=row['event'], remaining_s=row['remaining_s'],
                    distribution=dist, policy_eligible=distribution_scores(dist)['W'] is not None))
    candidate = AgentGBDTV2().fit(samples, targets)
    empirical = AgentEmpiricalV2().fit(samples, targets)
    candidate.save(output/'gbdt-candidate.json'); empirical.save(output/'empirical-candidate.json')
    restored = AgentGBDTV2.load(output/'gbdt-candidate.json')
    assert all(restored.predict(s['features']) == candidate.predict(s['features']) for s in samples)
    write(output/'training-samples.json', samples)
    write(output/'training-targets.json', targets)
    write(output/'cross-validation-predictions.json', predictions)
    full_scores = [distribution_scores(candidate.predict(s['features'])) for s in samples]
    summary = dict(status='completed_development_candidate', holdout_model_tasks=0, holdout_training_rows=0,
        source_plan_sha256=file_digest(args.plan), source_provenance=provenance,
        families=families, snapshots=len(rows), observed_episodes=len({r['episode_id'] for r in rows}),
        events=dict(Counter(r['event'] for r in rows)), excluded_snapshots=exclusions,
        original_queue_missing_snapshots=sum(r['features']['queue_length'] is None for r in rows),
        folds=folds, cross_validation=metrics(predictions),
        feature_names=FEATURE_NAMES, feature_gain=dict(zip(FEATURE_NAMES, candidate.booster.feature_importance('gain').tolist())),
        support_counts=[dict(right_s=t, conservative_source_families=r['workflows'])
                        for t,r in zip(candidate.support.right_edges, candidate.support.global_counts)],
        full_candidate_policy_eligible_snapshots=sum(s['W'] is not None for s in full_scores),
        candidate_sha256=file_digest(output/'gbdt-candidate.json'), baseline=baseline.evidence(),
        online_deployment=False, natural_JCT_acceptance=False,
        limitations=['six locally authored toy fixtures; repeated sessions are not new parents',
                    'family-grouped development CV is not independent holdout generalization',
                    'trace and shared-host conditions affect label timing',
                    'support counts use distinct families, conservatively no larger than workflow counts',
                    'no hyperparameter selection or calibration on validation folds',
                    'frozen-vs-adapted changes both data and features; refit_legacy_gbdt controls training data'])
    write(output/'summary.json', summary)
    print(json.dumps({k:summary[k] for k in ('status','snapshots','observed_episodes','cross_validation','feature_gain','support_counts')}))


if __name__ == '__main__':
    main()
