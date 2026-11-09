"""CPU-only pressure feasibility and changed-tool file closure; not JCT evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.frozen_distribution_baseline import FrozenEqualCostBaseline,file_digest
from leasepath.pressure_matrix import check_topology,run_tool_group
from leasepath.resident_runtime import ResidentFiles,ResidentRuntime
from leasepath.resident_trace_paths import trace_paths
from leasepath.controlled_resources import host_sample
from probe_qwen_resident_tree_tools import tasks_and_refs
from run_qwen_distribution_natural_verified import worker_violation


def write(path,data):
    path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')


def counters():
    u=resource.getrusage(resource.RUSAGE_SELF)
    io={k:int(v) for k,v in (line.split(':') for line in Path('/proc/self/io').read_text().splitlines())}
    return {'read_bytes':io['read_bytes'],'major_faults':u.ru_majflt,'block_input_ops':u.ru_inblock}


def probe(args,plan):
    args.output.mkdir(parents=True,exist_ok=False)
    runtime=json.loads((args.preparation/'runtime-copy.json').read_text())
    if sys.prefix!=runtime['dst']:raise ValueError('private runtime required')
    system=json.loads((args.preparation/'system-residency.json').read_text())
    paths=[Path(r['path']) for r in system['files']]
    for row in system['files']:
        if file_digest(row['path'])!=row['sha256']:raise ValueError('system dependency changed')
    if args.extra:
        for row in json.loads(args.extra.read_text()):
            if file_digest(row['path'])!=row['sha256']:raise ValueError('trace dependency changed')
            paths.append(Path(row['path']))
    paths+=list((ROOT/'scripts/__pycache__').glob('*.pyc'))
    paths+=list((ROOT/'src/leasepath').glob('*.py'))+list((ROOT/'scripts').glob('*.py'))
    paths+=[args.plan,ROOT/plan['baseline_manifest']]
    tasks,refs=tasks_and_refs(plan)
    for task in tasks:
        task['file']=str(args.inputs/(task['id']+'.npz'))
        if file_digest(task['file'])!=task['input_sha256']:raise ValueError('input differs')
        paths.append(Path(task['file']))
    guards=[];summary={'status':'failed','model_generations':0,'groups':[],'scope':'CPU tools only; no native controller or natural JCT acceptance'}
    try:
        guards.append(ResidentRuntime(runtime));guards.append(ResidentFiles(paths))
        for count in plan['preflight']['tool_groups']:
            before=counters();host_before=host_sample()
            result=run_tool_group(tasks[:count],refs,plan,args.output/f'concurrency-{count}')
            after=counters();result['parent_delta']={k:after[k]-before[k] for k in before}
            result['host_before']=host_before;result['host_after']=host_sample()
            result['residency']=[g.verify() for g in guards]
            write(args.output/f'concurrency-{count}/summary.json',result)
            summary['groups'].append(result)
            write(args.output/'summary.json',summary)
            if not all(r['correct'] for r in result['rows']):raise ValueError('tool/reference failure')
            # Discovery trace is outside natural strict gates. Its observations remain recorded.
            if args.extra and (any(result['parent_delta'].values()) or any(worker_violation(r['call']) for r in result['rows'])):
                raise ValueError('pinned CPU preflight disk input/fault gate failed')
        summary['status']='passed'
    except BaseException as exc:
        summary['error']=f'{type(exc).__name__}: {exc}';raise
    finally:
        write(args.output/'summary.json',summary)
        for g in reversed(guards):g.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,default=ROOT/'configs/prediction/qwen_distribution_pressure_2026-11-10.json')
    p.add_argument('--preparation',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--inputs',type=Path);p.add_argument('--extra',type=Path);p.add_argument('--probe',action='store_true')
    a=p.parse_args();plan=json.loads(a.plan.read_text())
    if a.probe:return probe(a,plan)
    a.output.mkdir(parents=True,exist_ok=False)
    baseline=FrozenEqualCostBaseline(ROOT,ROOT/plan['baseline_manifest'])
    snapshot=host_sample();topology=check_topology(plan)
    write(a.output/'resources.json',{'host':snapshot,'topology':topology,'gpus':subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,memory.used,memory.total','--format=csv'],text=True)})
    if snapshot['meminfo_kib']['MemAvailable']<plan['memory_budget']['minimum_MemAvailable_gib']*1024**2:raise ValueError('aggregate memory admission failed')
    if shutil.disk_usage('/dev/shm').free<plan['memory_budget']['minimum_tmpfs_available_gib']*1024**3:raise ValueError('tmpfs space admission failed')
    write(a.output/'frozen-config.json',plan);write(a.output/'baseline.json',baseline.evidence())
    inputs=a.output/'inputs';inputs.mkdir()
    tasks,refs=tasks_and_refs(plan)
    for task in tasks:
        dest=inputs/(task['id']+'.npz');shutil.copyfile(task['file'],dest)
        if file_digest(dest)!=task['input_sha256']:raise ValueError('input hash differs')
        dest.chmod(0o444)
    system_path=a.preparation/'system-residency.json';system=json.loads(system_path.read_text())
    known={r['path']:r for r in system['files']}
    prior=json.loads((ROOT/'.artifacts/qwen-distribution-natural-2026-11-09/preparation-natural/system-residency.json').read_text())
    for row in prior['files']:
        path=Path(row['path'])
        if path.is_file() and not path.is_relative_to('/dev/shm') and '__pycache__' not in path.parts:
            if file_digest(path)!=row['sha256']:raise ValueError('prior dependency changed')
            known[str(path)]=row
    system['files']=list(known.values());write(system_path,system)
    runtime=json.loads((a.preparation/'runtime-copy.json').read_text())
    env={**os.environ,'PYTHONHOME':runtime['dst'],'PYTHONPATH':runtime['dst']+'/lib/python3.13/site-packages:'+str(ROOT/'.artifacts/prediction-holdout-deps')+':'+str(ROOT/'src'), 'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false'}
    cmd=[runtime['interpreter'],str(Path(__file__).resolve()),'--probe','--plan',str(a.plan),'--preparation',str(a.preparation),'--inputs',str(inputs)]
    with (a.output/'traced.log').open('w') as log:
        subprocess.run(['strace','-ff','-ttt','-yy','-s','128','-e','trace=openat,open,read,pread64,mmap,execve,wait4','-o',str(a.output/'tools.strace'),*cmd,'--output',str(a.output/'traced')],env=env,stdout=log,stderr=log,check=True)
    accessed=set()
    for path in sorted(a.output.glob('tools.strace.*')):accessed.update(trace_paths(path.read_text(errors='replace').splitlines()))
    files=[{'path':str(p),'bytes':p.stat().st_size,'sha256':file_digest(p)} for p in sorted(accessed) if p.is_file() and not any(p.is_relative_to(x) for x in ['/dev/shm','/proc','/sys','/dev'])]
    extra=a.output/'file-closure.json';write(extra,files)
    with (a.output/'pinned.log').open('w') as log:
        subprocess.run([*cmd,'--extra',str(extra),'--output',str(a.output/'pinned')],env=env,stdout=log,stderr=log,check=True)
    write(a.output/'summary.json',{'status':'passed','model_generations':0,'real_tool_calls':38,'concurrencies':[3,6,10],'full_native_controller_gate_passed':False,'natural_JCT_gate_passed':False,'file_closure_sha256':file_digest(extra),'baseline':baseline.evidence()})

if __name__=='__main__':main()
