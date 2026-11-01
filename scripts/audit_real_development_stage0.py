"""Link controlled cases to complete raw backend evidence, without guessing outcomes."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from leasepath.real_audit import load_records, audit_requests, audit_blocks
from leasepath.real_observer import validate_stream, validate_observation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = load_records(args.run / 'raw')
    inventory = json.loads((args.run / 'expected-streams.json').read_text())
    transport = validate_observation((args.run / 'raw').glob('*.jsonl'), expected_stream_ids=inventory['stream_ids'])
    backend, blocks = audit_requests(raw), audit_blocks(raw)
    indexed = {r['request_id']: r for r in backend['requests']}
    enqueues = {r['data']['request_id']: r for r in raw if r['kind'] == 'request_enqueue'}
    releases = {r['data']['request_id']: r for r in raw if r['kind'] == 'request_release'}
    cases, controls = {}, []
    for directory in ['controlled-lifecycle', 'controlled-recovery']:
        path = args.run / directory / 'controller.jsonl'
        validate_stream(path)
        controls.extend(json.loads(s) for s in path.read_text().splitlines())
    for row in controls:
        if row['kind'] != 'request_ready':
            continue
        data = row['data']
        ids = [rid for rid in indexed if rid.startswith('cmpl-' + data['request_id'] + '-0-')]
        if len(ids) != 1 or not indexed[ids[0]]['complete']:
            raise ValueError('controlled request missing complete unique backend mapping')
        rid = ids[0]
        cases[data['case']] = {**indexed[rid], 'owner': data['owner'], 'client_request_id': data['request_id'],
                               'status': releases[rid]['data']['status'], 'retry_of': data.get('retry_of')}
    checks = {}
    checks['context_16K'] = cases['context_16K']['prompt_tokens'] == 16368
    for count in [2, 4, 8]:
        selected = [cases[f'concurrency_{count}_{i}'] for i in range(count)]
        timeline = []
        for c in selected:
            prefill = [r['ts_ns'] for r in raw if r['kind'] == 'prefill_scheduled' and r['data']['request_id'] == c['request_id']]
            timeline.extend([(min(prefill), 1), (c['release_ns'], -1)])
        active = peak = 0
        for _, delta in sorted(timeline):
            active += delta
            peak = max(peak, active)
        checks[f'actual_prefill_to_release_overlap_{count}'] = peak == count
    shared_ids = {cases['shared_0']['request_id'], cases['shared_1']['request_id']}
    shared_touches = [b for r in raw if r['kind'] == 'block_touch' and r['data']['request_id'] in shared_ids
                      for b in r['data']['after'] if b['ref_count'] >= 2]
    checks['shared_refcount_two'] = len(shared_touches) >= 32
    close_a = next(r for r in controls if r['kind'] == 'context_retire' and r['data']['owner'] == 'shared-owner-0')
    checks['other_owner_survives_close'] = (close_a['ts_ns'] < cases['shared_owner_B_continues']['lookup_ns'] and
                                          cases['shared_owner_B_continues']['cached_tokens_initial'] >= 4096)
    checks['actual_capacity_eviction'] = blocks['capacity_evictions'] > 0
    checks['partial_miss_after_eviction'] = (cases['after_capacity_competition']['cached_tokens_initial'] <
                                           cases['after_capacity_competition']['prompt_tokens'])
    checks['real_preemption_and_completion'] = (sum(cases[f'decode_pressure_{i}']['preemptions'] for i in range(2)) >= 1 and
                                               all(cases[f'decode_pressure_{i}']['normal_completion'] for i in range(2)))
    checks['cancel_reaches_backend_abort'] = cases['cancel_attempt']['status'] == 'FINISHED_ABORTED'
    checks['explicit_retry_has_distinct_real_request'] = (cases['explicit_client_retry_after_cancel']['normal_completion'] and
        cases['explicit_client_retry_after_cancel']['retry_of'] == cases['cancel_attempt']['client_request_id'])
    parent_close = next(r for r in controls if r['kind'] == 'context_retire' and r['data']['owner'] == 'fork-parent')
    checks['fork_child_survives_parent_close'] = (parent_close['ts_ns'] < cases['fork_child_after_parent_close']['lookup_ns'] and
                                                cases['fork_child_after_parent_close']['cached_tokens_initial'] >= 1024)
    original, restored = [enqueues[cases[key]['request_id']]['data']['prompt_token_ids']
                          for key in ['pressure_1', 'restored_input']]
    checks['restore_exact_input'] = original == restored
    checks['explicit_controlled_compaction_scope'] = any(r['kind'] == 'context_version_change' for r in controls)
    labels = [json.loads(s) for s in (args.run / 'controlled-dataset/episode_labels.jsonl').read_text().splitlines()]
    checks['complete_open_relationship_C'] = any(r['outcome'] == 'right_censored' for r in labels)
    checkpoint = [r for r in raw if r['kind'] == 'engine_checkpoint']
    checks['drained_checkpoint'] = len(checkpoint) == 1 and checkpoint[0]['data']['outstanding_requests'] == 0
    result = {'scope': 'controlled_actual_inference_and_KV_not_natural_occurrence',
              'checks': checks, 'passed': all(checks.values()), 'cases': cases,
              'backend': backend, 'blocks': blocks, 'transport': transport,
              'controlled_label_outcomes': dict(Counter(r['outcome'] for r in labels)),
              'limitations': ['Fork/compact/retry are explicit controller operations, not observed Codex operations.',
                             'Multi-lookup/preempted raw requests are audited but rejected by the v0.1 training projection.',
                             'Transport completeness, task correctness and inference overhead are separately assessed.']}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'checks': checks}))


if __name__ == '__main__':
    main()
