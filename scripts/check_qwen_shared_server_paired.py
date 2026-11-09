"""Independent native/protocol/quality/ledger audit and paired shared-host metrics."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import statistics
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.controlled_resources import psi_interval
from leasepath.prediction_training import read_json,read_jsonl,sha256
from leasepath.predictive_cache import PredictiveCache
from leasepath.qwen_data_tasks import equal
from leasepath.real_observer import validate_stream
from check_qwen_cache_matrix import check_actions
from check_qwen_cache_stage2 import check_native,require
from probe_qwen_cache_matrix import write


def mean(values):return statistics.mean(values) if values else None


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();manifest=read_json(args.run/'run-manifest.json');plan=read_json(args.run/'frozen-plan.json')
    require(manifest['status'] in ('completed','failed'),'run unfinished')
    for name,digest in {**manifest['source_sha256'],**manifest['runtime_libraries_sha256']}.items():
        require(sha256(name)==digest,'protected source/runtime differs: '+name)
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained('/home/liuxv/local-models/qwen25-coder-32b-leasepath/hf',local_files_only=True)
    refs={}
    for name in ('qwen-data-tasks-v5','qwen-data-tasks-extension38-v2'):
        refs.update(read_json(ROOT/'.artifacts'/name/'references.private.json'))
    by_id={};seen=set();phase_entries={}
    for phase in plan['phases']:
        directory=args.run/phase['name'];ledger=directory/'ledger.jsonl'
        entries=read_jsonl(ledger) if ledger.exists() else [];phase_entries[phase['name']]=entries
        require(len(entries)<=phase['budget_limits']['total'],'phase total cap exceeded')
        for lane in ('probe','pilot'):
            require(sum(r['lane']==lane for r in entries)<=phase['budget_limits'][lane],'lane cap exceeded')
        require(read_json(ledger.with_suffix('.sealed.json'))['sha256']==(sha256(ledger) if ledger.exists() else None),'sealed ledger differs')
        for row in entries:
            require(row['id'] not in by_id,'duplicate reservation');by_id[row['id']]=row
    require(len(by_id)<=plan['total_generation_hard_cap'],'aggregate cap exceeded')
    probes=streams=consumed=tasks=correct=computes=rejections=0;failures=[];metrics=[];task_metrics=[]
    for path in sorted((args.run/'native-gate').glob('*.json')):
        if path.name=='summary.json':continue
        row=read_json(path);rid=row['reservation_id']
        require(rid in by_id and rid not in seen and by_id[rid]['lane']=='probe','probe reservation differs')
        require(tokenizer.encode(row['prompt'],add_special_tokens=False)==row['tokens'],'probe tokenization differs')
        check_native(row,24,128,2048);seen.add(rid);probes+=1
    for phase in plan['phases']:
        directory=args.run/phase['name'];progress=directory/'progress.json'
        if not progress.exists():continue
        batches=read_json(progress)['results']
        for ordinal,batch in enumerate(batches):
            repeat,name=batch['repeat'],batch['policy']
            require(name==phase['orders'][ordinal//3][ordinal%3] and repeat==ordinal//3,'batch order differs')
            path=directory/f'concurrency-3-repeat-{repeat}-{name}'
            events=[];inputs={};pending={};artifacts={}
            for source in ('controller','backend'):
                stream=path/'raw'/f'{source}.jsonl';validate_stream(stream);streams+=1
                for event in read_jsonl(stream):
                    row=event['data']
                    if event['kind'] in ('context_retire','engine_request'):events.append(event)
                    if event['kind']=='request_input':
                        require(row['request_id'] not in inputs,'duplicate model input');inputs[row['request_id']]=row
                        require(tokenizer.encode(row['prompt'],add_special_tokens=False)==row['token_ids'],'live prompt tokenization differs')
                        if row['owner'] in pending:
                            require(pending.pop(row['owner']) in row['prompt'],'actual tool result not consumed');consumed+=1
                    if event['kind']=='tool_end':
                        pending[row['owner']]=json.dumps(row['result'],allow_nan=False,separators=(',',':'))
                        if row['tool_name']=='compute' and 'result' in row['result']:
                            artifacts[row['owner'],row['result']['artifact_id']]=row['result']['result']
            actual=[e['data'] for e in events if e['kind']=='engine_request']
            require(len(actual)==len(inputs)==batch['generations'],'live generation counts differ')
            require(sum(r['hit'] for r in actual)==batch['cached_tokens'],'hit total differs')
            replica=PredictiveCache(24,block_size=128)
            divergences=0
            for event in sorted(events,key=lambda e:e['ts_ns']):
                row=event['data'];now=event['ts_ns']/1e9
                if event['kind']=='context_retire':replica.retire(row['owner'],now);continue
                rid=row['request_id'][1:]
                require(rid in by_id and rid not in seen,'live reservation duplicate/missing')
                scope={'phase':phase['name'],'capacity':24,'concurrency':3,'repeat':repeat,'policy':name,'owner':row['owner'],'lane':'pilot'}
                require(all(by_id[rid][k]==v for k,v in scope.items()),'live scope differs');seen.add(rid)
                require(row['tokens']==inputs[row['request_id']]['token_ids'],'client/native tokens differ')
                check_native(row,24,128,2048)
                diag=check_actions(replica,row,manifest['runtime_compatibility'],128,now)
                divergences+=diag['victim_set_differs']
            names=set();tool_rows=[]
            for filename in batch['quality_records']:
                row=read_json(path/filename);names.add(row['task']);tasks+=1;correct+=row['success']
                require(math.isclose(row['jct_to_validated_answer_s'],row['validated_monotonic_s']-row['submit_monotonic_s'],abs_tol=1e-9),'JCT definition differs')
                final=row['final']
                if row['success']:
                    require(final and (row['owner'],final['artifact_id']) in artifacts,'successful final has unknown artifact')
                    require(equal(final['answer'],refs[row['task']]['reference']) and equal(artifacts[row['owner'],final['artifact_id']],refs[row['task']]['reference']),'final reference differs')
                    require(row['owner'] not in pending,'unconsumed tool result for successful task')
                else:failures.append({'phase':phase['name'],'repeat':repeat,'policy':name,'task':row['task'],'error':row['error']})
                saved=read_jsonl(path/'sessions'/row['owner']/'artifacts/calls.jsonl')
                require(len(saved)==len(row['tools']),'tool count differs')
                for call,stored in zip(row['tools'],saved):
                    for k,v in stored.items():
                        if k=='response_window':
                            require(all(call[k][key]==val for key,val in v.items()),'window saved evidence differs')
                        else:require(call[k]==v,'saved tool evidence differs')
                    require(call['ended_ns']>=call['started_ns'],'tool time reversed')
                    if call['status']!='ok':rejections+=1;continue
                    if phase['name']=='fixed_window':
                        w=call['response_window'];expected=plan['response_windows'][row['task']][call['name']]
                        require(w['budget_s']==expected and w['deadline_ns']==w['started_ns']+round(expected*1e9),'window differs from fixed plan')
                        if row['success']:
                            require(not w['compute_overrun'] and w['return_lateness_ns']<=plan['delivery_jitter_s']*1e9,'successful task hid window overrun')
                    if call['name']=='compute':
                        computes+=1;timing=call['worker_timing'];o=timing['overall']
                        require(call['child_wall_ns']>=call['worker_execute_ns']==o['wall_ns']>0,'worker timing differs')
                        require(o['read_bytes']==o['major_faults']==0,'numerical worker touched disk')
                        for state in call['worker_controls'].values():
                            expected=plan['tool_controls'][row['task']]
                            require(state['cpus']==expected['cpus'] and state['node_mask']==2 and state['memory_policy']==2 and state['thp_enabled']=='0','child resource setting differs')
                        require(all(p['num_threads']==1 for p in call['thread_diagnostics']['pools_after']),'threadpool count differs')
                        answer=call['result'];artifact=answer['artifact_id']
                        require(equal(answer['result'],read_json(path/'sessions'/row['owner']/'artifacts'/(artifact+'.json'))),'artifact storage differs')
                        tool_rows.append({'task':row['task'],'natural_tool_wall_s':(call['ended_ns']-call['started_ns'])/1e9,
                            'worker_kernel_s':o['system_ns']/1e9,'queue_fraction':o['all_thread_runqueue_wait_ns']/o['wall_ns'],
                            'window_added_wait_s':call.get('response_window',{}).get('added_wait_ns',0)/1e9})
                task_metrics.append({'phase':phase['name'],'repeat':repeat,'policy':name,'task':row['task'],
                    'success':row['success'],'jct_s':row['jct_to_validated_answer_s'],'generations':row['generations'],
                    'input_token_counts':[len(r['tokens']) for r in row['model_requests']],
                    'decode_tokens':[len(r['output_tokens']) for r in row['model_requests']],
                    'input_token_sha256':[__import__('hashlib').sha256(json.dumps(r['tokens']).encode()).hexdigest() for r in row['model_requests']],
                    'output_token_sha256':[__import__('hashlib').sha256(json.dumps(r['output_tokens']).encode()).hexdigest() for r in row['model_requests']]})
            require(names==set(plan['live_tasks']),'task subset differs')
            host=read_json(directory/f'host-repeat-{repeat}-{name}.json')
            metric={**{k:batch[k] for k in ('repeat','policy','status','mean_successful_jct_s','makespan_s','cached_tokens','executed_prefill_tokens','prefill_s','decode_s','prediction_s','generations')},
                'phase':phase['name'],'victim_set_divergences':divergences,'tools':tool_rows,
                'host_cpu_psi_pct':psi_interval(host['before'],host['after'],'cpu'),
                'host_io_psi_pct':psi_interval(host['before'],host['after'],'io'),
                'host_memory_full_psi_pct':psi_interval(host['before'],host['after'],'memory','full'),
                'parent_read_bytes':host['parent_io_delta']['read_bytes']}
            metrics.append(metric)
    require(seen==set(by_id),'unmapped generation reservations')
    if manifest['status']=='completed':
        require(not failures and probes==13,'completed experiment has failures or wrong gate count')
        require(len(metrics)==27 and tasks==81,'completed experiment missing planned batch/task')
    paired=[];aggregates=[]
    for phase in plan['phases']:
        valid=[r for r in metrics if r['phase']==phase['name']]
        for name in plan['policies']:
            selected=[r for r in valid if r['policy']==name]
            if selected:
                aggregates.append({'phase':phase['name'],'policy':name,'batches':len(selected),
                    **{k:mean([r[k] for r in selected]) for k in ('mean_successful_jct_s','makespan_s','cached_tokens','prefill_s','decode_s','prediction_s')},
                    'host_cpu_psi_pct':[r['host_cpu_psi_pct'] for r in selected],
                    'host_io_psi_pct':[r['host_io_psi_pct'] for r in selected],
                    'parent_read_bytes':sum(r['parent_read_bytes'] for r in selected)})
        for name in ('conditional','gbdt'):
            deltas=[];by_task=defaultdict(list);token_shapes=[];exact_workloads=[]
            for repeat in range(len(phase['orders'])):
                baseline=[r for r in task_metrics if r['phase']==phase['name'] and r['repeat']==repeat and r['policy']=='retired_first_lru']
                alternative=[r for r in task_metrics if r['phase']==phase['name'] and r['repeat']==repeat and r['policy']==name]
                if len(baseline)!=3 or len(alternative)!=3 or not all(r['success'] for r in baseline+alternative):continue
                index={r['task']:r for r in alternative}
                delta=mean([index[r['task']]['jct_s']-r['jct_s'] for r in baseline]);deltas.append(delta)
                for r in baseline:
                    by_task[r['task']].append(index[r['task']]['jct_s']-r['jct_s'])
                    token_shapes.append(r['input_token_counts']==index[r['task']]['input_token_counts'])
                    exact_workloads.append(all(r[k]==index[r['task']][k] for k in ('input_token_sha256','output_token_sha256')))
            paired.append({'phase':phase['name'],'policy':name,'complete_repeat_pairs':len(deltas),
                'mean_task_jct_delta_s':mean(deltas),'repeat_mean_jct_deltas_s':deltas,
                'per_task_mean_delta_s':{k:mean(v) for k,v in by_task.items()},
                'paired_input_shapes_equal':all(token_shapes),'paired_exact_token_workloads_equal':all(exact_workloads),'scope':phase['interpretation']})
    result={'status':'passed','experiment_status':manifest['status'],'requests_checked':len(seen),'probe_requests':probes,
        'live_requests':len(seen)-probes,'batches':len(metrics),'tasks':tasks,'correct':correct,'failed':tasks-correct,
        'sealed_streams':streams,'consumed_tool_results':consumed,'compute_calls':computes,'structured_rejections':rejections,
        'failures':failures,'aggregates':aggregates,'paired':paired,'batch_metrics':metrics,'task_metrics':task_metrics,
        'fixed_window_is_not_natural_jct':True,'prior_noise_gates_remain_failed':True,
        'natural_scope':'shared host counterbalanced observation; CPU, memory bandwidth and other tenants are not reserved',
        'source_sha256':manifest['source_sha256']}
    args.output.parent.mkdir(parents=True,exist_ok=True);write(args.output,result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('source_sha256','batch_metrics','task_metrics')}),flush=True)


if __name__=='__main__':main()
