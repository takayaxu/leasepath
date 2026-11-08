"""Run the frozen full matrix in isolated native lifetimes; never auto-rerun."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from leasepath.prediction_training import read_json, sha256
from leasepath.cache_matrix import MatrixBudget
from probe_qwen_cache_native import write


def resource_samples(output, stopping):
    with (output/'resources.jsonl').open('x') as stream:
        while not stopping.is_set():
            row = {'wall_ns': time.time_ns(), 'monotonic_ns': time.monotonic_ns(),
                   'loadavg': os.getloadavg(), 'cpu_pressure': Path('/proc/pressure/cpu').read_text(),
                   'io_pressure': Path('/proc/pressure/io').read_text(),
                   'meminfo': {k: v.strip() for k,v in (line.split(':', 1) for line in
                       Path('/proc/meminfo').read_text().splitlines()) if k in {'MemAvailable','Shmem'}}}
            try:
                row['gpus'] = subprocess.check_output(['nvidia-smi', '-i', '5,6,7',
                    '--query-gpu=index,memory.used,utilization.gpu,temperature.gpu,power.draw,clocks.sm',
                    '--format=csv,noheader,nounits'], text=True, timeout=5)
            except Exception as exc:
                row['gpu_sample_error'] = str(exc)
            stream.write(json.dumps(row)+'\n'); stream.flush()
            stopping.wait(10)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan', type=Path, default=ROOT/'configs/prediction/qwen_cache_stage3_2026-11-08.json')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    plan = read_json(args.plan)
    budget = MatrixBudget(ROOT/'.artifacts/qwen-cache-stage3/ledger.jsonl', plan)
    if budget.summary()['generations']:
        raise RuntimeError('fresh matrix requires empty global ledger; no implicit retry')
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output/'frozen-plan.json', plan)
    write(args.output/'model-copy.json', read_json(ROOT/'.artifacts/qwen-cache-stage3/model-copy.json'))
    manifest = {'status': 'running', 'plan_sha256': sha256(args.plan), 'started_wall_ns': time.time_ns(),
                'capacities': [], 'pid': os.getpid(), 'budget_limits': plan['budget_limits'],
                'cpu_affinity': sorted(os.sched_getaffinity(0)), 'resource_sample_interval_s': 10}
    write(args.output/'matrix-manifest.json', manifest)
    stopping = threading.Event()
    sampler = threading.Thread(target=resource_samples, args=(args.output, stopping), daemon=True)
    sampler.start()
    try:
        for capacity in plan['capacities_blocks']:
            cmd = [sys.executable, str(ROOT/'scripts/run_qwen_cache_matrix_capacity.py'),
                   '--plan', str(args.plan.resolve()), '--capacity', str(capacity),
                   '--output', str((args.output/f'capacity-{capacity}').resolve())]
            start = time.monotonic()
            with (args.output/f'capacity-{capacity}.log').open('x') as stream:
                done = subprocess.run(cmd, stdout=stream, stderr=subprocess.STDOUT, check=False)
            manifest['capacities'].append({'capacity': capacity, 'exit_code': done.returncode,
                                            'wall_s': time.monotonic()-start})
            write(args.output/'matrix-manifest.json', manifest)
            if done.returncode:
                raise RuntimeError(f'capacity {capacity} failed; no later cells or implicit reruns')
        manifest['status'] = 'passed'
    except BaseException as exc:
        manifest.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        stopping.set(); sampler.join(timeout=10)
        manifest['finished_wall_ns'] = time.time_ns()
        manifest['budget'] = budget.summary()
        write(args.output/'matrix-manifest.json', manifest)
        if manifest['status'] == 'passed':
            write(budget.path.with_suffix('.sealed.json'), {'status': 'sealed', 'reason': 'completed fixed matrix',
                  'plan_sha256': sha256(args.plan), 'summary': budget.summary()})


if __name__ == '__main__':
    main()
