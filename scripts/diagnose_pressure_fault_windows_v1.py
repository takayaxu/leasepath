"""Two bounded 10-tool diagnostics; no GPU, no acceptance or replacement samples."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.frozen_distribution_baseline import file_digest
from leasepath.controlled_resources import host_sample
from leasepath.pressure_matrix import check_topology
from leasepath.resident_runtime import ResidentFiles,ResidentRuntime
from leasepath.startup_fault_tools_v1 import StartupFaultSessionTools
from leasepath.restricted_data_tools import Resource
from leasepath.qwen_data_tasks import equal


def write(path,obj):path.write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
def read(path):return json.loads(Path(path).read_text())


def tasks_refs(plan):
    tasks={};refs={}
    for name in ('qwen-data-tasks-v5','qwen-data-tasks-extension38-v2'):
        d=ROOT/'.artifacts'/name
        tasks.update({t['id']:t for t in read(d/'tasks.private.json')});refs.update(read(d/'references.private.json'))
    return [tasks[n] for n in plan['pressure_controls']['live_tasks']],refs


def counters():
    u=resource.getrusage(resource.RUSAGE_SELF)
    io={k:int(v) for k,v in (line.split(':') for line in Path('/proc/self/io').read_text().splitlines())}
    return {'major_faults':u.ru_majflt,'block_input_ops':u.ru_inblock,'read_bytes':io['read_bytes']}


def probe(args,plan):
    args.output.mkdir(parents=True,exist_ok=False)
    runtime=read(args.preparation/'runtime-copy.json');system=read(args.preparation/'system-residency.json')
    assert sys.prefix==runtime['dst']
    tasks,refs=tasks_refs(plan);paths=[]
    for t in tasks:
        t['file']=str(args.preparation.parent/'tool-preflight-v2/inputs'/(t['id']+'.npz'))
        assert file_digest(t['file'])==t['input_sha256'];paths.append(Path(t['file']))
    for row in system['files']:
        assert file_digest(row['path'])==row['sha256'],row['path'];paths.append(Path(row['path']))
    for path,digest in plan['source_sha256'].items():
        assert file_digest(path)==digest,path;paths.append(Path(path))
    if args.extra:
        for row in read(args.extra):
            assert file_digest(row['path'])==row['sha256'];paths.append(Path(row['path']))
    guards=[];summary={'status':'failed','model_generations':0,'natural_gate_passed':False,'diagnostic_only':True}
    try:
        guards=[ResidentRuntime(runtime),ResidentFiles(paths)]
        allowed={(Path(r['path']).stat().st_dev,Path(r['path']).stat().st_ino) for g in guards for r in g.records}
        write(args.output/'locked-files.json',[r for g in guards for r in g.records])
        before=counters();host_before=host_sample()
        def run(task):
            tool=StartupFaultSessionTools(args.output/task['id'],
                {task['dataset_id']:Resource(Path(task['file']),task['input_sha256'],task['kind'],task['metadata'])},
                task['operations'],timeout=240,threads=1,memory_gib=12,
                controls=plan['pressure_controls']['tool_controls'][task['id']],session_key=task['id'])
            answer=tool.call('compute',dict(dataset_id=task['dataset_id'],operation=task['operation'],**task['parameters']))
            call=tool.calls[0];uncovered=[]
            for mapping in call.get('startup_fault_diagnostic',{}).get('mapped_files',[]):
                if mapping.get('unresolved') or (mapping['dev'],mapping['ino']) not in allowed:uncovered.append(mapping)
            return {'task':task['id'],'correct':'result' in answer and equal(answer['result'],refs[task['id']]['reference']),'call':call,'uncovered_final_mappings':uncovered}
        with ThreadPoolExecutor(max_workers=10) as pool:rows=list(pool.map(run,tasks))
        after=counters();summary.update(rows=rows,parent_delta={k:after[k]-before[k] for k in before},host_before=host_before,host_after=host_sample(),residency=[g.verify() for g in guards])
        if not all(r['correct'] for r in rows):raise ValueError('diagnostic numerical reference failed')
        summary['status']='completed_diagnostic_not_natural_acceptance'
    except BaseException as exc:summary['error']=f'{type(exc).__name__}: {exc}';raise
    finally:
        write(args.output/'summary.json',summary)
        for g in reversed(guards):g.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,required=True);p.add_argument('--preparation',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--probe',action='store_true');p.add_argument('--extra',type=Path);a=p.parse_args();plan=read(a.plan)
    if a.probe:return probe(a,plan)
    a.output.mkdir(parents=True,exist_ok=False);write(a.output/'frozen-plan.json',plan)
    check_topology(plan['pressure_controls']);snap=host_sample();write(a.output/'resources.json',snap)
    if snap['meminfo_kib']['MemAvailable']<220*1024**2:raise ValueError('memory admission failed')
    runtime=read(a.preparation/'runtime-copy.json')
    env={**os.environ,'PYTHONHOME':runtime['dst'],'PYTHONPATH':runtime['dst']+'/lib/python3.13/site-packages:'+str(ROOT/'.artifacts/prediction-holdout-deps')+':'+str(ROOT/'src'),
        'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false'}
    base=[runtime['interpreter'],str(Path(__file__).resolve()),'--probe','--plan',str(a.plan),'--preparation',str(a.preparation)]
    outcomes=[]
    try:
        with (a.output/'traced.log').open('w') as log:
            r=subprocess.run(['strace','-ff','-ttt','-yy','-s','256','-e','trace=%file,read,pread64,mmap,execve,wait4','-o',str(a.output/'startup.strace'),*base,'--output',str(a.output/'traced')],env=env,stdout=log,stderr=log,timeout=400)
        outcomes.append({'mode':'traced','returncode':r.returncode})
        if r.returncode:raise ValueError('traced diagnostic failed; stop without replacement')
        from leasepath.resident_trace_paths import trace_paths
        accessed=set()
        for f in a.output.glob('startup.strace.*'):accessed.update(trace_paths(f.read_text(errors='replace').splitlines()))
        closure=[{'path':str(x),'bytes':x.stat().st_size,'sha256':file_digest(x)} for x in sorted(accessed) if x.is_file() and not any(x.is_relative_to(v) for v in ('/proc','/sys','/dev'))]
        extra=a.output/'traced-file-closure.json';write(extra,closure)
        with (a.output/'untraced.log').open('w') as log:
            r=subprocess.run([*base,'--extra',str(extra),'--output',str(a.output/'untraced')],env=env,stdout=log,stderr=log,timeout=400)
        outcomes.append({'mode':'untraced','returncode':r.returncode})
        if r.returncode:raise ValueError('untraced diagnostic failed; no replacement')
    finally:write(a.output/'execution.json',{'model_generations':0,'outcomes':outcomes,'natural_acceptance':False,'planned_tool_calls_max':20,'replacement_batches':0})

if __name__=='__main__':main()
