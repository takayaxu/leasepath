"""Bounded, preregistered L1/L2/L3 runs; never imports reference patches."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from leasepath.real_environment import prepare_environment, preflight_environment
from leasepath.real_observer import Observer
from leasepath.real_patches import capture_patch
from leasepath.real_tools_v3 import RepairTools, ToolRejected, decode_action
from transformers import AutoTokenizer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['tasks','environments','run','config','diagnostics','output']:
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--level',choices=['L1','L2','L3'],required=True)
    args = parser.parse_args()
    args.output = args.output.absolute(); args.output.mkdir(parents=True,exist_ok=False)
    config = json.loads(args.config.read_text())
    diagnostics = json.loads(args.diagnostics.read_text())
    manifest = json.loads((args.run/'service-manifest.json').read_text())
    model = manifest['command'][manifest['command'].index('--model')+1]
    served = manifest['command'][manifest['command'].index('--served-model-name')+1]
    tokenizer = AutoTokenizer.from_pretrained(model,local_files_only=True)
    count = lambda text: len(tokenizer.encode(text,add_special_tokens=False))
    tasks = json.loads(args.tasks.read_text())['tasks']
    frozen = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
              [args.config,args.diagnostics,args.tasks,Path(__file__),
               Path(__file__).resolve().parents[1]/'src/leasepath/real_tools_v3.py']}
    (args.output/'freeze.json').write_text(json.dumps(frozen,indent=2)+'\n')
    # urllib has no application retry wrapper; exactly one HTTP call per attempt.
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    cpus = sorted(os.sched_getaffinity(0))[-4:]
    results = []
    for task in tasks:
        tid = task['instance_id']; spec = diagnostics['tasks'][tid]
        target = args.output/tid; target.mkdir(); work = target/'workspace'
        subprocess.run(['git','clone','-q','--shared',task['workspace'],str(work)],check=True)
        base = subprocess.check_output(['git','rev-parse','HEAD'],cwd=work,text=True).strip()
        source_manifest = args.tasks.parent/'evaluation'/(tid+'.source-files.json')
        if hashlib.sha256(source_manifest.read_bytes()).hexdigest()!=task['source_files_manifest_sha256']:
            raise ValueError('frozen source inventory differs')
        for name,digest in json.loads(source_manifest.read_text()).items():
            if hashlib.sha256((work/name).read_bytes()).hexdigest()!=digest:
                raise ValueError('workspace differs from frozen task source')
        template = args.environments/('flask' if task['repo']=='pallets/flask' else 'pytest-py39')
        env, wrappers = prepare_environment(work,template,target/'environment',task['repo'])
        evidence = preflight_environment(work,env,wrappers,task['repo'],cpus=cpus)
        (target/'environment.json').write_text(json.dumps(evidence,indent=2)+'\n')
        ledger=Path(diagnostics['budget_ledger']);ledger.parent.mkdir(parents=True,exist_ok=True)
        with ledger.open('a+') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX);stream.seek(0)
            used=[json.loads(line) for line in stream if line.strip()]
            identity=[str(Path(model).resolve()),config['version'],args.level,tid]
            if len(used)>=diagnostics['total_diagnostic_attempt_limit'] or any(x['identity']==identity for x in used):
                raise ValueError('diagnostic budget exhausted or repeated condition/source; no model call allowed')
            stream.write(json.dumps({'identity':identity,'reserved_at_ns':time.monotonic_ns(),
                'run':str(args.run),'output':str(target),'counts_even_if_invalid':True})+'\n');stream.flush()
            fcntl.flock(stream,fcntl.LOCK_UN)
        owner = tid+'-'+args.level+'-'+uuid.uuid4().hex[:8]
        log = Observer(target/'runner.jsonl',run_id=args.run.name,source='diagnostic_v3')
        log.emit('context_open',owner=owner,context_version='initial',level=args.level,task_id=tid,
                 source_group=task['source_group'],assistance='local_code' if args.level=='L1' else 'file_location' if args.level=='L2' else 'none')
        tools = RepairTools(work,target/'tool-outputs',env,cpus,count,page_tokens=config['tool_page_tokens'],
                            edit_paths=spec['production_paths'] if args.level=='L1' else None)
        system = config['L1_system'] if args.level=='L1' else config['agent_system']
        prompt = task['problem_statement']
        if args.level=='L1':
            prompt += '\n\nThe following unmodified source and existing visible tests are provided for LOCAL diagnosis.\n'
            for excerpt in spec['excerpts']:
                name,start,end = excerpt
                lines = (work/name).read_text().splitlines()
                prompt += f'\nFILE {name}, lines {start}-{min(end,len(lines))}:\n'+'\n'.join(lines[start-1:end])+'\n'
            prompt += '\nAllowed production files: '+json.dumps(spec['production_paths'])
        elif args.level=='L2':
            prompt += '\n\nGiven locations for this assisted diagnostic: '+', '.join(spec['production_paths'])
        else:
            prompt += '\n\nThis checkout is the library to repair; source src/, tests tests/ or testing/. Locate relevant implementation yourself.'
        prompt += '\nIndependent tests are NOT provided. Existing visible regression command: '+spec['visible_regression'] if args.level!='L3' else '\nUse the private python -m pytest on relevant existing tests.'
        messages = [{'role':'system','content':system},{'role':'user','content':prompt}]
        max_calls = config['L1_calls'] if args.level=='L1' else config['agent_calls']
        max_tokens = config['L1_output_tokens'] if args.level=='L1' else config['agent_output_tokens']
        budget = config['L1_wall_seconds'] if args.level=='L1' else config['agent_wall_seconds']
        started = time.monotonic(); requests = []; actions = []; status='CallLimit'
        invalid=0; syntax_streak=0; no_progress=0
        try:
            for call in range(1,max_calls+1):
                remaining = budget-(time.monotonic()-started)
                if remaining<=0:
                    status='WallTimeLimit'; break
                rendered = tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,
                    **config.get('chat_template_kwargs',{}))
                ids = tokenizer.encode(rendered,add_special_tokens=False)
                # This stop occurs BEFORE request_ready / HTTP, no opaque 400 retries.
                if len(ids)+max_tokens>config['context_tokens']:
                    log.emit('context_budget_stop',owner=owner,input_tokens=len(ids),reserved_output=max_tokens)
                    status='ContextBudgetLimit'; break
                rid = uuid.uuid4().hex
                payload = {'model':served,'messages':messages,'request_id':rid,'temperature':0,'top_p':1,
                           'seed':23,'max_tokens':max_tokens,**config.get('request_extra',{})}
                (target/f'request-{call:03d}.json').write_text(json.dumps(payload,ensure_ascii=False)+'\n')
                (target/f'request-{call:03d}.tokens.json').write_text(json.dumps({'input_tokens':len(ids),
                    'templated_sha256':hashlib.sha256(rendered.encode()).hexdigest(),
                    'prompt_token_ids_sha256':hashlib.sha256(json.dumps(ids).encode()).hexdigest(),
                    'reserved_output':max_tokens,'context_version':'initial'})+'\n')
                log.emit('request_ready',owner=owner,context_version='initial',request_id=rid,attempt_id=rid,
                         logical_request_id=rid,retry_of=None,purpose='demand',wire_api='chat',input_tokens=len(ids))
                before=time.monotonic_ns()
                req=urllib.request.Request(f"http://127.0.0.1:{manifest['port']}/v1/chat/completions",
                    data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
                try:
                    with http.open(req,timeout=min(remaining,config['http_seconds'])) as response:
                        body=response.read(); response_status=response.status
                except urllib.error.HTTPError as error:
                    body=error.read();(target/f'request-{call:03d}.error').write_bytes(body)
                    log.emit('client_error',owner=owner,request_id=rid,status=error.code,
                             body_sha256=hashlib.sha256(body).hexdigest(),retry_attempted=False)
                    status='HTTPError';break
                except (TimeoutError,OSError) as error:
                    log.emit('client_error',owner=owner,request_id=rid,status=type(error).__name__,retry_attempted=False)
                    status=type(error).__name__;break
                data=json.loads(body);(target/f'response-{call:03d}.json').write_bytes(body)
                choice=data['choices'][0];content=choice['message'].get('content') or ''
                metric={'request_id':rid,'status':response_status,'elapsed_s':(time.monotonic_ns()-before)/1e9,
                        'usage':data['usage'],'finish_reason':choice['finish_reason'],'local_input_tokens':len(ids)}
                requests.append(metric)
                log.emit('client_response',owner=owner,request_id=rid,**{k:metric[k] for k in ['status','finish_reason','usage']})
                messages.append({'role':'assistant','content':content})
                # Preserve EVERY model output, including invalid JSON and length-capped answers.
                tool_id=uuid.uuid4().hex;log.emit('tool_start',owner=owner,request_id=rid,tool_id=tool_id,raw_action=content)
                patch_before=capture_patch(work,base);began=time.monotonic_ns()
                try:
                    action=decode_action(content);observation=tools.execute(action);invalid=0
                except (ValueError,KeyError,TypeError,OSError) as error:
                    action=None;invalid+=1;observation={'rejected':True,'reason':str(error),'exception':type(error).__name__}
                patch_after=capture_patch(work,base)
                if action and action.get('op')=='shell' and patch_before!=patch_after:
                    names=subprocess.check_output(['git','diff','--name-only',base],cwd=work,text=True).splitlines()
                    names+=subprocess.check_output(['git','ls-files','--others','--exclude-standard'],cwd=work,text=True).splitlines()
                    observation['automatic_syntax']=tools.syntax([name for name in names if (work/name).is_file()])
                for syntax in observation.get('automatic_syntax',[]):
                    log.emit('automatic_syntax_check',owner=owner,parent_tool_id=tool_id,**syntax)
                syntax_failed=any(x['returncode'] for x in observation.get('automatic_syntax',[]))
                syntax_streak=syntax_streak+1 if syntax_failed else (0 if observation.get('automatic_syntax') else syntax_streak)
                no_progress=no_progress+1 if patch_before==patch_after else 0
                entry={'call':call,'action':action,'observation':observation,'patch_changed':patch_before!=patch_after,
                       'duration_s':(time.monotonic_ns()-began)/1e9}
                actions.append(entry)
                log.emit('tool_end',owner=owner,request_id=rid,tool_id=tool_id,
                         returncode=observation.get('returncode',1 if observation.get('rejected') else 0),
                         duration_ns=time.monotonic_ns()-began,patch_changed=entry['patch_changed'],rejected=observation.get('rejected',False))
                log.emit('results_ready',owner=owner,request_id=rid,tool_id=tool_id)
                messages.append({'role':'user','content':json.dumps(observation,ensure_ascii=False)})
                if observation.get('submitted'):
                    status='Submitted';break
                if args.level=='L1':
                    status='LocalCandidateProduced' if not observation.get('rejected') else 'LocalCandidateRejected';break
                if invalid>=config['invalid_action_stop'] or syntax_streak>=config['syntax_failure_stop'] or no_progress>=config['no_patch_progress_stop']:
                    status='BoundedNoProgress';break
        finally:
            elapsed=time.monotonic()-started
            # Close explicit relationship only after no outstanding HTTP calls;
            # export still requires independent backend drain checks.
            log.emit('context_retire',owner=owner,context_version='initial',reason='diagnostic_context_discard')
            log.emit('observation_end',owner=owner,client_complete=True)
            log.finish()
            (target/'messages.json').write_text(json.dumps(messages,indent=2,ensure_ascii=False)+'\n')
            patch=capture_patch(work,base);(target/'agent.patch').write_bytes(patch)
        record={'task_id':tid,'source_group':task['source_group'],'level':args.level,'exit_status':status,
                'requests':requests,'actions':actions,'elapsed_s':elapsed,'patch_bytes':len(patch),
                'automatic_syntax_is_not_functional_acceptance':True,'task_correctness':'pending_independent',
                'JCT_scope':'first request preparation to loop end, all tools and syntax checks; baseline environment/archive excluded'}
        (target/'result.json').write_text(json.dumps(record,indent=2)+'\n')
        results.append(record);(args.output/'results.json').write_text(json.dumps(results,indent=2)+'\n')
        print(json.dumps({k:record[k] for k in ['task_id','level','exit_status','elapsed_s','patch_bytes']}),flush=True)


if __name__=='__main__': main()
