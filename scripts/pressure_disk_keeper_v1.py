"""Bounded read-only model preparation; keep verified pages locked across cells.

No inference. No copying, cache dropping, global memory settings or retries.
One process owns all locks; its death invalidates admission immediately.
"""
import argparse
import ctypes as C
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import time

from resident_windowed_v1 import resident_bytes
from stage_pressure_model_v1 import digest, identity, write_json, memory_admission


def process_identity(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    if fields[0] == 'Z':
        raise ValueError('keeper process is dead')
    return {'pid': pid, 'start_ticks': int(fields[19]),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def available_bytes():
    return int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines()
                    if l.startswith('MemAvailable:'))) * 1024


class LockedModel:
    def __init__(self, path):
        self.path = Path(path)
        self.file = self.path.open('rb', buffering=0)
        self.maps = []
        self.locked_bytes = 0
        self.lib = C.CDLL(None, use_errno=True)
        self.lib.mmap.argtypes = [C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_long]
        self.lib.mmap.restype = C.c_void_p
        for name in ('mlock', 'munmap'):
            getattr(self.lib, name).argtypes = [C.c_void_p, C.c_size_t]

    def add(self, offset, length):
        address = self.lib.mmap(None, length, 1, 1, self.file.fileno(), offset)
        if address == C.c_void_p(-1).value:
            raise OSError(C.get_errno(), 'model mmap')
        try:
            if self.lib.mlock(address, length):
                raise OSError(C.get_errno(), 'model mlock')
        except BaseException:
            self.lib.munmap(address, length)
            raise
        self.maps.append((address, length))
        self.locked_bytes += length
        return address

    def close(self):
        for address, length in reversed(self.maps):
            self.lib.munmap(address, length)
        self.maps.clear()
        self.file.close()


def prepare(plan_path, progress, ready):
    plan = json.loads(plan_path.read_text())
    if ready.exists() or progress.exists():
        raise ValueError('existing preparation attempt cannot be replaced')
    for path, expected in plan['source_sha256'].items():
        if digest(path) != expected:
            raise ValueError('preparation source changed: ' + path)
    model = Path(plan['model_path'])
    if identity(model) != plan['model_identity'] or model.stat().st_size != plan['bytes']:
        raise ValueError('model identity changed')
    if plan['chunk_bytes'] % os.sysconf('SC_PAGE_SIZE'):
        raise ValueError('unaligned locking chunk')
    start = time.monotonic()
    state = {'status': 'preparing', 'plan_sha256': digest(plan_path),
             'process': process_identity(os.getpid()), 'started_wall_ns': time.time_ns(),
             'locked_bytes': 0, 'bytes_hashed': 0, 'pass': 1, 'passes': [],
             'new_model_generations': 0, 'outside_JCT': True}
    def save():
        state['elapsed_s'] = time.monotonic() - start
        state['updated_wall_ns'] = time.time_ns()
        write_json(progress, state)
    def stop(signum, frame):
        raise TimeoutError('bounded model preparation/holding stopped: ' + str(signum))
    handlers = {s: signal.signal(s, stop) for s in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT)}
    signal.setitimer(signal.ITIMER_REAL, plan['preparation_wall_budget_s'])
    guard = None
    try:
        state['initial_memory'] = memory_admission(available_bytes(), 0, plan['minimum_total_bytes'])
        os.sched_setaffinity(0, plan['cpus'])
        lib = C.CDLL(None, use_errno=True)
        if lib.prctl(41, 1, 0, 0, 0) or lib.prctl(42, 0, 0, 0, 0) != 1:
            raise OSError(C.get_errno(), 'private keeper THP control')
        resource.setrlimit(resource.RLIMIT_AS, (plan['address_space_bytes'],) * 2)
        state['controls'] = {'cpus': sorted(os.sched_getaffinity(0)), 'thp_disabled': True,
                             'address_space_bytes': resource.getrlimit(resource.RLIMIT_AS)[0]}
        guard = LockedModel(model)
        save()
        for pass_number in (1, 2):
            full = hashlib.sha256()
            state.update(pass_number=pass_number, bytes_hashed=0)
            for offset in range(0, plan['bytes'], plan['chunk_bytes']):
                length = min(plan['chunk_bytes'], plan['bytes'] - offset)
                state['last_memory_admission'] = memory_admission(
                    available_bytes(), guard.locked_bytes, plan['minimum_total_bytes'])
                address = (guard.add(offset, length) if pass_number == 1 else
                           guard.maps[offset // plan['chunk_bytes']][0])
                for relative in range(0, length, plan['hash_buffer_bytes']):
                    full.update(C.string_at(address + relative,
                                min(plan['hash_buffer_bytes'], length - relative)))
                state.update(locked_bytes=guard.locked_bytes, bytes_hashed=offset + length)
                save()
            if full.hexdigest() != plan['sha256'] or identity(model) != plan['model_identity']:
                raise ValueError('full model digest or source identity mismatch')
            state['passes'].append({'pass': pass_number, 'bytes': plan['bytes'], 'sha256': full.hexdigest()})
        if resident_bytes(model, plan['bytes']) != plan['bytes']:
            raise ValueError('model lost residency')
        state.update(status='ready', model_path=str(model), model_identity=identity(model),
                     sha256=plan['sha256'], model_bytes=plan['bytes'], mincore_all_pages_resident=True,
                     process_status=Path('/proc/self/status').read_text(),
                     preparation_wall_s=time.monotonic() - start)
        save()
        write_json(ready, state)
        signal.setitimer(signal.ITIMER_REAL, plan['maximum_hold_wall_s'])
        while True:
            # The supervising process owns lifetime; no orphaned 61GiB keeper.
            owner = plan.get('supervisor_process')
            if owner and process_identity(owner['pid']) != owner:
                raise RuntimeError('supervisor identity changed')
            if identity(model) != plan['model_identity']:
                raise RuntimeError('locked model identity changed')
            time.sleep(5)
    except BaseException as exc:
        state.update(status='released' if ready.exists() else 'failed', error=f'{type(exc).__name__}: {exc}')
        save()
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if guard:
            guard.close()
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--progress', type=Path, required=True)
    parser.add_argument('--ready', type=Path, required=True)
    args = parser.parse_args()
    with args.progress.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prepare(args.plan, args.progress, args.ready)
