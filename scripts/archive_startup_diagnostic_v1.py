"""Content-verified incremental archive against the preserved pressure checkpoint."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--base',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    baseproof=json.loads((a.base/'archive-verification.json').read_text());basearchive=Path(baseproof['archive'])
    if sha(basearchive)!=baseproof['sha256']:raise ValueError('base archive hash differs')
    baseindex=json.loads((a.base/'raw-index.json').read_text());old={r['relative']:r for r in baseindex['files']};oldroot=Path(baseindex['source']).name
    rows=[];changed=[]
    for f in sorted(a.source.rglob('*')):
        rel=str(f.relative_to(a.source))
        if f.is_symlink():
            import os
            rows.append({'relative':rel,'kind':'symlink','target':os.readlink(f)});changed.append(f)
        elif f.is_file():
            d=sha(f);prior=old.get(rel);unchanged=prior and prior['sha256']==d
            rows.append({'relative':rel,'kind':'base' if unchanged else 'delta','bytes':f.stat().st_size,'sha256':d,
                'archive_member':oldroot+'/'+rel if unchanged else a.source.name+'/'+rel})
            if not unchanged:changed.append(f)
    archive=a.output/'diagnostic-delta.tar.gz'
    with tarfile.open(archive,'w:gz',compresslevel=1) as tar:
        for f in changed:tar.add(f,arcname=a.source.name+'/'+str(f.relative_to(a.source)),recursive=False)
    expected={a.source.name+'/'+r['relative']:r for r in rows if r['kind'] in ('delta','symlink')}
    checked=0
    with tarfile.open(archive,'r:gz') as tar:
        for member in tar:
            r=expected.pop(member.name)
            if r['kind']=='symlink':assert member.issym() and member.linkname==r['target']
            else:
                assert member.isfile() and member.size==r['bytes']
                assert hashlib.file_digest(tar.extractfile(member),'sha256').hexdigest()==r['sha256']
            checked+=1
    assert not expected
    manifest={'status':'passed','source':str(a.source),'base_archive':str(basearchive),'base_archive_sha256':baseproof['sha256'],
        'delta_archive':str(archive),'delta_archive_sha256':sha(archive),'delta_bytes':archive.stat().st_size,'delta_members_verified':checked,
        'base_regular_files_reused':sum(r['kind']=='base' for r in rows),'files':rows,
        'restore_rule':'Construct source root from each base/delta archive_member at relative path; verify sha256 before use. Recreate recorded symlinks. Existing historical archive is immutable.'}
    (a.output/'restore-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({k:v for k,v in manifest.items() if k!='files'}),flush=True)

if __name__=='__main__':main()
