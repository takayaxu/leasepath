"""Read-only diagnostic audit; do not turn a quiet diagnostic into a JCT pass."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.frozen_distribution_baseline import file_digest
from leasepath.qwen_data_tasks import equal


def read(path):return json.loads(Path(path).read_text())


def classify(call):
    d=call['startup_fault_diagnostic'];bounds=d['boundaries']
    if not bounds or bounds[0]['label']!='first_after_resource_import':raise ValueError('missing earliest boundary')
    majors=[b['major_faults'] for b in bounds]
    if any(b<a for a,b in zip(majors,majors[1:])):raise ValueError('counter reversed')
    intervals=[]
    for before,after in zip(bounds,bounds[1:]):
        delta=after['major_faults']-before['major_faults']
        if delta:intervals.append({'from':before['label'],'to':after['label'],'major_faults':delta})
    lifetime=call['child_lifetime']['major_faults']
    if lifetime<majors[-1]:raise ValueError('wait4 less than worker self')
    return {'first_cumulative_major_faults':majors[0],'last_observed_self_major_faults':majors[-1],
        'lifetime_major_faults':lifetime,'post_last_snapshot_or_descendant_major_faults':lifetime-majors[-1],
        'observed_intervals':intervals,'numerical_window_major_faults':call['worker_timing']['overall']['major_faults'],
        'import_fault_windows':[r for r in d['imports'] if any(r['delta'].values())],
        'interpretation':'first counter includes all pre-entry startup; intervals can overlap import windows; no unique faulting file/address'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    plan=read(a.run/'frozen-plan.json');execution=read(a.run/'execution.json')
    if execution['outcomes']!=[{'mode':'traced','returncode':0},{'mode':'untraced','returncode':0}]:raise ValueError('diagnostic phases incomplete')
    for path,digest in plan['source_sha256'].items():
        if file_digest(path)!=digest:raise ValueError('frozen source changed: '+path)
    refs={}
    for name in ('qwen-data-tasks-v5','qwen-data-tasks-extension38-v2'):refs.update(read(ROOT/'.artifacts'/name/'references.private.json'))
    rows=[];evidence={};parents=[]
    for mode in ('traced','untraced'):
        f=a.run/mode/'summary.json';summary=read(f);evidence[str(f)]=file_digest(f)
        if summary['status']!='completed_diagnostic_not_natural_acceptance':raise ValueError('unfinished diagnostic')
        if [r['task'] for r in summary['rows']]!=plan['pressure_controls']['live_tasks']:raise ValueError('wrong task/order')
        if any(r['nonresident_pages'] for r in summary['residency']):raise ValueError('nonresident locked file')
        locked=read(a.run/mode/'locked-files.json')
        pinned={str(Path(r['path']).resolve()) for r in locked}
        parents.append({'mode':mode,'delta':summary['parent_delta']})
        for r in summary['rows']:
            call=r['call'];task=r['task']
            if not r['correct'] or call['status']!='ok' or not equal(call['result']['result'],refs[task]['reference']):raise ValueError('wrong reference')
            saved=[json.loads(x) for x in (a.run/mode/task/'calls.jsonl').read_text().splitlines()]
            if len(saved)!=1 or any(call[k]!=v for k,v in saved[0].items()):raise ValueError('call evidence differs')
            controls=plan['pressure_controls']['tool_controls'][task]
            if call['worker_controls']['after']['cpus']!=controls['cpus'] or call['worker_controls']['after']['node_mask']!=1<<controls['numa_node']:raise ValueError('CPU/NUMA differs')
            if call['worker_controls']['after']['thp_enabled']!='0':raise ValueError('THP differs')
            if any(x['num_threads']!=1 for x in call['thread_diagnostics']['pools_after']):raise ValueError('numerical threads differ')
            opened=set()
            for x in call['startup_fault_diagnostic']['opened']:
                path=Path(x['path'])
                if path.is_file() and not any(path.is_relative_to(v) for v in ('/proc','/sys','/dev')):opened.add(str(path.resolve()))
            rows.append({'mode':mode,'task':task,'correct':True,**classify(call),
                'read_bytes':call['file_diagnostics']['io_since_exec']['read_bytes'],'block_input_ops':call['child_lifetime']['block_input_ops'],
                'uncovered_final_mappings':r['uncovered_final_mappings'],'uncovered_opened_disk_paths':sorted(opened-pinned),
                'boundary_count':len(call['startup_fault_diagnostic']['boundaries']),'import_windows':len(call['startup_fault_diagnostic']['imports'])})
    new_faults=sum(r['lifetime_major_faults'] for r in rows)
    out={'audit_status':'passed','diagnostic_status':'fault_observed' if new_faults else 'fault_not_reproduced_in_bounded_diagnostics',
        'real_tool_calls':len(rows),'correct':len(rows),'model_generations':0,'replacement_batches':0,'natural_JCT_gate_passed':False,
        'previous_failed_preflight_remains_failed':True,'total_lifecycle_major_faults':new_faults,'rows':rows,'parents':parents,'evidence_sha256':evidence,
        'historical_attribution_correction':'The previous rank_sum_test fault was outside numerical timing; old evidence does NOT distinguish bootstrap from post-compute. Earlier before-timer wording is not justified.',
        'unique_faulting_file_identified':False,'rules':'No-resource-fault diagnostic does not repair a historical failure or replace natural-resource acceptance. Ptrace and Python profiling overhead never used as JCT.'}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps({k:out[k] for k in ('audit_status','diagnostic_status','real_tool_calls','correct','total_lifecycle_major_faults','natural_JCT_gate_passed')}))

if __name__=='__main__':main()
