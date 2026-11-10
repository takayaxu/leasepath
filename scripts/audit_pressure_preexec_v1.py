"""Independent preexec qualification audit, including raw syscall order."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.frozen_distribution_baseline import file_digest
from leasepath.qwen_data_tasks import equal


def read(p):return json.loads(Path(p).read_text())


def audit(root):
    plan=read(root/'frozen-config.json');summary=read(root/'summary.json');refs={}
    for name in ('qwen-data-tasks-v5','qwen-data-tasks-extension38-v2'):refs.update(read(ROOT/'.artifacts'/name/'references.private.json'))
    for p,d in plan['source_sha256'].items():
        if file_digest(p)!=d:raise ValueError('qualification source differs: '+p)
    rows=[];evidence={};violations=[]
    for mode,counts in [('trace',[10]),('qualify',[3,6,10])]:
        s=read(root/mode/'summary.json')
        for n in counts:
            p=root/mode/f'concurrency-{n}/summary.json'
            if not p.exists():break
            g=read(p);evidence[str(p)]=file_digest(p)
            if [r['task'] for r in g['rows']]!=plan['live_tasks'][:n]:raise ValueError('task order differs')
            if g!=next(r for r in s['groups'] if r['concurrency']==n):raise ValueError('group evidence differs')
            if any(g['parent_delta'].values()) and mode=='qualify':violations.append({'parent':True,'concurrency':n,'delta':g['parent_delta']})
            if any(r['nonresident_pages'] for r in g['residency']):raise ValueError('residency changed')
            for r in g['rows']:
                t=r['task'];c=r['call'];control=plan['tool_controls'][t]
                if not r['correct'] or not equal(c['result'].get('result'),refs[t]['reference']):raise ValueError('reference failure')
                d=c['preexec_controls'];launch=d['launcher'];entry=d['python_entry'];expected={'cpus':sorted(control['cpus']),'node_mask':1<<control['numa_node'],'memory_policy':2,'thp_disabled':True,'memory_limit_bytes':12*1024**3,'cpu_limit_s':240}
                if any(entry[k]!=v or launch[k]!=v for k,v in expected.items()):raise ValueError('resource inheritance differs')
                if entry['pid']!=launch['pid'] or entry['pid']!=c['child_pid']:raise ValueError('process identity differs')
                if not d['verified_before_legacy_worker_import']:raise ValueError('entry evidence missing')
                if c['worker_controls']['after']['cpus']!=expected['cpus'] or c['worker_controls']['after']['node_mask']!=expected['node_mask'] or c['worker_controls']['after']['thp_enabled']!='0':raise ValueError('post-compute controls differ')
                if any(p['num_threads']!=1 for p in c['thread_diagnostics']['pools_after']):raise ValueError('numeric helper threads')
                record={'mode':mode,'concurrency':n,'task':t,'correct':True,'lifetime_major_faults':c['child_lifetime']['major_faults'],'lifetime_block_input_ops':c['child_lifetime']['block_input_ops'],'read_bytes':c['file_diagnostics']['io_since_exec']['read_bytes'],'preexec_verified':True}
                if mode=='trace':
                    path=root/f'preexec.strace.{c["child_pid"]}';lines=path.read_text(errors='replace').splitlines();evidence[str(path)]=file_digest(path)
                    python=[i for i,line in enumerate(lines) if 'execve(' in line and '/bin/python3.13' in line and '= 0' in line]
                    if len(python)!=1:raise ValueError('expected one Python exec syscall')
                    before=lines[:python[0]]
                    for token in ('sched_setaffinity(','set_mempolicy(MPOL_BIND','PR_SET_THP_DISABLE','RLIMIT_AS','RLIMIT_CPU'):
                        if not any(token in l and '= 0' in l for l in before):raise ValueError('missing successful pre-exec syscall: '+token)
                    record['raw_syscall_order_verified']=True
                if mode=='qualify' and any(record[k] for k in ('lifetime_major_faults','lifetime_block_input_ops','read_bytes')):violations.append(record)
                rows.append(record)
    passed=summary['status']=='passed' and len(rows)==29 and not violations
    if summary['status']=='passed' and not passed:raise ValueError('false qualification pass')
    return {'audit_status':'passed','resource_qualification_passed':passed,'natural_launch_allowed':passed,'full_native_controller_passed':False,'natural_JCT_passed':False,
        'real_tool_calls':len(rows),'model_generations':0,'violations':violations,'rows':rows,'evidence_sha256':evidence,
        'historical_fault_cause_resolved':False,'resource_recipe':plan['resource_recipe'],'historical_failed_sample_replaced':False}


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();d=audit(a.run);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(d,indent=2)+'\n');print(json.dumps({k:v for k,v in d.items() if k not in ('rows','evidence_sha256')}))

if __name__=='__main__':main()
