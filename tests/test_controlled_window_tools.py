import json
import time
import pytest
from leasepath.controlled_window_tools import WindowSessionTools,ToolWindowOverrun
from leasepath.restricted_data_single_thread_tools import SingleThreadSessionTools


def make(tmp_path,budget):
    return WindowSessionTools(tmp_path,{},[],threads=1,session_key='test',
        controls={'cpus':[0],'numa_node':0,'disable_thp':True},response_windows={'compute':budget})


def fake_compute(self,name,args):
    result={'artifact_id':'atest','result':{'value':7}}
    self.calls.append({'started_ns':time.monotonic_ns(),'ended_ns':time.monotonic_ns(),'result':result})
    return result


def test_fixed_window_keeps_actual_result_and_records_added_wait(tmp_path,monkeypatch):
    monkeypatch.setattr(SingleThreadSessionTools,'call',fake_compute)
    tools=make(tmp_path/'a',.075)
    start=time.monotonic()
    result=tools.call('compute',{})
    assert time.monotonic()-start>=.075
    assert result=={'artifact_id':'atest','result':{'value':7}}
    saved=json.loads((tools.output/'calls.jsonl').read_text())
    assert saved['response_window']['added_wait_ns']>0
    assert not saved['response_window']['compute_overrun']


def test_overrun_is_preserved_and_fails_without_result_repair(tmp_path,monkeypatch):
    def slow(self,name,args):
        time.sleep(.02)
        return fake_compute(self,name,args)
    monkeypatch.setattr(SingleThreadSessionTools,'call',slow)
    tools=make(tmp_path/'b',.001)
    with pytest.raises(ToolWindowOverrun):tools.call('compute',{})
    saved=json.loads((tools.output/'calls.jsonl').read_text())
    assert saved['response_window']['compute_overrun']
    assert saved['result']['result']=={'value':7}
