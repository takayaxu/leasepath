"""Independent audit from sealed controller and engine streams to core U/D/C events."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from .prediction_state import snapshot,validate_features
from .qwen_data_backend import verify_request
from .real_observer import validate_observation
from .trace import derive_idle_labels,validate_events

def rows(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines()]

def client_tokenizer(identity):
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(identity['tokenizer'],local_files_only=True)

def audit(run,*,export=False):
    root=Path(run)
    plan=json.loads((root/'identity.json').read_text())
    paths=[root/'raw/controller.jsonl',root/'raw/backend.jsonl']
    transport=validate_observation(paths,expected_stream_ids=plan['expected_stream_ids'])
    controller,backend=[rows(p) for p in paths]
    client=[r for r in controller if r['kind']=='request_input']
    requests=[r for r in backend if r['kind']=='engine_request']
    opened={}
    retire_times={}
    running_tools={}
    for record in controller:
        d=record['data']
        if record['kind']=='context_open':
            if (d['owner'] in opened or d['context_version']!='v1' or
                    d['compatibility_id']!=plan['compatibility_id']):
                raise ValueError('invalid or duplicate context open')
            opened[d['owner']]=record['ts_ns']
        if record['kind']=='context_retire':
            if d['owner'] in retire_times or d['context_version']!='v1':
                raise ValueError('invalid or duplicate context retire')
            retire_times[d['owner']]=record['ts_ns']
        if record['kind']=='tool_start':
            if d['tool_id'] in running_tools:raise ValueError('duplicate running tool')
            running_tools[d['tool_id']]=(d['owner'],d['tool_name'])
        if record['kind']=='tool_end':
            if running_tools.pop(d['tool_id'],None)!=(d['owner'],d['tool_name']):
                raise ValueError('tool end has no matching start')
    if running_tools:raise ValueError('complete observation still has unresolved tools')
    client_map={r['data']['request_id']:r for r in client}
    tokenizer=client_tokenizer(plan)
    if len(client)!=len(client_map) or len(client)!=len(requests):
        raise ValueError('missing, duplicate or unmapped request')
    timelines=[]
    consumed=[]
    unconsumed=[]
    for r in requests:
        d=r['data']
        c=client_map.pop(d['request_id'])['data']
        if (c['owner']!=d['owner'] or c['prompt_sha256']!=d['prompt_sha256'] or
                c['context_version']!=d['context_version'] or d['context_version']!='v1'):
            raise ValueError('request identity differs')
        if not opened.get(d['owner'],float('inf'))<d['enqueue_ns']:
            raise ValueError('request has no explicit open context')
        if d['enqueue_ns']>=retire_times.get(d['owner'],float('inf')):
            raise ValueError('request enqueued after context retire')
        if hashlib.sha256(c['prompt'].encode()).hexdigest()!=c['prompt_sha256']:
            raise ValueError('prompt bytes differ')
        if tokenizer.encode(c['prompt'],add_special_tokens=False)!=c['token_ids']:
            raise ValueError('independent tokenizer differs from recorded client IDs')
        verify_request(d,c['token_ids'])
        if d['release_ns']>r['ts_ns']:
            raise ValueError('native release clock exceeds observed record')
        timelines.append((d['lookup_ns'],'lookup',d))
        timelines.append((d['release_ns'],'release',d))
    if client_map:
        raise ValueError('unmapped client attempts')
    for row in controller:
        if row['kind'] in ('context_retire','observation_end','tool_start','tool_end','workflow_submit'):
            timelines.append((row['ts_ns'],row['kind'],row['data']))
        if row['kind']=='tool_end':
            d=row['data']
            following=[r['data'] for r in client if r['data']['owner']==d['owner'] and
                       r['ts_ns']>row['ts_ns']]
            if not following:
                if retire_times.get(d['owner'],0)<=row['ts_ns']:
                    raise ValueError('unconsumed tool result has no explicit later discard')
                unconsumed.append({'tool_id':d['tool_id'],'owner':d['owner'],
                                   'reason':'no subsequent request before explicit context discard'})
                continue
            if d['result_json'] not in following[0]['prompt']:
                raise ValueError('tool result not consumed by next actual backend request')
            consumed.append(d['tool_id'])
    timelines.sort(key=lambda e:e[0])
    run_id=plan['run_id']
    clock='monotonic:'+transport['streams'][0]['boot_id']
    events=[]
    open_episodes={}
    retired=set()
    last_release={}
    ep_index=0
    compatibility=plan['compatibility_id']
    def emit(kind,t,owner,data,**keys):
        event={'schema_version':'0.1','run_id':run_id,'event_id':f'e{len(events)}',
               'event_type':kind,'ts_ns':t,'clock_domain':clock,'evidence_kind':'measured',
               'workflow_id':owner,'data':data,**keys}
        events.append(event)
        return event
    # Every core request stage is sourced to the real native record.
    for r in requests:
        d=r['data']; owner=d['owner']; rid=d['request_id']
        emit('request_ready',d['enqueue_ns'],owner,{'attempt_id':rid},request_id=rid)
        emit('request_enqueue',d['enqueue_ns'],owner,{},request_id=rid)
        emit('request_dispatch',d['dispatch_ns'],owner,{},request_id=rid)
    coverage=[]
    for t,kind,d in timelines:
        owner=d.get('owner')
        if kind=='workflow_submit':
            emit(kind,t,owner,{})
        elif kind=='lookup':
            rid=d['request_id']
            source=f"{plan['expected_stream_ids'][1]}:{d['backend_id']}"
            emit('cache_lookup',t,owner,{
                'prompt_tokens':len(d['tokens']),'cached_tokens':d['hit'],
                'executed_prefill_tokens':len(d['tokens'])-d['hit'],
                'backend_lookup':{'prompt_token_ids':d['tokens'],'compatibility_id':compatibility,
                                  'source_event_id':source}},request_id=rid)
            for episode,info in list(open_episodes.items()):
                if info['owner']==owner and d['tokens'][:len(info['tokens'])]==info['tokens']:
                    emit('legal_demand',t,owner,{'compatibility_id':compatibility,
                        'request_token_ids':d['tokens']},request_id=rid,idle_episode_id=episode)
                    del open_episodes[episode]
            coverage.append({'request_id':rid,'owner':owner,'mapped':True,'reason':d['reason'],
                             'hit':d['hit'],'source':source,'physical_release':bool(d['release_cells'])})
        elif kind=='release':
            rid=d['request_id']
            emit('request_end',t,owner,{'status':d['reason']},request_id=rid)
            last_release[owner]=t
            n=len(d['tokens'])//16*16
            if d['reason'] in ('eog','protocol_stop') and n and owner not in retired:
                episode=f'p{ep_index}';ep_index+=1
                prefix=d['tokens'][:n]
                emit('idle_start',t,owner,{'context_id':owner,'owner_id':owner,'context_version':'v1',
                    'prefix_id':hashlib.sha256(json.dumps(prefix).encode()).hexdigest(),
                    'compatibility_id':compatibility,'prefix_token_ids':prefix,
                    'source_request_id':rid,'actual_cells':d['release_cells'][:n]},
                     idle_episode_id=episode)
                open_episodes[episode]={'owner':owner,'tokens':prefix}
        elif kind=='context_retire':
            if owner in retired or last_release.get(owner,0)>=t:
                raise ValueError('duplicate retire or retire before final related release')
            retired.add(owner)
            for episode,info in list(open_episodes.items()):
                if info['owner']==owner:
                    emit('context_retire',t,owner,{'reason':d['reason']},idle_episode_id=episode)
                    del open_episodes[episode]
        elif kind in ('tool_start','tool_end'):
            active=[e for e,i in open_episodes.items() if i['owner']==owner]
            kwargs={'idle_episode_id':active[-1]} if active else {}
            emit(kind,t,owner,{'tool_id':d['tool_id'],'tool_kind':d['tool_name']},**kwargs)
    ends=[r for r in controller if r['kind']=='observation_end']
    if len(ends)!=1:
        raise ValueError('missing observation boundary')
    end=ends[0]['ts_ns']
    if any(r['data']['release_ns']>=end for r in requests):
        raise ValueError('observation ended before producers drained')
    emit('observation_end',end,run_id,{'complete':True})
    # Same timestamp retains insertion order; request phase events precede native lookup.
    events.sort(key=lambda e:e['ts_ns'])
    validate_events(events)
    labels=derive_idle_labels(events)
    snapshots=[]
    for event in events:
        if event['event_type']!='idle_start':continue
        item=snapshot(events,run_id,event['idle_episode_id'],event['ts_ns'])
        validate_features(item['features'])
        snapshots.append(item)
    for event in events:
        if event['event_type']!='tool_start' or 'idle_episode_id' not in event:continue
        item=snapshot(events,run_id,event['idle_episode_id'],event['ts_ns'])
        validate_features(item['features'])
        snapshots.append(item)
    # Frozen elapsed grid. Features project only the prefix through each timestamp.
    by_episode={label['idle_episode_id']:label for label in labels}
    for event in events:
        if event['event_type']!='idle_start':continue
        episode=event['idle_episode_id']
        for seconds in (1,3,10,30,60,120):
            t0=event['ts_ns']+seconds*10**9
            if t0>=by_episode[episode]['end_ns']:continue
            item=snapshot(events,run_id,episode,t0)
            validate_features(item['features'])
            snapshots.append(item)
    snapshots.sort(key=lambda s:(s['snapshot_ns'],s['idle_episode_id']))
    summary={'status':'passed','scope':'single_dense_sequence_no_preemption',
             'transport':transport,'requests':len(requests),'tool_results_consumed':len(consumed),
             'tool_results_unconsumed':unconsumed,
             'labels':dict(Counter(x['outcome'] for x in labels)), 'episodes':len(labels),
             'snapshot_count':len(snapshots),'unmapped':0,'unexplained':0,
             'cell_blocks':'analysis groups, not vLLM allocations'}
    if export:
        for name,items in [('events',events),('episodes',labels),('snapshots',snapshots),('coverage',coverage)]:
            with (root/(name+'.jsonl')).open('x') as stream:
                for item in items:stream.write(json.dumps(item)+'\n')
        (root/'audit.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary
