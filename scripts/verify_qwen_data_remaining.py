"""Resume whole-file verification, reusing explicit successful records from our interrupted downloader."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import hashlib
import json
from pathlib import Path
from leasepath.restricted_data_tools import file_hash

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--prior-log',type=Path,required=True)
    p.add_argument('--workers',type=int,default=4)
    a=p.parse_args()
    hf=json.loads((a.root/'metadata/hf.json').read_text())
    assert hf['sha']=='381fc969f78efac66bc87ff7ddeadb7e73c218a7'
    assert hf['id']=='Qwen/Qwen2.5-Coder-32B-Instruct'
    files={x['rfilename']:x for x in hf['siblings'] if x['rfilename'].endswith('.safetensors')}
    names=sorted(files)[:7]
    verified=[]
    for line in a.prior_log.read_text().splitlines():
        record=json.loads(line)
        if 'verified' in record:
            name=record['verified']
            assert name in names and name not in verified
            path=a.root/'hf'/name
            assert path.is_file() and not path.is_symlink() and path.stat().st_size==files[name]['size']
            verified.append(name)
    print(json.dumps({'inherited_completed_full_hashes':verified,'prior_log_sha256':file_hash(a.prior_log)}),flush=True)
    def check(name):
        path=a.root/'hf'/(name+'.part')
        assert path.is_file() and not path.is_symlink()
        info=path.stat()
        assert info.st_size==files[name]['size']
        actual=file_hash(path)
        assert actual==files[name]['lfs']['sha256'],name
        after=path.stat()
        assert (info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)==(
            after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)
        path.replace(a.root/'hf'/name)
        print(json.dumps({'verified':name,'sha256':actual}),flush=True)
        return name
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for future in as_completed([pool.submit(check,name) for name in names if name not in verified]):
            verified.append(future.result())
    assert set(verified)==set(names)
    hashes={name:files[name]['lfs']['sha256'] for name in names}
    for item in hf['siblings']:
        name=item['rfilename']
        if name.endswith('.safetensors') or name=='.gitattributes':continue
        data=(a.root/'hf'/name).read_bytes()
        assert hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()==item['blobId']
        hashes[name]=hashlib.sha256(data).hexdigest()
    (a.root/'hf/download-verified-0-7.json').write_text(json.dumps({
       'repository':hf['id'],'revision':hf['sha'],'verified':True,'sha256':hashes,
       'inherited_log_sha256':file_hash(a.prior_log),'inference_validated':False},indent=2)+'\n')

if __name__=='__main__':main()
