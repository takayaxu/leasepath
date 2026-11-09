"""Bounded real three-tool stability gate, frozen before performance is observed."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
import json
import mmap
import os
from pathlib import Path
import shutil
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from leasepath.controlled_resources import host_sample, psi_interval
from leasepath.prediction_training import read_json, sha256
from leasepath.qwen_data_tasks import equal
from leasepath.restricted_data_tools import Resource
from leasepath.restricted_data_resident_tools import ResidentSessionTools as ControlledSessionTools
from probe_qwen_cache_native import write


def tasks_and_refs(plan):
    tasks, refs = {}, {}
    for name in ('qwen-data-tasks-v5', 'qwen-data-tasks-extension38-v2'):
        directory = ROOT/'.artifacts'/name
        tasks.update({t['id']: t for t in read_json(directory/'tasks.private.json') if t['id'] in plan['live_tasks']})
        refs.update(read_json(directory/'references.private.json'))
    return [tasks[name] for name in plan['live_tasks']], refs


class LockedInputs:
    """Retain verified mlocked tmpfs input mappings for the entire measurement."""
    def __init__(self, tasks, directory):
        directory.mkdir(parents=True, exist_ok=False)
        self.maps, self.records = [], []
        libc = ctypes.CDLL(None, use_errno=True)
        libc.mlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        for task in tasks:
            source = Path(task['file'])
            dest = directory/(task['id']+'.npz')
            shutil.copyfile(source, dest)
            digest = sha256(dest)
            if digest != task['input_sha256']:
                raise ValueError('input copy hash differs')
            dest.chmod(0o444)
            with dest.open('rb') as stream:
                mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_COPY)
            pointer = ctypes.addressof(ctypes.c_char.from_buffer(mapping))
            if libc.mlock(pointer, len(mapping)):
                raise OSError(ctypes.get_errno(), 'mlock verified input')
            self.maps.append(mapping)
            self.records.append({'task':task['id'], 'source':str(source), 'path':str(dest),
                                 'sha256':digest, 'bytes':len(mapping), 'mlock':True})
            task['file'] = str(dest)

    def close(self):
        for mapping in self.maps:
            mapping.close()
        self.maps.clear()


class LockedRuntime:
    def __init__(self, manifest):
        root = Path(manifest['dst'])
        self.maps = []
        libc = ctypes.CDLL(None, use_errno=True)
        libc.mlock.argtypes = [ctypes.c_void_p,ctypes.c_size_t]
        for row in manifest['files']:
            path = root/row['relative']
            if sha256(path)!=row['sha256']: raise ValueError('runtime copy changed')
            if not row['bytes']: continue
            with path.open('rb') as stream:
                mapping = mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_COPY)
            pointer = ctypes.addressof(ctypes.c_char.from_buffer(mapping))
            if libc.mlock(pointer,len(mapping)): raise OSError(ctypes.get_errno(),'mlock runtime')
            self.maps.append(mapping)

    def close(self):
        for mapping in self.maps: mapping.close()
        self.maps.clear()


def run_group(tasks, refs, plan, output):
    output.mkdir(parents=True, exist_ok=False)
    before = host_sample()
    def run(task):
        tools = ControlledSessionTools(output/task['id'],
            {task['dataset_id']:Resource(Path(task['file']), task['input_sha256'],task['kind'],task['metadata'])},
            task['operations'], timeout=240, threads=plan['tool_threads'], memory_gib=12,
            controls=plan['tool_controls'][task['id']], session_key=task['id'])
        args = dict(dataset_id=task['dataset_id'], operation=task['operation'], **task['parameters'])
        answer = tools.call('compute', args)
        correct = 'result' in answer and equal(answer['result'], refs[task['id']]['reference'])
        return {'task':task['id'], 'correct':correct, 'call':tools.calls[0]}
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(run, tasks))
    result = {'before':before, 'after':host_sample(), 'rows':rows}
    write(output/'summary.json', result)
    for row in rows:
        call = row['call']
        print(output.name, row['task'], row['correct'], 'wall_s',round((call['ended_ns']-call['started_ns'])/1e9,3),
              'kernel_s',round(call.get('worker_timing',{}).get('overall',{}).get('system_ns',0)/1e9,3),flush=True)
    return result


from probe_qwen_controlled_tools import qualify


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan', type=Path, default=ROOT/'configs/prediction/qwen_cache_controlled_v2_2026-11-09.json')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--ram-root', type=Path, required=True)
    p.add_argument('--runtime-copy',type=Path,required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    args.ram_root.mkdir(parents=True, exist_ok=False)
    plan = read_json(args.plan)
    sources = [Path(__file__).resolve(),args.plan,ROOT/'src/leasepath/controlled_resources.py',
               ROOT/'src/leasepath/restricted_data_controlled_tools.py',ROOT/'src/leasepath/restricted_data_controlled_worker.py',
               ROOT/'src/leasepath/restricted_data_extended.py',ROOT/'src/leasepath/restricted_data_tools.py',
               ROOT/'scripts/probe_qwen_controlled_tools.py',
               ROOT/'src/leasepath/restricted_data_single_thread_tools.py',
               ROOT/'src/leasepath/restricted_data_thread_probe_worker.py',args.runtime_copy]
    for name in ('qwen-data-tasks-v5','qwen-data-tasks-extension38-v2'):
        sources.extend(ROOT/'.artifacts'/name/file for file in ('tasks.private.json','references.private.json'))
    hashes = {str(p):sha256(p) for p in sources}
    write(args.output/'frozen-plan.json', {**plan, 'source_sha256':hashes})
    tasks, refs = tasks_and_refs(plan)
    runtime = read_json(args.runtime_copy)
    locked_runtime = LockedRuntime(runtime)
    write(args.output/'runtime-copy.json',runtime)
    inputs = LockedInputs(tasks,args.ram_root/'inputs')
    write(args.output/'input-copies.json', inputs.records)
    try:
        for i in range(plan['preflight']['warmups']):
            group = run_group(tasks, refs, plan, args.ram_root/f'warmup-{i}')
            if not all(r['correct'] for r in group['rows']):
                raise ValueError('warmup reference failed')
        groups = []
        for i in range(plan['preflight']['repeats']):
            for name,digest in hashes.items():
                if sha256(name)!=digest: raise ValueError('frozen source changed')
            group = run_group(tasks, refs, plan, args.ram_root/f'measured-{i}')
            groups.append(group)
            if not all(r['correct'] for r in group['rows']): break
        summary = qualify(groups,plan)
        write(args.ram_root/'summary.json',summary)
    finally:
        # Copying evidence and releasing mlock happens only after the last measurement.
        shutil.copytree(args.ram_root,args.output/'raw-ram-evidence')
        inputs.close()
        locked_runtime.close()
    write(args.output/'summary.json',summary)
    print(json.dumps(summary),flush=True)


if __name__=='__main__': main()
