"""Verify all original 32B weights, convert to unquantized F16, and freeze its identity."""
import argparse
import json
from pathlib import Path
import os
import subprocess
import sys
from leasepath.restricted_data_tools import file_hash

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--converter',type=Path,required=True)
    p.add_argument('--converter-python',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--force-rehash',action='store_true',help='Reread all weights even if verified download manifests cover them.')
    a=p.parse_args()
    hf=json.loads((a.root/'metadata/hf.json').read_text())
    assert hf['id']=='Qwen/Qwen2.5-Coder-32B-Instruct'
    assert hf['sha']=='381fc969f78efac66bc87ff7ddeadb7e73c218a7'
    manifests={}
    observed={}
    for manifest_path in sorted((a.root/'hf').glob('download-verified-*.json')):
        record=json.loads(manifest_path.read_text())
        if (record['verified'] is not True or record['revision']!=hf['sha'] or
                record['repository']!=hf['id']):raise ValueError('unexpected verification manifest')
        for name,digest in record['sha256'].items():
            if name in observed and observed[name]!=digest:raise ValueError('conflicting verified hash: '+name)
            observed[name]=digest
        manifests[str(manifest_path)]=file_hash(manifest_path)
    before={}
    for item in hf['siblings']:
        name=item['rfilename']
        if not name.endswith('.safetensors'):continue
        path=a.root/'hf'/name
        assert path.is_file() and not path.is_symlink()
        info=path.stat()
        assert info.st_size==item['size'],name
        if a.force_rehash or observed.get(name)!=item['lfs']['sha256']:
            assert file_hash(path)==item['lfs']['sha256'],name
            print('full SHA256 before conversion',name,flush=True)
        else:
            print('full SHA256 verified by pinned download manifest',name,flush=True)
        before[path]=(info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    assert not a.output.exists()
    command=[str(a.converter_python),str(a.converter),str(a.root/'hf'),
             '--outtype','f16','--outfile',str(a.output)]
    env={**os.environ,'CUDA_VISIBLE_DEVICES':'','OMP_NUM_THREADS':'4','OPENBLAS_NUM_THREADS':'4'}
    result=subprocess.run(command,env=env)
    if result.returncode:raise SystemExit(result.returncode)
    for path,expected in before.items():
        info=path.stat()
        if (info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)!=expected:
            raise ValueError('verified input changed during conversion: '+path.name)
    identity={'repository':hf['id'],'revision':hf['sha'],'dtype':'float16','quantization':None,
              'gguf':str(a.output.resolve()),'gguf_sha256':file_hash(a.output),
              'source_metadata_sha256':file_hash(a.root/'metadata/hf.json'),
              'download_verification_manifests':manifests,
              'converter_sha256':file_hash(a.converter),'command':command,
              'source_weights_individually_verified':True}
    a.output.with_suffix('.identity.json').write_text(json.dumps(identity,indent=2)+'\n')

if __name__=='__main__':main()
