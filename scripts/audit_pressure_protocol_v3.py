"""Read-only pressure evidence audit. No acceptance without native/trace evidence."""
import argparse
import json
import math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.frozen_distribution_baseline import FrozenEqualCostBaseline,file_digest
from leasepath.predictive_cache import PredictiveCache
from leasepath.qwen_data_tasks import equal
from leasepath.real_observer import validate_stream
from leasepath.resident_trace_paths import trace_paths
from check_qwen_cache_stage2 import check_native,require
from check_qwen_cache_matrix import check_actions
from audit_pressure_live_state import audit_live_state
from audit_pressure_rejection_v1 import check_rejection,check_raw_workers,check_rejection_native_output


def read(p):return json.loads(Path(p).read_text())
def lines(p):return [json.loads(x) for x in Path(p).read_text().splitlines()]


def audit(directory,trace_prefix=None):
    plan=read(directory/'frozen-plan.json');manifest=read(directory/'run-manifest.json')
    require(manifest['status']=='completed','run incomplete; preserve failure separately, no acceptance')
    c,n=plan['capacity_blocks'],plan['concurrency'];diagnostic=manifest['diagnostic_trace']
    baseline=FrozenEqualCostBaseline(ROOT,ROOT/plan['baseline_manifest'])
    require(manifest['baseline']==baseline.evidence(),'baseline identity differs')
    for file,digest in {**manifest['source_sha256'],**manifest['runtime_libraries_sha256'],**manifest['post_load_libraries_sha256']}.items():
        require(file_digest(file)==digest,'source/library differs: '+file)
    require(read(directory/'model-keeper.json')['residency']['nonresident_pages']==0,'model not resident')
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained('/home/liuxv/local-models/qwen25-coder-32b-leasepath/hf',local_files_only=True)
    predictor=baseline.predictor(manifest['runtime_compatibility'])
    refs={};tasks={};rejections=[];worker_calls=[]
    for name in ('qwen-data-tasks-v5','qwen-data-tasks-extension38-v2'):
        refs.update(read(ROOT/'.artifacts'/name/'references.private.json'))
        tasks.update({t['id']:t for t in read(ROOT/'.artifacts'/name/'tasks.private.json')})
    reservations={};seen=set()
    for ledger in directory.rglob('*ledger.jsonl'):
        seal=read(ledger.with_suffix('.sealed.json'));require(seal['sealed'] and seal['sha256']==file_digest(ledger),'ledger unsealed/changed')
        entries=lines(ledger);limit=entries[0]['budget_limits'] if entries else {}
        require(entries and len(entries)<=limit['total'],'empty or over-budget ledger')
        for lane in ('probe','pilot'):require(sum(e['lane']==lane for e in entries)<=limit[lane],'lane quota exceeded')
        for e in entries:
            require(e['id'] not in reservations,'duplicate reservation');reservations[e['id']]=e
    probes=0
    for file in sorted((directory/'native-gate').glob('*.json')):
        if file.name=='summary.json':continue
        r=read(file);rid=r['reservation_id'];require(rid in reservations and reservations[rid]['lane']=='probe','unreserved probe')
        require(tokenizer.encode(r['prompt'],add_special_tokens=False)==r['tokens'],'probe tokenization')
        check_native(r,c,128,2048);seen.add(rid);probes+=1
    expected=[(i,arm) for i,order in enumerate([['gbdt_distribution']] if diagnostic else plan['matrix']['orders']) for arm in order]
    progress=read(directory/'progress.json')['results'];require(len(progress)==len(expected),'missing batches')
    metrics=[];task_count=consumed=0
    for result,(cycle,arm) in zip(progress,expected):
        require((result['repeat'],result['policy'])==(cycle,arm),'crossover order differs')
        batch=directory/f'cycle-{cycle}-{arm}'/'batch';inputs={};pending={};artifacts={}
        for source in ('controller','backend'):validate_stream(batch/'raw'/f'{source}.jsonl')
        controller=lines(batch/'raw/controller.jsonl');backend=lines(batch/'raw/backend.jsonl')
        for e in controller:
            r=e['data']
            if e['kind']=='tool_end':
                require(r['owner'] not in pending,'unconsumed overlapping tool feedback')
                pending[r['owner']]=json.dumps(r['result'],separators=(',',':'),allow_nan=False)
                if r['tool_name']=='compute' and 'result' in r['result']:artifacts[r['owner'],r['result']['artifact_id']]=r['result']['result']
            elif e['kind']=='request_input':
                require(r['request_id'] not in inputs,'duplicate input');inputs[r['request_id']]=r
                require(tokenizer.encode(r['prompt'],add_special_tokens=False)==r['token_ids'],'tokenizer differs')
                if r['owner'] in pending:require(pending.pop(r['owner']) in r['prompt'],'feedback missing');consumed+=1
        require(not pending,'unconsumed terminal feedback')
        replica=PredictiveCache(c,block_size=128,block_bytes=33554432);actual=[];scores=[]
        events=[e for e in controller if e['kind']=='context_retire']+[e for e in backend if e['kind']=='engine_request']
        for e in sorted(events,key=lambda e:e['ts_ns']):
            r=e['data'];now=e['ts_ns']/1e9
            if e['kind']=='context_retire':replica.retire(r['owner'],now);continue
            rid=r['request_id'][1:];require(rid in reservations and rid not in seen,'missing/duplicate reservation')
            reservation=reservations[rid]
            require(all(reservation[k]==v for k,v in {'capacity':c,'concurrency':n,'repeat':cycle,'policy':arm,'lane':'pilot'}.items()),'reservation scope differs')
            require(r['tokens']==inputs[r['request_id']]['token_ids'] and r['owner']==inputs[r['request_id']]['owner'],'native/client disagreement')
            check_native(r,c,128,2048)
            scores.append(audit_live_state(replica,r,manifest['runtime_compatibility'],predictor,None,now))
            check_actions(replica,r,manifest['runtime_compatibility'],128,now);seen.add(rid);actual.append(r)
        require(len(actual)==len(inputs)==result['generations'],'request counts differ')
        require(sum(r['hit'] for r in actual)==result['cached_tokens'],'reuse tokens differ')
        require(sum(r['prefill'] for r in actual)==result['executed_prefill_tokens'],'prefill tokens differ')
        task_names=[];jcts=[]
        for file in result['quality_records']:
            r=read(batch/file);task_names.append(r['task']);task_count+=1
            require(r['success'],'failed task cannot accept complete cell')
            require(equal(r['final']['answer'],refs[r['task']]['reference']),'final/reference differs')
            require(equal(artifacts[r['owner'],r['final']['artifact_id']],refs[r['task']]['reference']),'tool artifact/reference differs')
            require(math.isclose(r['jct_to_validated_answer_s'],r['validated_monotonic_s']-r['submit_monotonic_s'],abs_tol=1e-9),'JCT origin differs')
            jcts.append(r['jct_to_validated_answer_s'])
            stored=lines(batch/'sessions'/r['owner']/'artifacts/calls.jsonl')
            require(len(stored)==len(r['tools']),'tool record count differs')
            for call,saved in zip(r['tools'],stored):
                require(all(call[k]==v for k,v in saved.items()),'saved tool evidence differs')
                if call['name']=='compute':
                    if call.get('status')=='ok':
                        check_worker(call,manifest,plan,r['task']);worker_calls.append(call)
                    else:
                        proof=check_rejection(call,tasks[r['task']])
                        proof['native_output_matches']=check_rejection_native_output(call,r['owner'],actual,tokenizer)
                        rejections.append(proof)
        require(set(task_names)==set(plan['live_tasks']) and len(task_names)==n,'task membership differs')
        host=read(batch.parent/'host.json')
        require(not host['parent_io_delta']['read_bytes'] and not host['parent_major_faults'] and not host['parent_block_input_ops'],'parent I/O/fault failure')
        require(all(r['nonresident_pages']==0 for r in host['residency'].values()),'residency missing')
        metrics.append({'cycle':cycle,'policy':arm,'mean_jct_s':sum(jcts)/n,'cached_tokens':sum(r['hit'] for r in actual),
            'prefill_tokens':sum(r['prefill'] for r in actual),'prefill_s':sum(r['prefill_ns'] for r in actual)/1e9,'decode_s':sum(r['decode_ns'] for r in actual)/1e9,'score_audits':scores})
    sentinels=[directory/'after-load-tool-sentinel']+[directory/f'sentinel-{i}' for i in range(1 if diagnostic else 6)]
    for d in sentinels:
        group=read(d/'summary.json');require(len(group['rows'])==n,'missing sentinel tools')
        for r in group['rows']:
            require(r['correct'] and equal(r['call']['result']['result'],refs[r['task']]['reference']),'sentinel reference differs');check_worker(r['call'],manifest,plan,r['task']);worker_calls.append(r['call'])
    require(seen==set(reservations),'unmapped reservations')
    require(probes==(13 if diagnostic else 0),'native gate count differs')
    closure=[];traces=[];worker_trace_proof=None
    if diagnostic:
        require(n==10 and manifest.get('tracer_pid',0)>0 and trace_prefix is not None,'full 10-task ptrace evidence required')
        traces=sorted(trace_prefix.parent.glob(trace_prefix.name+'.*'));require(traces,'no actual trace files')
        worker_trace_proof=check_raw_workers(traces,worker_calls)
        accessed=set()
        for f in traces:accessed.update(trace_paths(f.read_text(errors='replace').splitlines()))
        model=Path(manifest['model_copy']['dst']).resolve()
        for p in sorted(accessed):
            if not p.is_file() or p.resolve()==model or any(p.is_relative_to(x) for x in ('/dev/shm','/proc','/sys','/dev')):continue
            closure.append({'path':str(p),'sha256':file_digest(p),'bytes':p.stat().st_size})
    return {'status':'passed','capacity':c,'concurrency':n,'diagnostic':diagnostic,'new_generations':len(reservations),'tasks_checked':task_count,
        'feedback_consumed':consumed,'prelaunch_schema_rejections':rejections,'worker_trace_proof':worker_trace_proof,'actual_compute_calls_checked':len(worker_calls),'source_sha256':manifest['source_sha256'],
        'config_controls':{k:plan[k] for k in ('model_cpus','tool_controls','baseline_manifest','model_sha256')},
        'traces_sha256':{str(f):file_digest(f) for f in traces},'file_closure':closure,'metrics':metrics,
        'interpretation':'diagnostic only; no JCT benefit claim' if diagnostic else 'shared-host paired cell; no exclusivity claim'}


def check_worker(call,manifest,plan,task):
    from preflight_pressure_preexec_v2 import verify_inheritance
    verify_inheritance(call,plan['tool_controls'][task])
    require(call['status']=='ok','worker failure');u=call['child_lifetime'];io=call['file_diagnostics']['io_since_exec'];o=call['worker_timing']['overall']
    require(not(u['major_faults'] or u['block_input_ops'] or io['read_bytes'] or o['major_faults'] or o['read_bytes']),'worker lifetime gate failure')
    require(call['file_diagnostics']['prefix']==manifest['private_prefix'],'wrong runtime')
    require(call['worker_controls']['after']['cpus']==plan['tool_controls'][task]['cpus'],'wrong tool CPUs')
    require(call['worker_controls']['after']['thp_enabled']=='0','THP enabled')
    require(all(p['num_threads']==1 for p in call['thread_diagnostics']['pools_after']),'numerical helpers enabled')


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--trace-prefix',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=audit(a.run,a.trace_prefix);a.output.write_text(json.dumps(result,indent=2)+'\n')

if __name__=='__main__':main()
