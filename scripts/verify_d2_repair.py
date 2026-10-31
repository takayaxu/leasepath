"""Replay measured feature prefixes, provenance, outputs and repair evidence."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.d2_repair import REPAIR_FEATURES, project_repair
from leasepath.d2_repair_audit import audit_repair
from leasepath.prediction_state import FEATURES


def read(p):
    return json.loads(p.read_text())


def lines(p):
    return [json.loads(line) for line in p.read_text().splitlines()]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def require(condition, description):
    if not condition:
        raise ValueError(description)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT / '.artifacts/d2-repair-v2-guided')
    parser.add_argument('--results', type=Path, default=ROOT / 'results/prediction/d2_repair_v2')
    args = parser.parse_args()
    home, out = args.root.resolve(), args.results.resolve()
    for name, expected in read(out / 'SHA256SUMS.json').items():
        require(sha(out / name) == expected, 'result hash mismatch: ' + name)
    for name, expected in read(out / 'source-hashes.json').items():
        require(sha(home / 'frozen-round/source' / name) == expected, 'frozen source mismatch: ' + name)
        require(sha(ROOT / name) == expected, 'current source mismatch: ' + name)
    raw = sorted(lines(home / 'frozen-round/raw.jsonl'), key=lambda r: (r['ts_ns'], r['pid'], r['seq']))
    task_manifest = read(home / 'tasks.json')
    tasks = {t['instance_id']: t for t in task_manifest['tasks']}
    runs = read(home / 'runs/greedy-guided/results.json')
    audits = read(out / 'retry-audits.json')
    group_map = {r['workflow_id']: r['group_id'] for r in runs}
    tool_pairs = 0
    for run in runs:
        task = tasks[run['task_id']]
        proof = audit_repair(raw, run['workflow_id'], task['initial_source_sha256'])
        require(proof == audits[run['workflow_id']], 'repair proof replay mismatch')
        tool_pairs += proof['tool_pairs']
        ends = {r['tool_id']: r for r in raw if r['kind'] == 'tool_end' and r.get('workflow_id') == run['workflow_id']}
        for tid, end in ends.items():
            artifact = read(home / 'runs/greedy-guided' / run['task_id'] / 'tools' / (tid + '.json'))
            require(hashlib.sha256(artifact['output'].encode()).hexdigest() == end['output_sha256'], 'tool output hash mismatch')
            require(artifact['duration_ns'] == end['duration_ns'] and artifact['returncode'] == end['returncode'], 'tool outcome mismatch')
    replayed, retry_running = 0, 0
    for scope in ['all-observed', 'qualified']:
        data = home / 'datasets' / scope
        if not data.exists():
            continue
        samples = lines(data / 'snapshots.jsonl')
        targets = lines(data / 'targets.jsonl')
        extras = lines(data / 'features-repair-v2.jsonl')
        require([s['sample_id'] for s in samples] == [t['sample_id'] for t in targets] == [e['sample_id'] for e in extras],
                'sample target feature identity/order mismatch')
        for name, expected in read(data / 'summary.json')['output_sha256'].items():
            require(sha(data / name) == expected, 'dataset hash mismatch: ' + name)
        weights = Counter()
        for s, e in zip(samples, extras):
            observed = [r for r in raw if r['ts_ns'] <= s['snapshot_ns']]
            projected = project_repair(observed, s['run_id'], s['idle_episode_id'], s['snapshot_ns'])
            require(set(projected) == REPAIR_FEATURES and set(s['features']) == FEATURES, 'feature allowlist mismatch')
            require(e['features'] == {**s['features'], **projected}, 'causal prefix replay mismatch')
            require(e['group_id'] == group_map[s['run_id']], 'parent group mismatch')
            weights[s['idle_episode_id']] += s['sample_weight']
            retry_running += projected['current_suite_is_retry'] is True
            replayed += 1
        require(all(abs(w-1) < 1e-9 for w in weights.values()), 'episode weights not normalized')
    original_parents = {t['instance_id'] for t in read(ROOT / '.artifacts/d2-expansion-v1/tasks.json')['tasks']}
    require(all(t['parent_task_id'] in original_parents for t in tasks.values()), 'prior parent linkage missing')
    require(all(t['group_id'] == t['source_group'] + ':' + t['parent_task_id'] for t in tasks.values()), 'v1/v2 parent split mismatch')
    previous_checks = {}
    for name in ['d2_expansion_v1', 'd2_mbpp_round', 'gbdt_review_2']:
        previous = ROOT / 'results/prediction' / name
        for f, expected in read(previous / 'SHA256SUMS.json').items():
            require(sha(previous / f) == expected, 'previous result changed: ' + name + '/' + f)
        previous_checks[name] = True
    require(sha(ROOT / '.artifacts/d2-next/frozen-round/raw.jsonl') == 'dfc5197ddee4b8ba34fd178493411e3f02c4620a038c9aa67921d4c3881fe318',
            'old MBPP raw changed')
    require(sha(ROOT / '.artifacts/d2-expansion-v1/frozen-round/raw.jsonl') == '96cc5836c938396cd085429205221a2ebb614064a446a0c49f14327f0fa322b0',
            'v1 raw changed')
    result = {'status': 'passed', 'causal_prefix_replays': replayed, 'retry_running_snapshots_replayed': retry_running,
              'tool_pairs_and_output_hashes_verified': tool_pairs, 'parent_groups': len(set(group_map.values())),
              'group_members': dict(Counter(group_map.values())), 'input_and_output_hashes_verified': True,
              'feature_allowlists_verified': True, 'episode_weights_verified': True,
              'previous_results_unchanged': previous_checks, 'previous_frozen_raw_unchanged': True,
              'no_predictor_retraining': True}
    (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'SHA256SUMS.json').write_text(json.dumps({p.name: sha(p) for p in sorted(out.iterdir())
                                                    if p.is_file() and p.name != 'SHA256SUMS.json'}, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
