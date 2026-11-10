"""Diagnostic only: cover bootstrap and post-compute gaps in legacy counters.

Fault deltas identify temporal windows, never a unique kernel page address.
The original tool implementation, arguments and numerical dispatch are retained.
"""
import resource
_FIRST = resource.getrusage(resource.RUSAGE_SELF)
import os
import time
import sys
import json
import io
from contextlib import redirect_stdout
import importlib.machinery


def counters():
    u=resource.getrusage(resource.RUSAGE_SELF)
    with open('/proc/self/io') as f:
        values={k:int(v) for k,v in (line.split(':') for line in f)}
    return {'major_faults':u.ru_majflt,'minor_faults':u.ru_minflt,'block_input_ops':u.ru_inblock,
            'read_bytes':values['read_bytes'],'rchar':values['rchar'],'monotonic_ns':time.monotonic_ns()}


def main():
    boundaries=[{'label':'first_after_resource_import','major_faults':_FIRST.ru_majflt,'minor_faults':_FIRST.ru_minflt,'block_input_ops':_FIRST.ru_inblock},
                {'label':'diagnostic_bootstrap_imports_complete',**counters()}]
    imports=[];opened=[];active=[];fault_windows=[];last=counters()
    def boundary(label,**extra):
        nonlocal last
        current=counters();delta={k:current[k]-last[k] for k in ('major_faults','read_bytes','block_input_ops')}
        row={'label':label,**current,**extra};boundaries.append(row)
        if any(delta.values()):
            with open('/proc/self/maps') as f:maps=f.read()
            fault_windows.append({'from':boundaries[-2]['label'],'to':label,'delta':delta,'maps_at_end':maps,'active_imports':list(active)})
        last=current
    def audit(event,args):
        if event=='open' and args and isinstance(args[0],(str,bytes)):
            name=os.fsdecode(args[0])
            if not name.startswith(('/proc/','/sys/')):opened.append({'path':name,'at_ns':time.monotonic_ns(),'active_imports':list(active)})
    sys.addaudithook(audit)
    originals=[]
    def wrap(original):
        def measured(loader,*args,**kwargs):
            origin=getattr(loader,'path',None);active.append(origin);before=counters()
            try:return original(loader,*args,**kwargs)
            finally:
                after=counters();delta={k:after[k]-before[k] for k in ('major_faults','read_bytes','block_input_ops')}
                imports.append({'origin':origin,'method':original.__name__,'before':before,'after':after,'delta':delta})
                if any(delta.values()):boundary('import_fault_window_end',origin=origin,method=original.__name__)
                active.pop()
        return measured
    for cls,methods in ((importlib.machinery.SourceFileLoader,('get_code',)),(importlib.machinery.ExtensionFileLoader,('create_module','exec_module'))):
        for method in methods:
            original=getattr(cls,method);originals.append((cls,method,original));setattr(cls,method,wrap(original))
    tracked={'restricted_data_resident_tree_worker.py':{'main'},'restricted_data_resident_worker.py':{'main'},
        'restricted_data_thread_probe_worker.py':{'main'},'restricted_data_controlled_worker.py':{'main','execute','snapshot'},
        'controlled_resources.py':{'apply_child_controls','child_state'},'threadpoolctl.py':{'threadpool_info'},
        'ctypes/util.py':{'find_library'}}
    def profile(frame,event,arg):
        if event not in ('call','return'):return
        filename=frame.f_code.co_filename;fn=frame.f_code.co_name
        for suffix,names in tracked.items():
            if filename.endswith(suffix) and fn in names:
                boundary(event+':'+suffix+':'+fn);break
    sys.setprofile(profile)
    try:
        boundary('before_legacy_tree_import')
        from . import restricted_data_resident_tree_worker as worker
        boundary('after_legacy_tree_import')
        stream=io.StringIO()
        with redirect_stdout(stream):worker.main()
        boundary('after_legacy_tree_main_and_serialization')
        envelope=json.loads(stream.getvalue())
        boundary('after_outer_json_parse')
    finally:
        sys.setprofile(None)
        for cls,method,original in originals:setattr(cls,method,original)
    # Full inode list for actually mapped files, excluding virtual pseudo files.
    maps=[]
    with open('/proc/self/maps') as f:
        for line in f:
            fields=line.split(None,5)
            if len(fields)==6 and fields[5].startswith('/'):
                path=fields[5].strip()
                try:
                    st=os.stat(path);maps.append({'path':path,'dev':st.st_dev,'ino':st.st_ino,'bytes':st.st_size})
                except OSError:maps.append({'path':path,'unresolved':True})
    envelope['startup_fault_diagnostic']={'version':1,'boundaries':boundaries,'fault_windows':fault_windows,'imports':imports,'opened':opened,'mapped_files':maps,
        'first_snapshot_excludes':'ELF loader, Python interpreter startup, site initialization, package import and resource module import; their cumulative faults remain in first counter',
        'timing_scope':'diagnostic instrumentation; never natural JCT',
        'attribution_limit':'Temporal/import windows and access paths, not a faulting page address or uniquely attributed file'}
    print(json.dumps(envelope,allow_nan=False))

if __name__=='__main__':main()
