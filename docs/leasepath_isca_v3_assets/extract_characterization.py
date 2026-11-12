import json,hashlib,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'results/analysis/agent_workload_v1_2026-11-11'
def extract(label,base):
    raw=base/'raw';manifest=json.loads((base/'raw-sha256.json').read_text())['files']
    results=[];hashes={}
    for stream in ['controller','backend']:
        p=raw/'raw'/f'{stream}.jsonl';h=hashlib.sha256();out=[]
        with p.open('rb') as f:
            for line in f:
                h.update(line);e=json.loads(line);d=e['data']
                if stream=='controller':
                    if e['kind']=='request_input':d={k:v for k,v in d.items() if k not in ['prompt','token_ids']}
                    out.append({'kind':e['kind'],'ts_ns':e['ts_ns'],'data':d})
                elif e['kind']=='engine_request':
                    keys=['owner','request_id','tokens','output_tokens','lookup_ns','release_ns','enqueue_ns','dispatch_ns','hit','prefill','prefill_ns','decode_ns','prompt_sha256']
                    q={k:d[k] for k in keys};a=d['policy_decision']['distribution_audit']
                    q['audit']={k:a[k] for k in ['t0_s','observed_idle_features','owner_scores']}
                    if label=='static':q['first_sweep']=a['sweeps'][0] if a['sweeps'] else None
                    out.append(q)
        digest=h.hexdigest();assert digest==manifest[f'raw/{stream}.jsonl']
        hashes[str(p.relative_to(ROOT))]=digest
        target=OUT/f'{label}-{stream}-compact.json';target.write_text(json.dumps(out,separators=(',',':'))+'\n')
        print(label,stream,len(out),'verified',flush=True)
    (OUT/f'{label}-source-sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
extract('collection',ROOT/'results/agent/continuous_pilot_v7_2026-11-11')
extract('static',ROOT/'results/agent/continuous_workflow_v6_2026-11-11/e2e/gbdt_static_distribution')
