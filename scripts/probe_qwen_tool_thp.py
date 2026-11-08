"""Six bounded real ridge calls to isolate a per-process memory setting.

Frozen AB/BA/AB order, same input hash, actual references, two BLAS threads,
12 GiB and 240 s child deadline. No model requests or system-wide settings.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from leasepath.prediction_training import read_json, sha256
from leasepath.qwen_data_tasks import equal
from leasepath.restricted_data_tools import open_resource
from probe_qwen_cache_native import write


def memory_sample():
    return {'memory_pressure': Path('/proc/pressure/memory').read_text(),
        'vmstat': {k:int(v) for k,v in (line.split() for line in Path('/proc/vmstat').read_text().splitlines())
                   if k.startswith(('compact_', 'allocstall', 'thp_'))}}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    directory = ROOT/'.artifacts/qwen-data-tasks-v5'
    task = next(t for t in read_json(directory/'tasks.private.json') if t['id'] == 'ridge_validation')
    reference = read_json(directory/'references.private.json')[task['id']]['reference']
    sources = [Path(__file__).resolve(), ROOT/'src/leasepath/restricted_data_timed_worker.py',
        ROOT/'src/leasepath/restricted_data_no_thp_worker.py', ROOT/'src/leasepath/restricted_data_extended.py',
        directory/'tasks.private.json', directory/'references.private.json']
    hashes = {str(p):sha256(p) for p in sources}
    plan = {'repetitions':3, 'order':[['normal','no_thp'],['no_thp','normal'],['normal','no_thp']],
        'task':task['id'], 'input_sha256':task['input_sha256'], 'threads':2, 'memory_gib':12,
        'child_deadline_s':240, 'new_model_generations':0, 'source_sha256':hashes,
        'treatment':'PR_SET_THP_DISABLE=1 in own child only; normal has no prctl',
        'scope':'CPU tool diagnostic, not cache policy or whole-task JCT; host remains shared',
        'failure_rule':'stop on any numerical/source/child validation failure; no reruns or outlier exclusion'}
    write(args.output/'frozen-plan.json', plan)
    rows = []
    for repeat, order in enumerate(plan['order']):
        for mode in order:
            for name,digest in hashes.items():
                if sha256(name) != digest: raise ValueError('protected source changed: '+name)
            before_sample = memory_sample()
            start = time.monotonic_ns()
            fd, before = open_resource(task['file'], task['input_sha256'])
            hash_done = time.monotonic_ns()
            try:
                request = {'arguments':dict(dataset_id=task['dataset_id'], operation=task['operation'], **task['parameters']),
                           'kind':task['kind'], 'fd':fd, 'threads':2, 'memory_gib':12}
                module = 'leasepath.restricted_data_timed_worker' if mode == 'normal' else 'leasepath.restricted_data_no_thp_worker'
                env = {**os.environ, 'OMP_NUM_THREADS':'2', 'OPENBLAS_NUM_THREADS':'2', 'MKL_NUM_THREADS':'2'}
                child_start = time.monotonic_ns()
                proc = subprocess.Popen([sys.executable, '-m', module], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, pass_fds=(fd,), env=env, start_new_session=True)
                try:
                    stdout, stderr = proc.communicate(json.dumps(request), timeout=max(0,240-(time.monotonic_ns()-start)/1e9))
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL); proc.communicate()
                    raise TimeoutError('diagnostic child killed and reaped')
                child_end = time.monotonic_ns()
                row = {'repeat':repeat, 'mode':mode, 'pid':proc.pid, 'returncode':proc.returncode,
                       'stderr':stderr, 'started_ns':start, 'input_check_ns':hash_done-start,
                       'child_wall_ns':child_end-child_start}
                if proc.returncode:
                    write(args.output/f'{repeat}-{mode}-failure.json',row)
                    raise ValueError('diagnostic child failed')
                envelope = json.loads(stdout)
                row.update(envelope)
                if not equal(envelope['result'], reference): raise ValueError('independent reference differs')
                after = os.fstat(fd)
                if before != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                    raise ValueError('input changed')
                post_start = time.monotonic_ns()
                post_fd, post_info = open_resource(task['file'], task['input_sha256'])
                os.close(post_fd)
                if post_info != before: raise ValueError('input changed during tool')
                ended = time.monotonic_ns()
                row.update(ended_ns=ended, post_input_check_ns=ended-post_start, tool_wall_ns=ended-start,
                           before=before_sample, after=memory_sample(), quality='passed')
                write(args.output/f'{repeat}-{mode}.json',row)
                rows.append(row)
                print(mode, repeat, 'tool_s',round(row['tool_wall_ns']/1e9,3),
                      'compute_s',round(row['worker_timing']['phases']['numerical_compute']['wall_ns']/1e9,3),
                      'system_cpu_s',round(row['worker_timing']['overall']['system_ns']/1e9,3), flush=True)
            finally:
                os.close(fd)
    summary = {'status':'passed','quality':f'{len(rows)}/6','new_model_generations':0,'rows':rows}
    summary['modes'] = {mode:{key:statistics.mean(r[key]/1e9 for r in rows if r['mode']==mode)
        for key in ['tool_wall_ns','worker_execute_ns','input_check_ns','post_input_check_ns']}
        for mode in ['normal','no_thp']}
    write(args.output/'summary.json',summary)


if __name__ == '__main__':
    main()
