"""Preregistered new resource recipe: trace10, then strict qualification3/6/10 once."""
import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.frozen_distribution_baseline import file_digest,FrozenEqualCostBaseline
from leasepath.controlled_resources import host_sample
from leasepath.preexec_pressure_v1 import check_topology,run_tool_group
from leasepath.resident_runtime import ResidentFiles,ResidentRuntime
from leasepath.resident_trace_paths import trace_paths


def read(p):return json.loads(Path(p).read_text())
def write(p,d):p.write_text(json.dumps(d,indent=2,allow_nan=False)+'\n')


def tasks_refs(plan):
    ts={};refs={}
    for name in ('qwen-data-tasks-v5','qwen-data-tasks-extension38-v2'):
        d=ROOT/'.artifacts'/name;ts.update({t['id']:t for t in read(d/'tasks.private.json')});refs.update(read(d/'references.private.json'))
    return [ts[x] for x in plan['live_tasks']],refs


def counts():
    u=resource.getrusage(resource.RUSAGE_SELF)
    with open('/proc/self/io') as f:d={k:int(v) for k,v in (line.split(':') for line in f)}
    return {'major_faults':u.ru_majflt,'block_input_ops':u.ru_inblock,'read_bytes':d['read_bytes']}


def resource_violation(call):
    if call.get('status')!='ok':return True
    u=call.get('child_lifetime',{});io=call.get('file_diagnostics',{}).get('io_since_exec',{});o=call.get('worker_timing',{}).get('overall',{})
    return bool(u.get('major_faults',-1) or u.get('block_input_ops',-1) or io.get('read_bytes',-1) or o.get('major_faults',-1) or o.get('read_bytes',-1))


def verify_inheritance(call,control):
    d=call['preexec_controls'];launch=d['launcher'];entry=d['python_entry']
    wanted={'cpus':sorted(control['cpus']),'memory_policy':2,'node_mask':1<<control['numa_node'],'thp_disabled':True,'memory_limit_bytes':12*1024**3,'cpu_limit_s':240}
    if any(launch[k]!=v or entry[k]!=v for k,v in wanted.items()):raise ValueError('preexec control differs')
    if entry['pid']!=call['child_pid'] or launch['pid']!=entry['pid']:raise ValueError('exec changed process identity')
    if not d['verified_before_legacy_worker_import']:raise ValueError('missing entry verification')
    if any(p['num_threads']!=1 for p in call['thread_diagnostics']['pools_after']):raise ValueError('multithread numeric pool')
    if call['worker_controls']['after']['cpus']!=wanted['cpus'] or call['worker_controls']['after']['node_mask']!=wanted['node_mask'] or call['worker_controls']['after']['thp_enabled']!='0':raise ValueError('postcompute resource controls differ')


def probe(a,plan):
    a.output.mkdir(parents=True,exist_ok=False);runtime=read(a.preparation/'runtime-copy.json');system=read(a.preparation/'system-residency.json')
    if sys.prefix!=runtime['dst']:raise ValueError('private runtime required')
    if file_digest(a.preparation/'runtime-copy.json')!=plan['runtime_manifest_sha256']:raise ValueError('runtime manifest differs')
    paths=[]
    for p,d in plan['source_sha256'].items():
        if file_digest(p)!=d:raise ValueError('source changed: '+p)
        paths.append(Path(p))
    for r in system['files']:
        if file_digest(r['path'])!=r['sha256']:raise ValueError('system file changed')
        paths.append(Path(r['path']))
    if a.extra:
        for r in read(a.extra):
            if file_digest(r['path'])!=r['sha256']:raise ValueError('trace file differs')
            paths.append(Path(r['path']))
    tasks,refs=tasks_refs(plan)
    for task in tasks:
        task['file']=str(a.preparation.parent/'tool-preflight-v2/inputs'/(task['id']+'.npz'))
        if file_digest(task['file'])!=task['input_sha256']:raise ValueError('input differs')
        paths.append(Path(task['file']))
    guards=[];summary={'status':'failed','model_generations':0,'groups':[],'mode':a.mode,'recipe':plan['resource_recipe']}
    try:
        guards=[ResidentRuntime(runtime),ResidentFiles(paths)]
        records=[r for g in guards for r in g.records];write(a.output/'locked-files.json',records)
        allowed={(Path(r['path']).stat().st_dev,Path(r['path']).stat().st_ino) for r in records}
        for n in ([10] if a.mode=='trace' else [3,6,10]):
            before=counts();host_before=host_sample()
            group=run_tool_group(tasks[:n],refs,plan,a.output/f'concurrency-{n}')
            after=counts();group.update(parent_delta={k:after[k]-before[k] for k in before},host_before=host_before,host_after=host_sample(),residency=[g.verify() for g in guards])
            summary['groups'].append(group);write(a.output/f'concurrency-{n}/summary.json',group);write(a.output/'summary.json',summary)
            for r in group['rows']:
                call=r['call']
                if not r['correct']:raise ValueError('reference failure')
                verify_inheritance(call,plan['tool_controls'][r['task']])
                for line in call['file_diagnostics']['maps'].splitlines():
                    parts=line.split(None,5)
                    if len(parts)<6 or not parts[5].startswith('/'):continue
                    st=Path(parts[5]).stat()
                    if (st.st_dev,st.st_ino) not in allowed:raise ValueError('unlocked worker mapping: '+parts[5])
            # Discovery trace retained independently; qualifying mode enforces every sample.
            if a.mode=='qualify' and (any(group['parent_delta'].values()) or any(resource_violation(r['call']) for r in group['rows'])):
                raise ValueError('strict lifetime fault/disk gate failed; no replacements')
        summary['status']='passed'
    except BaseException as exc:summary['error']=f'{type(exc).__name__}: {exc}';raise
    finally:
        write(a.output/'summary.json',summary)
        for g in reversed(guards):g.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,required=True);p.add_argument('--preparation',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--mode',choices=['trace','qualify']);p.add_argument('--extra',type=Path);a=p.parse_args();plan=read(a.plan)
    if a.mode:return probe(a,plan)
    a.output.mkdir(parents=True,exist_ok=False);write(a.output/'frozen-config.json',plan)
    snapshot=host_sample();write(a.output/'resources.json',snapshot);check_topology(plan)
    if snapshot['meminfo_kib']['MemAvailable']<220*1024**2:raise ValueError('memory admission')
    FrozenEqualCostBaseline(ROOT,ROOT/plan['baseline_manifest']).verify()
    runtime=read(a.preparation/'runtime-copy.json')
    env={**os.environ,'PYTHONHOME':runtime['dst'],'PYTHONPATH':runtime['dst']+'/lib/python3.13/site-packages:'+str(ROOT/'.artifacts/prediction-holdout-deps')+':'+str(ROOT/'src'),'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false'}
    base=[runtime['interpreter'],str(Path(__file__).resolve()),'--plan',str(a.plan),'--preparation',str(a.preparation)]
    outcomes=[];summary={'status':'failed','model_generations':0,'replacement_batches':0,'resource_recipe':plan['resource_recipe'],'outcomes':outcomes}
    try:
        with (a.output/'trace.log').open('w') as log:
            r=subprocess.run(['strace','-ff','-ttt','-yy','-s','256','-e','trace=%file,read,pread64,mmap,execve,wait4,set_mempolicy,get_mempolicy,prctl,sched_setaffinity,prlimit64','-o',str(a.output/'preexec.strace'),*base,'--mode','trace','--output',str(a.output/'trace')],env=env,stdout=log,stderr=log,timeout=420)
        outcomes.append({'mode':'trace','returncode':r.returncode})
        if r.returncode:raise ValueError('trace stage failed')
        accessed=set()
        for f in a.output.glob('preexec.strace.*'):accessed.update(trace_paths(f.read_text(errors='replace').splitlines()))
        closure=[{'path':str(p),'bytes':p.stat().st_size,'sha256':file_digest(p)} for p in sorted(accessed) if p.is_file() and not any(p.is_relative_to(v) for v in ('/dev','/proc','/sys'))]
        extra=a.output/'file-closure.json';write(extra,closure)
        with (a.output/'qualify.log').open('w') as log:
            r=subprocess.run([*base,'--mode','qualify','--extra',str(extra),'--output',str(a.output/'qualify')],env=env,stdout=log,stderr=log,timeout=900)
        outcomes.append({'mode':'qualify','returncode':r.returncode})
        if r.returncode:raise ValueError('qualification stopped; no replacement')
        summary.update(status='passed',concurrencies=[3,6,10],new_resource_qualification=True,full_native_controller_passed=False,natural_JCT_passed=False)
    except BaseException as exc:summary['error']=f'{type(exc).__name__}: {exc}';raise
    finally:write(a.output/'summary.json',summary)

if __name__=='__main__':main()
