"""Describe every retained batch of a stopped attempt; never fill missing cells."""
import argparse
from collections import defaultdict
import statistics
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from leasepath.prediction_training import read_json, read_jsonl, sha256
from check_qwen_cache_matrix import describe
from check_qwen_cache_stage2 import require
from probe_qwen_cache_native import write


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--audit', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    audit, plan = read_json(args.audit), read_json(args.run/'frozen-plan.json')
    require(audit['status'] == 'passed', 'independent audit required')
    manifest = read_json(args.run/'capacity-24/run-manifest.json')
    progress = read_json(args.run/'capacity-24/progress.json')
    cells, batches, tasks, tools, decisions = [], [], [], [], []
    pairs = defaultdict(dict)
    for b in progress['results']:
        path = args.run/'capacity-24'/f"concurrency-{b['concurrency']}-repeat-{b['repeat']}-{b['policy']}"
        batches.append({k:v for k,v in b.items() if k not in {'final_physical','quality_records'}})
        for filename in b['quality_records']:
            q = read_json(path/filename)
            row = {'capacity': 24, 'concurrency': b['concurrency'], 'repeat': b['repeat'],
                   'policy': b['policy'], 'task': q['task'], 'success': q['success'], 'error': q['error'],
                   'generations': q['generations'], 'jct_or_failed_termination_s': q['jct_to_validated_answer_s']}
            if q['success']: row['jct_s'] = q['jct_to_validated_answer_s']
            tasks.append(row)
            pairs[b['concurrency'], b['repeat'], q['task']][b['policy']] = row
            for call in q['tools']:
                clean = {k:v for k,v in call.items() if k not in {'result','child_stderr'}}
                tools.append({**clean, 'capacity': 24, 'concurrency': b['concurrency'],
                    'repeat': b['repeat'], 'policy': b['policy'], 'task': q['task']})
        for e in read_jsonl(path/'raw/backend.jsonl'):
            if e['kind'] != 'engine_request': continue
            r = e['data']; d = r['policy_decision']; a = d['same_state_diagnostic']
            decisions.append({'capacity':24, 'concurrency':b['concurrency'], 'repeat':b['repeat'],
                'policy':b['policy'], 'owner':r['owner'], 'pressure':bool(d['evicted']),
                'live_victims':sum(bool(v['live_owners']) for v in d['evicted']), **a})
    for c in plan['capacities_blocks']:
        for n in plan['concurrencies']:
            for policy in plan['policies']:
                rows = [r for r in tasks if (r['capacity'],r['concurrency'],r['policy']) == (c,n,policy)]
                bs = [r for r in batches if (r['capacity'],r['concurrency'],r['policy']) == (c,n,policy)]
                ds = [r for r in decisions if (r['capacity'],r['concurrency'],r['policy']) == (c,n,policy)]
                complete = len(bs) == plan['repeats'] and all(r['success'] for r in rows)
                complete_comparison = all(sum(b['capacity']==c and b['concurrency']==n and b['policy']==p and
                    b['status']=='passed' for b in batches) == plan['repeats'] for p in plan['policies'])
                deltas = [v[policy]['jct_s']-v['retired_first_lru']['jct_s'] for k,v in pairs.items()
                    if k[0]==n and policy in v and 'retired_first_lru' in v and
                    v[policy]['success'] and v['retired_first_lru']['success']] if c==24 else []
                cells.append({'capacity':c, 'concurrency':n, 'policy':policy,
                    'status':'complete' if complete else 'not_run' if not rows else 'incomplete_or_quality_failed',
                    'complete_paired_comparison':complete_comparison,
                    'tasks_attempted':len(rows), 'tasks_correct':sum(r['success'] for r in rows),
                    'tasks_failed':sum(not r['success'] for r in rows),
                    'jct_s':describe([r['jct_s'] for r in rows if r['success']]),
                    'paired_jct_delta_s':describe(deltas) if complete_comparison else None,
                    'makespan_s':describe([b['makespan_s'] for b in bs]),
                    **{f'mean_{key}':statistics.mean(b[key] for b in bs) if bs else None for key in
                       ['cached_tokens','executed_prefill_tokens','prefill_s','decode_s','prediction_s','evictions']},
                    'pressure_admissions':sum(d['pressure'] for d in ds),
                    'same_state_victim_set_changes':sum(d['victim_set_differs'] for d in ds),
                    'same_state_sequence_changes':sum(d['victim_sequence_differs'] for d in ds),
                    'probability_spread_admissions':sum(d['supported_probability_spread'] for d in ds),
                    'live_victims':sum(d['live_victims'] for d in ds)})
    result = {k:v for k,v in audit.items() if k != 'source_sha256'}
    result.update(status='quality_failed_and_stopped',audit_status='passed',
        scope='partial fixed matrix, known development parents; no complete pressure-matrix claim or favorable-cell splicing',
        planned_batches=plan['planned_batches'],completed_batches=len(batches),
        prior_failed_generations=plan.get('prior_failed_generations',0),
        stage_generations_including_failed_attempt=plan.get('prior_failed_generations',0)+audit['requests_checked'],
        cells=cells,missing_capacity_cells=[32,64],full_matrix_complete=False,
        manifest_sha256=sha256(args.run/'matrix-manifest.json'),independent_audit_sha256=sha256(args.audit))
    args.output.mkdir(parents=True, exist_ok=False)
    for name,value in [('summary',result),('audit',audit),('tasks',tasks),('batches',batches),('tools',tools),('decisions',decisions)]:
        write(args.output/(name+'.json'),value)
    print({k:v for k,v in result.items() if k not in {'cells','failures'}})


if __name__ == '__main__':
    main()
