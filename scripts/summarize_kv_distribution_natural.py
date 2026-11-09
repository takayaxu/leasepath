"""Paired natural metrics from independently audited four-policy raw evidence."""
import argparse
import csv
import json
from pathlib import Path
import statistics


def mean(values):return statistics.mean(values) if values else None


def main():
    p=argparse.ArgumentParser();p.add_argument('--audit',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();audit=json.loads(args.audit.read_text())
    if audit['status']!='passed' or audit['diagnostic_trace']:raise ValueError('independent natural audit required')
    args.output.mkdir(parents=True,exist_ok=True)
    batches=audit['batch_metrics'];tasks=audit['task_metrics']
    policies=['retired_first_lru','gbdt_point','gbdt_distribution','gbdt_cost']
    fields=['mean_successful_jct_s','makespan_s','cached_tokens','actual_prefill_tokens','prefill_s','decode_s','prediction_s','controller_prepare_s']
    aggregates=[]
    for policy in policies:
        rows=[b for b in batches if b['policy']==policy]
        if not rows:continue
        scores=[score for b in rows for score in b['score_audits']]
        aggregates.append({'policy':policy,'batches':len(rows),**{k:mean([b[k] for b in rows]) for k in fields},
            'cost_fallback_sweeps':sum(s['cost_fallback_sweeps'] for s in scores),
            'distribution_fallback_sweeps':sum(s['distribution_fallback_sweeps'] for s in scores),
            'mean_decision_with_shadows_s_per_batch':sum(s['decision_with_shadows_s'] for s in scores)/len(rows),
            'parent_read_bytes':sum(b['parent_read_bytes'] for b in rows),
            'parent_major_faults':sum(b['parent_major_faults'] for b in rows),
            'parent_block_input_ops':sum(b['parent_block_input_ops'] for b in rows),
            'mean_output_tokens_per_batch':sum(sum(t['decode_tokens']) for t in tasks if t['policy']==policy)/len(rows)})
    pairs=[]
    for baseline,alternative in [('retired_first_lru',p) for p in policies[1:]]+[('gbdt_point','gbdt_distribution'),('gbdt_distribution','gbdt_cost'),('gbdt_point','gbdt_cost')]:
        cycles=[]
        for phase in sorted({b['phase'] for b in batches}):
            index={b['policy']:b for b in batches if b['phase']==phase}
            if baseline not in index or alternative not in index:continue
            left,right=index[baseline],index[alternative]
            if left['status']!='passed' or right['status']!='passed':continue
            tb={t['task']:t for t in tasks if t['phase']==phase and t['policy']==baseline}
            ta={t['task']:t for t in tasks if t['phase']==phase and t['policy']==alternative}
            if set(ta)!=set(tb):raise ValueError('paired task set differs')
            cycles.append({'cycle':phase,'baseline':baseline,'policy':alternative,
                **{k+'_delta':right[k]-left[k] for k in fields},
                'jct_relative_delta_pct':100*(right['mean_successful_jct_s']/left['mean_successful_jct_s']-1),
                'task_jct_deltas_s':{task:ta[task]['jct_s']-tb[task]['jct_s'] for task in tb},
                'input_shapes_equal':all(ta[t]['input_token_counts']==tb[t]['input_token_counts'] for t in tb),
                'exact_input_tokens_equal':all(ta[t]['input_token_sha256']==tb[t]['input_token_sha256'] for t in tb),
                'exact_output_tokens_equal':all(ta[t]['output_token_sha256']==tb[t]['output_token_sha256'] for t in tb),
                'output_token_counts_equal':all(ta[t]['decode_tokens']==tb[t]['decode_tokens'] for t in tb)})
        if not cycles:continue
        left_mean=mean([b['mean_successful_jct_s'] for b in batches if b['policy']==baseline and b['phase'] in {c['cycle'] for c in cycles}])
        delta=mean([c['mean_successful_jct_s_delta'] for c in cycles])
        pairs.append({'baseline':baseline,'policy':alternative,'complete_cycles':len(cycles),
            **{'mean_'+k+'_delta':mean([c[k+'_delta'] for c in cycles]) for k in fields},
            'jct_relative_delta_pct':100*delta/left_mean,'slower_cycles':sum(c['mean_successful_jct_s_delta']>0 for c in cycles),
            'input_shapes_equal':all(c['input_shapes_equal'] for c in cycles),
            'exact_input_tokens_equal':all(c['exact_input_tokens_equal'] for c in cycles),
            'exact_output_tokens_equal':all(c['exact_output_tokens_equal'] for c in cycles),
            'cycles':cycles})
    tools=[]
    for policy in policies:
        for task in sorted({t['task'] for b in batches for t in b['tools']}):
            rows=[t for b in batches if b['policy']==policy for t in b['tools'] if t['task']==task]
            if rows:tools.append({'policy':policy,'task':task,'n':len(rows),
                **{k:mean([r[k] for r in rows]) for k in ('natural_tool_wall_s','worker_kernel_s','queue_fraction')}})
    result={'status':'passed','experiment_status':audit['experiment_status'],'tasks':audit['tasks'],'correct':audit['correct'],
        'requests_checked':audit['requests_checked'],'aggregates':aggregates,'paired':pairs,'tools':tools,
        'scope':'Four paired cycles, three known development parents; repeats/parents are correlated. Strict local I/O controls, shared CPU and memory bandwidth. No general causal or statistical-significance claim.',
        'host_cpu_psi_range_pct':[min(b['host_cpu_psi_pct'] for b in batches),max(b['host_cpu_psi_pct'] for b in batches)],
        'host_io_psi_range_pct':[min(b['host_io_psi_pct'] for b in batches),max(b['host_io_psi_pct'] for b in batches)]}
    (args.output/'natural-comparison.json').write_text(json.dumps(result,indent=2)+'\n')
    for name,rows in [('policy-comparison.csv',aggregates),('tool-comparison.csv',tools),
        ('paired-cycles.csv',[{k:v for k,v in c.items() if k!='task_jct_deltas_s'} for pair in pairs for c in pair['cycles']])]:
        if not rows:continue
        with (args.output/name).open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(rows)
    print(json.dumps({k:v for k,v in result.items() if k not in {'paired','tools'}},indent=2))


if __name__=='__main__':main()
