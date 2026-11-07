"""Finish a complete copied GGUF using bounded prefetch and canonical full SHA256."""
import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import time


def ordered_hash(path, workers):
    """Prefetch at most 128 MiB, hashing all bytes in their original order."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        block = 8 * 1024**2
        def read(offset):
            count = min(block, before.st_size - offset)
            pieces = []
            while count:
                data = os.pread(fd, count, offset)
                if not data:
                    raise ValueError('short read in persistent model')
                pieces.append(data)
                offset += len(data)
                count -= len(data)
            return b''.join(pieces)
        digest = hashlib.sha256()
        offsets = iter(range(0, before.st_size, block))
        total = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = deque()
            for _ in range(16):
                offset = next(offsets, None)
                if offset is not None:
                    pending.append(pool.submit(read, offset))
            while pending:
                data = pending.popleft().result()
                digest.update(data)
                total += len(data)
                if total % (1024**3) == 0 or total == before.st_size:
                    print(json.dumps({'full_hash_bytes': total, 'total_bytes': before.st_size,
                                      'wall_ns': time.time_ns()}), flush=True)
                offset = next(offsets, None)
                if offset is not None:
                    pending.append(pool.submit(read, offset))
        after = os.fstat(fd)
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('persistent copy changed during verification')
        return digest.hexdigest()
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--partial', type=Path, required=True)
    parser.add_argument('--identity', type=Path, required=True)
    parser.add_argument('--final', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        raise ValueError('worker count outside bounded range')
    if args.partial.parent.resolve() != args.final.parent.resolve() or args.final.exists():
        raise ValueError('final must be new and in the same directory')
    identity = json.loads(args.identity.read_text())
    source = Path(identity['gguf'])
    if args.partial.is_symlink() or args.partial.stat().st_size != source.stat().st_size:
        raise ValueError('copy is incomplete or a symlink')
    start = time.monotonic_ns()
    # The previous copy was fsynced; repeat that boundary before independently reading it.
    with args.partial.open('rb') as stream:
        os.fsync(stream.fileno())
    actual = ordered_hash(args.partial, args.workers)
    if actual != identity['gguf_sha256']:
        raise ValueError('persistent copy SHA256 differs from conversion identity')
    os.link(args.partial, args.final)
    args.partial.unlink()
    identity_copy = args.final.with_suffix('.identity.json')
    with identity_copy.open('xb') as stream:
        stream.write(args.identity.read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    receipt = {'status': 'passed', 'source': str(source),
               'persistent_model': str(args.final.resolve()),
               'persistent_identity': str(identity_copy.resolve()),
               'sha256': actual, 'bytes': args.final.stat().st_size,
               'verification': 'complete ordered SHA256; bounded prefetch, no sampled bytes',
               'identity_gguf_path_is_original_conversion_provenance': True,
               'verification_elapsed_s': (time.monotonic_ns()-start)/1e9,
               'completed_wall_ns': time.time_ns()}
    with args.final.with_suffix('.persistence.json').open('x') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(args.final.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    print(json.dumps(receipt, indent=2), flush=True)


if __name__ == '__main__':
    main()
