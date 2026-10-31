"""Replay diversity snapshots, source provenance and distinct parent membership."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.d2_expansion import EXTRA_FEATURES, project_extra
from leasepath.prediction_state import FEATURES


def read(p):
    return json.loads(p.read_text())


def lines(p):
    return [json.loads(line) for line in p.read_text().splitlines()]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, default=ROOT / '.artifacts/d2-diversity-v3')
    p.add_argument('--results', type=Path, default=ROOT / 'results/prediction/d2_diversity_v3')
    args = p.parse_args()
    home, out = args.root.resolve(), args.results.resolve()
    for name, expected in read(out / 'SHA256SUMS.json').items():
        require(sha(out / name) == expected, 'result hash changed: ' + name)
    for name, expected in read(out / 'source-hashes.json').items():
        require(sha(home / 'frozen-round/source' / name) == expected and sha(ROOT / name) == expected,
                'source hash changed: ' + name)
    tasks = read(home / 'tasks.json')['tasks']
    runs = read(home / 'runs/greedy-guided/results.json')
    manifest = read(out / 'run-manifest.json')
    require(sha(home / 'tasks.json') == manifest['tasks_sha256']
            == sha(home / 'frozen-round/tasks.json'), 'task manifest changed')
    require(sha(home / 'frozen-round/raw.jsonl') == read(out / 'summary.json')['raw_sha256'],
            'frozen raw hash changed')
    for task in tasks:
        evaluation = home / 'evaluation' / task['instance_id']
        require(sha(evaluation / 'reference.py') == task['reference_sha256'], 'reference changed')
        require(sha(evaluation / 'test_independent.py') == task['independent_test_sha256'],
                'independent oracle changed')
        work = Path(task['workspaces']['greedy-guided'])
        require(all(sha(work / f) == expected for f, expected in task['protected_sha256'].items()),
                'protected task file changed')
    by_workflow = {r['workflow_id']: r for r in runs}
    require(len(tasks) == len(runs) == 34, 'expected 34 distinct executions')
    old = {t['instance_id'] for t in read(ROOT / '.artifacts/d2-next/tasks.json')['tasks']}
    require(not old & {t['instance_id'] for t in tasks}, 'duplicate old MBPP parents')
    require(len({t['group_id'] for t in tasks}) == 34, 'new variants counted as distinct parents')
    references = read(home / 'reference-validation.json')
    require(references == read(out / 'reference-validation.json'), 'reference validation copy changed')
    require(len(references) == 34 and all(r['reference_returncode'] == 0 and r['stub_rejected'] for r in references),
            'reference pytest or stub negative controls failed')
    for record in read(out / 'evaluation-evidence.json'):
        for name, expected in record['files'].items():
            require(sha(home / name) == expected, 'evaluation log changed: ' + name)
    raw = sorted(lines(home / 'frozen-round/raw.jsonl'), key=lambda r: (r['ts_ns'], r['pid'], r['seq']))
    starts = {r['tool_id']: r for r in raw if r['kind'] == 'tool_start' and r.get('workflow_id') in by_workflow}
    ends = {r['tool_id']: r for r in raw if r['kind'] == 'tool_end' and r.get('workflow_id') in by_workflow}
    require(starts.keys() == ends.keys(), 'unpaired tool events')
    for tid, start in starts.items():
        end = ends[tid]
        require(start['ts_ns'] <= end['ts_ns'] and start['request_id'] == end['request_id']
                and start['workflow_id'] == end['workflow_id'], 'tool association or time changed')
    replayed = 0
    for scope in ['all-observed', 'qualified']:
        data = home / 'datasets' / scope
        if not data.exists():
            continue
        samples, targets, extras = [lines(data / f) for f in
                                    ['snapshots.jsonl', 'targets.jsonl', 'features-expansion-v1.jsonl']]
        require([s['sample_id'] for s in samples] == [t['sample_id'] for t in targets]
                == [e['sample_id'] for e in extras], 'snapshot/target/extra order mismatch')
        for name, expected in read(data / 'summary.json')['output_sha256'].items():
            require(sha(data / name) == expected, 'dataset output hash changed')
        weights = Counter()
        for sample, extra in zip(samples, extras):
            observed = [r for r in raw if r['ts_ns'] <= sample['snapshot_ns']]
            projected = project_extra(observed, sample['run_id'], sample['idle_episode_id'], sample['snapshot_ns'])
            require(set(sample['features']) == FEATURES and set(projected) == EXTRA_FEATURES, 'feature allowlist mismatch')
            require(extra['features'] == {**sample['features'], **projected}, 'causal replay mismatch')
            weights[sample['run_id'], sample['idle_episode_id']] += sample['sample_weight']
            replayed += 1
        require(all(abs(w-1) < 1e-9 for w in weights.values()), 'episode weight changed')
    previous = {}
    for name in ['d2_mbpp_round', 'd2_expansion_v1', 'd2_repair_v2', 'gbdt_review_2']:
        directory = ROOT / 'results/prediction' / name
        for f, expected in read(directory / 'SHA256SUMS.json').items():
            require(sha(directory / f) == expected, 'previous result changed: ' + name + '/' + f)
        previous[name] = True
    result = {'status': 'passed', 'new_distinct_parents': 34, 'overlap_with_old_mbpp': 0,
              'reference_pytest_passed': 34, 'stub_negative_controls_rejected': 34,
              'causal_prefix_replays': replayed, 'tool_start_end_pairs': len(starts),
              'source_input_and_output_hashes_verified': True, 'previous_results_unchanged': previous,
              'no_new_predictor_training': True}
    (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'SHA256SUMS.json').write_text(json.dumps({p.name: sha(p) for p in sorted(out.iterdir())
                                                    if p.is_file() and p.name != 'SHA256SUMS.json'}, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
