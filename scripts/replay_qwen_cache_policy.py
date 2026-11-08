"""Test-only cache pressure diagnostic; fixed service times, no JCT claims."""
import argparse
from collections import defaultdict, deque
import heapq
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.cache import block_keys
from leasepath.gbdt_predictor import GBDTRiskPredictor
from leasepath.predictive_cache import PredictiveCache
from leasepath.prediction_training import read_json, read_jsonl, sha256
from leasepath.qwen_prediction_holdout import load_dataset, require
from leasepath.reuse_predictor import EmpiricalRiskPredictor


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def prepare(dataset, assignments):
    """Environment holds observed requests/timing; policy receives only live features."""
    events = read_jsonl(dataset / 'events.jsonl')
    selected = [e for e in events if assignments.get(e['workflow_id']) == 'test']
    starts, episodes, lookups, ends, readies = {}, {}, {}, {}, {}
    for e in selected:
        k = (e['run_id'], e.get('request_id'))
        if e['event_type'] == 'request_ready': readies[k] = e
        if e['event_type'] == 'cache_lookup': lookups[k] = e
        if e['event_type'] == 'request_end': ends[k] = e
        if e['event_type'] == 'idle_start':
            request = (e['run_id'], e['data']['source_request_id'])
            starts[request] = e
            episodes[(e['run_id'], e['idle_episode_id'])] = request
    features = defaultdict(list)
    for row in read_jsonl(dataset / 'features.jsonl'):
        ep = (row['run_id'], row['idle_episode_id'])
        if ep in episodes:
            features[episodes[ep]].append(row)
    tasks = defaultdict(list)
    for request, lookup in lookups.items():
        owner = lookup['workflow_id']
        idle = starts[request]
        f = sorted(features[request], key=lambda r: r['snapshot_ns'])
        require(f[0]['snapshot_ns'] == idle['ts_ns'], 'missing idle-start features')
        tokens = lookup['data']['backend_lookup']['prompt_token_ids']
        aligned = (len(tokens)-1)//128*128  # Always recompute >=1 token for logits.
        keys = block_keys(tokens[:aligned], f[0]['features']['compatibility_id'], 128)
        tasks[owner].append({'request_id': request[1], 'ready_ns': readies[request]['ts_ns'],
            'release_ns': ends[request]['ts_ns'],
            'service_s': (ends[request]['ts_ns'] - lookup['ts_ns']) / 1e9,
            'keys': keys, 'prompt_tokens': len(tokens), 'features': f,
            'idle_start_ns': idle['ts_ns']})
    retire = {e['workflow_id']: e['ts_ns'] for e in selected if e['event_type'] == 'context_retire'}
    for owner, stages in tasks.items():
        stages.sort(key=lambda r: r['ready_ns'])
        for i, stage in enumerate(stages):
            stage['gap_s'] = ((stages[i+1]['ready_ns'] if i+1 < len(stages) else retire[owner]) - stage['release_ns']) / 1e9
            require(stage['gap_s'] >= 0 and stage['service_s'] > 0, 'invalid measured timing')
    return dict(tasks)


def replay(tasks, capacity, name, model):
    cache = PredictiveCache(capacity, block_size=128, block_bytes=256*1024*128,
                            policy='predicted_reuse' if model else name, predictor=model, horizon_s=3.)
    events, queue, rows, decisions = [], deque(), [], []
    counter = 0
    def push(when, kind, owner, index, payload=None):
        nonlocal counter
        counter += 1
        heapq.heappush(events, (when, counter, kind, owner, index, payload))
    for owner in sorted(tasks): push(0., 'ready', owner, 0)
    active = False
    while events or queue:
        if not active and queue:
            owner, index, ready = queue.popleft()
            stage = tasks[owner][index]
            decision = cache.start(owner, stage['keys'], cache.now)
            decisions.append({'owner': owner, 'request_index': index, 'at_s': cache.now, **decision})
            rows.append({'owner': owner, 'request_index': index, 'ready_s': ready, 'dispatch_s': cache.now,
                         'prompt_tokens': stage['prompt_tokens'], 'cached_tokens': decision['cached_blocks']*128})
            active = True
            push(cache.now + stage['service_s'], 'finish', owner, index)
            continue
        when, _, kind, owner, index, payload = heapq.heappop(events)
        cache.advance(when)
        stage = tasks[owner][index]
        if kind == 'ready': queue.append((owner, index, when))
        elif kind == 'observe': cache.observe(owner, payload, when)
        elif kind == 'retire': cache.retire(owner, when)
        elif kind == 'finish':
            active = False
            cache.release(owner, stage['features'][0]['features'], when)
            for row in stage['features'][1:]:
                offset = (row['snapshot_ns'] - stage['idle_start_ns']) / 1e9
                if offset < stage['gap_s']:
                    push(when + offset, 'observe', owner, index, row['features'])
            if index + 1 < len(tasks[owner]): push(when + stage['gap_s'], 'ready', owner, index+1)
            else: push(when + stage['gap_s'], 'retire', owner, index)
    require(len(rows) == sum(map(len, tasks.values())), 'missing request')
    return {'policy': name, 'capacity_blocks': capacity, 'requests': rows, 'decisions': decisions,
            'cached_tokens': sum(r['cached_tokens'] for r in rows),
            'prompt_tokens': sum(r['prompt_tokens'] for r in rows),
            'missed_input_tokens': sum(r['prompt_tokens']-r['cached_tokens'] for r in rows),
            'evictions': cache.evictions, 'prediction_calls': cache.prediction_calls,
            'prediction_wall_ms': cache.prediction_ns / 1e6, 'unsupported_fallback_sweeps': cache.fallback_sweeps,
            'evidence_kind': 'counterfactual_cache_metadata_replay_fixed_measured_service',
            'JCT_benefit_measured': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, default=ROOT / 'configs/prediction/qwen_cache_stage1_2026-11-08.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'results/cache_policy/qwen_stage1_verified_2026-11-08')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    plan = read_json(args.plan)
    prediction = ROOT / plan['prediction_result']
    config = read_json(prediction / 'configuration.json')
    load_dataset(ROOT / config['dataset'], config['input_sha256'])
    frozen = read_json(prediction / 'SHA256SUMS.json')
    for filename, digest in frozen.items():
        require(sha256(prediction / filename) == digest, 'frozen prediction result changed')
    if args.verify_only:
        require(read_json(args.output / 'frozen-plan.json') == plan, 'plan differs from frozen run')
        for name, digest in read_json(args.output / 'SHA256SUMS.json').items():
            require(sha256(args.output / name) == digest, 'output hash mismatch: ' + name)
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        write(args.output / 'frozen-plan.json', plan)
        resource = subprocess.run(['nvidia-smi', '--query-gpu=index,name,memory.used,memory.total,utilization.gpu',
                                  '--format=csv'], capture_output=True, text=True, check=True).stdout
        (args.output / 'resource-preflight.txt').write_text(resource)
    assignments = read_json(prediction / 'split-manifest.json')['assignments']
    tasks = prepare(ROOT / config['dataset'], assignments)
    models = {'lru': None, 'retired_first_lru': None,
              'conditional': EmpiricalRiskPredictor.load(prediction / 'conditional-model.json'),
              'gbdt': GBDTRiskPredictor.load(prediction / 'gbdt-model.json')}
    require(list(models) == plan['policies'] and plan['horizon_s'] == 3., 'unsupported policy plan')
    results = []
    for capacity in plan['capacity_blocks']:
        for name, model in models.items():
            result = replay(tasks, capacity, name, model)
            target = args.output / f'capacity-{capacity}-{name}.json'
            if args.verify_only:
                saved = read_json(target)
                require({k: v for k, v in result.items() if k != 'prediction_wall_ms'} ==
                        {k: v for k, v in saved.items() if k != 'prediction_wall_ms'}, 'replayed policy differs')
            else:
                write(target, result)
            results.append({k: v for k, v in result.items() if k not in {'requests', 'decisions'}})
    if args.verify_only:
        provenance = read_json(args.output / 'provenance.json')
        for filename, digest in provenance['source_sha256'].items():
            require(sha256(ROOT / filename) == digest, 'policy source differs: ' + filename)
        print(json.dumps({'status': 'passed', 'replayed_configurations': len(results),
                          'requests_decisions_and_hits_identical': True, 'source_and_input_hashes_verified': True}))
        return
    write(args.output / 'summary.json', {'status': 'passed', 'plan': plan,
          'test_tasks': sorted(tasks), 'results': results,
          'limitations': ['fixed observed requests and measured service duration; no counterfactual completion-time estimate',
              'all 10 test owners arrive together: constructed pressure workload, not natural arrival distribution',
              'metadata cache only; native 32B multi-owner KV enactment still required',
              'input-prefix KV only; generated suffix and output changes not represented',
              'same test set is now consumed for policy diagnostics; cannot tune and claim blind test',
              'queue is single-server FCFS; no continuous batching or preemption'],
          'predictor_boundary': 'current validated features only; future requests/timing remain in replay environment',
          'policy': 'on pressure, retire-free blocks first; then lowest max-owner F_U(3s) per equal block; LRU ties; leaves only; active never evicted; no hard pins',
          'runtime': {'python': platform.python_version()}})
    write(args.output / 'provenance.json', {'input_sha256': config['input_sha256'],
          'plan_sha256': sha256(args.plan), 'frozen_prediction_manifest_sha256': sha256(prediction / 'SHA256SUMS.json'),
          'source_sha256': {name: sha256(ROOT / name) for name in [
              'scripts/replay_qwen_cache_policy.py', 'src/leasepath/predictive_cache.py',
              'src/leasepath/cache.py', 'src/leasepath/prediction_training.py',
              'src/leasepath/reuse_predictor.py', 'src/leasepath/gbdt_predictor.py']}})
    write(args.output / 'SHA256SUMS.json', {p.name: sha256(p) for p in args.output.iterdir() if p.is_file()})
    print(json.dumps(results), flush=True)


if __name__ == '__main__': main()
