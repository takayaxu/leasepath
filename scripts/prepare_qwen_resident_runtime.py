"""Prepare a private verified interpreter/stdlib/site runtime, without GPU work."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]

def digest(p):
    with p.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--prefix',type=Path,required=True);p.add_argument('--refresh-sources',action='store_true')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    if a.refresh_sources:
        manifest=json.loads((a.output/'runtime-copy.json').read_text())
        if manifest['dst']!=str(a.prefix):raise ValueError('different runtime root')
        site=a.prefix/'lib/python3.13/site-packages/leasepath'
        for source in (ROOT/'src/leasepath').glob('*.py'):
            dest=site/source.name
            if dest.exists():dest.chmod(0o644)
            shutil.copyfile(source,dest);dest.chmod(0o444)
        paths=[a.prefix/r['relative'] for r in manifest['files']]
        paths+=list(site.glob('*.py'))
        manifest['files']=[{'relative':str(file.relative_to(a.prefix)),'bytes':file.stat().st_size,'sha256':digest(file)} for file in sorted(set(paths))]
        manifest['sources_finalized_before_measurement']=True
        (a.output/'runtime-copy.json').write_text(json.dumps(manifest,indent=2)+'\n')
        print('refreshed source files',len(manifest['files']),flush=True);return
    a.prefix.mkdir(parents=True,exist_ok=False)
    start=time.monotonic(); old=json.loads((ROOT/'.artifacts/qwen-cache-paired-shared-2026-11-09/runtime-copy.json').read_text())
    site=a.prefix/'lib/python3.13/site-packages';site.mkdir(parents=True)
    records=[]
    for row in old['files']:
        rel=Path(row['relative']);source=(ROOT/'src'/rel if rel.parts[0]=='leasepath' else Path(old['source'])/rel)
        dest=site/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
        if rel.parts[0]!='leasepath' and digest(dest)!=row['sha256']:raise ValueError('frozen dependency differs '+str(source))
    # All new Python sources are included; finalize again if source changes before freeze.
    for source in (ROOT/'src/leasepath').glob('*.py'):
        shutil.copyfile(source,site/'leasepath'/source.name)
    for source in Path('/opt/anaconda3/lib/python3.13').rglob('*'):
        rel=source.relative_to('/opt/anaconda3/lib/python3.13')
        if 'site-packages' in rel.parts or '__pycache__' in rel.parts or not source.is_file():continue
        dest=a.prefix/'lib/python3.13'/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
    binary=a.prefix/'bin/python3.13';binary.parent.mkdir();shutil.copyfile('/opt/anaconda3/bin/python3.13',binary);binary.chmod(0o555)
    for file in a.prefix.rglob('*'):
        if file.is_file():
            if file!=binary:file.chmod(0o444)
            records.append({'relative':str(file.relative_to(a.prefix)),'bytes':file.stat().st_size,'sha256':digest(file)})
    manifest={'source':old['source'],'dst':str(a.prefix),'files':records,'copy_s':time.monotonic()-start,
        'interpreter':str(binary),'stdlib':'complete host stdlib except site-packages and __pycache__; site-packages separately frozen',
        'site_packages_bytes_identical':True,'prior_runtime_manifest':str(ROOT/'.artifacts/qwen-cache-paired-shared-2026-11-09/runtime-copy.json')}
    (a.output/'runtime-copy.json').write_text(json.dumps(manifest,indent=2)+'\n')
    # Discover the transitive ELF closure, including libraries loaded via dlopen
    # by every copied extension (whether or not reached in the three sentinels).
    linked=set()
    elfs=[binary]+[f for f in a.prefix.rglob('*') if f.is_file() and ('.so' in f.name)]
    logs=[]
    for elf in elfs:
        result=subprocess.run(['ldd',str(elf)],text=True,capture_output=True)
        for attempt in range(8):
            missing=re.findall(r'^\s*(\S+) => not found',result.stdout,re.M)
            if not missing:break
            for name in missing:
                source=Path('/opt/anaconda3/lib')/name
                if not source.is_file():raise RuntimeError('missing original conda library '+name)
                target=a.prefix/'lib'/name
                if not target.exists():target.symlink_to(source.resolve())
            result=subprocess.run(['ldd',str(elf)],text=True,capture_output=True)
        logs.append({'elf':str(elf),'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
        for line in result.stdout.splitlines():
            match=re.search(r'(?:=>\s+)?(/[^\s]+)\s+\(',line)
            if match:
                path=Path(match[1]).resolve()
                if path.is_file() and not path.is_relative_to(a.prefix):linked.add(path)
        if 'not found' in result.stdout:raise RuntimeError('unresolved ELF dependency '+str(elf))
    for file in ('/etc/ld.so.cache','/etc/localtime','/etc/nsswitch.conf','/etc/passwd','/etc/group','/usr/lib/locale/locale-archive'):
        path=Path(file)
        if path.is_file():linked.add(path.resolve())
    system={'files':[{'path':str(file),'bytes':file.stat().st_size,'sha256':digest(file)} for file in sorted(linked)],
        'method':'ldd transitive closure of private interpreter and every copied ELF shared object; augmented by actual tool strace before freeze',
        'elfs':len(elfs),'ldd':logs}
    (a.output/'system-residency.json').write_text(json.dumps(system,indent=2)+'\n')
    print({'runtime_files':len(records),'runtime_bytes':sum(r['bytes'] for r in records),'system_files':len(linked),'system_bytes':sum(r['bytes'] for r in system['files'])},flush=True)

if __name__=='__main__':main()
