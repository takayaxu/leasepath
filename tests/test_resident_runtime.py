import os
from pathlib import Path
import sys
import pytest
from leasepath.resident_runtime import ResidentFiles
from leasepath.resident_worker_process import UsagePopen,worker_command


def test_residency_deduplicates_inode_and_rejects_replacement(tmp_path):
    source=tmp_path/'pages';source.write_bytes(bytes(8193));alias=tmp_path/'alias';alias.symlink_to(source)
    guard=ResidentFiles([source,alias])
    try:
        assert guard.verify()=={'files':1,'pages':3,'nonresident_pages':0}
        replacement=tmp_path/'other';replacement.write_bytes(bytes(8193));replacement.replace(source)
        with pytest.raises(ValueError,match='identity changed'):guard.verify()
    finally:guard.close()


def test_wait4_captures_startup_and_exit_resources():
    import subprocess
    proc=UsagePopen([sys.executable,'-c','a=bytearray(32*1024*1024); print(len(a))'],stdout=subprocess.PIPE,text=True)
    stdout,_=proc.communicate(timeout=30)
    assert proc.returncode==0 and stdout.strip()=='33554432'
    assert proc.lifetime_usage['minor_faults']>1000
    assert proc.lifetime_usage['major_faults']>=0


def test_ptrace_diagnostic_is_opt_in(monkeypatch,tmp_path):
    monkeypatch.delenv('LEASEPATH_TRACE_FILES',raising=False)
    assert worker_command(tmp_path,'test')[0]==sys.executable
    monkeypatch.setenv('LEASEPATH_TRACE_FILES','1')
    cmd=worker_command(tmp_path,'test')
    assert cmd[0]=='/usr/bin/strace' and str(tmp_path/'test-files.strace') in cmd


def test_real_worker_preserves_answer_and_records_file_lifetime(tmp_path):
    import numpy as np
    from leasepath.restricted_data_tools import Resource,SessionTools,file_hash
    from leasepath.restricted_data_resident_tools import ResidentSessionTools
    from leasepath.qwen_data_tasks import equal
    cpu=min(os.sched_getaffinity(0))
    node=next(int(p.name[4:]) for p in Path(f'/sys/devices/system/cpu/cpu{cpu}').glob('node*'))
    source=tmp_path/'input.npz';rng=np.random.default_rng(42);X=rng.normal(size=(200,8));np.savez(source,X=X,y=2*X[:,0])
    resources={'dtest':Resource(source,file_hash(source),'numpy',{})}
    args={'dataset_id':'dtest','operation':'ridge_cv','folds':5,'alpha':1.}
    expected=SessionTools(tmp_path/'old',resources,['ridge_cv'],threads=1).call('compute',args)
    tool=ResidentSessionTools(tmp_path/'new',resources,['ridge_cv'],threads=1,session_key='test',
        controls={'cpus':[cpu],'numa_node':node,'disable_thp':True})
    answer=tool.call('compute',args)
    assert set(answer)=={'artifact_id','result'} and equal(answer['result'],expected['result'])
    call=tool.calls[0]
    assert call['child_lifetime']['minor_faults']>=call['worker_timing']['overall']['minor_faults']
    assert call['file_diagnostics']['python_executable']==sys.executable
    assert not call['file_diagnostics']['tracing_enabled']
    assert call['file_diagnostics']['opened'] and call['file_diagnostics']['maps']
    assert call['file_diagnostics']['io_since_exec']['read_bytes']>=call['worker_timing']['overall']['read_bytes']


def test_keeper_checks_file_and_exits_without_orphan(tmp_path):
    from leasepath.resident_runtime import ModelResidentKeeper
    file=tmp_path/'file';file.write_bytes(bytes(12345))
    keeper=ModelResidentKeeper(file)
    pid=keeper.proc.pid
    try:
        assert keeper.initial['records'][0]['mapping']=='PROT_READ MAP_SHARED'
        assert keeper.verify()=={'files':1,'pages':4,'nonresident_pages':0}
        assert keeper.proc.poll() is None
    finally:keeper.close()
    assert keeper.proc.poll()==0


def test_runtime_rejects_changed_copy(tmp_path):
    from leasepath.resident_runtime import ResidentRuntime
    source=tmp_path/'file';source.write_bytes(b'wrong')
    with pytest.raises(ValueError,match='runtime copy changed'):
        ResidentRuntime({'dst':str(tmp_path),'files':[{'relative':'file','sha256':'0'*64}]})
