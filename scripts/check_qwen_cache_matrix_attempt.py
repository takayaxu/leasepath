"""Read-only audit of the stopped capacity-24 matrix attempts, including failures."""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from leasepath.prediction_training import read_json, read_jsonl, sha256
from leasepath.predictive_cache import PredictiveCache
from leasepath.qwen_data_tasks import equal
from leasepath.real_observer import validate_stream
from check_qwen_cache_matrix import check_actions
from check_qwen_cache_stage2 import check_native, require
from probe_qwen_cache_native import write


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base = args.run/'capacity-24'
    manifest = read_json(base/'run-manifest.json')
    plan = read_json(args.run/'frozen-plan.json')
    require(read_json(args.run/'matrix-manifest.json')['status'] == 'failed', 'attempt did not stop')
    require(manifest['status'] == 'failed', 'capacity did not stop')
    for name, digest in {**manifest['source_sha256'], **manifest['runtime_libraries_sha256']}.items():
        require(sha256(name) == digest, 'protected source/runtime changed: '+name)
    for name, digest in manifest.get('timed_worker_hashes', {}).items():
        require(sha256(name) == digest, 'timed worker source changed: '+name)
    entries = read_jsonl(args.ledger)
    by_id = {e['id']: e for e in entries}
    require(len(by_id) == len(entries) <= plan['budget_limits']['total'], 'global budget differs')
    for lane in ['probe', 'pilot']:
        require(sum(e['lane'] == lane for e in entries) <= plan['budget_limits'][lane], 'lane exceeded')
    if 'prior_failed_ledger' in plan:
        require(sha256(plan['prior_failed_ledger']) == plan['prior_failed_ledger_sha256'], 'prior ledger changed')
        require(sha256(plan['prior_failed_audit']) == plan['prior_failed_audit_sha256'], 'prior audit changed')
        require(len(read_jsonl(plan['prior_failed_ledger'])) == plan['prior_failed_generations'], 'prior usage differs')
        require(plan['prior_failed_generations']+len(entries) <= plan['stage_generation_total_cap'] == 3117,
                'stage budget exceeded')
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained('/home/liuxv/local-models/qwen25-coder-32b-leasepath/hf', local_files_only=True)
    refs = {}
    for directory in ['qwen-data-tasks-v5', 'qwen-data-tasks-extension38-v2']:
        refs.update(read_json(ROOT/'.artifacts'/directory/'references.private.json'))
    seen, failures = set(), []
    tasks = correct = consumed = streams = probes = tool_rejections = timings = 0
    for path in sorted((base/'native-gate').glob('*.json')):
        if path.name == 'summary.json': continue
        row = read_json(path)
        rid = row['reservation_id']
        require(rid in by_id and rid not in seen and by_id[rid]['lane'] == 'probe', 'probe reservation differs')
        seen.add(rid); probes += 1
        require(tokenizer.encode(row['prompt'], add_special_tokens=False) == row['tokens'], 'probe tokens differ')
        check_native(row, 24, 128, 2048)
    progress = read_json(base/'progress.json')
    for batch in progress['results']:
        path = base/f"concurrency-{batch['concurrency']}-repeat-{batch['repeat']}-{batch['policy']}"
        events, inputs, pending, artifacts = [], {}, {}, {}
        for source in ['controller', 'backend']:
            stream = path/'raw'/f'{source}.jsonl'
            validate_stream(stream); streams += 1
            for event in read_jsonl(stream):
                row = event['data']
                if event['kind'] in {'context_retire', 'engine_request'}: events.append(event)
                if event['kind'] == 'request_input':
                    require(row['request_id'] not in inputs, 'duplicate request input')
                    require(tokenizer.encode(row['prompt'], add_special_tokens=False) == row['token_ids'], 'input tokens differ')
                    inputs[row['request_id']] = row
                    if row['owner'] in pending:
                        require(pending.pop(row['owner']) in row['prompt'], 'real tool result not consumed')
                        consumed += 1
                if event['kind'] == 'tool_end':
                    pending[row['owner']] = json.dumps(row['result'], allow_nan=False, separators=(',', ':'))
                    if row['tool_name'] == 'compute' and 'result' in row['result']:
                        artifacts[row['owner'], row['result']['artifact_id']] = row['result']['result']
        require(not pending, 'unconsumed tool result')
        actual = [e['data'] for e in events if e['kind'] == 'engine_request']
        require(len(actual) == len(inputs) == batch['generations'], 'request counts differ')
        require(sum(r['hit'] for r in actual) == batch['cached_tokens'], 'hit total differs')
        require(sum(r['prefill'] for r in actual) == batch['executed_prefill_tokens'], 'prefill total differs')
        replica = PredictiveCache(24, block_size=128)
        for event in sorted(events, key=lambda e: e['ts_ns']):
            row, now = event['data'], event['ts_ns']/1e9
            if event['kind'] == 'context_retire':
                replica.retire(row['owner'], now); continue
            rid = row['request_id'][1:]
            require(rid in by_id and rid not in seen, 'live reservation missing or duplicate')
            scope = {'capacity': 24, 'concurrency': batch['concurrency'], 'repeat': batch['repeat'],
                     'policy': batch['policy'], 'lane': 'pilot', 'owner': row['owner']}
            require(all(by_id[rid][k] == v for k,v in scope.items()), 'reservation scope differs')
            seen.add(rid)
            require(row['tokens'] == inputs[row['request_id']]['token_ids'], 'native/client tokens differ')
            require(row['owner'] == inputs[row['request_id']]['owner'], 'native/client owner differs')
            check_native(row, 24, 128, 2048)
            check_actions(replica, row, manifest['runtime_compatibility'], 128, now)
        names = set()
        for name in batch['quality_records']:
            row = read_json(path/name)
            names.add(row['task']); tasks += 1; correct += row['success']
            require(math.isclose(row['jct_to_validated_answer_s'], row['validated_monotonic_s']-
                                 row['submit_monotonic_s'], abs_tol=1e-9), 'JCT differs')
            computed = []
            for (owner, identifier), value in artifacts.items():
                if owner != row['owner']: continue
                require(equal(read_json(path/'sessions'/owner/'artifacts'/(identifier+'.json')), value), 'saved artifact differs')
                computed.append(value)
            require(any(equal(value, refs[row['task']]['reference']) for value in computed), 'no correct computed artifact')
            known = (row['owner'], row['final']['artifact_id']) in artifacts
            numerical = equal(row['final']['answer'], refs[row['task']]['reference'])
            if row['success']:
                require(known and numerical, 'successful final fails strict checks')
                require(equal(artifacts[row['owner'],row['final']['artifact_id']], refs[row['task']]['reference']), 'selected artifact differs')
            else:
                require(not known or not numerical, 'failed final unexpectedly satisfies strict checks')
                failures.append({k: row[k] for k in ['task', 'error', 'generations', 'final']})
            saved_calls = read_jsonl(path/'sessions'/row['owner']/'artifacts/calls.jsonl')
            require(len(saved_calls) == len(row['tools']), 'tool record count differs')
            for call, saved in zip(row['tools'], saved_calls):
                require(all(call[k] == v for k,v in saved.items()), 'saved tool evidence differs')
                require(call['ended_ns'] >= call['started_ns'], 'tool time reversed')
                if call['status'] != 'ok':
                    require('error' in call['result'], 'rejection missing structured error')
                    tool_rejections += 1
                elif call['name'] == 'compute':
                    timing = call['worker_timing']
                    require(call['child_wall_ns'] >= timing['overall']['wall_ns'] == call['worker_execute_ns'] > 0, 'worker time boundary differs')
                    require(sum(p['wall_ns'] for p in timing['phases'].values()) <= timing['overall']['wall_ns'], 'phase overlap')
                    require(all(v >= 0 for p in timing['phases'].values() for v in p.values()), 'negative phase counter')
                    timings += 1
        require(names == set(plan['live_tasks'][:batch['concurrency']]), 'fixed task subset differs')
    require(seen == set(by_id), 'unmapped reservations')
    require(len(failures) == 1 and progress['results'][-1]['status'] == 'quality_failed', 'failure stopping boundary differs')
    result = {'status': 'passed', 'experiment_status': 'quality_failed_and_stopped',
        'requests_checked': len(seen), 'probe_requests': probes, 'live_requests': len(seen)-probes,
        'batches': len(progress['results']), 'tasks': tasks, 'correct': correct, 'failed': tasks-correct,
        'consumed_tool_results': consumed, 'sealed_streams': streams, 'tool_phase_records_checked': timings,
        'structured_tool_rejections_retained': tool_rejections, 'failures': failures,
        'ledger_sha256': sha256(args.ledger), 'source_sha256': manifest['source_sha256'],
        'scope': 'all reservations, physical cells, token IDs, same-state LRU, streams, saved artifacts, tool phases and JCT checked; failed final remains rejected'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write(args.output, result)
    print(json.dumps({k:v for k,v in result.items() if k != 'source_sha256'}), flush=True)


if __name__ == '__main__':
    main()
