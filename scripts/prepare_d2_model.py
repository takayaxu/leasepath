"""Download a pinned Qwen snapshot; verify every byte against source hashes."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPO = 'Qwen/Qwen2.5-Coder-7B-Instruct'
HF_REVISION = 'c03e6d358207e414f1eca0bb1891e29f1db0e242'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def fetch(item, output):
    name, size, expected = item['Path'], item['Size'], item['Sha256']
    target = output / name
    if target.exists() and target.stat().st_size == size and digest(target) == expected:
        return name
    partial = target.with_suffix(target.suffix + '.part')
    # ModelScope records a commit for each file; use that immutable revision.
    url = f"https://modelscope.cn/models/{REPO}/resolve/{item['Revision']}/{name}"
    for attempt in range(5):
        try:
            offset = partial.stat().st_size if partial.exists() else 0
            headers = {'Range': f'bytes={offset}-'} if offset else {}
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90) as response:
                if offset and response.status != 206:
                    offset = 0
                if offset and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                    raise ValueError('invalid download range')
                with partial.open('ab' if offset else 'wb') as stream:
                    while chunk := response.read(4 * 1024 * 1024):
                        stream.write(chunk)
            if partial.stat().st_size != size or digest(partial) != expected:
                raise ValueError(f'file size or SHA256 mismatch: {name}')
            partial.replace(target)
            return name
        except Exception as exc:
            print(json.dumps({'file': name, 'attempt': attempt + 1, 'error': str(exc)}), flush=True)
            if attempt == 4:
                raise
            time.sleep(min(2 ** attempt, 10))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--metadata', type=Path, required=True, help='ModelScope repo/files response')
    parser.add_argument('--hf-metadata', type=Path, required=True, help='HF pinned model metadata with blobs')
    parser.add_argument('--output', type=Path, default=ROOT / 'models/Qwen2.5-Coder-7B-Instruct')
    args = parser.parse_args()
    hf = json.loads(args.hf_metadata.read_text())
    if hf['sha'] != HF_REVISION:
        raise ValueError('unexpected Hugging Face revision')
    ms = json.loads(args.metadata.read_text())
    wanted = {f['rfilename']: f for f in hf['siblings'] if f['rfilename'].endswith(('.json', '.safetensors', '.txt', '.jinja'))}
    items = [f for f in ms['Data']['Files'] if f['Path'] in wanted]
    if {f['Path'] for f in items} != set(wanted):
        raise ValueError('incomplete model metadata')
    for item in items:
        source = wanted[item['Path']]
        if item['Size'] != source['size']:
            raise ValueError('mirror size differs from HF')
        if source.get('lfs') and item['Sha256'] != source['lfs']['sha256']:
            raise ValueError('mirror weights differ from HF')
        if '/' in item['Path'] or not item.get('Revision') or not item.get('Sha256'):
            raise ValueError('unsafe or unpinned file')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'source-manifest.json').write_text(json.dumps({
        'repository': REPO, 'hf_revision': HF_REVISION, 'files': items,
        'hf_metadata': hf,
    }, indent=2) + '\n')
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(fetch, item, args.output) for item in items]
        for job in as_completed(jobs):
            print(json.dumps({'verified': job.result()}), flush=True)
    # Small non-LFS files must also match the pinned HF commit (Git blob SHA1).
    for name, source in wanted.items():
        if not source.get('lfs'):
            data = (args.output / name).read_bytes()
            blob = hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
            if blob != source['blobId']:
                raise ValueError(f'mirror metadata differs from pinned HF commit: {name}')
    (args.output / 'download-complete.json').write_text(json.dumps({
        'verified': True, 'hf_revision': HF_REVISION,
        'sha256': {item['Path']: digest(args.output / item['Path']) for item in items},
    }, indent=2) + '\n')


if __name__ == '__main__':
    main()
