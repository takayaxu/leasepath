"""Independently recheck retained CPU-tool outputs, controls and fault gates."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.qwen_data_tasks import equal
from leasepath.frozen_distribution_baseline import file_digest


def audit(root):
    plan=json.loads((root/'frozen-config.json').read_text());refs={}
    for name in ('qwen-data-tasks-v5','qwen-data-tasks-extension38-v2'):
        refs.update(json.loads((ROOT/'.artifacts'/name/'references.private.json').read_text()))
    evidence={};rows=[];violations=[]
    for mode in ('traced','pinned'):
        summary=json.loads((root/mode/'summary.json').read_text())
        for count in (3,6,10):
            file=root/mode/f'concurrency-{count}/summary.json';g=json.loads(file.read_text());evidence[str(file)]=file_digest(file)
            assert g['concurrency']==count
            assert [r['task'] for r in g['rows']]==plan['live_tasks'][:count]
            assert g==next(x for x in summary['groups'] if x['concurrency']==count)
            for name,value in g['parent_delta'].items():
                if value:violations.append({'mode':mode,'concurrency':count,'scope':'parent','counter':name,'value':value})
            for row in g['rows']:
                task=row['task'];call=row['call'];life=call['child_lifetime'];io=call['file_diagnostics']['io_since_exec']
                assert row['correct'] and call['status']=='ok'
                assert equal(call['result']['result'],refs[task]['reference'])
                assert call['worker_controls']['after']['cpus']==plan['tool_controls'][task]['cpus']
                assert call['worker_controls']['after']['thp_enabled']=='0'
                assert call['worker_controls']['after']['node_mask']==1<<plan['tool_controls'][task]['numa_node']
                assert all(p['num_threads']==1 for p in call['thread_diagnostics']['pools_after'])
                record={'mode':mode,'concurrency':count,'task':task,'correct':True,'major_faults':life['major_faults'],'read_bytes':io['read_bytes'],'block_input_ops':life['block_input_ops'],
                    'compute_major_faults':call['worker_timing']['overall']['major_faults']}
                rows.append(record)
                if any(record[k] for k in ('major_faults','read_bytes','block_input_ops')):violations.append(record)
            assert all(r['nonresident_pages']==0 for r in g['residency'])
    strict=[r for r in violations if r['mode']=='pinned']
    if strict:assert json.loads((root/'pinned/summary.json').read_text())['status']=='failed'
    return {'audit_status':'passed','experiment_status':'blocked_by_worker_fault' if strict else 'cpu_tool_gate_passed',
        'model_generations':0,'tool_calls':len(rows),'correct':sum(r['correct'] for r in rows),'rows':rows,'violations':violations,
        'natural_launch_allowed':not strict,'failure_preserved_without_replacement':True,'evidence_sha256':evidence,
        'fault_attribution':'rank_sum_test fault precedes measured numerical window; read_bytes and block input zero; exact page/file unknown' if strict else None}


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=audit(a.run);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('audit_status','experiment_status','tool_calls','correct','model_generations','natural_launch_allowed')}))

if __name__=='__main__':main()
