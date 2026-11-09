import os
from pathlib import Path
import numpy as np
import pytest
from leasepath.restricted_data_tools import Resource,SessionTools,file_hash
from leasepath.restricted_data_single_thread_tools import SingleThreadSessionTools
from leasepath.qwen_data_tasks import equal


def test_single_thread_preserves_real_ridge_without_active_blas_helpers(tmp_path):
    cpu=min(os.sched_getaffinity(0)); node=next(int(p.name[4:]) for p in Path(f'/sys/devices/system/cpu/cpu{cpu}').glob('node*'))
    source=tmp_path/'input.npz';rng=np.random.default_rng(42);X=rng.normal(size=(200,8));np.savez(source,X=X,y=2*X[:,0])
    resources={'dtest':Resource(source,file_hash(source),'numpy',{})}
    control={'cpus':[cpu],'numa_node':node,'disable_thp':True}
    tools=SingleThreadSessionTools(tmp_path/'single',resources,['ridge_cv'],controls=control,session_key='test',threads=1)
    args={'dataset_id':'dtest','operation':'ridge_cv','folds':5,'alpha':1.}
    expected=SessionTools(tmp_path/'old',resources,['ridge_cv'],threads=2).call('compute',args)
    answer=tools.call('compute',args)
    assert set(answer)=={'artifact_id','result'}
    assert equal(expected['result'],answer['result'])
    diag=tools.calls[0]['thread_diagnostics']
    # Some base runtimes retain a sleeping OpenMP management thread even at one.
    initial=set(diag['samples'][0]['threads'])
    extra_cpu=sum(v['schedstat'][0] for tid,v in diag['samples'][-1]['threads'].items() if tid not in initial)
    assert extra_cpu<20_000_000
    assert all(p['num_threads']==1 for p in diag['pools_after'])
    assert 'thread_diagnostics' not in answer
    assert 'error' in tools.call('compute',{**args,'threads':8})
    with pytest.raises(ValueError):
        SingleThreadSessionTools(tmp_path/'invalid',resources,['ridge_cv'],controls=control,session_key='test',threads=2)
