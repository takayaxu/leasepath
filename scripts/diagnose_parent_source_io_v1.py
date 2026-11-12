"""Bounded, generation-free replay of the failed controller's source checks.

Keep every original SHA256 comparison and mmap/mlock recipe. Attribute I/O to
individual file checks using thread counters; do not drop caches or load a GPU.
This is diagnosis, never a natural-JCT acceptance or replacement sample.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.resident_runtime import ResidentFiles
from leasepath.prediction_training import sha256


def counters():
    usage = resource.getrusage(resource.RUSAGE_THREAD)
    io = dict(line.split(':') for line in Path('/proc/thread-self/io').read_text().splitlines())
    return {'read_bytes': int(io['read_bytes']), 'major_faults': usage.ru_majflt,
            'block_input_ops': usage.ru_inblock}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((args.run / 'run-manifest.json').read_text())
    hashes = manifest['source_sha256']
    summary = {'status': 'preparing', 'diagnostic_only': True, 'new_model_generations': 0,
               'source_run': str(args.run), 'source_manifest_sha256': sha256(args.run / 'run-manifest.json'),
               'threads': 3, 'checks_per_thread': 12, 'wall_budget_s': 600,
               'files_per_check': len(hashes), 'started_wall_ns': time.time_ns()}
    def stop(signum, frame):
        raise TimeoutError('source diagnostic wall budget')
    signal.signal(signal.SIGALRM, stop)
    signal.alarm(600)
    guard = None
    try:
        cpus = json.loads((args.run / 'frozen-plan.json').read_text())['model_cpus']
        os.sched_setaffinity(0, cpus)
        for path, expected in hashes.items():
            if sha256(path) != expected:
                raise ValueError('historical source changed: ' + path)
        guard = ResidentFiles(hashes)
        summary['initial_residency'] = guard.verify()
        (args.output / 'plan.json').write_text(json.dumps(summary, indent=2) + '\n')
        def run(worker):
            rows = []
            output = args.output / f'worker-{worker}.jsonl'
            with output.open('x') as log:
                for repeat in range(summary['checks_per_thread']):
                    start = time.monotonic()
                    before = counters()
                    events = []
                    for path, expected in hashes.items():
                        first = counters()
                        actual = sha256(path)
                        last = counters()
                        delta = {k: last[k] - first[k] for k in first}
                        if any(delta.values()):
                            events.append({'path': path, 'bytes': Path(path).stat().st_size, 'delta': delta})
                        if actual != expected:
                            raise ValueError('source changed: ' + path)
                    last = counters()
                    row = {'worker': worker, 'repeat': repeat, 'wall_s': time.monotonic() - start,
                           'delta': {k: last[k] - before[k] for k in before}, 'events': events}
                    log.write(json.dumps(row) + '\n'); log.flush()
                    rows.append(row)
            return rows
        with ThreadPoolExecutor(max_workers=summary['threads']) as pool:
            rows = [r for group in pool.map(run, range(summary['threads'])) for r in group]
        summary.update(status='completed', checks=len(rows), rows=rows,
                       events=[event for row in rows for event in row['events']],
                       final_residency=guard.verify())
    except BaseException as exc:
        summary.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        signal.alarm(0)
        if guard:
            guard.close()
        summary['finished_wall_ns'] = time.time_ns()
        (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    main()
