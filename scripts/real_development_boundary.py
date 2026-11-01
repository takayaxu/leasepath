"""Inventory producers before requests, then checkpoint/drain owned service."""
import argparse
import json
import os
from pathlib import Path
import signal
import time
import urllib.request

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('action',choices=['inventory','finish'])
parser.add_argument('--run',type=Path,required=True)
args=parser.parse_args()
manifest=json.loads((args.run/'service-manifest.json').read_text())
files=sorted((args.run/'raw').glob('*.jsonl'))
rows=[json.loads(p.open().readline()) for p in files]
if args.action=='inventory':
    if manifest['probe_mode']!='on':raise ValueError('inventory requires enabled observer')
    with (args.run/'expected-streams.json').open('x') as f:
        json.dump({'stream_ids':[r['stream_id'] for r in rows], 'captured_monotonic_ns':time.monotonic_ns(),
                   'scope':'after healthy TP startup before requests',
                   'producers':[{k:r[k] for k in ['stream_id','pid','source','rank']} for r in rows]},f,indent=2)
    (args.run/'ipc-before.json').write_text(json.dumps({'shm_names':sorted(p.name for p in Path('/dev/shm').iterdir())},indent=2)+'\n')
    print('inventoried',len(rows))
else:
    if manifest['probe_mode']=='on':
        expected=json.loads((args.run/'expected-streams.json').read_text())['stream_ids']
        if set(expected)!={r['stream_id'] for r in rows}:raise ValueError('producer inventory changed')
        req=urllib.request.Request(f"http://127.0.0.1:{manifest['port']}/collective_rpc",
             data=json.dumps({'method':'leasepath_checkpoint','timeout':60}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=70) as response:(args.run/'checkpoint.json').write_bytes(response.read())
    pid=int((args.run/'server.pid').read_text())
    path=Path(f'/proc/{pid}/cmdline')
    if path.exists() and b'vllm.entrypoints.openai.api_server' in path.read_bytes() and str(manifest['port']).encode() in path.read_bytes():os.killpg(pid,signal.SIGTERM)
    for _ in range(30):
        if not path.exists() or not path.read_bytes():break
        time.sleep(1)
    (args.run/'ipc-after.json').write_text(json.dumps({'shm_names':sorted(p.name for p in Path('/dev/shm').iterdir())},indent=2)+'\n')
    print('checkpointed and stopped own service',pid)
