"""Append-only, process-sequenced observations on one host monotonic clock."""
import atexit
import fcntl
import json
import os
from pathlib import Path
import queue
import threading
import time

_writers = {}


class Writer:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.pid = os.getpid()
        self.sequence = 0
        self.pending = queue.Queue()
        self.error = None
        self.thread = threading.Thread(target=self._write, daemon=True)
        self.thread.start()
        atexit.register(self.flush)

    def emit(self, kind, *, ts_ns=None, **data):
        if self.error:
            raise RuntimeError('D2 observer failed') from self.error
        self.sequence += 1
        record = {'raw_version': 1, 'pid': self.pid, 'seq': self.sequence,
                  'ts_ns': ts_ns if ts_ns is not None else time.monotonic_ns(),
                  'kind': kind, **data}
        self.pending.put_nowait(record)

    def _write(self):
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            while True:
                record = self.pending.get()
                try:
                    start = time.perf_counter_ns()
                    encoded = json.dumps(record, separators=(',', ':')).encode() + b'\n'
                    record['observer_encode_ns'] = time.perf_counter_ns() - start
                    encoded = json.dumps(record, separators=(',', ':')).encode() + b'\n'
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    try:
                        view = memoryview(encoded)
                        while view:
                            view = view[os.write(fd, view):]
                    finally:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                except Exception as exc:
                    self.error = exc
                finally:
                    self.pending.task_done()
        finally:
            os.close(fd)

    def flush(self):
        self.pending.join()
        if self.error:
            raise RuntimeError('D2 observer failed') from self.error


def writer(path=None):
    path = path or os.environ['LEASEPATH_D2_RAW']
    key = (os.getpid(), str(path))
    if key not in _writers:
        _writers[key] = Writer(path)
        _writers[key].emit('observer_start', clock='CLOCK_MONOTONIC',
                           boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    return _writers[key]
