"""Variable-length scripted-source DAGs, shared policy logic on CPU and device.

Native callbacks execute the same cache plan as replay, including pins, LRU,
retirement and FCFS head blocking. Quotas constrain actual resident tensor
payload; a larger uniformly reserved native buffer is reported separately.
"""
from copy import deepcopy
import math
import time

from .cache import Cache,block_keys
from .kv_layout import Layout


def keys_for(tokens,size,compatibility='qwen2.5-3b-f16-source-v1'):
    # Always recompute at least one token to obtain logits; partial input tail
    # and 31 evaluated generated tokens use the two common headroom blocks.
    count=(len(tokens)-1)//size
    return block_keys(tokens[:count*size],compatibility,size)


class DeviceExecutor:
    def __init__(self,native,block_size=128):
        self.native=native;self.block_size=block_size
        self.layout=Layout(native.usage()['capacity_cells'],block_size)

    def __call__(self,request,cache,previous,hits):
        n=self.native;size=self.block_size;wanted=keys_for(request['tokens'],size)
        self.layout.drop(set(previous)-set(cache.blocks))
        layout=self.layout.step(wanted,hits,len(request['tokens']))
        # Drop evicted owners and any unprotected noncontinuous cached suffix
        # before recomputation; no device copy to host or seed request is used.
        for key,(slot,_) in previous.items():
            if key not in cache.blocks:n.drop(slot+1)
        for index,key in enumerate(wanted):
            if key in previous and index>=hits:
                if cache.blocks[key].pins:raise AssertionError('protected prefix has a missing ancestor')
                n.drop(previous[key][0]+1)
        for index,key in enumerate(wanted[:hits]):
            n.copy(cache.blocks[key].slot+1,0,index*size,(index+1)*size)
        before=n.usage(True)
        result=n.run(request['tokens'],hits*size)
        realized=n.usage(True)
        if realized['active_cells']!=len(request['tokens'])+31:raise AssertionError('wrong real active KV cells')
        for index,key in enumerate(wanted):
            n.copy(0,cache.blocks[key].slot+1,index*size,(index+1)*size)
        n.drop(0);idle=n.usage()
        if idle['resident_cells']!=len(cache.blocks)*size or idle['active_cells']:
            raise AssertionError('device cache union differs from controller')
        expected=len(cache.blocks)*size+(len(request['tokens'])-len(wanted)*size)+31
        if realized['resident_cells']!=expected:raise AssertionError('actual peak union mismatch')
        if (layout['peak_span'],layout['idle_span'],layout['peak_resident'],layout['idle_resident'])!=(
                realized['attention_span'],idle['attention_span'],realized['resident_cells'],idle['resident_cells']):
            raise AssertionError(f'physical KV layout mismatch: {layout}, real peak {realized}, idle {idle}')
        if realized['resident_cells']>(cache.capacity+2)*size:raise AssertionError('device payload quota exceeded')
        return result|{'before':before,'peak':realized,'idle':idle,'layout':layout,
                       'owner_sequences':{key:b.slot+1 for key,b in cache.blocks.items()}}


def run(case,capacity,retention,policy='lru',ttl=0,executor=None,cost=None,script=None,clock=None):
    if policy not in {'lru','retired_first_lru','fixed_ttl','fixed_ttl_retired_first','offline_script','offline_script_retired_first'}:
        raise ValueError('unknown source policy')
    if policy.startswith('offline_script')!=(script is not None):raise ValueError('future script privilege mismatch')
    if script is not None and (set(script)-{r['id'] for r in case['requests']} or
                              any(not math.isfinite(v) or v<0 for v in script.values())):
        raise ValueError('invalid future action script')
    if ttl<0 or not math.isfinite(ttl):raise ValueError('invalid TTL')
    if (executor is None)==(cost is None):raise ValueError('choose exactly one device or model executor')
    size=case['block_size'];cache=Cache(capacity-2,retention,size*36864)
    if cache.capacity>254:raise ValueError('native owner sequence limit')
    if any(len(keys_for(r['tokens'],size))>cache.capacity for r in case['requests']):raise ValueError('request exceeds quota')
    active_clock=clock or time.perf_counter;start=active_clock() if executor else None
    pending={r['id']:r for r in case['requests']};order={r['id']:i for i,r in enumerate(case['requests'])}
    done={};rows=[];bindings={};decisions=[];now=0.;events=[]
    arrivals=case['arrivals'];tokens={r['id']:keys_for(r['tokens'],size) for r in case['requests']}
    def current():return active_clock()-start if executor else now
    def advance(t):cache.advance(t)
    while pending:
        ready,_,request=min((max([arrivals[r['workflow']]]+[done[d]+delay for d,delay in r['dependencies']]),order[r['id']],r)
                            for r in pending.values() if all(d in done for d,_ in r['dependencies']))
        if executor:
            wait=ready-current()
            if wait>0:time.sleep(wait)
            now=current()
        else:now=max(now,ready)
        advance(now);wanted=tokens[request['id']];blocked=0.
        while not cache.can_start(wanted):
            expiry=cache.next_expiry()
            if not math.isfinite(expiry):raise RuntimeError('source cache deadlock')
            before=now
            if executor:
                wait=expiry-current()
                if wait>0:time.sleep(wait)
                now=current()
            else:now=max(now,expiry)
            advance(now);blocked+=now-before
        previous={key:(b.slot,b.generation) for key,b in cache.blocks.items()}
        live={key for keys in bindings.values() for key in keys}
        rank=(lambda key:int(key not in live)) if 'retired_first' in policy else None
        hits=cache.start(request['context'],wanted,rank)
        resident=len(cache.blocks)*size
        dispatched=now
        if executor:
            control_start=active_clock();output=executor(request,cache,previous,hits)
            now=current();service=now-dispatched
            control_ms=1000*(active_clock()-control_start)-output['service_ms']
        else:
            service=(cost.controller_cost(request,cache,previous,hits) if hasattr(cost,'controller_cost')
                     else cost(request,hits*size,resident))/1000
            if not math.isfinite(service) or service<=0:raise ValueError('invalid source cost')
            now+=service;output=None;control_ms=None
        finish=now;advance(finish)
        cache.finish(request['context'],wanted,0);cache.unpin(request['context'])
        bindings[request['context']]=set(wanted)
        done[request['id']]=finish;del pending[request['id']]
        terminal=not any(r['workflow']==request['workflow'] for r in pending.values())
        retired=request.get('retire_context',False)
        duration=0 if terminal or retired else (script.get(request['id'],0) if script is not None else ttl if policy.startswith('fixed_ttl') else 0)
        if not math.isfinite(duration) or duration<0:raise ValueError('invalid scripted TTL')
        admitted_before=cache.admitted
        if duration:cache.finish(request['context'],wanted,duration)
        if retired:cache.unpin(request['context']);bindings.pop(request['context'],None)
        if terminal:
            for context in {r['context'] for r in case['requests'] if r['workflow']==request['workflow']}:
                cache.unpin(context);bindings.pop(context,None)
        decisions.append({'request_id':request['id'],'ttl':duration,'admitted':cache.admitted>admitted_before,'terminal':terminal})
        if output is not None:
            output['protected_unique_blocks']=sum(bool(b.pins) for b in cache.blocks.values())
            output['protected_owner_counts']={key:len(b.pins) for key,b in cache.blocks.items() if b.pins}
            # Releasing a lease affects the controller, never resident native KV.
        rows.append({'request_id':request['id'],'workflow':request['workflow'],'ready_s':ready,'dispatch_s':dispatched,
                     'finish_s':finish,'queue_s':dispatched-ready,'protection_wait_s':blocked,'service_s':service,
                     'cached_tokens':hits*size,'prompt_tokens':len(request['tokens']),'resident_input_tokens':resident,
                     'control_ms':control_ms,'device':output})
    jct={workflow:max(done[r['id']] for r in case['requests'] if r['workflow']==workflow)-arrival
         for workflow,arrival in arrivals.items()}
    return {'case':case['id'],'policy':policy,'ttl':ttl,'capacity':capacity,'retention':retention,
            'evidence_kind':'measured' if executor else 'replayed','workflow_jct_s':jct,
            'mean_jct_s':sum(jct.values())/len(jct),'queue_s':sum(r['queue_s'] for r in rows),
            'protection_wait_s':sum(r['protection_wait_s'] for r in rows),
            'prefill_tokens':sum(r['prompt_tokens']-r['cached_tokens'] for r in rows),
            'protected_byte_seconds':cache.protected_byte_seconds,'evictions':cache.evictions,
            'admitted':cache.admitted,'rejected':cache.rejected,'requests':rows,'decisions':decisions,
            'performance_claim_allowed':False}
