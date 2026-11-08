"""Read-only independent checks and paired timing summary; no model generation."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from leasepath.prediction_training import read_json, read_jsonl, sha256
from leasepath.qwen_data_backend import verify_request
from leasepath.qwen_data_tasks import equal
from leasepath.real_observer import validate_stream


def require(value, message):
    if not value: raise ValueError(message)


def check_native(result, capacity, block, headroom):
    native = result['native_evidence']
    verify_request(result, result['tokens'])
    require(result['hit'] == result['policy_decision']['cached_blocks']*block, 'policy/native hit differs')
    for name in ['before', 'peak', 'after']:
        snapshot = native[name]
        physical, idle = {}, set()
        for sequence, rows in snapshot['sequences'].items():
            if sequence != '0': require(len(rows) == block, 'partial persistent block')
            for position, cell, token in rows:
                if cell in physical: require(physical[cell] == (position, token), 'shared cell differs')
                physical[cell] = (position, token)
                if sequence != '0': idle.add(cell)
        require(len(physical) == snapshot['used_cells'] and len(idle) == snapshot['idle_union_cells'], 'physical union differs')
        require(len(idle) <= capacity*block and len(physical) <= capacity*block+headroom, 'physical quota exceeded')
    require('0' not in native['after']['sequences'], 'active cells leaked after request')
    if result['reason'] in {'eog', 'protocol_stop', 'length'}:
        require([r[2] for r in native['release_cells']] == result['tokens'], 'physical input tokens differ')
        require(native['release_cells'][:result['hit']] == native['lookup_cells'], 'physical hits moved')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = read_json(args.run/'run-manifest.json')
    require(manifest['status'] in {'passed', 'quality_failed'}, 'incomplete experiment')
    for name, digest in manifest['source_sha256'].items():
        require(sha256(name) == digest, 'frozen source changed: '+name)
    plan = read_json(args.run/'frozen-plan.json')
    capacity, block, headroom = plan['capacity_blocks'], plan['block_tokens'], plan['transient_headroom_tokens']
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained('/home/liuxv/local-models/qwen25-coder-32b-leasepath/hf', local_files_only=True)
    reservations = read_jsonl(ROOT/'.artifacts/qwen-cache-stage2/ledger.jsonl')
    by_reservation = {r['id']: r for r in reservations}
    require(len(by_reservation) == len(reservations) <= plan['budget_limits']['total'], 'ledger exceeds cap or repeats')
    for lane in ['probe', 'pilot']:
        require(sum(r['lane'] == lane for r in reservations) <= plan['budget_limits'][lane], 'lane cap exceeded')
    seen = set()
    gate_count = 0
    for directory in ['native-gate', 'prefill-probe']:
        for path in sorted((args.run/directory).glob('*.json')):
            if path.name == 'summary.json': continue
            row = read_json(path)
            if 'reservation_id' not in row: continue
            require(row['reservation_id'] in by_reservation and row['reservation_id'] not in seen, 'probe reservation mismatch')
            seen.add(row['reservation_id'])
            require(tokenizer.encode(row['prompt'], add_special_tokens=False) == row['tokens'], 'probe prompt token IDs differ')
            check_native(row, capacity, block, headroom)
            gate_count += 1
    refs = {}
    for directory in ['qwen-data-tasks-v5', 'qwen-data-tasks-extension38-v2']:
        refs.update(read_json(ROOT/'.artifacts'/directory/'references.private.json'))
    measured = read_json(args.run/'summary.json')
    pairs, summaries, requests, streams, consumed = defaultdict(dict), [], 0, [], 0
    for batch in measured['batches']:
        path = args.run/f"repeat-{batch['repeat']}-{batch['policy']}"
        for source in ['controller', 'backend']:
            streams.append(validate_stream(path/'raw'/f'{source}.jsonl'))
        inputs, pending, artifacts = {}, {}, {}
        for record in read_jsonl(path/'raw/controller.jsonl'):
            data = record['data']
            if record['kind'] == 'tool_end':
                pending[data['owner']] = json.dumps(data['result'], allow_nan=False, separators=(',', ':'))
                if data['tool_name'] == 'compute' and 'result' in data['result']:
                    artifacts[data['owner'], data['result']['artifact_id']] = data['result']['result']
            if record['kind'] == 'request_input':
                require(data['request_id'] not in inputs, 'duplicate request input')
                inputs[data['request_id']] = data
                require(tokenizer.encode(data['prompt'], add_special_tokens=False) == data['token_ids'], 'rendered input token IDs differ')
                if data['owner'] in pending:
                    require(pending.pop(data['owner']) in data['prompt'], 'real tool result not consumed')
                    consumed += 1
        data = [r['data'] for r in read_jsonl(path/'raw/backend.jsonl') if r['kind'] == 'engine_request']
        require(len(data) == batch['generations'] == len(inputs), 'backend/agent generation count differs')
        for row in data:
            require(row['request_id'].startswith('r'), 'invalid request ID')
            reservation = row['request_id'][1:]
            require(reservation in by_reservation and reservation not in seen, 'live generation reservation differs')
            seen.add(reservation)
            require(row['tokens'] == inputs[row['request_id']]['token_ids'], 'client/native tokens differ')
            require(row['owner'] == inputs[row['request_id']]['owner'], 'owner differs')
            check_native(row, capacity, block, headroom)
            requests += 1
        require(not pending, 'unconsumed tool results')
        for name in batch['quality_records']:
            row = read_json(path/name)
            require(math.isclose(row['jct_to_validated_answer_s'],
                row['validated_monotonic_s']-row['submit_monotonic_s'], abs_tol=1e-9), 'JCT origin differs')
            if row['success']:
                require(equal(row['final']['answer'], refs[row['task']]['reference']), 'independent final answer differs')
                actual = artifacts[row['owner'], row['final']['artifact_id']]
                require(equal(actual, refs[row['task']]['reference']), 'actual selected compute artifact differs')
                artifact_path = path/'sessions'/row['owner']/'artifacts'/(row['final']['artifact_id']+'.json')
                require(equal(read_json(artifact_path), actual), 'persisted artifact differs from tool output')
            pairs[(batch['repeat'], row['task'])][batch['policy']] = row
        require(sum(r['hit'] for r in data) == batch['cached_tokens'], 'cached total differs')
        require(sum(r['prefill'] for r in data) == batch['executed_prefill_tokens'], 'prefill total differs')
    paired = {}
    for policy in plan['policies']:
        values, deltas, failed = [], [], 0
        for key, results in pairs.items():
            if policy not in results or 'retired_first_lru' not in results: continue
            row, base = results[policy], results['retired_first_lru']
            if row['success'] and base['success']:
                values.append(row['jct_to_validated_answer_s'])
                deltas.append(row['jct_to_validated_answer_s']-base['jct_to_validated_answer_s'])
            else: failed += 1
        if values:
            import numpy as np
            paired[policy] = {'paired_successful_task_runs': len(values), 'failed_pairs': failed,
                'mean_jct_s': statistics.mean(values), 'p50_jct_s': statistics.median(values),
                'p95_jct_s': float(np.quantile(values, .95)),
                'mean_paired_delta_vs_retired_first_lru_s': statistics.mean(deltas),
                'interpretation': 'descriptive 3-parent repeated development smoke; repetitions not independent tasks'}
    summary = {'status': 'passed', 'gate_and_prefill_requests_checked': gate_count,
        'live_requests_checked': requests, 'consumed_tool_results_checked': consumed,
        'sealed_streams_checked': len(streams), 'budget_reserved': len(reservations),
        'requests_mapped_to_this_run': len(seen), 'reserved_outside_this_run': len(set(by_reservation)-seen),
        'current_run_all_requests_accounted': len(seen) == requests+gate_count,
        'physical_used_union_and_quota_checked': True, 'quality_rechecked': True,
        'paired_jct': paired, 'final_independent_performance_claim': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__': main()
