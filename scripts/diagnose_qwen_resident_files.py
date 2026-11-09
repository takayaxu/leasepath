"""Real file tracing outside timed JCT; retain every numerical answer and access."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.prediction_training import read_json,sha256
from leasepath.shared_experiment_residency import LockedInputs
from leasepath.resident_runtime import ResidentFiles
from probe_qwen_resident_tools import tasks_and_refs,run_group
from probe_qwen_cache_native import write


def main():
    p=argparse.ArgumentParser();p.add_argument('--preparation',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--reuse-traces',type=Path)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    plan=read_json(ROOT/'configs/prediction/qwen_shared_server_paired_2026-11-09.json')
    tasks,refs=tasks_and_refs(plan)
    inputs=LockedInputs(tasks,a.output/'inputs')
    try:
        os.environ['LEASEPATH_TRACE_FILES']='1'
        trace_root=a.reuse_traces or a.output/'traced'
        traced=read_json(trace_root/'summary.json') if a.reuse_traces else run_group(tasks,refs,plan,trace_root)
        accessed=set();special=set()
        for trace in trace_root.rglob('*strace*'):
            for line in trace.read_text().splitlines():
                # Successful open/read/mmap descriptors only; failed path probes
                # and metadata-only stat calls do not imply file data reads.
                if not any(key in line for key in ('openat(','open(','read(','pread64(','mmap(')):continue
                for name in re.findall(r'\b[0-9]+<(/[^<>]+)>',line):
                    file=Path(name)
                    try:
                        if file.is_file() and not name.startswith(('/proc/','/sys/','/dev/shm/')):accessed.add(file.resolve())
                        else:special.add(name)
                    except OSError:continue
        system=read_json(a.preparation/'system-residency.json')
        prior={r['path']:r for r in system['files']}
        for file in accessed:
            prior[str(file)]={'path':str(file),'bytes':file.stat().st_size,'sha256':sha256(file),'actual_tool_trace':True}
        system['files']=[prior[k] for k in sorted(prior)]
        system['trace_special_paths']=sorted(special)
        write(a.preparation/'system-residency.json',system)
        runtime=read_json(a.preparation/'runtime-copy.json')
        paths=[Path(runtime['dst'])/r['relative'] for r in runtime['files']]+[Path(r['path']) for r in system['files']]
        guard=ResidentFiles(paths)
        try:
            os.environ.pop('LEASEPATH_TRACE_FILES')
            groups=[]
            for index in range(3):groups.append(run_group(tasks,refs,plan,a.output/f'locked-{index}'))
            require=[]
            for group in groups:
                for row in group['rows']:
                    call=row['call'];d=call.get('file_diagnostics',{});usage=call.get('child_lifetime')
                    if not row['correct'] or not usage or usage['major_faults'] or usage['block_input_ops'] or d.get('io_since_exec',{}).get('read_bytes',-1):
                        require.append({'task':row['task'],'correct':row['correct'],'usage':usage,'io':d.get('io_since_exec')})
            result={'status':'passed' if not require else 'failed','traced_correct':all(r['correct'] for r in traced['rows']),
                'locked_compute_calls':9,'failures':require,'residency':guard.verify(),
                'actual_disk_files':sorted(map(str,accessed)),'traced_evidence':str(trace_root),
                'attribution':'strace file access plus import-window rusage. Historical missing file cannot be reconstructed; no kernel page-fault address access.',
                'system_manifest_sha256':sha256(a.preparation/'system-residency.json')}
            write(a.output/'summary.json',result);print(json.dumps(result),flush=True)
            if result['status']!='passed' or not result['traced_correct']:raise RuntimeError('real residency diagnosis failed')
        finally:guard.close()
    finally:inputs.close()

if __name__=='__main__':main()
