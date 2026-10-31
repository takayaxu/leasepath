"""Replay saved fold predictions and scores against immutable merged inputs."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.gbdt_predictor import GBDTRiskPredictor
from leasepath.prediction_merged import load_inventory, require
from leasepath.prediction_training import (
    assign_group_folds, check_distribution, read_json, read_jsonl,
    score_predictions, sha256, split_fold,
)
from leasepath.reuse_predictor import EmpiricalRiskPredictor
from train_prediction_v2 import check_history
from train_reuse_predictor import build_model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--results', type=Path, default=ROOT / 'results/prediction/prediction_v2_50parents')
    args = parser.parse_args()
    out = args.results.resolve()
    manifest, summary = read_json(out / 'run-manifest.json'), read_json(out / 'summary.json')
    config, baseline = manifest['configuration'], manifest['baseline_configuration']
    artifact = Path(summary['artifacts'])
    require(summary['status'] == manifest['status'] == 'passed', 'incomplete training run')
    for name, digest in read_json(out / 'SHA256SUMS.json').items():
        require(sha256(out / name) == digest, 'result hash mismatch: ' + name)
    for name, digest in manifest['source_sha256'].items():
        require(sha256(ROOT / name) == digest == sha256(artifact / 'source' / name), 'frozen source mismatch')
    for name, digest in manifest['input_sha256'].items():
        require(sha256(ROOT / name) == digest, 'training input changed')
    require(sha256(out / 'input-inventory.json') == config['inventory_sha256'], 'inventory copy mismatch')
    require(read_json(artifact / 'training-config.json') == config, 'frozen training configuration mismatch')
    require(read_json(artifact / 'configuration.json') == {'training': config, 'baseline': baseline},
            'frozen baseline configuration mismatch')
    check_history(config['historical_result_manifest_sha256'])
    data, _ = load_inventory(ROOT, ROOT / config['inventory'],
                             expected_inventory_sha256=config['inventory_sha256'],
                             expected_result_manifests=config['round_result_manifest_sha256'])
    splits = read_json(out / 'split-manifest.json')
    assignments = assign_group_folds(data['all-observed']['run_groups'].values(), config['folds'], config['seed'])
    require(assignments == splits['assignments'], 'parent fold assignment changed')
    fold_records, metrics = read_json(out / 'cv-models.json'), read_json(out / 'metrics.json')
    replayed, models_checked, views = 0, 0, {}
    for scope, records in data.items():
        samples, targets, groups = records['samples'], records['targets'], records['run_groups']
        selected_summary = summary['datasets'][scope]
        merge_dir = artifact / 'merged' / scope
        for name, digest in selected_summary['merged_files_sha256'].items():
            require(sha256(merge_dir / name) == digest, 'merged hash mismatch')
        require(read_jsonl(merge_dir / 'snapshots-workflow-weighted.jsonl') == samples,
                'workflow-balanced merged samples changed')
        require(read_jsonl(merge_dir / 'targets.jsonl') == targets, 'merged target changed')
        predictions = {m: {} for m in config['models']}
        oof_path = artifact / f'{scope}-oof-predictions.jsonl'
        require(sha256(oof_path) == selected_summary['oof_sha256'], 'OOF hash mismatch')
        oof = read_jsonl(oof_path)
        by_id = {r['sample_id']: r for r in oof}
        require(len(by_id) == len(oof) == len(samples), 'duplicate/missing OOF identity')
        require(set(by_id) == {s['sample_id'] for s in samples}, 'OOF identity mismatch')
        for fold in range(config['folds']):
            train, train_targets, validation, _ = split_fold(samples, targets, groups, assignments, fold)
            entry = fold_records[scope][fold]
            train_groups, validation_groups = {groups[s['run_id']] for s in train}, {groups[s['run_id']] for s in validation}
            require(train_groups == set(entry['training_groups']) and
                    validation_groups == set(entry['validation_groups']) and not train_groups & validation_groups,
                    'fold parent membership differs')
            expected_global = build_model('global', baseline, config).fit(train, train_targets)
            expected_conditional = build_model('conditional', baseline, config).fit(train, train_targets)
            for name in config['models']:
                path = artifact / f'{scope}-fold-{fold}-{name}.json'
                require(sha256(path) == entry['models'][name]['sha256'], 'fold model hash changed')
                model = (GBDTRiskPredictor if name == 'gbdt' else EmpiricalRiskPredictor).load(path)
                if name == 'gbdt':
                    require(model.support.to_dict() == expected_global.to_dict(), 'GBDT support includes different fold labels')
                    require(model.parameters == config['gbdt'], 'GBDT parameters changed')
                    require(max(t['num_leaves'] for t in model.booster.dump_model()['tree_info']) <= config['gbdt']['num_leaves'],
                            'tree regularization differs')
                    for key in ['tool_name', 'phase']:
                        require(set(model.vocabularies[key]) == {s['features'][key] for s in train},
                                'vocabulary includes held-out state')
                else:
                    expected = expected_global if name == 'global' else expected_conditional
                    require(model.to_dict() == expected.to_dict(), 'empirical counts differ from training-only risk set')
                for sample in validation:
                    sid, workflow = sample['sample_id'], sample['run_id']
                    record = by_id[sid]
                    require(record['run_id'] == workflow and record['group_id'] == groups[workflow]
                            and record['fold'] == assignments[groups[workflow]] == fold, 'OOF fold association changed')
                    probability = model.predict(sample['features'])
                    require(probability == record['models'][name], 'saved OOF probability differs from fold model')
                    check_distribution(probability)
                    predictions[name][sid] = probability
                    replayed += 1
                models_checked += 1
        recomputed = score_predictions(samples, targets, predictions, groups,
                                       baseline['right_edges'], config['calibration_edges'])
        require(recomputed == metrics[scope], 'OOF metrics do not reproduce')
        views[scope] = {'snapshots': len(samples), 'parents': len(set(groups.values())),
                        'fold_models': config['folds'] * len(config['models']), 'OOF_scores_reproduced': True}
    result = {'status': 'passed', 'views': views, 'fold_models_checked': models_checked,
              'OOF_model_distributions_replayed': replayed, 'input_source_and_result_hashes_verified': True,
              'training_only_empirical_counts_and_gbdt_support_verified': True,
              'shared_parent_folds_across_rounds_and_views_verified': True,
              'workflow_balanced_merged_inputs_reproduced': True, 'old_results_unchanged': True,
              'verifier_sha256': sha256(Path(__file__))}
    (out / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    (out / 'SHA256SUMS.json').write_text(json.dumps({p.name: sha256(p) for p in sorted(out.iterdir())
                                                  if p.is_file() and p.name != 'SHA256SUMS.json'}, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
