"""Start the preregistered alternative as a separate, unmapped L1 reference.

This does not claim leasepath KV-probe compatibility for a hybrid architecture.
It never edits the user's saved qwen-services configuration or starts its gateway.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

from preflight_real_development import parse_npu, add_process_evidence, resource_gate
from real_development_service import runtime_env, RUNTIME_PYTHON

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run', type=Path, required=True)
parser.add_argument('--snapshot', type=Path, required=True)
parser.add_argument('--devices', nargs=2, type=int, default=[2,3])
parser.add_argument('--port', type=int, default=8213)
args = parser.parse_args()
snapshot = json.loads(args.snapshot.read_text())
if not snapshot['verified'] or snapshot['repository']!='Qwen/Qwen3.8-27B':
    raise ValueError('alternative not verified or not preregistered')
args.run = args.run.absolute(); args.run.mkdir(parents=True,exist_ok=False)
npu = subprocess.check_output(['npu-smi','info'],text=True)
gate = resource_gate(add_process_evidence(parse_npu(npu),npu),args.devices)
(args.run/'preflight.json').write_text(json.dumps({'npu_smi':npu,'gate':gate},indent=2)+'\n')
if not gate['ready']:raise ValueError('selected devices not idle')
env = runtime_env(args.run)
env.update(LEASEPATH_REAL_PROBE_MODE='off',VLLM_PLUGINS='ascend',
           ASCEND_VISIBLE_DEVICES=','.join(map(str,args.devices)),
           ASCEND_RT_VISIBLE_DEVICES=','.join(map(str,args.devices)))
command = [RUNTIME_PYTHON,'-m','vllm.entrypoints.openai.api_server',
    '--model',snapshot['directory'],'--served-model-name','leasepath-diagnostic-alternative',
    '--host','127.0.0.1','--port',str(args.port),'--dtype','bfloat16',
    '--tensor-parallel-size','2','--max-model-len','16384','--max-num-seqs','1',
    '--max-num-batched-tokens','2048','--gpu-memory-utilization','0.85',
    '--enable-chunked-prefill','--enable-prefix-caching','--enforce-eager',
    '--generation-config','vllm','--reasoning-parser','qwen3','--seed','23',
    '--language-model-only']
manifest = {'observed_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
    'devices':args.devices,'port':args.port,'probe_mode':'off','command':command,
    'model_snapshot':snapshot,'snapshot_sha256':hashlib.sha256(args.snapshot.read_bytes()).hexdigest(),
    'scope':'local offline capability diagnostic; NO backend owner/prefix mapping, NO U/D/C acceptance',
    'future_gate':'if selected as main chain, validate hybrid cache hooks/lifecycle independently before quality acceptance'}
manifest['compatibility_id']=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
(args.run/'service-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
with (args.run/'server.log').open('ab') as stream:
    process=subprocess.Popen(command,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
(args.run/'server.pid').write_text(str(process.pid)+'\n')
print(json.dumps({'pid':process.pid,'run':str(args.run),'scope':manifest['scope']}))
