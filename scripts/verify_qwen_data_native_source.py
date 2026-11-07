"""Verify the exact upstream headers/source used for the native ABI and preserve library hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from leasepath.qwen_data_backend import PINNED_LLAMA

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda:stream.read(8*1024**2),b''):h.update(data)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--tree',type=Path,required=True)
    p.add_argument('--bin',type=Path,required=True)
    p.add_argument('--adapter',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    manifest=json.loads(a.tree.read_text())
    assert manifest['commit']==PINNED_LLAMA
    checked=[]
    for item in manifest['files']:
        if item['type']!='blob':continue
        path=a.source/item['path']
        if item['path'].startswith(('src/','include/','ggml/include/','vendor/nlohmann/')) or item['path']=='convert_hf_to_gguf.py':
            data=path.read_bytes()
            assert hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()==item['sha'],item['path']
            checked.append({'path':item['path'],'git_blob':item['sha']})
    libraries={str(path.resolve()):digest(path.resolve()) for path in a.bin.glob('*.so*') if not path.is_symlink()}
    dependencies=subprocess.run(['ldd',str(a.adapter)],capture_output=True,text=True,check=True)
    a.output.write_text(json.dumps({
        'commit':PINNED_LLAMA,'verified_upstream_files':checked,
        'tree_manifest_sha256':digest(a.tree),'adapter_sha256':digest(a.adapter),
        'linked_runtime_libraries':libraries,'ldd':dependencies.stdout,
        'scope':'headers and source Git blob rechecked; shared libraries identified by local SHA256; no claim of reproducible binary build'
    },indent=2)+'\n')
    print('verified files',len(checked),'runtime libraries',len(libraries))

if __name__=='__main__':main()
