"""Audit existing frozen dataset views without merging or changing their rows."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUNDS = [
    ('mbpp', 'd2_mbpp_round', '.artifacts/d2-next', 'all-main', 20),
    ('file-v1', 'd2_expansion_v1', '.artifacts/d2-expansion-v1', 'all-observed', 6),
    ('repair-v2', 'd2_repair_v2', '.artifacts/d2-repair-v2-guided', 'all-observed', 6),
    ('diversity-v3', 'd2_diversity_v3', '.artifacts/d2-diversity-v3', 'all-observed', 34),
]


def read(p):
    return json.loads(p.read_text())


def lines(p):
    return [json.loads(line) for line in p.read_text().splitlines()]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def require(ok, why):
    if not ok:
        raise ValueError(why)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'results/prediction/dataset_inventory_50parents_2026-10-31.json')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('inventory output exists; use a new version path')
    combined = {scope: {'workflows': set(), 'episodes': set(), 'samples': set(),
                        'parents': set(), 'sources': set(), 'outcomes': Counter(), 'risk_rows': 0}
                for scope in ['all-observed', 'qualified']}
    records = []
    for name, result_name, artifact_dir, main_scope, declared in ROUNDS:
        result_dir, home = ROOT / 'results/prediction' / result_name, ROOT / artifact_dir
        for f, expected in read(result_dir / 'SHA256SUMS.json').items():
            require(sha(result_dir / f) == expected, 'result hash mismatch: ' + str(result_dir / f))
        quality = read(result_dir / 'execution-quality.json')
        quality = quality['executions'] if isinstance(quality, dict) else quality
        by_wf = {r['workflow_id']: r for r in quality}
        item = {'round': name, 'declared_main_executions': declared, 'artifact_root': artifact_dir,
                'result_root': str(result_dir.relative_to(ROOT)), 'views': {}}
        for logical_scope, directory_scope in [('all-observed', main_scope), ('qualified', 'qualified')]:
            data = home / 'datasets' / directory_scope
            if not data.exists():
                item['views'][logical_scope] = {'workflows': 0, 'status': 'empty'}
                continue
            summary = read(data / 'summary.json')
            for f, expected in summary['output_sha256'].items():
                require(sha(data / f) == expected, 'dataset hash mismatch: ' + str(data / f))
            samples, targets, labels = [lines(data / f) for f in
                                        ['snapshots.jsonl', 'targets.jsonl', 'episode_labels.jsonl']]
            ids = {s['sample_id'] for s in samples}
            workflows = {s['run_id'] for s in samples}
            episodes = {(r['run_id'], r['idle_episode_id']) for r in labels}
            require(len(ids) == len(samples) == len(targets), 'duplicate sample/target identity')
            require(ids == {t['sample_id'] for t in targets}, 'snapshot/target mismatch')
            require(len(labels) == len(episodes) == summary['idle_episodes'], 'episode count mismatch')
            require(len(samples) == summary['snapshots'] and len(workflows) == summary['runs'], 'summary count mismatch')
            parents = {by_wf[w]['group_id'] for w in workflows}
            sources = {s['source_group'] for s in samples}
            outcomes = Counter(l['outcome'] for l in labels)
            weights = Counter()
            for sample in samples:
                weights[sample['run_id'], sample['idle_episode_id']] += sample['sample_weight']
            require(all(abs(w - 1) < 1e-9 for w in weights.values()), 'episode weights invalid')
            hazards = home / 'hazards' / directory_scope
            hazard_summary = read(hazards / 'summary.json')
            for f, expected in hazard_summary['output_sha256'].items():
                require(sha(hazards / f) == expected, 'hazard hash mismatch')
            risk_rows = len(lines(hazards / 'risk_intervals.jsonl'))
            require(risk_rows == hazard_summary['risk_interval_rows'], 'risk count mismatch')
            total = combined[logical_scope]
            require(not (total['workflows'] & workflows or total['samples'] & ids or total['episodes'] & episodes),
                    'duplicate rows across rounds')
            for key, value in [('workflows', workflows), ('samples', ids), ('episodes', episodes),
                               ('parents', parents), ('sources', sources)]:
                total[key].update(value)
            total['outcomes'].update(outcomes)
            total['risk_rows'] += risk_rows
            durations = [l['duration_ns'] / 1e9 for l in labels]
            item['views'][logical_scope] = {
                'dataset_path': str(data.relative_to(ROOT)), 'hazards_path': str(hazards.relative_to(ROOT)),
                'workflows': len(workflows), 'parent_tasks': len(parents), 'episodes': len(episodes),
                'snapshots': len(samples), 'risk_rows': risk_rows, 'outcomes': dict(outcomes),
                'parent_group_ids': sorted(parents), 'workflow_ids': sorted(workflows),
                'idle_duration_s': {'max': max(durations), 'ge_1s': sum(t >= 1 for t in durations),
                                    'ge_3s': sum(t >= 3 for t in durations), 'ge_5s': sum(t >= 5 for t in durations)},
                'summary_sha256': sha(data / 'summary.json'),
                'file_sha256': {p.name: sha(p) for p in sorted(data.glob('*.jsonl'))},
                'hazard_summary_sha256': sha(hazards / 'summary.json')}
        records.append(item)
    require(combined['qualified']['workflows'] <= combined['all-observed']['workflows'], 'qualified not workflow subset')
    require(combined['qualified']['samples'] <= combined['all-observed']['samples'], 'qualified not sample subset')
    require(len(combined['all-observed']['parents']) == 50, 'target 50 audited parents not achieved')
    result = {'as_of': '2026-10-31', 'timezone': 'Asia/Shanghai',
              'scope': 'logical inventory of existing development views; no merged training set',
              'rounds': records, 'combined_views': {},
              'validation': {'input_content_and_metadata_hashes_matched': True,
                             'cross_round_workflow_episode_sample_duplicates': 0,
                             'qualified_is_subset_of_all_observed': True,
                             'snapshot_target_ids_aligned': True,
                             'declared_main_executions': sum(r[-1] for r in ROUNDS),
                             'backend_quarantined_executions': sum(r[-1] for r in ROUNDS) - len(combined['all-observed']['workflows'])},
              'inventory_script_sha256': sha(Path(__file__))}
    for scope, c in combined.items():
        result['combined_views'][scope] = {
            'workflows': len(c['workflows']), 'parent_tasks': len(c['parents']), 'source_families': sorted(c['sources']),
            'episodes': len(c['episodes']), 'snapshots': len(c['samples']), 'risk_rows': c['risk_rows'],
            'outcomes': dict(c['outcomes']), 'parent_group_ids': sorted(c['parents'])}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result['combined_views'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
