"""Restore an API-recovered shallow Git checkpoint without losing its exact baseline."""
import argparse
import json
from pathlib import Path
import subprocess

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--metadata',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    m=json.loads(a.metadata.read_text())
    a.output.mkdir(parents=True,exist_ok=False)
    def git(*args):
        subprocess.run(['git','-C',str(a.output),*args],check=True)
    git('init','-q')
    # Git bundle v2 does not serialize shallow boundaries; ordinary clone would reject the absent ancestor.
    (a.output/'.git/shallow').write_text(m['shallow_boundary']+'\n')
    git('bundle','unbundle',str(a.bundle.resolve()))
    git('update-ref','refs/heads/'+m['branch'],m['head'])
    git('symbolic-ref','HEAD','refs/heads/'+m['branch'])
    git('checkout','--force','HEAD')
    git('remote','add','origin',m['remote'])
    git('fsck','--no-reflogs')
    git('status','--short','--branch')

if __name__=='__main__':main()
