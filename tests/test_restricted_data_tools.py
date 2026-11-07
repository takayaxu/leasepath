"""Boundary checks are necessary because model calls are untrusted parameters."""
import json
import os
from pathlib import Path
import threading
import pytest

from leasepath.restricted_data_tools import Resource, SessionTools,file_hash
from leasepath.qwen_data_ledger import GenerationBudget

def session(tmp_path):
    source=tmp_path/'input'
    source.write_text('immutable input')
    return SessionTools(tmp_path/'session',{'dtest':Resource(source,file_hash(source),'numpy',{'shape':[2,3]})},
                        ['pca','kmeans']),source

@pytest.mark.parametrize('name,args',[
  ('inspect_input',{'dataset_id':'../input'}),
  ('inspect_input',{'dataset_id':'dhidden'}),
  ('inspect_input',{'dataset_id':'dtest','path':'/etc/passwd'}),
  ('compute',{'dataset_id':'dtest','operation':'eval','code':'__import__("os")'}),
  ('compute',{'dataset_id':'dtest','operation':'pca','components':10**9}),
  ('compute',{'dataset_id':'dtest','operation':'pca','components':True}),
  ('compute',{'dataset_id':'dtest','operation':'pca','components':2,'url':'https://x'}),
  ('compute',{'dataset_id':'dtest','operation':'pca','components':2,'trees':10}),
  ('compute',{'dataset_id':'dtest','operation':'group_sum','group_by':'x"; DROP TABLE orders;--','value':'n'}),
  ('view_artifact',{'artifact_id':'aother','offset':0,'limit':10}),
  ('view_artifact',{'artifact_id':'aother','offset':0,'limit':10**9}),
])
def test_rejects_without_side_effect(tmp_path,name,args):
    tools,source=session(tmp_path)
    initial=source.read_bytes()
    result=tools.call(name,args)
    assert 'error' in result
    assert source.read_bytes()==initial
    assert not tools.artifacts

def test_source_integrity_and_symlink(tmp_path):
    tools,source=session(tmp_path)
    assert tools.call('inspect_input',{'dataset_id':'dtest'})['shape']==[2,3]
    original=source.read_text()
    source.write_text('changed')
    assert 'error' in tools.call('inspect_input',{'dataset_id':'dtest'})
    source.write_text(original)
    alias=tmp_path/'alias'
    source.rename(alias)
    source.symlink_to(alias)
    assert 'error' in tools.call('inspect_input',{'dataset_id':'dtest'})

def test_budget_atomic_and_bounded(tmp_path):
    budget=GenerationBudget(tmp_path/'ledger')
    threads=[threading.Thread(target=budget.reserve,args=('probe',)) for _ in range(20)]
    for t in threads:t.start()
    for t in threads:t.join()
    assert budget.summary()['probe']==20
    for _ in range(52):budget.reserve('probe')
    with pytest.raises(RuntimeError):budget.reserve('probe')
    for _ in range(72):budget.reserve('pilot')
    with pytest.raises(RuntimeError):budget.reserve('pilot')
    assert budget.summary()['generations']==144

def test_hash_check_leaves_input_at_start(tmp_path):
    from leasepath.restricted_data_tools import open_resource
    source=tmp_path/'source'
    source.write_bytes(b'input body')
    fd,_=open_resource(source,file_hash(source))
    try:
        assert os.read(fd,100)==b'input body'
    finally:
        os.close(fd)

def test_timeout_reaps_process_group(tmp_path):
    tools,source=session(tmp_path)
    tools.timeout=0.001
    result=tools.call('compute',{'dataset_id':'dtest','operation':'pca','components':1})
    assert result['error']=='TimeoutError'
    pid=tools.calls[-1]['child_pid']
    with pytest.raises(ProcessLookupError):
        os.killpg(pid,0)
