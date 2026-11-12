"""V7 continuous pilot: explicit contracts, transactional JSON, public behavior checks.

LRU collection precedes target-data training and E2E policy comparison. Every
failure is retained. Neither independent holdout fixtures nor oracle feedback
are passed to the model. A round ending leaves its KV owner alive for the next
user message. This runner does not claim a timing benefit from one arm.
"""
import argparse
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.continuous_projects_v2 import projects,initialize,api_payloads
from leasepath.continuous_tools_v1 import SCHEMAS,compact
from leasepath.continuous_tools_v4 import ProjectTools
from leasepath.continuous_verify_v1 import verify
from leasepath.continuous_protocol_v2 import delivery
from leasepath.frozen_distribution_baseline import FrozenEqualCostBaseline,file_digest
from leasepath.preexec_pressure_v2 import PressureEngine,check_topology
from leasepath.pressure_data_agent import PressureObservedQwen
from leasepath.real_observer import Observer
from leasepath.controlled_resources import host_sample
from run_qwen_cache_controlled import ScopedBudget
from probe_qwen_cache_matrix import run_gate
from check_qwen_cache_stage2 import check_native

SYSTEM='''You are maintaining a real project across a continuing conversation.
Use the actual project tools and returned results. Choose the tools, arguments,
edits, checks and stopping yourself. Work on the current user request and
preserve earlier deliverables. Do not invent tool results. All history stays.
Use one function call at a time with the ✿FUNCTION✿ / ✿ARGS✿ protocol.
You may make at most 11 tool calls and 12 model responses per user turn.
Finish the current turn with a concise normal assistant reply summarizing your actual deliverable.
No further tool calls are needed once the requested deliverable is correct.
Each tools result is real; independent checking occurs only after delivery.
Keep outputs and explanations concise, and do not dump large input files.
run_python executes in the project directory: open and process the actual files
by relative path. Never copy file contents into Python string literals or echo
entire datasets. Modify cumulative artifacts programmatically when convenient.
Use run_checks on the requested target artifact before delivery; consume its
real error output and fix detected problems. It checks public schema, small
behavior examples and cross-field consistency, not private reference answers.
Persist actual query or calculation results, not empty/default placeholders.
Serialize JSON with json.dump/json.dumps; Python dict notation is not JSON.
Deliver promptly after the current requirement and checks are satisfied.'''


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n');temporary.replace(path)


def identity(path):
    stat=Path(path).stat()
    return [getattr(stat,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')]


def serve():
    payload=api_payloads()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            value=payload.get(self.path)
            self.send_response(200 if value is not None else 404);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(compact(value if value is not None else {'error':'unknown route'}).encode())
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server,'http://127.0.0.1:'+str(server.server_port)


def run_batch(engine,tokenizer,plan,work,save):
    controller=Observer(work/'raw/controller.jsonl',run_id=work.name,source='continuous_project_controller_v1')
    backend=Observer(work/'raw/backend.jsonl',run_id=work.name,source='multi_owner_native_cuda')
    states={};ready=deque();notifications=queue.Queue();records=[];server=None
    pool=ThreadPoolExecutor(max_workers=6)
    engine.clear();engine.history=[]
    begin=time.monotonic();server,url=serve()
    def enqueue(owner,at):
        ready.append((at,owner));controller.emit('request_ready',owner=owner,round=states[owner]['round'],actual_observed_monotonic_s=at)
    def tool_call(owner,tools,call):
        started=time.monotonic();notifications.put((started,'start',owner,{'tool_name':call['name']}))
        try:args=json.loads(call['arguments'])
        except (TypeError,ValueError):args={}
        result=tools.call(call['name'],args)
        notifications.put((time.monotonic(),'end',owner,{'tool_name':call['name'],'result':result}))
    def retire(owner,reason):
        state=states[owner]
        state['done']=True
        if owner in engine.cache.idle:engine.retire(owner)
        controller.emit('context_retire',owner=owner,reason=reason)
    def arrive(owner,at):
        state=states[owner];spec=state['project']['turns'][state['round']]
        state['tools'].round=state['round'];state['tools'].round_calls=0
        state['round_generations_before']=state['llm'].generation_attempts;state['round_started']=at
        state['llm'].task_deadline=at+1800
        message=f"Project {state['project']['id']}, user turn {state['round']+1}/8.\n"+spec['request']
        state['messages'].append({'role':'user','content':message})
        state['phase']='queued';state['next_user_at']=None
        controller.emit('user_arrival',owner=owner,round=state['round'],message=message,actual_observed_monotonic_s=at)
        enqueue(owner,at)
    def finish_round(owner,final=None,error=None):
        state=states[owner];validation_start=time.monotonic()
        acceptance={'success':False,'error':error}
        if error is None:
            if not isinstance(final,dict) or not final.get('assistant_text'):
                error='nonempty assistant delivery required';acceptance={'success':False,'error':error}
            elif state['llm'].pending_tool_results:
                error='unconsumed feedback at delivery';acceptance={'success':False,'error':error}
            else:
                acceptance=verify(state['project'],state['round'],state['workspace']);error=acceptance['error']
        ended=time.monotonic()
        snapshot=work/'artifacts'/owner/f'round-{state["round"]:02d}'
        shutil.copytree(state['workspace'],snapshot)
        record=dict(owner=owner,project=owner,source_family=state['project']['family'],round=state['round'],success=acceptance['success'],error=error,acceptance=acceptance,final=final,
            generations=state['llm'].generation_attempts-state['round_generations_before'],tool_calls=state['tools'].round_calls,
            submit_s=state['round_started'],terminal_s=ended,response_s=ended-state['round_started'],validation_s=ended-validation_start,
            artifact_path=str(snapshot),artifact_sha256={str(p.relative_to(snapshot)):file_digest(p) for p in snapshot.rglob('*') if p.is_file()})
        records.append(record);write(work/'rounds'/f'{owner}-{state["round"]:02d}.json',record)
        controller.emit('round_terminal',**{k:v for k,v in record.items() if k not in ('artifact_sha256','acceptance')})
        print('round',owner,state['round']+1,'success',acceptance['success'],'generations',record['generations'],'response_s',round(record['response_s'],2),flush=True)
        write(work/'messages'/f'{owner}.json',state['messages'])
        if error is not None:
            with (work/'failure_registry.jsonl').open('a') as stream:stream.write(compact(record)+'\n')
            retire(owner,'failed_round_preserved_pending_root_cause_analysis')
        elif state['round']==7:retire(owner,'project_completed')
        else:
            state['round']+=1;state['phase']='waiting_user'
            state['next_user_at']=ended+plan['user_think_s'][state['round']]
            controller.emit('user_wait',owner=owner,round=state['round'])
            timer=threading.Timer(plan['user_think_s'][state['round']],lambda:notifications.put((time.monotonic(),'user',owner,{})))
            timer.daemon=True;timer.start()
        save(records)
    try:
        for project in projects():
            owner=project['id'];workspace=initialize(project,work/'workspaces'/owner)
            tools=ProjectTools(workspace,work/'tools'/owner,project,plan['tool_controls'][owner]['cpus'],url if owner=='continuous_api' else None)
            budget=ScopedBudget(work/'ledgers'/f'{owner}.jsonl',limits={'probe':1,'pilot':96,'total':96})
            llm=PressureObservedQwen(engine,tokenizer,budget,controller,backend,lane='pilot',context=plan['context_tokens']);llm.owner=owner
            states[owner]=dict(project=project,workspace=workspace,tools=tools,budget=budget,llm=llm,messages=[{'role':'system','content':SYSTEM}],round=0,phase='queued',last_tool=None,tool_started=None,done=False,next_user_at=None)
            controller.emit('workflow_submit',owner=owner,source_group=project['source_group'])
            controller.emit('context_open',owner=owner,compatibility_id=engine.compatibility)
            arrive(owner,time.monotonic())
        while any(not s['done'] for s in states.values()):
            if time.monotonic()-begin>plan['live_wall_budget_s']:raise TimeoutError('bounded continuous batch wall budget')
            while True:
                try:observed,kind,owner,data=notifications.get_nowait()
                except queue.Empty:break
                state=states[owner]
                if kind=='user':
                    if not state['done']:arrive(owner,observed)
                    continue
                controller.emit('tool_'+kind,owner=owner,round=state['round'],actual_observed_monotonic_s=observed,**data)
                if kind=='start':state.update(phase='waiting_tool',tool_started=observed,last_tool=data['tool_name'])
                else:
                    result=compact(data['result'])
                    state['messages'].append({'role':'function','name':data['tool_name'],'content':result});state['llm'].pending_tool_results.append(result)
                    state.update(phase='queued',tool_started=None);enqueue(owner,observed)
            now=time.monotonic()
            if not ready:time.sleep(.002);continue
            ready=deque(sorted(ready));queue_owners=[owner for _,owner in ready]
            observations={};observed_at=time.monotonic()
            for owner,(initial,at) in list(engine.cache.idle.items()):
                state=states[owner]
                features={**initial,'elapsed_idle_s':initial['elapsed_idle_s']+observed_at-at,
                    'tool_name':state['last_tool'],'tool_elapsed_s':observed_at-state['tool_started'] if state['tool_started'] is not None else None,
                    'phase':state['phase'],'is_queued':owner in queue_owners,'queue_length':len(queue_owners),'retry_count':0}
                engine.observe(owner,features);observations[owner]=features
            controller.emit('scheduler_snapshot',observed_monotonic_s=observed_at,ready_owners=queue_owners,observed_features=observations)
            _,owner=ready.popleft();state=states[owner]
            if state['done']:continue
            controller.emit('request_dispatch',owner=owner,round=state['round'])
            try:
                attempts=state['llm'].generation_attempts-state['round_generations_before']
                if attempts>=12:raise RuntimeError('per-turn twelve-generation budget exhausted')
                engine.owner=owner;state['phase']='generating'
                replies=state['llm'].chat(state['messages'],functions=[SCHEMAS[n] for n in state['project']['tools']],stream=False)
                state['messages'].extend(replies)
                calls=[m for m in replies if m.get('function_call')]
                if not calls:
                    final=delivery(replies);finish_round(owner,final=final)
                else:
                    if len(calls)!=1:raise ValueError('one function per model response required')
                    if state['tools'].round_calls>=11:raise RuntimeError('per-turn eleven-tool budget exhausted')
                    call=calls[0]['function_call']
                    arguments=json.loads(call['arguments'])
                    state.update(phase='waiting_tool',last_tool=call['name'],tool_started=None)
                    controller.emit('tool_submitted',owner=owner,round=state['round'],request_id=state['llm'].history[-1]['request_id'],tool_name=call['name'],arguments=arguments)
                    pool.submit(tool_call,owner,state['tools'],call)
            except Exception as exc:finish_round(owner,error=type(exc).__name__+': '+str(exc))
        pool.shutdown(wait=True)
        controller.emit('observation_end',complete=True);controller.finish();backend.finish()
        summary=dict(status='passed' if len(records)==48 and all(r['success'] for r in records) else 'quality_failed',
            planned_projects=6,planned_rounds=48,observed_rounds=len(records),successful_rounds=sum(r['success'] for r in records),
            completed_projects=sum(s['round']==7 and s['done'] and all(r['success'] for r in records if r['owner']==o) for o,s in states.items()),
            model_generations=len(engine.history),tool_calls=sum(len(s['tools'].calls) for s in states.values()),
            tools=dict(Counter(c['name'] for s in states.values() for c in s['tools'].calls)),
            cached_tokens=sum(r['hit'] for r in engine.history),prefill_tokens=sum(r['prefill'] for r in engine.history),
            prefill_s=sum(r['prefill_ns']/1e9 for r in engine.history),decode_s=sum(r['decode_ns']/1e9 for r in engine.history),
            evictions=engine.cache.evictions,prediction_calls=engine.cache.prediction_calls,
            max_prompt_tokens=max((len(r['tokens']) for r in engine.history),default=0),makespan_s=time.monotonic()-begin,
            final_physical=engine.inspect(),policy='retired_first_lru',holdout_model_tasks=0,natural_JCT_acceptance=False,
            scope='six authored development projects; capability/trace collection, not a policy performance comparison')
        write(work/'summary.json',summary)
        return summary
    finally:
        pool.shutdown(wait=True)
        if server is not None:server.shutdown();server.server_close()
        for state in states.values():
            budget=state['budget']
            if budget.path.exists():write(budget.path.with_suffix('.sealed.json'),dict(sealed=True,sha256=file_digest(budget.path),summary=budget.summary()))
        controller.abort();backend.abort()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,required=True);args=parser.parse_args()
    plan=json.loads(args.plan.read_text());evidence=Path(plan['evidence']);work=Path(plan['work'])
    if work.exists() or (evidence/'state.json').exists():raise ValueError('attempt already exists; never overwrite')
    work.mkdir(parents=True);write(work/'frozen-plan.json',plan)
    state=dict(status='preparing',pid=os.getpid(),started_wall_ns=time.time_ns(),plan_sha256=file_digest(args.plan),holdout_model_tasks=0,completed_rounds=0,successful_rounds=0)
    def save(records=None):
        if records is not None:state.update(completed_rounds=len(records),successful_rounds=sum(r['success'] for r in records))
        state['updated_utc']=datetime.now(timezone.utc).isoformat()
        state['new_generations']=sum(len(p.read_text().splitlines()) for p in work.rglob('*.jsonl') if p.parent.name=='ledgers')
        write(evidence/'state.json',state)
    def stop(signum,frame):raise InterruptedError('bounded run stopped by signal '+str(signum))
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGALRM):signal.signal(sig,stop)
    signal.alarm(plan['overall_wall_budget_s']);engine=None;save()
    try:
        if sys.prefix!=plan['runtime_prefix']:raise ValueError('wrong runtime')
        def check_sources():
            for path,expected in plan['source_sha256'].items():
                if file_digest(path)!=expected:raise ValueError('registered source changed: '+path)
            if identity(plan['model']['path'])!=plan['model']['identity']:raise ValueError('model inode changed')
        check_sources()
        baseline=FrozenEqualCostBaseline(ROOT,ROOT/plan['baseline_manifest']);baseline.verify()
        receipt=json.loads(Path(plan['model_receipt']).read_text())
        if file_digest(plan['model_receipt'])!=plan['model_receipt_sha256'] or receipt['sha256']!=plan['model']['sha256'] or receipt['identity']!=plan['model']['identity']:raise ValueError('previous full model verification no longer valid')
        write(work/'model-verification.json',dict(method='previous full SHA256 plus unchanged dev/inode/size/mtime/ctime',receipt_path=plan['model_receipt'],receipt_sha256=plan['model_receipt_sha256'],identity=identity(plan['model']['path']),new_full_hash=False,residency_claim=False))
        native=json.loads(Path(plan['native_identity']).read_text())
        if file_digest(plan['library'])!=native['adapter_sha256']:raise ValueError('native adapter changed')
        for path,expected in native['linked_runtime_libraries'].items():
            if file_digest(path)!=expected:raise ValueError('native dependency changed')
        runtime=json.loads(Path(plan['runtime_manifest']).read_text())
        for row in runtime['files']:
            if file_digest(Path(runtime['dst'])/row['relative'])!=row['sha256']:raise ValueError('private runtime changed')
        state['topology']=check_topology(plan);write(work/'host-before.json',host_sample())
        if host_sample()['meminfo_kib']['MemAvailable']<plan['minimum_available_gib']*1024**2:raise ValueError('insufficient available memory')
        os.sched_setaffinity(0,plan['model_cpus'])
        from transformers import AutoTokenizer
        tokenizer=AutoTokenizer.from_pretrained(plan['tokenizer'],local_files_only=True)
        compatibility=hashlib.sha256(compact(dict(stage='continuous-projects-v7',model=plan['model']['sha256'],library=native['adapter_sha256'],tokenizer=file_digest(Path(plan['tokenizer'])/'tokenizer.json'),capacity=plan['capacity_blocks'],context=plan['context_tokens'],block=128)).encode()).hexdigest()
        state.update(status='loading_model',runtime_compatibility=compatibility);save();started=time.monotonic()
        engine=PressureEngine(plan['library'],plan['model']['path'],tokenizer,capacity=plan['capacity_blocks'],context=plan['context_tokens'],headroom=2048,baseline=baseline,compatibility=compatibility,lock_path=Path(plan['engine_lock']))
        state.update(model_load_s=time.monotonic()-started,device_preflight=engine.resource_preflight,status='native_gate');save()
        probe=ScopedBudget(work/'ledgers/probe.jsonl',limits={'probe':13,'pilot':1,'total':13})
        gate=run_gate(engine,tokenizer,probe,work/'native-gate')
        for path in (work/'native-gate').glob('*.json'):
            if path.name!='summary.json':check_native(json.loads(path.read_text()),plan['capacity_blocks'],128,2048)
        write(probe.path.with_suffix('.sealed.json'),dict(sealed=True,sha256=file_digest(probe.path),summary=probe.summary()))
        state.update(status='running_continuous_projects',native_gate=gate['status']);save()
        result=run_batch(engine,tokenizer,plan,work,save)
        check_sources();baseline.verify()
        state.update(status='completed_collection',collection_status=result['status'],summary=result);save()
    except BaseException as exc:
        state.update(status='stopped',error=type(exc).__name__+': '+str(exc));save()
        raise
    finally:
        signal.alarm(0)
        if engine is not None:engine.close()
        state['finished_wall_ns']=time.time_ns();save();write(work/'run-manifest.json',state);write(work/'host-after.json',host_sample())


if __name__=='__main__':main()
