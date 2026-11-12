"""Retain source path metadata and attribute parent I/O without changing gates.

mlock covers nonempty file data. O_PATH handles also retain dentries/inodes for
empty files and path directories. This does not promise kernel metadata is
unevictable; the unchanged zero-I/O gate remains authoritative.
"""
import os
from pathlib import Path
import resource
import threading
import time

from leasepath.prediction_training import sha256


def identity(stat):
    return tuple(getattr(stat, k) for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


class SourcePathHandles:
    def __init__(self, paths):
        self.handles = []
        self.records = []
        files = sorted(set(map(lambda p: Path(p).resolve(strict=True), paths)))
        directories = sorted({d for p in files for d in p.parents})
        try:
            for path in [*directories, *files]:
                directory = path in directories
                flags = os.O_PATH | os.O_CLOEXEC | (os.O_DIRECTORY if directory else 0)
                fd = os.open(path, flags)
                self.handles.append(fd)
                self.records.append({'path': str(path), 'directory': directory,
                                     'identity': identity(os.fstat(fd))})
        except BaseException:
            self.close()
            raise

    def verify(self):
        for fd, row in zip(self.handles, self.records):
            current = identity(os.fstat(fd))
            target = identity(os.stat(row['path']))
            # Directory contents can grow (new evidence); its inode must persist.
            count = 2 if row['directory'] else 5
            if current[:count] != row['identity'][:count] or target[:count] != row['identity'][:count]:
                raise ValueError('retained source path changed: ' + row['path'])
        return {'handles': len(self.handles), 'files': sum(not r['directory'] for r in self.records),
                'empty_files': sum(not r['directory'] and r['identity'][2] == 0 for r in self.records),
                'all_identities_match': True}

    def close(self):
        for fd in reversed(self.handles):
            os.close(fd)
        self.handles.clear()


def thread_counters():
    usage = resource.getrusage(resource.RUSAGE_THREAD)
    io = dict(line.split(':') for line in Path('/proc/thread-self/io').read_text().splitlines())
    return {'read_bytes': int(io['read_bytes']), 'major_faults': usage.ru_majflt,
            'block_input_ops': usage.ru_inblock}


class SourceIOAudit:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset('preparation')

    def reset(self, batch):
        with self.lock:
            self.batch = batch
            self.events = []
            self.stats = {}

    def measure(self, phase, function, *args, path=None, **kwargs):
        before = thread_counters()
        start = time.monotonic_ns()
        try:
            return function(*args, **kwargs)
        finally:
            after = thread_counters()
            elapsed = time.monotonic_ns() - start
            delta = {k: after[k] - before[k] for k in before}
            with self.lock:
                row = self.stats.setdefault(phase, {'calls': 0, 'summed_thread_wall_ns': 0})
                row['calls'] += 1
                row['summed_thread_wall_ns'] += elapsed
                if any(delta.values()):
                    self.events.append({'batch': self.batch, 'phase': phase, 'path': str(path) if path else None,
                                        'tid': threading.get_native_id(), 'wall_ns': time.time_ns(), 'delta': delta})

    def digest(self, path):
        return self.measure('source_sha256', sha256, path, path=path)

    def snapshot(self):
        with self.lock:
            return {'batch': self.batch, 'stats': {k: dict(v) for k, v in self.stats.items()},
                    'events': list(self.events), 'scope': 'calling-thread counters; source SHA256 and native generate only',
                    'timing': 'summed per-call elapsed time; concurrent calls overlap; not additive JCT',
                    'zero_io_gate_unchanged': True}
