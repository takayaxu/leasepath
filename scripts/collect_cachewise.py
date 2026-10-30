"""Acquire a pinned CacheWise snapshot and audit it without executing its code."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.cachewise import ARCHIVE_SHA256, COMMIT, REPOSITORY, audit_dataset, verify_snapshot


def collect(destination):
    destination = Path(destination)
    if destination.exists():
        return verify_snapshot(destination)
    url = f'https://codeload.github.com/cachewise-project/cachewise-coding-traces/tar.gz/{COMMIT}'
    request = urllib.request.Request(url, headers={'User-Agent': 'LeasePath-data-audit'})
    with urllib.request.urlopen(request, timeout=60) as response:
        blob = response.read()
    if hashlib.sha256(blob).hexdigest() != ARCHIVE_SHA256:
        raise ValueError('archive checksum mismatch; refusing changed download')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cachewise-', dir=destination.parent) as staging:
        snapshot = Path(staging) / 'snapshot'
        upstream = snapshot / 'upstream'
        hashes, total_bytes = {}, 0
        with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as archive:
            for member in archive.getmembers():
                parts = Path(member.name).parts
                if not parts or parts[0] != f'cachewise-coding-traces-{COMMIT}':
                    raise ValueError('unexpected archive prefix')
                relative = Path(*parts[1:])
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('unsafe archive path')
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError('nonregular archive member')
                data = archive.extractfile(member).read()
                target = upstream / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                total_bytes += len(data)
                hashes[str(relative)] = hashlib.sha256(data).hexdigest()
        provenance = {
            'repository': REPOSITORY, 'commit': COMMIT, 'archive_url': url,
            'archive_sha256': ARCHIVE_SHA256, 'files': len(hashes), 'bytes': total_bytes,
            'retrieved_at_utc': datetime.now(timezone.utc).isoformat(),
            'files_sha256': dict(sorted(hashes.items())),
        }
        (snapshot / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
        shutil.move(str(snapshot), str(destination))
    return provenance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT / 'data/sources/cachewise')
    parser.add_argument('--output', type=Path, default=ROOT / 'results/prediction/cachewise_audit')
    parser.add_argument('--offline', action='store_true', help='Verify local snapshot only; never download')
    args = parser.parse_args()
    if args.offline:
        verify_snapshot(args.source)
    else:
        collect(args.source)
    result = audit_dataset(args.source, args.output)
    print(json.dumps({
        'commit': result['commit'], 'projects': result['project_count'],
        'sessions': result['counts']['sessions'], 'nonempty_sessions': result['nonempty_sessions'],
        'tools': result['counts']['tool_event_rows'],
        'proxy_candidates': result['counts']['candidate_tool_span_rows'],
        'exact_kv_labels_supported': False, 'output': str(args.output),
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
