"""Download pinned 32B weights from a mirror and verify against HF LFS SHA-256."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import urllib.request

REPOSITORY = 'Qwen/Qwen2.5-Coder-32B-Instruct'
REVISION = '381fc969f78efac66bc87ff7ddeadb7e73c218a7'


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def download(item, output):
    name = item['Path']
    target = output / name
    if target.exists():
        if target.stat().st_size != item['Size'] or digest(target) != item['Sha256']:
            raise ValueError(f'existing weight differs from source: {target}')
        return name
    partial = output / (name + '.part')
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > item['Size']:
        raise ValueError(f'partial file too large: {partial}')
    while offset < item['Size']:
        # Some CDN responses are bounded byte ranges even on the first GET.
        # Key the cache by offset and validate each returned range before append.
        url = (f"https://modelscope.cn/models/{REPOSITORY}/resolve/{item['Revision']}/{name}"
               f'?leasepath_offset={offset}')
        request = urllib.request.Request(url, headers={'Range': f'bytes={offset}-'} if offset else {})
        with urllib.request.urlopen(request, timeout=60) as response:
            previous_size = offset
            if offset and response.status != 206:
                offset = 0
            if response.status == 206:
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                if (not match or int(match[1]) != offset or int(match[3]) != item['Size'] or
                        not offset <= int(match[2]) < item['Size']):
                    raise ValueError('download range mismatch')
            with partial.open('ab' if offset else 'wb') as stream:
                while chunk := response.read(4 * 1024 * 1024):
                    stream.write(chunk)
        offset = partial.stat().st_size
        if offset <= previous_size or offset > item['Size']:
            raise ValueError('download made no progress or exceeded expected size')
    if partial.stat().st_size != item['Size'] or digest(partial) != item['Sha256']:
        raise ValueError(f'weight size or SHA-256 mismatch: {partial}')
    partial.replace(target)
    print(json.dumps({'verified': name, 'bytes': item['Size']}), flush=True)
    return name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hf-metadata', type=Path, required=True)
    parser.add_argument('--mirror-metadata', type=Path, required=True)
    parser.add_argument('--small-files', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    hf = json.loads(args.hf_metadata.read_text())
    mirror = json.loads(args.mirror_metadata.read_text())
    if hf['id'] != REPOSITORY or hf['sha'] != REVISION:
        raise ValueError('unexpected model or HF revision')
    weights = {f['rfilename']: f for f in hf['siblings'] if f['rfilename'].endswith('.safetensors')}
    items = [f for f in mirror['Data']['Files'] if f['Path'] in weights]
    if len(items) != len(weights) or {f['Path'] for f in items} != set(weights):
        raise ValueError('incomplete or duplicate mirror weights')
    for item in items:
        source = weights[item['Path']]
        if (Path(item['Path']).name != item['Path'] or
                not item.get('Revision') or item['Size'] != source['size'] or
                item['Sha256'] != source['lfs']['sha256']):
            raise ValueError('unsafe or different mirror weight')
    args.output.mkdir(parents=True, exist_ok=True)
    missing_bytes = sum(max(0, f['Size'] - (args.output / (f['Path'] + '.part')).stat().st_size)
                        if (args.output / (f['Path'] + '.part')).exists() else f['Size']
                        for f in items if not (args.output / f['Path']).exists())
    if shutil.disk_usage(args.output).free < missing_bytes + 5 * 1024**3:
        raise ValueError('insufficient disk space')
    hashes = {}
    for source in hf['siblings']:
        name = source['rfilename']
        if name in weights or name == '.gitattributes':
            continue
        data = (args.small_files / name).read_bytes()
        if hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest() != source['blobId']:
            raise ValueError(f'small file differs from pinned Git blob: {name}')
        target = args.output / name
        if target.exists() and target.read_bytes() != data:
            raise ValueError(f'existing small file differs: {name}')
        target.write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    manifest = {'repository': REPOSITORY, 'revision': REVISION, 'mirror_files': items,
                'small_file_sha256': hashes}
    (args.output / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda item: download(item, args.output), items))
    hashes.update({f['Path']: f['Sha256'] for f in items})
    (args.output / 'download-complete.json').write_text(json.dumps({
        'repository': REPOSITORY, 'revision': REVISION, 'verified': True,
        'sha256': hashes, 'inference_validated': False}, indent=2) + '\n')


if __name__ == '__main__':
    main()
