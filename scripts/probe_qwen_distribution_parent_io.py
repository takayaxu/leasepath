"""Bounded parent + real tool file attribution outside any GPU/JCT measurement.

Two three-tool groups: traced and then pinned with discovered file identities.
This does not identify the historical 4KiB read and does not cover native CUDA
execution. A later complete controller preflight is still required for JCT.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.prediction_training import read_json,sha256
from leasepath.resident_runtime import ResidentFiles,ResidentRuntime
from leasepath.resident_trace_paths import trace_paths
from leasepath.shared_experiment_residency import LockedInputs
from probe_qwen_resident_tree_tools import tasks_and_refs,run_group
from probe_qwen_cache_matrix import write


def counters():
    usage=resource.getrusage(resource.RUSAGE_SELF);children=resource.getrusage(resource.RUSAGE_CHILDREN)
    io={k:int(v) for k,v in (line.split(':') for line in Path('/proc/self/io').read_text().splitlines())}
    return io | {'self_major_faults':usage.ru_majflt,'self_block_input_ops':usage.ru_inblock,
        'reaped_child_major_faults':children.ru_majflt,'reaped_child_block_input_ops':children.ru_inblock}


def probe(args):
    args.output.mkdir(parents=True,exist_ok=False)
    runtime=read_json(args.preparation/'runtime-copy.json')
    if sys.prefix!=runtime['dst']:raise ValueError('private prefix required')
    system=read_json(args.preparation/'system-residency.json')
    for row in system['files']:
        if sha256(row['path'])!=row['sha256']:raise ValueError('system identity differs')
    extra=read_json(args.extra) if args.extra else []
    paths=[Path(row['path']) for row in system['files']]+[Path(row['path']) for row in extra]
    for row in extra:
        if sha256(row['path'])!=row['sha256']:raise ValueError('traced file identity differs')
    plan=read_json(ROOT/'configs/prediction/qwen_resident_tree_natural_2026-11-09.json')
    tasks,refs=tasks_and_refs(plan)
    for task in tasks:task['file']=str(args.inputs/(task['id']+'.npz'))
    # Load the actual model-controller Python path, but do not substitute fake
    # model timings or claim this is a complete native controller preflight.
    import qwen_cache_resident_tree_batch
    from leasepath.distribution_cache_backend import DistributionEngine
    sources=[p for p in sorted((ROOT/'src/leasepath').glob('*.py'))]+[Path(qwen_cache_resident_tree_batch.__file__)]
    runtime_guard=ResidentRuntime(runtime);files_guard=ResidentFiles(paths+sources)
    input_guard=ResidentFiles([task['file'] for task in tasks])
    try:
        baseline=counters();source_reads=[]
        for path in sources:
            before=counters()
            with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            after=counters()
            source_reads.append({'path':str(path),'sha256':digest,'read_bytes':after['read_bytes']-before['read_bytes'],
                'major_faults':after['self_major_faults']-before['self_major_faults']})
        group_before=counters();group=run_group(tasks,refs,plan,args.output/'real-tools');group_after=counters()
        after=counters();delta={k:after[k]-baseline[k] for k in baseline}
        write(args.output/'summary.json',{'status':'passed' if all(r['correct'] for r in group['rows']) else 'failed',
            'pid':os.getpid(),'real_compute_calls':3,'source_reads':source_reads,'parent_process_delta':delta,
            'tool_group_process_delta':{k:group_after[k]-group_before[k] for k in group_before},
            'worker_usage':[{'task':r['task'],'lifetime':r['call'].get('child_lifetime'),
                'io_since_exec':r['call'].get('file_diagnostics',{}).get('io_since_exec')} for r in group['rows']],
            'residency':{'runtime':runtime_guard.verify(),'files':files_guard.verify(),'inputs':input_guard.verify()},
            'scope':'real parent Python/controller imports/source hashes and three actual tools; native CUDA and full model-driven loop not covered',
            'historical_4096B_file_identified':False,'natural_JCT_gate_passed':False})
        if not all(r['correct'] for r in group['rows']):raise ValueError('real tool answer differs')
    finally:
        input_guard.close();files_guard.close();runtime_guard.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--preparation',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--inputs',type=Path);p.add_argument('--extra',type=Path);p.add_argument('--probe',action='store_true');args=p.parse_args()
    if args.probe:return probe(args)
    args.output.mkdir(parents=True,exist_ok=False)
    runtime=read_json(args.preparation/'runtime-copy.json');system=read_json(args.preparation/'system-residency.json')
    previous=read_json(ROOT/'.artifacts/qwen-cache-resident-tree-natural-2026-11-09/system-residency.json')
    known={r['path']:r for r in system['files']}
    for row in previous['files']:
        path=Path(row['path'])
        if path.is_file() and not path.is_relative_to('/dev/shm'):
            known[str(path)]={'path':str(path),'bytes':path.stat().st_size,'sha256':sha256(path)}
    system['files']=list(known.values());write(args.preparation/'system-residency.json',system)
    plan=read_json(ROOT/'configs/prediction/qwen_resident_tree_natural_2026-11-09.json');tasks,refs=tasks_and_refs(plan)
    for task in tasks:task['file']=str(ROOT/'.artifacts/qwen-cache-resident-tree-natural-2026-11-09/run/inputs'/(task['id']+'.npz'))
    input_guard=LockedInputs(tasks,args.output/'inputs')
    try:
        env={**os.environ,'PYTHONHOME':runtime['dst'],'PYTHONPATH':runtime['dst']+'/lib/python3.13/site-packages:'+str(ROOT/'.artifacts/prediction-holdout-deps')+':'+str(ROOT/'src'),
            'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false'}
        base=[runtime['interpreter'],str(Path(__file__).resolve()),'--probe','--preparation',str(args.preparation.resolve()),'--inputs',str((args.output/'inputs').resolve())]
        trace_prefix=args.output/'parent.strace'
        command=['strace','-ff','-ttt','-yy','-s','128','-e','trace=openat,open,read,pread64,mmap,execve,wait4',
            '-o',str(trace_prefix),*base,'--output',str(args.output/'traced')]
        with (args.output/'traced.log').open('w') as log:subprocess.run(command,env=env,stdout=log,stderr=log,check=True)
        accessed=set()
        traces=sorted(args.output.glob('parent.strace.*'))
        for path in traces:accessed.update(trace_paths(path.read_text(errors='replace').splitlines()))
        files=[]
        for path in sorted(accessed):
            if path.is_relative_to('/dev/shm') or path.is_relative_to('/proc') or path.is_relative_to('/sys') or not path.is_file():continue
            files.append({'path':str(path),'bytes':path.stat().st_size,'sha256':sha256(path)})
        extra=args.output/'actual-parent-descendant-files.json';write(extra,files)
        with (args.output/'locked.log').open('w') as log:
            subprocess.run([*base,'--output',str(args.output/'locked'),'--extra',str(extra.resolve())],env=env,stdout=log,stderr=log,check=True)
        traced=read_json(args.output/'traced/summary.json');locked=read_json(args.output/'locked/summary.json')
        write(args.output/'summary.json',{'status':'passed','real_compute_calls':6,'model_generations':0,
            'traced':traced,'locked':locked,'regular_disk_files':len(files),'trace_processes_or_threads':len(traces),
            'historical_4096B_file_identified':False,'full_native_controller_JCT_gate_passed':False})
    finally:input_guard.close()


if __name__=='__main__':main()
