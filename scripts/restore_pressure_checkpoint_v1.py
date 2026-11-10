"""Restore the archived runtime/evidence by content, without editing old evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import time


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def restore(manifest_path, output):
    started = time.monotonic()
    manifest = json.loads(manifest_path.read_text())
    root = Path(manifest['source'])
    if not root.is_relative_to('/dev/shm') or root.exists():
        raise ValueError('restore requires absent own tmpfs root')
    temporary = root.with_name(root.name + '.restoring')
    temporary.mkdir(mode=0o700, exist_ok=False)
    rows = manifest['files']
    for row in rows:
        rel = PurePosixPath(row['relative'])
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('unsafe archive target')
    checked = []
    for kind in ('base', 'delta'):
        archive = Path(manifest[kind + '_archive'])
        expected = manifest[kind + '_archive_sha256']
        actual = sha(archive)
        if actual != expected:
            raise ValueError('archive digest differs: ' + str(archive))
        wanted = {r['archive_member']: r for r in rows if r['kind'] == kind}
        with tarfile.open(archive, 'r|gz') as tar:
            for member in tar:
                row = wanted.pop(member.name, None)
                if row is None:
                    continue
                if not member.isfile() or member.size != row['bytes']:
                    raise ValueError('archive member geometry differs')
                target = temporary / row['relative']
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as stream:
                    shutil.copyfileobj(tar.extractfile(member), stream, 4 * 1024**2)
                if sha(target) != row['sha256']:
                    raise ValueError('restored bytes differ: ' + row['relative'])
                target.chmod(member.mode & 0o777)
        if wanted:
            raise ValueError('missing archive members')
        checked.append({'kind': kind, 'archive': str(archive), 'sha256': actual})
        print(json.dumps(checked[-1]), flush=True)
    for row in rows:
        if row['kind'] == 'symlink':
            target = temporary / row['relative']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(row['target'])
    os.rename(temporary, root)
    result = {'status': 'passed', 'manifest_sha256': sha(manifest_path),
              'root': str(root), 'archives': checked, 'files': len(rows),
              'regular_bytes': sum(r.get('bytes', 0) for r in rows),
              'elapsed_s': time.monotonic() - started,
              'method': 'full archive hash plus every restored regular file hash; recorded symlinks'}
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    restore(args.manifest, args.output)
