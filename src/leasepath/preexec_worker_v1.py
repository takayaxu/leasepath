"""Verify inherited resource controls before importing the original numerical worker."""
import resource
_FIRST=resource.getrusage(resource.RUSAGE_SELF)
import os
import sys
import json
import time
import ctypes
import io
from contextlib import redirect_stdout


def counters():
    u=resource.getrusage(resource.RUSAGE_SELF)
    with open('/proc/self/io') as f:io_values={k:int(v) for k,v in (line.split(':') for line in f)}
    return {'monotonic_ns':time.monotonic_ns(),'major_faults':u.ru_majflt,'block_input_ops':u.ru_inblock,'read_bytes':io_values['read_bytes']}


def inherited_controls():
    if sys.platform!='linux' or os.uname().machine!='x86_64':raise ValueError('frozen launcher requires Linux x86_64')
    evidence=json.loads(os.environ['LEASEPATH_PREEXEC_V1'])
    lib=ctypes.CDLL(None,use_errno=True);lib.syscall.restype=ctypes.c_long
    mode=ctypes.c_int();mask=ctypes.c_ulong()
    if lib.syscall(239,ctypes.byref(mode),ctypes.byref(mask),ctypes.c_ulong(64),ctypes.c_void_p(),0):raise OSError(ctypes.get_errno(),'get_mempolicy')
    thp=lib.prctl(42,ctypes.c_ulong(0),ctypes.c_ulong(0),ctypes.c_ulong(0),ctypes.c_ulong(0))
    actual={'pid':os.getpid(),'cpus':sorted(os.sched_getaffinity(0)),'memory_policy':mode.value,'node_mask':mask.value,'thp_disabled':thp==1,
        'memory_limit_bytes':resource.getrlimit(resource.RLIMIT_AS)[0],'cpu_limit_s':resource.getrlimit(resource.RLIMIT_CPU)[0]}
    if any(actual[k]!=evidence[k] for k in actual):raise ValueError('pre-exec controls did not survive exec')
    if actual['memory_policy']!=2 or actual['node_mask'] not in (1,2) or not actual['thp_disabled']:raise ValueError('unexpected inherited policy')
    return {'launcher':evidence,'python_entry':actual,'verified_before_legacy_worker_import':True}


def main():
    inherited=inherited_controls()
    if sys.argv[1:]==['--verify-only']:
        print(json.dumps(inherited));return
    checkpoints=[{'label':'first_after_resource_import','major_faults':_FIRST.ru_majflt,'block_input_ops':_FIRST.ru_inblock},
                 {'label':'inherited_controls_verified',**counters()}]
    from . import restricted_data_resident_tree_worker as worker
    checkpoints.append({'label':'legacy_tree_imported',**counters()})
    stream=io.StringIO()
    with redirect_stdout(stream):worker.main()
    checkpoints.append({'label':'legacy_tree_completed_and_serialized',**counters()})
    envelope=json.loads(stream.getvalue())
    inherited['boundaries']=checkpoints
    inherited['limits']='File residency is held by parent; inherited placement is not exclusive CPU/memory or proof of historical fault cause. Final wait4 covers execution beyond these samples.'
    envelope['preexec_controls']=inherited
    print(json.dumps(envelope,allow_nan=False))

if __name__=='__main__':main()
