"""Read-only finalization after a registered v2 run exits; never retries it."""
from collections import Counter
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.frozen_distribution_baseline import file_digest
from leasepath.prediction_state import validate_features
from run_independent_collection_v2 import write
from evaluate_independent_predictions_v1 import extract_snapshots


def read(path):
    return json.loads(Path(path).read_text())


def lines(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def audit_queue(batch):
    ready, running, known = set(), {}, {}
    latest = None
    snapshots = {}
    for event in lines(batch / 'raw/controller.jsonl'):
        data, kind = event['data'], event['kind']
        owner = data.get('owner')
        if kind == 'workflow_submit':
            ready.add(owner)
        elif kind == 'tool_start':
            assert owner not in ready
            running[owner] = data['actual_observed_monotonic_s']
            known[owner] = data['tool_name']
        elif kind == 'tool_end':
            running.pop(owner)
            ready.add(owner)
        elif kind in ('context_retire', 'tool_failed'):
            running.pop(owner, None)
            ready.discard(owner)
        elif kind == 'scheduler_snapshot':
            assert set(data['ready_owners']) == ready
            assert len(data['ready_owners']) == len(ready)
            now = data['observed_monotonic_s']
            assert now <= event['ts_ns'] / 1e9
            for name, features in data['observed_features'].items():
                validate_features(features)
                assert features['queue_length'] == len(ready)
                assert features['is_queued'] == (name in ready)
                assert features['tool_name'] == known.get(name)
                assert features['retry_count'] == 0
                if name in running:
                    assert features['phase'] == 'waiting_tool'
                    assert abs(features['tool_elapsed_s'] - (now - running[name])) < 1e-9
                else:
                    assert features['tool_elapsed_s'] is None
                    assert features['phase'] == ('queued' if name in ready else None)
            latest = data
        elif kind == 'request_input':
            assert latest is not None and owner == latest['ready_owners'][0]
            snapshots[data['request_id']] = latest
            ready.remove(owner)
            latest = None
    checked = 0
    for event in lines(batch / 'raw/backend.jsonl'):
        if event['kind'] != 'engine_request':
            continue
        row = event['data']
        snapshot = snapshots[row['request_id']]
        audit = row['policy_decision']['distribution_audit']
        assert snapshot['observed_monotonic_s'] <= audit['t0_s']
        for owner, features in audit['observed_idle_features'].items():
            source = snapshot['observed_features'][owner]
            for key in features:
                if key in ('elapsed_idle_s', 'tool_elapsed_s') and source[key] is not None:
                    advance = features[key] - source[key]
                    assert -1e-9 <= advance <= audit['t0_s'] - snapshot['observed_monotonic_s'] + 1e-9
                else:
                    assert features[key] == source[key]
            checked += 1
    return dict(status='passed', requests=len(snapshots), idle_feature_vectors_checked=checked,
                scope='controller-visible state and decision-time linkage; no future information')


def finalize(plan_path):
    plan = read(plan_path)
    work, evidence = Path(plan['work']), Path(plan['evidence'])
    state = read(work / 'run-manifest.json')
    if state['status'] != 'completed_behavioral_pilot':
        raise ValueError('registered run stopped; do not replace its samples')
    tasks = {t['id']: t for t in read(Path(plan['suite']) / 'tasks.json')}
    result = dict(status='passed', holdout_model_tasks=0, natural_JCT_acceptance=False,
                  task_instance_scope=plan['task_instance_scope'], capacity=plan['capacity_blocks'],
                  concurrency=plan['concurrency'], new_generations=state['new_model_generations'], batches=[])
    workers = []
    for directory in sorted(work.glob('batch-*')):
        batch = directory / 'batch'
        assert read(directory / 'loop-audit.json')['status'] == 'passed'
        assert read(directory / 'decision-audit.json')['status'] == 'passed'
        write(directory / 'queue-audit.json', audit_queue(batch))
        snapshots, exclusions = extract_snapshots(batch, tasks)
        assert all(tasks[row['owner']]['split'] == 'development' for row in snapshots)
        write(directory / 'prediction-snapshots.json', dict(rows=snapshots, exclusions=exclusions))
        requests = [e['data'] for e in lines(batch / 'raw/backend.jsonl') if e['kind'] == 'engine_request']
        victims = [v for r in requests for v in r['policy_decision']['evicted']]
        competition = [s for r in requests for s in r['policy_decision']['distribution_audit']['sweeps']
                       if len({o['owner'] for c in s['candidates'] for o in c['owners']}) >= 2]
        scores = [v for r in requests for v in r['policy_decision']['distribution_audit']['owner_scores'].values()]
        pressure = dict(live_owner_evictions=sum(bool(v['live_owners']) for v in victims),
                        total_evictions=len(victims), competing_live_owner_sweeps=len(competition),
                        prediction_calls=sum(r['policy_decision']['distribution_audit']['prediction_calls'] for r in requests),
                        lru_victim_set_changes=sum(r['policy_decision']['same_state_diagnostic']['victim_set_differs'] for r in requests),
                        point_victim_set_changes=sum(r['policy_decision']['same_state_diagnostic']['point_victim_set_differs'] for r in requests),
                        estimate_sources=dict(Counter(p['estimate_source'] for v in scores for p in v.get('distribution', []))),
                        unsupported_score_calls=sum(v.get('W') is None for v in scores))
        write(directory / 'pressure-evidence.json', pressure)
        for path in sorted(batch.glob('*-quality.json')):
            quality = read(path)
            assert tasks[quality['task']]['split'] == 'development'
            for call in quality['tools']:
                if 'child_pid' not in call:
                    continue
                trace = Path(plan['trace_prefix'] + '.' + str(call['child_pid']))
                content = trace.read_text(errors='replace')
                assert 'leasepath.independent_agent_worker' in content and 'execve(' in content
                assert '+++ exited with 0 +++' in content
                assert 'references.private.json' not in content and 'independent_agent_reference' not in content
                workers.append(dict(task=quality['task'], pid=call['child_pid'], trace_sha256=file_digest(trace)))
        result['batches'].append(dict(summary=read(batch / 'summary.json'), pressure=pressure,
                                     loop_audit=read(directory / 'loop-audit.json'),
                                     snapshots=len(snapshots), episodes=len({s['episode_id'] for s in snapshots})))
    write(evidence / 'worker-trace-audit.json', dict(status='passed', workers=workers,
          scope='observed worker file access; not a filesystem security sandbox'))
    destination = evidence / 'raw'
    shutil.copytree(work, destination)
    files = {str(p.relative_to(work)): file_digest(p) for p in work.rglob('*') if p.is_file()}
    assert all(file_digest(destination / name) == digest for name, digest in files.items())
    write(evidence / 'raw-sha256.json', dict(files=files, source_retained=str(work), copy_verified=True))
    write(evidence / 'delivery.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(finalize(args.plan), ensure_ascii=False))
