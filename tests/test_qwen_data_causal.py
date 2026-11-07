"""No future labels or total tool time in the frozen elapsed-grid snapshots."""
import json
from pathlib import Path
import pytest
from leasepath.prediction_state import snapshot

def event(kind,t,data,episode=None):
    r={'schema_version':'0.1','run_id':'r','event_id':str(t),
       'event_type':kind,'ts_ns':t,'clock_domain':'monotonic:test','evidence_kind':'synthetic','data':data,
       'workflow_id':'w'}
    if episode:r['idle_episode_id']=episode
    return r

def test_future_tool_result_cannot_change_snapshot():
    data=[event('idle_start',10,{'context_id':'w','context_version':'v1','compatibility_id':'c',
                                'prefix_token_ids':[1]*16},'p'),
          event('tool_start',20,{'tool_id':'t','tool_kind':'compute'},'p'),
          event('tool_end',40,{'tool_id':'t','tool_kind':'compute','actual_total_s':99999},'p')]
    initial=snapshot(data,'r','p',30)
    data[-1]['data']['secret_label']='U'
    data[-1]['data']['actual_total_s']=1
    assert snapshot(data,'r','p',30)==initial
    assert initial['features']['phase']=='waiting_tool'
    assert set(initial['features'])=={'elapsed_idle_s','compatibility_id','prefix_tokens','tool_name',
                                     'tool_elapsed_s','phase','retry_count','is_queued','queue_length'}
