"""Bounded candidate evaluation worker; no reference-solver import or network."""
import json
import os
from pathlib import Path
import resource
import time
from .independent_agent_tasks import evaluate


def main():
    import sys
    payload=json.load(sys.stdin)
    controls=payload['controls'];os.sched_setaffinity(0,controls['cpus'])
    resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
    resource.setrlimit(resource.RLIMIT_CPU,(240,240))
    before=resource.getrusage(resource.RUSAGE_SELF);start=time.monotonic_ns()
    try:result=evaluate(payload['task'],payload['candidate'])
    except (ValueError,TypeError,KeyError,IndexError) as exc:result={'error':f'{type(exc).__name__}: {exc}'}
    after=resource.getrusage(resource.RUSAGE_SELF)
    io={k:int(v) for k,v in (line.split(':') for line in Path('/proc/self/io').read_text().splitlines())}
    print(json.dumps({'result':result,'evidence':{'wall_ns':time.monotonic_ns()-start,'cpu_s':after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,
        'major_faults_since_exec':after.ru_majflt,'read_bytes_since_exec':io['read_bytes'],'affinity':sorted(os.sched_getaffinity(0)),
        'maps':Path('/proc/self/maps').read_text(),'scope':'diagnostic candidate computation; NUMA/THP and full residency not yet accepted for natural JCT'}},allow_nan=False))

if __name__=='__main__':main()
