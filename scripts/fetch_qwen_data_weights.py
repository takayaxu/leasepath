"""Resumable, range-validated download against two matching pinned hash manifests."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import urllib.request

REPO = 'Qwen/Qwen2.5-Coder-32B-Instruct'
REV = '381fc969f78efac66bc87ff7ddeadb7e73c218a7'
CHUNK = 32 * 1024**2

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while data := stream.read(8 * 1024**2):
            h.update(data)
    return h.hexdigest()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--workers', type=int, default=24)
    p.add_argument('--start-index',type=int,default=0)
    p.add_argument('--end-index',type=int,default=14)
    p.add_argument('--source',choices=['hf','modelscope'],default='hf')
    p.add_argument('--recheck-markers',action='store_true',help='Reread each existing chunk before scheduling; full shard SHA256 is always checked.')
    args = p.parse_args()
    root = args.root.resolve()
    hf = json.loads((root/'metadata/hf.json').read_text())
    mirror = json.loads((root/'metadata/modelscope.json').read_text())
    assert hf['id'] == REPO and hf['sha'] == REV
    files = {x['rfilename']: x for x in hf['siblings'] if x['rfilename'].endswith('.safetensors')}
    files={name:files[name] for name in sorted(files)[args.start_index:args.end_index]}
    mf = {x['Path']: x for x in mirror['Data']['Files'] if x['Path'] in files}
    assert set(mf) == set(files)
    out = root/'hf'
    out.mkdir(exist_ok=True)
    for name, item in files.items():
        assert item['size'] == mf[name]['Size'] and item['lfs']['sha256'] == mf[name]['Sha256']
    jobs, descriptors = [], {}
    for name, item in files.items():
        target = out/name
        if target.exists():
            assert target.stat().st_size == item['size'] and sha(target) == item['lfs']['sha256']
            continue
        partial = out/(name+'.part')
        checkpoint = out/(name+'.ranges.json')
        if checkpoint.exists():
            state = json.loads(checkpoint.read_text())
            assert state['revision'] == REV and state['chunk'] == CHUNK
        else:
            state = {'revision': REV, 'chunk': CHUNK, 'prefix': partial.stat().st_size if partial.exists() else 0}
            checkpoint.write_text(json.dumps(state)+'\n')
        fd = os.open(partial, os.O_RDWR | os.O_CREAT, 0o600)
        os.ftruncate(fd, item['size'])
        descriptors[name] = fd
        for start in range(state['prefix'], item['size'], CHUNK):
            end = min(start+CHUNK, item['size'])-1
            marker = out/(name+f'.range-{start}-{end}.sha256')
            if marker.exists():
                # Markers only schedule work; they can never qualify a model.
                if args.recheck_markers:
                    old = os.pread(fd, end-start+1, start)
                    assert hashlib.sha256(old).hexdigest() == marker.read_text().strip()
                continue
            jobs.append((name, start, end, marker))
    lock = threading.Lock()
    done_bytes = 0
    begun = time.monotonic()
    def fetch(job):
        nonlocal done_bytes
        name, start, end, marker = job
        expected = f'bytes {start}-{end}/{files[name]["size"]}'
        for attempt in range(4):
            try:
                if args.source=='hf':
                    url = f'https://hf-mirror.com/{REPO}/resolve/{REV}/{name}?range_start={start}'
                else:
                    url = (f"https://modelscope.cn/models/{REPO}/resolve/{mf[name]['Revision']}/{name}"
                           f'?leasepath_offset={start}&chunk_end={end}')
                req = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end}'})
                with urllib.request.urlopen(req, timeout=90) as res:
                    assert res.status == 206 and res.headers.get('Content-Range') == expected
                    data = res.read(CHUNK+1)
                assert len(data) == end-start+1
                offset = 0
                while offset < len(data):
                    n = os.pwrite(descriptors[name], data[offset:], start+offset)
                    if n <= 0:
                        raise OSError('write made no progress')
                    offset += n
                os.fsync(descriptors[name])
                marker.write_text(hashlib.sha256(data).hexdigest()+'\n')
                with lock:
                    done_bytes += len(data)
                return
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(attempt+1)
    print(json.dumps({'range_jobs': len(jobs), 'workers': args.workers}), flush=True)
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for count, future in enumerate(as_completed([pool.submit(fetch, j) for j in jobs]), 1):
                future.result()
                if count % 32 == 0:
                    print(json.dumps({'ranges': count, 'GiB': round(done_bytes/2**30, 2),
                                      'MiB_s': round(done_bytes/2**20/(time.monotonic()-begun), 1)}), flush=True)
    finally:
        for fd in descriptors.values():
            os.close(fd)
    hashes = {}
    for name, item in files.items():
        partial, final = out/(name+'.part'), out/name
        path = partial if partial.exists() else final
        assert path.stat().st_size == item['size'] and sha(path) == item['lfs']['sha256']
        if path == partial:
            partial.replace(final)
        hashes[name] = item['lfs']['sha256']
        print(json.dumps({'verified': name}), flush=True)
    for item in hf['siblings']:
        name = item['rfilename']
        if name.endswith('.safetensors') or name == '.gitattributes':
            continue
        data = (out/name).read_bytes()
        assert hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest() == item['blobId']
        hashes[name] = hashlib.sha256(data).hexdigest()
    complete_name='download-complete.json' if len(files)==14 else f'download-verified-{args.start_index}-{args.end_index}.json'
    (out/complete_name).write_text(json.dumps({
        'repository': REPO, 'revision': REV, 'verified': True, 'sha256': hashes,
        'inference_validated': False}, indent=2)+'\n')

if __name__ == '__main__':
    main()
