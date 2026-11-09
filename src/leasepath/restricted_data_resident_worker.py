"""Versioned worker: same numeric code, import-window and complete I/O evidence.

A module window with major faults is a bounded attribution, not a kernel fault
address. strace observes file accesses, not memory-mapped major fault events.
"""
import importlib.machinery
import json
import os
from pathlib import Path
import resource
import sys
import time


def counters():
    r = resource.getrusage(resource.RUSAGE_SELF)
    io = {k:int(v) for k,v in (line.split(':') for line in Path('/proc/self/io').read_text().splitlines())}
    return {'major_faults':r.ru_majflt,'minor_faults':r.ru_minflt,'read_bytes':io['read_bytes'],'rchar':io['rchar']}


def main():
    windows = []
    opened = []
    active = []
    def audit(event,args):
        if event == 'open' and args and isinstance(args[0],(str,bytes)):
            path = os.fsdecode(args[0])
            if not path.startswith('/proc/'):
                opened.append({'path':path,'active_imports':list(active)})
    sys.addaudithook(audit)
    def wrap(original):
        def observed(loader,*args,**kwargs):
            origin = getattr(loader,'path',None)
            active.append(origin)
            before = counters(); began = time.monotonic_ns(); first = len(opened)
            try:
                return original(loader,*args,**kwargs)
            finally:
                after = counters()
                delta = {k:after[k]-before[k] for k in before}
                if delta['read_bytes'] or delta['major_faults']:
                    windows.append({'loader':type(loader).__name__,'method':original.__name__,'origin':origin,
                        'duration_ns':time.monotonic_ns()-began,'delta':delta,
                        'opened_paths':sorted(set(r['path'] for r in opened[first:])),
                        'scope':'nested import window; not unique kernel page-fault file attribution'})
                active.pop()
        return observed
    for cls,methods in ((importlib.machinery.SourceFileLoader,('get_code',)),
                        (importlib.machinery.ExtensionFileLoader,('create_module','exec_module'))):
        for method in methods:
            setattr(cls,method,wrap(getattr(cls,method)))
    from contextlib import redirect_stdout
    import io
    from . import restricted_data_thread_probe_worker as worker
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        worker.main()
    envelope = json.loads(buffer.getvalue())
    envelope['file_diagnostics'] = {'python_executable':sys.executable,'prefix':sys.prefix,
        'sys_path':sys.path,'io_since_exec':counters(),'import_io_windows':windows,
        'opened':opened,'maps':Path('/proc/self/maps').read_text(),
        'tracing_enabled':os.environ.get('LEASEPATH_TRACE_FILES')=='1',
        'attribution_limit':'Python audit excludes ELF loader opens; preparation strace covers those. Import windows can overlap. No privileged kernel fault-address attribution.'}
    print(json.dumps(envelope,allow_nan=False))


if __name__ == '__main__':
    main()
