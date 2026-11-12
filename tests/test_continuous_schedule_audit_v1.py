from copy import deepcopy
import pytest
from leasepath.continuous_schedule_audit_v1 import replay


def trajectory():
    def event(ts,kind,**data):return dict(ts_ns=ts,kind=kind,data=data)
    f=dict(elapsed_idle_s=1.,compatibility_id='runtime',prefix_tokens=128,
           tool_name='read_file',tool_elapsed_s=None,phase='waiting_tool',
           retry_count=0,is_queued=False,queue_length=1)
    return [event(1,'request_ready',owner='a'),event(2,'request_ready',owner='b'),
        event(3,'scheduler_snapshot',ready_owners=['a','b'],observed_features={},observed_monotonic_s=3.),
        event(4,'request_dispatch',owner='a'),event(5,'request_input',owner='a',request_id='r1'),
        event(7,'tool_submitted',owner='a',request_id='r1',tool_name='read_file',arguments={'path':'x'}),
        event(8,'scheduler_snapshot',ready_owners=['b'],observed_features={'a':f},observed_monotonic_s=8.),
        event(9,'tool_start',owner='a',tool_name='read_file',actual_observed_monotonic_s=9.),
        event(10,'scheduler_snapshot',ready_owners=['b'],observed_features={'a':{**f,'tool_elapsed_s':1.}},observed_monotonic_s=10.),
        event(11,'tool_end',owner='a',tool_name='read_file')], [event(6,'engine_request',owner='a',request_id='r1',text='actual call')]


def parse(text):
    assert text=='actual call'
    return 'read_file',{'path':'x'}


def test_submission_wait_has_no_execution_elapsed_until_observed_start():
    c,b=trajectory()
    assert set(replay(c,b,parse))=={'r1'}


@pytest.mark.parametrize('change',['missing_submission','future_elapsed','wrong_native_args','early_start'])
def test_replay_rejects_missing_or_future_evidence(change):
    c,b=deepcopy(trajectory())
    if change=='missing_submission':c.pop(5)
    elif change=='future_elapsed':c[6]['data']['observed_features']['a']['tool_elapsed_s']=0.
    elif change=='wrong_native_args':c[5]['data']['arguments']={'path':'secret'}
    elif change=='early_start':c[5],c[7]=c[7],c[5]
    with pytest.raises((AssertionError,KeyError)):replay(c,b,parse)


def test_historical_submission_comes_from_actual_native_output():
    c,b=trajectory();c.pop(5)
    c[5]['data']['observed_features']['a']['tool_name']=None
    assert set(replay(c,b,parse,explicit=False))=={'r1'}
