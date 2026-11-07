"""Read actual migration resources, preserving argv, stdout, stderr and exit codes."""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    commands=[
      ['uname','-a'],['id'],['lscpu'],['free','-b'],['df','-B1',str(Path.cwd())],
      ['nvidia-smi','--query-gpu=index,uuid,pci.bus_id,name,driver_version,memory.total,memory.used,memory.free,utilization.gpu','--format=csv'],
      ['nvidia-smi','--query-compute-apps=pid,gpu_uuid,used_memory,process_name','--format=csv'],
      ['/usr/local/cuda-12.4/bin/nvcc','--version'],
      ['ss','-ltnH'],['ps','-eo','pid,user,comm,%cpu,rss','--sort=-rss'],
      ['docker','info','--format','{{.ServerVersion}} {{range $k,$v := .Runtimes}}{{$k}} {{end}}'],
      ['unshare','-Ur','true'],
      ['git','rev-parse','HEAD'],['git','status','--short','--branch']]
    results=[]
    for cmd in commands:
        start=time.monotonic_ns()
        try:
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
            result={'command':cmd,'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
        except (OSError,subprocess.TimeoutExpired) as exc:
            result={'command':cmd,'error':str(exc)}
        result['wall_ns']=time.monotonic_ns()-start
        results.append(result)
    info={'captured_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'evidence_kind':'environment_observation','hostname':platform.node(),
          'architecture':platform.machine(),'python':platform.python_version(),
          'paths':{'checkout':str(Path.cwd()),'model_root':'/home/liuxv/local-models/qwen25-coder-32b-leasepath',
                   'runs':str(Path.cwd()/'.artifacts')},
          'permissions':'same UID capability boundary, no OS isolation claim',
          'results':results}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(info,indent=2)+'\n')

if __name__=='__main__':main()
