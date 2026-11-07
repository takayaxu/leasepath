"""Copy a completed tmpfs model to durable storage and verify its full hash."""
import argparse
import json
import os
from pathlib import Path
import shutil
import time

from leasepath.restricted_data_tools import file_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    destination = args.destination.resolve()
    if source == destination or destination.exists():
        raise ValueError('destination must be a new persistent path')
    identity_path = source.with_suffix('.identity.json')
    identity = json.loads(identity_path.read_text())
    if identity.get('gguf_sha256') != file_hash(source):
        raise ValueError('source differs from completed conversion identity')
    before = source.stat()
    started = time.monotonic_ns()
    temporary = destination.with_suffix(destination.suffix + '.copying')
    with source.open('rb') as reader, temporary.open('xb') as writer:
        shutil.copyfileobj(reader, writer, length=16 * 1024**2)
        writer.flush()
        os.fsync(writer.fileno())
    after = source.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('source changed during copy; incomplete destination retained')
    if file_hash(temporary) != identity['gguf_sha256']:
        raise ValueError('persistent copy hash differs; incomplete destination retained')
    # Reserve the final name without overwriting another run's file.
    os.link(temporary, destination)
    temporary.unlink()
    copied_identity = destination.with_suffix('.identity.json')
    with copied_identity.open('xb') as writer:
        writer.write(identity_path.read_bytes())
        writer.flush()
        os.fsync(writer.fileno())
    receipt = {
        'status': 'passed', 'source': str(source), 'persistent_model': str(destination),
        'persistent_identity': str(copied_identity),
        'identity_gguf_path_is_original_conversion_provenance': True,
        'bytes': before.st_size, 'sha256': identity['gguf_sha256'],
        'elapsed_s': (time.monotonic_ns() - started) / 1e9,
        'completed_wall_ns': time.time_ns(),
    }
    receipt_path = destination.with_suffix('.persistence.json')
    with receipt_path.open('x') as writer:
        json.dump(receipt, writer, indent=2)
        writer.write('\n')
        writer.flush()
        os.fsync(writer.fileno())
    directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
