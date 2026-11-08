"""Read-only physical, protocol, decision, timing and paired matrix audit."""
import argparse
from collections import defaultdict
from copy import deepcopy
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from leasepath.cache import block_keys
from leasepath.predictive_cache import PredictiveCache, Resident
from leasepath.prediction_training import read_json, read_jsonl, sha256
from leasepath.qwen_data_tasks import equal
from leasepath.real_observer import validate_stream
from check_qwen_cache_stage2 import check_native, require
from probe_qwen_cache_native import write


def describe(values):
    import numpy as np
    return {'n': len(values), 'mean': statistics.mean(values), 'p50': statistics.median(values),
            'p95': float(np.quantile(values, .95)), 'min': min(values), 'max': max(values)} if values else None


def check_actions(replica, row, compatibility, block, now):
    keys = block_keys(row['tokens'][:((len(row['tokens'])-1)//block)*block], compatibility, block)
    decision = row['policy_decision']
    shadow = deepcopy(replica)
    reference = shadow.start(row['owner'], keys, now)
    actual = [r['key'] for r in decision['evicted']]
    alternate = [r['key'] for r in reference['evicted']]
    audit = decision['same_state_diagnostic']
    require(audit['retired_first_lru_victims'] == alternate, 'shadow LRU differs from independent replay')
    require(audit['victim_set_differs'] == (set(actual) != set(alternate)), 'decision divergence flag differs')
    require(audit['victim_sequence_differs'] == (actual != alternate), 'decision sequence flag differs')
    require(replica.prefix_hits(keys) == decision['cached_blocks'], 'independent logical hit differs')
    owner = row['owner']
    replica.idle.pop(owner, None)
    replica.bindings[owner] = tuple(keys)
    live = replica._live_owners()
    for victim in decision['evicted']:
        key = victim['key']
        require(key in replica.blocks and key not in keys and
                key not in {b.parent for b in replica.blocks.values()}, 'illegal actual victim')
        require(victim['live_owners'] == sorted(live.get(key, ())), 'victim ownership differs')
        del replica.blocks[key]
    for i, key in enumerate(keys):
        replica.clock += 1
        if key not in replica.blocks:
            replica.blocks[key] = Resident(keys[i-1] if i else None, i, replica.clock)
        replica.blocks[key].touched = replica.clock
        replica.blocks[key].active.add(owner)
    replica.advance(now)
    replica.release(owner, {'elapsed_idle_s': 0., 'compatibility_id': compatibility,
        'prefix_tokens': len(keys)*block, 'tool_name': None, 'tool_elapsed_s': None,
        'phase': None, 'retry_count': None, 'is_queued': None, 'queue_length': None}, now)
    require(len(replica.blocks) == decision['resident_blocks'], 'resident metadata differs')
    return audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plan = read_json(args.run/'frozen-plan.json')
    manifest = read_json(args.run/'matrix-manifest.json')
    require(manifest['status'] == 'passed', 'matrix incomplete')
    require(len(manifest['capacities']) == len(plan['capacities_blocks']), 'capacities missing')
    reservations = read_jsonl(ROOT/'.artifacts/qwen-cache-stage3/ledger.jsonl')
    by_id = {r['id']: r for r in reservations}
    require(len(by_id) == len(reservations) <= plan['budget_limits']['total'], 'global ledger differs')
    for lane in ['probe', 'pilot']:
        require(sum(r['lane'] == lane for r in reservations) <= plan['budget_limits'][lane], 'lane exceeded')
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained('/home/liuxv/local-models/qwen25-coder-32b-leasepath/hf', local_files_only=True)
    refs = {}
    for directory in ['qwen-data-tasks-v5', 'qwen-data-tasks-extension38-v2']:
        refs.update(read_json(ROOT/'.artifacts'/directory/'references.private.json'))
    seen, expected_cells, task_rows, batch_rows, tool_rows, decision_rows = set(), set(), [], [], [], []
    streams, consumed, live_requests, probe_requests = 0, 0, 0, 0
    block, headroom = plan['block_tokens'], plan['transient_headroom_tokens']
    for capacity in plan['capacities_blocks']:
        directory = args.run/f'capacity-{capacity}'
        cm = read_json(directory/'run-manifest.json')
        require(cm['status'] == 'passed', 'capacity incomplete')
        for name, digest in cm['source_sha256'].items():
            require(sha256(name) == digest, 'frozen source differs: '+name)
        for name, digest in cm['runtime_libraries_sha256'].items():
            require(sha256(name) == digest, 'runtime library differs: '+name)
        measured = read_json(directory/'summary.json')
        for path in sorted((directory/'native-gate').glob('*.json')):
            if path.name == 'summary.json': continue
            row = read_json(path)
            reservation = row['reservation_id']
            require(reservation in by_id and reservation not in seen, 'probe reservation differs')
            require(by_id[reservation]['lane'] == 'probe' and by_id[reservation]['capacity'] == capacity, 'probe scope differs')
            seen.add(reservation)
            require(tokenizer.encode(row['prompt'], add_special_tokens=False) == row['tokens'], 'probe tokens differ')
            check_native(row, capacity, block, headroom)
            probe_requests += 1
        for batch in measured['batches']:
            concurrency, repeat, policy = batch['concurrency'], batch['repeat'], batch['policy']
            cell = (capacity, concurrency, repeat, policy)
            require(cell not in expected_cells, 'duplicate cell')
            expected_cells.add(cell)
            path = directory/f'concurrency-{concurrency}-repeat-{repeat}-{policy}'
            records = read_jsonl(path/'raw/controller.jsonl')
            backend = read_jsonl(path/'raw/backend.jsonl')
            for source in ['controller', 'backend']:
                validate_stream(path/'raw'/f'{source}.jsonl'); streams += 1
            inputs, pending, artifacts = {}, {}, {}
            for record in records:
                data = record['data']
                if record['kind'] == 'tool_end':
                    pending[data['owner']] = json.dumps(data['result'], allow_nan=False, separators=(',', ':'))
                    if data['tool_name'] == 'compute' and 'result' in data['result']:
                        artifacts[data['owner'], data['result']['artifact_id']] = data['result']['result']
                elif record['kind'] == 'request_input':
                    require(data['request_id'] not in inputs, 'duplicate input')
                    inputs[data['request_id']] = data
                    require(tokenizer.encode(data['prompt'], add_special_tokens=False) == data['token_ids'], 'input tokenization differs')
                    if data['owner'] in pending:
                        require(pending.pop(data['owner']) in data['prompt'], 'tool result not consumed')
                        consumed += 1
            require(not pending, 'pending tool results')
            actual = [r for r in backend if r['kind'] == 'engine_request']
            require(len(actual) == len(inputs) == batch['generations'], 'request counts differ')
            replica = PredictiveCache(capacity, block_size=block, policy='retired_first_lru')
            events = actual+[r for r in records if r['kind'] == 'context_retire']
            for event in sorted(events, key=lambda e: e['ts_ns']):
                row, now = event['data'], event['ts_ns']/1e9
                if event['kind'] == 'context_retire':
                    replica.retire(row['owner'], now); continue
                reservation = row['request_id'][1:]
                require(reservation in by_id and reservation not in seen, 'live reservation differs')
                entry = by_id[reservation]
                require(entry['lane'] == 'pilot' and all(entry[k] == v for k,v in
                    zip(['capacity','concurrency','repeat','policy'], cell)), 'live reservation scope differs')
                seen.add(reservation)
                require(row['tokens'] == inputs[row['request_id']]['token_ids'], 'native input differs')
                require(row['owner'] == inputs[row['request_id']]['owner'], 'owner differs')
                check_native(row, capacity, block, headroom)
                audit = check_actions(replica, row, cm['runtime_compatibility'], block, now)
                decision_rows.append({'capacity': capacity, 'concurrency': concurrency, 'repeat': repeat,
                    'policy': policy, 'owner': row['owner'], 'pressure': bool(row['policy_decision']['evicted']),
                    'live_victims': sum(bool(x['live_owners']) for x in row['policy_decision']['evicted']),
                    **audit})
                live_requests += 1
            require(batch['tasks'] == concurrency and batch['complete_success'] == concurrency, 'quality count differs')
            names = set()
            for name in batch['quality_records']:
                row = read_json(path/name); names.add(row['task'])
                require(row['success'], 'quality failed')
                require(math.isclose(row['jct_to_validated_answer_s'], row['validated_monotonic_s']-
                                     row['submit_monotonic_s'], abs_tol=1e-9), 'JCT differs')
                require(equal(row['final']['answer'], refs[row['task']]['reference']), 'final reference differs')
                artifact = artifacts[row['owner'], row['final']['artifact_id']]
                require(equal(artifact, refs[row['task']]['reference']), 'computed artifact differs')
                require(equal(read_json(path/'sessions'/row['owner']/'artifacts'/
                    (row['final']['artifact_id']+'.json')), artifact), 'saved artifact differs')
                task_rows.append({'capacity': capacity, 'concurrency': concurrency, 'repeat': repeat,
                    'policy': policy, 'task': row['task'], 'jct_s': row['jct_to_validated_answer_s']})
                for call in row['tools']:
                    require(call['status'] == 'ok', 'tool call failed')
                    clean = {k: v for k,v in call.items() if k not in {'result', 'child_stderr'}}
                    clean.update(capacity=capacity, concurrency=concurrency, repeat=repeat, policy=policy, task=row['task'])
                    if call['name'] == 'compute':
                        timing = call['worker_timing']; overall = timing['overall']
                        require(call['child_wall_ns'] >= overall['wall_ns'] == call['worker_execute_ns'] > 0, 'worker timing boundary differs')
                        require(sum(v['wall_ns'] for v in timing['phases'].values()) <= overall['wall_ns'], 'phase overlap')
                        require(all(v >= 0 for phase in timing['phases'].values() for v in phase.values()), 'negative diagnostic counter')
                    tool_rows.append(clean)
            require(names == set(plan['live_tasks'][:concurrency]), 'nested task subset differs')
            require(sum(r['data']['hit'] for r in actual) == batch['cached_tokens'], 'hit sum differs')
            require(sum(r['data']['prefill'] for r in actual) == batch['executed_prefill_tokens'], 'prefill sum differs')
            batch_rows.append({k: v for k,v in batch.items() if k not in {'final_physical','quality_records'}})
    expected = {(c,n,r,p) for c in plan['capacities_blocks'] for n in plan['concurrencies']
                for r in range(plan['repeats']) for p in plan['policies']}
    require(expected_cells == expected and len(task_rows) == plan['planned_task_executions'], 'matrix incomplete')
    require(seen == set(by_id), 'unmapped reservations')
    pairs = defaultdict(dict)
    for row in task_rows:
        pairs[row['capacity'], row['concurrency'], row['repeat'], row['task']][row['policy']] = row['jct_s']
    cells = []
    for c in plan['capacities_blocks']:
        for n in plan['concurrencies']:
            for p in plan['policies']:
                rows = [x for x in task_rows if (x['capacity'],x['concurrency'],x['policy']) == (c,n,p)]
                batches = [x for x in batch_rows if (x['capacity'],x['concurrency'],x['policy']) == (c,n,p)]
                decisions = [x for x in decision_rows if (x['capacity'],x['concurrency'],x['policy']) == (c,n,p)]
                deltas = [v[p]-v['retired_first_lru'] for k,v in pairs.items() if k[:2] == (c,n)]
                cells.append({'capacity': c, 'concurrency': n, 'policy': p,
                    'jct_s': describe([x['jct_s'] for x in rows]),
                    'paired_jct_delta_s': describe(deltas),
                    'makespan_s': describe([x['makespan_s'] for x in batches]),
                    **{f'mean_{key}': statistics.mean(x[key] for x in batches) for key in
                       ['cached_tokens','executed_prefill_tokens','prefill_s','decode_s','prediction_s','evictions']},
                    'pressure_admissions': sum(x['pressure'] for x in decisions),
                    'same_state_victim_set_changes': sum(x['victim_set_differs'] for x in decisions),
                    'same_state_sequence_changes': sum(x['victim_sequence_differs'] for x in decisions),
                    'probability_spread_admissions': sum(x['supported_probability_spread'] for x in decisions),
                    'live_victims': sum(x['live_victims'] for x in decisions)})
    summary = {'status': 'passed', 'scope': 'descriptive fixed matrix of 10 known development parents; repetitions and nested sets are not independent sources',
        'live_requests_checked': live_requests, 'probe_requests_checked': probe_requests,
        'sealed_streams_checked': streams, 'consumed_tool_results_checked': consumed,
        'budget_reserved': len(reservations), 'unmapped_reservations': len(set(by_id)-seen),
        'batches_checked': len(batch_rows), 'tasks_correct': len(task_rows), 'cells': cells,
        'same_state_LRU_replayed_independently': True, 'final_independent_performance_claim': False}
    args.output.mkdir(parents=True, exist_ok=False)
    for name, obj in [('summary',summary),('tasks',task_rows),('batches',batch_rows),('tools',tool_rows),('decisions',decision_rows)]:
        write(args.output/(name+'.json'), obj)
    print(json.dumps({k:v for k,v in summary.items() if k != 'cells'}), flush=True)


if __name__ == '__main__':
    main()
