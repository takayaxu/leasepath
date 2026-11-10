import json
import os
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]


def test_future_allocations_locked_and_unknown_library_rejected():
    resolution=json.loads((ROOT/'.artifacts/pressure-preexec-v1-20261110/fixed-resolution.json').read_text())
    code='''import json,ctypes.util
from leasepath.preexec_worker_v2 import inherited_controls,fixed_resolver_and_lock
inherited_controls()
record,locked=fixed_resolver_and_lock()
before=locked()
allocation=bytearray(4*1024*1024)
after=locked()
assert after-before>=4096,(before,after)
assert ctypes.util.find_library('numa')==record['frozen_resolution']['numa']['path']
try:ctypes.util.find_library('unregistered-library')
except ValueError:pass
else:raise AssertionError('unknown library lookup allowed')
print(json.dumps({'before_kib':before,'after_kib':after,'unknown_lookup_rejected':True}))'''
    env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'LEASEPATH_LIBRARY_RESOLUTION_V2':json.dumps(resolution),'PYTHONDONTWRITEBYTECODE':'1'}
    r=subprocess.run([str(ROOT/'.artifacts/pressure-preexec-v1-20261110/launcher'),'14,42','1','12','--',sys.executable,'-c',code],env=env,capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stderr
    assert json.loads(r.stdout)['unknown_lookup_rejected']


def test_syscall_parser_uses_executable_not_nested_argv():
    sys.path.insert(0,str(ROOT/'scripts'))
    from audit_pressure_preexec_v3 import is_python_exec
    assert not is_python_exec('execve("/x/launcher", ["launcher", "/x/bin/python3.13"], []) = 0')
    assert is_python_exec('execve("/x/bin/python3.13", ["python3.13"], []) = 0')
