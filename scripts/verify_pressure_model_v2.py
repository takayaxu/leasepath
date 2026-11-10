"""Two independently initialized destination hashes with durable SHA256 state.

The complete, immutable tmpfs copy is read with one reusable 1MiB buffer. A
checkpoint is reusable only under the same file stat, verifier and libcrypto
ABI. The first pass also compares every original copy chunk. No model calls.
"""
import argparse
import ctypes as C
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time

from stage_pressure_model_v1 import digest, identity, write_json, memory_admission, resident_bytes


class SHA256Context(C.Structure):
    _fields_ = [('h', C.c_uint * 8), ('Nl', C.c_uint), ('Nh', C.c_uint),
                ('data', C.c_uint * 16), ('num', C.c_uint), ('md_len', C.c_uint)]


class ResumableSHA256:
    def __init__(self, library, state=None, processed=0):
        self.lib = library
        for name in ('SHA256_Init', 'SHA256_Update', 'SHA256_Final'):
            getattr(library, name).restype = C.c_int
        library.SHA256_Init.argtypes = [C.POINTER(SHA256Context)]
        library.SHA256_Update.argtypes = [C.POINTER(SHA256Context), C.c_void_p, C.c_size_t]
        library.SHA256_Final.argtypes = [C.c_void_p, C.POINTER(SHA256Context)]
        if C.sizeof(SHA256Context) != 112 or sys.byteorder != 'little':
            raise ValueError('unregistered SHA256 ABI')
        if state is None:
            self.ctx = SHA256Context()
            if library.SHA256_Init(C.byref(self.ctx)) != 1:
                raise RuntimeError('SHA256 init failed')
        else:
            raw = bytes.fromhex(state)
            if len(raw) != C.sizeof(SHA256Context):
                raise ValueError('SHA256 state size differs')
            self.ctx = SHA256Context.from_buffer_copy(raw)
            if self.ctx.md_len != 32 or self.ctx.num != processed % 64 or (self.ctx.Nh << 32 | self.ctx.Nl) != processed * 8:
                raise ValueError('SHA256 state offset differs')

    def update(self, buffer, length):
        array = (C.c_ubyte * length).from_buffer(buffer)
        if self.lib.SHA256_Update(C.byref(self.ctx), array, length) != 1:
            raise RuntimeError('SHA256 update failed')

    def export(self):
        return bytes(self.ctx).hex()

    def hexdigest(self):
        clone = SHA256Context.from_buffer_copy(bytes(self.ctx))
        result = (C.c_ubyte * 32)()
        if self.lib.SHA256_Final(result, C.byref(clone)) != 1:
            raise RuntimeError('SHA256 final failed')
        return bytes(result).hex()


def self_test(library):
    data = bytearray(bytes(range(256)) * 1024 + b'last partial block')
    for boundary in (0, 1, 63, 64, 65537):
        h = ResumableSHA256(library)
        h.update(data, boundary)
        h = ResumableSHA256(library, h.export(), boundary)
        tail = data[boundary:]
        h.update(tail, len(tail))
        if h.hexdigest() != hashlib.sha256(data).hexdigest():
            raise ValueError('SHA256 ABI/checkpoint self-test failed')


def seal_state(path, state):
    payload = {k: v for k, v in state.items() if k != 'checkpoint_sha256'}
    state['checkpoint_sha256'] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    write_json(path, state)


def read_state(path):
    state = json.loads(path.read_text())
    payload = {k: v for k, v in state.items() if k != 'checkpoint_sha256'}
    if hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() != state['checkpoint_sha256']:
        raise ValueError('checkpoint checksum differs')
    return state


def verify(plan_path, checkpoint, *, stop_after_chunks=None):
    plan = json.loads(plan_path.read_text())
    for path, expected in plan['source_sha256'].items():
        if digest(Path(path)) != expected:
            raise ValueError('verification source/evidence differs: ' + path)
    model = Path(plan['model_path'])
    if model.is_symlink() or model.stat().st_mode & 0o222 or identity(model) != plan['model_identity']:
        raise ValueError('immutable model identity differs')
    original = json.loads(Path(plan['copy_plan']).read_text())
    if identity(original['source']) != original['source_identity']:
        raise ValueError('original model identity differs')
    copied = json.loads(Path(plan['copy_checkpoint']).read_text())
    chunks = copied['chunks']
    offset = 0
    for row in chunks:
        if row['offset'] != offset or not 0 < row['bytes'] <= original['chunk_bytes']:
            raise ValueError('copy chunks noncontiguous')
        offset += row['bytes']
    if offset != plan['bytes'] or copied['bytes'] != offset or model.stat().st_size != offset:
        raise ValueError('incomplete copy cannot enter verification')
    lib = C.CDLL(plan['libcrypto'])
    self_test(lib)
    buffer = bytearray(plan['buffer_bytes'])
    started = time.monotonic()
    with checkpoint.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if checkpoint.exists():
            state = read_state(checkpoint)
            if state['plan_sha256'] != digest(plan_path):
                raise ValueError('different verification plan')
            if state['status'] == 'complete':
                return state
            if state['status'] not in ('paused',):
                raise ValueError('unclean or failed verification requires explicit accounting before resume')
        else:
            state = {'version': 2, 'status': 'new', 'plan_sha256': digest(plan_path),
                     'pass': 1, 'offset': 0, 'chunk_index': 0, 'sha_state': None,
                     'elapsed_s': 0, 'attempts': [], 'passes': [], 'model_identity': identity(model),
                     'new_model_generations': 0}
        previous = state['elapsed_s']
        remaining = plan['remaining_budget_s'] - previous
        if remaining <= 0:
            raise TimeoutError('original cumulative preparation budget exhausted')
        attempt = {'started_wall_ns': time.time_ns(), 'resume_pass': state['pass'], 'resume_offset': state['offset']}
        state['attempts'].append(attempt)
        state['status'] = 'verifying'
        seal_state(checkpoint, state)
        def timeout(signum, frame):
            raise TimeoutError('verification deadline/signal ' + str(signum))
        handlers = {sig: signal.signal(sig, timeout) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM)}
        signal.setitimer(signal.ITIMER_REAL, remaining)
        completed = 0
        try:
            # Reading may fault during preparation. None of these times/counters
            # are reported as natural JCT or zero-I/O evidence.
            with model.open('rb', buffering=0) as stream:
                while state['pass'] <= 2:
                    full = ResumableSHA256(lib, state['sha_state'], state['offset'])
                    stream.seek(state['offset'])
                    for index in range(state['chunk_index'], len(chunks)):
                        row = chunks[index]
                        chunk = hashlib.sha256()
                        count = 0
                        while count < row['bytes']:
                            wanted = min(len(buffer), row['bytes'] - count)
                            n = stream.readinto(memoryview(buffer)[:wanted])
                            if not n:
                                raise ValueError('unexpected model EOF')
                            full.update(buffer, n)
                            chunk.update(memoryview(buffer)[:n])
                            count += n
                        if chunk.hexdigest() != row['sha256']:
                            raise ValueError('copied chunk digest differs: ' + str(index))
                        if identity(model) != plan['model_identity']:
                            raise ValueError('model changed during verification')
                        state.update(offset=row['offset'] + row['bytes'], chunk_index=index + 1,
                                     sha_state=full.export(), elapsed_s=previous + time.monotonic() - started)
                        if index % 16 == 0:
                            available = int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:'))) * 1024
                            state['memory_admission'] = memory_admission(available, resident_bytes(model, plan['bytes']), plan['minimum_total_available_bytes'])
                        seal_state(checkpoint, state)
                        print(json.dumps({'pass': state['pass'], 'bytes': state['offset'], 'total_bytes': plan['bytes'], 'elapsed_s': state['elapsed_s']}), flush=True)
                        completed += 1
                        if stop_after_chunks and completed >= stop_after_chunks:
                            raise TimeoutError('test checkpoint interruption')
                    actual = full.hexdigest()
                    if actual != plan['sha256']:
                        raise ValueError('complete model SHA256 mismatch')
                    state['passes'].append({'pass': state['pass'], 'sha256': actual, 'bytes': state['offset'],
                                            'all_copy_chunk_hashes_checked': True, 'finished_wall_ns': time.time_ns()})
                    state.update(offset=0, chunk_index=0, sha_state=None, **{'pass': state['pass'] + 1})
                    seal_state(checkpoint, state)
            state.update(status='complete', sha256=plan['sha256'], bytes=plan['bytes'], destination_identity=identity(model),
                         destination_full_hash_verified=True, verification='two independent complete destination SHA256 passes and all original copy-chunk hashes')
        except TimeoutError as exc:
            state.update(status='paused', error=f'{type(exc).__name__}: {exc}')
            raise
        except BaseException as exc:
            state.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            raise
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
            state['elapsed_s'] = previous + time.monotonic() - started
            attempt.update(finished_wall_ns=time.time_ns(), elapsed_s=time.monotonic() - started,
                           status=state['status'], verified_pass=state['pass'], verified_offset=state['offset'])
            seal_state(checkpoint, state)
        return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.plan, args.checkpoint)
    print(json.dumps({k: v for k, v in result.items() if k != 'sha_state'}), flush=True)
