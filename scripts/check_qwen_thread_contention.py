"""Replay diagnostic evidence without running any calculations or model requests."""
import argparse
import json
from pathlib import Path
import statistics
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from leasepath.prediction_training import read_json,sha256
from leasepath.qwen_data_tasks import equal
from probe_qwen_cache_matrix import write


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();plan=read_json(args.run/'frozen-plan.json');summary=read_json(args.run/'summary.json')
    for name,digest in plan['source_sha256'].items():
        if sha256(name)!=digest:raise ValueError('frozen source differs')
    reference=read_json(ROOT/'.artifacts/qwen-data-tasks-v5/references.private.json')[plan['task']['id']]['reference']
    if sha256(args.run/'inputs/logistic_holdout.npz')!=plan['task']['input_sha256']:raise ValueError('input hash differs')
    rows=[]
    for i,spec in enumerate(plan['order']):
        row=read_json(args.run/f'{i}.json')
        if row['threads']!=spec['threads'] or row['warmup']!=spec['warmup']:raise ValueError('frozen AB/BA/AB sequence differs')
        if row['returncode'] or not equal(row['result'],reference):raise ValueError('numerical/reference mismatch')
        diag=row['thread_diagnostics']
        if any(pool['num_threads']!=spec['threads'] for pool in diag['pools_after']):raise ValueError('threadpool setting differs')
        for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):
            if diag['environment'][k]!=str(spec['threads']):raise ValueError('env differs')
        o=row['worker_timing']['overall'];first=diag['samples'][0]['threads'];last=diag['samples'][-1]['threads']
        if set(first)-set(last):raise ValueError('thread exited before final sample; aggregate counter incomplete')
        delta_wait=sum(v['schedstat'][1]-first.get(tid,{'schedstat':[0,0,0]})['schedstat'][1] for tid,v in last.items())
        # Sampling is immediately after the worker counters; allow sampling CPU/scheduler overhead.
        if abs(delta_wait-o['all_thread_runqueue_wait_ns'])>100_000_000:raise ValueError('thread/aggregate wait inconsistent')
        if o['read_bytes'] or o['major_faults']:raise ValueError('disk I/O in numerical worker')
        rows.append({'index':i,**spec,'wall_s':row['tool_wall_s'],
                     'all_thread_wait_s':o['all_thread_runqueue_wait_ns']/1e9,
                     'peak_threads':max(len(s['threads']) for s in diag['samples'])})
    result={'status':'passed','correct':len(rows),'measured':6,'warmups':2,'new_model_generations':0,
        'order':'warmup two/one, then two/one; one/two; two/one; no exclusion',
        'modes':{str(n):{'mean_wall_s':statistics.mean(r['wall_s'] for r in rows if r['threads']==n and not r['warmup']),
                       'measured_wait_s':[r['all_thread_wait_s'] for r in rows if r['threads']==n and not r['warmup']],
                       'peak_threads':[r['peak_threads'] for r in rows if r['threads']==n and not r['warmup']]} for n in (2,1)},
        'scope':'whole recipe thread count treatment, no host exclusivity or cache policy comparison'}
    write(args.output,result);print(json.dumps(result),flush=True)


if __name__=='__main__':main()
