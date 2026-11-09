"""Same frozen numerical dispatch; retain per-thread samples outside model output."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path

from . import restricted_data_controlled_worker as worker


def main():
    samples = []
    original = worker.snapshot

    def snapshot():
        result = original()
        threads = {}
        for task in Path('/proc/self/task').iterdir():
            try:
                stat = (task/'stat').read_text()
                fields = stat[stat.rindex(')')+2:].split()
                threads[task.name] = {'schedstat': list(map(int, (task/'schedstat').read_text().split())),
                    'state': fields[0], 'last_cpu': int(fields[36]),
                    'affinity': sorted(os.sched_getaffinity(int(task.name)))}
            except (OSError, ValueError):
                continue
        samples.append({'wall_ns': result['wall_ns'], 'threads': threads})
        return result

    worker.snapshot = snapshot
    stream = io.StringIO()
    with redirect_stdout(stream):
        worker.main()
    envelope = json.loads(stream.getvalue())
    from threadpoolctl import threadpool_info
    envelope['thread_diagnostics'] = {'samples': samples, 'pools_after': threadpool_info(),
        'environment': {k: os.environ.get(k) for k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS')},
        'scope': 'read-only per-thread samples at the same phase boundaries; metadata never returned to model'}
    print(json.dumps(envelope, allow_nan=False))


if __name__ == '__main__':
    main()
