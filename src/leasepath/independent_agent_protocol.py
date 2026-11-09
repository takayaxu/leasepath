"""Bounded genuine model/tool protocol; never supplies an oracle next action."""
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import sys
import time
import uuid
from .independent_agent_tasks import evaluate
from .resident_tree_worker_process import UsagePopen

SYSTEM = '''Solve the session problem using the available tools and real returned feedback.
You choose which tool to call, its arguments, whether to revise a candidate, and when to stop.
Tools do not provide an optimal answer. A valid candidate may still be suboptimal.
Use the ✿FUNCTION✿ / ✿ARGS✿ format for each function call, one call at a time.
Return bare JSON {"candidate": ...} when finished. Preserve all prior history.
Do not repeat identical calls unless the actual feedback makes that necessary.
You have at most 12 model responses and 11 tool calls. No hidden retry is available.'''


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def schemas(task):
    if task['kind'] in ('automata_reachability','edit_distance'):candidate={'type':'string','maxLength':10}
    else:candidate={'type':'array','maxItems':16,'items':{'type':'string' if task['kind']=='voting_rule' else 'integer'}}
    return [
        {'name':'inspect_problem','description':'Read the actual session problem data and constraints.',
         'parameters':{'type':'object','properties':{},'additionalProperties':False}},
        {'name':'evaluate_candidate','description':'Execute constraint checks on your submitted candidate and report violations and its objective; no optimum or next action is returned.',
         'parameters':{'type':'object','properties':{'candidate':candidate},'required':['candidate'],'additionalProperties':False}}]


class CandidateTools:
    def __init__(self,task,output,controls,timeout=240):
        self.task=task;self.output=Path(output);self.output.mkdir(parents=True,exist_ok=False)
        self.controls=controls;self.timeout=timeout;self.calls=[]

    def call(self,name,args):
        if len(self.calls)>=11:raise RuntimeError('eleven-call budget exhausted')
        failure=None
        start=time.monotonic_ns();call={'call_id':'t'+uuid.uuid4().hex,'name':name,'arguments':args,'started_ns':start}
        try:
            if name=='inspect_problem':
                if args!={}:raise ValueError('inspect_problem takes no arguments')
                result={'intent':self.task['intent'],'problem':self.task['problem']}
            elif name=='evaluate_candidate':
                if not isinstance(args,dict) or set(args)!={'candidate'}:raise ValueError('candidate argument required')
                # A separate bounded process executes the model-selected candidate.
                payload=canonical({'task':self.task,'candidate':args['candidate'],'controls':self.controls})
                child=UsagePopen([sys.executable,'-m','leasepath.independent_agent_worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
                call['child_pid']=child.pid
                try:stdout,stderr=child.communicate(payload,timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    import os,signal
                    os.killpg(child.pid,signal.SIGKILL);stdout,stderr=child.communicate();raise TimeoutError('candidate tool timeout')
                finally:call['child_lifetime']=child.lifetime_usage
                call['child_returncode']=child.returncode;call['child_stderr']=stderr
                if child.returncode:raise RuntimeError('candidate worker failed: '+stderr[:400])
                worker=json.loads(stdout);result=worker['result'];call['worker_evidence']=worker['evidence']
            else:raise ValueError('unknown tool')
            call['status']='ok'
        except (ValueError,TypeError,KeyError,IndexError) as exc:
            # Actual validation feedback, never an invented failure to provoke loops.
            result={'error':f'{type(exc).__name__}: {exc}'};call['status']='rejected'
        except Exception as exc:
            result={'error':f'{type(exc).__name__}: {exc}'};call['status']='failed';failure=exc
        call.update(result=result,ended_ns=time.monotonic_ns());call['result_sha256']=digest(result);self.calls.append(call)
        with (self.output/'calls.jsonl').open('a') as stream:stream.write(canonical(call)+'\n')
        if failure is not None:raise failure
        return result


def check_final(task,reference,answer):
    if not isinstance(answer,dict) or set(answer)!={'candidate'}:return False,'final schema'
    try:checked=evaluate(task,answer['candidate'])
    except (ValueError,TypeError,KeyError,IndexError) as exc:return False,str(exc)
    if not checked['valid']:return False,'constraint violation'
    if checked['objective_minimize']!=reference['optimum_minimize']:return False,'suboptimal candidate'
    return True,None


def certify_loop(turns,tool_calls,*,successful):
    """Necessary behavioral evidence only; pair with raw native stream verification.

    A different candidate is not automatically proof of reasoning from feedback.
    We certify an observable refinement only if two evaluated candidates differ,
    the intervening exact result is in the next actual prompt, and a failed or
    worse candidate is followed by a valid/better candidate. Otherwise mark
    unestablished; don't force retries or discard one-shot successes.
    """
    evaluations=[c for c in tool_calls if c['name']=='evaluate_candidate' and c.get('status')=='ok' and 'valid' in c['result']]
    evidence=[]
    linked=True
    for c in tool_calls:
        following=[t for t in turns if t['input_ns']>=c['ended_ns']]
        feedback=json.dumps(c['result'],allow_nan=False,separators=(',',':'))
        if not following or feedback not in following[0]['prompt']:linked=False
    for a,b in zip(evaluations,evaluations[1:]):
        changed=digest(a['arguments'])!=digest(b['arguments'])
        better=b['result']['valid'] and (not a['result']['valid'] or b['result']['objective_minimize']<a['result']['objective_minimize'])
        consumed=any(t['input_ns']>=a['ended_ns'] and t['input_ns']<=b['started_ns'] and json.dumps(a['result'],allow_nan=False,separators=(',',':')) in t['prompt'] for t in turns)
        if changed and better and consumed:evidence.append([a['call_id'],b['call_id']])
    return {'successful':successful,'all_feedback_consumed':linked,'observed_refinement':bool(evidence),
            'refinement_pairs':evidence,'behavioral_loop_supported':bool(successful and linked and evidence),'loop_certified':False,
            'native_output_link_audit_required':True,
            'classification':'observable_refinement_pending_native_audit' if successful and linked and evidence else 'no_certified_iterative_evidence'}
