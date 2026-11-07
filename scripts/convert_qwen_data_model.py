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
    a=p.parse_args()
    hf=json.loads((a.root/'metadata/hf.json').read_text())
    assert hf['id']=='Qwen/Qwen2.5-Coder-32B-Instruct'
    assert hf['sha']=='381fc969f78efac66bc87ff7ddeadb7e73c218a7'
    for item in hf['siblings']:
        name=item['rfilename']
        if not name.endswith('.safetensors'):continue
        path=a.root/'hf'/name
        assert path.stat().st_size==item['size'] and file_hash(path)==item['lfs']['sha256'],name
        print('verified before conversion',name,flush=True)
    assert not a.output.exists()
    command=[str(a.converter_python),str(a.converter),str(a.root/'hf'),
             '--outtype','f16','--outfile',str(a.output)]
    env={**os.environ,'CUDA_VISIBLE_DEVICES':'','OMP_NUM_THREADS':'4','OPENBLAS_NUM_THREADS':'4'}
    result=subprocess.run(command,env=env)
    if result.returncode:raise SystemExit(result.returncode)
    identity={'repository':hf['id'],'revision':hf['sha'],'dtype':'float16','quantization':None,
              'gguf':str(a.output.resolve()),'gguf_sha256':file_hash(a.output),
              'source_metadata_sha256':file_hash(a.root/'metadata/hf.json'),
              'converter_sha256':file_hash(a.converter),'command':command,
              'source_weights_individually_verified':True}
    a.output.with_suffix('.identity.json').write_text(json.dumps(identity,indent=2)+'\n')

if __name__=='__main__':main()
