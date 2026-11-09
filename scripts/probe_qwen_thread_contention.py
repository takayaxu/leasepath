"""Fixed two/one-thread logistic diagnostic; no policy or end-to-end claims."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.controlled_resources import host_sample
from leasepath.prediction_training import read_json,sha256
from leasepath.qwen_data_tasks import equal
from leasepath.restricted_data_tools import open_resource
from probe_qwen_controlled_tools import LockedInputs,LockedRuntime,tasks_and_refs
from probe_qwen_cache_matrix import write


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--runtime-copy',type=Path,required=True)
    args=p.parse_args()
    if not args.output.resolve().is_relative_to(Path('/dev/shm')):raise ValueError('tmpfs output required')
    args.output.mkdir(parents=True,exist_ok=False)
    old=read_json(ROOT/'configs/prediction/qwen_cache_controlled_2026-11-09.json')
    task=next(t for t in tasks_and_refs(old)[0] if t['id']=='logistic_holdout')
    refs=tasks_and_refs(old)[1]
    locked_runtime=LockedRuntime(read_json(args.runtime_copy))
    inputs=LockedInputs([task],args.output/'inputs')
    sources=[Path(__file__).resolve(),args.runtime_copy,ROOT/'src/leasepath/restricted_data_thread_probe_worker.py',
             ROOT/'src/leasepath/restricted_data_controlled_worker.py',ROOT/'src/leasepath/controlled_resources.py',
             ROOT/'src/leasepath/restricted_data_extended.py']
    hashes={str(p):sha256(p) for p in sources}
    plan={'task':task,'controls':old['tool_controls'][task['id']],
          'order':[{'warmup':True,'threads':2},{'warmup':True,'threads':1}]+
                  [{'warmup':False,'threads':n} for n in [2,1,1,2,2,1]],
          'source_sha256':hashes,'new_model_generations':0,
          'recipe_choice':'one numerical thread removes own BLAS helper contention; not chosen by fastest mean',
          'failure_rule':'stop on numerical/input/source/child failure; no replacements or outlier exclusion',
          'scope':'mechanism diagnostic only; shared host uncontrolled; do not infer cache-policy JCT'}
    write(args.output/'frozen-plan.json',plan)
    write(args.output/'input-copies.json',inputs.records)
    rows=[]
    try:
        for i,spec in enumerate(plan['order']):
            for name,digest in hashes.items():
                if sha256(name)!=digest:raise ValueError('source changed')
            n=spec['threads'];before=host_sample();start=time.monotonic_ns()
            fd,identity=open_resource(task['file'],task['input_sha256'])
            try:
                request={'arguments':dict(dataset_id=task['dataset_id'],operation=task['operation'],**task['parameters']),
                         'kind':task['kind'],'fd':fd,'threads':n,'memory_gib':12,'controls':plan['controls']}
                env={**os.environ,**{k:str(n) for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']}}
                proc=subprocess.Popen([sys.executable,'-m','leasepath.restricted_data_thread_probe_worker'],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                    pass_fds=(fd,),env=env,start_new_session=True)
                try:
                    stdout,stderr=proc.communicate(json.dumps(request),timeout=max(0,240-(time.monotonic_ns()-start)/1e9))
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL);proc.communicate();raise
                row={'index':i,**spec,'pid':proc.pid,'returncode':proc.returncode,'stderr':stderr,'before':before}
                if proc.returncode:
                    write(args.output/f'{i}-failed.json',row);raise ValueError('child failed')
                row.update(json.loads(stdout))
                post_fd,post_identity=open_resource(task['file'],task['input_sha256']);os.close(post_fd)
                if post_identity!=identity:raise ValueError('source changed')
                row.update(tool_wall_s=(time.monotonic_ns()-start)/1e9,after=host_sample(),
                           correct=equal(row['result'],refs[task['id']]['reference']))
                write(args.output/f'{i}.json',row);rows.append(row)
                if not row['correct']:raise ValueError('reference differs')
                o=row['worker_timing']['overall']
                print(i,n,'wall',round(row['tool_wall_s'],3),'queue',round(o['all_thread_runqueue_wait_ns']/1e9,3),
                      'peak_threads',max(len(x['threads']) for x in row['thread_diagnostics']['samples']),flush=True)
            finally:os.close(fd)
        write(args.output/'summary.json',{'status':'passed','calls':len(rows),'measured':6,'warmups':2,
              'correct':sum(r['correct'] for r in rows),'new_model_generations':0,
              'scope':plan['scope'],'rows':[{k:r[k] for k in ['index','threads','warmup','tool_wall_s','worker_timing','thread_diagnostics']} for r in rows]})
    finally:inputs.close();locked_runtime.close()


if __name__=='__main__':main()
