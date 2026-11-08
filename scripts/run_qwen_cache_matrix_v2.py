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
from leasepath.cache_matrix_continuation import ContinuedMatrixBudget as MatrixBudget
from probe_qwen_cache_native import write


from run_qwen_cache_matrix import resource_samples


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan', type=Path, default=ROOT/'configs/prediction/qwen_cache_stage3b_2026-11-08.json')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    plan = read_json(args.plan)
    budget = MatrixBudget(ROOT/'.artifacts/qwen-cache-stage3/attempt-b-ledger.jsonl', plan)
    if budget.summary()['generations']:
        raise RuntimeError('fresh matrix requires empty global ledger; no implicit retry')
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output/'frozen-plan.json', plan)
    write(args.output/'model-copy.json', read_json(ROOT/'.artifacts/qwen-cache-stage3/model-copy.json'))
    manifest = {'status': 'running', 'plan_sha256': sha256(args.plan), 'started_wall_ns': time.time_ns(),
                'capacities': [], 'pid': os.getpid(), 'budget_limits': plan['budget_limits'], 'prior_failed_generations': plan['prior_failed_generations'], 'stage_generation_total_cap': plan['stage_generation_total_cap'],
                'cpu_affinity': sorted(os.sched_getaffinity(0)), 'resource_sample_interval_s': 10}
    manifest['memory_settings'] = {name: Path(name).read_text().strip() for name in
        ['/sys/kernel/mm/transparent_hugepage/enabled', '/sys/kernel/mm/transparent_hugepage/defrag',
         '/proc/sys/kernel/numa_balancing'] if Path(name).exists()}
    write(args.output/'matrix-manifest.json', manifest)
    stopping = threading.Event()
    sampler = threading.Thread(target=resource_samples, args=(args.output, stopping), daemon=True)
    sampler.start()
    try:
        for capacity in plan['capacities_blocks']:
            cmd = [sys.executable, str(ROOT/'scripts/run_qwen_cache_matrix_v2_capacity.py'),
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
