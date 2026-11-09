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
from leasepath.restricted_data_controlled_tools import ControlledSessionTools
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
            task['operations'], timeout=240, threads=2, memory_gib=12,
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


def qualify(groups, plan):
    rows = [r for g in groups for r in g['rows']]
    reasons = []
    if not all(r['correct'] and r['call']['status']=='ok' for r in rows):
        reasons.append('tool/reference validation failed')
    ridge = [(r['call']['ended_ns']-r['call']['started_ns'])/1e9 for r in rows if r['task']=='ridge_validation']
    cv = statistics.pstdev(ridge)/statistics.mean(ridge)
    ratio = max(ridge)/min(ridge)
    limits = plan['preflight']
    if cv > limits['ridge_cv_max']: reasons.append('ridge CV exceeds frozen limit')
    if ratio > limits['ridge_max_min_ratio_max']: reasons.append('ridge max/min exceeds frozen limit')
    waits = []
    for row in rows:
        call = row['call']
        if call['status']!='ok': continue
        overall = call['worker_timing']['overall']
        waits.append(overall['all_thread_runqueue_wait_ns']/overall['wall_ns'])
        expected = plan['tool_controls'][row['task']]
        for state in call['worker_controls'].values():
            if (state['cpus'] != expected['cpus'] or state['memory_policy'] != 2 or
                    state['node_mask'] != 1<<expected['numa_node'] or state['thp_enabled']!='0'):
                reasons.append('worker controls differ')
        for name,path in call['worker_controls']['after']['runtime_modules'].items():
            if path and not path.startswith('/dev/shm/leasepath-controlled-runtime-2026-11-09/'):
                reasons.append('numerical dependency loaded outside frozen RAM runtime')
        if overall['read_bytes'] or overall['major_faults']:
            reasons.append('worker disk read or major page fault')
    if max(waits,default=1) > limits['all_thread_runqueue_fraction_max']:
        reasons.append('all-thread runqueue/wall exceeds frozen limit')
    intervals = [{'cpu_psi_pct':psi_interval(g['before'],g['after'],'cpu'),
                  'io_psi_pct':psi_interval(g['before'],g['after'],'io'),
                  'memory_full_psi_pct':psi_interval(g['before'],g['after'],'memory','full')}
                 for g in groups]
    for interval,group in zip(intervals,groups):
        stat = [list(map(int,g['cpu_stat'].splitlines()[0].split()[1:9])) for g in (group['before'],group['after'])]
        delta = [b-a for a,b in zip(*stat)]
        cores = sum(1 for line in group['after']['cpu_stat'].splitlines() if line.startswith('cpu') and line[3:4].isdigit())
        interval['host_busy_cpu_equivalents'] = cores*(sum(delta)-delta[3]-delta[4])/sum(delta)
    host_limits = plan['resource_gate']
    memory = [s['meminfo_kib']['MemAvailable']/1024**2 for g in groups for s in (g['before'],g['after'])]
    memory_range = max(memory)-min(memory)
    if memory_range > host_limits['available_memory_range_gib_max']:
        reasons.append('host available-memory range exceeds frozen limit')
    for key, bound in [('cpu_psi_pct','cpu_psi_range_pp_max'),('io_psi_pct','io_psi_range_pp_max')]:
        values = [r[key] for r in intervals]
        if max(values)-min(values) > host_limits[bound]:
            reasons.append(key+' range exceeds frozen limit')
    if max(r['memory_full_psi_pct'] for r in intervals) > host_limits['memory_full_psi_max_pct']:
        reasons.append('host memory full PSI exceeds frozen limit')
    busy = [r['host_busy_cpu_equivalents'] for r in intervals]
    if max(busy)-min(busy)>host_limits['host_busy_cpu_range_max']:
        reasons.append('host CPU load range exceeds frozen limit')
    settings = [s['global_settings'] for g in groups for s in (g['before'],g['after'])]
    if any(s!=settings[0] for s in settings): reasons.append('global memory settings changed')
    return {'status':'passed' if not reasons else 'noise_gate_failed', 'reasons':sorted(set(reasons)),
            'ridge_wall_s':ridge, 'ridge_cv':cv,'ridge_max_min_ratio':ratio,
            'max_all_thread_runqueue_fraction':max(waits,default=None),
            'tool_calls':len(rows),'correct':sum(r['correct'] for r in rows),'new_model_generations':0,
            'host_intervals':intervals, 'available_memory_range_gib':memory_range,
            'no_outlier_exclusion':True, 'gate_frozen_before_observations':True}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan', type=Path, default=ROOT/'configs/prediction/qwen_cache_controlled_2026-11-09.json')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--ram-root', type=Path, required=True)
    p.add_argument('--runtime-copy',type=Path,required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    args.ram_root.mkdir(parents=True, exist_ok=False)
    plan = read_json(args.plan)
    sources = [Path(__file__).resolve(),args.plan,ROOT/'src/leasepath/controlled_resources.py',
               ROOT/'src/leasepath/restricted_data_controlled_tools.py',ROOT/'src/leasepath/restricted_data_controlled_worker.py',
               ROOT/'src/leasepath/restricted_data_extended.py',ROOT/'src/leasepath/restricted_data_tools.py']
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
