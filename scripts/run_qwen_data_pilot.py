"""Sequential Qwen-Agent + local CUDA, gated main pilot with complete failure retention."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from transformers import AutoTokenizer
from leasepath.qwen_data_agent import ObservedQwen
from leasepath.qwen_data_audit import audit
from leasepath.qwen_data_backend import NativeEngine,PINNED_LLAMA
from leasepath.qwen_data_ledger import GenerationBudget
from leasepath.qwen_data_tasks import equal
from leasepath.real_observer import Observer
from leasepath.restricted_data_tools import Resource,SessionTools,file_hash,schemas_for

SYSTEM = """You are a data analyst using bounded parameter tools. Use only the tools provided.
Select the correct operation and its parameters from the task. Do real computation with compute.
You must read its returned result before answering. Do not invent results or write code/SQL.
Arguments are strict JSON. Call one tool at a time. Keep the full history. There is no compression.
After a successful computation, end with exactly one JSON object:
{"artifact_id": "<ID returned by compute>", "answer": <the complete result object returned by compute>}.
Copy every required row, scalar and fold score accurately; preserve at least 9 significant digits.
Do not add explanations, code fences or tool calls after your final answer.
"""

class TimedObserver(Observer):
    def __init__(self,*args,**kwargs):
        self.timings=[]
        super().__init__(*args,**kwargs)

    def emit(self,kind,**data):
        begin=time.monotonic_ns()
        super().emit(kind,**data)
        self.timings.append({'kind':kind,'stream_id':self.identity['stream_id'],
                             'sequence':self.sequence-1,'start_ns':begin,
                             'end_ns':time.monotonic_ns(),
                             'scope':'serialization_and_os_write_no_fsync'})

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library',type=Path,required=True)
    p.add_argument('--model',type=Path,required=True)
    p.add_argument('--tokenizer',type=Path,required=True)
    p.add_argument('--tasks',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--ledger',type=Path,required=True)
    p.add_argument('--lane',choices=['probe','pilot'],required=True)
    p.add_argument('--task-ids',nargs='+')
    p.add_argument('--protocol-pass',type=Path)
    p.add_argument('--backend-pass',type=Path)
    p.add_argument('--infrastructure-only',action='store_true')
    p.add_argument('--model-identity',type=Path)
    p.add_argument('--gpus',type=int,default=3)
    args=p.parse_args()
    if not args.infrastructure_only:
        if args.model_identity is None:raise ValueError('verified 32B conversion identity required')
        model_identity=json.loads(args.model_identity.read_text())
        if (model_identity['repository']!='Qwen/Qwen2.5-Coder-32B-Instruct' or
                model_identity['revision']!='381fc969f78efac66bc87ff7ddeadb7e73c218a7' or
                model_identity['dtype']!='float16' or model_identity['quantization'] is not None or
                model_identity['gguf_sha256']!=file_hash(args.model)):
            raise ValueError('model differs from verified 32B identity')
        if args.backend_pass is None:raise ValueError('32B backend boundary gate required')
        backend_gate=json.loads((args.backend_pass/'summary.json').read_text())
        if (backend_gate.get('scope')!='32b_backend_boundaries' or backend_gate.get('status')!='passed' or
                backend_gate.get('model_identity_sha256')!=file_hash(args.model_identity)):
            raise ValueError('32B backend boundary gate failed')
    all_tasks=json.loads((args.tasks/'tasks.private.json').read_text())
    tasks=[t for t in all_tasks if not args.task_ids or t['id'] in args.task_ids]
    if not tasks:raise ValueError('no selected tasks')
    if args.lane=='pilot':
        if args.infrastructure_only:raise ValueError('infrastructure model cannot enter pilot')
        preflight=json.loads((args.tasks/'preflight.json').read_text())
        if preflight['tasks_sha256']!=file_hash(args.tasks/'tasks.private.json'):
            raise ValueError('task manifest changed after preflight')
        gate=json.loads((args.protocol_pass/'summary.json').read_text())
        if (gate.get('scope')!='32b_protocol' or gate.get('complete_success',0)<2 or gate.get('audit_status')!='passed' or
                gate.get('model_identity_sha256')!=file_hash(args.model_identity)):
            raise ValueError('32B real protocol gate not passed')
        if args.task_ids or len(tasks)!=12:raise ValueError('main pilot is one frozen batch of twelve')
    refs_path=args.tasks/'references.private.json'
    refs=json.loads(refs_path.read_text())
    if not all(t['id'] in refs for t in tasks):raise ValueError('task references incomplete')
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    (output/'raw').mkdir()
    run_id=output.name+'-'+uuid.uuid4().hex[:8]
    budget=GenerationBudget(args.ledger)
    controller=TimedObserver(output/'raw/controller.jsonl',run_id=run_id,source='qwen_controller')
    backend=TimedObserver(output/'raw/backend.jsonl',run_id=run_id,source='native_cuda_engine')
    protected=[Path(__file__).resolve(),args.tasks/'tasks.private.json',refs_path,args.library,
               args.tokenizer/'tokenizer.json',args.tokenizer/'tokenizer_config.json']
    protected+=list((Path(__file__).resolve().parents[1]/'src/leasepath').glob('qwen_data_*.py'))
    protected+=list((Path(__file__).resolve().parents[1]/'src/leasepath').glob('restricted_data_*.py'))
    hashes={str(p.resolve()):file_hash(p) for p in protected}
    identity={'run_id':run_id,'baseline':'66a34c8eb618f4849153cb3c8d72880362e8e387',
              'lane':args.lane,'scope':'infrastructure_only' if args.infrastructure_only else
                      '32b_protocol' if args.lane=='probe' else '32b_pilot',
              'model':str(args.model.resolve()),'model_sha256':file_hash(args.model),
              'model_identity_sha256':file_hash(args.model_identity) if args.model_identity else None,
              'tokenizer':str(args.tokenizer.resolve()),'library_sha256':file_hash(args.library),
              'tokenizer_sha256':file_hash(args.tokenizer/'tokenizer.json'),
              'template_sha256':file_hash(args.tokenizer/'tokenizer_config.json'),
              'dtype':'float16','kv_dtype':'float16','context':16384,
              'layer_partition':True,'tensor_parallel':False,
              'llama_commit':PINNED_LLAMA,'qwen_agent':'0.0.34','protected_sha256':hashes,
              'tasks':[t['id'] for t in tasks],
              'expected_stream_ids':[controller.identity['stream_id'],backend.identity['stream_id']],
              'start_wall_ns':time.time_ns(),'start_monotonic_ns':time.monotonic_ns()}
    identity['compatibility_id']=hashlib.sha256(json.dumps({
        k:identity[k] for k in ('model_sha256','library_sha256','llama_commit','qwen_agent',
                              'tokenizer_sha256','template_sha256','dtype','kv_dtype','context')},
        sort_keys=True).encode()).hexdigest()
    (output/'identity.json').write_text(json.dumps(identity,indent=2)+'\n')
    def check_sources():
        for name,value in hashes.items():
            if file_hash(name)!=value:raise ValueError('protected source changed: '+name)
    tokenizer=AutoTokenizer.from_pretrained(str(args.tokenizer),local_files_only=True)
    engine=None
    qualities=[]
    complete=False
    try:
        engine=NativeEngine(args.library,args.model,gpus=args.gpus)
        controller.emit('engine_started',device_preflight=engine.resource_preflight,
                        cuda_visible_devices=os.environ.get('CUDA_VISIBLE_DEVICES'),
                        python_executable=os.sys.executable,argv=os.sys.argv)
        llm=ObservedQwen(engine,tokenizer,budget,controller,backend,lane=args.lane)
        llm.check_sources=check_sources
        for task in tasks:
            owner=task['id'];llm.owner=owner
            llm.task_deadline=time.monotonic()+900
            engine.clear()
            backend.emit('cache_clear',owner=owner)
            controller.emit('workflow_submit',owner=owner,source_group=task['source_group'])
            controller.emit('context_open',owner=owner,context_version='v1',
                            compatibility_id=identity['compatibility_id'])
            tools=SessionTools(output/'sessions'/owner/'artifacts',
                    {task['dataset_id']:Resource(Path(task['file']),task['input_sha256'],task['kind'],task['metadata'])},
                    task['operations'],timeout=240,threads=2,memory_gib=12)
            functions=schemas_for(task['operations'])
            messages=[{'role':'system','content':SYSTEM},
                      {'role':'user','content':f"Session dataset_id: {task['dataset_id']}\n"
                       f"Task: {task['intent']}\nUse only this session dataset. "
                       "The dataset schema is available from inspect_input."}]
            began=time.monotonic_ns()
            final=None
            attempts=0
            failure=None
            try:
                for turn in range(6):
                    if time.monotonic()>=llm.task_deadline:raise TimeoutError('task wall budget')
                    replies=llm.chat(messages,functions=functions,stream=False)
                    attempts+=1
                    messages.extend(replies)
                    calls=[m for m in replies if m.get('function_call')]
                    if not calls:
                        text=''.join(m.get('content','') for m in replies).strip()
                        final=json.loads(text)
                        break
                    if len(calls)!=1:raise ValueError('multiple calls in sequential protocol')
                    call=calls[0]['function_call']
                    try:
                        arguments=json.loads(call['arguments'])
                    except (TypeError,ValueError):
                        arguments={'invalid_json':call['arguments'][:200]}
                    tool_id='tool'+uuid.uuid4().hex
                    controller.emit('tool_start',owner=owner,tool_id=tool_id,tool_name=call['name'])
                    protected_start=time.monotonic_ns()
                    check_sources()
                    protected_pre_ns=time.monotonic_ns()-protected_start
                    result=tools.call(call['name'],arguments)
                    protected_start=time.monotonic_ns()
                    check_sources()
                    protected_post_ns=time.monotonic_ns()-protected_start
                    result_json=json.dumps(result,allow_nan=False,separators=(',',':'))
                    controller.emit('tool_end',owner=owner,tool_id=tool_id,tool_name=call['name'],
                                    result_json=result_json,call=tools.calls[-1],
                                    protected_pre_ns=protected_pre_ns,protected_post_ns=protected_post_ns)
                    messages.append({'role':'function','name':call['name'],'content':result_json})
                    llm.pending_tool_results.append(result_json)
                if final is None:raise RuntimeError('six-generation task budget')
                if not isinstance(final,dict) or set(final)!={'artifact_id','answer'}:
                    raise ValueError('final answer schema')
                if final['artifact_id'] not in tools.artifacts:raise ValueError('unknown final artifact')
                expected=refs[owner]['reference']
                if not equal(tools.artifacts[final['artifact_id']],expected):
                    raise ValueError('computed artifact differs from independent reference')
                if not equal(final['answer'],expected):raise ValueError('final answer differs from reference')
            except Exception as exc:
                failure={'type':type(exc).__name__,'message':str(exc)[:1000]}
            finally:
                # Every synchronous native request has drained before this explicit discard.
                controller.emit('context_retire',owner=owner,context_version='v1',
                                reason='runner_explicitly_discards_completed_or_failed_task')
            quality={'task':owner,'source_group':task['source_group'],'planned_band':task['planned_band'],
                     'success':failure is None,'failure':failure,'generations':attempts,
                     'wall_s':(time.monotonic_ns()-began)/1e9,'final':final,
                     'tools':[{k:c.get(k) for k in ('call_id','name','started_ns','ended_ns','child_wall_ns','status')}
                              for c in tools.calls]}
            qualities.append(quality)
            (output/'sessions'/owner/'messages.json').write_text(json.dumps(messages,indent=2)+'\n')
            (output/'sessions'/owner/'quality.json').write_text(json.dumps(quality,indent=2)+'\n')
            print(owner,'PASS' if quality['success'] else failure,flush=True)
        engine.close();engine=None
        controller.emit('observation_end',complete=True)
        seal_started=time.monotonic_ns()
        backend.finish();controller.finish()
        seal_ns=time.monotonic_ns()-seal_started
        complete=True
        (output/'overhead.json').write_text(json.dumps({
            'observer_writes':controller.timings+backend.timings,'final_fsync_and_close_ns':seal_ns,
            'workspace_hash_1':'not_applicable','workspace_hash_2':'not_applicable',
            'scope':'nested durations: child_wall is inside tool, observer writes inside inter-request wall; do not sum all categories',
            'durability':'generation reservations fsync before entry; artifacts fsync before tool return; observation streams fsync only after producers drained'
        },indent=2)+'\n')
        try:
            audit_result=audit(output,export=True)
        except Exception as exc:
            audit_result={'status':'rejected','reason':str(exc),'type':type(exc).__name__}
            (output/'audit-rejection.json').write_text(json.dumps(audit_result,indent=2)+'\n')
        summary={'scope':identity['scope'],'model_identity_sha256':identity['model_identity_sha256'],
                 'status':'passed' if all(q['success'] for q in qualities) else 'quality_failed',
                 'complete_success':sum(q['success'] for q in qualities),'tasks':len(qualities),
                 'success_per_planned_band':dict(Counter(q['planned_band'] for q in qualities if q['success'])),
                 'quality':qualities,'audit_status':audit_result['status'],'audit':audit_result,
                 'budget':budget.summary(),'runtime_s':(time.monotonic_ns()-identity['start_monotonic_ns'])/1e9}
        successes={q['task'] for q in qualities if q['success']}
        if audit_result['status']=='passed' and any(
                item['owner'] in successes for item in audit_result['tool_results_unconsumed']):
            summary['audit_status']='rejected'
            summary['audit']['status']='rejected'
            summary['audit']['reason']='successful task contains an unconsumed tool result'
        if args.lane=='pilot':
            summary['pilot_qualified']=(summary['complete_success']>=10 and
                    all(summary['success_per_planned_band'].get(k,0)>=3 for k in ('short','medium','long')) and
                    summary['audit_status']=='passed')
        (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        print(json.dumps({k:v for k,v in summary.items() if k not in ('quality','audit')},indent=2),flush=True)
    finally:
        if engine:engine.close()
        if not complete:
            backend.abort();controller.abort()

if __name__=='__main__':
    main()
