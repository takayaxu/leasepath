"""Replay only controller-observed state, with native evidence for submissions."""
from .prediction_state import validate_features


def replay(controller, backend, parse_call, *, explicit=True):
    requests={e['data']['request_id']:e for e in backend if e['kind']=='engine_request'}
    ready=set();phase={};running={};last_tool={};pending={};snapshot=None;dispatch={}
    events=list(controller)
    if not explicit:
        # Historical controllers changed phase after parsing native output but
        # did not emit submission. Reconstruct solely from that actual output;
        # do not derive ready/running state from future completion timestamps.
        for event in backend:
            if event['kind']!='engine_request':continue
            try:name,args=parse_call(event['data']['text'])
            except (ValueError,TypeError):continue
            events.append(dict(ts_ns=event['ts_ns'],kind='legacy_tool_submitted',
                data=dict(owner=event['data']['owner'],request_id=event['data']['request_id'],tool_name=name,arguments=args)))
        events.sort(key=lambda e:e['ts_ns'])
    for event in events:
        kind,data=event['kind'],event['data'];owner=data.get('owner')
        if kind=='request_ready':
            assert owner not in ready
            ready.add(owner);phase[owner]='queued'
        elif kind in ('tool_submitted','legacy_tool_submitted'):
            assert phase[owner]=='generating' and owner not in pending
            native=requests[data['request_id']]
            assert native['data']['owner']==owner and native['ts_ns']<=event['ts_ns']
            assert parse_call(native['data']['text'])==(data['tool_name'],data['arguments'])
            phase[owner]='waiting_tool';pending[owner]=data['tool_name']
            if kind=='tool_submitted':last_tool[owner]=data['tool_name']
        elif kind=='tool_start':
            assert phase[owner]=='waiting_tool' and pending[owner]==data['tool_name']
            assert owner not in running
            running[owner]=data['actual_observed_monotonic_s'];last_tool[owner]=data['tool_name']
        elif kind=='tool_end':
            assert owner in running and pending.pop(owner)==data['tool_name']
            running.pop(owner);phase[owner]='queued'
        elif kind=='user_wait':phase[owner]='waiting_user'
        elif kind=='context_retire':ready.discard(owner);phase[owner]='retired'
        elif kind=='scheduler_snapshot':
            assert set(data['ready_owners'])==ready and len(data['ready_owners'])==len(ready)
            for name,f in data['observed_features'].items():
                validate_features(f)
                assert f['is_queued']==(name in ready) and f['queue_length']==len(ready)
                assert f['phase']==phase[name] and f['tool_name']==last_tool.get(name), (event.get('seq'),name,f,phase[name],last_tool.get(name))
                if name in running:
                    assert abs(f['tool_elapsed_s']-(data['observed_monotonic_s']-running[name]))<1e-8
                else:assert f['tool_elapsed_s'] is None
            snapshot=data
        elif kind=='request_dispatch':
            assert snapshot['ready_owners'][0]==owner
            ready.remove(owner);phase[owner]='generating'
        elif kind=='request_input':dispatch[data['request_id']]=snapshot
    assert not pending and not running
    return dispatch
