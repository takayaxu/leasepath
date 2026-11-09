import os
from pathlib import Path
import numpy as np
import pytest
from leasepath.restricted_data_tools import Resource, SessionTools, file_hash
from leasepath.restricted_data_controlled_tools import ControlledSessionTools


def test_controlled_child_equivalence_binding_and_session_capabilities(tmp_path):
    source=tmp_path/'input.npz'
    rng=np.random.default_rng(42)
    X=rng.normal(size=(160,8))
    np.savez(source,X=X,y=X[:,0]*2)
    resources={'dtest':Resource(source,file_hash(source),'numpy',{})}
    affinity=os.sched_getaffinity(0)
    cpu=min(affinity)
    node=next(int(p.name[4:]) for p in Path(f'/sys/devices/system/cpu/cpu{cpu}').glob('node*'))
    controls={'cpus':[cpu],'numa_node':node,'disable_thp':True}
    thp=Path('/proc/self/status').read_text().split('THP_enabled:')[1].splitlines()[0]
    def make(name,key):
        return ControlledSessionTools(tmp_path/name,resources,['ridge_cv'],controls=controls,session_key=key)
    a,b,c=make('a','same'),make('b','same'),make('c','other')
    args={'dataset_id':'dtest','operation':'ridge_cv','folds':5,'alpha':1.}
    expected=SessionTools(tmp_path/'old',resources,['ridge_cv']).call('compute',args)
    first=a.call('compute',args); paired=b.call('compute',args); foreign=c.call('compute',args)
    assert first['result']==expected['result']==paired['result']==foreign['result']
    assert first['artifact_id']==paired['artifact_id']!=foreign['artifact_id']
    assert set(first)=={'artifact_id','result'}
    assert 'error' in c.call('view_artifact',{'artifact_id':first['artifact_id'],'offset':0,'limit':1})
    assert 'error' in a.call('compute',{**args,'controls':{'cpus':[1]}})
    for state in a.calls[0]['worker_controls'].values():
        assert state['cpus']==[cpu] and state['node_mask']==1<<node
        assert state['thp_enabled']=='0' and state['memory_policy']==2
    assert os.sched_getaffinity(0)==affinity
    assert Path('/proc/self/status').read_text().split('THP_enabled:')[1].splitlines()[0]==thp
    source.write_bytes(b'changed')
    assert 'error' in a.call('inspect_input',{'dataset_id':'dtest'})
    assert os.sched_getaffinity(0)==affinity


def test_affinity_restored_when_evidence_write_raises(tmp_path):
    prior=os.sched_getaffinity(0)
    tools=ControlledSessionTools(tmp_path/'a',{},[],controls={'cpus':[min(prior)],'numa_node':0,'disable_thp':True},session_key='a')
    tools.output.rmdir()
    with pytest.raises(FileNotFoundError): tools.call('unknown',{})
    assert os.sched_getaffinity(0)==prior
