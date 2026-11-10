import json
from pathlib import Path
import os
import subprocess
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
LAUNCHER=ROOT/'.artifacts/pressure-preexec-v1-20261110/launcher'


def run(args):
    env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'PYTHONDONTWRITEBYTECODE':'1'}
    return subprocess.run([str(LAUNCHER),*args],capture_output=True,text=True,env=env,timeout=20)


def test_exec_inherits_affinity_numa_thp_and_limits():
    r=run(['14,42','1','12','--',sys.executable,'-m','leasepath.preexec_worker_v1','--verify-only'])
    assert r.returncode==0,r.stderr
    d=json.loads(r.stdout)
    assert d['python_entry']['cpus']==[14,42]
    assert d['python_entry']['node_mask']==2
    assert d['python_entry']['thp_disabled']
    assert d['python_entry']['memory_limit_bytes']==12*1024**3
    assert d['python_entry']['pid']==d['launcher']['pid']


@pytest.mark.parametrize('cpus,node,memory',[('14,,42','1','12'),('14,14','1','12'),('14,42','2','12'),('14,42','1','13'),('14,42','1','0')])
def test_invalid_controls_fail_before_exec(cpus,node,memory):
    r=run([cpus,node,memory,'--',sys.executable,'-c','print("SHOULD_NOT_EXEC")'])
    assert r.returncode==125 and 'SHOULD_NOT_EXEC' not in r.stdout
