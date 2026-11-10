"""Bounded, resumable, hash-verified tmpfs staging, outside all JCT windows.

This program never imports an inference backend or runs a generation. A prefix
is reusable only after every committed chunk has been rehashed. The final
destination is independently read in full and compared to the expected digest.
"""
import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import time

GIB = 1024 ** 3
STAT_KEYS = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')


def identity(path):
    st = Path(path).stat()
    return {key: getattr(st, key) for key in STAT_KEYS}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, data):
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def memory_admission(available, owned_bytes, minimum=220 * GIB):
    # tmpfs is already subtracted from MemAvailable. Count exactly this model's
    # allocated prefix once, not arbitrary cache, virtual mappings or other jobs.
    if available + owned_bytes < minimum:
        raise RuntimeError('memory budget below preregistered total headroom')
    return {'available_bytes': available, 'owned_model_bytes': owned_bytes,
            'effective_available_bytes': available + owned_bytes,
            'required_total_bytes': minimum}


def resident_bytes(path, size):
    if not size:
        return 0
    lib = ctypes.CDLL(None, use_errno=True)
    lib.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
    lib.mmap.restype = ctypes.c_void_p
    lib.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
    lib.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    page = os.sysconf('SC_PAGE_SIZE')
    count = (size + page - 1) // page
    vector = ctypes.create_string_buffer(count)
    with Path(path).open('rb') as stream:
        address = lib.mmap(None, size, 1, 1, stream.fileno(), 0)
        if address == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_errno(), 'mmap for mincore')
        try:
            if lib.mincore(address, size, vector):
                raise OSError(ctypes.get_errno(), 'mincore')
            present = vector.raw.translate(bytes(v & 1 for v in range(256))).count(b'\x01')
            # Last partial page is credited only for its real bytes.
            return present * page - ((page - size % page) % page if vector.raw[-1] & 1 else 0)
        finally:
            lib.munmap(address, size)


def resources(destination, committed, remaining, plan):
    info = {k: int(v.split()[0]) * 1024 for k, v in
            (line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())}
    result = memory_admission(info['MemAvailable'], resident_bytes(destination, committed), plan['minimum_total_available_bytes'])
    st = os.statvfs(destination.parent)
    result['tmpfs_available_bytes'] = st.f_bavail * st.f_frsize
    if result['tmpfs_available_bytes'] < remaining + plan['tmpfs_reserve_bytes']:
        raise RuntimeError('insufficient tmpfs space for remaining model plus reserve')
    return result


def verify_prefix(stream, chunks, chunk_bytes):
    offset = 0
    for row in chunks:
        if row['offset'] != offset or not 0 < row['bytes'] <= chunk_bytes:
            raise ValueError('noncontiguous/invalid checkpoint chunk')
        block = stream.read(row['bytes'])
        if len(block) != row['bytes'] or hashlib.sha256(block).hexdigest() != row['sha256']:
            raise ValueError('checkpoint prefix bytes differ')
        offset += row['bytes']
    return offset


def verify_complete(plan, state, destination):
    if state['status'] != 'complete' or state['plan_sha256'] != plan['_sha256']:
        raise ValueError('incomplete or different staging plan')
    if state['sha256'] != plan['sha256'] or state['bytes'] != plan['bytes']:
        raise ValueError('wrong staged model digest/size')
    if str(destination.resolve()) != plan['destination'] or identity(destination) != state['destination_identity']:
        raise ValueError('staged model path/stat changed')
    return {'full_sha256': state['sha256'], 'model_identity': state['destination_identity'],
            'verification': 'complete source-stream SHA256 and independent full destination SHA256',
            'preparation_wall_s': state['total_elapsed_s'], 'outside_JCT': True}


def stage(plan_path, checkpoint):
    plan = json.loads(plan_path.read_text())
    plan['_sha256'] = digest(plan_path)
    destination = Path(plan['destination'])
    if not destination.parent.resolve().is_relative_to('/dev/shm'):
        raise ValueError('own tmpfs destination required')
    if destination.is_symlink():
        raise ValueError('destination must not be a symlink')
    destination.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        source = Path(plan['source'])
        source_id = identity(source)
        if source_id != plan['source_identity'] or source_id['st_size'] != plan['bytes']:
            raise ValueError('source differs from preregistration')
        if checkpoint.exists():
            state = json.loads(checkpoint.read_text())
            if state['plan_sha256'] != plan['_sha256']:
                raise ValueError('cannot resume under a different plan')
            if state['status'] == 'complete':
                return verify_complete(plan, state, destination)
            if state['status'] == 'failed':
                raise ValueError('integrity failure cannot be resumed')
            if not destination.exists():
                raise ValueError('checkpoint destination missing')
        else:
            with destination.open('xb'):
                pass
            state = {'version': 1, 'status': 'new', 'plan_sha256': plan['_sha256'],
                     'source_identity': source_id, 'chunks': [], 'bytes': 0,
                     'total_elapsed_s': 0, 'attempts': [], 'new_model_generations': 0}
            write_json(checkpoint, state)
        remaining_s = plan['total_budget_s'] - state['total_elapsed_s']
        if remaining_s <= 0:
            raise TimeoutError('cumulative preparation budget exhausted')
        started = time.monotonic()
        attempt = {'started_wall_ns': time.time_ns(), 'resume_bytes': state['bytes']}
        state['attempts'].append(attempt)
        previous_elapsed = state['total_elapsed_s']

        def bounded_stop(signum, frame):
            raise TimeoutError('bounded preparation interrupted: signal ' + str(signum))

        old_handlers = {sig: signal.signal(sig, bounded_stop) for sig in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT)}
        signal.setitimer(signal.ITIMER_REAL, min(remaining_s, plan['attempt_budget_s']))
        try:
            state['status'] = 'verifying_prefix'
            write_json(checkpoint, state)
            with destination.open('r+b', buffering=0) as out:
                offset = verify_prefix(out, state['chunks'], plan['chunk_bytes'])
                if offset != state['bytes']:
                    raise ValueError('checkpoint byte count differs')
                # A killed write may have an uncommitted tail. Only that tail is
                # discarded, after all acknowledged bytes pass their hashes.
                out.truncate(offset)
                full = hashlib.sha256()
                out.seek(0)
                while block := out.read(plan['chunk_bytes']):
                    full.update(block)
                with source.open('rb', buffering=0) as inp:
                    inp.seek(offset)
                    state['status'] = 'copying'
                    while offset < plan['bytes']:
                        state['last_resource_check'] = resources(destination, offset, plan['bytes'] - offset, plan)
                        wanted = min(plan['chunk_bytes'], plan['bytes'] - offset)
                        parts = bytearray()
                        while len(parts) < wanted:
                            block = inp.read(min(4 * 1024**2, wanted - len(parts)))
                            if not block:
                                raise ValueError('unexpected source EOF')
                            parts.extend(block)
                        view = memoryview(parts)
                        written = 0
                        while written < wanted:
                            count = out.write(view[written:])
                            if not count:
                                raise OSError('short destination write')
                            written += count
                        os.fsync(out.fileno())
                        full.update(parts)
                        state['chunks'].append({'offset': offset, 'bytes': wanted,
                                                'sha256': hashlib.sha256(parts).hexdigest()})
                        offset += wanted
                        state['bytes'] = offset
                        state['total_elapsed_s'] = previous_elapsed + time.monotonic() - started
                        write_json(checkpoint, state)
                        print(json.dumps({'bytes': offset, 'total_bytes': plan['bytes'],
                                          'elapsed_s': state['total_elapsed_s']}), flush=True)
                if full.hexdigest() != plan['sha256'] or identity(source) != source_id:
                    raise ValueError('complete source stream digest/stat mismatch')
            state['status'] = 'verifying_destination'
            write_json(checkpoint, state)
            if digest(destination) != plan['sha256']:
                raise ValueError('independent destination full hash mismatch')
            destination.chmod(0o444)
            state.update(status='complete', sha256=plan['sha256'],
                         destination_identity=identity(destination), source_stream_sha256=full.hexdigest(),
                         destination_full_hash_verified=True)
        except (TimeoutError, KeyboardInterrupt) as exc:
            state.update(status='paused', error=f'{type(exc).__name__}: {exc}')
            raise
        except RuntimeError as exc:
            state.update(status='paused', error=f'{type(exc).__name__}: {exc}')
            raise
        except BaseException as exc:
            state.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            raise
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
            state['total_elapsed_s'] = previous_elapsed + time.monotonic() - started
            attempt.update(finished_wall_ns=time.time_ns(), status=state['status'],
                           elapsed_s=time.monotonic() - started, committed_bytes=state['bytes'])
            write_json(checkpoint, state)
        return verify_complete(plan, state, destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(stage(args.plan, args.checkpoint)), flush=True)


if __name__ == '__main__':
    main()
